"""Tests for spatial CLW/ICMR interpolation across route points."""

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.spatial_interpolation import interpolate_cloud_water_spatially
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_route_points(n: int, spacing_nm: float = 10.0) -> list[RoutePoint]:
    """Create n route points evenly spaced."""
    return [
        RoutePoint(lat=50.0, lon=i * 0.1, distance_from_origin_nm=i * spacing_nm)
        for i in range(n)
    ]


def _make_pl(pressure: int, clw: float | None, icmr: float | None = None) -> PressureLevelData:
    """Create a PressureLevelData with given CLW/ICMR."""
    return PressureLevelData(
        pressure_hpa=pressure,
        cloud_liquid_water_kg_kg=clw,
        ice_mixing_ratio_kg_kg=icmr,
    )


def _make_cross_section(
    point_levels: list[list[PressureLevelData]],
) -> RouteCrossSection:
    """Build a RouteCrossSection from per-point pressure level lists.

    Each entry in point_levels is the set of PressureLevelData for one route point,
    wrapped in a single hourly forecast.
    """
    now = datetime.now(tz=timezone.utc)
    wpt = Waypoint(icao="XXXX", name="Test", lat=50.0, lon=0.0)

    point_forecasts = []
    for levels in point_levels:
        wf = WaypointForecast(
            waypoint=wpt,
            model=ModelSource.GFS,
            fetched_at=now,
            hourly=[HourlyForecast(time=now, pressure_levels=levels)],
        )
        point_forecasts.append(wf)

    return RouteCrossSection(
        model=ModelSource.GFS,
        route_points=[],  # not used by interpolation
        fetched_at=now,
        point_forecasts=point_forecasts,
    )


# ── Tests ────────────────────────────────────────────────────────────


