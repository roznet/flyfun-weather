"""Advisory evaluator registry — @register decorator, evaluate_all(), get_catalog()."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryAggregation,
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    RouteAdvisoryResult,
)

if TYPE_CHECKING:
    from weatherbrief.analysis.advisories import AdvisoryEvaluator, RouteContext

logger = logging.getLogger(__name__)

_EVALUATORS: dict[str, type[AdvisoryEvaluator]] = {}

# Category display order for the settings page (#387). Ordered by pilot-input
# density, NOT meteorological family — airport/flight-rules/wind first (the
# choices most pilots actually tune), diagnostics-only categories last. The
# catalog is served in this order so every client (web/iOS/MCP) renders the
# same layout without a client-side ``CATEGORY_KEYS`` copy that drifts.
# ``model`` is the "Diagnostics" group — both its advisories are disabled-by-
# default dev signals — rendered visually distinct at the bottom.
CATEGORY_ORDER: list[str] = [
    "airport",
    "flight_rules",
    "wind",
    "icing",
    "cloud",
    "convective",
    "precipitation",
    "turbulence",
    "sun",
    "fronts",
    "model",
]

# Categories rendered as the visually-distinct "Diagnostics" group (dev signals,
# disabled-by-default). Kept backend-side so the flag travels with the ordered
# category list and clients need no hard-coded exception.
_DIAGNOSTICS_CATEGORIES: frozenset[str] = frozenset({"model"})

# Display order of advisories *within* each category. An advisory not listed
# here sorts after the listed ones (in registration order); a category absent
# from this map keeps registration order entirely. Ordering lives in the
# registry, not the client.
ENTRY_ORDER: dict[str, list[str]] = {
    "airport": ["flight_category", "airport_wind", "density_altitude", "llws"],
    "flight_rules": ["vfr_feasibility", "ifr_feasibility", "approach_feasibility"],
    "wind": ["headwind"],
    "icing": ["icing_escape", "fiki_icing", "freezing_precip"],
    "cloud": ["cloud_top", "vmc_cruise"],
    "convective": ["convective", "convective_character"],
    "precipitation": ["enroute_precip"],
    "turbulence": ["turbulence", "mountain_wind"],
    "sun": ["sun"],
    "fronts": ["fronts"],
    "model": ["model_agreement", "dd_nwp_agreement"],
}


def register(cls: type[AdvisoryEvaluator]) -> type[AdvisoryEvaluator]:
    """Class decorator that registers an advisory evaluator."""
    entry = cls.catalog_entry()
    _EVALUATORS[entry.id] = cls
    return cls


def _catalog_sort_key(
    entry: AdvisoryCatalogEntry, registration_index: int
) -> tuple[int, int, int]:
    """Sort key placing entries in (category, within-category) display order.

    Unlisted categories sort after listed ones; within a category an unlisted
    entry sorts after listed ones, both falling back to registration order so
    the sort stays deterministic and stable.
    """
    cat_idx = (
        CATEGORY_ORDER.index(entry.category)
        if entry.category in CATEGORY_ORDER
        else len(CATEGORY_ORDER)
    )
    order = ENTRY_ORDER.get(entry.category, [])
    entry_idx = order.index(entry.id) if entry.id in order else len(order)
    return (cat_idx, entry_idx, registration_index)


def get_catalog() -> list[AdvisoryCatalogEntry]:
    """Return catalog entries for all registered evaluators, in display order."""
    _ensure_loaded()
    indexed = list(enumerate(cls.catalog_entry() for cls in _EVALUATORS.values()))
    indexed.sort(key=lambda pair: _catalog_sort_key(pair[1], pair[0]))
    return [entry for _, entry in indexed]


def get_category_order(
    entries: list[AdvisoryCatalogEntry] | None = None,
) -> list[dict[str, object]]:
    """Return the ordered category list served alongside the catalog (#387).

    Only categories that actually have registered advisories are returned, in
    ``CATEGORY_ORDER`` (unknown categories appended). Labels stay client-side
    (i18n); each entry carries its stable ``key`` plus a ``diagnostics`` flag so
    the client renders the Diagnostics group distinctly without its own list.

    Pass ``entries`` (e.g. the list ``get_catalog()`` already built) to avoid
    re-invoking every evaluator's ``catalog_entry()`` a second time on the hot
    catalog-endpoint path; omit it and the categories are derived directly.
    """
    _ensure_loaded()
    if entries is None:
        present = {cls.catalog_entry().category for cls in _EVALUATORS.values()}
    else:
        present = {e.category for e in entries}
    ordered = [c for c in CATEGORY_ORDER if c in present]
    ordered += sorted(c for c in present if c not in CATEGORY_ORDER)
    return [
        {"key": key, "diagnostics": key in _DIAGNOSTICS_CATEGORIES}
        for key in ordered
    ]


def get_altitude_dependent_ids() -> set[str]:
    """Return IDs of evaluators that depend on cruise altitude."""
    _ensure_loaded()
    return {aid for aid, cls in _EVALUATORS.items() if cls.catalog_entry().altitude_dependent}


def get_scan_class_ids() -> set[str]:
    """IDs of ``timing_class="scan"`` evaluators — the timing-scan ranking set.

    These are the timing-sensitive, GRIB-dependent hazards; the scan's
    improvement margin is summed over this set (while every candidate is still
    graded on the *full* advisory set).
    """
    _ensure_loaded()
    return {aid for aid, cls in _EVALUATORS.items() if cls.catalog_entry().timing_class == "scan"}


def get_timing_hint_ids() -> set[str]:
    """IDs whose RED/AMBER triggers the Flexibility hint on unscanned flights.

    TODO(timing-scenario-plan.md, decision A): not yet consumed — the soft
    "set Flexibility to scan for a better window" lightbulb on
    Flexibility=None flights is a planned follow-up; this is its trigger set.

    The scan-class set plus explicitly ``timing_hint=True`` entries (e.g.
    ``flight_category`` — OM/TAF-graded, so never scan-class, but airport
    fog/ceiling burn-off is a classic timing case worth hinting on).
    """
    _ensure_loaded()
    return get_scan_class_ids() | {
        aid for aid, cls in _EVALUATORS.items() if cls.catalog_entry().timing_hint
    }


def resolve_enabled_ids(enabled_map: dict[str, bool] | None) -> set[str] | None:
    """Resolve a saved per-profile advisory enable map into the set to evaluate.

    The saved ``{id: bool}`` map is treated as **overrides, not an exhaustive
    allow-list**: an advisory the map does not mention falls back to its catalog
    ``default_enabled``. This keeps the backend in lockstep with the settings UI,
    which renders each toggle as ``enabledMap[id] ?? default_enabled`` (see
    ``web/ts/settings-main.ts`` ``renderAdvisorySettings``). Without this merge a
    newly added default-on advisory is invisible on every *customized* profile
    until the pilot re-saves it (the old sparse ``{k for k, v in … if v}`` dropped
    any id absent from the saved map). Explicit opt-outs (``id: false``) are
    honored.

    Returns ``None`` when the profile has no advisory customization, so callers
    let :func:`evaluate_all` apply ``default_enabled`` to every evaluator — the
    same result the merge would produce, kept as ``None`` to preserve the
    "uncustomized profile" signal that the front-advisory gating relies on.
    """
    if not enabled_map:
        return None
    return {
        entry.id
        for entry in get_catalog()
        if enabled_map.get(entry.id, entry.default_enabled)
    }


def evaluate_all(
    ctx: RouteContext,
    enabled_ids: set[str] | None = None,
    user_params: dict[str, dict[str, float]] | None = None,
    aggregation: AdvisoryAggregation = AdvisoryAggregation.MAJORITY,
) -> list[RouteAdvisoryResult]:
    """Evaluate all enabled advisories against the route context.

    Args:
        ctx: Route context with all analysis data.
        enabled_ids: Set of advisory IDs to evaluate. None = all defaults.
        user_params: Per-advisory parameter overrides {advisory_id: {param: value}}.
        aggregation: How per-model statuses combine (WORST or MAJORITY).

    Returns:
        List of RouteAdvisoryResult, one per evaluated advisory.
    """
    _ensure_loaded()
    user_params = user_params or {}
    results: list[RouteAdvisoryResult] = []

    for adv_id, evaluator_cls in _EVALUATORS.items():
        entry = evaluator_cls.catalog_entry()

        # Filter by enabled set or default_enabled
        if enabled_ids is not None:
            if adv_id not in enabled_ids:
                continue
        elif not entry.default_enabled:
            continue

        # Merge user params with defaults
        defaults = {p.key: p.default for p in entry.parameters}
        overrides = user_params.get(adv_id, {})
        params = {**defaults, **overrides}

        try:
            result = evaluator_cls.evaluate(ctx, params)
            # Canonicalize the aggregate under the requested mode. Evaluators
            # build their aggregate with ``from_per_model``'s own default
            # (MAJORITY) and some then customize it — convective synthesizes a
            # cross-model ``aggregate_detail``; fronts builds an explicit
            # all-UNAVAILABLE result to stay hidden. So only re-aggregate when the
            # requested mode actually DIFFERS from that majority default (else we
            # would discard those customizations), and never re-run an
            # all-UNAVAILABLE set through ``from_per_model`` (it collapses to
            # GREEN — resurrecting a deliberately-hidden advisory). Re-aggregating
            # only for non-majority modes is what fixes the prior bug where a
            # WORST preference silently kept a majority-built aggregate.
            if aggregation != AdvisoryAggregation.MAJORITY and not all(
                m.status == AdvisoryStatus.UNAVAILABLE for m in result.per_model
            ):
                result = RouteAdvisoryResult.from_per_model(
                    result.advisory_id, result.per_model, result.parameters_used,
                    aggregation=aggregation,
                )
            results.append(result)
        except Exception as exc:
            # A throwing evaluator must NOT vanish from the briefing — a missing
            # advisory reads as "not a concern" (#391). Emit an explicit
            # UNAVAILABLE result with a diagnostic instead, built directly (never
            # through from_per_model, whose empty-list path we already rely on
            # elsewhere) so the deliberately-empty per_model is preserved.
            logger.warning("Advisory %s evaluation failed", adv_id, exc_info=True)
            results.append(RouteAdvisoryResult(
                advisory_id=adv_id,
                aggregate_status=AdvisoryStatus.UNAVAILABLE,
                aggregate_detail=adv_t(
                    "evaluation_failed", getattr(ctx, "locale", None),
                    error=type(exc).__name__,
                ),
                per_model=[],
                parameters_used=params,
            ))

    return results


_loaded = False


def _ensure_loaded() -> None:
    """Import all evaluator modules so @register decorators run."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    import importlib
    import pkgutil

    import weatherbrief.analysis.advisories as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.name.startswith("_") and info.name != "registry":
            importlib.import_module(f"weatherbrief.analysis.advisories.{info.name}")
