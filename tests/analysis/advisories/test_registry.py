"""Tests for the advisory registry and framework."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog
from weatherbrief.models import AdvisoryStatus


def test_get_catalog_returns_entries():
    """get_catalog returns entries for all registered evaluators."""
    catalog = get_catalog()
    assert len(catalog) >= 3  # at least the 3 core ones
    ids = {e.id for e in catalog}
    assert "icing_escape" in ids
    assert "vmc_cruise" in ids
    assert "turbulence" in ids


def test_evaluate_all_clear_sky(clear_context: RouteContext):
    """All advisories green for clear sky conditions."""
    results = evaluate_all(clear_context)
    assert len(results) > 0
    for r in results:
        assert r.aggregate_status in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE), \
            f"{r.advisory_id} unexpected status: {r.aggregate_status}"


def test_evaluate_all_respects_enabled_ids(clear_context: RouteContext):
    """Only evaluates advisories in the enabled set."""
    results = evaluate_all(clear_context, enabled_ids={"icing_escape"})
    assert len(results) == 1
    assert results[0].advisory_id == "icing_escape"


def test_evaluate_all_user_params(clear_context: RouteContext):
    """User parameter overrides are applied."""
    results = evaluate_all(
        clear_context,
        enabled_ids={"icing_escape"},
        user_params={"icing_escape": {"terrain_margin_ft": 2000}},
    )
    assert results[0].parameters_used["terrain_margin_ft"] == 2000


def test_catalog_entries_have_required_fields():
    """Every catalog entry has required metadata."""
    for entry in get_catalog():
        assert entry.id
        assert entry.name
        assert entry.short_description
        assert entry.description
        assert entry.category
        for param in entry.parameters:
            assert param.key
            assert param.label
            assert param.type
