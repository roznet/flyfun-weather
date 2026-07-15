"""Tests for airport conditions computation."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from weatherbrief.analysis.airport_conditions import (
    _ceiling_from_sounding,
    reconcile_ceiling,
    classify_flight_category,
    compute_airport_conditions,
    compute_runway_winds,
    format_wind_string,
    GUST_DISPLAY_MIN_EXCESS_KT,
)
from weatherbrief.models import (
    CloudCoverage,
    EnhancedCloudLayer,
    HourlyForecast,
    NWPCloudDiagnostics,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)
from weatherbrief.models.airport_conditions import (
    FlightCategory,
    RunwayEnd,
)


# --- format_wind_string ---

class TestFormatWindString:
    """Standard `dir@speedGgust` notation — mirrors web `formatWind`
    (units.ts) and iOS `WindFormat`; the three must stay in sync."""

    def test_dir_speed_gust_padded(self):
        assert format_wind_string(273, 15, 22) == "273@15G22"
        assert format_wind_string(5, 8, None) == "005@8"

    def test_empty_when_dir_or_speed_missing(self):
        assert format_wind_string(None, 15, 20) == ""
        assert format_wind_string(270, None, 20) == ""

    def test_gust_shown_only_above_threshold(self):
        thr = GUST_DISPLAY_MIN_EXCESS_KT
        assert format_wind_string(9, 5, 6) == "009@5"            # +1 kt — suppressed
        assert format_wind_string(9, 5, 5 + thr) == "009@5G9"    # exactly threshold
        assert format_wind_string(9, 5, 10) == "009@5G10"        # well above
        assert format_wind_string(9, 5, 5 + thr - 1) == "009@5"  # just below

    def test_gust_below_speed_is_suppressed(self):
        assert format_wind_string(80, 14, 6) == "080@14"


# --- classify_flight_category ---

class TestFlightCategoryClassification:

    def test_vfr_good_conditions(self):
        assert classify_flight_category(5000, 10.0) == FlightCategory.VFR

    def test_vfr_no_data(self):
        assert classify_flight_category(None, None) == FlightCategory.VFR

    def test_mvfr_low_ceiling(self):
        assert classify_flight_category(2500, 10.0) == FlightCategory.MVFR

    def test_mvfr_low_visibility(self):
        assert classify_flight_category(5000, 4.0) == FlightCategory.MVFR

    def test_ifr_low_ceiling(self):
        assert classify_flight_category(800, 10.0) == FlightCategory.IFR

    def test_ifr_low_visibility(self):
        assert classify_flight_category(5000, 2.0) == FlightCategory.IFR

    def test_lifr_very_low_ceiling(self):
        assert classify_flight_category(300, 10.0) == FlightCategory.LIFR

    def test_lifr_very_low_visibility(self):
        assert classify_flight_category(5000, 0.5) == FlightCategory.LIFR

    def test_worst_of_ceiling_and_visibility(self):
        # Ceiling is MVFR but visibility is IFR — result should be IFR
        assert classify_flight_category(2000, 2.0) == FlightCategory.IFR

    def test_ceiling_only_no_visibility(self):
        assert classify_flight_category(800, None) == FlightCategory.IFR

    def test_visibility_only_no_ceiling(self):
        assert classify_flight_category(None, 4.0) == FlightCategory.MVFR

    def test_boundary_vfr_ceiling(self):
        assert classify_flight_category(3000, 10.0) == FlightCategory.VFR

    def test_boundary_mvfr_ceiling(self):
        assert classify_flight_category(2999, 10.0) == FlightCategory.MVFR

    def test_boundary_ifr_ceiling(self):
        assert classify_flight_category(999, 10.0) == FlightCategory.IFR

    def test_boundary_vfr_visibility(self):
        assert classify_flight_category(5000, 5.0) == FlightCategory.VFR

    def test_boundary_mvfr_visibility(self):
        assert classify_flight_category(5000, 4.9) == FlightCategory.MVFR


# --- compute_runway_winds ---

class TestRunwayWinds:

    def test_headwind_no_crosswind(self):
        ends = [RunwayEnd(id="36", heading_deg=360.0)]
        result = compute_runway_winds(ends, 20.0, 360.0)
        assert len(result) == 1
        assert result[0].crosswind_kt == pytest.approx(0.0, abs=0.5)
        assert result[0].headwind_kt == pytest.approx(20.0, abs=0.5)

    def test_pure_crosswind(self):
        ends = [RunwayEnd(id="36", heading_deg=360.0)]
        result = compute_runway_winds(ends, 20.0, 90.0)
        assert len(result) == 1
        assert result[0].crosswind_kt == pytest.approx(20.0, abs=0.5)
        assert result[0].headwind_kt == pytest.approx(0.0, abs=0.5)

    def test_tailwind(self):
        ends = [RunwayEnd(id="36", heading_deg=360.0)]
        result = compute_runway_winds(ends, 20.0, 180.0)
        assert len(result) == 1
        assert result[0].headwind_kt == pytest.approx(-20.0, abs=0.5)

    def test_45_degree_wind(self):
        ends = [RunwayEnd(id="09", heading_deg=90.0)]
        result = compute_runway_winds(ends, 20.0, 135.0)
        expected_xwind = abs(20.0 * math.sin(math.radians(45)))
        assert result[0].crosswind_kt == pytest.approx(expected_xwind, abs=0.5)

    def test_multiple_runways_best_selection(self):
        ends = [
            RunwayEnd(id="09", heading_deg=90.0),
            RunwayEnd(id="27", heading_deg=270.0),
        ]
        # Wind from south: both runways should have crosswind but same magnitude
        result = compute_runway_winds(ends, 20.0, 180.0)
        assert len(result) == 2
        # Both should have equal crosswind (20kt from perpendicular)
        assert result[0].crosswind_kt == pytest.approx(result[1].crosswind_kt, abs=0.5)


# --- ceiling_from_sounding ---

class TestCeilingFromSounding:

    def test_bkn_layer(self):
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.BKN),
            ],
        )
        assert _ceiling_from_sounding(sounding) == 3000

    def test_ovc_layer(self):
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=2000, top_ft=4000, coverage=CloudCoverage.OVC),
            ],
        )
        assert _ceiling_from_sounding(sounding) == 2000

    def test_sct_ignored(self):
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=1500, top_ft=3000, coverage=CloudCoverage.SCT),
            ],
        )
        assert _ceiling_from_sounding(sounding) is None

    def test_lowest_bkn_ovc(self):
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=5000, top_ft=8000, coverage=CloudCoverage.OVC),
                EnhancedCloudLayer(base_ft=2000, top_ft=4000, coverage=CloudCoverage.BKN),
                EnhancedCloudLayer(base_ft=1000, top_ft=2000, coverage=CloudCoverage.SCT),
            ],
        )
        assert _ceiling_from_sounding(sounding) == 2000

    def test_no_layers(self):
        sounding = SoundingAnalysis(cloud_layers=[])
        assert _ceiling_from_sounding(sounding) is None

    def test_uses_precomputed_ceiling_from_indices(self):
        """Pre-computed sounding_ceiling_ft (LCL-corrected) is preferred over cloud_layers."""
        sounding = SoundingAnalysis(
            indices=ThermodynamicIndices(sounding_ceiling_ft=1400.0),
            cloud_layers=[
                EnhancedCloudLayer(base_ft=430, top_ft=2000, coverage=CloudCoverage.BKN),
            ],
        )
        # Should use indices value (1400), not cloud layer base (430)
        assert _ceiling_from_sounding(sounding) == 1400.0

    def test_fallback_when_no_indices(self):
        """Without indices, falls back to cloud layer scan."""
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.BKN),
            ],
        )
        assert _ceiling_from_sounding(sounding) == 3000


# --- FlightCategory.worst ---

class TestFlightCategoryWorst:

    def test_vfr_worst(self):
        assert FlightCategory.worst([FlightCategory.VFR, FlightCategory.VFR]) == FlightCategory.VFR

    def test_mixed(self):
        assert FlightCategory.worst([FlightCategory.VFR, FlightCategory.IFR]) == FlightCategory.IFR

    def test_lifr_wins(self):
        assert FlightCategory.worst([FlightCategory.MVFR, FlightCategory.LIFR]) == FlightCategory.LIFR


# --- compute_airport_conditions ---

class TestComputeAirportConditions:

    def _make_rpa(self, icao: str | None, index: int, distance: float,
                  sounding: dict | None = None) -> RoutePointAnalysis:
        return RoutePointAnalysis(
            point_index=index,
            lat=48.0,
            lon=2.0,
            distance_from_origin_nm=distance,
            waypoint_icao=icao,
            waypoint_name=icao,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0),
            track_deg=90.0,
            sounding=sounding or {},
            model_divergence=[],
        )

    def test_basic_computation(self):
        """Test that airport conditions are computed for dep and arr."""
        dep_sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=5000, top_ft=8000, coverage=CloudCoverage.BKN),
            ],
        )
        arr_sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=800, top_ft=3000, coverage=CloudCoverage.OVC),
            ],
        )
        analyses = [
            self._make_rpa("LFPG", 0, 0.0, {"gfs": dep_sounding}),
            self._make_rpa(None, 1, 100.0),
            self._make_rpa("EGLL", 2, 200.0, {"gfs": arr_sounding}),
        ]

        result = compute_airport_conditions(
            analyses=analyses,
            cross_sections=[],
            models=["gfs"],
            dep_icao="LFPG", dep_name="Paris CDG",
            arr_icao="EGLL", arr_name="London Heathrow",
        )

        assert result.departure.icao == "LFPG"
        assert result.arrival.icao == "EGLL"
        assert len(result.departure.conditions) == 1
        assert len(result.arrival.conditions) == 1

        # Departure: 5000ft ceiling = VFR (no visibility data)
        dep_cond = result.departure.conditions[0]
        assert dep_cond.flight_category == FlightCategory.VFR
        assert dep_cond.ceiling_ft == 5000

        # Arrival: 800ft ceiling = IFR
        arr_cond = result.arrival.conditions[0]
        assert arr_cond.flight_category == FlightCategory.IFR
        assert arr_cond.ceiling_ft == 800

    def test_fallback_first_last_point(self):
        """When waypoint_icao doesn't match, uses first/last point."""
        sounding = SoundingAnalysis(cloud_layers=[])
        analyses = [
            self._make_rpa(None, 0, 0.0, {"gfs": sounding}),
            self._make_rpa(None, 1, 200.0, {"gfs": sounding}),
        ]

        result = compute_airport_conditions(
            analyses=analyses,
            cross_sections=[],
            models=["gfs"],
            dep_icao="LFPG", dep_name="Paris CDG",
            arr_icao="EGLL", arr_name="London Heathrow",
        )

        # Should still produce results using first/last
        assert len(result.departure.conditions) == 1
        assert len(result.arrival.conditions) == 1

    def test_runway_wind_selection(self):
        """Test that best runway is selected by lowest crosswind."""
        sounding = SoundingAnalysis(cloud_layers=[])
        analyses = [
            self._make_rpa("LFPG", 0, 0.0, {"gfs": sounding}),
        ]

        runway_data = {
            "LFPG": [
                RunwayEnd(id="09L", heading_deg=90.0),
                RunwayEnd(id="27R", heading_deg=270.0),
                RunwayEnd(id="08R", heading_deg=80.0),
                RunwayEnd(id="26L", heading_deg=260.0),
            ],
        }

        # Can't easily test runway wind without cross-section data
        # (wind comes from HourlyForecast), but we can verify structure
        result = compute_airport_conditions(
            analyses=analyses,
            cross_sections=[],
            models=["gfs"],
            dep_icao="LFPG", dep_name="Paris CDG",
            arr_icao="EGLL", arr_name="London Heathrow",
            runway_data=runway_data,
        )

        assert result.departure.runway_ends == runway_data["LFPG"]
        # No cross-section → no wind → no runway wind computed
        assert result.departure.conditions[0].best_runway is None


