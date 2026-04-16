"""Tests for weatherbrief.frontal.detect — synthetic field detection tests.

These are the most critical tests: they use known synthetic temperature
fields to verify that detection, classification, and terrain masking work.
"""

import numpy as np
import pytest

from weatherbrief.frontal.detect import (
    compute_frontal_zones,
    compute_frontal_zones_dual,
    classify_front_type,
)


def _make_grid(n_lat=21, n_lon=21, lat_range=(45, 55), lon_range=(0, 10)):
    """Create a small test grid."""
    lat = np.linspace(lat_range[0], lat_range[1], n_lat)
    lon = np.linspace(lon_range[0], lon_range[1], n_lon)
    return lat, lon


class TestComputeFrontalZones:
    def test_uniform_field_no_fronts(self):
        """Uniform temperature → no frontal detection."""
        lat, lon = _make_grid()
        T850 = np.ones((len(lat), len(lon))) * 10.0
        result = compute_frontal_zones(T850, lat, lon)
        assert not result["frontal_mask"].any()
        assert result["gradient"].max() < 0.1

    def test_tanh_cold_front_detected(self):
        """Tanh step in T → frontal band at the step location."""
        lat, lon = _make_grid(n_lat=41, n_lon=41)
        lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")

        # 8K drop over ~200km centered at lat=50 (tanh transition)
        T850 = 10.0 - 4.0 * np.tanh((lat_grid - 50.0) / 0.8)

        result = compute_frontal_zones(
            T850, lat, lon, smooth_sigma=0.5, gradient_threshold=1.5,
        )

        # Should detect a frontal band near lat=50
        assert result["frontal_mask"].any()
        # Peak gradient should be in the expected range
        assert result["gradient"].max() > 1.0
        # Frontal points should cluster near the step
        frontal_lats = lat_grid[result["frontal_mask"]]
        assert np.abs(frontal_lats.mean() - 50.0) < 2.0

    def test_weak_gradient_not_detected(self):
        """Gradient below threshold → no detection."""
        lat, lon = _make_grid(n_lat=41, n_lon=41)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        # Very gentle gradient: 0.5K over the entire domain
        T850 = 10.0 + 0.05 * (lat_grid - 45.0)

        result = compute_frontal_zones(
            T850, lat, lon, gradient_threshold=2.0,
        )
        assert not result["frontal_mask"].any()

    def test_terrain_mask_excludes_cells(self):
        """Terrain-masked cells should not appear in frontal_mask."""
        lat, lon = _make_grid(n_lat=21, n_lon=21)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        # Strong uniform gradient across the entire domain
        T850 = 10.0 - 5.0 * np.tanh((lat_grid - 50.0) / 0.5)

        terrain_mask = np.ones((len(lat), len(lon)), dtype=bool)
        terrain_mask[8:12, 8:12] = False  # mask center block

        result = compute_frontal_zones(
            T850, lat, lon, terrain_mask=terrain_mask,
        )

        # Masked cells should never be frontal
        assert not result["frontal_mask"][~terrain_mask].any()

    def test_terrain_adjacent_cells_not_corrupted(self):
        """Cells adjacent to terrain should have reasonable gradients."""
        lat, lon = _make_grid(n_lat=21, n_lon=21)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        # Smooth gradient field
        T850 = 10.0 + 0.5 * (lat_grid - 45.0)

        # Without terrain mask
        result_no_mask = compute_frontal_zones(T850, lat, lon)

        # With terrain mask in center
        terrain_mask = np.ones((len(lat), len(lon)), dtype=bool)
        terrain_mask[9:11, 9:11] = False
        result_masked = compute_frontal_zones(
            T850, lat, lon, terrain_mask=terrain_mask,
        )

        # Gradient at terrain-adjacent valid cells should be similar
        # to the unmasked version (fill-then-mask prevents corruption)
        adj_row = 8  # row just above terrain
        adj_grad_masked = result_masked["gradient"][adj_row, 10]
        adj_grad_unmasked = result_no_mask["gradient"][adj_row, 10]
        assert adj_grad_masked == pytest.approx(adj_grad_unmasked, rel=0.3)

    def test_returns_expected_keys(self):
        lat, lon = _make_grid()
        T850 = np.ones((len(lat), len(lon))) * 10.0
        result = compute_frontal_zones(T850, lat, lon)
        expected = {
            "gradient", "frontal_mask", "tfp", "T_smooth",
            "front_orientation", "dT_dx", "dT_dy",
        }
        assert set(result.keys()) == expected