def test_no_gaps_noop():
    """All points have CLW data → nothing to fill."""
    rps = _make_route_points(3)
    cs = _make_cross_section([
        [_make_pl(500, 0.001)],
        [_make_pl(500, 0.002)],
        [_make_pl(500, 0.003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 0


def test_single_gap_interpolated():
    """One None between two data points → linearly interpolated."""
    rps = _make_route_points(3, spacing_nm=10.0)
    cs = _make_cross_section([
        [_make_pl(500, 0.001)],
        [_make_pl(500, None)],
        [_make_pl(500, 0.003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 1

    # Verify interpolated value (midpoint)
    pl = cs.point_forecasts[1].hourly[0].pressure_levels[0]
    assert pl.cloud_liquid_water_kg_kg == pytest.approx(0.002, abs=1e-9)
    assert pl.clw_interpolated is True


def test_edge_gap_first_point_not_interpolated():
    """First point has None → one-sided gap, not interpolated."""
    rps = _make_route_points(3)
    cs = _make_cross_section([
        [_make_pl(500, None)],
        [_make_pl(500, 0.002)],
        [_make_pl(500, 0.003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 0
    assert cs.point_forecasts[0].hourly[0].pressure_levels[0].cloud_liquid_water_kg_kg is None


def test_edge_gap_last_point_not_interpolated():
    """Last point has None → one-sided gap, not interpolated."""
    rps = _make_route_points(3)
    cs = _make_cross_section([
        [_make_pl(500, 0.001)],
        [_make_pl(500, 0.002)],
        [_make_pl(500, None)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 0
    assert cs.point_forecasts[2].hourly[0].pressure_levels[0].cloud_liquid_water_kg_kg is None


def test_gap_wider_than_max_not_interpolated():
    """Gap exceeds max_gap_nm → not interpolated."""
    rps = _make_route_points(3, spacing_nm=60.0)  # 0, 60, 120 nm → gap = 120 nm
    cs = _make_cross_section([
        [_make_pl(500, 0.001)],
        [_make_pl(500, None)],
        [_make_pl(500, 0.003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps, max_gap_nm=100.0)
    assert filled == 0


def test_gap_within_max_interpolated():
    """Gap just within max_gap_nm → interpolated."""
    rps = _make_route_points(3, spacing_nm=40.0)  # 0, 40, 80 nm → gap = 80 nm
    cs = _make_cross_section([
        [_make_pl(500, 0.001)],
        [_make_pl(500, None)],
        [_make_pl(500, 0.003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps, max_gap_nm=100.0)
    assert filled == 1


def test_consecutive_nones_interpolated():
    """Multiple consecutive None points → all filled with correct fractions."""
    rps = _make_route_points(5, spacing_nm=10.0)  # 0, 10, 20, 30, 40 nm
    cs = _make_cross_section([
        [_make_pl(500, 0.000)],
        [_make_pl(500, None)],
        [_make_pl(500, None)],
        [_make_pl(500, None)],
        [_make_pl(500, 0.004)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 3

    # Check fractions: distance 0→40, so at 10nm: 0.25, at 20nm: 0.5, at 30nm: 0.75
    pl1 = cs.point_forecasts[1].hourly[0].pressure_levels[0]
    pl2 = cs.point_forecasts[2].hourly[0].pressure_levels[0]
    pl3 = cs.point_forecasts[3].hourly[0].pressure_levels[0]

    assert pl1.cloud_liquid_water_kg_kg == pytest.approx(0.001, abs=1e-9)
    assert pl2.cloud_liquid_water_kg_kg == pytest.approx(0.002, abs=1e-9)
    assert pl3.cloud_liquid_water_kg_kg == pytest.approx(0.003, abs=1e-9)

    assert pl1.clw_interpolated is True
    assert pl2.clw_interpolated is True
    assert pl3.clw_interpolated is True


def test_icmr_interpolated_alongside_clw():
    """ICMR is also interpolated when both neighbors have it."""
    rps = _make_route_points(3, spacing_nm=10.0)
    cs = _make_cross_section([
        [_make_pl(500, 0.001, icmr=0.0001)],
        [_make_pl(500, None, icmr=None)],
        [_make_pl(500, 0.003, icmr=0.0003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 1

    pl = cs.point_forecasts[1].hourly[0].pressure_levels[0]
    assert pl.cloud_liquid_water_kg_kg == pytest.approx(0.002, abs=1e-9)
    assert pl.ice_mixing_ratio_kg_kg == pytest.approx(0.0002, abs=1e-9)
    assert pl.clw_interpolated is True


def test_clw_zero_preserved_not_a_gap():
    """CLW=0.0 is real data (measured zero), not a gap."""
    rps = _make_route_points(3, spacing_nm=10.0)
    cs = _make_cross_section([
        [_make_pl(500, 0.001)],
        [_make_pl(500, 0.0)],   # measured zero
        [_make_pl(500, 0.003)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 0

    # CLW=0.0 remains untouched
    pl = cs.point_forecasts[1].hourly[0].pressure_levels[0]
    assert pl.cloud_liquid_water_kg_kg == 0.0
    assert pl.clw_interpolated is False


def test_per_pressure_level_independence():
    """Interpolation operates independently per pressure level."""
    rps = _make_route_points(3, spacing_nm=10.0)
    cs = _make_cross_section([
        [_make_pl(500, 0.001), _make_pl(700, None)],
        [_make_pl(500, None),  _make_pl(700, 0.010)],
        [_make_pl(500, 0.003), _make_pl(700, None)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)

    # 500 hPa: point 1 filled (has neighbors at 0 and 2)
    # 700 hPa: point 0 and 2 are edge gaps (only point 1 has data), not filled
    assert filled == 1

    pl500 = cs.point_forecasts[1].hourly[0].pressure_levels[0]
    assert pl500.cloud_liquid_water_kg_kg == pytest.approx(0.002, abs=1e-9)
    assert pl500.clw_interpolated is True


def test_empty_inputs():
    """Empty cross_sections or route_points → returns 0."""
    rps = _make_route_points(3)
    assert interpolate_cloud_water_spatially([], rps) == 0
    assert interpolate_cloud_water_spatially([], []) == 0


def test_unequal_point_count_skipped():
    """Cross-section with mismatched point count is skipped."""
    rps = _make_route_points(3)
    cs = _make_cross_section([
        [_make_pl(500, None)],
        [_make_pl(500, 0.002)],
    ])
    filled = interpolate_cloud_water_spatially([cs], rps)
    assert filled == 0
