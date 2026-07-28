"""Tests for the approach_feasibility advisory evaluator (issue #509).

The advisory joins three facts nothing else joins: the destination's ceiling,
the runway the wind favours, and which runways actually have a published
approach. The cases below pin the grade map, the two rules that keep it honest
(RED needs a hard fact; estimate uncertainty may only soften), and the
UNAVAILABLE-vs-no-approach distinction.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.approach_feasibility import (
    ApproachFeasibilityEvaluator,
)
from weatherbrief.models import AdvisoryStatus
from weatherbrief.units import M_PER_SM
from weatherbrief.models.airport_conditions import (
    AirportApproaches,
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
    RunwayApproach,
    RunwayEnd,
    RunwayWind,
)

MODELS = ["gfs", "ecmwf"]

# Ceiling/visibility that produce each category (the evaluator branches on the
# stored ``flight_category``; these keep the fixtures self-consistent).
_CAT_DEFAULTS: dict[FlightCategory, tuple[int, float]] = {
    FlightCategory.VFR: (5000, 10.0),
    FlightCategory.MVFR: (2000, 4.0),
    FlightCategory.IFR: (800, 2.0),
    FlightCategory.LIFR: (300, 0.5),
}


def _defaults() -> dict[str, float]:
    entry = ApproachFeasibilityEvaluator.catalog_entry()
    return {p.key: p.default for p in entry.parameters}


def _runway_wind(runway_id: str, heading: float, headwind: float, crosswind: float = 0.0):
    return RunwayWind(
        runway_id=runway_id, heading_deg=heading,
        crosswind_kt=crosswind, headwind_kt=headwind,
    )


def _conditions(
    category: FlightCategory,
    all_runways: list[RunwayWind],
    *,
    best_runway: RunwayWind | None = None,
    ceiling_ft: int | None = None,
    visibility_m: float | None = None,
    icao: str = "EGKA",
) -> AirportConditions:
    """Airport conditions where every model sees the same arrival picture."""
    default_ceiling, vis = _CAT_DEFAULTS[category]
    ceiling = default_ceiling if ceiling_ft is None else ceiling_ft
    vis_m = vis * M_PER_SM if visibility_m is None else visibility_m
    # Best runway by pure wind (what enrich_wind / airport_wind pick) — the
    # approach-blind answer this advisory exists to cross-check.
    best = best_runway or (
        min(all_runways, key=lambda r: (r.crosswind_kt, -r.headwind_kt))
        if all_runways else None
    )
    conds = [
        AirportModelCondition(
            model=m, flight_category=category, ceiling_ft=ceiling,
            visibility_sm=vis, visibility_m=vis_m,
            wind_speed_kt=15.0, wind_direction_deg=240.0,
            best_runway=best, all_runways=all_runways,
        )
        for m in MODELS
    ]
    summary = AirportConditionsSummary(
        icao=icao, name=icao,
        runway_ends=[RunwayEnd(id=r.runway_id, heading_deg=r.heading_deg) for r in all_runways],
        conditions=conds,
    )
    dep = AirportConditionsSummary(icao="EGTK", name="EGTK", conditions=[
        AirportModelCondition(model=m, flight_category=FlightCategory.VFR) for m in MODELS
    ])
    return AirportConditions(departure=dep, arrival=summary)


def _approaches(
    *approaches: RunwayApproach, has_procedure_data: bool = True, icao: str = "EGKA",
) -> AirportApproaches:
    return AirportApproaches(
        icao=icao,
        has_procedure_data=has_procedure_data or bool(approaches),
        approaches=list(approaches),
    )


def _ctx(
    conditions: AirportConditions | None,
    approaches: AirportApproaches | None,
) -> RouteContext:
    return RouteContext(
        analyses=[], cross_sections=[], elevation=None, models=MODELS,
        cruise_altitude_ft=6000, flight_ceiling_ft=12000, total_distance_nm=120,
        airport_conditions=conditions, arrival_approaches=approaches,
    )


def _grade(ctx: RouteContext, **overrides) -> AdvisoryStatus:
    params = {**_defaults(), **overrides}
    return ApproachFeasibilityEvaluator.evaluate(ctx, params).aggregate_status


# The EGKA shape from #509: hard runway 02/20 has the approaches, the grass
# 06/24 does not, so the wind can favour a runway with no way in.
_RWY_02 = _runway_wind("02", 20.0, headwind=-8.0, crosswind=6.0)   # 8 kt tailwind
_RWY_20 = _runway_wind("20", 200.0, headwind=8.0, crosswind=6.0)
_RWY_24 = _runway_wind("24", 240.0, headwind=15.0, crosswind=0.0)  # wind-best
_ILS_02 = RunwayApproach(name="RWY02 ILS", approach_type="ILS", runway_id="02")
_ILS_20 = RunwayApproach(name="RWY20 ILS", approach_type="ILS", runway_id="20")


class TestUnavailable:
    def test_no_approach_data_is_unavailable_not_green(self):
        """A missing collection step is a data gap, never a clear approach."""
        ctx = _ctx(_conditions(FlightCategory.IFR, [_RWY_02, _RWY_20]), None)
        assert _grade(ctx) == AdvisoryStatus.UNAVAILABLE

    def test_no_airport_conditions_is_unavailable(self):
        assert _grade(_ctx(None, _approaches(_ILS_02))) == AdvisoryStatus.UNAVAILABLE

    def test_airport_with_no_approaches_is_graded_not_unavailable(self):
        """Absence of procedures means "no approach", not "could not assess"."""
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_20]),
            _approaches(has_procedure_data=False),
        )
        assert _grade(ctx) == AdvisoryStatus.RED

    def test_failed_lookup_is_unavailable_not_a_red_no_approach(self):
        """A data gap must never impersonate the grade map's most severe input.

        ``lookup_failed`` and "no approaches" both arrive as an empty approach
        list; only the flag tells them apart, and grading the gap would
        manufacture a RED out of a parse failure.
        """
        failed = AirportApproaches(icao="EGKA", lookup_failed=True)
        ctx = _ctx(_conditions(FlightCategory.IFR, [_RWY_02, _RWY_20]), failed)
        assert _grade(ctx) == AdvisoryStatus.UNAVAILABLE

    def test_failed_lookup_is_unavailable_even_at_mvfr(self):
        failed = AirportApproaches(icao="EGKA", lookup_failed=True)
        ctx = _ctx(_conditions(FlightCategory.MVFR, [_RWY_24]), failed)
        assert _grade(ctx) == AdvisoryStatus.UNAVAILABLE


class TestCategoryGating:
    def test_vfr_is_always_green(self):
        """Visually reachable — approach infrastructure is irrelevant."""
        ctx = _ctx(
            _conditions(FlightCategory.VFR, [_RWY_24]),
            _approaches(has_procedure_data=False),
        )
        assert _grade(ctx) == AdvisoryStatus.GREEN

    def test_mvfr_with_any_approach_is_green(self):
        """Alignment is not a penalty when the landing can be completed visually."""
        ctx = _ctx(
            _conditions(FlightCategory.MVFR, [_RWY_02, _RWY_24]),
            _approaches(_ILS_02),
        )
        assert _grade(ctx) == AdvisoryStatus.GREEN

    def test_mvfr_without_approach_is_amber_never_red(self):
        ctx = _ctx(
            _conditions(FlightCategory.MVFR, [_RWY_24]),
            _approaches(has_procedure_data=False),
        )
        assert _grade(ctx) == AdvisoryStatus.AMBER

    def test_mvfr_ignores_a_misaligned_approach(self):
        """The EGKA shape at MVFR: wind favours 24, ILS serves 02 — still green."""
        ctx = _ctx(
            _conditions(FlightCategory.MVFR, [_RWY_02, _RWY_24]),
            _approaches(_ILS_02),
        )
        assert _grade(ctx) == AdvisoryStatus.GREEN


class TestFullLogic:
    def test_aligned_headwind_clear_ceiling_is_green(self):
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_20], ceiling_ft=900),
            _approaches(_ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.GREEN

    def test_ceiling_inside_the_estimated_minima_band_is_amber(self):
        """ILS proxy DH band is 200-300 ft: inside it the plate decides, not us.

        Visibility is held clear of its own band so the ceiling alone drives it.
        """
        ctx = _ctx(
            _conditions(
                FlightCategory.LIFR, [_RWY_02, _RWY_20],
                ceiling_ft=250, visibility_m=4000,
            ),
            _approaches(_ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.AMBER

    def test_ceiling_below_best_case_dh_is_red(self):
        ctx = _ctx(
            _conditions(
                FlightCategory.LIFR, [_RWY_02, _RWY_20],
                ceiling_ft=150, visibility_m=4000,
            ),
            _approaches(_ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.RED

    def test_visibility_inside_the_estimated_minima_band_is_amber(self):
        """ILS proxy visibility band is 550-1500 m."""
        ctx = _ctx(
            _conditions(FlightCategory.LIFR, [_RWY_20], ceiling_ft=900, visibility_m=900),
            _approaches(_ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.AMBER

    def test_visibility_below_best_case_minimum_is_red(self):
        ctx = _ctx(
            _conditions(FlightCategory.LIFR, [_RWY_20], ceiling_ft=900, visibility_m=300),
            _approaches(_ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.RED

    def test_absent_visibility_does_not_flag_the_axis(self):
        """ECMWF publishes no visibility — absence is skipped, not read as poor."""
        conds = _conditions(FlightCategory.IFR, [_RWY_20], ceiling_ft=900)
        for c in conds.arrival.conditions:
            c.visibility_m = None
        assert _grade(_ctx(conds, _approaches(_ILS_20))) == AdvisoryStatus.GREEN

    def test_wind_components_missing_never_drives_red(self):
        """A served end the wind model did not cover is our gap, not a fact."""
        conds = _conditions(FlightCategory.IFR, [], ceiling_ft=600)
        assert _grade(_ctx(conds, _approaches(_ILS_20))) == AdvisoryStatus.AMBER

    def test_tailwind_within_limit_on_the_served_end_is_amber(self):
        """A usable-but-compromised arrival: served end has an 8 kt tailwind."""
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_24], ceiling_ft=900),
            _approaches(_ILS_02),
        )
        assert _grade(ctx) == AdvisoryStatus.AMBER

    def test_tailwind_over_limit_with_circling_ceiling_is_amber(self):
        """Straight-in is out on wind, but the ceiling still supports circling."""
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_24], ceiling_ft=1200),
            _approaches(_ILS_02),
            # 8 kt tailwind on 02 exceeds a tightened 5 kt limit
        )
        assert _grade(ctx, tailwind_limit_kt=5) == AdvisoryStatus.AMBER

    def test_tailwind_over_limit_without_circling_ceiling_is_red(self):
        """The #509 RED case: misaligned + no circling ceiling + tailwind out."""
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_24], ceiling_ft=600),
            _approaches(_ILS_02),
        )
        assert _grade(ctx, tailwind_limit_kt=5) == AdvisoryStatus.RED

    def test_circling_only_approach_is_amber_never_graded(self):
        """No circling minima in the data — flag it, don't compute a verdict."""
        circling = RunwayApproach(name="RNP A", approach_type="RNP", circling=True)
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_24], ceiling_ft=1500),
            _approaches(circling),
        )
        assert _grade(ctx) == AdvisoryStatus.AMBER

    def test_unresolved_alignment_never_drives_red(self):
        """Estimate/data uncertainty may soften a grade, never harden it."""
        unresolved = RunwayApproach(name="MIPS: RNP (LNAV) ARINC CODING", approach_type="RNP")
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_24], ceiling_ft=600),
            _approaches(unresolved),
        )
        assert _grade(ctx, tailwind_limit_kt=5) == AdvisoryStatus.AMBER

    def test_crosswind_over_limit_on_the_served_end_is_amber(self):
        gusty = _runway_wind("20", 200.0, headwind=8.0, crosswind=25.0)
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [gusty], ceiling_ft=900),
            _approaches(_ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.AMBER

    def test_best_usable_end_wins_over_a_worse_served_end(self):
        """Two served ends: the grade comes from the better arrival, not the worse."""
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_20], ceiling_ft=900),
            _approaches(_ILS_02, _ILS_20),
        )
        assert _grade(ctx) == AdvisoryStatus.GREEN


