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
