"""Public per-source data catalog endpoint.

Backs the help-page "Data Sources & Models" table and any other UI that
needs to display "which providers do we pull from, where is each one
right now, and what is their horizon/levels/coverage?".

Single source of truth lives in :data:`fetch.freshness.registry.SOURCE_REGISTRY`
— so updating model metadata (resolution, levels, description) updates
both the help page and the freshness logic together, removing the drift
this endpoint was created to fix.

No auth required: catalogue data is purely descriptive system config plus
the last observed init/publish time per source; nothing user-specific.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from weatherbrief.fetch.freshness import catalog as freshness_catalog

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _serialise_entry(entry: freshness_catalog.SourceCatalogEntry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "model": entry.model,
        "model_label": entry.model_label,
        "provider_label": entry.provider_label,
        "provider_url": entry.provider_url,
        "role": entry.role,
        "resolution": entry.resolution,
        "coverage": entry.coverage,
        "pressure_levels": entry.pressure_levels,
        "description": entry.description,
        "cycles": list(entry.cycles),
        # Stringify int keys so the wire format is unambiguous JSON.
        "horizon_hours": {str(h): v for h, v in entry.horizon_hours.items()},
        "delivery_offset_hours": {
            str(h): v for h, v in entry.delivery_offset_hours.items()
        },
        "latest_init": _iso(entry.latest_init),
        "published_at": _iso(entry.published_at),
        "next_expected": _iso(entry.next_expected),
        "horizon_end": _iso(entry.horizon_end),
        "marker_health": entry.marker_health,
    }


@router.get("")
def list_data_sources(model: str | None = None) -> dict[str, Any]:
    """Return every tracked (model, source) entry with merged static + live state.

    ``model`` optionally narrows to a single pack-model key
    (``ecmwf``, ``gfs``, ``icon``, ``icon_eu``, ``ukmo``, ``meteofrance``).
    Multiple sources for the same pack-model are kept distinct (e.g. for
    ``icon`` you get both ``icon_eu:dwd`` and ``icon:openmeteo``).

    Response shape:
        {
          "sources": [...per-source entries...],
          "generated_at": "ISO UTC timestamp",
        }
    """
    # Capture the wallclock *before* the snapshot read so ``generated_at``
    # is at-or-before the moment the marker store was sampled — matters
    # if the value is ever used for cache validation downstream.
    generated_at = freshness_catalog.utcnow()
    entries = freshness_catalog.build()
    if model:
        entries = [e for e in entries if e.model == model]
    return {
        "sources": [_serialise_entry(e) for e in entries],
        "generated_at": generated_at.isoformat(),
    }
