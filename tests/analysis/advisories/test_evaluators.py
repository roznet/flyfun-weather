"""Tests for individual advisory evaluators."""

from __future__ import annotations

from datetime import datetime

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
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveRisk,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

# Default convective params (LOW floor drives colour, MODERATE+ anchors headline).
_CONV_PARAMS = {
    "min_risk": 2,
    "affected_pct_amber": 20,
    "affected_pct_red": 50,
    "top_clearance_ft": 2000,
}


def _conv_route(
    per_model_risks: dict[str, list[ConvectiveRisk]],
    *,
    total_nm: float = 200.0,
    cruise_ft: int = 8000,
    precip_mm_h: float | None = None,
) -> RouteContext:
    """Build a RouteContext where each model carries an explicit per-point risk
    list. All lists must share the same length (= number of route points).

    Each point's convective assessment is a bare thermo-less CAPE risk (no
    convective_thermo/convective_nwp), so the DD-floor and cross-check paths stay
    inert — isolating the headline-wording logic under test.
    """
    lengths = {len(v) for v in per_model_risks.values()}
    assert len(lengths) == 1, "all per-model risk lists must be the same length"
    n = lengths.pop()
    analyses = []
    for i in range(n):
        sounding = {
            model: SoundingAnalysis(
                indices=ThermodynamicIndices(),
                convective=ConvectiveAssessment(
                    risk_level=risks[i], cape_jkg=1000.0,
                    convective_precip_mm_h=precip_mm_h,
                ),
            )
            for model, risks in per_model_risks.items()
        }
        analyses.append(
            RoutePointAnalysis(
                point_index=i,
                lat=48.0 + i * 0.5,
                lon=2.0 + i * 0.5,
                distance_from_origin_nm=i * (total_nm / max(n - 1, 1)),
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0),
                track_deg=135.0,
                sounding=sounding,
            )
        )
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=None,
        models=list(per_model_risks.keys()),
        cruise_altitude_ft=cruise_ft,
        flight_ceiling_ft=18000,
        total_distance_nm=total_nm,
    )


