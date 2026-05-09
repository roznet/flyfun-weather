"""Tests for the DWD hobbymet chart cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from weatherbrief.fetch.dwd_charts import (
    CHART_IDS,
    DWD_BASE_URL,
    FORECAST_OFFSETS_H,
    chart_meta,
    cycle_dir,
    evict_cycles_older_than,
    evict_old_cycles,
    list_cycles,
    parse_run_cycle_from_last_modified,
    refresh_charts,
    resolve_chart_path,
    select_default_chart_id,
    _FILENAMES,
)


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"x" * 64  # plausibly minimal


def _url(chart_id: str) -> str:
    return f"{DWD_BASE_URL}/{_FILENAMES[chart_id]}"


# ---------------------------------------------------------------------------
# parse_run_cycle_from_last_modified
# ---------------------------------------------------------------------------


def test_parse_run_cycle_rounds_down_to_synoptic_hour():
    # 09:42 UTC → 06Z (not 12Z). Per spec: round DOWN.
    assert (
        parse_run_cycle_from_last_modified("Wed, 08 May 2026 09:42:00 GMT")
        == "2026-05-08T06Z"
    )


def test_parse_run_cycle_at_synoptic_boundary():
    # Exactly 12:00 UTC stays at 12Z, not 06Z.
    assert (
        parse_run_cycle_from_last_modified("Wed, 08 May 2026 12:00:00 GMT")
        == "2026-05-08T12Z"
    )


def test_parse_run_cycle_handles_each_synoptic_hour():
    cases = [
        ("Wed, 08 May 2026 00:30:00 GMT", "2026-05-08T00Z"),
        ("Wed, 08 May 2026 05:59:59 GMT", "2026-05-08T00Z"),
        ("Wed, 08 May 2026 06:00:00 GMT", "2026-05-08T06Z"),
        ("Wed, 08 May 2026 17:59:00 GMT", "2026-05-08T12Z"),
        ("Wed, 08 May 2026 18:00:00 GMT", "2026-05-08T18Z"),
        ("Wed, 08 May 2026 23:59:00 GMT", "2026-05-08T18Z"),
    ]
    for header, expected in cases:
        assert parse_run_cycle_from_last_modified(header) == expected, header


def test_parse_run_cycle_invalid_returns_none():
    assert parse_run_cycle_from_last_modified(None) is None
    assert parse_run_cycle_from_last_modified("") is None
    assert parse_run_cycle_from_last_modified("not a date") is None


# ---------------------------------------------------------------------------
# select_default_chart_id
# ---------------------------------------------------------------------------


def test_select_default_chart_etd_within_3h_picks_analysis():
    issued = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    etd = issued + timedelta(hours=2, minutes=30)
    assert select_default_chart_id(etd, "2026-05-08T06Z") == "ana"


def test_select_default_chart_etd_at_36h_picks_036():
    issued = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    etd = issued + timedelta(hours=36)
    assert select_default_chart_id(etd, "2026-05-08T06Z") == "036"


def test_select_default_chart_etd_between_offsets_picks_nearest():
    issued = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    # 42h is equidistant between 36 and 48 — tie-break to earlier
    assert select_default_chart_id(
        issued + timedelta(hours=42), "2026-05-08T06Z",
    ) == "036"
    # 50h closer to 48 than 60
    assert select_default_chart_id(
        issued + timedelta(hours=50), "2026-05-08T06Z",
    ) == "048"
    # 96h closer to 84 than 108
    assert select_default_chart_id(
        issued + timedelta(hours=96), "2026-05-08T06Z",
    ) == "084"


def test_select_default_chart_etd_beyond_horizon_returns_108():
    issued = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    assert select_default_chart_id(
        issued + timedelta(hours=200), "2026-05-08T06Z",
    ) == "108"


# ---------------------------------------------------------------------------
# refresh_charts
# ---------------------------------------------------------------------------


def _add_chart_response(chart_id: str, last_modified: str, status: int = 200, etag: str = "abc"):
    headers = {"Last-Modified": last_modified, "ETag": etag}
    body = _PNG_BYTES if status == 200 else b""
    responses.add(responses.GET, _url(chart_id), body=body, status=status, headers=headers)


@responses.activate
def test_refresh_first_run_downloads_all_six(tmp_path: Path):
    lm = "Wed, 08 May 2026 06:30:00 GMT"
    for cid in CHART_IDS:
        _add_chart_response(cid, lm, status=200, etag=f'"{cid}-1"')

    report = refresh_charts(tmp_path)

    assert report.error is None
    assert report.run_cycle == "2026-05-08T06Z"
    assert sorted(report.charts_refreshed) == sorted(CHART_IDS)
    assert report.charts_unchanged == []
    assert report.charts_failed == []

    # All files written
    cdir = cycle_dir(tmp_path, report.run_cycle)
    for cid in CHART_IDS:
        assert (cdir / f"{cid}.png").exists()
    assert (cdir / "meta.json").exists()
    # meta has last_modified and etag for every chart
    for cid in CHART_IDS:
        m = chart_meta(tmp_path, report.run_cycle, cid)
        assert m is not None
        assert m["last_modified"]
        assert m["etag"] == f'"{cid}-1"'


@responses.activate
def test_refresh_second_run_304s_everything(tmp_path: Path):
    # Seed: first run downloads everything
    lm = "Wed, 08 May 2026 06:30:00 GMT"
    for cid in CHART_IDS:
        _add_chart_response(cid, lm, status=200, etag=f'"{cid}-1"')
    refresh_charts(tmp_path)

    # Second run: server returns 304 for all
    responses.reset()
    for cid in CHART_IDS:
        _add_chart_response(cid, lm, status=304, etag=f'"{cid}-1"')
    report = refresh_charts(tmp_path)

    assert report.error is None
    assert report.run_cycle == "2026-05-08T06Z"
    assert report.charts_refreshed == []
    assert sorted(report.charts_unchanged) == sorted(CHART_IDS)
    assert report.charts_failed == []
    # No new cycle directory was created
    assert list_cycles(tmp_path) == ["2026-05-08T06Z"]


@responses.activate
def test_refresh_new_cycle_creates_new_dir(tmp_path: Path):
    # Seed cycle 06Z
    for cid in CHART_IDS:
        _add_chart_response(
            cid, "Wed, 08 May 2026 06:30:00 GMT", status=200, etag=f'"{cid}-06"',
        )
    refresh_charts(tmp_path)
    responses.reset()

    # Server now publishes 12Z run
    for cid in CHART_IDS:
        _add_chart_response(
            cid, "Wed, 08 May 2026 12:42:00 GMT", status=200, etag=f'"{cid}-12"',
        )
    report = refresh_charts(tmp_path)

    assert report.run_cycle == "2026-05-08T12Z"
    assert sorted(report.charts_refreshed) == sorted(CHART_IDS)
    cycles = list_cycles(tmp_path)
    assert "2026-05-08T06Z" in cycles
    assert "2026-05-08T12Z" in cycles


@responses.activate
def test_refresh_one_forecast_failure_doesnt_kill_others(tmp_path: Path):
    lm = "Wed, 08 May 2026 06:30:00 GMT"
    for cid in CHART_IDS:
        if cid == "060":
            _add_chart_response(cid, lm, status=500)
        else:
            _add_chart_response(cid, lm, status=200, etag=f'"{cid}-1"')

    report = refresh_charts(tmp_path)

    assert report.run_cycle == "2026-05-08T06Z"
    assert "060" in report.charts_failed
    # The five other charts still landed
    for cid in CHART_IDS:
        if cid != "060":
            assert cid in report.charts_refreshed


@responses.activate
def test_refresh_analysis_failure_aborts(tmp_path: Path):
    _add_chart_response("ana", "", status=500)
    # Forecasts won't be queried because we abort early.
    report = refresh_charts(tmp_path)
    assert report.run_cycle is None
    assert report.error is not None
    assert "ana" in report.charts_failed


# ---------------------------------------------------------------------------
# eviction
# ---------------------------------------------------------------------------


def test_evict_old_cycles_keeps_n_newest(tmp_path: Path):
    root = tmp_path / "dwd_charts"
    for name in [
        "2026-05-06T00Z",
        "2026-05-06T06Z",
        "2026-05-06T12Z",
        "2026-05-06T18Z",
        "2026-05-07T00Z",
    ]:
        (root / name).mkdir(parents=True)

    evicted = evict_old_cycles(tmp_path, keep=2)

    assert sorted(evicted) == sorted(
        ["2026-05-06T00Z", "2026-05-06T06Z", "2026-05-06T12Z"]
    )
    remaining = list_cycles(tmp_path)
    assert remaining == ["2026-05-06T18Z", "2026-05-07T00Z"]


def test_evict_old_cycles_noop_when_under_limit(tmp_path: Path):
    root = tmp_path / "dwd_charts"
    (root / "2026-05-08T00Z").mkdir(parents=True)
    (root / "2026-05-08T06Z").mkdir(parents=True)
    assert evict_old_cycles(tmp_path, keep=8) == []


def test_evict_cycles_older_than_uses_cycle_datetime(tmp_path: Path):
    root = tmp_path / "dwd_charts"
    # Older than 24h
    (root / "2026-05-06T00Z").mkdir(parents=True)
    # Within 24h of "now" (test uses real clock — choose dates accordingly)
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%HZ")
    (root / recent).mkdir(parents=True)

    evicted = evict_cycles_older_than(tmp_path, max_age_hours=24)
    assert "2026-05-06T00Z" in evicted
    assert recent not in evicted


# ---------------------------------------------------------------------------
# resolve_chart_path / chart_meta
# ---------------------------------------------------------------------------


def test_resolve_chart_path_hit_and_miss(tmp_path: Path):
    cdir = cycle_dir(tmp_path, "2026-05-08T06Z")
    cdir.mkdir(parents=True)
    (cdir / "ana.png").write_bytes(_PNG_BYTES)

    assert resolve_chart_path(tmp_path, "2026-05-08T06Z", "ana") == cdir / "ana.png"
    assert resolve_chart_path(tmp_path, "2026-05-08T06Z", "036") is None
    # Bogus chart_id
    assert resolve_chart_path(tmp_path, "2026-05-08T06Z", "999") is None


def test_chart_meta_returns_none_on_miss(tmp_path: Path):
    assert chart_meta(tmp_path, "2026-05-08T06Z", "ana") is None
