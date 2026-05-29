"""Regression guard: _build_pack_meta must copy the chart-reference fields
from the BriefingResult onto the persisted BriefingPackMeta.

This is the seam the original Met Office wiring missed — the fields existed
on both BriefingResult and BriefingPackMeta, but the result→meta construction
in _build_pack_meta didn't copy the metoffice_* ones, so they silently
defaulted to (None, None, False, False) on every pack. A field-name parity
check wouldn't catch that; only exercising the copy does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.api.packs import _build_pack_meta
from weatherbrief.models.storage import Flight
from weatherbrief.pipeline import BriefingResult


def _flight() -> Flight:
    return Flight(
        id="t-2026-05-31-0000",
        route_name="t",
        waypoints=["LFAT", "EGTF"],
        departure_time=datetime(2026, 5, 31, 14, tzinfo=timezone.utc),
        created_at=datetime(2026, 5, 29, tzinfo=timezone.utc),
    )


def test_build_pack_meta_copies_chart_reference_fields():
    # snapshot is a required positional but _build_pack_meta never reads it.
    result = BriefingResult(snapshot=None, snapshot_path=Path("/tmp/none"))
    result.dwd_charts_run_cycle = "2026-05-29T12Z"
    result.dwd_charts_default_id = "048"
    result.dwd_charts_in_coverage = True
    result.dwd_charts_within_horizon = True
    result.metoffice_charts_run_cycle = "2026-05-29T00Z"
    result.metoffice_charts_default_id = "060"
    result.metoffice_charts_in_coverage = True
    result.metoffice_charts_within_horizon = True

    meta = _build_pack_meta(
        "t-2026-05-31-0000",
        _flight(),
        datetime(2026, 5, 29, tzinfo=timezone.utc),
        Path("/tmp/pack"),
        result,
        as_of_time=datetime(2026, 5, 29, tzinfo=timezone.utc),
    )

    # DWD (the established source — sanity)
    assert meta.dwd_charts_run_cycle == "2026-05-29T12Z"
    assert meta.dwd_charts_default_id == "048"
    assert meta.dwd_charts_in_coverage is True
    assert meta.dwd_charts_within_horizon is True

    # Met Office — the fields that were being dropped
    assert meta.metoffice_charts_run_cycle == "2026-05-29T00Z"
    assert meta.metoffice_charts_default_id == "060"
    assert meta.metoffice_charts_in_coverage is True
    assert meta.metoffice_charts_within_horizon is True


def test_build_pack_meta_defaults_when_result_unset():
    result = BriefingResult(snapshot=None, snapshot_path=Path("/tmp/none"))
    meta = _build_pack_meta(
        "t-2026-05-31-0000",
        _flight(),
        datetime(2026, 5, 29, tzinfo=timezone.utc),
        Path("/tmp/pack"),
        result,
        as_of_time=datetime(2026, 5, 29, tzinfo=timezone.utc),
    )
    assert meta.metoffice_charts_run_cycle is None
    assert meta.metoffice_charts_in_coverage is False
    assert meta.metoffice_charts_within_horizon is False