class TestIcingEscape:
    def test_green_no_icing(self, clear_context: RouteContext):
        result = IcingEscapeEvaluator.evaluate(clear_context, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20})
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "icing_escape"

    def test_clear_low_coverage_is_unavailable(self):
        """Ice-free verdict from soundings at too few route points → UNAVAILABLE.

        Safety-sensitive (#391 review): 2 of 10 points assessed (clear), rest have
        no sounding — the icing evaluator must not vouch for the 8 unseen points.
        """
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = IcingEscapeEvaluator.evaluate(
            ctx, {"terrain_margin_ft": 1000, "tight_margin_ft": 2000, "icing_coverage_pct_amber": 20},
        )
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

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

    def test_clear_subset_below_coverage_is_unavailable(self):
        """A clear verdict from a sounding subset too small to represent the route.

        Regression for #391: 2 of 10 route points assessed (both clear) used to
        grade GREEN, silently vouching for the 8 unassessed points. Below the
        coverage tolerance a clear verdict is UNAVAILABLE.
        """
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = VMCCruiseEvaluator.evaluate(ctx, {"bkn_pct_amber": 25, "ovc_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_hazard_on_partial_coverage_still_flags(self):
        """Real hazard on a partial-coverage subset still grades — never blanked.

        2 of 10 points assessed, both OVC at cruise: the coverage tolerance only
        downgrades a would-be-GREEN, never a flagged verdict (#391 trap).
        """
        from weatherbrief.models import CloudCoverage, EnhancedCloudLayer

        ovc = EnhancedCloudLayer(base_ft=6000, top_ft=12000, coverage=CloudCoverage.OVC)
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(
                        indices=ThermodynamicIndices(freezing_level_ft=5000),
                        cloud_layers=[ovc],
                    )}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = VMCCruiseEvaluator.evaluate(ctx, {"bkn_pct_amber": 25, "ovc_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.RED


class TestTurbulence:
    def test_green_smooth(self):
        """Complete coverage with a clear vertical-motion assessment → GREEN.

        A genuinely-smooth model has a ``VerticalMotionAssessment`` with no CAT
        layers and no strong omega — the "assessed, nothing flagged" case, which
        must still grade GREEN (distinct from the no-``vm`` UNAVAILABLE case
        below).
        """
        from weatherbrief.models import (
            VerticalMotionAssessment,
            VerticalMotionClass,
        )

        clear_vm = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT,
            cat_risk_layers=[],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(
                    indices=ThermodynamicIndices(freezing_level_ft=5000),
                    vertical_motion=clear_vm,
                )},
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = TurbulenceEvaluator.evaluate(ctx, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_no_vertical_motion_is_unavailable(self):
        """Soundings without a vertical-motion assessment cannot grade turbulence.

        Regression for #391: a model with soundings but no ``vertical_motion``
        (lite analysis / old pack) used to count total=N, affected=0 → GREEN
        "smooth ride". Absent data is not a smooth ride; it is UNAVAILABLE.
        """
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(
                    indices=ThermodynamicIndices(freezing_level_ft=5000),
                    vertical_motion=None,
                )},
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = TurbulenceEvaluator.evaluate(ctx, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
        assert result.per_model[0].total_points == 0

    def test_omega_less_model_with_cat_still_grades(self):
        """An omega-less model still has a complete Richardson CAT assessment.

        Guards against the over-correction #389 made and had to revert: gating
        the point on omega (``max_w_fpm``) would blank a valid CAT verdict. Here
        ``max_w_fpm`` is None but a MODERATE CAT layer sits at cruise → the
        advisory must still flag, not go UNAVAILABLE.
        """
        from weatherbrief.models import (
            CATRiskLayer,
            CATRiskLevel,
            VerticalMotionAssessment,
            VerticalMotionClass,
        )

        vm = VerticalMotionAssessment(
            classification=VerticalMotionClass.UNAVAILABLE,
            max_w_fpm=None, max_w_level_ft=None,
            cat_risk_layers=[CATRiskLayer(base_ft=7000, top_ft=10000, risk=CATRiskLevel.MODERATE)],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"icon_eu": SoundingAnalysis(
                    indices=ThermodynamicIndices(freezing_level_ft=5000),
                    vertical_motion=vm,
                )},
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["icon_eu"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = TurbulenceEvaluator.evaluate(ctx, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)
        assert result.per_model[0].total_points == 10

    def test_turbulent_route(self, turbulent_context: RouteContext):
        """CAT at cruise along full route → AMBER or RED."""
        result = TurbulenceEvaluator.evaluate(turbulent_context, {"icing_coverage_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)

    def test_clear_low_coverage_is_unavailable(self):
        """A smooth verdict from vm at too few route points → UNAVAILABLE (#391 review).

        2 of 10 points carry a (clear) vertical-motion assessment; the rest have
        none. A smooth grade cannot vouch for the 8 unassessed points.
        """
        from weatherbrief.models import VerticalMotionAssessment, VerticalMotionClass

        clear_vm = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT, cat_risk_layers=[],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(vertical_motion=(clear_vm if i < 2 else None))},
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = TurbulenceEvaluator.evaluate(ctx, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_hazard_low_coverage_still_flags(self):
        """SEVERE CAT at 2 of 10 assessed points still REDs — coverage never blanks a hazard."""
        from weatherbrief.models import (
            CATRiskLayer,
            CATRiskLevel,
            VerticalMotionAssessment,
            VerticalMotionClass,
        )

        severe_vm = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT,
            cat_risk_layers=[CATRiskLayer(base_ft=7000, top_ft=10000, risk=CATRiskLevel.SEVERE)],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(vertical_motion=(severe_vm if i < 2 else None))},
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = TurbulenceEvaluator.evaluate(ctx, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.RED

    @staticmethod
    def _bl_severe_ctx(n_flagged: int) -> RouteContext:
        """Route where *n_flagged* of 17 points carry a BL-severe layer at cruise."""
        from weatherbrief.models import (
            CATRiskLayer,
            CATRiskLevel,
            VerticalMotionAssessment,
            VerticalMotionClass,
        )

        def vm(boundary_layer: bool) -> VerticalMotionAssessment:
            return VerticalMotionAssessment(
                classification=VerticalMotionClass.QUIESCENT,
                cat_risk_layers=[CATRiskLayer(
                    base_ft=2000, top_ft=3000, risk=CATRiskLevel.SEVERE,
                    boundary_layer=boundary_layer,
                )],
            )

        clear = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT, cat_risk_layers=[],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 10.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"icon_eu": SoundingAnalysis(
                    vertical_motion=(vm(True) if i < n_flagged else clear),
                )},
            )
            for i in range(17)
        ]
        return RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["icon_eu"],
            cruise_altitude_ft=2500, flight_ceiling_ft=18000, total_distance_nm=170,
        )

    @staticmethod
    def _risk_ctx(n_flagged: int, risk: "CATRiskLevel") -> RouteContext:
        """Route where *n_flagged* of 17 points carry a free-atmosphere layer
        of the given risk at cruise."""
        from weatherbrief.models import (
            CATRiskLayer,
            VerticalMotionAssessment,
            VerticalMotionClass,
        )

        flagged = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT,
            cat_risk_layers=[CATRiskLayer(
                base_ft=2000, top_ft=3000, risk=risk, boundary_layer=False,
            )],
        )
        clear = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT, cat_risk_layers=[],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 10.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"icon_eu": SoundingAnalysis(
                    vertical_motion=(flagged if i < n_flagged else clear),
                )},
            )
            for i in range(17)
        ]
        return RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["icon_eu"],
            cruise_altitude_ft=2500, flight_ceiling_ft=18000, total_distance_nm=170,
        )

    def test_light_only_widespread_is_amber_not_red(self):
        """LIGHT chop over most of the route caps at AMBER (#533 follow-up).

        The RED tier of the coverage gate requires moderate-or-worse: with
        honest layer geometry, an Ri read of a sheared low-level day produces
        widespread light/moderate coverage, and light-everywhere is a
        ride-quality note, not a RED hazard.
        """
        from weatherbrief.models import CATRiskLevel

        result = TurbulenceEvaluator.evaluate(
            self._risk_ctx(15, CATRiskLevel.LIGHT),
            {"route_pct_amber": 20, "strong_w_fpm": 200},
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_moderate_widespread_still_reds(self):
        """MODERATE over half the route keeps the RED coverage tier."""
        from weatherbrief.models import CATRiskLevel

        result = TurbulenceEvaluator.evaluate(
            self._risk_ctx(15, CATRiskLevel.MODERATE),
            {"route_pct_amber": 20, "strong_w_fpm": 200},
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_boundary_layer_severe_does_not_force_red(self):
        """A BL-severe layer at 1 of 17 points is AMBER, not RED (#533).

        The EGNY→EGKB case: ICON resolved a sharp low-level shear sheet at
        2,500 ft that GFS smoothed away, and severe-anywhere → RED reddened
        the whole advisory off a single route point for a week. Boundary-layer
        severe shear now goes through the route-percentage gate — floored at
        AMBER so the SEVERE detail text stays coherent, but not RED.
        """
        result = TurbulenceEvaluator.evaluate(
            self._bl_severe_ctx(1), {"route_pct_amber": 20, "strong_w_fpm": 200},
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_boundary_layer_severe_reds_when_widespread(self):
        """BL-severe over most of the route still REDs — the gate, not a mute."""
        result = TurbulenceEvaluator.evaluate(
            self._bl_severe_ctx(15), {"route_pct_amber": 20, "strong_w_fpm": 200},
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_free_atmosphere_severe_still_forces_red(self):
        """A severe layer above the boundary layer keeps the severe-anywhere bypass."""
        from weatherbrief.models import (
            CATRiskLayer,
            CATRiskLevel,
            VerticalMotionAssessment,
            VerticalMotionClass,
        )

        severe_vm = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT,
            cat_risk_layers=[CATRiskLayer(
                base_ft=7000, top_ft=10000, risk=CATRiskLevel.SEVERE,
                boundary_layer=False,
            )],
        )
        clear = VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT, cat_risk_layers=[],
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 10.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(
                    vertical_motion=(severe_vm if i == 0 else clear),
                )},
            )
            for i in range(17)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=170,
        )
        result = TurbulenceEvaluator.evaluate(ctx, {"route_pct_amber": 20, "strong_w_fpm": 200})
        assert result.aggregate_status == AdvisoryStatus.RED


