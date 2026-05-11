"""Public per-source catalog: static :data:`SOURCE_REGISTRY` config merged
with live :class:`MarkerStore` state.

Used by :mod:`api.data_sources` to back the public ``/api/data-sources``
endpoint that feeds the help-page table and any other place that needs
"what data sources do we use, and where is each one right now?"
information.

This module is *pure read* on top of the registry and the marker store —
no I/O, no mutations.  Tests can inject a stub store via :func:`build`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import registry
from .markers import Marker, MarkerStore, get_store


@dataclass(frozen=True)
class SourceCatalogEntry:
    """One row of the public data-sources catalog.

    Static fields come from :class:`registry.SourceConfig`; dynamic fields
    (``latest_init``, ``published_at``, ``next_expected``, ``horizon_end``,
    ``marker_health``) come from the live :class:`MarkerStore`.  All
    timestamps are aware UTC; the API layer ISO-formats them for the wire.
    """

    # --- Identity ----------------------------------------------------------
    key: str
    model: str  # marker-store model name (e.g. "icon_eu", "icon", "ecmwf")
    model_label: str  # display label ("ICON-EU", "ICON-Global", "ECMWF IFS")
    provider_label: str
    provider_url: str

    # --- Static description -----------------------------------------------
    role: str
    resolution: str
    coverage: str
    pressure_levels: int | None
    description: str

    # --- Static schedule --------------------------------------------------
    cycles: tuple[int, ...]
    # Per-cycle hours.  Always emitted as a dict so the API shape is
    # stable whether the underlying config used a uniform timedelta or a
    # per-cycle mapping.
    horizon_hours: dict[int, float]
    delivery_offset_hours: dict[int, float]

    # --- Live (None until the marker store has bootstrapped) --------------
    latest_init: datetime | None
    published_at: datetime | None
    next_expected: datetime | None
    horizon_end: datetime | None
    marker_health: str  # "ok" | "suspect" | "unknown"


def _per_cycle_hours(
    value: object,
    cycles: tuple[int, ...],
) -> dict[int, float]:
    """Normalise a ``timedelta | dict[int, timedelta]`` to a per-cycle dict of hours."""
    from datetime import timedelta

    if isinstance(value, dict):
        return {h: value[h].total_seconds() / 3600.0 for h in cycles}
    if isinstance(value, timedelta):
        return {h: value.total_seconds() / 3600.0 for h in cycles}
    raise TypeError(f"Unexpected horizon/offset type: {type(value)!r}")


def _marker_health(marker: Marker | None, loop_interval_s: float) -> str:
    """Return ``"ok" | "suspect" | "unknown"`` for the marker."""
    from datetime import timedelta

    if marker is None:
        return "unknown"
    if marker.is_stale(timedelta(seconds=loop_interval_s)):
        return "suspect"
    return "ok"


def build(
    *,
    store: MarkerStore | None = None,
    loop_interval_s: float = 300.0,
) -> list[SourceCatalogEntry]:
    """Build the full catalog (one entry per registry source).

    ``store`` defaults to the process-wide singleton from :func:`get_store`;
    pass an explicit instance for tests.  ``loop_interval_s`` controls the
    ``2 × loop_interval`` threshold for :attr:`Marker.is_stale`.
    """
    store = store if store is not None else get_store()
    out: list[SourceCatalogEntry] = []
    for key, cfg in registry.SOURCE_REGISTRY.items():
        model = key.split(":", 1)[0]
        marker = store.get_sync(key, model)
        latest_init = marker.init if marker is not None else None
        published_at = marker.published_at if marker is not None else None
        next_expected = marker.next_expected if marker is not None else None
        # Prefer the provider's actual data_end_time (Open-Meteo) when
        # available: a given run often serves fewer hours than the static
        # config horizon advertises (e.g. ARPEGE config=144h, actual~103h).
        # Direct GRIB sources don't expose this — fall back to the config.
        data_end = marker.data_end if marker is not None else None
        if data_end is not None:
            horizon_end = data_end
        elif latest_init is not None:
            horizon_end = latest_init + cfg.horizon_for(latest_init.hour)
        else:
            horizon_end = None
        out.append(SourceCatalogEntry(
            key=key,
            model=model,
            model_label=cfg.model_label or model.upper(),
            provider_label=cfg.provider_label or key.split(":", 1)[1].title(),
            provider_url=cfg.provider_url,
            role=cfg.role,
            resolution=cfg.resolution,
            coverage=cfg.coverage,
            pressure_levels=cfg.pressure_levels,
            description=cfg.description,
            cycles=cfg.cycles,
            horizon_hours=_per_cycle_hours(cfg.horizon, cfg.cycles),
            delivery_offset_hours=_per_cycle_hours(cfg.delivery_offset, cfg.cycles),
            latest_init=latest_init,
            published_at=published_at,
            next_expected=next_expected,
            horizon_end=horizon_end,
            marker_health=_marker_health(marker, loop_interval_s),
        ))
    return out


def utcnow() -> datetime:
    """Aware UTC ``datetime.now`` — exposed so tests can monkeypatch."""
    return datetime.now(timezone.utc)