class TestComputeFrontalZonesDual:
    def test_theta_e_only_warm_front(self):
        """Weak T gradient + strong θe gradient → detected by θe only."""
        lat, lon = _make_grid(n_lat=41, n_lon=41)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        # T850: very weak gradient (below T threshold)
        T850 = 10.0 + 0.1 * (lat_grid - 45.0)

        # θe: strong gradient (above θe threshold)
        theta_e = 320.0 - 10.0 * np.tanh((lat_grid - 50.0) / 0.8)

        result = compute_frontal_zones_dual(
            T850, theta_e, lat, lon,
            t_gradient_threshold=0.8,
            te_gradient_threshold=4.0,
        )

        # Should be detected
        assert result["frontal_mask"].any()

        # Should be detected by θe, not T
        frontal_points = result["frontal_mask"]
        detected = result["detected_by"][frontal_points]
        # At least some points should be θe-only (value == 2)
        assert (detected == 2).any()

    def test_cold_front_detected_by_both(self):
        """Strong T + strong θe → detected by both methods."""
        lat, lon = _make_grid(n_lat=41, n_lon=41)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        T850 = 10.0 - 4.0 * np.tanh((lat_grid - 50.0) / 0.8)
        theta_e = 320.0 - 15.0 * np.tanh((lat_grid - 50.0) / 0.8)

        result = compute_frontal_zones_dual(T850, theta_e, lat, lon)

        frontal_points = result["frontal_mask"]
        detected = result["detected_by"][frontal_points]
        # Should have points detected by both (value == 3)
        assert (detected == 3).any()


class TestClassifyFrontType:
    def _make_frontal_setup(self, n=21):
        """Create a frontal zone with known gradient for classification tests."""
        lat, lon = _make_grid(n_lat=n, n_lon=n)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        # North-south gradient: cold to the north
        T850 = 10.0 - 4.0 * np.tanh((lat_grid - 50.0) / 0.8)

        result = compute_frontal_zones(T850, lat, lon, gradient_threshold=0.5)
        return result, lat, lon

    def test_cold_front_classification(self):
        """Wind from cold side → cold advection → cold front."""
        result, lat, lon = self._make_frontal_setup(n=41)

        # Northerly wind (from cold side) — v < 0 in km/h
        u850 = np.zeros_like(result["dT_dx"])
        v850 = np.full_like(result["dT_dy"], -40.0)  # 40 km/h from north

        front_type = classify_front_type(
            result["dT_dx"], result["dT_dy"],
            u850, v850, result["frontal_mask"],
        )

        # Frontal points should be classified as cold (1)
        frontal_types = front_type[result["frontal_mask"]]
        assert (frontal_types == 1).sum() > 0
        # Cold should dominate
        assert (frontal_types == 1).sum() > (frontal_types == 2).sum()

    def test_warm_front_classification(self):
        """Wind from warm side → warm advection → warm front."""
        result, lat, lon = self._make_frontal_setup(n=41)

        # Southerly wind (from warm side) — v > 0 in km/h
        u850 = np.zeros_like(result["dT_dx"])
        v850 = np.full_like(result["dT_dy"], 40.0)  # 40 km/h from south

        front_type = classify_front_type(
            result["dT_dx"], result["dT_dy"],
            u850, v850, result["frontal_mask"],
        )

        frontal_types = front_type[result["frontal_mask"]]
        assert (frontal_types == 2).sum() > 0
        assert (frontal_types == 2).sum() > (frontal_types == 1).sum()

    def test_parallel_wind_indeterminate(self):
        """Wind parallel to front → near-zero advection → indeterminate."""
        result, lat, lon = self._make_frontal_setup(n=41)

        # Westerly wind (parallel to E-W gradient line)
        u850 = np.full_like(result["dT_dx"], 40.0)
        v850 = np.zeros_like(result["dT_dy"])

        front_type = classify_front_type(
            result["dT_dx"], result["dT_dy"],
            u850, v850, result["frontal_mask"],
        )

        frontal_types = front_type[result["frontal_mask"]]
        # Most should be indeterminate (3) since wind is along-front
        assert (frontal_types == 3).sum() > 0

    def test_theta_e_only_biased_warm(self):
        """Points detected only by θe should be biased toward warm."""
        result, lat, lon = self._make_frontal_setup(n=41)

        # Weak wind → would be indeterminate
        u850 = np.full_like(result["dT_dx"], 5.0)
        v850 = np.full_like(result["dT_dy"], 5.0)

        # Create detected_by with some θe-only points
        detected_by = np.zeros_like(result["frontal_mask"], dtype=np.uint8)
        detected_by[result["frontal_mask"]] = 2  # all θe-only

        front_type = classify_front_type(
            result["dT_dx"], result["dT_dy"],
            u850, v850, result["frontal_mask"],
            detected_by=detected_by,
        )

        # θe-only + weak advection should be warm (2), not indeterminate (3)
        frontal_types = front_type[result["frontal_mask"]]
        assert (frontal_types == 2).sum() > (frontal_types == 3).sum()

    def test_non_frontal_points_are_zero(self):
        """Points outside frontal_mask should always be 0."""
        result, lat, lon = self._make_frontal_setup(n=41)
        u850 = np.full_like(result["dT_dx"], -40.0)
        v850 = np.zeros_like(result["dT_dy"])

        front_type = classify_front_type(
            result["dT_dx"], result["dT_dy"],
            u850, v850, result["frontal_mask"],
        )

        assert (front_type[~result["frontal_mask"]] == 0).all()
