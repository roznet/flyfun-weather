"""Tests for the Met Office surface-pressure chart cache."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from weatherbrief.fetch.metoffice_charts import (
    CHART_IDS,
    CHART_NATIVE_SIZE,
    MO_BASE_URL,
    MO_INDEX_URL,
    MO_STYLE,
    build_route_overlay,
    chart_meta,
    cycle_dir,
    evict_old_cycles,
    fetch_index,
    is_calibrated,
    list_cycles,
    lonlat_to_chart_pixel,
    parse_run_cycle_dt,
    public_enabled,
    refresh_charts,
    resolve_chart_path,
    run_token_to_cycle,
    select_default_chart_id,
)

_GIF_BYTES = b"GIF89a" + b"x" * 64

_RUN_TOKEN = "2026-05-29T0000"
# offset (hours) -> filename HH token used in FSXX<RR>T_<HH>.gif
_OFFSETS = {"ana": "00", "012": "12", "024": "24", "036": "36",
            "048": "48", "060": "60", "072": "72", "096": "96", "120": "120"}


def _gif_url(hh: str, run_token: str = _RUN_TOKEN) -> str:
    # The FSXX prefix embeds the run hour: a 12Z run serves FSXX12T_<HH>.gif.
    run_hh = run_token[-4:-2]
    return f"{MO_BASE_URL}/{MO_STYLE}/{run_token}/FSXX{run_hh}T_{hh}.gif"


def _index_payload(
    issued: str = "2026-05-29T07:30:29Z",
    run_token: str = _RUN_TOKEN,
) -> dict:
    return {
        "issued": issued,
        "products": [
            {"data_date": "2026-05-29T00:00:00Z", "uri": _gif_url(hh, run_token)}
            for hh in _OFFSETS.values()
        ],
    }


def _add_index(payload: dict | None = None, status: int = 200):
    responses.add(
        responses.GET, MO_INDEX_URL,
        body=json.dumps(payload if payload is not None else _index_payload()),
        status=status, content_type="application/json",
    )


def _add_gifs(status: int = 200, etag_prefix: str = "v1"):
    for cid, hh in _OFFSETS.items():
        responses.add(
            responses.GET, _gif_url(hh),
            body=_GIF_BYTES if status == 200 else b"",
            status=status,
            headers={"Last-Modified": "Fri, 29 May 2026 00:26:10 GMT", "ETag": f'"{etag_prefix}-{cid}"'},
        )


# ---------------------------------------------------------------------------
# cycle key helpers
# ---------------------------------------------------------------------------


def test_run_token_to_cycle():
    assert run_token_to_cycle("2026-05-29T0000") == "2026-05-29T00Z"
    assert run_token_to_cycle("2026-05-29T1200") == "2026-05-29T12Z"
    assert run_token_to_cycle("garbage") is None


def test_parse_run_cycle_dt_roundtrip():
    dt = parse_run_cycle_dt("2026-05-29T12Z")
    assert dt == datetime(2026, 5, 29, 12, tzinfo=timezone.utc)
    assert parse_run_cycle_dt("nope") is None


# ---------------------------------------------------------------------------
# select_default_chart_id
# ---------------------------------------------------------------------------


def test_select_default_within_3h_picks_analysis():
    issued = datetime(2026, 5, 29, 0, tzinfo=timezone.utc)
    assert select_default_chart_id(issued + timedelta(hours=2), "2026-05-29T00Z") == "ana"


def test_select_default_nearest_offset():
    issued = datetime(2026, 5, 29, 0, tzinfo=timezone.utc)
    assert select_default_chart_id(issued + timedelta(hours=12), "2026-05-29T00Z") == "012"
    assert select_default_chart_id(issued + timedelta(hours=30), "2026-05-29T00Z") == "024"
    # 18h equidistant 12/24 -> tie-break earlier
    assert select_default_chart_id(issued + timedelta(hours=18), "2026-05-29T00Z") == "012"


def test_select_default_skips_unavailable_offsets():
    """A run may omit the longest offsets — never default to a chart not fetched."""
    issued = datetime(2026, 5, 29, 0, tzinfo=timezone.utc)
    available = {"ana", "012", "024", "036", "048", "060"}  # 072+ missing
    # 75h out would normally pick 072; constrained, falls back to nearest available (060)
    assert select_default_chart_id(
        issued + timedelta(hours=75), "2026-05-29T00Z", available_ids=available
    ) == "060"
    # within the available range it still picks the true nearest
    assert select_default_chart_id(
        issued + timedelta(hours=48), "2026-05-29T00Z", available_ids=available
    ) == "048"
    # no forecast charts available at all -> analysis
    assert select_default_chart_id(
        issued + timedelta(hours=48), "2026-05-29T00Z", available_ids={"ana"}
    ) == "ana"


# ---------------------------------------------------------------------------
# fetch_index
# ---------------------------------------------------------------------------


@responses.activate
def test_fetch_index_parses_run_and_products():
    _add_index()
    import requests
    idx = fetch_index(requests.Session())
    assert idx.error is None
    assert idx.run_cycle == "2026-05-29T00Z"
    assert idx.issued == datetime(2026, 5, 29, 7, 30, 29, tzinfo=timezone.utc)
    assert {e.chart_id for e in idx.entries} == set(CHART_IDS)


@responses.activate
def test_fetch_index_parses_non_00z_run():
    """Regression: a 12Z run serves FSXX12T_<HH>.gif. The offset regex must
    match any run hour, not just FSXX00T — otherwise prod parses zero
    products for half the day (the 12Z run is current ~evening onward)."""
    _add_index(_index_payload(run_token="2026-05-29T1200"))
    import requests
    idx = fetch_index(requests.Session())
    assert idx.error is None
    assert idx.run_cycle == "2026-05-29T12Z"
    assert {e.chart_id for e in idx.entries} == set(CHART_IDS)


@responses.activate
def test_fetch_index_http_error():
    _add_index(status=503)
    import requests
    idx = fetch_index(requests.Session())
    assert idx.run_cycle is None
    assert idx.error is not None


# ---------------------------------------------------------------------------
# refresh_charts
# ---------------------------------------------------------------------------


@responses.activate
def test_refresh_first_run_downloads_all(tmp_path: Path):
    _add_index()
    _add_gifs(status=200)
    report = refresh_charts(tmp_path)
    assert report.error is None
    assert report.run_cycle == "2026-05-29T00Z"
    assert sorted(report.charts_refreshed) == sorted(CHART_IDS)
    assert report.charts_failed == []
    cdir = cycle_dir(tmp_path, report.run_cycle)
    for cid in CHART_IDS:
        assert (cdir / f"{cid}.gif").exists()
        m = chart_meta(tmp_path, report.run_cycle, cid)
        assert m and m["etag"] == f'"v1-{cid}"'


@responses.activate
def test_refresh_second_run_304s_everything(tmp_path: Path):
    _add_index()
    _add_gifs(status=200)
    refresh_charts(tmp_path)
    responses.reset()
    _add_index()
    _add_gifs(status=304)
    report = refresh_charts(tmp_path)
    assert report.charts_refreshed == []
    assert sorted(report.charts_unchanged) == sorted(CHART_IDS)
    assert list_cycles(tmp_path) == ["2026-05-29T00Z"]


@responses.activate
def test_refresh_index_failure_aborts(tmp_path: Path):
    _add_index(status=500)
    report = refresh_charts(tmp_path)
    assert report.run_cycle is None
    assert report.error is not None


@responses.activate
def test_refresh_one_chart_failure_doesnt_kill_others(tmp_path: Path):
    _add_index()
    for cid, hh in _OFFSETS.items():
        st = 500 if cid == "060" else 200
        responses.add(
            responses.GET, _gif_url(hh),
            body=_GIF_BYTES if st == 200 else b"", status=st,
            headers={"Last-Modified": "Fri, 29 May 2026 00:26:10 GMT", "ETag": f'"x-{cid}"'},
        )
    report = refresh_charts(tmp_path)
    assert "060" in report.charts_failed
    assert "ana" in report.charts_refreshed


# ---------------------------------------------------------------------------
# eviction / lookups
# ---------------------------------------------------------------------------


def test_evict_old_cycles_keeps_n_newest(tmp_path: Path):
    root = tmp_path / "metoffice_charts"
    for name in ["2026-05-27T00Z", "2026-05-27T12Z", "2026-05-28T00Z", "2026-05-28T12Z"]:
        (root / name).mkdir(parents=True)
    evicted = evict_old_cycles(tmp_path, keep=2)
    assert sorted(evicted) == ["2026-05-27T00Z", "2026-05-27T12Z"]
    assert list_cycles(tmp_path) == ["2026-05-28T00Z", "2026-05-28T12Z"]


def test_resolve_chart_path_hit_and_miss(tmp_path: Path):
    cdir = cycle_dir(tmp_path, "2026-05-29T00Z")
    cdir.mkdir(parents=True)
    (cdir / "ana.gif").write_bytes(_GIF_BYTES)
    assert resolve_chart_path(tmp_path, "2026-05-29T00Z", "ana") == cdir / "ana.gif"
    assert resolve_chart_path(tmp_path, "2026-05-29T00Z", "012") is None
    assert resolve_chart_path(tmp_path, "2026-05-29T00Z", "999") is None


# ---------------------------------------------------------------------------
# georeferencing (calibrated)
# ---------------------------------------------------------------------------


def test_chart_is_calibrated():
    assert is_calibrated("colour") is True


def test_lonlat_to_chart_pixel_inside_bounds():
    # Central-Europe point projects inside the 800x540 frame.
    w, h = CHART_NATIVE_SIZE["colour"]
    x, y = lonlat_to_chart_pixel(5.0, 50.0, "colour")
    assert 0 < x < w
    assert 0 < y < h


def test_lonlat_north_is_smaller_y():
    # Oslo (north) should be higher up (smaller y) than Paris.
    _, paris_y = lonlat_to_chart_pixel(2.35, 48.86, "colour")
    _, oslo_y = lonlat_to_chart_pixel(10.75, 59.91, "colour")
    assert oslo_y < paris_y


def test_build_route_overlay_shape():
    waypoints = [("EGTF", 51.34, -0.55), ("LFRQ", 47.97, -4.17)]
    overlay = build_route_overlay(waypoints)
    assert set(overlay.keys()) == {"colour"}
    section = overlay["colour"]
    assert section["native_size"] == list(CHART_NATIVE_SIZE["colour"])
    assert len(section["waypoints"]) == 2
    for orig, proj in zip(waypoints, section["waypoints"]):
        assert proj["icao"] == orig[0]
        assert isinstance(proj["x"], int)
        assert isinstance(proj["y"], int)


# ---------------------------------------------------------------------------
# public_enabled gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("", False), ("false", False),
])
def test_public_enabled(monkeypatch, val, expected):
    monkeypatch.setenv("METOFFICE_CHARTS_PUBLIC", val)
    assert public_enabled() is expected


def test_public_enabled_unset(monkeypatch):
    monkeypatch.delenv("METOFFICE_CHARTS_PUBLIC", raising=False)
    assert public_enabled() is False
