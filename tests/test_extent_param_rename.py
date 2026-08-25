"""Key-rename rule + lossless guarantee for the #571 Stage 3 consolidation.

Migration ``093``, the server helper
:mod:`weatherbrief.analysis.advisories.extent_param_migration` and the client's
``renameExtentParams`` share one rule: rewrite each pre-consolidation extent key
to ``extent_pct_amber`` / ``extent_pct_red``, flipping ``fiki_icing``'s inverted
polarity as it goes.

The load-bearing test is :class:`TestLossless`: a profile carrying *every* old
key must resolve byte-identically before and after, because a rename that quietly
reverted a pilot's tuning to a default is exactly the failure the active rewrite
exists to prevent. ``profile_sparsify`` cannot do this job — it deliberately
keeps any key it cannot prove is a default, so a renamed-away key would linger
forever, doing nothing, while the pilot believed it was live.
"""

from __future__ import annotations

import copy

from weatherbrief.analysis.advisories.extent_param_migration import (
    EXTENT_KEY_RENAMES,
    INVERTED_PCT_KEYS,
    has_legacy_extent_keys,
    rename_extent_params,
)
from weatherbrief.analysis.advisories.registry import get_catalog


def _catalog_keys() -> dict[str, set[str]]:
    return {e.id: {p.key for p in e.parameters} for e in get_catalog()}


class TestRenameRule:
    def test_every_new_key_exists_in_the_live_catalog(self):
        """The map may not rename a key to something no advisory declares.

        Read live (never snapshotted), so a key that moves after this merges
        fails here rather than silently writing a dead key into every profile.
        """
        catalog = _catalog_keys()
        for adv_id, renames in EXTENT_KEY_RENAMES.items():
            assert adv_id in catalog, adv_id
            for old, new in renames.items():
                assert new in catalog[adv_id], f"{adv_id}:{old} -> {new}"

    def test_no_old_key_survives_in_the_catalog(self):
        """A renamed key must be gone from the catalog, or the rename is a lie."""
        catalog = _catalog_keys()
        for adv_id, renames in EXTENT_KEY_RENAMES.items():
            for old in renames:
                assert old not in catalog[adv_id], f"{adv_id}:{old} still declared"

    def test_renames_and_prunes_the_old_key(self):
        out, stats = rename_extent_params(
            {"advisories": {"params": {"vmc_cruise": {"bkn_pct_amber": 30}}}}
        )
        assert out["advisories"]["params"]["vmc_cruise"] == {"extent_pct_amber": 30}
        assert stats.renamed == 1
        assert not has_legacy_extent_keys(out)

    def test_fiki_polarity_flips_with_the_name(self):
        # "amber below 70% clear" means "amber at or above 30% affected".
        out, stats = rename_extent_params(
            {"advisories": {"params": {"fiki_icing": {
                "clear_cruise_amber_pct": 70, "clear_cruise_red_pct": 40,
            }}}}
        )
        assert out["advisories"]["params"]["fiki_icing"] == {
            "extent_pct_amber": 30.0, "extent_pct_red": 60.0,
        }
        assert stats.inverted == 2

    def test_secondary_alias_only_applies_when_the_primary_is_absent(self):
        """icing_escape's read path preferred the primary and fell back to the
        alias; the rename must resolve the same way or a profile carrying both
        would grade differently after migrating."""
        out, _ = rename_extent_params(
            {"advisories": {"params": {"icing_escape": {
                "icing_coverage_pct_amber": 25, "route_pct_amber": 99,
            }}}}
        )
        assert out["advisories"]["params"]["icing_escape"] == {
            "extent_pct_amber": 25,
        }

    def test_secondary_alias_is_used_when_it_is_all_there_is(self):
        out, _ = rename_extent_params(
            {"advisories": {"params": {"icing_escape": {"min_route_pct": 8}}}}
        )
        assert out["advisories"]["params"]["icing_escape"] == {"extent_pct_red": 8}

    def test_a_value_already_under_the_new_key_wins(self):
        out, stats = rename_extent_params(
            {"advisories": {"params": {"turbulence": {
                "extent_pct_amber": 40, "route_pct_amber": 15,
            }}}}
        )
        assert out["advisories"]["params"]["turbulence"] == {"extent_pct_amber": 40}
        assert stats.dropped_shadowed == 1

    def test_unrelated_keys_and_enable_flags_are_untouched(self):
        before = {
            "icing_method": "sfip",
            "advisories": {
                "enabled": {"fronts": False},
                "params": {
                    "airport_wind": {"crosswind_red_kt": 25},
                    "enroute_precip": {"snow_pct_amber": 8, "rain_pct_amber": 40},
                },
            },
        }
        snapshot = copy.deepcopy(before)
        out, _ = rename_extent_params(before)
        assert before == snapshot                      # pure
        assert out["icing_method"] == "sfip"
        assert out["advisories"]["enabled"] == {"fronts": False}
        assert out["advisories"]["params"]["airport_wind"] == {"crosswind_red_kt": 25}
        assert out["advisories"]["params"]["enroute_precip"] == {
            "extent_pct_amber": 8, "rain_pct_amber": 40,
        }

    def test_is_idempotent(self):
        once, _ = rename_extent_params(
            {"advisories": {"params": {"vfr_feasibility": {"imc_pct_amber": 10}}}}
        )
        twice, stats = rename_extent_params(once)
        assert twice == once
        assert not stats.touched

    def test_handles_a_profile_with_no_advisory_params(self):
        for settings in ({}, {"advisories": {}}, {"advisories": {"params": None}}):
            out, stats = rename_extent_params(settings)
            assert out == settings
            assert not stats.touched