class TestConvective:
    def test_green_no_convection(self, clear_context: RouteContext):
        result = ConvectiveEvaluator.evaluate(clear_context, {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_moderate_convection(self, convective_context: RouteContext):
        result = ConvectiveEvaluator.evaluate(convective_context, {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50})
        # All 10 points have MODERATE risk → 100% > red threshold
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_cross_check_populated_grade_unchanged(self):
        """When the two signals diverge by >=2 tiers AT the driving point, the
        per-model cross_check is populated (#442 f/u), but the grade is identical
        to the same context with no NWP scheme attached — the cross-check is
        additive metadata only, never a regrade."""
        from datetime import datetime

        from weatherbrief.models import (
            ConvectiveAssessment,
            ConvectiveRisk,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        # Thermodynamics MODERATE at the driving point; the model's own scheme
        # quiet (NONE) — a 3-tier gap → the note fires ("thermo drives, NWP quiet").
        thermo = ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE,
            cape_jkg=1100.0,
            top_ft=25000.0,
            method="thermo",
        )
        nwp_quiet = ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cover_pct=0.0, method="nwp"
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
        assert "Thermo Convective shows" in res_with.per_model[0].cross_check
        assert res_without.per_model[0].cross_check is None
        # Grade must be unchanged by the cross-check.
        assert res_with.aggregate_status == res_without.aggregate_status
        assert res_with.per_model[0].status == res_without.per_model[0].status

    def _driver_ctx(self, nwp_risk, dd_risk, *, nwp_top=30000.0):
        """Route where every point has the given NWP-scheme + thermo tiers, so the
        driving (peak) point carries exactly that pair."""
        from datetime import datetime

        from weatherbrief.models import (
            ConvectiveAssessment,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )
        nwp = ConvectiveAssessment(
            risk_level=nwp_risk, top_ft=nwp_top,
            base_ft=(3000.0 if nwp_top is not None else None), method="nwp",
        )
        dd = ConvectiveAssessment(
            risk_level=dd_risk, cape_jkg=500.0, top_ft=30000.0, base_ft=3000.0,
            method="thermo",
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0 + i * 0.5, lon=2.0 + i * 0.5,
                distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(
                    indices=ThermodynamicIndices(),
                    convective=nwp, convective_nwp=nwp, convective_thermo=dd,
                )},
            )
            for i in range(10)
        ]
        return RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )

    def test_cross_check_nwp_drives_two_tier_gap(self):
        """#442 f/u: NWP fires (HIGH) at the driver while thermo lags (LOW, a
        2-tier gap) → note names the model's own forecast as the driver."""
        from weatherbrief.models import ConvectiveRisk
        params = {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50, "top_clearance_ft": 2000}
        res = ConvectiveEvaluator.evaluate(
            self._driver_ctx(ConvectiveRisk.HIGH, ConvectiveRisk.LOW), params)
        xc = res.per_model[0].cross_check
        assert xc is not None
        assert "NWP Convective" in xc and "drives this" in xc and "LOW" in xc

    def test_cross_check_suppressed_when_driver_one_off(self):
        """#442 f/u: NWP HIGH + thermo MODERATE (1-tier apart — normal spread) at
        the driver → no note. This is the ICON case that read as contradictory."""
        from weatherbrief.models import ConvectiveRisk
        params = {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50, "top_clearance_ft": 2000}
        res = ConvectiveEvaluator.evaluate(
            self._driver_ctx(ConvectiveRisk.HIGH, ConvectiveRisk.MODERATE), params)
        assert res.per_model[0].status == AdvisoryStatus.RED
        assert res.per_model[0].cross_check is None

    def test_cross_check_thermo_drives_when_nwp_quiet(self):
        """#442 f/u: green NWP (no tower) + thermo MODERATE → dd_trigger amber, and
        the note names the thermodynamics as the driver."""
        from weatherbrief.models import ConvectiveRisk
        params = {"min_risk": 2, "affected_pct_amber": 20, "affected_pct_red": 50, "top_clearance_ft": 2000}
        res = ConvectiveEvaluator.evaluate(
            self._driver_ctx(ConvectiveRisk.NONE, ConvectiveRisk.MODERATE, nwp_top=None), params)
        assert res.per_model[0].status == AdvisoryStatus.AMBER
        xc = res.per_model[0].cross_check
        assert xc is not None
        assert "Thermo Convective shows MODERATE" in xc and "quiet here" in xc

    def test_dd_trigger_uses_thermo_el_for_altitude_filter(self):
        """Regression (#283 review I1, updated for #442): when a green NWP is raised
        to a ``dd_trigger`` amber and the active track has no geometry
        (top_ft=None), the below-cruise filter falls back to the thermo EL so
        convection topping out below cruise is still skipped — not counted via the
        None-top bypass. Here the DD EL (FL180) is below the FL300 cruise → GREEN."""
        from datetime import datetime

        from weatherbrief.models import (
            ConvectiveAssessment,
            ConvectiveRisk,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        # Capped loaded gun: DD reads HIGH, thermo EL tops out at FL180 (well
        # below a FL300 cruise). The active (quiet ECMWF) NWP has no geometry.
        thermo = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=1500.0,
            top_ft=18000.0, method="thermo",
        )
        nwp_quiet = ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, top_ft=None, method="nwp",
        )

        def _ctx(active: ConvectiveAssessment) -> RouteContext:
            analyses = [
                RoutePointAnalysis(
                    point_index=i, lat=48.0 + i * 0.5, lon=2.0 + i * 0.5,
                    distance_from_origin_nm=i * 20.0,
                    interpolated_time=datetime(2026, 3, 1, 10, 0),
                    forecast_hour=datetime(2026, 3, 1, 9, 0),
                    track_deg=135.0,
                    sounding={
                        "gfs": SoundingAnalysis(
                            indices=ThermodynamicIndices(),
                            convective=active,
                            convective_thermo=thermo,
                            convective_nwp=nwp_quiet,
                        )
                    },
                )
                for i in range(10)
            ]
            return RouteContext(
                analyses=analyses, cross_sections=[], elevation=None,
                models=["gfs"], cruise_altitude_ft=30000,
                flight_ceiling_ft=41000, total_distance_nm=200,
            )

        params = {
            "min_risk": 2, "affected_pct_amber": 20,
            "affected_pct_red": 50, "top_clearance_ft": 2000,
        }
        # Active = quiet NWP → dd_trigger raises grade to amber. Thermo EL FL180 +
        # 2000 ft clearance = FL200 <= FL300 cruise → every point skipped → GREEN.
        res = ConvectiveEvaluator.evaluate(_ctx(nwp_quiet), params)
        assert res.aggregate_status == AdvisoryStatus.GREEN

    def test_shallow_nwp_moderate_below_cruise_greens_despite_deep_dd(self):
        """#442: DD no longer floors the colour, and ``dd_trigger`` only rescues a
        *green* NWP. Here the NWP fires its own MODERATE cell (so it is not green)
        but tops out at FL150, below the FL300 cruise → filtered to GREEN. The deep
        DD HIGH (EL FL350) does NOT floor it back up.

        Known limitation (§18): a shallow NWP MODERATE + deep DD HIGH falls in the
        cross-check's "neither active nor quiet" gap, so the DD's deeper reach is
        not surfaced even as amber. Rare configuration; flagged as a calibration
        item rather than handled here."""
        from datetime import datetime

        from weatherbrief.models import (
            ConvectiveAssessment,
            ConvectiveRisk,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        # NWP MODERATE, shallow top FL150; DD HIGH, EL FL350. Cruise FL300.
        nwp_shallow = ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, top_ft=15000.0, method="nwp",
        )
        thermo = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=1500.0,
            top_ft=35000.0, method="thermo",
        )
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0 + i * 0.5, lon=2.0 + i * 0.5,
                distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0),
                track_deg=135.0,
                sounding={
                    "gfs": SoundingAnalysis(
                        indices=ThermodynamicIndices(),
                        convective=nwp_shallow,
                        convective_thermo=thermo,
                        convective_nwp=nwp_shallow,
                    )
                },
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None,
            models=["gfs"], cruise_altitude_ft=30000,
            flight_ceiling_ft=41000, total_distance_nm=200,
        )
        params = {
            "min_risk": 2, "affected_pct_amber": 20,
            "affected_pct_red": 50, "top_clearance_ft": 2000,
        }
        res = ConvectiveEvaluator.evaluate(ctx, params)
        # NWP's own MODERATE tops below cruise → GREEN; DD no longer floors (#442).
        assert res.aggregate_status == AdvisoryStatus.GREEN


