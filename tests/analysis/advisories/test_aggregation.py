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

    def test_all_unavailable_returns_green(self):
        statuses = [AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE]
        assert AdvisoryStatus.majority(statuses) == AdvisoryStatus.GREEN

    def test_empty_returns_green(self):
        assert AdvisoryStatus.majority([]) == AdvisoryStatus.GREEN


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

    def test_default_aggregation_is_worst(self):
        per_model = self._make_per_model([AdvisoryStatus.GREEN, AdvisoryStatus.GREEN, AdvisoryStatus.RED])
        result = RouteAdvisoryResult.from_per_model("test", per_model, {})
        assert result.aggregate_status == AdvisoryStatus.RED


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
