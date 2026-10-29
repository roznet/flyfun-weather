"""Met Office UKV for the UKMO slot (#937), network-free.

Schedule and domain arithmetic, the cloud and surface conversions, the
remote NetCDF chunk reader on a local HDF5 file, and the no-fallback rule.
The real bucket was checked by hand when this landed (see the PR): height
levels are AGL, winds are true-north, a 3-step route read is ~33 MB.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.fetch.grib import nc_range, ukv
from weatherbrief.fetch.grib.primary import fill_grib_primary_gaps
from weatherbrief.models import ModelSource, RoutePoint

from tests.test_grib_primary import DEP, _FakeClient, _points, _sections

UTC = timezone.utc


def _route(*latlons: tuple[float, float]) -> list[RoutePoint]:
    return [
        RoutePoint(lat=la, lon=lo, distance_from_origin_nm=10.0 * i)
        for i, (la, lo) in enumerate(latlons)
    ]


def _line(a: tuple[float, float], b: tuple[float, float], n: int = 20) -> list[RoutePoint]:
    return _route(*[
        (a[0] + (b[0] - a[0]) * k / (n - 1), a[1] + (b[1] - a[1]) * k / (n - 1))
        for k in range(n)
    ])


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------


def test_domain_inside_and_outside():
    assert ukv.route_in_ukv_domain(_line((51.47, -0.45), (51.84, -1.32)))   # EGLL-EGTK
    assert ukv.route_in_ukv_domain(_line((49.01, 2.55), (50.03, 8.57)))     # LFPG-EDDF
    assert not ukv.route_in_ukv_domain(_line((51.47, -0.45), (40.47, -3.56)))  # EGLL-LEMD
    assert not ukv.route_in_ukv_domain(_route((43.54, 6.95)))               # LFMD
    assert not ukv.route_in_ukv_domain([])


def test_grid_corners_project_to_the_grid_origin():
    # The lower-left grid point, back through the projection.
    from pyproj import Transformer

    inv = Transformer.from_crs(ukv.UKV_PROJ, "EPSG:4326", always_xy=True)
    lon, lat = inv.transform(ukv.UKV_X0_M, ukv.UKV_Y0_M)
    x, y = ukv.project_points([lat], [lon])
    assert x[0] == pytest.approx(ukv.UKV_X0_M, abs=1.0)
    assert y[0] == pytest.approx(ukv.UKV_Y0_M, abs=1.0)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cycle,hours", [(3, 120), (15, 120), (0, 54), (21, 54), (1, 12), (14, 12)])
def test_run_horizon(cycle, hours):
    assert ukv.ukv_run_horizon_h(cycle) == hours


def test_published_runs_respect_the_delay():
    as_of = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    runs = ukv.published_ukv_runs(as_of)
    assert runs[0] == as_of.replace(hour=5)   # 05Z + 6h30 = 11:30 <= 12:00
    assert runs[1] == as_of.replace(hour=4)


def test_native_steps_hourly_then_three_hourly():
    run = datetime(2026, 10, 25, 3, tzinfo=UTC)
    hrs = [run + timedelta(hours=h) for h in (10, 11, 12)]
    assert ukv.native_steps(run, hrs) == [10, 11, 12]
    hrs = [run + timedelta(hours=h) for h in (55, 56, 57)]
    assert ukv.native_steps(run, hrs) == [54, 57]


def test_candidate_runs_newest_covering_run_first():
    as_of = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    # Short window just ahead: the newest (+12 h) run already covers it.
    near = [datetime(2026, 10, 25, 14, tzinfo=UTC), datetime(2026, 10, 25, 15, tzinfo=UTC)]
    assert ukv.candidate_runs(near, as_of)[0] == datetime(2026, 10, 25, 5, tzinfo=UTC)
    # Two days out: only 00/03Z (54/120 h) runs reach it.
    far = [datetime(2026, 10, 27, 9, tzinfo=UTC), datetime(2026, 10, 27, 10, tzinfo=UTC)]
    runs = ukv.candidate_runs(far, as_of)
    assert runs[0] == datetime(2026, 10, 25, 3, tzinfo=UTC)
    assert all(r.hour in ukv.UKV_LONG_CYCLES | ukv.UKV_MID_CYCLES for r in runs)
    # Past the 03Z run's 120 h: nothing.
    beyond = [datetime(2026, 10, 30, 9, tzinfo=UTC)]
    assert ukv.candidate_runs(beyond, as_of) == []


def test_candidate_runs_skip_a_run_starting_at_the_window():
    # Step 0 has no accumulations, so a run at the first hour is not usable.
    as_of = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    hrs = [datetime(2026, 10, 25, 5, tzinfo=UTC), datetime(2026, 10, 25, 6, tzinfo=UTC)]
    assert all(r < hrs[0] for r in ukv.candidate_runs(hrs, as_of))


def test_window_out_of_range_and_horizon_end():
    as_of = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    assert not ukv.ukv_window_out_of_range(datetime(2026, 10, 25, 15, tzinfo=UTC), 1.5, as_of)
    assert ukv.ukv_window_out_of_range(datetime(2026, 10, 30, 9, tzinfo=UTC), 1.5, as_of)
    # Newest 03Z run (published by 09:30) + 120 h.
    assert ukv.ukv_horizon_end(as_of) == datetime(2026, 10, 30, 3, tzinfo=UTC)


def test_global_runs_and_time_weights():
    as_of = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    runs = ukv.global_candidate_runs(datetime(2026, 10, 25, 5, tzinfo=UTC), as_of)
    assert len(runs) == 2 and all(r.hour % 6 == 0 for r in runs) and runs[0] > runs[1]
    run = datetime(2026, 10, 25, 0, tzinfo=UTC)
    assert ukv._global_steps_for(run, run + timedelta(hours=7)) == [(7, 1.0)]
    parts = ukv._global_steps_for(run, run + timedelta(hours=7, minutes=30))
    assert [s for s, _ in parts] == [7, 8] and sum(w for _, w in parts) == pytest.approx(1.0)
    assert ukv._global_steps_for(run, run - timedelta(hours=1)) == []


def test_step_zero_has_no_accumulations():
    assert not any("PT0" in p for p in ukv.surface_params_for(0))
    assert "precipitation_accumulation-PT01H" in ukv.surface_params_for(1)
    assert "precipitation_accumulation-PT03H" in ukv.surface_params_for(57)
    assert "temperature_at_screen_level" in ukv.surface_params_for(0)


# ---------------------------------------------------------------------------
# Cloud, ceiling, conversions
# ---------------------------------------------------------------------------


def test_cloud_fraction_at_heights():
    h = ukv._UKV_HEIGHTS_M
    f = np.zeros_like(h)
    f[h >= 1000] = 1.0
    assert ukv.cloud_fraction_at_heights(h, f, 1500.0) == 1.0
    assert ukv.cloud_fraction_at_heights(h, f, 900.0) == pytest.approx(0.5)
    assert ukv.cloud_fraction_at_heights(h, f, 6500.0) is None   # above 6 km AGL
    assert ukv.cloud_fraction_at_heights(h, f, -10.0) is None    # below ground
    assert ukv.cloud_fraction_at_heights(h, np.full_like(h, np.nan), 500.0) is None


def test_ceiling_is_the_lowest_fifty_percent_crossing():
    h = ukv._UKV_HEIGHTS_M
    f = np.zeros_like(h)
    f[(h >= 700) & (h <= 1000)] = 0.8
    # 600 m at 0, 700 m at 0.8: crosses 0.5 at 662.5 m.
    assert ukv.ceiling_agl_m(h, f) == pytest.approx(662.5)
    assert ukv.ceiling_agl_m(h, np.full_like(h, 0.4)) is None
    assert ukv.ceiling_agl_m(h, np.full_like(h, 0.9)) == 5.0


def test_surface_snapshot_units():
    raw = {
        "t2m_k": 283.15, "td2m_k": 278.15, "rh2m_fraction": 0.7,
        "mslp_pa": 101300.0, "ws10_m_s": 10.0, "wd10_deg": 270.0,
        "precip_m": 0.003, "snow_m_we": 0.001, "total_cloud_fraction": 1.2,
    }
    out = ukv.surface_snapshot(raw, period_h=3)
    assert out["temperature_2m_c"] == pytest.approx(10.0)
    assert out["relative_humidity_2m_pct"] == pytest.approx(70.0)
    assert out["pressure_msl_hpa"] == pytest.approx(1013.0)
    assert out["wind_speed_10m_kt"] == pytest.approx(19.44, abs=0.01)
    assert out["precipitation_mm"] == pytest.approx(1.0)     # 3 mm over 3 h
    assert out["snowfall_cm"] == pytest.approx(0.7 / 3, abs=1e-6)
    assert out["cloud_cover_pct"] == 100.0                   # clipped
    assert out["surface_pressure_hpa"] is None


def test_cloud_diagnostics_heights():
    raw = {
        "low_cloud_fraction": 0.6, "total_cloud_fraction": 0.9,
        "cloud_base_agl_m": 300.0, "ceiling_agl_m": 600.0,
        "freezing_level_agl_m": 1000.0, "orography_m": 500.0,
    }
    d = ukv.cloud_diagnostics(raw)
    assert d.low.cover_pct == pytest.approx(60.0)
    assert d.low.base_ft == round(300 * 3.28084)
    assert d.ceiling_ft == round(600 * 3.28084)
    assert d.freezing_level_ft == round(1500 * 3.28084)     # AGL + orography → MSL
    assert d.convective_scheme_absent


def test_wind_from_direction_to_uv():
    u, v = ukv._uv_from_speed_dir(10.0, 270.0)   # westerly blows east
    assert u == pytest.approx(10.0) and v == pytest.approx(0.0, abs=1e-9)
    u, v = ukv._uv_from_speed_dir(10.0, 0.0)     # northerly blows south
    assert u == pytest.approx(0.0, abs=1e-9) and v == pytest.approx(-10.0)


def test_level_coordinate_check():
    pa = np.asarray(ukv._ALL_PRESSURE_LEVELS_HPA, float) * 100.0
    ukv.check_level_coords("temperature_on_pressure_levels", {"pressure": pa})
    ukv.check_level_coords(ukv._CLOUD_HL_PARAM, {"height": ukv._UKV_HEIGHTS_M})
    ukv.check_level_coords("x", {})   # cache hit: nothing to check
    with pytest.raises(ukv.UkvLayoutChanged):
        ukv.check_level_coords("temperature_on_pressure_levels", {"pressure": pa[::-1]})
    with pytest.raises(ukv.UkvLayoutChanged):
        ukv.check_level_coords(ukv._CLOUD_HL_PARAM, {"height": ukv._UKV_HEIGHTS_M[:-1]})


# ---------------------------------------------------------------------------
# Remote NetCDF chunk reader, on a local HDF5 file
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, content: bytes, lo: int, hi: int, size: int):
        self.content = content
        self.headers = {"Content-Range": f"bytes {lo}-{hi}/{size}"}


@pytest.fixture
def local_nc(tmp_path, monkeypatch):
    """A UKV-shaped file (gzip, (1,128,128) chunks, v1 B-tree), served by range."""
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "f.nc"
    rng = np.random.default_rng(0)
    data = rng.random((3, 300, 400), dtype=np.float32)
    data[0, 0, 0] = 9.96921e36
    # libver v108 writes what netCDF4 does: superblock v2, object header v2,
    # layout message v3 with a v1 B-tree chunk index (162 chunks → 2 levels).
    with h5py.File(path, "w", libver=("v108", "v108")) as f:
        f.create_dataset("pressure", data=np.array([100000.0, 85000.0, 50000.0]))
        f.create_dataset(
            "air_temperature", data=data, chunks=(1, 128, 128), compression="gzip",
            fillvalue=np.float32(9.96921e36),
        )
    blob = path.read_bytes()
    requests: list[tuple[int, int]] = []

    def fake_get_range(session, url, lo, hi, stats):
        requests.append((lo, hi))
        if stats is not None:
            stats.add(len(blob[lo:hi + 1]))
        return _Resp(blob[lo:hi + 1], lo, hi, len(blob))

    monkeypatch.setattr(nc_range, "_get_range", fake_get_range)
    return data, requests


def test_read_chunks_matches_the_array(local_nc, caplog):
    data, _ = local_nc
    keys = {(0, 0, 0), (1, 128, 256), (2, 256, 384), (2, 0, 128)}
    stats = nc_range.TransferStats()
    with ThreadPoolExecutor(4) as pool, caplog.at_level("INFO"):
        var, out = nc_range.read_chunks(
            None, "mem://f.nc", keys, coord_names=("pressure",), pool=pool, stats=stats,
        )
    # The custom B-tree walk ran, not the h5py fallback.
    assert "using h5py's chunk lookup" not in caplog.text
    assert var.name == "air_temperature" and var.chunks == (1, 128, 128)
    assert var.coords["pressure"].tolist() == [100000.0, 85000.0, 50000.0]
    assert set(out) == keys
    for (lv, r, c), arr in out.items():
        want = np.full((1, 128, 128), np.nan, np.float32)
        part = data[lv:lv + 1, r:r + 128, c:c + 128]
        want[:, :part.shape[1], :part.shape[2]] = part
        if (lv, r, c) == (0, 0, 0):
            want[0, 0, 0] = np.nan   # fill value
        np.testing.assert_array_equal(arr[:, :part.shape[1], :part.shape[2]],
                                      want[:, :part.shape[1], :part.shape[2]])
    assert stats.requests > 0 and stats.bytes > 0


def test_read_chunks_absent_key_is_left_out(local_nc):
    with ThreadPoolExecutor(2) as pool:
        _, out = nc_range.read_chunks(None, "mem://f.nc", {(0, 0, 0), (9, 0, 0)}, pool=pool)
    assert set(out) == {(0, 0, 0)}


def test_merge_ranges_joins_close_chunks():
    refs = [
        ((0,), nc_range._ChunkRef(1000, 100, 0)),
        ((1,), nc_range._ChunkRef(1200, 100, 0)),        # 100 B gap: merged
        ((2,), nc_range._ChunkRef(100_000, 100, 0)),     # far: own request
    ]
    groups = nc_range._merge_ranges(refs)
    assert [[k for k, _ in g] for g in groups] == [[(0,), (1,)], [(2,)]]


# ---------------------------------------------------------------------------
# No Open-Meteo fallback
# ---------------------------------------------------------------------------


def test_uncovered_ukmo_slot_is_dropped_not_filled():
    pts = _points()
    sections = _sections(ModelSource.UKMO, pts, covered={0})
    client = _FakeClient()
    (out,) = fill_grib_primary_gaps(
        sections, pts, [ModelSource.UKMO], DEP, 2.0,
        client=client, start_date="2026-05-04", end_date="2026-05-04",
    )
    assert client.call_count == 0
    assert out.failed and out.no_fallback == "grib_incomplete"


def test_covered_ukmo_slot_is_kept():
    pts = _points()
    sections = _sections(ModelSource.UKMO, pts, covered={0, 1, 2})
    client = _FakeClient()
    (out,) = fill_grib_primary_gaps(
        sections, pts, [ModelSource.UKMO], DEP, 2.0,
        client=client, start_date="2026-05-04", end_date="2026-05-04",
    )
    assert client.call_count == 0 and not out.failed


def test_ukmo_gate_reasons():
    from weatherbrief.fetch.grib.primary import _ukmo_skip_reason

    as_of = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    uk = _line((51.47, -0.45), (51.84, -1.32))
    dep = datetime(2026, 10, 25, 15, tzinfo=UTC)
    assert _ukmo_skip_reason(uk, dep, 1.0, as_of, False) is None
    assert _ukmo_skip_reason(uk, dep, 1.0, as_of, True) == "historical"
    assert _ukmo_skip_reason(_route((43.54, 6.95)), dep, 1.0, as_of, False) == "out_of_domain"
    late = datetime(2026, 10, 30, 9, tzinfo=UTC)
    assert _ukmo_skip_reason(uk, late, 1.0, as_of, False) == "out_of_range"


# ---------------------------------------------------------------------------
# Wind blending and read_step failure handling (PR #943 review)
# ---------------------------------------------------------------------------


def _stencil_mid(n: int = 1):
    # Every point halfway between its four corners.
    return ukv._Stencil(
        iy=np.zeros((n, 2), int), ix=np.zeros((n, 2), int),
        wy=np.full(n, 0.5), wx=np.full(n, 0.5),
    )


def test_wind_blend_keeps_a_northerly_across_the_wrap():
    st = _stencil_mid()
    speed = np.full((1, 2, 2), 10.0)
    from_deg = np.array([[[350.0, 10.0], [350.0, 10.0]]])
    spd, wdir = ukv._blend_wind(st, speed, from_deg)
    assert min(wdir[0], 360.0 - wdir[0]) < 1e-6   # 000°, not 180°
    assert spd[0] == pytest.approx(10.0 * np.cos(np.radians(10.0)))


def test_wind_blend_per_level_and_nan_corner():
    st = _stencil_mid()
    speed = np.full((1, 2, 2, 2), 10.0)
    from_deg = np.full((1, 2, 2, 2), 270.0)
    from_deg[0, 1] = 90.0
    speed[0, 0, 0, 0] = np.nan                   # missing corner drops out
    spd, wdir = ukv._blend_wind(st, speed, from_deg)
    assert spd.shape == (1, 2)
    assert wdir[0, 0] == pytest.approx(270.0) and wdir[0, 1] == pytest.approx(90.0)
    assert spd[0, 0] == pytest.approx(10.0)


def _fake_fetch(monkeypatch, fail: dict[str, Exception]):
    fetch = ukv.UkvFetch(
        run=datetime(2026, 10, 25, 9, tzinfo=UTC), lats=[51.8], lons=[-1.3],
        session=None, run_dir=None,
    )
    n_pl = len(ukv.UKV_PRESSURE_LEVELS)
    pl = {
        "temperature_on_pressure_levels": 280.0,
        "relative_humidity_on_pressure_levels": 0.8,
        "wind_speed_on_pressure_levels": 10.0,
    }
    sfc_raw = {"t2m_k": 283.0, "ws10_m_s": 5.0}

    def fake_read(step, param, n_levels, pool, chunk_pool, level_indices=None, corners=False):
        if param in fail:
            raise fail[param]
        if param == ukv._CLOUD_HL_PARAM:
            return np.zeros((1, 33)), {}
        if param == ukv._OROGRAPHY_PARAM:
            return np.array([80.0]), {}
        if n_levels is not None:
            shape = (1, n_pl, 2, 2) if corners else (1, n_pl)
            if param == "geopotential_height_on_pressure_levels":
                return np.broadcast_to(np.linspace(100, 13000, n_pl)[None, :], (1, n_pl)).copy(), {}
            if param == "wind_direction_on_pressure_levels":
                d = np.full(shape, 350.0)
                d[..., 1] = 10.0
                return d, {}
            return np.full(shape, pl[param]), {}
        key = ukv._SURFACE_PARAMS.get(param, ukv._SURFACE_PARAMS.get(
            param.replace("PT01H", "PT{p:02d}H"), (None,)))[0]
        if corners:
            if key == "wd10_deg":
                d = np.full((1, 2, 2), 350.0)
                d[..., 1] = 10.0
                return d, {}
            return np.full((1, 2, 2), sfc_raw.get(key, 1.0)), {}
        return np.array([sfc_raw.get(key, 1.0)]), {}

    monkeypatch.setattr(fetch, "_read_field", fake_read)
    monkeypatch.setattr(fetch, "global_w_at", lambda valid, cp: None)
    return fetch


def test_read_step_blends_winds_and_loses_only_a_failed_optional_field(monkeypatch):
    fetch = _fake_fetch(monkeypatch, {"visibility_at_screen_level": RuntimeError("timeout")})
    with ThreadPoolExecutor(4) as pool:
        st = fetch.read_step(3, pool, pool)
    assert st.complete, st.problem
    sfc = st.surface[0]
    assert "vis_m" not in sfc
    # Corners at 350°/010°: near north (scalar blending gave ~180°).
    assert min(sfc["wd10_deg"], 360 - sfc["wd10_deg"]) < 10.0
    lvl = st.levels[0][850]
    assert lvl["raw_v_wind_m_s"] < -9.0          # northerly: blowing south


def test_read_step_fails_on_a_required_field(monkeypatch):
    fetch = _fake_fetch(monkeypatch, {"pressure_at_mean_sea_level": RuntimeError("timeout")})
    with ThreadPoolExecutor(4) as pool:
        st = fetch.read_step(3, pool, pool)
    assert not st.complete and "pressure_at_mean_sea_level" in st.problem


def test_cached_tiles_still_return_level_coordinates(tmp_path, monkeypatch):
    calls = []

    def fake_read_chunks(session, url, keys, *, coord_names=(), pool, stats=None, variable=None):
        calls.append(set(keys))
        var = nc_range.ChunkedVariable(
            url=url, name="x", shape=(2, 128, 128), chunks=(1, 128, 128),
            dtype=np.float32, fill_value=None, attrs={},
            coords={"pressure": np.array([100000.0, 85000.0])},
        )
        return var, {k: np.ones((1, 128, 128), np.float32) for k in keys}

    monkeypatch.setattr(ukv, "read_chunks", fake_read_chunks)
    url = "https://x/run/f-temperature_on_pressure_levels.nc"
    stats = nc_range.TransferStats()
    _, c1 = ukv.read_tiles(None, url, {(0, 0, 0)}, run_dir=tmp_path, pool=None,
                           stats=stats, coord_names=("pressure",))
    _, c2 = ukv.read_tiles(None, url, {(0, 0, 0)}, run_dir=tmp_path, pool=None,
                           stats=stats, coord_names=("pressure",))
    assert calls == [{(0, 0, 0)}]                 # second read fully cached
    assert c2["pressure"].tolist() == c1["pressure"].tolist() == [100000.0, 85000.0]
    # A cache from before coordinates were stored re-reads the header only.
    for p in tmp_path.rglob("coord_*.npy"):
        p.unlink()
    _, c3 = ukv.read_tiles(None, url, {(0, 0, 0)}, run_dir=tmp_path, pool=None,
                           stats=stats, coord_names=("pressure",))
    assert calls[-1] == set() and c3["pressure"].tolist() == [100000.0, 85000.0]


def test_run_fetch_skips_ukmo_when_no_run_covers_the_window(monkeypatch):
    """The fetch-stage skip uses the gate's own coverage test, so UKMO is
    never refused by the gate as out_of_range and fetched from Open-Meteo."""
    from unittest.mock import patch

    from weatherbrief.tasks import fetch as fetch_module
    from tests.test_fetch_parallel import _disable_elevation, _route

    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    # At 09:00Z the 03Z run is not out yet (09:30Z): the newest +120 h run is
    # the previous 15Z, reaching 10-29 15Z, so a 16Z departure (< 5 days out)
    # has no UKV run.
    as_of = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    dep = datetime(2026, 10, 29, 16, 0, tzinfo=UTC)
    om_calls: list[str] = []

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        om_calls.append(model.value)
        return _FakeClient().fetch_multi_point(points, model, start_date=start_date, end_date=end_date)

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(fetch_module, "_open_meteo_live_horizons", return_value={}), patch(
        "weatherbrief.fetch.grib.enrich_forecasts", return_value=({}, {}, {}),
    ):
        result = fetch_module.run_fetch(
            route=_route(), departure_time=dep, models=[ModelSource.UKMO],
            enrich_grib=True, as_of_time=as_of,
        )
    assert "ukmo" not in om_calls
    assert "ukmo" not in result.models_fetched
    from weatherbrief.models.diagnostic_codes import FetchCode

    assert any(d.code == FetchCode.MODEL_SKIPPED_HORIZON and "UKMO" in d.message
               for d in result.diagnostics)