class TestConvectiveHeadline:
    """Headline wording (#300): anchor extent on MODERATE+, name the peak, and
    show a cross-model range — never the LOW-floor union as one number."""

    def test_moderate_plus_anchoring_not_low_union(self):
        """6 HIGH + 18 LOW of 24 points: every point clears the LOW floor (100%)
        but only 25% reaches MODERATE+. The headline extent must reflect the
        MODERATE+ 25%, not the 100% LOW union, with the peak named separately."""
        risks = [ConvectiveRisk.HIGH] * 6 + [ConvectiveRisk.LOW] * 18
        ctx = _conv_route({"gfs": risks})
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)

        assert res.aggregate_status == AdvisoryStatus.RED  # colour unchanged
        m = res.per_model[0]
        assert m.affected_points == 24
        assert m.affected_mod_points == 6
        # Per-model detail anchors on the MODERATE+ extent (25%) + peak HIGH.
        assert "MODERATE+" in m.detail
        assert "25%" in m.detail
        assert "peak HIGH" in m.detail
        assert "100%" not in m.detail  # the LOW union must not be the headline
        # Single model → aggregate collapses to a single % (no range).
        assert "25%" in res.aggregate_detail
        assert "peak HIGH" in res.aggregate_detail
        assert "across models" not in res.aggregate_detail

    def test_cross_model_range(self):
        """Three RED models with differing MODERATE+ coverage (25/50/75%) →
        aggregate shows the range across the supporting models + peak."""
        ctx = _conv_route(
            {
                "gfs": [ConvectiveRisk.HIGH] * 2 + [ConvectiveRisk.LOW] * 6,  # 25%
                "icon": [ConvectiveRisk.HIGH] * 4 + [ConvectiveRisk.LOW] * 4,  # 50%
                "ecmwf": [ConvectiveRisk.HIGH] * 6 + [ConvectiveRisk.LOW] * 2,  # 75%
            }
        )
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)

        assert res.aggregate_status == AdvisoryStatus.RED
        assert "MODERATE+" in res.aggregate_detail
        assert "25–75%" in res.aggregate_detail
        assert "across models" in res.aggregate_detail
        assert "peak HIGH" in res.aggregate_detail

    def test_low_only_favorability_fallback(self):
        """4 LOW + 6 NONE of 10: 40% clears the LOW floor (AMBER) but nothing
        reaches MODERATE. Wording must be 'primed, not firing' favorability —
        never 'MODERATE+ over 0%'."""
        risks = [ConvectiveRisk.LOW] * 4 + [ConvectiveRisk.NONE] * 6
        ctx = _conv_route({"gfs": risks})
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)

        assert res.aggregate_status == AdvisoryStatus.AMBER
        m = res.per_model[0]
        assert m.affected_points == 4
        assert m.affected_mod_points == 0
        assert "primed" in m.detail.lower()
        assert "MODERATE+" not in m.detail
        assert "40%" in m.detail
        # Aggregate (single model) → favorability single %, no range/peak.
        assert "primed" in res.aggregate_detail.lower()
        assert "40%" in res.aggregate_detail
        assert "across models" not in res.aggregate_detail
        assert "peak" not in res.aggregate_detail.lower()

    def test_low_only_realized_scheme_says_firing_not_primed(self):
        """Same LOW-only shape, but the scheme IS precipitating convectively.

        The nwp_precip ladder caps below MODERATE because depth is unknown from
        rate alone — that is NOT the same as a quiet scheme, and the copy must
        not say "not firing". Observed on EGTF→BIG→LFAT→LFQA 2026-07-17, where
        ECMWF carried 1.96 mm/h of native convective precip at LFQA (5x ICON's
        0.40, the hardest-raining model on the route) and was reported to the
        pilot as "Low-end CAPE primed, not firing".
        """
        risks = [ConvectiveRisk.LOW] * 4 + [ConvectiveRisk.NONE] * 6
        ctx = _conv_route({"ecmwf": risks}, precip_mm_h=1.96)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)

        m = res.per_model[0]
        assert m.affected_mod_points == 0          # still LOW-only
        assert "firing" in m.detail.lower()
        assert "not firing" not in m.detail.lower()
        assert "primed" not in m.detail.lower()
        # The cross-model headline follows the same realization rule.
        assert "firing" in res.aggregate_detail.lower()
        assert "not firing" not in res.aggregate_detail.lower()

    def test_low_only_quiet_scheme_still_says_primed(self):
        """The #300 case is unchanged: precip at/below the firing floor is a
        genuinely quiet scheme, so favourable CAPE must still read 'primed,
        not firing' and never masquerade as active convection."""
        risks = [ConvectiveRisk.LOW] * 4 + [ConvectiveRisk.NONE] * 6
        for precip in (None, 0.0, 0.1):
            ctx = _conv_route({"gfs": risks}, precip_mm_h=precip)
            res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
            m = res.per_model[0]
            assert "primed" in m.detail.lower(), precip
            assert "not firing" in m.detail.lower(), precip

    def test_range_collapses_when_models_agree(self):
        """Two RED models with identical MODERATE+ coverage (50%) → the range
        collapses to a single number; no '–' range, no 'across models'."""
        ctx = _conv_route(
            {
                "gfs": [ConvectiveRisk.HIGH] * 4 + [ConvectiveRisk.LOW] * 4,  # 50%
                "icon": [ConvectiveRisk.HIGH] * 4 + [ConvectiveRisk.LOW] * 4,  # 50%
            }
        )
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)

        assert res.aggregate_status == AdvisoryStatus.RED
        assert "MODERATE+ over 50%" in res.aggregate_detail
        assert "peak HIGH" in res.aggregate_detail
        assert "across models" not in res.aggregate_detail
        assert "–" not in res.aggregate_detail  # no en-dash range


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

    def test_clear_subset_below_coverage_is_unavailable(self):
        """Clear cloud-top verdict from too few assessed points → UNAVAILABLE.

        Regression for #391: missing points shrank the denominator and a clear
        2-of-10 subset stayed GREEN.
        """
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = CloudTopEvaluator.evaluate(ctx, {"margin_ft": 1000, "pct_amber": 25})
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE


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

    def test_all_absent_metrics_is_unavailable_not_good(self):
        """Divergences with every metric absent (mean=None) → UNAVAILABLE.

        Regression for #391: ``agreement=GOOD`` on an all-absent metric means
        "nothing to disagree about", not "models agreed". Branching on
        ``agreement`` alone graded a route of all-absent divergences GREEN "Good
        agreement across all models". Only real comparisons (mean not None)
        establish agreement.
        """
        from weatherbrief.models import AgreementLevel, ModelDivergence

        def _absent_divergence():
            return [
                ModelDivergence(
                    variable=v, model_values={"gfs": None, "ecmwf": None},
                    mean=None, spread=0.0, agreement=AgreementLevel.GOOD,
                )
                for v in ("temperature_c", "wind_speed_kt", "cloud_cover_pct")
            ]

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(), "ecmwf": SoundingAnalysis()},
                model_divergence=_absent_divergence(),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None,
            models=["gfs", "ecmwf"], cruise_altitude_ft=8000,
            flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = ModelAgreementEvaluator.evaluate(
            ctx, {"min_poor_vars": 3, "poor_pct_amber": 25, "poor_pct_red": 50}
        )
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_good_agreement_low_coverage_is_unavailable(self):
        """"Good agreement" from real comparisons at too few points → UNAVAILABLE (#391 review)."""
        from weatherbrief.models import AgreementLevel, ModelDivergence

        def _good_real():
            return [ModelDivergence(
                variable="temperature_c", model_values={"gfs": 5.0, "ecmwf": 6.0},
                mean=5.5, spread=1.0, agreement=AgreementLevel.GOOD,
            )]

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(), "ecmwf": SoundingAnalysis()},
                model_divergence=(_good_real() if i < 2 else []),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None,
            models=["gfs", "ecmwf"], cruise_altitude_ft=8000,
            flight_ceiling_ft=18000, total_distance_nm=200,
        )
        result = ModelAgreementEvaluator.evaluate(
            ctx, {"min_poor_vars": 3, "poor_pct_amber": 25, "poor_pct_red": 50}
        )
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# VFR Feasibility
# ---------------------------------------------------------------------------

