"""The server-built charts manifest on pack meta, the downscaled DWD analysis
and the cached GRAMET PNG (#799)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weatherbrief.api._chart_manifest import (
    build_pack_charts_manifest,
    temsi_tab_label,
    temsi_validity_offset,
)
from weatherbrief.fetch import dwd_charts, meteofrance_charts, metoffice_charts
from weatherbrief.models import BriefingPackMeta

_FLIGHT_ID = "zzaa_zzbb-2026-05-10-abcd"
_FETCH_TS = datetime(2026, 5, 8, 7, 0, tzinfo=timezone.utc)
_DWD_CYCLE = "2026-05-08T06Z"
_MO_CYCLE = "2026-05-08T00Z"
_PNG = b"\x89PNG\r\n\x1a\n" + b"y" * 64


def _meta(**overrides) -> BriefingPackMeta:
    fields = dict(
        flight_id=_FLIGHT_ID,
        fetch_timestamp=_FETCH_TS,
        days_out=2,
        has_gramet=False,
        has_skewt=False,
        has_digest=False,
        assessment="GREEN",
        assessment_reason="ok",
        dwd_charts_run_cycle=_DWD_CYCLE,
        dwd_charts_default_id="048",
        dwd_charts_in_coverage=True,
        dwd_charts_within_horizon=True,
    )
    fields.update(overrides)
    return BriefingPackMeta(**fields)


def _write(module, data_dir: Path, run_cycle: str, chart_id: str, meta: dict | None = None) -> None:
    cdir = module.cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    ext = "gif" if module is metoffice_charts else "png"
    (cdir / f"{chart_id}.{ext}").write_bytes(_PNG)
    if meta is not None:
        path = cdir / "meta.json"
        existing = json.loads(path.read_text()) if path.exists() else {}
        existing[chart_id] = meta
        path.write_text(json.dumps(existing))


def _build(meta: BriefingPackMeta, data_dir: Path, *, mf_options=None, mf_default=None,
           metoffice_public=False, departure=None):
    return build_pack_charts_manifest(
        meta,
        data_dir=data_dir,
        meteofrance_options=mf_options or [],
        meteofrance_default_id=mf_default,
        metoffice_public=metoffice_public,
        departure_time=departure,
    )


# --- gating -----------------------------------------------------------------


def test_dwd_source_lists_every_chart_with_availability(tmp_path):
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "ana", {"last_modified": "2026-05-08T06:40:00+00:00"})
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "048", {"last_modified": "2026-05-08T04:43:00+00:00"})

    manifest = _build(_meta(), tmp_path)

    assert manifest.in_coverage is True
    [dwd] = manifest.sources
    assert dwd.source == "dwd"
    assert dwd.default_chart_id == "048"
    assert dwd.attribution == "Deutscher Wetterdienst (DWD), CC BY 4.0"
    assert [c.chart_id for c in dwd.charts] == list(dwd_charts.CHART_IDS)
    by_id = {c.chart_id: c for c in dwd.charts}
    # Evicted / never fetched charts stay listed, flagged unavailable.
    assert by_id["ana"].available and by_id["048"].available
    assert not by_id["036"].available
    assert by_id["ana"].label == "Analysis" and by_id["036"].label == "+36h"
    assert by_id["ana"].overlay_key == "analysis" and by_id["048"].overlay_key == "icon"
    assert dwd.overlay_url.endswith("/dwd-chart-overlay")


def test_cycle_memo_reads_a_shared_cycle_once(tmp_path, monkeypatch):
    """A pack-history list shares one memo: packs on the same run cycle reuse
    its directory read, and the manifests match the unmemoised build."""
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "048", {"last_modified": "2026-05-08T04:43:00+00:00"})
    plain = _build(_meta(), tmp_path)

    calls: list[str] = []
    real = dwd_charts.cycle_snapshot
    monkeypatch.setattr(dwd_charts, "cycle_snapshot", lambda d, rc: calls.append(rc) or real(d, rc))
    memo: dict = {}
    built = [
        build_pack_charts_manifest(
            _meta(fetch_timestamp=_FETCH_TS + timedelta(hours=h)), data_dir=tmp_path,
            meteofrance_options=[], meteofrance_default_id=None, metoffice_public=False,
            cycle_memo=memo,
        )
        for h in (0, 1, 2)
    ]
    assert calls == [_DWD_CYCLE]
    assert [c.cache_key for c in built[0].sources[0].charts] == [
        c.cache_key for c in plain.sources[0].charts
    ]
    assert [c.available for c in built[2].sources[0].charts] == [
        c.available for c in plain.sources[0].charts
    ]


def test_evicted_cycle_lists_charts_unavailable(tmp_path):
    [dwd] = _build(_meta(dwd_charts_run_cycle="2026-04-01T06Z"), tmp_path).sources
    assert dwd.charts and not any(c.available or c.prefetch for c in dwd.charts)


def test_dwd_analysis_points_at_downscaled_variant(tmp_path):
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "ana", {"last_modified": "2026-05-08T06:40:00+00:00"})
    ana = _build(_meta(), tmp_path).sources[0].charts[0]

    assert ana.url.endswith("/dwd-chart/ana?w=1600")
    assert ana.cache_key == "dwd/2026-05-08T06Z/ana_w1600-20260508T064000Z"
    # The flight id and the pack timestamp are percent-encoded into the path.
    assert ana.url.startswith(f"/api/flights/{_FLIGHT_ID}/packs/2026-05-08T07%3A00%3A00%2B00%3A00/")


def test_dwd_hidden_beyond_horizon_but_still_in_coverage(tmp_path):
    manifest = _build(_meta(dwd_charts_within_horizon=False), tmp_path)
    assert manifest.sources == []
    assert manifest.in_coverage is True  # → "charts unavailable" message, not hidden


def test_out_of_coverage_has_no_sources(tmp_path):
    manifest = _build(_meta(dwd_charts_in_coverage=False), tmp_path)
    assert manifest.sources == []
    assert manifest.in_coverage is False


def test_metoffice_needs_public_flag(tmp_path):
    meta = _meta(
        metoffice_charts_run_cycle=_MO_CYCLE,
        metoffice_charts_default_id="048",
        metoffice_charts_in_coverage=True,
        metoffice_charts_within_horizon=True,
    )
    _write(metoffice_charts, tmp_path, _MO_CYCLE, "048", {"last_modified": "2026-05-08T03:00:00+00:00"})

    assert [s.source for s in _build(meta, tmp_path).sources] == ["dwd"]

    sources = _build(meta, tmp_path, metoffice_public=True).sources
    assert [s.source for s in sources] == ["dwd", "metoffice"]
    mo = sources[1]
    assert mo.charts[0].overlay_key == "colour"
    assert mo.charts[0].file_ext == "gif"
    assert mo.attribution == "Met Office · © Crown copyright"


def test_metoffice_flag_without_coverage_does_not_count(tmp_path):
    manifest = _build(_meta(dwd_charts_in_coverage=False), tmp_path, metoffice_public=True)
    assert manifest.in_coverage is False


# --- cache keys ---------------------------------------------------------------


def test_cache_key_changes_when_forecast_bytes_are_replaced_in_the_same_cycle(tmp_path):
    """A DWD cycle is the analysis; its forecasts are swapped in place when the
    next ICON run lands. The key must follow the bytes, not the cycle."""
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "048", {"last_modified": "2026-05-07T04:40:00+00:00"})
    before = _build(_meta(), tmp_path).sources[0].charts[2]
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "048", {"last_modified": "2026-05-08T04:43:00+00:00"})
    after = _build(_meta(), tmp_path).sources[0].charts[2]

    assert before.chart_id == after.chart_id == "048"
    assert before.cache_key != after.cache_key
    assert after.cache_key.startswith("dwd/2026-05-08T06Z/048-")


def test_dwd_forecast_caption_uses_the_charts_own_run(tmp_path):
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "048", {
        "last_modified": "2026-05-08T04:43:00+00:00",
        "init_time": "2026-05-08T00:00:00Z", "lead_h": 48,
        "valid_time": "2026-05-10T00:00:00Z", "time_source": "ocr",
    })
    chart = _build(_meta(), tmp_path).sources[0].charts[2]
    assert chart.valid_time == "2026-05-10T00:00:00Z"
    assert chart.init_time == "2026-05-08T00:00:00Z"
    assert chart.caption == "Valid 2026-05-10 00Z · ICON 2026-05-08 00Z run +48h"


# --- TEMSI ----------------------------------------------------------------------


def test_temsi_options_become_charts_with_departure_gap(tmp_path):
    meta = _meta(
        dwd_charts_in_coverage=False,
        meteofrance_charts_in_coverage=True,
        meteofrance_charts_within_horizon=True,
    )
    options = [
        {"zone": "france", "run_cycle": "2026-05-10T09Z"},
        {"zone": "euroc", "run_cycle": "2026-05-10T09Z"},
    ]
    _write(meteofrance_charts, tmp_path, "2026-05-10T09Z", "france", {"date_run": "20260510060000"})
    departure = datetime(2026, 5, 10, 11, 30, tzinfo=timezone.utc)

    manifest = _build(meta, tmp_path, mf_options=options, mf_default="france|2026-05-10T09Z",
                      departure=departure)

    [mf] = manifest.sources
    assert mf.source == "meteofrance"
    assert [c.chart_id for c in mf.charts] == ["france|2026-05-10T09Z", "euroc|2026-05-10T09Z"]
    france = mf.charts[0]
    assert france.label == "France 09Z"
    assert france.caption == "TEMSI France — SFC to FL150 · valid 2026-05-10 09Z (2.5 h before departure)"
    assert france.overlay_key == "france"
    assert france.cache_key == "meteofrance/2026-05-10T09Z/france-20260510060000"
    assert france.url.endswith("/meteofrance-chart/2026-05-10T09Z/france")
    assert all(c.prefetch for c in mf.charts)


def test_temsi_evicted_options_are_filtered_from_the_manifest(tmp_path, monkeypatch):
    """Evicted validities are dropped by ``_live_meteofrance_options`` before
    the manifest is built — the same filter the flat fields get."""
    from weatherbrief.api.packs import _meta_to_response

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    meta = _meta(
        dwd_charts_in_coverage=False,
        meteofrance_charts_in_coverage=True,
        meteofrance_charts_within_horizon=True,
        meteofrance_charts_options=[
            {"zone": "france", "run_cycle": "2026-05-10T09Z"},
            {"zone": "france", "run_cycle": "2026-05-10T12Z"},
        ],
        meteofrance_charts_default_id="france|2026-05-10T09Z",
    )
    _write(meteofrance_charts, tmp_path, "2026-05-10T12Z", "france")

    resp = _meta_to_response(meta)

    [mf] = resp.charts.sources
    assert [c.chart_id for c in mf.charts] == ["france|2026-05-10T12Z"]
    assert mf.default_chart_id == "france|2026-05-10T12Z"


def test_temsi_label_and_offset_ports_match_the_web():
    assert temsi_tab_label("france", "2026-08-31T15Z", False) == "France 15Z"
    assert temsi_tab_label("euroc", "2026-08-31T15Z", True) == "Europe 31/15Z"
    etd = datetime(2026, 8, 31, 15, 30, tzinfo=timezone.utc)
    assert temsi_validity_offset(datetime(2026, 8, 31, 15, tzinfo=timezone.utc), etd) is None
    assert temsi_validity_offset(datetime(2026, 8, 31, 13, tzinfo=timezone.utc), etd) == "2.5 h before departure"
    assert temsi_validity_offset(datetime(2026, 8, 31, 18, tzinfo=timezone.utc), etd) == "2.5 h after departure"


# --- offline prefetch subset -----------------------------------------------------


def test_dwd_prefetch_is_analysis_plus_forecasts_bracketing_etd(tmp_path):
    for cid in dwd_charts.CHART_IDS:
        _write(dwd_charts, tmp_path, _DWD_CYCLE, cid, {"last_modified": "2026-05-08T06:40:00+00:00"})
    # ETD at cycle +50h → brackets 048 (+48h) and 060 (+60h).
    departure = datetime(2026, 5, 10, 8, tzinfo=timezone.utc)
    dwd = _build(_meta(), tmp_path, departure=departure).sources[0]
    assert {c.chart_id for c in dwd.charts if c.prefetch} == {"ana", "048", "060"}


def test_prefetch_skips_unavailable_charts(tmp_path):
    _write(dwd_charts, tmp_path, _DWD_CYCLE, "ana", {"last_modified": "2026-05-08T06:40:00+00:00"})
    departure = datetime(2026, 5, 10, 8, tzinfo=timezone.utc)
    dwd = _build(_meta(), tmp_path, departure=departure).sources[0]
    assert {c.chart_id for c in dwd.charts if c.prefetch} == {"ana"}


def test_metoffice_prefetch_is_two_nearest_etd(tmp_path):
    meta = _meta(
        dwd_charts_in_coverage=False,
        metoffice_charts_run_cycle=_MO_CYCLE,
        metoffice_charts_default_id="048",
        metoffice_charts_in_coverage=True,
        metoffice_charts_within_horizon=True,
    )
    for cid in metoffice_charts.CHART_IDS:
        _write(metoffice_charts, tmp_path, _MO_CYCLE, cid, {"last_modified": "2026-05-08T03:00:00+00:00"})
    departure = datetime(2026, 5, 10, 2, tzinfo=timezone.utc)  # cycle +50h
    [mo] = _build(meta, tmp_path, metoffice_public=True, departure=departure).sources
    assert {c.chart_id for c in mo.charts if c.prefetch} == {"048", "060"}


def test_prefetch_without_etd_falls_back_to_default(tmp_path):
    for cid in dwd_charts.CHART_IDS:
        _write(dwd_charts, tmp_path, _DWD_CYCLE, cid, {"last_modified": "2026-05-08T06:40:00+00:00"})
    dwd = _build(_meta(), tmp_path).sources[0]
    assert {c.chart_id for c in dwd.charts if c.prefetch} == {"ana", "048"}


# --- downscaled DWD analysis ------------------------------------------------------


def _write_real_analysis(data_dir: Path) -> Path:
    from PIL import Image

    cdir = dwd_charts.cycle_dir(data_dir, _DWD_CYCLE)
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "ana.png"
    Image.new("P", dwd_charts.CHART_NATIVE_SIZE["analysis"], color=3).save(path)
    return path


def test_downscaled_analysis_keeps_aspect_so_overlay_scales_by_width(tmp_path):
    from PIL import Image

    _write_real_analysis(tmp_path)
    small = dwd_charts.resolve_small_analysis_path(tmp_path, _DWD_CYCLE)
    assert small is not None and small.name == "ana_w1600.png"

    with Image.open(small) as im:
        w, h = im.size
    native_w, native_h = dwd_charts.CHART_NATIVE_SIZE["analysis"]
    assert w == dwd_charts.ANALYSIS_SMALL_WIDTH
    # One scale factor for both axes: a projected waypoint lands within half a
    # pixel of where the native overlay puts it once scaled by the width ratio.
    scale = w / native_w
    overlay = dwd_charts.build_route_overlay([("ZZZZ", 50.0, 8.6)])["analysis"]
    wp = overlay["waypoints"][0]
    assert abs(wp["y"] * scale - wp["y"] * (h / native_h)) < 0.5
    assert abs(native_h * scale - h) < 0.5


def test_downscaled_analysis_none_when_full_chart_evicted(tmp_path):
    assert dwd_charts.resolve_small_analysis_path(tmp_path, _DWD_CYCLE) is None


def test_downscale_failure_does_not_raise(tmp_path):
    cdir = dwd_charts.cycle_dir(tmp_path, _DWD_CYCLE)
    cdir.mkdir(parents=True)
    (cdir / "ana.png").write_bytes(_PNG)  # not a decodable image
    assert dwd_charts.resolve_small_analysis_path(tmp_path, _DWD_CYCLE) is None


# --- API: ?w=1600 and gramet.png cache --------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from conftest import make_app_engine
    from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
    from flyfun_common.db.models import UserPreferencesRow, UserRow
    from weatherbrief.api.app import create_app

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-api-tests")
    engine = make_app_engine()
    session_factory = sessionmaker(bind=engine)
    s = session_factory()
    s.add(UserRow(id=DEV_USER_ID, provider="local", provider_sub="dev",
                  email="dev@localhost", display_name="Dev User", approved=True))
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
    s.commit()
    s.close()

    app = create_app()

    def _db():
        session = session_factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    yield TestClient(app, raise_server_exceptions=False), session_factory, tmp_path / "data"
    engine.dispose()


def _seed(session_factory) -> tuple[str, str]:
    from flyfun_common.db import DEV_USER_ID
    from weatherbrief.models import Flight
    from weatherbrief.storage.flights import save_flight, save_pack_meta

    now = datetime.now(timezone.utc)
    departure = (now + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    flight = Flight(
        id=f"zzaa_zzbb-{departure:%Y-%m-%d}-abcd",
        user_id=DEV_USER_ID,
        route_name="zzaa_zzbb",
        waypoints=["EGTK", "LSGS"],
        departure_time=departure,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=2.0,
        created_at=now - timedelta(days=1),
    )
    s = session_factory()
    save_flight(s, flight, DEV_USER_ID)
    meta = _meta(flight_id=flight.id, fetch_timestamp=now - timedelta(hours=1), has_gramet=True)
    save_pack_meta(s, meta)
    s.commit()
    s.close()
    return flight.id, meta.fetch_timestamp.isoformat()


def test_api_serves_downscaled_analysis(api):
    from PIL import Image
    import io

    client, sf, data_dir = api
    flight_id, ts = _seed(sf)
    _write_real_analysis(data_dir)

    resp = client.get(f"/api/flights/{flight_id}/packs/{ts}/dwd-chart/ana?w=1600")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(resp.content)).size[0] == 1600

    assert client.get(f"/api/flights/{flight_id}/packs/{ts}/dwd-chart/ana?w=800").status_code == 400
    assert client.get(f"/api/flights/{flight_id}/packs/{ts}/dwd-chart/048?w=1600").status_code == 400


def test_api_downscaled_analysis_410_when_evicted(api):
    client, sf, _ = api
    flight_id, ts = _seed(sf)
    assert client.get(f"/api/flights/{flight_id}/packs/{ts}/dwd-chart/ana?w=1600").status_code == 410


def test_pack_response_carries_charts_manifest(api):
    client, sf, data_dir = api
    flight_id, _ = _seed(sf)
    _write(dwd_charts, data_dir, _DWD_CYCLE, "ana", {"last_modified": "2026-05-08T06:40:00+00:00"})

    pack = client.get(f"/api/flights/{flight_id}/packs/latest").json()
    charts = pack["charts"]
    assert charts["in_coverage"] is True
    assert charts["sources"][0]["source"] == "dwd"
    assert charts["sources"][0]["charts"][0]["cache_key"].startswith("dwd/2026-05-08T06Z/ana_w1600")


def test_gramet_png_rendered_once_and_cached(api):
    import fitz

    from flyfun_common.db import DEV_USER_ID
    from weatherbrief.storage.flights import pack_dir_for

    client, sf, _ = api
    flight_id, ts = _seed(sf)
    pack_dir = pack_dir_for(DEV_USER_ID, flight_id, ts)
    pack_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    doc.new_page(width=200, height=100)
    doc.save(str(pack_dir / "gramet.pdf"))
    doc.close()

    first = client.get(f"/api/flights/{flight_id}/packs/{ts}/gramet.png")
    assert first.status_code == 200
    cached = pack_dir / "gramet.png"
    assert cached.exists()
    assert cached.read_bytes() == first.content

    second = client.get(f"/api/flights/{flight_id}/packs/{ts}/gramet.png")
    assert second.status_code == 200
    assert second.content == first.content
