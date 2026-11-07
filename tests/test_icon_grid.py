"""ICON native-grid sampler and reader (#974)."""

from __future__ import annotations

import numpy as np
import pytest

from weatherbrief.fetch.grib import icon_grid
from weatherbrief.fetch.grib.icon_grid import (
    IconNativeGrid,
    NativeWeights,
    gather_native,
    get_grid,
    grid_cache_path,
    register_grid,
)

UUID = "c6b12daa91ad64045b26c1b6452a2a20"


def _jittered_centres(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """~0.1 degree cells over 45-50N, 0-5E, jittered so no lattice is exact."""
    rng = np.random.default_rng(seed)
    lat, lon = np.meshgrid(np.arange(45.0, 50.0, 0.1), np.arange(0.0, 5.0, 0.1), indexing="ij")
    lat = lat.ravel() + rng.uniform(-0.03, 0.03, lat.size)
    lon = lon.ravel() + rng.uniform(-0.03, 0.03, lon.size)
    return lat, lon


@pytest.fixture
def grid() -> IconNativeGrid:
    lat, lon = _jittered_centres()
    return IconNativeGrid(UUID, lat, lon)


@pytest.fixture(autouse=True)
def _clean_registry():
    icon_grid.clear_registry()
    yield
    icon_grid.clear_registry()


def _linear_field(lat, lon):
    return 3.0 * np.asarray(lat) - 2.0 * np.asarray(lon) + 100.0


def test_spacing_matches_the_cells(grid):
    # 0.1 degree of latitude is ~11 km, of longitude at 47.5N ~7.5 km.
    assert 5.0 < grid.spacing_km < 12.0
    assert grid.n_cells == 50 * 50


def test_barycentric_reproduces_a_linear_field(grid):
    values = _linear_field(grid.clat, grid.clon).astype(np.float32)
    rng = np.random.default_rng(1)
    lats = rng.uniform(45.3, 49.6, 200)
    lons = rng.uniform(0.3, 4.6, 200)
    nw = grid.barycentric_weights(lats, lons)
    assert nw.inb_idx.tolist() == list(range(200))
    assert nw.idx.shape == (200, 3)
    np.testing.assert_allclose(nw.w.sum(axis=1), 1.0)
    assert (nw.w >= 0).all()
    got = gather_native(values, nw)
    # Gnomonic triangles are not exactly linear in lat/lon; a few 1e-3 of
    # the field's ~10-unit range is the expected residual.
    np.testing.assert_allclose(got, _linear_field(lats, lons), atol=5e-3)


def test_barycentric_on_a_centre_returns_that_cell(grid):
    values = np.arange(grid.n_cells, dtype=np.float32)
    i = 1234
    got = gather_native(values, grid.barycentric_weights([grid.clat[i]], [grid.clon[i]]))
    assert got[0] == pytest.approx(float(i), abs=1e-6)


def test_nearest_picks_the_closest_centre(grid):
    values = np.arange(grid.n_cells, dtype=np.float32)
    i = 777
    nw = grid.nearest_weights([grid.clat[i] + 0.005], [grid.clon[i] - 0.005])
    assert nw.idx.shape == (1, 1)
    assert gather_native(values, nw)[0] == float(i)


def test_points_outside_the_footprint_are_left_out(grid):
    lats = [47.0, 52.0, 47.0, 44.9]
    lons = [2.0, 2.0, 7.0, 2.0]   # inside, north, east, just south (in margin)
    for method in ("barycentric", "nearest"):
        nw = grid.weights(lats, lons, method)
        assert nw.inb_idx.tolist() == [0, 3], method
    assert grid.contains(lats, lons).tolist() == [True, False, False, True]


def test_masked_cell_blanks_only_with_weight(grid):
    values = np.ones(grid.n_cells, dtype=np.float32)
    i = 1500
    nw = grid.barycentric_weights([grid.clat[i]], [grid.clon[i]])
    # The target sits on cell i: weight 1 there, 0 on the other two corners.
    others = [c for c, w in zip(nw.idx[0], nw.w[0]) if w == 0.0 and c != i]
    masked = values.copy()
    for c in others:
        masked[c] = np.nan
    assert gather_native(masked, nw)[0] == pytest.approx(1.0)
    masked[i] = np.nan
    assert np.isnan(gather_native(masked, nw)[0])


def test_gather_broadcasts_over_leading_axes(grid):
    stack = np.stack([np.full(grid.n_cells, k, dtype=np.float32) for k in range(4)])
    nw = grid.barycentric_weights([46.0, 48.0], [1.0, 3.0])
    out = gather_native(stack, nw)
    assert out.shape == (4, 2)
    np.testing.assert_allclose(out[:, 0], [0, 1, 2, 3])


def test_radians_are_detected():
    lat, lon = _jittered_centres()
    g = IconNativeGrid(UUID, np.radians(lat), np.radians(lon))
    np.testing.assert_allclose(g.clat, lat)
    np.testing.assert_allclose(g.clon, lon)


def test_unknown_method_raises(grid):
    with pytest.raises(ValueError):
        grid.weights([47.0], [2.0], "bilinear")


def test_save_load_and_registry(grid, tmp_path):
    path = grid_cache_path(tmp_path, UUID)
    grid.save(path)
    assert path.exists()
    assert get_grid(UUID) is None                 # not in-process yet
    loaded = get_grid(UUID, cache_root=tmp_path)  # from disk, then registered
    assert loaded is not None
    np.testing.assert_array_equal(loaded.clat, grid.clat)
    assert get_grid(UUID) is loaded
    assert register_grid(grid) is loaded          # first registration wins
    assert get_grid("0" * 32, cache_root=tmp_path) is None


def test_empty_weights_gather():
    nw = NativeWeights(np.zeros((0, 3), np.intp), np.zeros((0, 3)), np.zeros(0, np.intp))
    assert gather_native(np.ones(10, np.float32), nw).shape == (0,)


# -- GRIB round trip: template 101 messages as DWD's v1 feed delivers them ---

eccodes = pytest.importorskip("eccodes")


def _native_message(values: np.ndarray, uuid: str = UUID, *, bitmap: bool = False) -> bytes:
    gid = eccodes.codes_grib_new_from_samples("GRIB2")
    try:
        eccodes.codes_set(gid, "gridDefinitionTemplateNumber", 101)
        eccodes.codes_set(gid, "numberOfGridUsed", 47)
        eccodes.codes_set(gid, "numberOfGridInReference", 1)
        eccodes.codes_set_string(gid, "uuidOfHGrid", uuid)
        eccodes.codes_set(gid, "numberOfDataPoints", values.size)
        if bitmap:
            eccodes.codes_set(gid, "bitmapPresent", 1)
            eccodes.codes_set(gid, "missingValue", 9999.0)
            values = np.where(np.isnan(values), 9999.0, values)
        eccodes.codes_set(gid, "packingType", "grid_ccsds")
        eccodes.codes_set_values(gid, values.astype(np.float64))
        return eccodes.codes_get_message(gid)
    finally:
        eccodes.codes_release(gid)


def test_grid_from_clat_clon_grib():
    lat, lon = _jittered_centres()
    g = IconNativeGrid.from_grib(_native_message(lat), _native_message(lon))
    assert g.uuid == UUID
    np.testing.assert_allclose(g.clat, lat, atol=1e-3)  # CCSDS packing precision


def test_grid_from_grib_rejects_mismatched_uuids():
    lat, lon = _jittered_centres()
    with pytest.raises(ValueError):
        IconNativeGrid.from_grib(_native_message(lat), _native_message(lon, "1" * 32))


def test_reader_unpacks_native_values_with_bitmap():
    from weatherbrief.fetch.grib.grib_reader import iter_messages

    vals = np.arange(20, dtype=np.float64) + 250.0
    vals[[3, 7]] = np.nan
    msgs = []
    for msg in iter_messages(_native_message(vals, bitmap=True)):
        assert msg.grid_type == "unstructured_grid"
        assert msg.uuid == UUID
        assert msg.grid() is None          # no lat/lon axes
        msgs.append(msg.native_values())
    (got,) = msgs
    assert got.dtype == np.float32
    assert np.isnan(got[[3, 7]]).all()
    np.testing.assert_allclose(np.delete(got, [3, 7]), np.delete(vals, [3, 7]), atol=1e-2)


def test_regular_grid_messages_have_no_uuid():
    from weatherbrief.fetch.grib.grib_reader import iter_messages

    gid = eccodes.codes_grib_new_from_samples("GRIB2")
    blob = eccodes.codes_get_message(gid)
    eccodes.codes_release(gid)
    for msg in iter_messages(blob):
        assert msg.uuid == ""
        assert msg.native_values() is None


# -- v1 URL layout ------------------------------------------------------------

def test_v1_urls():
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_D2,
        ICON_EU,
        ICON_V1_LEVEL_ECHOTOP,
        ICON_V1_LEVEL_MODEL,
        ICON_V1_LEVEL_PRESSURE,
        icon_v1_grid_urls,
        icon_v1_url,
    )

    base = "https://opendata.dwd.de/weather/nwp/v1/m"
    assert icon_v1_url(ICON_D2, "ceiling", "20261107", 15, 3) == (
        f"{base}/icon-d2/p/CEILING/r/2026-11-07T15:00/s/PT003H00M.grib2"
    )
    assert icon_v1_url(
        ICON_EU, "t", "20261107", 15, 3, level_type=ICON_V1_LEVEL_MODEL, level=60,
    ) == f"{base}/icon-eu/p/T/lvt1/150/lv1/60/r/2026-11-07T15:00/s/PT003H00M.grib2"
    assert icon_v1_url(
        ICON_EU, "relhum", "20261107", 0, 117,
        level_type=ICON_V1_LEVEL_PRESSURE, level=85000,
    ) == f"{base}/icon-eu/p/RELHUM/lvt1/100/lv1/85000/r/2026-11-07T00:00/s/PT117H00M.grib2"
    assert icon_v1_url(
        ICON_D2, "echotop", "20261107", 12, 0,
        level_type=ICON_V1_LEVEL_ECHOTOP, level=18, minutes=45,
    ) == f"{base}/icon-d2/p/ECHOTOP/lvt1/25/lv1/18/r/2026-11-07T12:00/s/PT000H45M.grib2"
    clat, clon = icon_v1_grid_urls(ICON_EU, "20261107", 15)
    assert clat.endswith("/icon-eu/p/CLAT/r/2026-11-07T15:00/s/PT000H00M.grib2")
    assert clon.endswith("/icon-eu/p/CLON/r/2026-11-07T15:00/s/PT000H00M.grib2")
    with pytest.raises(ValueError):
        icon_v1_url(ICON_EU, "t", "20261107", 15, 3, level=60)


def test_v1_run_label_rejects_malformed_dates():
    from weatherbrief.fetch.grib.icon_eu_fetch import icon_v1_run_label

    with pytest.raises(ValueError):
        icon_v1_run_label("2026-11-07", 15)


def test_save_leaves_no_temp_files(grid, tmp_path):
    path = grid_cache_path(tmp_path, UUID)
    grid.save(path)
    grid.save(path)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
