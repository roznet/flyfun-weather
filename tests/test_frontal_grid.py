"""Tests for weatherbrief.frontal.grid — grid coords, wind, NaN handling, terrain."""

import numpy as np
import pytest
from scipy.interpolate import griddata

import weatherbrief.frontal.grid as grid_mod

from weatherbrief.frontal.grid import (
    build_grid_coords,
    build_grid_points,
    wind_to_uv,
    prepare_field,
    fill_terrain,
    clear_fill_terrain_cache,
    compute_theta_e,
    terrain_mask_for_level,
)


class TestBuildGridCoords:
    def test_shape(self):
        lat, lon = build_grid_coords()
        assert len(lat) == 101  # 35.0 to 60.0 in 0.25 steps
        assert len(lon) == 193  # -20.0 to 28.0 in 0.25 steps

    def test_bounds(self):
        lat, lon = build_grid_coords()
        assert lat[0] == pytest.approx(35.0)
        assert lat[-1] == pytest.approx(60.0)
        assert lon[0] == pytest.approx(-20.0)
        assert lon[-1] == pytest.approx(28.0)

    def test_spacing(self):
        lat, lon = build_grid_coords()
        assert np.diff(lat).mean() == pytest.approx(0.25)
        assert np.diff(lon).mean() == pytest.approx(0.25)


class TestBuildGridPoints:
    def test_count(self):
        lat, lon = build_grid_coords()
        points = build_grid_points(lat, lon)
        assert len(points) == 101 * 193

    def test_lat_major_order(self):
        lat = np.array([35.0, 35.5])
        lon = np.array([-12.0, -11.5, -11.0])
        points = build_grid_points(lat, lon)
        # First row: all lons at lat=35.0
        assert points[0] == (35.0, -12.0)
        assert points[1] == (35.0, -11.5)
        assert points[2] == (35.0, -11.0)
        # Second row: all lons at lat=35.5
        assert points[3] == (35.5, -12.0)


class TestWindToUV:
    """Verify meteorological convention: direction is where wind comes FROM."""

    def test_westerly(self):
        """270° = from west → u > 0, v ≈ 0."""
        u, v = wind_to_uv(np.array([10.0]), np.array([270.0]))
        assert u[0] > 0
        assert abs(v[0]) < 0.01

    def test_southerly(self):
        """180° = from south → u ≈ 0, v > 0."""
        u, v = wind_to_uv(np.array([10.0]), np.array([180.0]))
        assert abs(u[0]) < 0.01
        assert v[0] > 0

    def test_northerly(self):
        """0/360° = from north → u ≈ 0, v < 0."""
        u, v = wind_to_uv(np.array([10.0]), np.array([0.0]))
        assert abs(u[0]) < 0.01
        assert v[0] < 0

    def test_easterly(self):
        """90° = from east → u < 0, v ≈ 0."""
        u, v = wind_to_uv(np.array([10.0]), np.array([90.0]))
        assert u[0] < 0
        assert abs(v[0]) < 0.01

    def test_knots_to_kmh(self):
        """10 knots = 18.52 km/h."""
        u, v = wind_to_uv(np.array([10.0]), np.array([270.0]))
        assert abs(u[0]) == pytest.approx(18.52, rel=0.01)


class TestPrepareField:
    def test_no_nan_passthrough(self):
        field = np.ones((5, 5))
        result = prepare_field(field)
        assert result is not None
        np.testing.assert_array_equal(result, field)

    def test_sparse_nan_filled(self):
        field = np.ones((10, 10))
        field[3, 4] = np.nan  # 1% missing
        result = prepare_field(field)
        assert result is not None
        assert not np.any(np.isnan(result))
        # Nearest-neighbor should fill with 1.0
        assert result[3, 4] == pytest.approx(1.0)

    def test_too_many_nan_returns_none(self):
        field = np.ones((10, 10))
        field[:6, :] = np.nan  # 60% missing
        result = prepare_field(field, max_nan_fraction=0.05)
        assert result is None

    def test_exactly_at_threshold_returns_none(self):
        field = np.ones((100, 1))
        field[:5, :] = np.nan  # exactly 5%
        result = prepare_field(field, max_nan_fraction=0.05)
        assert result is None


