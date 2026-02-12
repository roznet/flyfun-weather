"""Tests for route interpolation."""

from __future__ import annotations

import pytest

from weatherbrief.fetch.route_points import interpolate_route
from weatherbrief.models import RouteConfig, Waypoint


@pytest.fixture
def two_waypoint_route():
    """Simple two-waypoint route (~225 nm)."""
    return RouteConfig(
        name="EGTK to LFPB",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32),
            Waypoint(icao="LFPB", name="Paris Le Bourget", lat=48.9694, lon=2.4414),
        ],
    )


@pytest.fixture
def three_waypoint_route(sample_route):
    """Three-waypoint route from conftest (~482 nm)."""
    return sample_route


class TestInterpolateRoute:
    def test_includes_all_waypoints(self, three_waypoint_route):
        points = interpolate_route(three_waypoint_route, spacing_nm=20.0)
        icaos = [p.waypoint_icao for p in points if p.waypoint_icao]
        assert icaos == ["EGTK", "LFPB", "LSGS"]

    def test_waypoint_names_preserved(self, three_waypoint_route):
        points = interpolate_route(three_waypoint_route, spacing_nm=20.0)
        wp_points = {p.waypoint_icao: p for p in points if p.waypoint_icao}
        assert wp_points["EGTK"].waypoint_name == "Oxford Kidlington"
        assert wp_points["LFPB"].waypoint_name == "Paris Le Bourget"
        assert wp_points["LSGS"].waypoint_name == "Sion"

    def test_origin_at_zero_distance(self, two_waypoint_route):
        points = interpolate_route(two_waypoint_route)
        assert points[0].waypoint_icao == "EGTK"
        assert points[0].distance_from_origin_nm == 0.0

    def test_distances_monotonically_increasing(self, three_waypoint_route):
        points = interpolate_route(three_waypoint_route, spacing_nm=20.0)
        for i in range(1, len(points)):
            assert points[i].distance_from_origin_nm > points[i - 1].distance_from_origin_nm

    def test_spacing_approximately_correct(self, two_waypoint_route):
        points = interpolate_route(two_waypoint_route, spacing_nm=20.0)
        # Check gaps between consecutive interpolated (non-waypoint) points
        for i in range(1, len(points) - 1):
            if points[i].waypoint_icao is None and points[i - 1].waypoint_icao is None:
                gap = points[i].distance_from_origin_nm - points[i - 1].distance_from_origin_nm
                assert abs(gap - 20.0) < 1.0

    def test_interpolated_points_have_no_icao(self, two_waypoint_route):
        points = interpolate_route(two_waypoint_route, spacing_nm=20.0)
        for p in points:
            if p is not points[0] and p is not points[-1]:
                assert p.waypoint_icao is None
                assert p.waypoint_name is None

    def test_total_distance_reasonable(self, three_waypoint_route):
        """EGTK-LFPB-LSGS is roughly 480 nm."""
        points = interpolate_route(three_waypoint_route, spacing_nm=20.0)
        total = points[-1].distance_from_origin_nm
        assert 450 < total < 520

    def test_large_spacing_still_includes_waypoints(self, two_waypoint_route):
        """With spacing larger than the leg, only waypoints are returned."""
        points = interpolate_route(two_waypoint_route, spacing_nm=500.0)
        assert len(points) == 2
        assert points[0].waypoint_icao == "EGTK"
        assert points[-1].waypoint_icao == "LFPB"

    def test_point_count_scales_with_spacing(self, two_waypoint_route):
        coarse = interpolate_route(two_waypoint_route, spacing_nm=50.0)
        fine = interpolate_route(two_waypoint_route, spacing_nm=10.0)
        assert len(fine) > len(coarse)