class TestLossless:
    """Resolved settings must be identical before and after, for a profile
    carrying every old key."""

    def _dense_legacy_profile(self) -> dict:
        """One profile with every pre-consolidation key set to a NON-default
        value, so a rename that dropped or defaulted anything shows up."""
        params: dict[str, dict[str, float]] = {}
        value = 7.0
        for adv_id, renames in EXTENT_KEY_RENAMES.items():
            inverted = INVERTED_PCT_KEYS.get(adv_id, set())
            secondary = {"route_pct_amber", "min_route_pct"}
            for old in renames:
                if adv_id == "icing_escape" and old in secondary:
                    continue  # exercised separately; both would shadow
                params.setdefault(adv_id, {})[old] = (
                    100.0 - value if old in inverted else value
                )
                value = value + 1.0 if value < 40 else 7.0
        return {"advisories": {"params": params}}

    def _resolve(self, settings: dict) -> dict:
        """The grading-relevant projection: catalog defaults merged with the
        stored params, per advisory. If this is identical before and after, no
        grade can move — which is what makes the rename safe to ship without a
        pack replay."""
        catalog = {e.id: {p.key: p.default for p in e.parameters} for e in get_catalog()}
        user = (settings.get("advisories") or {}).get("params") or {}
        return {
            adv_id: {**defaults, **user.get(adv_id, {})}
            for adv_id, defaults in catalog.items()
        }

    def test_every_old_key_lands_on_a_declared_key(self):
        before = self._dense_legacy_profile()
        after, stats = rename_extent_params(before)
        assert stats.renamed == sum(
            len(v) for v in before["advisories"]["params"].values()
        )
        assert not has_legacy_extent_keys(after)
        catalog = _catalog_keys()
        for adv_id, params in after["advisories"]["params"].items():
            for key in params:
                assert key in catalog[adv_id], f"{adv_id}:{key} is not a catalog key"

    def test_no_stored_value_is_lost(self):
        before = self._dense_legacy_profile()
        after, _ = rename_extent_params(before)
        for adv_id, params in before["advisories"]["params"].items():
            inverted = INVERTED_PCT_KEYS.get(adv_id, set())
            for old, val in params.items():
                new = EXTENT_KEY_RENAMES[adv_id][old]
                expected = 100.0 - val if old in inverted else val
                assert after["advisories"]["params"][adv_id][new] == expected

    def test_resolved_settings_carry_the_same_overrides(self):
        """Byte-identical apart from the deliberate key rename: for every
        advisory, the resolved value under the NEW key after the migration is
        the value the OLD key resolved to before it."""
        before = self._dense_legacy_profile()
        after, _ = rename_extent_params(before)
        res_before = self._resolve(before)
        res_after = self._resolve(after)

        for adv_id, renames in EXTENT_KEY_RENAMES.items():
            stored = before["advisories"]["params"].get(adv_id, {})
            inverted = INVERTED_PCT_KEYS.get(adv_id, set())
            for old, new in renames.items():
                if old not in stored:
                    continue
                old_value = stored[old]
                expected = 100.0 - old_value if old in inverted else old_value
                assert res_after[adv_id][new] == expected
            # Every OTHER parameter of the advisory resolves unchanged.
            touched = set(renames.values())
            for key, val in res_before[adv_id].items():
                if key in touched or key in renames:
                    continue
                assert res_after[adv_id][key] == val


