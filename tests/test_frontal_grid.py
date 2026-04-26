"""Tests for weatherbrief.frontal.grid — grid coords, wind, NaN handling, terrain."""

import numpy as np
import pytest

from weatherbrief.frontal.grid import (
    build_grid_coords,
    build_grid_points,
    wind_to_uv,
    prepare_field,
    fill_terrain,
    compute_theta_e,
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