_VFR_DEFAULTS = {
    "cloud_clearance_ft": 1000,
    "imc_pct_amber": 15,
    "imc_pct_red": 30,
    "terminal_corridor_nm": 5,
}


class TestVFRFeasibility:
    def test_green_clear_vfr(self, vfr_clear_context: RouteContext):
        """VFR at airports + clear en-route → GREEN."""
        result = VFRFeasibilityEvaluator.evaluate(vfr_clear_context, _VFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "vfr_feasibility"

    def test_no_airport_axis_not_hardcoded_green(self):
        """Missing airport data must not read as a clear airport axis (#391).

        With no airport conditions AND no en-route soundings, the whole
        composite is UNAVAILABLE — the airport axis no longer contributes a
        hardcoded GREEN that would keep the aggregate green.
        """
        from weatherbrief.analysis.advisories.vfr_feasibility import _check_airport_vfr

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={},
            )
            for i in range(5)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
            airport_conditions=None,
        )
        assert _check_airport_vfr(ctx, "gfs")[0] == AdvisoryStatus.UNAVAILABLE
        result = VFRFeasibilityEvaluator.evaluate(ctx, _VFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_no_airport_but_clear_enroute_still_green(self):
        """Real clear en-route with missing airport data still grades GREEN.

        Guards against over-correcting the airport-axis fix into blanking a
        composite that has genuine clear en-route evidence.
        """
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={"gfs": SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))},
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
            airport_conditions=None,
        )
        result = VFRFeasibilityEvaluator.evaluate(ctx, _VFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_thin_enroute_coverage_is_unavailable_even_with_vfr_airports(self):
        """Thin en-route coverage → UNAVAILABLE despite confirmed-VFR airports (#391 review).

        VFR feasibility is en-route-driven: a GREEN grade while 8 of 10 route
        points have no sounding overstates confidence even when the airports and
        the one covered corridor point are clear. The composite coverage guard
        catches the corridor/precip-mapping leak the per-axis en-route guard
        alone cannot.
        """
        from tests.analysis.advisories.conftest import _make_airport_conditions

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
            airport_conditions=_make_airport_conditions(models=["gfs"]),
        )
        result = VFRFeasibilityEvaluator.evaluate(ctx, _VFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

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
        assert len(entry.parameters) == 6

    def test_tunable_clearance(self, vfr_marginal_clearance_context: RouteContext):
        """With 500ft clearance threshold, 800ft gap is comfortable → GREEN."""
        params = {**_VFR_DEFAULTS, "cloud_clearance_ft": 500}
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_marginal_clearance_context, params,
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_amber_bkn_climb_corridor(self, vfr_bkn_corridor_context: RouteContext):
        """Clear cruise + VFR airports but a BKN deck in the climb-out → AMBER."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_bkn_corridor_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "BKN" in result.aggregate_detail

    def test_red_ovc_climb_corridor(self, vfr_ovc_corridor_context: RouteContext):
        """Clear cruise + VFR airports but an OVC deck in the climb-out → RED."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_ovc_corridor_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED
        assert "OVC" in result.aggregate_detail

    def test_short_route_no_corridor_double_count(self, vfr_short_route_overlap_context: RouteContext):
        """On a short route, a midpoint deck is attributed to the climb-out only,
        not double-counted as a descent deck at the far airport."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_short_route_overlap_context, _VFR_DEFAULTS,
        )
        assert result.aggregate_status == AdvisoryStatus.RED
        detail = result.aggregate_detail
        assert "climb-out" in detail
        assert "descent" not in detail  # not misattributed to the arrival end

    def test_midroute_subcruise_deck_stays_green(self, vfr_midroute_deck_context: RouteContext):
        """An OVC deck below cruise in the MIDDLE of the route (not near either
        airport) is irrelevant to VFR — you cruise above it → GREEN."""
        result = VFRFeasibilityEvaluator.evaluate(
            vfr_midroute_deck_context, _VFR_DEFAULTS,
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
    # No convective keys: §22 retired them. The convective axis is graded by the
    # convective advisory's parameters, resolved off ``ctx.advisory_params``.
}


class TestIFRFeasibility:
    def test_green_ifr_normal(self, ifr_normal_context: RouteContext):
        """IFR at airports, no icing/convective → GREEN."""
        result = IFRFeasibilityEvaluator.evaluate(ifr_normal_context, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.advisory_id == "ifr_feasibility"

    def test_no_airport_axis_not_hardcoded_green(self):
        """Missing airport data → UNAVAILABLE airport axis, not clear GREEN (#391)."""
        from weatherbrief.analysis.advisories.ifr_feasibility import _check_airport_ifr

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding={},
            )
            for i in range(5)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
            airport_conditions=None,
        )
        assert _check_airport_ifr(ctx, "gfs", 200, 400)[0] == AdvisoryStatus.UNAVAILABLE
        result = IFRFeasibilityEvaluator.evaluate(ctx, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_thin_enroute_coverage_is_unavailable_even_with_ifr_airports(self):
        """Thin en-route hazard coverage → UNAVAILABLE despite IFR-OK airports (#391 review).

        The convective axis got no coverage guard (only icing did) and a real
        airport axis could carry a would-be-GREEN composite past both — the
        composite guard closes that.
        """
        from tests.analysis.advisories.conftest import _make_airport_conditions
        from weatherbrief.models.airport_conditions import FlightCategory

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
            airport_conditions=_make_airport_conditions(
                dep_category=FlightCategory.IFR, dep_ceiling_ft=800,
                arr_category=FlightCategory.IFR, arr_ceiling_ft=900, models=["gfs"],
            ),
        )
        result = IFRFeasibilityEvaluator.evaluate(ctx, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_convective_hazard_partial_coverage_still_flags(self):
        """HIGH convective at 2 of 10 assessed points still REDs — coverage never blanks a hazard."""
        conv = ConvectiveAssessment(risk_level=ConvectiveRisk.HIGH, cape_jkg=2500)
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
                sounding=(
                    {"gfs": SoundingAnalysis(
                        indices=ThermodynamicIndices(freezing_level_ft=5000), convective=conv,
                    )}
                    if i < 2 else {}
                ),
            )
            for i in range(10)
        ]
        ctx = RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
            airport_conditions=None,
        )
        result = IFRFeasibilityEvaluator.evaluate(ctx, _IFR_DEFAULTS)
        assert result.aggregate_status == AdvisoryStatus.RED

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
        # 5, not 7: `convective_min_risk` and `convective_pct_red` were retired
        # in §22 — the convective axis is now graded by the convective
        # advisory's own parameters, so a second set here could only let the two
        # diverge again.
        assert len(entry.parameters) == 5
        assert not {"convective_min_risk", "convective_pct_red"} & {
            p.key for p in entry.parameters
        }

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
