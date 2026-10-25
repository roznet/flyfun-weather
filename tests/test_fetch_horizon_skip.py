"""Live-horizon skip compares against the real window end (#807).

It used to compare against 00:00 of the arrival day, up to ~24 h before the
window actually ends — so a model whose current run stopped mid-window was
fetched anyway, shipped null tails and silently dropped out at later route
points with no ``MODEL_SKIPPED_HORIZON`` diagnostic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from weatherbrief.models import ModelSource
from weatherbrief.models.diagnostic_codes import FetchCode
from weatherbrief.tasks import fetch as fetch_module

from tests.test_fetch_parallel import _disable_elevation, _make_forecast, _route

DEP = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)  # 2 h flight → ends 11:00Z


def _run(live_end: datetime):
    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        return [_make_forecast(p, model) for p in points]

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(
        fetch_module, "_open_meteo_live_horizons",
        return_value={"ecmwf": live_end},
    ):
        return fetch_module.run_fetch(
            route=_route(),
            departure_time=DEP,
            models=[ModelSource.GFS, ModelSource.ECMWF],
            enrich_grib=False,
        )


def _horizon_codes(result) -> list[str]:
    return [d.message for d in result.diagnostics if d.code == FetchCode.MODEL_SKIPPED_HORIZON]


def test_model_ending_mid_window_is_skipped():
    # Data through 10:00Z: past midnight of the arrival day (the old check
    # passed it) but short of the 11:00Z window end.
    result = _run(datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc))
    assert "ecmwf" not in result.models_fetched
    assert result.models_fetched == ["gfs"]
    msgs = _horizon_codes(result)
    assert len(msgs) == 1 and msgs[0].startswith("ECMWF skipped")


def test_model_covering_the_window_is_fetched():
    result = _run(datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc))
    assert sorted(result.models_fetched) == ["ecmwf", "gfs"]
    assert _horizon_codes(result) == []


# ICON is ICON-EU only: past the newest ICON-EU run's 120h horizon the slot is
# skipped, not fetched from Open-Meteo (whose dwd-icon serves ICON global).
# As of 12:00Z the newest main run is 06z (09z is a 30h short run): through
# 2026-05-09 06:00Z.
AS_OF = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _run_icon(departure: datetime, *, historical: bool = False):
    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        return [_make_forecast(p, model) for p in points]

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(fetch_module, "_open_meteo_live_horizons", return_value={}):
        return fetch_module.run_fetch(
            route=_route(),
            departure_time=departure,
            models=[ModelSource.GFS, ModelSource.ICON],
            enrich_grib=False,
            as_of_time=AS_OF,
            historical_mode=historical,
        )


def test_icon_eu_horizon_end_is_the_newest_main_run():
    from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_horizon_end

    assert icon_eu_horizon_end(AS_OF) == datetime(2026, 5, 9, 6, 0, tzinfo=timezone.utc)


def test_icon_past_icon_eu_horizon_is_skipped():
    # 05:00Z + 2 h ends 07:00Z, an hour past the 06z run's f120.
    result = _run_icon(datetime(2026, 5, 9, 5, 0, tzinfo=timezone.utc))
    assert result.models_fetched == ["gfs"]
    msgs = _horizon_codes(result)
    assert len(msgs) == 1 and msgs[0].startswith("ICON skipped")


def test_icon_inside_icon_eu_horizon_is_fetched():
    result = _run_icon(datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc))
    assert sorted(result.models_fetched) == ["gfs", "icon"]
    assert _horizon_codes(result) == []


def test_historical_icon_keeps_open_meteo():
    # Replays stay on Open-Meteo as before: DWD keeps no old ICON-EU runs.
    result = _run_icon(datetime(2026, 5, 9, 5, 0, tzinfo=timezone.utc), historical=True)
    assert "icon" in result.models_fetched


def _run_icon_route(route, *, historical: bool = False):
    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        return [_make_forecast(p, model) for p in points]

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(fetch_module, "_open_meteo_live_horizons", return_value={}):
        return fetch_module.run_fetch(
            route=route,
            departure_time=datetime(2026, 5, 5, 9, 0, tzinfo=timezone.utc),
            models=[ModelSource.GFS, ModelSource.ICON],
            enrich_grib=False,
            as_of_time=AS_OF,
            historical_mode=historical,
        )


def _route_to_canaries():
    from weatherbrief.models import RouteConfig, Waypoint

    # GCLP (27.9°N) is south of ICON-EU's 29.5°N edge.
    return RouteConfig(
        name="EGTK-GCLP",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8361, lon=-1.32),
            Waypoint(icao="GCLP", name="Gran Canaria", lat=27.9319, lon=-15.3866),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=6.0,
    )


def test_icon_skipped_when_route_leaves_icon_eu_domain():
    result = _run_icon_route(_route_to_canaries())
    assert result.models_fetched == ["gfs"]
    assert any(
        d.code == FetchCode.MODEL_SKIPPED_REGION and d.message.startswith("ICON")
        for d in result.diagnostics
    )


def test_icon_kept_inside_icon_eu_domain():
    result = _run_icon_route(_route())
    assert sorted(result.models_fetched) == ["gfs", "icon"]


def test_historical_icon_outside_domain_keeps_open_meteo():
    result = _run_icon_route(_route_to_canaries(), historical=True)
    assert "icon" in result.models_fetched