class TestFillTerrain:
    def test_all_valid_passthrough(self):
        field = np.ones((5, 5)) * 10.0
        mask = np.ones((5, 5), dtype=bool)
        result = fill_terrain(field, mask)
        np.testing.assert_array_equal(result, field)

    def test_terrain_cells_filled_smoothly(self):
        """Terrain cells should get interpolated values, not NaN."""
        field = np.linspace(0, 10, 25).reshape(5, 5)
        mask = np.ones((5, 5), dtype=bool)
        mask[2, 2] = False  # one terrain cell in the middle
        result = fill_terrain(field, mask)
        assert not np.any(np.isnan(result))
        # The filled value should be close to the surrounding values
        assert result[2, 2] == pytest.approx(field[2, 2], abs=1.0)

    def test_valid_cells_unchanged(self):
        field = np.arange(25, dtype=float).reshape(5, 5)
        mask = np.ones((5, 5), dtype=bool)
        mask[0, 0] = False
        result = fill_terrain(field, mask)
        # All valid cells should be unchanged
        np.testing.assert_array_equal(result[mask], field[mask])


def _fill_terrain_reference(field: np.ndarray, terrain_mask: np.ndarray) -> np.ndarray:
    """The pre-#627 implementation: griddata (fresh Delaunay) on every call."""
    valid = terrain_mask
    if valid.all():
        return field
    coords_valid = np.argwhere(valid)
    coords_invalid = np.argwhere(~valid)
    filled = field.copy()
    filled[~valid] = griddata(
        coords_valid, field[valid], coords_invalid, method="linear",
    )
    still_nan = np.isnan(filled)
    if still_nan.any():
        filled[still_nan] = griddata(
            coords_valid, field[valid], np.argwhere(still_nan), method="nearest",
        )
    return filled


def _terrain_like_mask(shape=(41, 61), seed=0) -> np.ndarray:
    """Blobby interior holes plus masked edges/corners (outside the hull)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    mask = np.ones(shape, dtype=bool)
    for _ in range(6):
        cy, cx = rng.integers(5, shape[0] - 5), rng.integers(5, shape[1] - 5)
        r = rng.uniform(1.5, 4.5)
        mask &= (yy - cy) ** 2 + (xx - cx) ** 2 > r ** 2
    mask[0, :8] = False       # top edge run
    mask[-3:, -3:] = False    # corner block
    mask[10:20, 0] = False    # left edge run
    return mask


class TestFillTerrainMatchesGriddata:
    """#627: cached triangulation must be bit-identical to per-call griddata."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_fill_terrain_cache()
        yield
        clear_fill_terrain_cache()

    @staticmethod
    def _field(shape, seed, dtype=np.float64):
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
        f = 280 + 0.3 * yy - 0.1 * xx + rng.normal(0, 0.5, shape)
        return f.astype(dtype)

    def _assert_identical(self, field, mask):
        expected = _fill_terrain_reference(field, mask)
        # Twice: first call builds the cache entry, second reuses it.
        for _ in range(2):
            got = fill_terrain(field, mask)
            assert got.dtype == expected.dtype
            assert np.array_equal(got, expected, equal_nan=True)

    def test_interior_holes(self):
        mask = np.ones((30, 40), dtype=bool)
        mask[5:9, 10:15] = False
        mask[20:23, 25:33] = False
        mask[15, 5] = False
        self._assert_identical(self._field(mask.shape, 1), mask)

    def test_outside_convex_hull_uses_nearest(self):
        mask = _terrain_like_mask()
        field = self._field(mask.shape, 2)
        # Sanity: the linear pass alone leaves NaN, so the fallback is exercised.
        lin = griddata(np.argwhere(mask), field[mask], np.argwhere(~mask), method="linear")
        assert np.isnan(lin).any()
        self._assert_identical(field, mask)

    def test_all_valid_early_return(self):
        mask = np.ones((10, 12), dtype=bool)
        field = self._field(mask.shape, 3)
        assert fill_terrain(field, mask) is field
        self._assert_identical(field, mask)

    def test_nan_in_valid_values(self):
        mask = _terrain_like_mask(seed=4)
        field = self._field(mask.shape, 4)
        field[3, 30] = np.nan               # isolated valid NaN
        field[25:28, 40:44] = np.nan        # valid NaN block (poisons simplices)
        field[0, 8] = np.nan                # valid NaN next to masked edge
        assert mask[3, 30] and mask[0, 8]
        self._assert_identical(field, mask)

    def test_float32_field(self):
        mask = _terrain_like_mask(seed=5)
        self._assert_identical(self._field(mask.shape, 5, np.float32), mask)

    def test_many_fields_same_mask(self):
        mask = _terrain_like_mask(seed=6)
        for seed in range(5):
            self._assert_identical(self._field(mask.shape, 100 + seed), mask)


