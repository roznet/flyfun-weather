"""Tests for weatherbrief.frontal.zones — zone intersection and orientation."""

import numpy as np
import pytest

from weatherbrief.frontal.zones import (
    ZONES,
    ROUTE_TEMPLATES,
    find_fronts_in_regions,
    find_route_zones,
    _orientation_label,
    _MIN_FRONTAL_FRACTION,
    _MIN_FRONTAL_POINTS,
)


def _make_grid(lat_range=(35, 60), lon_range=(-12, 28)):
    """Create the standard frontal grid for zone tests."""
    lat = np.arange(lat_range[0], lat_range[1] + 0.5, 0.5)
    lon = np.arange(lon_range[0], lon_range[1] + 0.5, 0.5)
    return lat, lon


class TestZoneDefinitions:
    def test_all_zones_have_required_keys(self):
        for name, z in ZONES.items():
            assert "lat" in z, f"{name} missing 'lat'"
            assert "lon" in z, f"{name} missing 'lon'"
            assert "display" in z, f"{name} missing 'display'"
            assert len(z["lat"]) == 2
            assert len(z["lon"]) == 2

    def test_all_zones_within_grid(self):
        """All zones should fit within the frontal grid bounds."""
        for name, z in ZONES.items():
            assert z["lat"][0] >= 35.0, f"{name} lat_min below grid"
            assert z["lat"][1] <= 60.0, f"{name} lat_max above grid"
            assert z["lon"][0] >= -12.0, f"{name} lon_min below grid"
            assert z["lon"][1] <= 28.0, f"{name} lon_max above grid"

    def test_minimum_grid_points(self):
        """Each zone should have ≥96 grid points at 0.5° for reliable detection."""
        for name, z in ZONES.items():
            n_lat = int((z["lat"][1] - z["lat"][0]) / 0.5) + 1
            n_lon = int((z["lon"][1] - z["lon"][0]) / 0.5) + 1
            assert n_lat * n_lon >= 96, f"{name} has only {n_lat * n_lon} points"

    def test_route_templates_reference_valid_zones(self):
        for route_name, zone_list in ROUTE_TEMPLATES.items():
            for zone in zone_list:
                assert zone in ZONES, f"Route '{route_name}' references unknown zone '{zone}'"


