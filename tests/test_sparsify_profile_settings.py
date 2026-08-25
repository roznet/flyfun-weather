"""Sparsify rule + byte-identical guarantee for the #405 profile backfill.

The migration (``079``) and the client (``profile-sparsify.ts``) share one rule:
persist only what differs from the default. These tests pin the rule against the
live catalog and, crucially, assert the backfill is a **no-op for grading** — the
resolved effective settings (what actually feeds the evaluator) are identical
before and after sparsifying, for a spread of prod-shaped profiles.

Fixtures are built from the known template seeds (``configs/system_profiles.json``)
and the documented legacy shapes — never real user data.
"""

from __future__ import annotations

import copy

from weatherbrief.analysis.advisories.engine_methods import (
    ENGINE_METHOD_DEFAULTS,
    legacy_cloud_source,
)
from weatherbrief.analysis.advisories.profile_sparsify import (
    build_param_defaults,
    sparsify_settings,
)
from weatherbrief.analysis.advisories.registry import get_catalog


def _param_defaults():
    return build_param_defaults(get_catalog())


def _resolve_effective(settings: dict, param_defaults) -> dict:
    """The grading-relevant projection of a profile: the merged advisory params
    (``{**catalog_defaults, **user}``), the resolved enable map is untouched by
    sparsify so it is excluded, and the three engine axes resolved through their
    defaults exactly as ``_resolve_analyses`` does. If this is identical before
    and after sparsify, no grade can move."""
    # Advisory params, catalog-merged per advisory.
    catalog_by_adv: dict[str, dict] = {}
    for (adv_id, key), dflt in param_defaults.items():
        catalog_by_adv.setdefault(adv_id, {})[key] = dflt
    user_params = (settings.get("advisories") or {}).get("params") or {}
    merged = {}
    for adv_id, defaults in catalog_by_adv.items():
        merged[adv_id] = {**defaults, **user_params.get(adv_id, {})}

    # Engine axes: absence → default. Fixtures still carry the pre-#410 fused
    # ``cloud_method`` to exercise the sweep, so the "before" state is resolved
    # the same way the sweep does — new key wins, else reduce the legacy form.
    return {
        "params": merged,
        "icing_method": settings.get("icing_method")
        or ENGINE_METHOD_DEFAULTS["icing_method"],
        "cloud_source": settings.get("cloud_source")
        or legacy_cloud_source(settings.get("cloud_method"))
        or ENGINE_METHOD_DEFAULTS["cloud_source"],
        "convective_method": settings.get("convective_method")
        or ENGINE_METHOD_DEFAULTS["convective_method"],
    }


# --- prod-shaped fixtures -------------------------------------------------

def _vfr_only_dense():
    """A settings-page Save densified this vfr_only profile: every engine method
    and a full slab of advisory params frozen at the default. The two real
    template overrides (vmc_cruise / vfr_feasibility) must survive."""
    return {
        "cruise_altitude_ft": 5500,
        "flight_rules": "vfr_only",
        # engine methods frozen at the default by the dense save → all prunable
        "icing_method": "ogimet_nwp",
        "cloud_source": "nwp",
        "convective_method": "nwp",
        "advisories": {
            "enabled": {"fronts": False, "ifr_feasibility": False},
            "params": {
                # real template overrides — differ from catalog default → KEEP
                "vmc_cruise": {"extent_pct_amber": 5, "extent_pct_red": 10},
                "vfr_feasibility": {"extent_pct_amber": 5, "extent_pct_red": 10},
            },
            "aggregation": "majority",
        },
    }


def test_enable_flags_are_never_touched():
    pd = _param_defaults()
    settings = {
        "advisories": {"enabled": {"fronts": False, "convective": True}, "params": {}}
    }
    out, stats = sparsify_settings(settings, pd)
    assert out["advisories"]["enabled"] == {"fronts": False, "convective": True}
    assert stats.params_deleted == 0


