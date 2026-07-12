"""Tests for advisory aggregation modes (worst vs majority)."""

from __future__ import annotations

import pytest

from weatherbrief.analysis.advisories import RouteContext, evaluate_all
from weatherbrief.models import AdvisoryAggregation, AdvisoryStatus, ModelAdvisoryResult, RouteAdvisoryResult


# ---------------------------------------------------------------------------
# AdvisoryStatus.majority() unit tests
# ---------------------------------------------------------------------------

class TestAdvisoryStatusMajority:
    """Unit tests for AdvisoryStatus.majority()."""

    def test_clear_majority_green(self):
        statuses = [AdvisoryStatus.GREEN, AdvisoryStatus.GREEN, AdvisoryStatus.AMBER]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.GREEN

    def test_clear_majority_red(self):
        statuses = [AdvisoryStatus.RED, AdvisoryStatus.RED, AdvisoryStatus.GREEN]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.RED

    def test_tie_broken_by_worst(self):
        """2 AMBER + 2 GREEN + 1 RED → tie between AMBER and GREEN → worst = AMBER."""
        statuses = [
            AdvisoryStatus.AMBER, AdvisoryStatus.AMBER,
            AdvisoryStatus.GREEN, AdvisoryStatus.GREEN,
            AdvisoryStatus.RED,
        ]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.AMBER

    def test_tie_all_equal_count(self):
        """1 GREEN + 1 AMBER + 1 RED → three-way tie → worst = RED."""
        statuses = [AdvisoryStatus.GREEN, AdvisoryStatus.AMBER, AdvisoryStatus.RED]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.RED

    def test_single_model(self):
        assert AdvisoryStatus.majority([AdvisoryStatus.AMBER]) == AdvisoryStatus.AMBER

    def test_all_same(self):
        statuses = [AdvisoryStatus.GREEN] * 5
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.GREEN

    def test_unavailable_ignored(self):
        statuses = [
            AdvisoryStatus.GREEN,
            AdvisoryStatus.UNAVAILABLE,
            AdvisoryStatus.UNAVAILABLE,
        ]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.GREEN

    def test_all_unavailable_returns_unavailable(self):
        """Every model failed to assess → UNAVAILABLE. Missing is not clear."""
        statuses = [AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.UNAVAILABLE

    def test_empty_returns_unavailable(self):
        """Nothing to grade → UNAVAILABLE. An evaluator that hands us no models
        (e.g. no airport domain) has not established that conditions are fine."""
        assert AdvisoryStatus.majority([]) == AdvisoryStatus.UNAVAILABLE


class TestAdvisoryStatusWorst:
    """Unit tests for AdvisoryStatus.worst()."""

    def test_picks_most_severe(self):
        statuses = [AdvisoryStatus.GREEN, AdvisoryStatus.RED, AdvisoryStatus.AMBER]
        assert AdvisoryStatus.worst(statuses) == AdvisoryStatus.RED

    def test_unavailable_ignored_when_a_valid_status_exists(self):
        statuses = [AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.AMBER]
        assert AdvisoryStatus.worst(statuses) == AdvisoryStatus.AMBER

    def test_all_unavailable_returns_unavailable(self):
        statuses = [AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE]
        assert AdvisoryStatus.worst(statuses) == AdvisoryStatus.UNAVAILABLE

    def test_empty_returns_unavailable(self):
        assert AdvisoryStatus.worst([]) == AdvisoryStatus.UNAVAILABLE


class TestMissingDataNeverGradesGreen:
    """The advisory-level half of "missing data must never read as clear".

    The airport evaluators (flight_category, airport_wind, density_altitude,
    llws) return ``from_per_model(id, [], params)`` when their domain is
    absent. That empty list must not aggregate to GREEN.
    """

    def test_no_models_at_all_is_unavailable(self):
        """An evaluator with no airport domain hands us an empty per_model list."""
        result = RouteAdvisoryResult.from_per_model("airport_wind", [], {})
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    @pytest.mark.parametrize(
        "aggregation",
        [AdvisoryAggregation.MAJORITY, AdvisoryAggregation.WORST],
    )
    def test_every_model_unavailable_is_unavailable(self, aggregation):
        per_model = [
            ModelAdvisoryResult(
                model=m, status=AdvisoryStatus.UNAVAILABLE, detail="no data available"
            )
            for m in ("gfs", "icon_eu", "ecmwf")
        ]
        result = RouteAdvisoryResult.from_per_model(
            "turbulence", per_model, {}, aggregation=aggregation
        )
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_one_valid_model_still_grades(self):
        """A single assessable model is enough — we don't blank the advisory."""
        per_model = [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.UNAVAILABLE, detail=""),
            ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.GREEN, detail="smooth"),
        ]
        result = RouteAdvisoryResult.from_per_model("turbulence", per_model, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN


# ---------------------------------------------------------------------------
# RouteAdvisoryResult.from_per_model() with aggregation param
# ---------------------------------------------------------------------------

class TestFromPerModelAggregation:
    """Test that from_per_model respects the aggregation parameter."""

    @staticmethod
    def _make_per_model(statuses: list[AdvisoryStatus]) -> list[ModelAdvisoryResult]:
        return [
            ModelAdvisoryResult(model=f"model_{i}", status=s, detail=f"detail_{s.value}")
            for i, s in enumerate(statuses)
        ]

    def test_worst_mode_picks_red(self):
        per_model = self._make_per_model([AdvisoryStatus.GREEN, AdvisoryStatus.GREEN, AdvisoryStatus.RED])
        result = RouteAdvisoryResult.from_per_model(
            "test", per_model, {}, aggregation=AdvisoryAggregation.WORST,
        )
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_majority_mode_picks_green(self):
        """2 GREEN + 1 RED → majority = GREEN (differs from worst = RED)."""
        per_model = self._make_per_model([AdvisoryStatus.GREEN, AdvisoryStatus.GREEN, AdvisoryStatus.RED])
        result = RouteAdvisoryResult.from_per_model(
            "test", per_model, {}, aggregation=AdvisoryAggregation.MAJORITY,
        )
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_majority_detail_from_representative(self):
        per_model = self._make_per_model([AdvisoryStatus.AMBER, AdvisoryStatus.AMBER, AdvisoryStatus.RED])
        result = RouteAdvisoryResult.from_per_model(
            "test", per_model, {}, aggregation=AdvisoryAggregation.MAJORITY,
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "amber" in result.aggregate_detail

    def test_default_aggregation_is_majority(self):
        per_model = self._make_per_model([AdvisoryStatus.GREEN, AdvisoryStatus.GREEN, AdvisoryStatus.RED])
        result = RouteAdvisoryResult.from_per_model("test", per_model, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN


# ---------------------------------------------------------------------------
# evaluate_all() with aggregation parameter
# ---------------------------------------------------------------------------

class TestEvaluateAllAggregation:
    """Test that evaluate_all threads aggregation correctly."""

    def test_majority_mode_clear_sky(self, clear_context: RouteContext):
        """Clear sky → all GREEN regardless of aggregation mode."""
        results = evaluate_all(clear_context, aggregation=AdvisoryAggregation.MAJORITY)
        assert len(results) > 0
        for r in results:
            assert r.aggregate_status in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE)

    def test_worst_is_default(self, clear_context: RouteContext):
        """Default aggregation should be WORST (backward compatible)."""
        results_default = evaluate_all(clear_context)
        results_worst = evaluate_all(clear_context, aggregation=AdvisoryAggregation.WORST)
        assert len(results_default) == len(results_worst)
        for rd, rw in zip(results_default, results_worst):
            assert rd.aggregate_status == rw.aggregate_status

    def test_worst_preference_overrides_evaluator_majority_default(self, clear_context):
        """Regression: a WORST preference must re-aggregate even though evaluators
        build their aggregate with ``from_per_model``'s MAJORITY default.

        Previously the registry skipped re-aggregation whenever the requested
        mode was WORST, silently keeping a majority-built aggregate — so a
        divergent advisory (2 GREEN + 1 RED) read GREEN under a WORST preference.
        """
        from weatherbrief.analysis.advisories import registry
        from weatherbrief.models import AdvisoryCatalogEntry

        class _DivergentEvaluator:
            @classmethod
            def catalog_entry(cls):
                return AdvisoryCatalogEntry(
                    id="_test_divergent", name="Test Divergent",
                    short_description="", description="", category="model",
                )

            @staticmethod
            def evaluate(ctx, params):
                # 2 GREEN + 1 RED, aggregated with the evaluator default (MAJORITY
                # → GREEN), exactly like the real evaluators.
                per_model = [
                    ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.GREEN, detail="g"),
                    ModelAdvisoryResult(model="icon", status=AdvisoryStatus.GREEN, detail="g"),
                    ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.RED, detail="r"),
                ]
                return RouteAdvisoryResult.from_per_model("_test_divergent", per_model, {})

        registry._ensure_loaded()
        registry._EVALUATORS["_test_divergent"] = _DivergentEvaluator
        try:
            worst = evaluate_all(
                clear_context, enabled_ids={"_test_divergent"},
                aggregation=AdvisoryAggregation.WORST,
            )
            majority = evaluate_all(
                clear_context, enabled_ids={"_test_divergent"},
                aggregation=AdvisoryAggregation.MAJORITY,
            )
        finally:
            registry._EVALUATORS.pop("_test_divergent", None)

        assert worst[0].aggregate_status == AdvisoryStatus.RED
        assert majority[0].aggregate_status == AdvisoryStatus.GREEN

    def test_reaggregation_preserves_custom_detail_and_unavailable(self, clear_context):
        """Re-aggregation must not clobber an evaluator's synthesized aggregate
        detail (convective), nor resurrect an explicit all-UNAVAILABLE result
        (fronts) into GREEN.
        """
        from weatherbrief.analysis.advisories import registry
        from weatherbrief.models import AdvisoryCatalogEntry

        class _CustomDetailEvaluator:
            @classmethod
            def catalog_entry(cls):
                return AdvisoryCatalogEntry(
                    id="_test_custom_detail", name="Custom", short_description="",
                    description="", category="model",
                )

            @staticmethod
            def evaluate(ctx, params):
                per_model = [
                    ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.AMBER, detail="raw gfs"),
                    ModelAdvisoryResult(model="icon", status=AdvisoryStatus.AMBER, detail="raw icon"),
                ]
                r = RouteAdvisoryResult.from_per_model("_test_custom_detail", per_model, {})
                r.aggregate_detail = "SYNTHESIZED cross-model summary"  # convective-style override
                return r

        class _UnavailableEvaluator:
            @classmethod
            def catalog_entry(cls):
                return AdvisoryCatalogEntry(
                    id="_test_unavailable", name="Unavail", short_description="",
                    description="", category="model",
                )

            @staticmethod
            def evaluate(ctx, params):
                # fronts-style: explicit UNAVAILABLE that must stay hidden, not
                # collapse to GREEN when re-aggregated.
                return RouteAdvisoryResult(
                    advisory_id="_test_unavailable",
                    aggregate_status=AdvisoryStatus.UNAVAILABLE,
                    aggregate_detail="no data",
                    per_model=[ModelAdvisoryResult(
                        model="all", status=AdvisoryStatus.UNAVAILABLE, detail="no data")],
                    parameters_used={},
                )

        registry._ensure_loaded()
        registry._EVALUATORS["_test_custom_detail"] = _CustomDetailEvaluator
        registry._EVALUATORS["_test_unavailable"] = _UnavailableEvaluator
        try:
            for mode in (AdvisoryAggregation.MAJORITY, AdvisoryAggregation.WORST):
                cd = evaluate_all(clear_context, enabled_ids={"_test_custom_detail"}, aggregation=mode)
                un = evaluate_all(clear_context, enabled_ids={"_test_unavailable"}, aggregation=mode)
                # Custom detail survives under the default (majority); the
                # all-UNAVAILABLE result stays UNAVAILABLE under BOTH modes.
                if mode == AdvisoryAggregation.MAJORITY:
                    assert cd[0].aggregate_detail == "SYNTHESIZED cross-model summary"
                assert un[0].aggregate_status == AdvisoryStatus.UNAVAILABLE
        finally:
            registry._EVALUATORS.pop("_test_custom_detail", None)
            registry._EVALUATORS.pop("_test_unavailable", None)


