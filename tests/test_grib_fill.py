"""Tests for unified GRIB forward-fill (time axis) and diagnostics spatial interpolation."""

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.spatial_interpolation import (
    interpolate_diagnostics_spatially,
    interpolate_all_spatially,
)
from weatherbrief.fetch.grib.fill import propagate_all
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)


# ── Helpers ──────────────────────────────────────────────────────────

T0 = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)


def _make_route_points(n: int, spacing_nm: float = 10.0) -> list[RoutePoint]:
    return [
        RoutePoint(lat=50.0, lon=i * 0.1, distance_from_origin_nm=i * spacing_nm)
        for i in range(n)
    ]


def _make_diag(
    low_pct: float = 50, mid_pct: float = 30, high_pct: float = 10,
    low_base: float = 1000, low_top: float = 5000,
    mid_base: float = 6500, mid_top: float = 15000,
    ceiling: float = 1000,
) -> NWPCloudDiagnostics:
    return NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=low_pct, base_ft=low_base, top_ft=low_top),
        mid=NWPCloudLayerDiag(cover_pct=mid_pct, base_ft=mid_base, top_ft=mid_top),
        high=NWPCloudLayerDiag(cover_pct=high_pct),
        ceiling_ft=ceiling,
    )


def _make_hourly(
    time: datetime,
    diag: NWPCloudDiagnostics | None = None,
    clw: float | None = None,
    icmr: float | None = None,
) -> HourlyForecast:
    levels = [PressureLevelData(
        pressure_hpa=500,
        cloud_liquid_water_kg_kg=clw,
        ice_mixing_ratio_kg_kg=icmr,
    )] if clw is not None or icmr is not None else [
        PressureLevelData(pressure_hpa=500),
    ]
    return HourlyForecast(
        time=time,
        nwp_cloud_diagnostics=diag,
        pressure_levels=levels,
    )


def _make_cs_with_hourly(
    point_hourly: list[list[HourlyForecast]],
) -> RouteCrossSection:
    now = datetime.now(tz=timezone.utc)
    wpt = Waypoint(icao="XXXX", name="Test", lat=50.0, lon=0.0)
    point_forecasts = [
        WaypointForecast(
            waypoint=wpt, model=ModelSource.GFS, fetched_at=now,
            hourly=hourly_list,
        )
        for hourly_list in point_hourly
    ]
    return RouteCrossSection(
        model=ModelSource.GFS, route_points=[], fetched_at=now,
        point_forecasts=point_forecasts,
    )


# ── Forward-fill: cloud diagnostics ─────────────────────────────────

