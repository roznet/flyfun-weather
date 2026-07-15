"""Server-side sparsify of stored profile settings (#405 / #402 slice 2).

The Python sibling of ``web/ts/helpers/profile-sparsify.ts``. Both implement the
same rule so the client's save path and the one-time backfill migration converge
on an identical stored shape: **persist only what differs from the default.**

Absence resolves to the same default at grading time — advisory params via
``evaluate_all``'s ``{**catalog_defaults, **user_params}`` merge, engine methods
via :data:`ENGINE_METHOD_DEFAULTS` — so pruning a value equal to its default is
lossless for grading and lets an already-dense profile shrink.

Three things are swept, and **nothing else is touched**:

1. **Advisory params** (``advisories.params[id][key]``): a stored param is
   dropped only when the catalog declares a default for that exact ``id:key``
   *and* the stored value equals it. A param with no catalog default (unknown /
   legacy key) is kept — we never delete a value we cannot prove is a default.
2. **Legacy ``cloud_method``** (pre-#410 fused ``<style>_<source>`` key): reduced
   to its bare source. NWP (the default) → the key is dropped (absence follows
   the default); DD (**not** the default) → rewritten to the canonical
   ``cloud_source: "dd"`` so the DD grade is preserved exactly. Either way the
   fused key is removed, which is what lets the #410 read-path fallback be
   deleted.
3. **Engine methods** (``icing_method`` / ``cloud_source`` / ``convective_method``):
   dropped when equal to :data:`ENGINE_METHOD_DEFAULTS`; a differing value is a
   deliberate override and is kept.

**``advisories.enabled`` is never touched** (#402): ``fronts`` declares
``default_enabled=False`` yet is injected by default when the
``auto_front_detection`` artifact exists, so an explicit ``fronts: false`` is a
real opt-out that a naive "equals default" rule would wrongly delete. Enable
flags are already centrally switchable via ``resolve_enabled_ids`` and stay out
of the sparsify entirely.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from weatherbrief.analysis.advisories.engine_methods import (
    ENGINE_METHOD_DEFAULTS,
    legacy_cloud_source,
)

if TYPE_CHECKING:
    from weatherbrief.models.advisories import AdvisoryCatalogEntry


@dataclass
class SparsifyStats:
    """Per-profile blast radius, summed by the migration for its dry-run report."""

    params_deleted: int = 0
    engine_deleted: int = 0
    cloud_method_dropped: int = 0  # legacy NWP/redundant key removed outright
    cloud_method_rewritten: int = 0  # legacy DD key canonicalized to cloud_source

    @property
    def touched(self) -> bool:
        return bool(
            self.params_deleted
            or self.engine_deleted
            or self.cloud_method_dropped
            or self.cloud_method_rewritten
        )


def build_param_defaults(
    catalog: list[AdvisoryCatalogEntry],
) -> dict[tuple[str, str], float]:
    """Lookup of catalog default keyed ``(advisory_id, param_key)``.

    ``catalog`` is the live ``get_catalog()`` list — imported at migration time
    (never snapshotted as a literal) so a default that moves between merge and
    deploy cannot silently turn the backfill from a no-op into a data change.
    """
    out: dict[tuple[str, str], float] = {}
    for entry in catalog:
        for p in entry.parameters:
            out[(entry.id, p.key)] = p.default
    return out


def sparsify_settings(
    settings: dict,
    param_defaults: dict[tuple[str, str], float],
) -> tuple[dict, SparsifyStats]:
    """Return a sparsified copy of ``settings`` plus a :class:`SparsifyStats`.

    Pure — the input dict is never mutated (the byte-identical test relies on
    comparing the original against the sparsified copy). Order matters: the
    legacy ``cloud_method`` sweep runs *before* the engine-method prune so a
    rewritten ``cloud_source: "dd"`` (an override) survives while a redundant
    ``cloud_source: "nwp"`` (the default) is then dropped.
    """
    out = copy.deepcopy(settings)
    stats = SparsifyStats()

    # 1. Advisory params equal to the catalog default.
    adv = out.get("advisories")
    if isinstance(adv, dict) and isinstance(adv.get("params"), dict):
        pruned: dict[str, dict] = {}
        for adv_id, params in adv["params"].items():
            if not isinstance(params, dict):
                pruned[adv_id] = params
                continue
            kept: dict[str, float] = {}
            for key, val in params.items():
                dflt = param_defaults.get((adv_id, key))
                if dflt is not None and val == dflt:
                    stats.params_deleted += 1
                    continue  # equals default → prune
                kept[key] = val
            if kept:
                pruned[adv_id] = kept
        adv["params"] = pruned

    # 2. Legacy fused ``cloud_method`` → canonical ``cloud_source`` (or dropped).
    if "cloud_method" in out:
        legacy = out.pop("cloud_method")
        if "cloud_source" in out:
            # New key already present and authoritative → the legacy key is
            # redundant regardless of its value.
            stats.cloud_method_dropped += 1
        else:
            source = legacy_cloud_source(legacy)
            if source is not None and source != ENGINE_METHOD_DEFAULTS["cloud_source"]:
                out["cloud_source"] = source  # DD: preserve the override
                stats.cloud_method_rewritten += 1
            else:
                # NWP (the default) or unparseable → absence follows the default.
                stats.cloud_method_dropped += 1

    # 3. Engine methods equal to their declared default.
    for key, dflt in ENGINE_METHOD_DEFAULTS.items():
        if out.get(key) == dflt:
            out.pop(key, None)
            stats.engine_deleted += 1

    return out, stats
