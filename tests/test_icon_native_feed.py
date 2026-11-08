"""ICON on DWD's native-grid v1 feed behind WB_ICON_FEED (#974, step 2)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from weatherbrief.fetch.grib import icon_eu_fetch as ief
from weatherbrief.fetch.grib import icon_grid
from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2, ICON_EU
from weatherbrief.fetch.grib.icon_grid import IconNativeGrid

UUID = "c6b12daa91ad64045b26c1b6452a2a20"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    icon_grid.clear_registry()
    ief._RUN_GRID_UUID.clear()
    monkeypatch.delenv(ief.ICON_FEED_ENV_VAR, raising=False)
    yield
    icon_grid.clear_registry()
    ief._RUN_GRID_UUID.clear()


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setenv(ief.ICON_FEED_ENV_VAR, "v1")


def _centres(seed: int = 0):
    rng = np.random.default_rng(seed)
    lat, lon = np.meshgrid(np.arange(45.0, 50.0, 0.1), np.arange(0.0, 5.0, 0.1), indexing="ij")
    return (
        lat.ravel() + rng.uniform(-0.03, 0.03, lat.size),
        lon.ravel() + rng.uniform(-0.03, 0.03, lon.size),
    )


# -- feed switch ----------------------------------------------------------------

@pytest.mark.parametrize("raw,feed", [
    ("", "legacy"), ("legacy", "legacy"), ("v1", "v1"), ("Native", "v1"), ("junk", "legacy"),
])
def test_feed_switch(monkeypatch, raw, feed):
    monkeypatch.setenv(ief.ICON_FEED_ENV_VAR, raw)
    assert ief.icon_feed() == feed
    assert ief.icon_native_feed() is (feed == "v1")


def test_legacy_default_keeps_cache_keys_and_urls():
    assert ief.icon_cache_prefix(ICON_EU) == "ICON_EU"
    assert ief.icon_rain_con_cache_key(ICON_EU) == ief.ICON_EU_RAIN_CON_CACHE_KEY
    assert ief.icon_cloud_diag_cache_key(ICON_EU) == ief.ICON_EU_CLOUD_DIAG_CACHE_KEY
    url = ief.icon_eu_single_level_url("20261107", 12, 6, "ceiling", ICON_EU)
    assert "regular-lat-lon" in url and url.endswith(".bz2")


def test_native_cache_keys_are_separate(native):
    assert ief.icon_cache_prefix(ICON_EU) == "ICON_EU_N"
    assert ief.icon_cache_prefix(ICON_D2) == "ICON_D2_N"
    assert ief.icon_rain_con_cache_key(ICON_EU) != ief.ICON_EU_RAIN_CON_CACHE_KEY
    assert ief.icon_cloud_diag_cache_key(ICON_EU) != ief.ICON_EU_CLOUD_DIAG_CACHE_KEY


def test_native_urls(native):
    base = ief.DWD_V1_BASE_URL
    assert ief.icon_eu_file_url("20261107", 12, 6, 60, "t", ICON_EU) == (
        f"{base}/icon-eu/p/T/lvt1/150/lv1/60/r/2026-11-07T12:00/s/PT006H00M.grib2"
    )
    assert ief.icon_eu_pressure_level_url("20261107", 12, 6, 850, "relhum", ICON_D2) == (
        f"{base}/icon-d2/p/RELHUM/lvt1/100/lv1/85000/r/2026-11-07T12:00/s/PT006H00M.grib2"
    )
    assert ief.icon_eu_single_level_url("20261107", 12, 6, "ceiling", ICON_D2) == (
        f"{base}/icon-d2/p/CEILING/r/2026-11-07T12:00/s/PT006H00M.grib2"
    )


def test_echotop_is_four_quarter_hour_files(native):
    urls = ief.icon_single_level_urls("20261107", 12, 6, "echotop", ICON_D2)
    assert [u.rsplit("/", 1)[-1] for u in urls] == [
        "PT006H00M.grib2", "PT006H15M.grib2", "PT006H30M.grib2", "PT006H45M.grib2",
    ]
    assert all("/p/ECHOTOP/lvt1/25/lv1/18/" in u for u in urls)


def test_legacy_single_level_urls_is_one():
    assert len(ief.icon_single_level_urls("20261107", 12, 6, "echotop", ICON_D2)) == 1


def test_eu_short_runs_reach_51h_on_native(monkeypatch):
    legacy = ief.icon_eu_model_level_max_hour(15, ICON_EU)
    monkeypatch.setenv(ief.ICON_FEED_ENV_VAR, "v1")
    assert ief.icon_eu_model_level_max_hour(15, ICON_EU) == 51
    assert legacy < 51
    assert ief.icon_eu_model_level_max_hour(12, ICON_EU) == 120


def test_per_model_sampling():
    assert ICON_EU.native_sampling == "barycentric"
    assert ICON_D2.native_sampling == "nearest"


# -- single-level fetch: no VIS, no bz2 ------------------------------------------

class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


def test_native_single_level_skips_vis_and_keeps_plain_grib(native, monkeypatch):
    asked = []

    def fake_get(session, url, timeout=None):
        asked.append(url)
        return _Resp(200, b"GRIB" + url.encode() + b"7777")

    monkeypatch.setattr(ief, "get_with_retry", fake_get)
    out = ief.fetch_icon_eu_single_level(
        "20261107", 12, [6], variables=["ceiling", "vis"], session=object(), variant=ICON_EU,
    )
    assert not any("/VIS/" in u for u in asked)
    assert out[6].startswith(b"GRIB")


def test_download_one_file_only_unpacks_bz2(monkeypatch):
    import bz2

    payload = b"GRIB....7777"
    for body in (payload, bz2.compress(payload)):
        monkeypatch.setattr(ief, "get_with_retry", lambda s, u, timeout=None, b=body: _Resp(200, b))
        data, status = ief._download_one_file("https://x/f.grib2", object())
        assert (data, status) == (payload, 200)


# -- ensure_icon_native_grid ------------------------------------------------------

class _Session:
    """Serves the Range read of CLAT and the full CLAT/CLON files."""

    def __init__(self, clat: bytes, clon: bytes):
        self.clat, self.clon = clat, clon
        self.calls: list[tuple[str, bool]] = []

    def get(self, url, headers=None, timeout=None, **_):
        ranged = bool(headers and "Range" in headers)
        self.calls.append((url, ranged))
        body = self.clat if "/CLAT/" in url else self.clon
        return _Resp(206, body[:2048]) if ranged else _Resp(200, body)


def _grid_session():
    eccodes = pytest.importorskip("eccodes")
    lat, lon = _centres()

    def msg(values):
        gid = eccodes.codes_grib_new_from_samples("GRIB2")
        try:
            eccodes.codes_set(gid, "gridDefinitionTemplateNumber", 101)
            eccodes.codes_set(gid, "numberOfGridUsed", 47)
            eccodes.codes_set(gid, "numberOfGridInReference", 1)
            eccodes.codes_set_string(gid, "uuidOfHGrid", UUID)
            eccodes.codes_set(gid, "numberOfDataPoints", values.size)
            eccodes.codes_set(gid, "packingType", "grid_ccsds")
            eccodes.codes_set_values(gid, values.astype(np.float64))
            return eccodes.codes_get_message(gid)
        finally:
            eccodes.codes_release(gid)

    return _Session(msg(lat), msg(lon))


def test_ensure_grid_builds_once_then_reads_disk(native, monkeypatch, tmp_path):
    session = _grid_session()
    monkeypatch.setattr(ief, "get_with_retry", lambda s, u, timeout=None: s.get(u))
    grid = ief.ensure_icon_native_grid(ICON_D2, "20261107", 12, session, tmp_path)
    assert grid is not None and grid.uuid == UUID
    assert grid.default_method == "nearest"
    assert (tmp_path / f"icon-grid-{ICON_D2.slug}.current").read_text() == UUID
    assert ief.current_icon_native_grid(ICON_D2, tmp_path) is grid
    n_calls = len(session.calls)

    # Same run, same process: no request at all.
    assert ief.ensure_icon_native_grid(ICON_D2, "20261107", 12, session, tmp_path) is grid
    assert len(session.calls) == n_calls

    # Another run, fresh process: only the 2 KB Range read, grid from disk.
    icon_grid.clear_registry()
    ief._RUN_GRID_UUID.clear()
    again = ief.ensure_icon_native_grid(ICON_D2, "20261107", 15, session, tmp_path)
    assert again is not None and again.uuid == UUID
    assert [ranged for _u, ranged in session.calls[n_calls:]] == [True]


def test_ensure_grid_none_when_clat_missing(native, tmp_path):
    class Missing:
        def get(self, *a, **k):
            return _Resp(404)

    assert ief.ensure_icon_native_grid(ICON_EU, "20261107", 12, Missing(), tmp_path) is None


# -- domain gate -------------------------------------------------------------------

def test_route_gate_uses_footprint_once_a_grid_is_known(native, monkeypatch, tmp_path):
    lat, lon = _centres()
    grid = IconNativeGrid(UUID, lat, lon)
    grid.save(icon_grid.grid_cache_path(tmp_path, UUID))
    (tmp_path / f"icon-grid-{ICON_D2.slug}.current").write_text(UUID)
    monkeypatch.setenv("DATA_DIR", str(tmp_path.parent))
    monkeypatch.setattr(icon_grid, "default_cache_root", lambda: tmp_path)

    pt = lambda la, lo: SimpleNamespace(lat=la, lon=lo)
    assert ief.route_in_icon_eu_domain([pt(47.0, 2.0)], ICON_D2)
    # Inside D2's bbox, outside this (synthetic) grid's footprint.
    assert not ief.route_in_icon_eu_domain([pt(52.0, 10.0)], ICON_D2)


# -- Hewson: nearest native cell on the lattice -------------------------------------

def test_hewson_native_lattice_is_nearest_cell(monkeypatch, tmp_path):
    from weatherbrief.hewson import grib_source

    lat, lon = _centres()
    grid = IconNativeGrid(UUID, lat, lon)
    grid.save(icon_grid.grid_cache_path(tmp_path, UUID))
    monkeypatch.setattr(icon_grid, "default_cache_root", lambda: tmp_path)

    la = np.array([46.0, 47.0])
    lo = np.array([1.0, 2.0, 3.0])
    cells = grib_source._native_lattice_cells(UUID, la, lo)
    assert cells.shape == (6,)
    want = grid.nearest_weights(*[a.ravel() for a in np.meshgrid(la, lo, indexing="ij")]).idx[:, 0]
    np.testing.assert_array_equal(cells, want)
    # A lattice point off the grid: skip the model, never shift the field.
    assert grib_source._native_lattice_cells(UUID, np.array([60.0]), lo) is None


# -- native decode branches -----------------------------------------------------

def _native_msg(values, *, short_name=None, uuid=UUID, bitmap=False) -> bytes:
    eccodes = pytest.importorskip("eccodes")
    gid = eccodes.codes_grib_new_from_samples("GRIB2")
    try:
        eccodes.codes_set_string(gid, "centre", "edzw")
        eccodes.codes_set(gid, "gridDefinitionTemplateNumber", 101)
        eccodes.codes_set(gid, "numberOfGridUsed", 47)
        eccodes.codes_set(gid, "numberOfGridInReference", 1)
        eccodes.codes_set_string(gid, "uuidOfHGrid", uuid)
        if short_name:
            eccodes.codes_set_string(gid, "shortName", short_name)
        eccodes.codes_set(gid, "numberOfDataPoints", values.size)
        vals = np.asarray(values, dtype=np.float64)
        if bitmap:
            eccodes.codes_set(gid, "bitmapPresent", 1)
            eccodes.codes_set(gid, "missingValue", 9999.0)
            vals = np.where(np.isnan(vals), 9999.0, vals)
        eccodes.codes_set(gid, "packingType", "grid_ccsds")
        eccodes.codes_set_values(gid, vals)
        return eccodes.codes_get_message(gid)
    finally:
        eccodes.codes_release(gid)


@pytest.fixture
def grid_on_disk(tmp_path, monkeypatch):
    """A grid saved under a run cache root that is NOT ``$DATA_DIR``."""
    lat, lon = _centres()
    grid = IconNativeGrid(UUID, lat, lon)
    root = tmp_path / "data" / ".cache" / "grib"
    grid.save(icon_grid.grid_cache_path(root, UUID))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
    icon_grid._MISSING_REPORTED.clear()
    return grid, root


def test_surface_decoder_finds_the_grid_next_to_the_run_file(grid_on_disk):
    from weatherbrief.fetch.grib.decode import decode_icon_surface_per_point

    grid, root = grid_on_disk
    run = root / "icon-eu" / "20261107_12z"
    run.mkdir(parents=True)
    path = run / "f006_ICON_EU_N_SFC_T_2M_V1.grib2"
    path.write_bytes(_native_msg(250.0 + grid.clat - grid.clon / 10.0))
    out = decode_icon_surface_per_point({"t_2m": path}, [47.0, 60.0], [2.0, 2.0])
    assert out[0]["t_2m"] == pytest.approx(250.0 + 47.0 - 0.2, abs=0.02)
    assert out[1] == {}   # outside the grid: missing, not 0


def test_missing_grid_skips_the_field(grid_on_disk, tmp_path, caplog):
    from weatherbrief.fetch.grib.decode import decode_icon_surface_per_point

    grid, _root = grid_on_disk
    blob = _native_msg(np.ones(grid.n_cells), uuid="1" * 32)
    with caplog.at_level("ERROR"):
        out = decode_icon_surface_per_point({"t_2m": blob, "pmsl": blob}, [47.0], [2.0])
    assert out == [{}]
    assert sum("not found" in r.message for r in caplog.records) == 1   # once per grid


def test_cloud_diag_native(grid_on_disk):
    from weatherbrief.fetch.grib.decode import decode_icon_eu_cloud_diag_per_point

    grid, _root = grid_on_disk
    icon_grid.register_grid(grid)
    blob = _native_msg(np.full(grid.n_cells, 55.0), short_name="clct") + _native_msg(
        np.full(grid.n_cells, 1200.0), short_name="ceil",
    )
    (pt,) = decode_icon_eu_cloud_diag_per_point(blob, [47.0], [2.0])
    assert pt["total_cover_pct"] == pytest.approx(55.0, abs=0.01)
    assert pt["ceiling_m"] == pytest.approx(1200.0, abs=0.5)


def test_d2_mask_and_corridor_native(grid_on_disk):
    from weatherbrief.fetch.grib.decode import (
        build_d2_validity_mask,
        d2_corridor_fully_valid,
    )

    grid, root = grid_on_disk
    values = np.ones(grid.n_cells)
    hole = int(np.argmin((grid.clat - 46.0) ** 2 + (grid.clon - 1.0) ** 2))
    values[hole] = np.nan
    icon_grid.register_grid(grid)   # ensure_icon_native_grid ran first
    mask = build_d2_validity_mask(_native_msg(values, bitmap=True))
    assert mask["uuid"] == UUID and int((~mask["valid"]).sum()) == 1
    icon_grid.clear_registry()
    mask["cache_root"] = str(root)   # as load_d2_validity_mask sets it
    assert d2_corridor_fully_valid(mask, [48.5], [3.5], radius_nm=5)
    assert not d2_corridor_fully_valid(mask, [46.0], [1.0], radius_nm=5)   # masked cell
    assert not d2_corridor_fully_valid(mask, [49.9], [2.5], radius_nm=20)  # off the edge


def test_d2_corridor_cells_native(grid_on_disk):
    from weatherbrief.fetch.grib.decode import _d2_field_cells

    grid, _root = grid_on_disk
    values = grid.clat.copy()
    cells = _d2_field_cells(("native", grid, values), 47.5, 2.5, radius_nm=10)
    assert cells.size > 3
    reach = 10 * 1.852 / 111.2   # 10 NM in degrees of latitude
    assert 47.5 + 0.05 < cells.max() <= 47.5 + reach + 1e-6
    assert _d2_field_cells(("native", grid, values), 49.95, 2.5, radius_nm=10) is None


def test_partial_echotop_hour_is_left_out(native, monkeypatch):
    def fake_get(session, url, timeout=None):
        if url.endswith("PT006H30M.grib2"):
            return _Resp(404)
        return _Resp(200, b"GRIB" + url.rsplit("/", 1)[-1].encode() + b"7777")

    monkeypatch.setattr(ief, "get_with_retry", fake_get)
    out = ief.fetch_icon_eu_single_level(
        "20261107", 12, [6], variables=["echotop"], session=object(), variant=ICON_D2,
    )
    assert 6 not in out


def test_echotop_windows_concatenate_in_order(native, monkeypatch):
    monkeypatch.setattr(
        ief, "get_with_retry",
        lambda s, url, timeout=None: _Resp(200, b"GRIB" + url.rsplit("/", 1)[-1].encode()),
    )
    out = ief.fetch_icon_eu_single_level(
        "20261107", 12, [6], variables=["echotop"], session=object(), variant=ICON_D2,
    )
    assert out[6] == b"".join(
        b"GRIBPT006H%02dM.grib2" % m for m in (0, 15, 30, 45)
    )