class TestDowngrade:
    """The reverse direction, exercised directly (#571 review).

    ``downgrade()`` is the highest-scrutiny path in migration 093 — it rewrites
    live profile data and un-inverts ``fiki_icing``'s stored value — and the
    round-trip tests above only prove ``upgrade()``. These drive the same
    reversal logic the migration runs, so a change to the rename map that breaks
    the way back is caught here rather than in production.
    """

    def _downgrade(self, params: dict) -> dict:
        """The real reversal migration 093 runs — imported, not re-implemented.

        The earlier version of this helper hand-copied the reverse logic, so it
        carried the same missing non-numeric guard the migration did and could
        not have caught it (#571 review).
        """
        from weatherbrief.analysis.advisories.extent_param_migration import (
            revert_extent_params,
        )

        out, _ = revert_extent_params({"advisories": {"params": params}})
        return out["advisories"]["params"]

    def test_restores_the_original_keys(self):
        before = {"vmc_cruise": {"bkn_pct_amber": 35, "ovc_pct_red": 60}}
        migrated, _ = rename_extent_params({"advisories": {"params": before}})
        restored = self._downgrade(migrated["advisories"]["params"])
        assert restored == before

    def test_un_inverts_fiki_so_the_value_round_trips(self):
        before = {"fiki_icing": {"clear_cruise_amber_pct": 70}}
        migrated, _ = rename_extent_params({"advisories": {"params": before}})
        assert migrated["advisories"]["params"]["fiki_icing"] == {
            "extent_pct_amber": 30.0
        }
        restored = self._downgrade(migrated["advisories"]["params"])
        assert restored == {"fiki_icing": {"clear_cruise_amber_pct": 70.0}}

    def test_secondary_alias_comes_back_under_the_primary_name(self):
        """Documented edge: two old names map onto one new one, so only the
        primary can be restored. The VALUE must still round-trip exactly."""
        before = {"icing_escape": {"min_route_pct": 8}}
        migrated, _ = rename_extent_params({"advisories": {"params": before}})
        restored = self._downgrade(migrated["advisories"]["params"])
        assert restored == {"icing_escape": {"no_escape_pct_red": 8}}

    def test_turbulence_is_not_treated_as_an_alias(self):
        """`route_pct_amber` is a secondary alias for icing_escape and the
        PRIMARY (only) old name for turbulence. A bare key-name check would skip
        turbulence and silently leave it un-downgraded."""
        before = {"turbulence": {"route_pct_amber": 40}}
        migrated, _ = rename_extent_params({"advisories": {"params": before}})
        restored = self._downgrade(migrated["advisories"]["params"])
        assert restored == before

    def test_round_trips_a_profile_carrying_every_key(self):
        dense = TestLossless()._dense_legacy_profile()["advisories"]["params"]
        migrated, _ = rename_extent_params({"advisories": {"params": dense}})
        restored = self._downgrade(migrated["advisories"]["params"])
        assert restored == dense

    def test_leaves_unrelated_params_untouched(self):
        params = {"airport_wind": {"crosswind_red_kt": 25}}
        assert self._downgrade(params) == params

    def test_a_non_numeric_value_is_carried_not_raised(self):
        """`downgrade()` used to lack the guard `upgrade()` had, so one string
        value would `TypeError` and roll back the reversal for every profile."""
        out = self._downgrade({"fiki_icing": {"extent_pct_amber": "eighty"}})
        assert out == {"fiki_icing": {"clear_cruise_amber_pct": "eighty"}}

    def test_a_bool_is_not_silently_inverted(self):
        """`100 - True` would have stored a plausible-looking 99."""
        out = self._downgrade({"fiki_icing": {"extent_pct_red": True}})
        assert out == {"fiki_icing": {"clear_cruise_red_pct": True}}


