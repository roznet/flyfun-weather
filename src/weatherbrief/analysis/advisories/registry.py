"""Advisory evaluator registry — @register decorator, evaluate_all(), get_catalog()."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from weatherbrief.models import AdvisoryCatalogEntry, RouteAdvisoryResult

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


def evaluate_all(
    ctx: RouteContext,
    enabled_ids: set[str] | None = None,
    user_params: dict[str, dict[str, float]] | None = None,
) -> list[RouteAdvisoryResult]:
    """Evaluate all enabled advisories against the route context.

    Args:
        ctx: Route context with all analysis data.
        enabled_ids: Set of advisory IDs to evaluate. None = all defaults.
        user_params: Per-advisory parameter overrides {advisory_id: {param: value}}.

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
