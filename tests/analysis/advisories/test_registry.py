"""Tests for the advisory registry and framework."""

from __future__ import annotations

from weatherbrief.analysis.advisories import (
    RouteContext,
    evaluate_all,
    get_catalog,
    get_category_order,
    resolve_enabled_ids,
)
from weatherbrief.analysis.advisories.registry import CATEGORY_ORDER
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


# --- Slice 1: audience metadata + catalog ordering (#387) ---


def test_every_param_has_valid_audience():
    """Every catalog param declares one of the two allowed audience tiers."""
    for entry in get_catalog():
        for param in entry.parameters:
            assert param.audience in ("pilot", "advanced"), (
                f"{entry.id}.{param.key} has invalid audience {param.audience!r}"
            )


def test_pilot_tier_param_count_bounded():
    """Pilot-tier params stay a curated few so the compact page stays compact.

    The bound (<= 25) is intentionally slack above the ~17 tagged today — it is
    a guardrail against the tier silently ballooning, not an exact count.
    """
    pilot = [
        (entry.id, param.key)
        for entry in get_catalog()
        for param in entry.parameters
        if param.audience == "pilot"
    ]
    assert len(pilot) <= 25, f"too many pilot-tier params ({len(pilot)}): {pilot}"
    # And at least the intended set exists (guards against a mass-untag regression).
    assert len(pilot) >= 15


def test_expected_pilot_tier_params_are_tagged():
    """The 17 designed pilot-tier params are tagged (the mechanism's anchor set)."""
    expected = {
        ("airport_wind", "crosswind_green_kt"),
        ("airport_wind", "crosswind_red_kt"),
        ("airport_wind", "gust_green_kt"),
        ("airport_wind", "gust_red_kt"),
        ("flight_category", "amber_ceiling_ft"),
        ("flight_category", "amber_vis_sm"),
        ("flight_category", "red_ceiling_ft"),
        ("flight_category", "red_vis_sm"),
        ("density_altitude", "da_amber_ft"),
        ("density_altitude", "da_red_ft"),
        ("cloud_top", "margin_ft"),
        ("headwind", "mean_amber_kt"),
        ("vfr_feasibility", "cloud_clearance_ft"),
        ("ifr_feasibility", "min_dep_ceiling_ft"),
        ("ifr_feasibility", "min_arr_ceiling_ft"),
        ("sun", "warn_near_sunset"),
        ("sun", "sunset_margin_min"),
    }
    tagged = {
        (entry.id, param.key)
        for entry in get_catalog()
        for param in entry.parameters
        if param.audience == "pilot"
    }
    assert expected <= tagged, f"missing pilot tags: {expected - tagged}"


def test_conv_radius_stays_advanced():
    """A param explicitly kept advanced in the design is not mis-tagged pilot."""
    by_id = {e.id: e for e in get_catalog()}
    conv_radius = next(
        p for p in by_id["flight_category"].parameters if p.key == "conv_radius_nm"
    )
    assert conv_radius.audience == "advanced"


def test_every_category_in_category_order():
    """Every category present in the catalog appears in CATEGORY_ORDER."""
    present = {e.category for e in get_catalog()}
    missing = present - set(CATEGORY_ORDER)
    assert not missing, f"categories missing from CATEGORY_ORDER: {missing}"


def test_catalog_is_ordered_by_category():
    """Catalog entries are grouped and ordered per CATEGORY_ORDER.

    Entries appear in non-decreasing category-order index — i.e. all entries of
    an earlier category come before any entry of a later one.
    """
    rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    indices = [rank[e.category] for e in get_catalog()]
    assert indices == sorted(indices), (
        f"catalog not grouped by CATEGORY_ORDER: {[e.id for e in get_catalog()]}"
    )


def test_catalog_order_is_deterministic():
    """Repeated calls return the same order (no dict/set nondeterminism)."""
    first = [e.id for e in get_catalog()]
    second = [e.id for e in get_catalog()]
    assert first == second


def test_within_category_entry_order_honored():
    """Airport advisories follow the declared within-category order."""
    airport_ids = [e.id for e in get_catalog() if e.category == "airport"]
    assert airport_ids == [
        "flight_category",
        "airport_wind",
        "density_altitude",
        "llws",
    ]


def test_get_category_order_matches_present_categories():
    """The served category list covers exactly the catalog's categories, ordered."""
    cats = get_category_order()
    keys = [c["key"] for c in cats]
    present = {e.category for e in get_catalog()}
    assert set(keys) == present
    # Ordered per CATEGORY_ORDER.
    rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    assert keys == sorted(keys, key=lambda k: rank.get(k, len(CATEGORY_ORDER)))
    # Diagnostics flag is set only for the model category.
    diag = {c["key"] for c in cats if c["diagnostics"]}
    assert diag == {"model"}