class TestDetail:
    def test_detail_names_the_wind_runway_and_the_served_runway(self):
        ctx = _ctx(
            _conditions(FlightCategory.IFR, [_RWY_02, _RWY_24], ceiling_ft=600),
            _approaches(_ILS_02),
        )
        result = ApproachFeasibilityEvaluator.evaluate(
            ctx, {**_defaults(), "tailwind_limit_kt": 5},
        )
        detail = result.aggregate_detail
        assert "24" in detail and "02" in detail

    def test_detail_reports_the_arrival_category(self):
        ctx = _ctx(
            _conditions(FlightCategory.LIFR, [_RWY_20], ceiling_ft=900),
            _approaches(_ILS_20),
        )
        result = ApproachFeasibilityEvaluator.evaluate(ctx, _defaults())
        assert "LIFR" in result.aggregate_detail
        assert "EGKA" in result.aggregate_detail


class TestPerModel:
    def test_models_disagreeing_on_the_wind_aggregate_by_majority(self):
        """Two models see a usable straight-in, one does not — majority wins."""
        conds = _conditions(FlightCategory.IFR, [_RWY_02, _RWY_20], ceiling_ft=900)
        # Flip one model onto a heavy tailwind on the only served end.
        arr = conds.arrival
        arr.conditions[1] = AirportModelCondition(
            model=MODELS[1], flight_category=FlightCategory.IFR, ceiling_ft=600,
            visibility_sm=2.0,
            all_runways=[_runway_wind("20", 200.0, headwind=-20.0, crosswind=3.0)],
            best_runway=_runway_wind("02", 20.0, headwind=20.0, crosswind=3.0),
        )
        result = ApproachFeasibilityEvaluator.evaluate(_ctx(conds, _approaches(_ILS_20)), _defaults())
        statuses = {m.model: m.status for m in result.per_model}
        assert statuses[MODELS[0]] == AdvisoryStatus.GREEN
        assert statuses[MODELS[1]] == AdvisoryStatus.RED

    def test_model_without_conditions_is_unavailable_for_that_model(self):
        conds = _conditions(FlightCategory.IFR, [_RWY_20], ceiling_ft=900)
        conds.arrival.conditions = [conds.arrival.conditions[0]]
        result = ApproachFeasibilityEvaluator.evaluate(_ctx(conds, _approaches(_ILS_20)), _defaults())
        statuses = {m.model: m.status for m in result.per_model}
        assert statuses[MODELS[1]] == AdvisoryStatus.UNAVAILABLE


class TestCatalog:
    def test_registered_in_flight_rules_with_pilot_tier_limits(self):
        entry = ApproachFeasibilityEvaluator.catalog_entry()
        assert entry.category == "flight_rules"
        assert entry.default_enabled is True
        audiences = {p.key: p.audience for p in entry.parameters}
        assert audiences["tailwind_limit_kt"] == "pilot"
        assert audiences["circling_ceiling_ft"] == "advanced"

    @pytest.mark.parametrize("key", ["tailwind_limit_kt", "crosswind_limit_kt", "circling_ceiling_ft"])
    def test_params_are_honoured_on_recalc(self, key):
        """Every declared param must actually reach the grading code."""
        entry = ApproachFeasibilityEvaluator.catalog_entry()
        param = next(p for p in entry.parameters if p.key == key)
        assert param.min <= param.default <= param.max