# The pre-consolidation key census, transcribed from issue #571's D5 section —
# NOT derived from EXTENT_KEY_RENAMES. That independence is the point: the
# lossless tests above build their fixture *from* the map, so a key the map
# forgot is invisible to them. That is exactly how `turbulence.route_pct_red`
# survived a "no old key survives" assertion while being silently unmigrated
# (#571 review).
_ISSUE_CENSUS: dict[str, set[str]] = {
    "cloud_top": {"pct_amber"},
    "convective": {"affected_pct_amber", "affected_pct_red"},
    "dd_nwp_agreement": {"amber_pct", "red_pct"},
    "enroute_precip": {"snow_pct_amber", "snow_moderate_pct_red"},
    "fiki_icing": {"clear_cruise_amber_pct", "clear_cruise_red_pct"},
    "freezing_precip": {"primed_pct_amber"},
    "icing_escape": {
        "icing_coverage_pct_amber", "no_escape_pct_red",
        "route_pct_amber", "min_route_pct",
    },
    "ifr_feasibility": {"icing_pct_amber", "icing_pct_red"},
    "model_agreement": {"poor_pct_amber", "poor_pct_red"},
    "turbulence": {"route_pct_amber", "route_pct_red"},
    "vfr_feasibility": {"imc_pct_amber", "imc_pct_red"},
    "vmc_cruise": {"bkn_pct_amber", "ovc_pct_red"},
}

# Deliberately NOT consolidated, with the reason. `enroute_precip`'s rain axis
# is a second amber axis an advisory can only have one common key for;
# `convective_character`'s are three-way band boundaries and a contiguous-run
# floor, not an amber/red pair.
_DELIBERATELY_KEPT: dict[str, set[str]] = {
    "enroute_precip": {"rain_pct_amber"},
    "convective_character": {
        "isolated_max_pct", "scattered_max_pct", "embed_min_nm",
    },
}


class TestMapCompleteness:
    """The rename map against an independent census, not against itself."""

    def test_every_censused_key_is_migrated(self):
        missing = {
            adv_id: sorted(keys - set(EXTENT_KEY_RENAMES.get(adv_id, {})))
            for adv_id, keys in _ISSUE_CENSUS.items()
            if keys - set(EXTENT_KEY_RENAMES.get(adv_id, {}))
        }
        assert not missing, (
            "these pre-consolidation keys would linger in stored profiles, "
            f"inert, while the pilot believes their tuning is live: {missing}"
        )

    def test_the_map_invents_nothing(self):
        """The reverse direction: no rename for a key the census never had."""
        extra = {
            adv_id: sorted(set(renames) - _ISSUE_CENSUS.get(adv_id, set()))
            for adv_id, renames in EXTENT_KEY_RENAMES.items()
            if set(renames) - _ISSUE_CENSUS.get(adv_id, set())
        }
        assert not extra, f"renames for keys not in the census: {extra}"

    def test_deliberately_kept_keys_are_still_declared(self):
        """A key we chose not to consolidate must still exist in the catalog —
        otherwise it was silently dropped rather than deliberately kept."""
        catalog = _catalog_keys()
        for adv_id, keys in _DELIBERATELY_KEPT.items():
            for key in keys:
                assert key in catalog[adv_id], f"{adv_id}:{key} vanished"

    def test_the_census_and_the_kept_set_are_disjoint(self):
        for adv_id, keys in _DELIBERATELY_KEPT.items():
            assert not keys & _ISSUE_CENSUS.get(adv_id, set())