class TestFillTerrainCache:
    @pytest.fixture(autouse=True)
    def _clear(self):
        clear_fill_terrain_cache()
        yield
        clear_fill_terrain_cache()

    def test_recreated_equal_mask_hits_cache(self, monkeypatch):
        calls = []
        real = grid_mod._build_fill_geometry
        monkeypatch.setattr(
            grid_mod, "_build_fill_geometry", lambda v: calls.append(1) or real(v),
        )
        field = np.arange(400, dtype=float).reshape(20, 20)
        fill_terrain(field, _terrain_like_mask((20, 20), seed=7))
        fill_terrain(field + 1, _terrain_like_mask((20, 20), seed=7).copy())
        assert len(calls) == 1

    def test_different_masks_same_shape_do_not_share(self):
        field = np.random.default_rng(8).normal(size=(25, 25))
        a = _terrain_like_mask((25, 25), seed=8)
        b = a.copy()
        b[12, 12] = not b[12, 12]
        b[1, 1] = not b[1, 1]
        ra = fill_terrain(field, a)
        rb = fill_terrain(field, b)
        assert len(grid_mod._fill_cache) == 2
        assert np.array_equal(ra, _fill_terrain_reference(field, a), equal_nan=True)
        assert np.array_equal(rb, _fill_terrain_reference(field, b), equal_nan=True)

    def test_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(grid_mod, "_FILL_CACHE_MAX", 3)
        field = np.zeros((10, 10))
        for k in range(6):
            mask = np.ones((10, 10), dtype=bool)
            mask[4, k + 2] = False
            fill_terrain(field, mask)
        assert len(grid_mod._fill_cache) == 3

    def test_concurrent_calls_are_correct(self):
        from concurrent.futures import ThreadPoolExecutor

        masks = [_terrain_like_mask(seed=s) for s in (10, 11, 12)]
        fields = [np.random.default_rng(s).normal(280, 3, masks[0].shape) for s in range(12)]
        jobs = [(fields[i], masks[i % 3]) for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda fm: fill_terrain(*fm), jobs))
        for (f, m), r in zip(jobs, results):
            assert np.array_equal(r, _fill_terrain_reference(f, m), equal_nan=True)


class TestTerrainMaskForLevel:
    """Level-aware terrain masking (#216 Fix 3): mask a cell when terrain reaches
    the level's standard-atmosphere height (925≈762m, 850≈1457m, 700≈3012m)."""

    # lake (370m), pre-Alps (1102m), high Alps (2500m), ocean (NaN)
    _ELEV = np.array([[370.0, 1102.0, 2500.0, np.nan]])

    def test_925_masks_prealps_and_alps(self):
        valid = terrain_mask_for_level(self._ELEV, 925)
        np.testing.assert_array_equal(valid, [[True, False, False, True]])

    def test_850_masks_only_high_alps(self):
        valid = terrain_mask_for_level(self._ELEV, 850)
        np.testing.assert_array_equal(valid, [[True, True, False, True]])

    def test_700_keeps_all_terrain(self):
        """700 hPa (~3012m) is above 2500m terrain → not masked (the over-masking
        the flat 1500m threshold used to cause)."""
        valid = terrain_mask_for_level(self._ELEV, 700)
        np.testing.assert_array_equal(valid, [[True, True, True, True]])

    def test_nan_elevation_is_valid(self):
        """Ocean / no-SRTM cells (NaN) are always valid, never masked."""
        valid = terrain_mask_for_level(np.array([[np.nan]]), 925)
        assert valid.tolist() == [[True]]

    def test_buffer_lowers_threshold(self):
        """A positive buffer masks terrain a margin below the surface too."""
        elev = np.array([[700.0]])  # below 925's ~762m → valid with no buffer
        assert terrain_mask_for_level(elev, 925).tolist() == [[True]]
        # 200m buffer → threshold ~562m → 700m now masked.
        assert terrain_mask_for_level(elev, 925, buffer_m=200.0).tolist() == [[False]]

    def test_shape_preserved(self):
        elev = np.zeros((5, 7))
        assert terrain_mask_for_level(elev, 850).shape == (5, 7)


class TestComputeThetaE:
    def test_basic_computation(self):
        """θe should be > T in Kelvin for moist air at 850hPa."""
        T = np.array([[15.0]])  # 15°C
        Td = np.array([[10.0]])  # 10°C dewpoint
        theta_e = compute_theta_e(T, Td)
        # θe should be significantly warmer than T (288K) due to latent heat
        assert theta_e[0, 0] > 288 + 15  # well above dry potential temp

    def test_shape_preserved(self):
        T = np.ones((5, 5)) * 15.0
        Td = np.ones((5, 5)) * 10.0
        theta_e = compute_theta_e(T, Td)
        assert theta_e.shape == (5, 5)

    def test_higher_moisture_higher_theta_e(self):
        """Higher dewpoint → higher θe at same T."""
        T = np.array([[15.0, 15.0]])
        Td = np.array([[5.0, 14.0]])  # dry vs moist
        theta_e = compute_theta_e(T, Td)
        assert theta_e[0, 1] > theta_e[0, 0]
