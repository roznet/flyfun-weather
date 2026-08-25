"""Old extent-parameter key → consolidated key, for stored profiles (#571 Stage 3).

Twenty-four catalog keys across thirteen advisories expressed one idea — "how
much of the route counts as a lot" — including a generic pair in either word
order (``amber_pct`` and ``pct_amber``, in different advisories) and one pair of
inverted polarity. They are now ``extent_pct_amber`` / ``extent_pct_red`` /
``extent_min_nm``, each advisory still declaring **its own default**: the
consolidation is of shape and semantics, not of values.

**Why this cannot be left to sparsify.** ``profile_sparsify`` deliberately keeps
any key it cannot prove is a default — *"we never delete a value we cannot prove
is a default"* — so after a rename the old keys are simply unknown keys and would
linger forever, silently doing nothing while the pilot believes their tuning is
live. The rewrite has to be active.

The map is a module-level constant rather than derived from the catalog because
the catalog no longer contains the old names: this is the only surviving record
of what each key used to be called, and it must stay readable after the code that
read those keys is gone. Its TypeScript sibling is
``web/ts/helpers/profile-sparsify.ts`` (``renameExtentParams``) — the two must
move together, or a client would re-save a profile the server just migrated.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

# advisory_id → {old key: new key}. Order within an advisory matters only for
# the legacy aliases, which are applied last (see ``rename_extent_params``).
EXTENT_KEY_RENAMES: dict[str, dict[str, str]] = {
    "cloud_top": {"pct_amber": "extent_pct_amber"},
    "convective": {
        "affected_pct_amber": "extent_pct_amber",
        "affected_pct_red": "extent_pct_red",
    },
    "dd_nwp_agreement": {
        "amber_pct": "extent_pct_amber",
        "red_pct": "extent_pct_red",
    },
    "enroute_precip": {
        "snow_pct_amber": "extent_pct_amber",
        "snow_moderate_pct_red": "extent_pct_red",
    },
    "fiki_icing": {
        "clear_cruise_amber_pct": "extent_pct_amber",
        "clear_cruise_red_pct": "extent_pct_red",
    },
    "freezing_precip": {"primed_pct_amber": "extent_pct_amber"},
    "icing_escape": {
        "icing_coverage_pct_amber": "extent_pct_amber",
        "no_escape_pct_red": "extent_pct_red",
        # Pre-existing read-path aliases this advisory accepted at grading time
        # (``min_route_pct`` was never even a catalog key). Applied only when the
        # primary name is absent, exactly as the old fallback chain did.
        "route_pct_amber": "extent_pct_amber",
        "min_route_pct": "extent_pct_red",
    },
    "ifr_feasibility": {
        "icing_pct_amber": "extent_pct_amber",
        "icing_pct_red": "extent_pct_red",
    },
    "model_agreement": {
        "poor_pct_amber": "extent_pct_amber",
        "poor_pct_red": "extent_pct_red",
    },
    "turbulence": {
        "route_pct_amber": "extent_pct_amber",
        # Added by Stage 2 as a real catalog key (the previously hardcoded
        # ``red_pct=50``) and renamed by Stage 3 in the same PR, so no deployed
        # profile can carry it — but a dev profile saved between the two stages
        # can, and a rename map that is complete only by accident of deployment
        # order is not complete (#571 review).
        "route_pct_red": "extent_pct_red",
    },
    "vfr_feasibility": {
        "imc_pct_amber": "extent_pct_amber",
        "imc_pct_red": "extent_pct_red",
    },
    "vmc_cruise": {
        "bkn_pct_amber": "extent_pct_amber",
        "ovc_pct_red": "extent_pct_red",
    },
}

# Keys whose stored VALUE must be inverted as well as renamed. ``fiki_icing``
# expressed its thresholds as a percentage of the *clear* cruise, compared with
# ``<`` — the one gate in the system that read the other way. A pilot who set
# "amber below 70% clear" means "amber at or above 30% affected", so the number
# must flip with the name or their tuning would silently invert.
INVERTED_PCT_KEYS: dict[str, set[str]] = {
    "fiki_icing": {"clear_cruise_amber_pct", "clear_cruise_red_pct"},
}

# Aliases applied only when the primary name is absent (see above). Scoped by
# advisory: ``route_pct_amber`` is a secondary alias for ``icing_escape`` and the
# PRIMARY (and only) old name for ``turbulence``, so a bare key-name check would
# silently skip turbulence.
SECONDARY_ALIASES: dict[str, set[str]] = {
    "icing_escape": {"route_pct_amber", "min_route_pct"},
}


@dataclass
class RenameStats:
    """Per-profile blast radius, summed by the migration for its dry-run report."""

    renamed: int = 0
    inverted: int = 0
    dropped_shadowed: int = 0  # old key discarded because the new one already existed
    per_advisory: dict[str, int] = field(default_factory=dict)

    @property
    def touched(self) -> bool:
        return bool(self.renamed or self.dropped_shadowed)


def rename_extent_params(settings: dict) -> tuple[dict, RenameStats]:
    """Rewrite old extent keys in ``advisories.params`` to the consolidated ones.

    Pure — the input is never mutated. Only keys this module names are touched;
    everything else in ``settings`` is passed through untouched, including
    ``advisories.enabled`` and any parameter the consolidation did not reach.

    Conflict rule: a value already stored under the NEW key wins and the old key
    is dropped. That can only happen if a client wrote both, in which case the
    new key is the more recent write. Secondary aliases (``icing_escape``'s
    pre-existing read-path fallbacks) are applied only when the primary is
    absent, mirroring the fallback chain they replace, so a profile carrying both
    resolves exactly as it graded before.
    """
    out = copy.deepcopy(settings)
    stats = RenameStats()

    adv = out.get("advisories")
    if not isinstance(adv, dict) or not isinstance(adv.get("params"), dict):
        return out, stats

    for adv_id, renames in EXTENT_KEY_RENAMES.items():
        params = adv["params"].get(adv_id)
        if not isinstance(params, dict):
            continue
        secondary = SECONDARY_ALIASES.get(adv_id, set())
        inverted = INVERTED_PCT_KEYS.get(adv_id, set())
        # Primaries first, so a secondary alias can never shadow a real value.
        for old in sorted(renames, key=lambda k: (k in secondary, k)):
            if old not in params:
                continue
            new = renames[old]
            value = params.pop(old)
            if new in params:
                stats.dropped_shadowed += 1
                continue
            if old in inverted:
                value = 100.0 - value
                stats.inverted += 1
            params[new] = value
            stats.renamed += 1
            stats.per_advisory[adv_id] = stats.per_advisory.get(adv_id, 0) + 1
        if not params:
            adv["params"].pop(adv_id, None)

    return out, stats


def has_legacy_extent_keys(settings: dict) -> bool:
    """True when any stored param still uses a pre-consolidation extent key."""
    adv = settings.get("advisories")
    if not isinstance(adv, dict) or not isinstance(adv.get("params"), dict):
        return False
    for adv_id, renames in EXTENT_KEY_RENAMES.items():
        params = adv["params"].get(adv_id)
        if isinstance(params, dict) and any(k in params for k in renames):
            return True
    return False