class TestAffectedNmConsistency:
    """affected_nm stays consistent with affected_pct / affected_points (#391 review).

    A ribbon-derived affected_nm was reverted because the ribbon's amber/red
    membership does not match ``affected`` for every evaluator, which produced a
    result object whose affected_nm contradicted its affected_pct. affected_nm is
    the nm form of the same ``affected`` count and must track it.
    """

    def test_affected_nm_tracks_affected_count(self):
        """affected_nm and affected_pct both key off ``affected`` — no divergence.

        Even when a highlights ribbon is present with a *different* flagged span,
        affected_nm follows the count (2/5 of 200nm = 80nm), matching
        affected_pct (40%). This is the consistency the ribbon-derived version
        broke.
        """
        from weatherbrief.models import (
            AdvisoryHighlights,
            HighlightSeverity,
            RibbonSegment,
        )

        ribbon = [
            RibbonSegment(dist_from_nm=0.0, dist_to_nm=15.0, severity=HighlightSeverity.RED),
            RibbonSegment(dist_from_nm=15.0, dist_to_nm=200.0, severity=HighlightSeverity.GREEN),
        ]
        result = ModelAdvisoryResult.build(
            model="gfs", status=AdvisoryStatus.AMBER, detail="",
            affected=2, total=5, total_distance_nm=200,
            highlights=AdvisoryHighlights(ribbon=ribbon),
        )
        assert result.affected_nm == 80.0
        assert result.affected_pct == 40.0
        # nm / total_nm and pct/100 describe the same fraction.
        assert result.affected_nm / result.total_nm == result.affected_pct / 100

    def test_no_ribbon_same_proportion(self):
        result = ModelAdvisoryResult.build(
            model="gfs", status=AdvisoryStatus.AMBER, detail="",
            affected=2, total=5, total_distance_nm=200,
        )
        assert result.affected_nm == 80.0
