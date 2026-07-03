"""Advisory evaluator registry — @register decorator, evaluate_all(), get_catalog()."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from weatherbrief.models import AdvisoryAggregation, AdvisoryCatalogEntry, RouteAdvisoryResult

if TYPE_CHECKING:
    from weatherbrief.analysis.advisories import AdvisoryEvaluator, RouteContext

logger = logging.getLogger(__name__)

_EVALUATORS: dict[str, type[AdvisoryEvaluator]] = {}


def register(cls: type[AdvisoryEvaluator]) -> type[AdvisoryEvaluator]:
    """Class decorator that registers an advisory evaluator."""
    entry = cls.catalog_entry()
    _EVALUATORS[entry.id] = cls
    return cls


def get_catalog() -> list[AdvisoryCatalogEntry]:
    """Return catalog entries for all registered evaluators."""
    _ensure_loaded()
    return [cls.catalog_entry() for cls in _EVALUATORS.values()]


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
            # Re-aggregate with the requested mode (evaluators always use WORST)
            if aggregation != AdvisoryAggregation.WORST:
                result = RouteAdvisoryResult.from_per_model(
                    result.advisory_id, result.per_model, result.parameters_used,
                    aggregation=aggregation,
                )
            results.append(result)
        except Exception:
            logger.warning("Advisory %s evaluation failed", adv_id, exc_info=True)

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
