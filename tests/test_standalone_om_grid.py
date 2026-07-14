"""The Open-Meteo path (GFS/ICON) must honour the same per-day sample grid.

ECMWF's grid is enforced where its GRIB steps are read; GFS and ICON come from
Open-Meteo, which happily returns every hour. If they kept the fine five-hour
grid on the far day, the map's 09Z and 15Z slots out there would carry GFS but
no ECMWF — a lone model with nothing to cross-check it (#415).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.models.analysis import (
    HourlyForecast,
    ModelSource,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.tasks.airport_watchlist import WatchlistAirport
import weatherbrief.tasks.standalone_verification as sv

INIT = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)


def _forecast_every_hour(icao: str, days: int) -> WaypointForecast:
    """What Open-Meteo actually returns: every hour, unfiltered."""
    return WaypointForecast(
        waypoint=Waypoint(icao=icao, name=icao, lat=50.0, lon=2.0),
        model=ModelSource.GFS,
        fetched_at=INIT,
        hourly=[
            HourlyForecast(
                time=INIT + timedelta(hours=h),
                temperature_2m_c=10.0,
                cloud_cover_pct=30.0,
                precipitation_mm=0.0,
                pressure_levels=[],
            )
            for h in range(24 * (days + 1))
        ],
    )


@pytest.fixture
def om_returns_every_hour(monkeypatch):
    def fake_fetch_multi_point(self, points, model, *, start_date=None,
                               end_date=None, chunk_size=None, hour_filter=None):
        self._record_call()
        return [_forecast_every_hour(p.waypoint_icao, days=6) for p in points]

    with patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        yield


def _steps(snaps) -> list[int]:
    return sorted(
        int((s["forecast_hour"] - INIT).total_seconds() // 3600) for s in snaps
    )


def _fetch(days: int, sample_hours=None):
    snaps, _ = sv._fetch_forecasts_for_model(
        "gfs", INIT, [WatchlistAirport(icao="LFPG", lat=50.0, lon=2.0)],
        session=None, sample_hours=sample_hours, days=days,
    )
    return snaps


class TestParseTimeMemoryBound:
    """`hour_filter` is a memory bound, not a correctness filter (#236).

    Open-Meteo returns every hour on the wire regardless. The filter decides
    how much of that is materialised as Pydantic objects — without it, ~80% of
    a 6-day hourly response (up to 28 pressure levels) is built only to be
    thrown away, and stays alive across the chunk's sounding analysis in
    several threads at once. Passing `None` here would be silently correct and
    expensively wrong, so pin it.
    """

    def test_superset_of_sample_hours_is_pushed_down_to_open_meteo(self, monkeypatch):
        seen = {}

        def capture(self, points, model, *, start_date=None, end_date=None,
                    chunk_size=None, hour_filter=None):
            seen["hour_filter"] = hour_filter
            self._record_call()
            return [_forecast_every_hour(p.waypoint_icao, days=6) for p in points]

        with patch(
            "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
            new=capture,
        ):
            _fetch(days=6)

        assert seen["hour_filter"] == {6, 9, 12, 15, 18}, (
            "the per-day grid path must still bound what Open-Meteo parses"
        )

    def test_explicit_hours_are_pushed_down_unchanged(self, monkeypatch):
        seen = {}

        def capture(self, points, model, *, start_date=None, end_date=None,
                    chunk_size=None, hour_filter=None):
            seen["hour_filter"] = hour_filter
            self._record_call()
            return [_forecast_every_hour(p.waypoint_icao, days=2) for p in points]

        with patch(
            "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
            new=capture,
        ):
            _fetch(days=2, sample_hours=[12])

        assert seen["hour_filter"] == {12}


class TestPerDayGrid:
    def test_near_days_keep_five_sample_hours(self, om_returns_every_hour):
        steps = _steps(_fetch(days=6))
        d5 = [s for s in steps if 120 < s <= 144]
        assert d5 == [126, 129, 132, 135, 138]

    def test_far_day_drops_to_three_sample_hours(self, om_returns_every_hour):
        """09Z and 15Z on D+6 are exactly the slots ECMWF cannot fill."""
        steps = _steps(_fetch(days=6))
        d6 = [s for s in steps if 144 < s <= 168]
        assert d6 == [150, 156, 162]
        assert 153 not in steps and 159 not in steps

    def test_horizon_is_respected(self, om_returns_every_hour):
        """ICON's shorter horizon must actually stop it, not just slow it."""
        steps = _steps(_fetch(days=4))
        assert max(steps) == 114  # D+4 18Z; nothing past ICON's 120h GRIB wall

    def test_explicit_sample_hours_apply_to_every_day(self, om_returns_every_hour):
        """The alternates path passes the flight's ETA hour — unchanged."""
        steps = _steps(_fetch(days=2, sample_hours=[12]))
        assert steps == [12, 36, 60]
