"""Tests for individual advisory evaluators."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.analysis.advisories.turbulence import TurbulenceEvaluator
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.freezing_level import FreezingLevelEvaluator
from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.model_agreement import ModelAgreementEvaluator
from weatherbrief.models import AdvisoryStatus


class TestIcingEscape:
    def test_green_no_icing(self, clear_context: RouteContext):
        result = IcingEscapeEvaluator.evaluate(clear_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "route_pct_amber": 20})
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "icing_escape"

    def test_icing_with_warm_escape(self, icing_context: RouteContext):
        """Icing present but freezing level above terrain — escape viable."""
        result = IcingEscapeEvaluator.evaluate(icing_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "route_pct_amber": 20})
        # All points have icing = 100% > 20% amber threshold
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)

    def test_no_escape_is_red(self, icing_no_escape_context: RouteContext):
        """Freezing level at terrain — no warm air escape."""
        result = IcingEscapeEvaluator.evaluate(
            icing_no_escape_context,
            {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "route_pct_amber": 20},
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_per_model_results(self, icing_context: RouteContext):
        result = IcingEscapeEvaluator.evaluate(icing_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "route_pct_amber": 20})
        assert len(result.per_model) == 2  # gfs + ecmwf
        for m in result.per_model:
            assert m.total_points > 0


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
        result = TurbulenceEvaluator.evaluate(clear_context, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_turbulent_route(self, turbulent_context: RouteContext):
        """CAT at cruise along full route → AMBER or RED."""
        result = TurbulenceEvaluator.evaluate(turbulent_context, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)


class TestConvective:
    def test_green_no_convection(self, clear_context: RouteContext):
        result = ConvectiveEvaluator.evaluate(clear_context, {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_moderate_convection(self, convective_context: RouteContext):
        result = ConvectiveEvaluator.evaluate(convective_context, {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50})
        # All 10 points have MODERATE risk → 100% > red threshold
        assert result.aggregate_status == AdvisoryStatus.RED


class TestFreezingLevel:
    def test_green_high_freezing(self, clear_context: RouteContext):
        """Freezing level at 5000ft, terrain at 500ft — well clear."""
        result = FreezingLevelEvaluator.evaluate(clear_context, {"margin_ft": 1000, "tight_margin_ft": 2000})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_red_low_freezing_high_terrain(self, icing_no_escape_context: RouteContext):
        """Freezing level at 3500ft, terrain up to 5000ft — RED."""
        result = FreezingLevelEvaluator.evaluate(
            icing_no_escape_context,
            {"margin_ft": 1000, "tight_margin_ft": 2000},
        )
        assert result.aggregate_status == AdvisoryStatus.RED


class TestCloudTop:
    def test_green_no_clouds(self, clear_context: RouteContext):
        result = CloudTopEvaluator.evaluate(clear_context, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_tops_above_ceiling(self, cloudy_context: RouteContext):
        """Cloud tops at 12000ft, ceiling 18000ft — still reachable."""
        result = CloudTopEvaluator.evaluate(cloudy_context, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.GREEN


class TestModelAgreement:
    def test_green_good_agreement(self, clear_context: RouteContext):
        result = ModelAgreementEvaluator.evaluate(clear_context, {"poor_pct_amber": 25, "poor_pct_red": 50})
        # No model_divergence data → unavailable or green
        assert result.aggregate_status in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE)

    def test_poor_agreement(self, poor_agreement_context: RouteContext):
        """100% poor agreement → RED."""
        result = ModelAgreementEvaluator.evaluate(
            poor_agreement_context,
            {"poor_pct_amber": 25, "poor_pct_red": 50},
        )
        assert result.aggregate_status == AdvisoryStatus.RED