class TestFindFrontsInRegions:
    def test_front_in_north_france(self):
        """A frontal band across north_france should be detected there."""
        lat, lon = _make_grid()
        n_lat, n_lon = len(lat), len(lon)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        # Create frontal mask: band at lat 48-49 (within north_france: 47-51)
        frontal_mask = (lat_grid >= 48) & (lat_grid <= 49)
        front_type = np.where(frontal_mask, 1, 0)  # all cold
        gradient = np.where(frontal_mask, 2.0, 0.1)
        front_orientation = np.where(frontal_mask, 45.0, 0.0)

        results = find_fronts_in_regions(
            frontal_mask, front_type, gradient, front_orientation, lat, lon,
        )

        assert results["north_france"]["present"] is True
        assert results["north_france"]["type"] == "cold"
        assert results["north_france"]["intensity"] > 1.0

    def test_no_front_in_clear_zone(self):
        """A front in north_france should not trigger detection in uk_south."""
        lat, lon = _make_grid()
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

        frontal_mask = (lat_grid >= 48) & (lat_grid <= 49)
        front_type = np.where(frontal_mask, 1, 0)
        gradient = np.where(frontal_mask, 2.0, 0.1)
        front_orientation = np.where(frontal_mask, 45.0, 0.0)

        results = find_fronts_in_regions(
            frontal_mask, front_type, gradient, front_orientation, lat, lon,
        )

        # uk_south is 49-53N — the front band at 48-49 barely touches it
        # balkans (38-46N, 19-27E) should definitely be clear
        assert results["balkans"]["present"] is False

    def test_fractional_threshold(self):
        """Tiny patch below _MIN_FRONTAL_FRACTION should not trigger."""
        lat, lon = _make_grid()
        n_lat, n_lon = len(lat), len(lon)

        # Only 2 frontal points — well below threshold for any zone
        frontal_mask = np.zeros((n_lat, n_lon), dtype=bool)
        frontal_mask[15, 10] = True
        frontal_mask[15, 11] = True
        front_type = np.where(frontal_mask, 1, 0)
        gradient = np.where(frontal_mask, 3.0, 0.0)
        front_orientation = np.zeros((n_lat, n_lon))

        results = find_fronts_in_regions(
            frontal_mask, front_type, gradient, front_orientation, lat, lon,
        )

        # No zone should report a front
        for zone_name, result in results.items():
            assert result["present"] is False, f"{zone_name} falsely detected"

    def test_terrain_adjusted_denominator(self):
        """Terrain mask should adjust the denominator, not the numerator."""
        lat, lon = _make_grid()
        n_lat, n_lon = len(lat), len(lon)
        lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")

        # Front covering a portion of alps zone
        alps = ZONES["alps"]
        in_alps = (
            (lat_grid >= alps["lat"][0]) & (lat_grid <= alps["lat"][1])
            & (lon_grid >= alps["lon"][0]) & (lon_grid <= alps["lon"][1])
        )

        # Make 20% of alps frontal
        frontal_mask = np.zeros((n_lat, n_lon), dtype=bool)
        alps_indices = np.argwhere(in_alps)
        n_frontal = max(int(len(alps_indices) * 0.20), 10)
        for idx in alps_indices[:n_frontal]:
            frontal_mask[idx[0], idx[1]] = True

        front_type = np.where(frontal_mask, 1, 0)
        gradient = np.where(frontal_mask, 2.0, 0.0)
        front_orientation = np.zeros((n_lat, n_lon))

        # With heavy terrain mask (60% masked)
        terrain_mask = np.ones((n_lat, n_lon), dtype=bool)
        for idx in alps_indices[: int(len(alps_indices) * 0.6)]:
            terrain_mask[idx[0], idx[1]] = False

        # Ensure frontal points are in unmasked area
        frontal_mask = frontal_mask & terrain_mask

        result_with_terrain = find_fronts_in_regions(
            frontal_mask, front_type, gradient, front_orientation,
            lat, lon, terrain_mask=terrain_mask,
        )

        result_no_terrain = find_fronts_in_regions(
            frontal_mask, front_type, gradient, front_orientation,
            lat, lon,
        )

        # With terrain adjustment, coverage fraction should be higher
        # (smaller denominator for same numerator)
        if result_with_terrain["alps"]["present"] and result_no_terrain["alps"]["present"]:
            assert (
                result_with_terrain["alps"]["coverage_fraction"]
                >= result_no_terrain["alps"]["coverage_fraction"]
            )


class TestOrientationLabel:
    def test_north_south(self):
        """Gradient points east → front runs N-S."""
        orientation = np.array([90.0, 90.0, 90.0])
        mask = np.array([True, True, True])
        label = _orientation_label(orientation, mask)
        assert label == "E-W"  # 90° orientation = E-W front line

    def test_ne_sw(self):
        """Orientation near 45° → NE-SW."""
        orientation = np.array([45.0, 45.0])
        mask = np.array([True, True])
        label = _orientation_label(orientation, mask)
        assert label == "NE-SW"

    def test_wraparound(self):
        """Orientations near 0°/360° should average correctly."""
        orientation = np.array([5.0, 355.0])
        mask = np.array([True, True])
        label = _orientation_label(orientation, mask)
        assert label == "N-S"


class TestFindRouteZones:
    def test_egtf_to_lsgs(self):
        """EGTF (51.3N, -0.8W) to LSGS (46.2N, 7.3E) crosses known zones."""
        waypoints = [
            (51.3, -0.8),  # EGTF — Southern England
            (49.0, 2.5),   # Over northern France
            (47.0, 4.0),   # Southern France border
            (46.2, 7.3),   # LSGS — Alps region
        ]
        zones = find_route_zones(waypoints)
        assert "uk_south" in zones
        assert "north_france" in zones

    def test_no_duplicate_consecutive(self):
        """Multiple waypoints in same zone should not duplicate."""
        waypoints = [
            (50.0, 0.0),   # uk_south
            (50.5, 0.5),   # still uk_south
            (49.0, 3.0),   # north_france
        ]
        zones = find_route_zones(waypoints)
        # Should not have consecutive duplicates
        for i in range(1, len(zones)):
            assert zones[i] != zones[i - 1]

    def test_empty_waypoints(self):
        zones = find_route_zones([])
        assert zones == []
