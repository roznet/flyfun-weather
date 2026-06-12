"""Tests for the advisory registry and framework."""

from __future__ import annotations

from weatherbrief.analysis.advisories import (
    RouteContext,
    evaluate_all,
    get_catalog,
    resolve_enabled_ids,
)
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


def test_resolve_enabled_ids_none_for_uncustomized():
    """No saved customization -> None (caller applies default_enabled to all)."""
    assert resolve_enabled_ids(None) is None
    assert resolve_enabled_ids({}) is None


def test_resolve_enabled_ids_absent_key_falls_back_to_default():
    """An advisory absent from the saved map uses its catalog default_enabled.

    This is the core fix: a profile customized before a new default-on advisory
    existed still gets that advisory, because the saved map is overrides — not an
    exhaustive allow-list. Mirrors the settings UI (`enabledMap[id] ?? default`).
    """
    catalog = {e.id: e for e in get_catalog()}
    # Find a default-on and a default-off advisory to anchor the assertions.
    default_on = next(e.id for e in catalog.values() if e.default_enabled)
    default_off = next(e.id for e in catalog.values() if not e.default_enabled)

    # Saved map mentions only one unrelated advisory; everything else is absent.
    resolved = resolve_enabled_ids({"icing_escape": True})

    # Absent default-on advisory is still evaluated...
    assert default_on in resolved
    # ...absent default-off advisory is still skipped.
    assert default_off not in resolved


def test_resolve_enabled_ids_honors_explicit_optout():
    """An explicit `id: false` overrides default_enabled and is excluded."""
    default_on = next(e.id for e in get_catalog() if e.default_enabled)
    resolved = resolve_enabled_ids({default_on: False})
    assert default_on not in resolved


def test_resolve_enabled_ids_honors_explicit_optin():
    """An explicit `id: true` includes a default-off advisory."""
    default_off = next(e.id for e in get_catalog() if not e.default_enabled)
    resolved = resolve_enabled_ids({default_off: True})
    assert default_off in resolved


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