class TestMalformedValues:
    """One bad profile must not cost every other profile its tuning (#571 review).

    ``ProfileSettings.advisories`` is an untyped dict, so a non-numeric value can
    reach the rename. Alembic runs the whole `upgrade` in one transaction, and
    this PR removes the evaluators' legacy old-key read path — so a migration
    that raises on row 7, behind already-deployed code, leaves every pilot's
    non-default tuning silently ignored fleet-wide. Carrying the value across
    under the new name is strictly better: the evaluator's own `params.get`
    default handles junk exactly as it did before.
    """

    def test_a_non_numeric_inverted_value_is_carried_not_raised(self):
        out, stats = rename_extent_params(
            {"advisories": {"params": {"fiki_icing": {
                "clear_cruise_amber_pct": "eighty",
            }}}}
        )
        assert out["advisories"]["params"]["fiki_icing"] == {
            "extent_pct_amber": "eighty"
        }
        assert stats.uninvertible == 1
        assert stats.inverted == 0

    def test_a_bool_is_not_treated_as_a_number(self):
        """`isinstance(True, int)` is True in Python; `100 - True` would store
        99 and look like a real threshold."""
        out, stats = rename_extent_params(
            {"advisories": {"params": {"fiki_icing": {
                "clear_cruise_red_pct": True,
            }}}}
        )
        assert out["advisories"]["params"]["fiki_icing"] == {
            "extent_pct_red": True
        }
        assert stats.uninvertible == 1

    def test_a_normal_numeric_value_still_inverts(self):
        out, stats = rename_extent_params(
            {"advisories": {"params": {"fiki_icing": {
                "clear_cruise_amber_pct": 70,
            }}}}
        )
        assert out["advisories"]["params"]["fiki_icing"] == {
            "extent_pct_amber": 30.0
        }
        assert stats.inverted == 1
        assert stats.uninvertible == 0

    def test_an_already_empty_entry_is_passed_through_not_deleted(self):
        """The transform may only remove an entry IT emptied.

        Both callers gate on ``stats.touched`` today, so the difference is
        invisible — but "everything else is passed through untouched" is the
        contract the migration's losslessness argument rests on, and a future
        unconditional consumer would inherit a silent deletion (#571 review
        round 9).
        """
        out, stats = rename_extent_params(
            {"advisories": {"params": {"vmc_cruise": {}, "turbulence": {}}}}
        )
        assert out["advisories"]["params"] == {"vmc_cruise": {}, "turbulence": {}}
        assert not stats.touched

    def test_an_entry_this_pass_empties_is_still_removed(self):
        """The other half of the rule: a fully-renamed entry leaves no husk."""
        out, stats = rename_extent_params(
            {"advisories": {"params": {"turbulence": {"route_pct_amber": 20}}}}
        )
        assert out["advisories"]["params"] == {
            "turbulence": {"extent_pct_amber": 20}
        }

    def test_a_non_dict_params_entry_is_left_alone(self):
        out, stats = rename_extent_params(
            {"advisories": {"params": {"vmc_cruise": "not-a-dict"}}}
        )
        assert out["advisories"]["params"]["vmc_cruise"] == "not-a-dict"
        assert not stats.touched
