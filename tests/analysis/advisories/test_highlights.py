"""Tests for advisory highlight geometry (scrim regions + verdict ribbon, #373).

Covers the shared helpers (``build_ribbon`` / ``build_regions`` / ``ribbon_peak``)
and the two emitting evaluators (``vmc_cruise`` and ``convective``) proven against
every geometry the schema allows.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    FlaggedCell,
    build_regions,
    build_ribbon,
    ribbon_peak,
)
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveRisk,
    EnhancedCloudLayer,
    HighlightSeverity,
    ModelAdvisoryResult,
    RoutePointAnalysis,
    SoundingAnalysis,
)

S = HighlightSeverity

_VMC_PARAMS = {"extent_pct_amber": 25, "extent_pct_red": 50}
_CONV_PARAMS = {
    "min_risk": 2,
    "extent_pct_amber": 20,
    "extent_pct_red": 50,
    "top_clearance_ft": 2000,
}


def _rpa(i: int, dist: float, sounding: dict[str, SoundingAnalysis]) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i,
        lat=48.0 + i * 0.5,
        lon=2.0 + i * 0.5,
        distance_from_origin_nm=dist,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=135.0,
        sounding=sounding,
    )


def _ctx(analyses: list[RoutePointAnalysis], *, models: list[str], total_nm: float,
         cruise_ft: int = 8000) -> RouteContext:
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=None,
        models=models,
        cruise_altitude_ft=cruise_ft,
        flight_ceiling_ft=18000,
        total_distance_nm=total_nm,
    )


def _assert_tiles(ribbon, total_nm: float) -> None:
    """Ribbon segments are sorted, non-overlapping, gapless, and tile [0, total]."""
    assert ribbon, "ribbon must not be empty"
    assert ribbon[0].dist_from_nm == 0.0
    assert abs(ribbon[-1].dist_to_nm - total_nm) < 1e-9
    for a, b in zip(ribbon, ribbon[1:]):
        assert a.dist_to_nm <= b.dist_from_nm + 1e-9  # non-overlapping
        assert abs(a.dist_to_nm - b.dist_from_nm) < 1e-9  # gapless
        assert a.dist_from_nm < a.dist_to_nm  # positive extent
        assert a.severity != b.severity  # runs are merged


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestBuildRibbon:
    def test_tiles_gapless_with_midpoint_boundaries(self):
        ribbon = build_ribbon(
            [(0.0, S.GREEN), (40.0, S.AMBER), (80.0, S.AMBER), (160.0, S.GREEN)],
            160.0,
        )
        _assert_tiles(ribbon, 160.0)
        # 3 runs: green | amber | green. Boundaries at midpoints 20 and 120.
        assert [(r.dist_from_nm, r.dist_to_nm, r.severity) for r in ribbon] == [
            (0.0, 20.0, S.GREEN),
            (20.0, 120.0, S.AMBER),
            (120.0, 160.0, S.GREEN),
        ]

    def test_all_same_severity_single_segment(self):
        ribbon = build_ribbon([(0.0, S.GREEN), (50.0, S.GREEN), (100.0, S.GREEN)], 100.0)
        assert len(ribbon) == 1
        assert ribbon[0].severity == S.GREEN
        _assert_tiles(ribbon, 100.0)

    def test_unavailable_gap_mid_route(self):
        ribbon = build_ribbon(
            [(0.0, S.RED), (50.0, S.UNAVAILABLE), (100.0, S.RED)],
            100.0,
        )
        _assert_tiles(ribbon, 100.0)
        assert [r.severity for r in ribbon] == [S.RED, S.UNAVAILABLE, S.RED]

    def test_empty_returns_empty(self):
        assert build_ribbon([], 100.0) == []

    def test_unsorted_input_is_sorted(self):
        ribbon = build_ribbon([(80.0, S.RED), (0.0, S.GREEN), (40.0, S.GREEN)], 120.0)
        _assert_tiles(ribbon, 120.0)
        assert ribbon[0].severity == S.GREEN and ribbon[-1].severity == S.RED


class TestBuildRegions:
    def test_flagged_points_only(self):
        cells = [
            (0.0, None),
            (40.0, FlaggedCell("cruise_imc", S.AMBER, 5000, 8000)),
            (80.0, None),
        ]
        regions = build_regions(cells, 120.0)
        assert len(regions) == 1
        assert regions[0].kind == "cruise_imc"
        assert regions[0].severity == S.AMBER

    def test_envelope_across_run(self):
        cells = [
            (0.0, FlaggedCell("cruise_imc", S.AMBER, 5000, 8000)),
            (40.0, FlaggedCell("cruise_imc", S.AMBER, 4000, 9000)),
            (80.0, FlaggedCell("cruise_imc", S.AMBER, 6000, 7000)),
        ]
        regions = build_regions(cells, 120.0)
        assert len(regions) == 1
        assert regions[0].base_ft == 4000  # min base
        assert regions[0].top_ft == 9000   # max top

    def test_different_severity_does_not_merge(self):
        cells = [
            (0.0, FlaggedCell("cruise_imc", S.RED, 6000, 12000)),
            (40.0, FlaggedCell("cruise_imc", S.AMBER, 6000, 10000)),
        ]
        regions = build_regions(cells, 120.0)
        assert len(regions) == 2
        assert regions[0].severity == S.RED
        assert regions[1].severity == S.AMBER

    def test_different_kind_does_not_merge(self):
        cells = [
            (0.0, FlaggedCell("tower", S.RED, 4000, 30000)),
            (40.0, FlaggedCell("tower_unresolved", S.RED, None, None)),
        ]
        regions = build_regions(cells, 120.0)
        assert len(regions) == 2
        assert regions[0].kind == "tower"
        assert regions[1].kind == "tower_unresolved"

    def test_full_column_stays_none(self):
        cells = [(40.0, FlaggedCell("tower_unresolved", S.RED, None, None))]
        regions = build_regions(cells, 120.0)
        assert regions[0].base_ft is None and regions[0].top_ft is None

    def test_gap_between_flagged_points_splits_regions(self):
        cells = [
            (0.0, FlaggedCell("cruise_imc", S.AMBER, 5000, 8000)),
            (40.0, None),
            (80.0, FlaggedCell("cruise_imc", S.AMBER, 5000, 8000)),
        ]
        regions = build_regions(cells, 120.0)
        assert len(regions) == 2


class TestRibbonPeak:
    def test_prefers_longest_red_run(self):
        ribbon = build_ribbon(
            [(0.0, S.RED), (20.0, S.AMBER), (40.0, S.AMBER), (60.0, S.AMBER), (80.0, S.RED)],
            100.0,
        )
        # Red wins over the (longer) amber run. Of the two red runs — [0,10] and
        # [70,100] — the longer trailing one wins; its center is 85.
        last_red = ribbon[-1]
        assert last_red.severity == S.RED
        assert ribbon_peak(ribbon) == (last_red.dist_from_nm + last_red.dist_to_nm) / 2.0

    def test_falls_back_to_amber(self):
        ribbon = build_ribbon([(0.0, S.GREEN), (40.0, S.AMBER), (80.0, S.AMBER)], 120.0)
        assert ribbon_peak(ribbon) is not None

    def test_none_when_all_green(self):
        ribbon = build_ribbon([(0.0, S.GREEN), (40.0, S.GREEN)], 80.0)
        assert ribbon_peak(ribbon) is None


# ---------------------------------------------------------------------------
# vmc_cruise evaluator
# ---------------------------------------------------------------------------

class TestVMCCruiseHighlights:
    def test_ovc_and_bkn_ribbon_and_regions(self):
        ovc = EnhancedCloudLayer(base_ft=6000, top_ft=12000, coverage=CloudCoverage.OVC)
        bkn = EnhancedCloudLayer(base_ft=6000, top_ft=10000, coverage=CloudCoverage.BKN)
        analyses = [
            _rpa(i, i * 20.0, {"gfs": SoundingAnalysis(cloud_layers=[ovc if i < 6 else bkn])})
            for i in range(10)
        ]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = VMCCruiseEvaluator.evaluate(ctx, _VMC_PARAMS)
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, 180.0)
        assert h.ribbon[0].severity == S.RED     # OVC → red
        assert h.ribbon[1].severity == S.AMBER   # BKN → amber
        # Two regions (different severity → no merge), envelope = the cloud band.
        assert [r.severity for r in h.regions] == [S.RED, S.AMBER]
        assert h.regions[0].kind == "cruise_imc"
        assert h.regions[0].base_ft == 6000 and h.regions[0].top_ft == 12000
        assert h.regions[1].top_ft == 10000
        assert h.peak_dist_nm is not None

    def test_all_green_no_regions_solid_ribbon(self):
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis()}) for i in range(10)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = VMCCruiseEvaluator.evaluate(ctx, _VMC_PARAMS)
        h = res.per_model[0].highlights
        assert h is not None
        assert h.regions == []
        assert len(h.ribbon) == 1 and h.ribbon[0].severity == S.GREEN
        assert h.peak_dist_nm is None

    def test_scattered_below_amber_is_green_no_region(self):
        """SCT/FEW at cruise → GREEN ribbon, no cutout (only BKN/OVC flag)."""
        sct = EnhancedCloudLayer(base_ft=6000, top_ft=10000, coverage=CloudCoverage.SCT)
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(cloud_layers=[sct])}) for i in range(5)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=80)
        res = VMCCruiseEvaluator.evaluate(ctx, _VMC_PARAMS)
        h = res.per_model[0].highlights
        assert h.regions == []
        assert all(seg.severity == S.GREEN for seg in h.ribbon)

    def test_unavailable_gap(self):
        ovc = EnhancedCloudLayer(base_ft=6000, top_ft=12000, coverage=CloudCoverage.OVC)
        analyses = []
        for i in range(10):
            sounding = {} if i == 5 else {"gfs": SoundingAnalysis(cloud_layers=[ovc])}
            analyses.append(_rpa(i, i * 20.0, sounding))
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = VMCCruiseEvaluator.evaluate(ctx, _VMC_PARAMS)
        h = res.per_model[0].highlights
        _assert_tiles(h.ribbon, 180.0)
        assert any(seg.severity == S.UNAVAILABLE for seg in h.ribbon)

    def test_no_data_model_has_no_highlights(self):
        """A model with no sounding anywhere → status UNAVAILABLE, highlights None."""
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis()}) for i in range(5)]
        ctx = _ctx(analyses, models=["gfs", "ecmwf"], total_nm=80)
        res = VMCCruiseEvaluator.evaluate(ctx, _VMC_PARAMS)
        ecmwf = next(m for m in res.per_model if m.model == "ecmwf")
        assert ecmwf.status == AdvisoryStatus.UNAVAILABLE
        assert ecmwf.highlights is None


# ---------------------------------------------------------------------------
# convective evaluator
# ---------------------------------------------------------------------------

class TestConvectiveHighlights:
    def test_high_tower_resolved_geometry(self):
        conv = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=2500, base_ft=4000, top_ft=35000,
        )
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}) for i in range(10)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, 180.0)
        assert all(seg.severity == S.RED for seg in h.ribbon)  # HIGH → red
        assert len(h.regions) == 1
        assert h.regions[0].kind == "tower"
        assert h.regions[0].base_ft == 4000 and h.regions[0].top_ft == 35000
        assert h.peak_dist_nm is not None

    def test_low_risk_is_amber(self):
        conv = ConvectiveAssessment(
            risk_level=ConvectiveRisk.LOW, cape_jkg=400, base_ft=4000, top_ft=25000,
        )
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}) for i in range(10)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert all(r.severity == S.AMBER for r in h.regions)

    def test_ghost_full_column(self):
        """Depth-unresolved (nwp_precip cover-only) → tower_unresolved full column."""
        conv = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=None, base_ft=None, top_ft=None,
            method="nwp_precip",
        )
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}) for i in range(10)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        assert len(h.regions) == 1
        assert h.regions[0].kind == "tower_unresolved"
        assert h.regions[0].base_ft is None and h.regions[0].top_ft is None

    def test_resolved_top_unknown_base_is_ghost_not_grounded_tower(self):
        """Known top but no base (e.g. nwp_lcl_top w/o LCL) → full-column ghost,
        not a solid 'tower' box the client would draw down to terrain."""
        conv = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=1500,
            base_ft=None, top_ft=30000, method="nwp_lcl_top",
        )
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}) for i in range(10)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        assert len(h.regions) == 1
        assert h.regions[0].kind == "tower_unresolved"
        assert h.regions[0].base_ft is None and h.regions[0].top_ft is None

    def test_below_cruise_tops_are_green(self):
        """Tops well below cruise (with clearance) → not flagged → GREEN ribbon."""
        # cruise 8000, top 3000 + clearance 2000 = 5000 <= 8000 → skipped.
        conv = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=1500, base_ft=1000, top_ft=3000,
        )
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}) for i in range(10)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180, cruise_ft=8000)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        assert all(seg.severity == S.GREEN for seg in h.ribbon)
        assert h.regions == []
        assert h.peak_dist_nm is None

    def test_peak_is_highest_cape_among_worst_risk(self):
        """Peak = worst graded risk, ties broken by highest CAPE."""
        # All HIGH, but the CAPE peaks at point index 3 (dist 60nm).
        capes = [1000, 1200, 1500, 3000, 1100, 900, 800, 700, 600, 500]
        analyses = [
            _rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=ConvectiveAssessment(
                risk_level=ConvectiveRisk.HIGH, cape_jkg=capes[i], base_ft=4000, top_ft=35000,
            ))})
            for i in range(10)
        ]
        ctx = _ctx(analyses, models=["gfs"], total_nm=180)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        assert h.peak_dist_nm == 60.0

    def test_partial_route_ribbon_tiles(self):
        """Convection on the middle third only → ribbon still tiles [0, total]."""
        analyses = []
        for i in range(9):
            if 3 <= i <= 5:
                conv = ConvectiveAssessment(
                    risk_level=ConvectiveRisk.HIGH, cape_jkg=2000, base_ft=4000, top_ft=35000,
                )
                analyses.append(_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}))
            else:
                analyses.append(_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(
                    convective=ConvectiveAssessment(risk_level=ConvectiveRisk.NONE),
                )}))
        ctx = _ctx(analyses, models=["gfs"], total_nm=160)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        h = res.per_model[0].highlights
        _assert_tiles(h.ribbon, 160.0)
        assert [seg.severity for seg in h.ribbon] == [S.GREEN, S.RED, S.GREEN]
        assert len(h.regions) == 1  # one merged tower over the middle run


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestOldPackDeserialization:
    def test_highlights_defaults_none(self):
        old = ModelAdvisoryResult.model_validate({"model": "gfs", "status": "green"})
        assert old.highlights is None

    def test_build_without_highlights(self):
        res = ModelAdvisoryResult.build(
            model="gfs", status=AdvisoryStatus.GREEN, detail="",
            affected=0, total=10, total_distance_nm=100.0,
        )
        assert res.highlights is None

    def test_roundtrip_preserves_highlights(self):
        conv = ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH, cape_jkg=2500, base_ft=4000, top_ft=35000,
        )
        analyses = [_rpa(i, i * 20.0, {"gfs": SoundingAnalysis(convective=conv)}) for i in range(5)]
        ctx = _ctx(analyses, models=["gfs"], total_nm=80)
        res = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
        dumped = res.per_model[0].model_dump()
        reloaded = ModelAdvisoryResult.model_validate(dumped)
        assert reloaded.highlights is not None
        assert reloaded.highlights.regions[0].kind == "tower"