class TestForwardFillDiagnostics:
    def test_fills_gap_after_native_hour(self):
        """Diagnostics at T0 should forward-fill to T0+1h."""
        diag = _make_diag()
        hourly = [
            _make_hourly(T0, diag=diag),
            _make_hourly(T0 + timedelta(hours=1)),  # gap
            _make_hourly(T0 + timedelta(hours=2)),  # gap
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert cs.point_forecasts[0].hourly[1].nwp_cloud_diagnostics is diag
        assert cs.point_forecasts[0].hourly[2].nwp_cloud_diagnostics is diag

    def test_does_not_overwrite_existing(self):
        """Existing diagnostics should not be overwritten."""
        diag1 = _make_diag(low_pct=10)
        diag2 = _make_diag(low_pct=90)
        hourly = [
            _make_hourly(T0, diag=diag1),
            _make_hourly(T0 + timedelta(hours=1), diag=diag2),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert cs.point_forecasts[0].hourly[1].nwp_cloud_diagnostics is diag2

    def test_no_fill_before_first_anchor(self):
        """Hours before the first enriched hour stay None."""
        diag = _make_diag()
        hourly = [
            _make_hourly(T0),  # gap — no anchor yet
            _make_hourly(T0 + timedelta(hours=1), diag=diag),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert cs.point_forecasts[0].hourly[0].nwp_cloud_diagnostics is None


# ── Forward-fill: cloud water ────────────────────────────────────────

class TestForwardFillCloudWater:
    def test_fills_clw_gap(self):
        """CLW at T0 should forward-fill to T0+1h."""
        hourly = [
            _make_hourly(T0, clw=0.001, icmr=0.0002),
            _make_hourly(T0 + timedelta(hours=1)),  # gap
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        pl = cs.point_forecasts[0].hourly[1].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg == 0.001
        assert pl.ice_mixing_ratio_kg_kg == 0.0002

    def test_no_fill_before_first_anchor(self):
        """Pressure levels before the first enriched hour stay None."""
        hourly = [
            _make_hourly(T0),  # gap — no anchor
            _make_hourly(T0 + timedelta(hours=1), clw=0.001),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        pl = cs.point_forecasts[0].hourly[0].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg is None

    def test_updates_anchor_on_new_value(self):
        """A later native hour's CLW should replace the fill value."""
        hourly = [
            _make_hourly(T0, clw=0.001),
            _make_hourly(T0 + timedelta(hours=1), clw=0.005),
            _make_hourly(T0 + timedelta(hours=2)),  # gap
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        # Should fill with 0.005, not 0.001
        pl = cs.point_forecasts[0].hourly[2].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg == 0.005


# ── Spatial interpolation: diagnostics ───────────────────────────────

class TestSpatialDiagnostics:
    def test_interpolates_middle_point(self):
        """Point 1 has no diagnostics; points 0 and 2 do → linear interp."""
        rps = _make_route_points(3)
        diag0 = _make_diag(low_pct=40, ceiling=2000)
        diag2 = _make_diag(low_pct=80, ceiling=4000)

        cs = _make_cs_with_hourly([
            [_make_hourly(T0, diag=diag0)],
            [_make_hourly(T0)],  # gap
            [_make_hourly(T0, diag=diag2)],
        ])
        filled = interpolate_diagnostics_spatially([cs], rps)

        assert filled == 1
        result = cs.point_forecasts[1].hourly[0].nwp_cloud_diagnostics
        assert result is not None
        assert result.low.cover_pct == pytest.approx(60.0)  # midpoint
        assert result.ceiling_ft == pytest.approx(3000.0)

    def test_no_fill_edge_gap(self):
        """First point missing, only right neighbor → skip."""
        rps = _make_route_points(3)
        diag = _make_diag()
        cs = _make_cs_with_hourly([
            [_make_hourly(T0)],  # gap
            [_make_hourly(T0, diag=diag)],
            [_make_hourly(T0, diag=diag)],
        ])
        filled = interpolate_diagnostics_spatially([cs], rps)
        assert filled == 0

    def test_respects_max_gap(self):
        """Gap > max_gap_nm → skip."""
        rps = _make_route_points(3, spacing_nm=60.0)  # 120nm total gap
        diag = _make_diag()
        cs = _make_cs_with_hourly([
            [_make_hourly(T0, diag=diag)],
            [_make_hourly(T0)],  # gap
            [_make_hourly(T0, diag=diag)],
        ])
        filled = interpolate_diagnostics_spatially([cs], rps, max_gap_nm=100.0)
        assert filled == 0

    def test_interpolates_layer_base_top(self):
        """Verify layer base/top are linearly interpolated."""
        rps = _make_route_points(3)
        diag0 = _make_diag(low_base=1000, low_top=5000)
        diag2 = _make_diag(low_base=2000, low_top=7000)
        cs = _make_cs_with_hourly([
            [_make_hourly(T0, diag=diag0)],
            [_make_hourly(T0)],
            [_make_hourly(T0, diag=diag2)],
        ])
        interpolate_diagnostics_spatially([cs], rps)

        result = cs.point_forecasts[1].hourly[0].nwp_cloud_diagnostics
        assert result is not None
        assert result.low.base_ft == pytest.approx(1500.0)
        assert result.low.top_ft == pytest.approx(6000.0)


# ── Unified entry point ──────────────────────────────────────────────

class TestInterpolateAllSpatially:
    def test_fills_both_clw_and_diagnostics(self):
        """interpolate_all_spatially fills both field types."""
        rps = _make_route_points(3)
        diag = _make_diag()
        cs = _make_cs_with_hourly([
            [HourlyForecast(
                time=T0, nwp_cloud_diagnostics=diag,
                pressure_levels=[PressureLevelData(pressure_hpa=500, cloud_liquid_water_kg_kg=0.001)],
            )],
            [HourlyForecast(
                time=T0,
                pressure_levels=[PressureLevelData(pressure_hpa=500)],
            )],
            [HourlyForecast(
                time=T0, nwp_cloud_diagnostics=diag,
                pressure_levels=[PressureLevelData(pressure_hpa=500, cloud_liquid_water_kg_kg=0.003)],
            )],
        ])
        interpolate_all_spatially([cs], rps)

        # CLW filled
        pl = cs.point_forecasts[1].hourly[0].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg == pytest.approx(0.002)

        # Diagnostics filled
        diag_result = cs.point_forecasts[1].hourly[0].nwp_cloud_diagnostics
        assert diag_result is not None