def test_param_equal_to_default_is_pruned_override_is_kept():
    pd = _param_defaults()
    # Find a real (advisory, key, default) from the live catalog to build on.
    (adv_id, key), dflt = next(iter(pd.items()))
    settings = {
        "advisories": {
            "params": {
                adv_id: {key: dflt},  # equals default → prune
            }
        }
    }
    out, stats = sparsify_settings(settings, pd)
    assert stats.params_deleted == 1
    assert adv_id not in out["advisories"]["params"]

    # A value one step off the default is an override → kept untouched.
    off = (dflt + 1) if isinstance(dflt, (int, float)) else dflt
    settings2 = {"advisories": {"params": {adv_id: {key: off}}}}
    out2, stats2 = sparsify_settings(settings2, pd)
    assert stats2.params_deleted == 0
    assert out2["advisories"]["params"][adv_id][key] == off


def test_unknown_param_key_is_never_deleted():
    pd = _param_defaults()
    settings = {"advisories": {"params": {"made_up_adv": {"mystery": 42}}}}
    out, stats = sparsify_settings(settings, pd)
    assert stats.params_deleted == 0
    assert out["advisories"]["params"]["made_up_adv"]["mystery"] == 42


def test_engine_methods_equal_to_default_pruned():
    pd = _param_defaults()
    settings = dict(ENGINE_METHOD_DEFAULTS)  # all three at default
    out, stats = sparsify_settings(settings, pd)
    assert stats.engine_deleted == 3
    assert "icing_method" not in out
    assert "cloud_source" not in out
    assert "convective_method" not in out


def test_engine_override_kept():
    pd = _param_defaults()
    settings = {
        "icing_method": "ogimet_dd",  # override
        "cloud_source": "dd",  # override
        "convective_method": "thermo",  # override
    }
    out, stats = sparsify_settings(settings, pd)
    assert stats.engine_deleted == 0
    assert out == settings


def test_legacy_cloud_method_nwp_is_dropped():
    pd = _param_defaults()
    out, stats = sparsify_settings({"cloud_method": "soft_nwp"}, pd)
    assert "cloud_method" not in out
    assert "cloud_source" not in out  # absence → nwp default
    assert stats.cloud_method_dropped == 1
    assert stats.cloud_method_rewritten == 0


def test_legacy_cloud_method_dd_is_rewritten_to_cloud_source():
    pd = _param_defaults()
    out, stats = sparsify_settings({"cloud_method": "square_dd"}, pd)
    assert "cloud_method" not in out
    assert out["cloud_source"] == "dd"  # override preserved
    assert stats.cloud_method_rewritten == 1
    assert stats.cloud_method_dropped == 0


def test_legacy_cloud_method_redundant_with_cloud_source_is_dropped():
    pd = _param_defaults()
    out, stats = sparsify_settings(
        {"cloud_source": "dd", "cloud_method": "square_nwp"}, pd
    )
    assert "cloud_method" not in out
    assert out["cloud_source"] == "dd"  # new key is authoritative
    assert stats.cloud_method_dropped == 1


def test_input_is_not_mutated():
    pd = _param_defaults()
    settings = _vfr_only_dense()
    before = copy.deepcopy(settings)
    sparsify_settings(settings, pd)
    assert settings == before


def test_byte_identical_resolved_settings_over_prod_shapes():
    """The core no-op guarantee: resolving the effective grading settings yields
    an identical projection before and after sparsify, across a spread of
    prod-shaped profiles."""
    pd = _param_defaults()
    fixtures = [
        _vfr_only_dense(),
        dict(ENGINE_METHOD_DEFAULTS),
        {"icing_method": "ogimet_dd", "cloud_source": "dd", "convective_method": "thermo"},
        {"cloud_method": "soft_nwp", "cruise_altitude_ft": 5500},
        {"cloud_method": "square_dd", "cruise_altitude_ft": 5500},
        {"cloud_source": "dd", "cloud_method": "natural_nwp"},
        {},  # a born-sparse profile — must be a strict no-op
    ]
    for settings in fixtures:
        before = _resolve_effective(settings, pd)
        sparsified, _ = sparsify_settings(settings, pd)
        after = _resolve_effective(sparsified, pd)
        assert before == after, f"grading projection moved for {settings!r}"


def test_born_sparse_profile_is_untouched():
    pd = _param_defaults()
    out, stats = sparsify_settings({}, pd)
    assert out == {}
    assert not stats.touched