# --- ceiling reconciliation ---

class TestReconcileCeiling:

    def test_both_available_takes_lower(self):
        """When both sounding and NWP ceilings exist, take the lower."""
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.BKN),
            ],
        )
        hourly = HourlyForecast(
            time=datetime(2026, 3, 1, 10, 0),
            nwp_cloud_diagnostics=NWPCloudDiagnostics(ceiling_ft=2000),
        )
        result = reconcile_ceiling(sounding, hourly)
        assert result == 2000  # NWP is lower

    def test_both_available_sounding_lower(self):
        """When sounding is lower than NWP, take sounding."""
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=1500, top_ft=4000, coverage=CloudCoverage.OVC),
            ],
        )
        hourly = HourlyForecast(
            time=datetime(2026, 3, 1, 10, 0),
            nwp_cloud_diagnostics=NWPCloudDiagnostics(ceiling_ft=5000),
        )
        result = reconcile_ceiling(sounding, hourly)
        assert result == 1500  # Sounding is lower

    def test_only_sounding(self):
        """When only sounding has ceiling, use it."""
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=4000, top_ft=8000, coverage=CloudCoverage.BKN),
            ],
        )
        hourly = HourlyForecast(
            time=datetime(2026, 3, 1, 10, 0),
        )
        result = reconcile_ceiling(sounding, hourly)
        assert result == 4000

    def test_only_nwp(self):
        """When only NWP has ceiling, use it."""
        sounding = SoundingAnalysis(cloud_layers=[])
        hourly = HourlyForecast(
            time=datetime(2026, 3, 1, 10, 0),
            nwp_cloud_diagnostics=NWPCloudDiagnostics(ceiling_ft=2500),
        )
        result = reconcile_ceiling(sounding, hourly)
        assert result == 2500

    def test_neither_returns_none(self):
        """When neither source has ceiling, return None."""
        sounding = SoundingAnalysis(cloud_layers=[])
        hourly = HourlyForecast(time=datetime(2026, 3, 1, 10, 0))
        result = reconcile_ceiling(sounding, hourly)
        assert result is None

    def test_no_sounding_no_hourly(self):
        """Both None returns None."""
        result = reconcile_ceiling(None, None)
        assert result is None

    def test_sounding_sct_only_nwp_ceiling(self):
        """SCT layers are not ceilings; NWP ceiling should be used."""
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=2000, top_ft=4000, coverage=CloudCoverage.SCT),
            ],
        )
        hourly = HourlyForecast(
            time=datetime(2026, 3, 1, 10, 0),
            nwp_cloud_diagnostics=NWPCloudDiagnostics(ceiling_ft=3000),
        )
        result = reconcile_ceiling(sounding, hourly)
        assert result == 3000  # SCT doesn't count as ceiling

    def test_nwp_diag_no_ceiling_field(self):
        """NWP diagnostics exist but ceiling_ft is None — use sounding only."""
        sounding = SoundingAnalysis(
            cloud_layers=[
                EnhancedCloudLayer(base_ft=2500, top_ft=5000, coverage=CloudCoverage.BKN),
            ],
        )
        hourly = HourlyForecast(
            time=datetime(2026, 3, 1, 10, 0),
            nwp_cloud_diagnostics=NWPCloudDiagnostics(),  # no ceiling_ft
        )
        result = reconcile_ceiling(sounding, hourly)
        assert result == 2500
