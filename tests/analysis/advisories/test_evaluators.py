"""Tests for individual advisory evaluators."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.analysis.advisories.turbulence import TurbulenceEvaluator
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.model_agreement import ModelAgreementEvaluator
from weatherbrief.analysis.advisories.vfr_feasibility import VFRFeasibilityEvaluator
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.models import AdvisoryStatus


class TestIcingEscape:
    def test_green_no_icing(self, clear_context: RouteContext):
        result = IcingEscapeEvaluator.evaluate(clear_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20})
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "icing_escape"

    def test_icing_with_warm_escape(self, icing_context: RouteContext):
        """Icing present but freezing level above terrain — escape viable."""
        result = IcingEscapeEvaluator.evaluate(icing_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20})
        # All points have icing = 100% > 20% amber threshold
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)

    def test_no_escape_is_red(self, icing_no_escape_context: RouteContext):
        """Freezing level at terrain — no warm air escape."""
        result = IcingEscapeEvaluator.evaluate(
            icing_no_escape_context,
            {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20},
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_per_model_results(self, icing_context: RouteContext):
        result = IcingEscapeEvaluator.evaluate(icing_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20})
        assert len(result.per_model) == 2  # gfs + ecmwf
        for m in result.per_model:
            assert m.total_points > 0

    def test_high_altitude_icing_ignored(
        self, ifr_high_altitude_icing_context: RouteContext,
    ):
        """Icing at 14000ft with cruise 6000ft — above cruise + buffer → GREEN."""
        result = IcingEscapeEvaluator.evaluate(
            ifr_high_altitude_icing_context,
            {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20},
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_icing_at_cruise_still_triggers(self, icing_context: RouteContext):
        """Icing at 4000–10000ft with cruise 8000ft is relevant."""
        result = IcingEscapeEvaluator.evaluate(
            icing_context,
            {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20},
        )
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)


class TestVMCCruise:
    def test_green_clear_sky(self, clear_context: RouteContext):
        result = VMCCruiseEvaluator.evaluate(clear_context, {"bkn_pct_amber": 25, "ovc_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_red_ovc_at_cruise(self, cloudy_context: RouteContext):
        """OVC at cruise over 50% of route → RED."""
        result = VMCCruiseEvaluator.evaluate(cloudy_context, {"bkn_pct_amber": 25, "ovc_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.RED


class TestTurbulence:
    def test_green_smooth(self, clear_context: RouteContext):
        result = TurbulenceEvaluator.evaluate(clear_context, {"icing_coverage_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_turbulent_route(self, turbulent_context: RouteContext):
        """CAT at cruise along full route → AMBER or RED."""
        result = TurbulenceEvaluator.evaluate(turbulent_context, {"icing_coverage_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)


class TestConvective:
    def test_green_no_convection(self, clear_context: RouteContext):
        result = ConvectiveEvaluator.evaluate(clear_context, {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_moderate_convection(self, convective_context: RouteContext):
        result = ConvectiveEvaluator.evaluate(convective_context, {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50})
        # All 10 points have MODERATE risk → 100% > red threshold
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_cross_check_populated_grade_unchanged(self):
        """High-CAPE thermo + zero-cover NWP scheme populates per-model
        cross_check, but the grade is identical to the same context with no
        NWP scheme attached (cross-check is additive metadata only)."""
        from datetime import datetime

        from weatherbrief.models import (
            ConvectiveAssessment,
            ConvectiveRisk,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        thermo = ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE,
            cape_jkg=1100.0,
            top_ft=25000.0,
            method="thermo",
        )
        nwp_quiet = ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, cover_pct=0.0, method="nwp"
        )

        def _ctx(conv_nwp: ConvectiveAssessment | None) -> RouteContext:
            analyses = [
                RoutePointAnalysis(
                    point_index=i,
                    lat=48.0 + i * 0.5,
                    lon=2.0 + i * 0.5,
                    distance_from_origin_nm=i * 20.0,
                    interpolated_time=datetime(2026, 3, 1, 10, 0),
                    forecast_hour=datetime(2026, 3, 1, 9, 0),
                    track_deg=135.0,
                    sounding={
                        "gfs": SoundingAnalysis(
                            indices=ThermodynamicIndices(),
                            convective=thermo,
                            convective_thermo=thermo,
                            convective_nwp=conv_nwp,
                        )
                    },
                )
                for i in range(10)
            ]
            return RouteContext(
                analyses=analyses,
                cross_sections=[],
                elevation=None,
                models=["gfs"],
                cruise_altitude_ft=8000,
                flight_ceiling_ft=18000,
                total_distance_nm=200,
            )

        params = {
            "min_risk": 2,
            "affected_pct_amber": 20,
            "affected_pct_red": 50,
            "top_clearance_ft": 2000,
        }

        res_with = ConvectiveEvaluator.evaluate(_ctx(nwp_quiet), params)
        res_without = ConvectiveEvaluator.evaluate(_ctx(None), params)

        assert res_with.per_model[0].cross_check is not None
        assert "not corroborated" in res_with.per_model[0].cross_check
        assert res_without.per_model[0].cross_check is None
        # Grade must be unchanged by the cross-check.
        assert res_with.aggregate_status == res_without.aggregate_status
        assert res_with.per_model[0].status == res_without.per_model[0].status


class TestCloudTop:
    def test_green_no_clouds(self, clear_context: RouteContext):
        result = CloudTopEvaluator.evaluate(clear_context, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_tops_above_ceiling(self, cloudy_context: RouteContext):
        """Cloud tops at 12000ft, ceiling 18000ft — still reachable."""
        result = CloudTopEvaluator.evaluate(cloudy_context, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_ignores_cirrus_above_ceiling(self, high_cirrus_context: RouteContext):
        """High cirrus (35000-39000ft) above ceiling (18000ft) should be ignored.
        Only lower cloud (6000-10000ft) should be considered — tops well within ceiling."""
        result = CloudTopEvaluator.evaluate(high_cirrus_context, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.GREEN
        # max_top should be 10000 (lower layer), not 39000 (cirrus)
        for m in result.per_model:
            assert "10000" in m.detail or "No significant" in m.detail

    def test_only_cirrus_is_green(self, only_cirrus_context: RouteContext):
        """When ALL layers are above ceiling, treat as no significant clouds."""
        result = CloudTopEvaluator.evaluate(only_cirrus_context, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.GREEN
        for m in result.per_model:
            assert "No significant" in m.detail


_FIKI_DEFAULTS = {
    "proximity_nm": 50,
    "cruise_icing_buffer_ft": 2000,
    "transit_thickness_amber_ft": 3000,
    "transit_thickness_red_ft": 5000,
    "clear_cruise_amber_pct": 80,
    "clear_cruise_red_pct": 50,
    "severe_is_red": 1,
}


class TestFIKIIcing:
    def test_green_no_icing(self, clear_context: RouteContext):
        result = FIKIIcingEvaluator.evaluate(clear_context, _FIKI_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "fiki_icing"
        for m in result.per_model:
            assert "No icing along route" in m.detail

    def test_full_route_icing_is_red(self, icing_context: RouteContext):
        """Icing 4000–10000ft along entire route, cruise 8000ft.

        Transit: 4000ft (amber), cruise: 0% clear (red) → RED overall.
        """
        result = FIKIIcingEvaluator.evaluate(icing_context, _FIKI_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.RED
        for m in result.per_model:
            assert "cruise 0% clear" in m.detail

    def test_departure_only_icing(self, fiki_departure_icing_context: RouteContext):
        """Icing only near departure, transit 5000ft, 70% clear cruise.

        Transit: 5000ft >= 5000 (red), cruise: 70% < 80% (amber) → RED.
        """
        result = FIKIIcingEvaluator.evaluate(
            fiki_departure_icing_context, _FIKI_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED
        for m in result.per_model:
            assert "dep 5000ft" in m.detail
            assert "cruise 70% clear" in m.detail

    def test_icing_above_cruise_with_margin(
        self, fiki_icing_above_cruise_context: RouteContext,
    ):
        """Icing 11000–14000ft, cruise 8000ft, 3000ft clearance > 2000ft buffer.

        Transit: 0ft, cruise: 100% clear → GREEN.
        """
        result = FIKIIcingEvaluator.evaluate(
            fiki_icing_above_cruise_context, _FIKI_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN
        for m in result.per_model:
            assert "No icing along route" in m.detail

    def test_icing_close_above_cruise_not_clear(
        self, fiki_icing_close_above_cruise_context: RouteContext,
    ):
        """Icing 9000–12000ft, cruise 8000ft, only 1000ft clearance < 2000ft buffer.

        Transit: 0ft (green), cruise: 0% clear (red) → RED.
        """
        result = FIKIIcingEvaluator.evaluate(
            fiki_icing_close_above_cruise_context, _FIKI_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED
        for m in result.per_model:
            assert "cruise 0% clear" in m.detail

    def test_sld_always_red(self, fiki_sld_context: RouteContext):
        result = FIKIIcingEvaluator.evaluate(fiki_sld_context, _FIKI_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.RED
        for m in result.per_model:
            assert "SLD" in m.detail

    def test_tunable_proximity(self, fiki_departure_icing_context: RouteContext):
        """With proximity_nm=20, only point at 0nm and 20nm count for departure.

        Icing at 0, 20, 40nm. With 20nm radius, only 0 and 20nm are departure.
        Transit thickness still 5000ft (same zones), but 40nm point is no longer
        in departure zone — still has icing for cruise clear count though.
        """
        params = {**_FIKI_DEFAULTS, "proximity_nm": 20}
        result = FIKIIcingEvaluator.evaluate(fiki_departure_icing_context, params)
        # Should still detect transit icing at departure
        for m in result.per_model:
            assert "dep 5000ft" in m.detail

    def test_tunable_buffer(self, fiki_icing_close_above_cruise_context: RouteContext):
        """With smaller buffer (500ft), 1000ft clearance becomes sufficient."""
        params = {**_FIKI_DEFAULTS, "cruise_icing_buffer_ft": 500}
        result = FIKIIcingEvaluator.evaluate(
            fiki_icing_close_above_cruise_context, params,
        )
        # 1000ft clearance > 500ft buffer → cruise is clear → GREEN
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_per_model_results(self, icing_context: RouteContext):
        result = FIKIIcingEvaluator.evaluate(icing_context, _FIKI_DEFAULTS)
        assert len(result.per_model) == 2  # gfs + ecmwf
        for m in result.per_model:
            assert m.total_points > 0


class TestModelAgreement:
    def test_green_good_agreement(self, clear_context: RouteContext):
        result = ModelAgreementEvaluator.evaluate(clear_context, {"poor_pct_amber": 25, "poor_pct_red": 50})
        # No model_divergence data → unavailable or green
        assert result.aggregate_status in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE)

    def test_poor_agreement(self, poor_agreement_context: RouteContext):
        """100% poor agreement (3+ variables) → RED."""
        result = ModelAgreementEvaluator.evaluate(
            poor_agreement_context,
            {"min_poor_vars": 3, "poor_pct_amber": 25, "poor_pct_red": 50},
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_few_poor_variables_is_green(self, poor_agreement_context: RouteContext):
        """With high min_poor_vars threshold, few POOR variables → GREEN."""
        result = ModelAgreementEvaluator.evaluate(
            poor_agreement_context,
            {"min_poor_vars": 5, "poor_pct_amber": 25, "poor_pct_red": 50},
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN


# ---------------------------------------------------------------------------
# VFR Feasibility
# ---------------------------------------------------------------------------

_VFR_DEFAULTS = {
    "cloud_clearance_ft": 1000,
    "imc_pct_amber": 15,
    "imc_pct_red": 30,
}


class TestVFRFeasibility:
    def test_green_clear_vfr(self, vfr_clear_context: RouteContext):
        """VFR at airports + clear en-route → GREEN."""
        result = VFRFeasibilityEvaluator.evaluate(vfr_clear_context, _VFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "vfr_feasibility"

    def test_red_ifr_airport(self, vfr_ifr_airport_context: RouteContext):
        """IFR at arrival airport → RED for VFR."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_ifr_airport_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_amber_mvfr_airport(self, vfr_mvfr_airport_context: RouteContext):
        """MVFR at departure → AMBER for VFR."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_mvfr_airport_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_marginal_cloud_clearance(self, vfr_marginal_clearance_context: RouteContext):
        """BKN cloud 800ft above cruise — below 1000ft clearance → AMBER.

        All points have marginal clearance (100% > 15% amber threshold) but
        no points are actually in IMC (0% < 30% red threshold).
        """
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_marginal_clearance_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_red_imc_enroute(self, vfr_imc_enroute_context: RouteContext):
        """OVC at cruise along entire route → RED (100% > 30%)."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_imc_enroute_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_no_airport_data_still_works(self, clear_context: RouteContext):
        """Without airport conditions, evaluates en-route only."""
        result = VFRFeasibilityEvaluator.evaluate(clear_context, _VFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_per_model_results(self, vfr_clear_context: RouteContext):
        result = VFRFeasibilityEvaluator.evaluate(vfr_clear_context, _VFR_DEFAULTS)
        assert len(result.per_model) == 2  # gfs + ecmwf
        for m in result.per_model:
            assert m.total_points > 0

    def test_catalog_entry(self):
        entry = VFRFeasibilityEvaluator.catalog_entry()
        assert entry.id == "vfr_feasibility"
        assert entry.category == "flight_rules"
        assert len(entry.parameters) == 3

    def test_tunable_clearance(self, vfr_marginal_clearance_context: RouteContext):
        """With 500ft clearance threshold, 800ft gap is comfortable → GREEN."""
        params = {**_VFR_DEFAULTS, "cloud_clearance_ft": 500}
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_marginal_clearance_context, params,
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN


# ---------------------------------------------------------------------------
# IFR Feasibility
# ---------------------------------------------------------------------------

_IFR_DEFAULTS = {
    "min_dep_ceiling_ft": 200,
    "min_arr_ceiling_ft": 400,
    "icing_pct_amber": 15,
    "icing_pct_red": 30,
    "convective_min_risk": 3,
    "convective_pct_red": 10,
}


class TestIFRFeasibility:
    def test_green_ifr_normal(self, ifr_normal_context: RouteContext):
        """IFR at airports, no icing/convective → GREEN."""
        result = IFRFeasibilityEvaluator.evaluate(ifr_normal_context, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "ifr_feasibility"

    def test_amber_lifr(self, ifr_lifr_context: RouteContext):
        """LIFR at arrival (ceiling 450ft >= 400ft min) → AMBER."""
        result = IFRFeasibilityEvaluator.evaluate(ifr_lifr_context, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_red_lifr_below_minimums(self, ifr_lifr_below_mins_context: RouteContext):
        """LIFR at arrival, ceiling 150ft < 400ft minimum → RED."""
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_lifr_below_mins_context, _IFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_red_heavy_icing(self, ifr_heavy_icing_context: RouteContext):
        """Icing at 100% of route > 30% threshold → RED."""
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_heavy_icing_context, _IFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_red_convective(self, ifr_convective_context: RouteContext):
        """HIGH convective risk → RED."""
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_convective_context, _IFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_no_airport_data(self, icing_context: RouteContext):
        """Without airport conditions, evaluates en-route factors only."""
        result = IFRFeasibilityEvaluator.evaluate(icing_context, _IFR_DEFAULTS)
        # 100% icing > 30% threshold → RED
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_per_model_results(self, ifr_normal_context: RouteContext):
        result = IFRFeasibilityEvaluator.evaluate(ifr_normal_context, _IFR_DEFAULTS)
        assert len(result.per_model) == 2
        for m in result.per_model:
            assert m.total_points > 0

    def test_catalog_entry(self):
        entry = IFRFeasibilityEvaluator.catalog_entry()
        assert entry.id == "ifr_feasibility"
        assert entry.category == "flight_rules"
        assert len(entry.parameters) == 7

    def test_tunable_icing_threshold(self, ifr_heavy_icing_context: RouteContext):
        """With higher icing threshold, 100% icing should still be RED."""
        params = {**_IFR_DEFAULTS, "icing_pct_red": 80}
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_heavy_icing_context, params,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_vfr_airports_green_for_ifr(self, vfr_clear_context: RouteContext):
        """VFR airports are fine for IFR — GREEN."""
        result = IFRFeasibilityEvaluator.evaluate(vfr_clear_context, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_high_altitude_icing_ignored(
        self, ifr_high_altitude_icing_context: RouteContext,
    ):
        """Icing at 14000ft with cruise 6000ft — above cruise + 2000ft buffer → GREEN."""
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_high_altitude_icing_context, _IFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_icing_at_cruise_still_red(self, ifr_heavy_icing_context: RouteContext):
        """Icing at 4000–10000ft with cruise 8000ft is relevant → RED."""
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_heavy_icing_context, _IFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_tunable_icing_altitude_buffer(
        self, ifr_high_altitude_icing_context: RouteContext,
    ):
        """With large buffer (10000ft), icing at 14000ft becomes relevant for cruise 6000ft."""
        params = {**_IFR_DEFAULTS, "icing_altitude_buffer_ft": 10000}
        result = IFRFeasibilityEvaluator.evaluate(
            ifr_high_altitude_icing_context, params,
        )
        # 14000 < 6000 + 10000 = 16000 → relevant → 100% icing > 30% → RED
        assert result.aggregate_status == AdvisoryStatus.RED
