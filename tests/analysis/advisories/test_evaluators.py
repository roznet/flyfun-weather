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
                    risk_level=risks[i], cape_jkg=1000.0
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

    def test_dd_floor_uses_thermo_el_for_altitude_filter(self):
        """Regression (#283 review I1): when the DD floor raises a quiet-NWP
        point and the active track has no geometry (top_ft=None), the below-cruise
        filter falls back to the thermo EL so convection topping out below cruise
        is still skipped — not counted via the None-top bypass."""
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
        # Active = quiet NWP (DD floor raises grade to HIGH). Thermo EL FL180 +
        # 2000 ft clearance = FL200 <= FL300 cruise → every point skipped → GREEN.
        res = ConvectiveEvaluator.evaluate(_ctx(nwp_quiet), params)
        assert res.aggregate_status == AdvisoryStatus.GREEN

    def test_dd_floor_altitude_filter_uses_deeper_top(self):
        """Regression (#283 review): when the DD floor applies and the NWP top is
        non-None but shallow, the below-cruise filter uses the deeper DD EL — a
        shallow NWP top must not filter out a point graded HIGH by a deep DD EL."""
        from datetime import datetime

        from weatherbrief.models import (
            ConvectiveAssessment,
            ConvectiveRisk,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        # NWP MODERATE, shallow top FL150; DD HIGH, EL FL350. Cruise FL300 sits
        # above the NWP top but below the DD EL → the HIGH grade reaches cruise.
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
        # DD EL FL350 reaches FL300 cruise → not filtered → HIGH → RED.
        assert res.aggregate_status == AdvisoryStatus.RED


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
