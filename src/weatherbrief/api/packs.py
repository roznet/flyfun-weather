"""API endpoints for briefing packs (fetch history + refresh)."""

from __future__ import annotations

import asyncio
import json as json_mod
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from flyfun_common.auth import is_dev_mode
from weatherbrief.api.security import audit_pack_access
from weatherbrief.db.models import BriefingUsageRow
from weatherbrief.api.throttle import generation_slot, pdf_limiter, plot_limiter

from weatherbrief.api.validation import WAYPOINT_RE

# Input validation for path-sensitive parameters
_MODEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_icao(icao: str) -> str:
    """Validate and sanitize waypoint code used in file paths."""
    if not WAYPOINT_RE.match(icao):
        raise HTTPException(status_code=400, detail="Invalid waypoint code")
    return icao


def _validate_model(model: str) -> str:
    """Validate and sanitize model name used in file paths."""
    if not _MODEL_RE.match(model):
        raise HTTPException(status_code=400, detail="Invalid model name")
    return model
from weatherbrief.api.flights import _load_flight_or_404, _load_owned_flight
from flyfun_common.db import current_user_id, get_db, SessionLocal
from weatherbrief.fetch.freshness import registry as freshness_registry
from weatherbrief.fetch.model_status import fetch_model_metadata
from weatherbrief.tasks.artifacts import parse_target_time as _parse_target_time
from weatherbrief.storage.sounding_profiles import (
    build_sounding_sidecar,
    load_or_build_sounding_profile,
    read_sounding_sidecar,
)
from weatherbrief.tasks.route_weather import run_realtime_refresh
from weatherbrief.models.observations import RefreshDelta, RouteObservations, RouteSigmets
from weatherbrief.models import (
    BriefingPackMeta,
    DiagnosticPublic,
    Flight,
    # Imported eagerly so an ImportError surfaces at module load rather than
    # being silently swallowed by the broad except in _assessment_from_advisories.
    RouteAdvisoriesManifest as _RouteAdvisoriesManifest,
    RouteWindOverlay,
)
from weatherbrief.storage.flights import (
    list_packs,
    load_pack_meta,
    pack_dir_for,
    pack_has_advisories,
    save_pack_meta,
    update_pack_meta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Refresh Registry — prevents duplicate refreshes, tracks status
# ---------------------------------------------------------------------------


class RefreshEntry(BaseModel):
    """Status entry for an active refresh."""

    flight_id: str
    user_id: str | None = None
    status: str = "queued"  # "queued" | "refreshing"
    triggered_by: str = "user"  # "user" | "scheduler"
    stage: str | None = None
    detail: str | None = None
    queued_at: str = ""  # ISO timestamp
    queued_at_mono: float = 0.0  # monotonic clock for accurate elapsed measurement
    refreshing_at_mono: float | None = None


class UserQueueLimitError(Exception):
    """Raised when a user already has the maximum number of active refreshes."""

    def __init__(self, user_id: str, current_count: int, limit: int) -> None:
        self.user_id = user_id
        self.current_count = current_count
        self.limit = limit
        super().__init__(f"User {user_id} already has {current_count} active refresh(es) (limit {limit})")


class _RefreshRegistry:
    """Thread-safe in-memory registry of active refreshes."""

    MAX_QUEUE_DEPTH = 5
    MAX_PER_USER = 2

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, RefreshEntry] = {}

    def try_register(
        self, flight_id: str, triggered_by: str = "user",
        user_id: str | None = None,
    ) -> RefreshEntry | None:
        """Atomically register a refresh.

        Returns the entry on success, or None if the flight is already active.
        Raises ``QueueFullError`` when the queue depth exceeds the cap
        (scheduler bypasses the cap).
        Raises ``UserQueueLimitError`` when a user already has
        ``MAX_PER_USER`` active refreshes.
        """
        with self._lock:
            if flight_id in self._entries:
                return None
            if triggered_by != "scheduler":
                if len(self._entries) >= self.MAX_QUEUE_DEPTH:
                    raise QueueFullError(len(self._entries))
                if user_id is not None:
                    user_count = sum(
                        1 for e in self._entries.values() if e.user_id == user_id
                    )
                    if user_count >= self.MAX_PER_USER:
                        raise UserQueueLimitError(user_id, user_count, self.MAX_PER_USER)
            now_mono = time.monotonic()
            entry = RefreshEntry(
                flight_id=flight_id,
                user_id=user_id,
                status="queued",
                triggered_by=triggered_by,
                queued_at=datetime.now(tz=timezone.utc).isoformat(),
                queued_at_mono=now_mono,
            )
            self._entries[flight_id] = entry
            return entry

    def set_refreshing(self, flight_id: str) -> None:
        with self._lock:
            if flight_id in self._entries:
                self._entries[flight_id].status = "refreshing"
                self._entries[flight_id].refreshing_at_mono = time.monotonic()

    def get_timing(self, flight_id: str) -> tuple[float, float]:
        """Return (queue_wait_seconds, total_elapsed_seconds) for a flight.

        Must be called before unregister().
        """
        with self._lock:
            entry = self._entries.get(flight_id)
            if entry is None:
                return (0.0, 0.0)
            now = time.monotonic()
            total_elapsed = now - entry.queued_at_mono
            queue_wait = (
                (entry.refreshing_at_mono - entry.queued_at_mono)
                if entry.refreshing_at_mono is not None
                else total_elapsed
            )
            return (round(queue_wait, 2), round(total_elapsed, 2))

    def update_progress(self, flight_id: str, stage: str, detail: str | None = None) -> None:
        with self._lock:
            if flight_id in self._entries:
                self._entries[flight_id].stage = stage
                self._entries[flight_id].detail = detail

    def unregister(self, flight_id: str) -> None:
        with self._lock:
            self._entries.pop(flight_id, None)

    def get(self, flight_id: str) -> RefreshEntry | None:
        with self._lock:
            entry = self._entries.get(flight_id)
            return entry.model_copy() if entry else None

    def get_all_active(self) -> list[RefreshEntry]:
        with self._lock:
            return [e.model_copy() for e in self._entries.values()]

    def queue_snapshot(self) -> dict[str, int]:
        """Return counts of queued vs refreshing entries (for metrics)."""
        with self._lock:
            queued = sum(1 for e in self._entries.values() if e.status == "queued")
            refreshing = sum(1 for e in self._entries.values() if e.status == "refreshing")
            return {"queued": queued, "refreshing": refreshing}


class QueueFullError(Exception):
    """Raised when the refresh queue is at capacity."""

    def __init__(self, current_depth: int) -> None:
        self.current_depth = current_depth
        super().__init__(f"Refresh queue full ({current_depth} active)")


refresh_registry = _RefreshRegistry()

router = APIRouter(prefix="/flights/{flight_id}/packs", tags=["packs"])


class ModelStatus(BaseModel):
    """Per-(model, source) freshness detail (issue #108)."""

    source: str  # registry key, e.g. "ecmwf:direct"
    pack_init: int | None = None  # Unix ts of the init the pack was built from
    latest_available: int  # Unix ts of latest known init for this source
    next_expected: str  # ISO datetime — wallclock when next run is expected
    # Provider-reported publish wallclock when known.  OM: from meta.json's
    # last_run_availability_time.  Direct GRIB: NOAA's S3 Last-Modified,
    # DWD's HTTP Last-Modified, ECMWF's local sentinel mtime.
    published_at: str | None = None
    state: str  # "current" | "stale" | "awaiting" | "delayed"
    # True when this source's latest available run reaches the flight's end
    # time.  Drives the tiered refresh gate: only models whose
    # latest run covers the flight count toward the refresh threshold.
    covers_horizon: bool = False


class ModelSourceDetail(BaseModel):
    """One (model, source) row for the freshness info popover.

    Distinct from :class:`ModelStatus` because a single pack-model can have
    *multiple* contributing sources (Open-Meteo as base + direct GRIB
    enrichment); ``models`` only carries the primary, while ``sources``
    enumerates every contributing path.
    """

    model: str  # pack-model name, e.g. "gfs"
    source: str  # registry key, e.g. "gfs:noaa"
    provider: str  # human display name: "NOAA", "DWD", "ECMWF", "Open-Meteo"
    role: str  # "primary" (in pack.model_sources) | "base" (OM under direct)
    init: int  # Unix ts of the run as it landed in the pack
    # Provider-reported publish wallclock — populated for all sources now
    # (OM meta.json; direct GRIB Last-Modified or sentinel mtime).
    published_at: str | None = None
    next_expected: str  # ISO datetime
    state: str  # "current" | "stale" | "awaiting" | "delayed"


class RefreshDecision(BaseModel):
    """Outcome of the tiered refresh gate."""

    mode: Literal["full", "realtime", "none"]
    reason: str
    needed: int          # models-updated threshold, capped by n_eligible
    n_eligible: int      # selected models whose latest run covers the flight
    n_updated: int       # eligible models with a newer-than-pack covering run
    days_out: int
    # ISO wallclock when enough not-yet-updated covering models will have
    # refreshed to cross the threshold — populated when mode != "full".
    eta_useful: str | None = None
    # Covering models not yet updated, soonest-first — the runs the user is
    # waiting on before a full refresh becomes worthwhile. Drives the freshness
    # bar's "awaiting {models}" hint.
    pending_models: list[str] = Field(default_factory=list)


class DataStatus(BaseModel):
    """Model freshness status.

    Back-compat fields (``fresh``, ``stale_models``, ``model_init_times``,
    ``next_expected_update``, ``next_expected_model``) are preserved so
    existing clients keep working.  ``models`` and ``marker_health`` are
    additive (issue #108).  ``sources`` enumerates every contributing
    (model, source) pair for the freshness info popover.
    """

    fresh: bool  # True = all models up to date for the flight horizon
    stale_models: list[str] = Field(default_factory=list)
    model_init_times: dict[str, int] = Field(default_factory=dict)  # latest known init per model
    next_expected_update: str | None = None  # earliest ISO datetime across sources
    next_expected_model: str | None = None  # which model updates next
    marker_health: str = "ok"  # "ok" | "suspect"
    models: dict[str, ModelStatus] = Field(default_factory=dict)
    sources: list[ModelSourceDetail] = Field(default_factory=list)
    # Tiered refresh gate outcome for the *current* lead time — what pressing
    # the refresh button will actually do (full pipeline / cheap real-time /
    # no-op).  Lets the freshness UI agree with the button instead of relying
    # on the raw ``fresh`` min-rule.  Populated when a flight is in context
    # (e.g. ``GET /packs/freshness``); ``None`` from contexts without a flight.
    refresh_decision: RefreshDecision | None = None


def _provider_label(source: str) -> str:
    """Human display name for a source key (fallback to the Title-cased suffix).

    Single source of truth is
    :data:`weatherbrief.fetch.freshness.registry.SOURCE_REGISTRY[source].provider_label`
    so the freshness popover, the data-sources API, and the help page all
    use the same label.  Falls back defensively so legacy packs with an
    unknown source key still render.
    """
    cfg = freshness_registry.SOURCE_REGISTRY.get(source)
    if cfg is not None and cfg.provider_label:
        return cfg.provider_label
    return source.split(":", 1)[-1].title()


class PackMetaResponse(BaseModel):
    """Pack metadata in API responses."""

    flight_id: str
    fetch_timestamp: str
    days_out: int
    is_historical: bool = False
    has_gramet: bool
    has_skewt: bool
    has_digest: bool
    has_advisories: bool = False
    has_alt_advisories: bool = False
    assessment: str | None
    assessment_reason: str | None
    alt_assessment: str | None = None
    alt_assessment_reason: str | None = None
    model_init_times: dict[str, int] = Field(default_factory=dict)
    grib_init_times: dict[str, int] = Field(default_factory=dict)
    models_skipped_region: list[str] = Field(default_factory=list)
    # NOTE: DiagnosticPublic (not Diagnostic) — strips `detail` and
    # `request_id` so we don't ship stack traces or upstream API
    # correlation ids to authenticated clients. The full Diagnostic
    # stays in the DB and on disk for debugging.
    diagnostics: list[DiagnosticPublic] = Field(default_factory=list)
    data_status: DataStatus | None = None
    # DWD Surface Analysis & Forecast — frontend uses these to decide
    # whether to render the section, which chart to default-select, and
    # to compute the "Issued Xh ago" caption client-side.
    dwd_charts_run_cycle: str | None = None
    dwd_charts_default_id: str | None = None
    dwd_charts_in_coverage: bool = False
    dwd_charts_within_horizon: bool = False


def _meta_to_response(
    meta: BriefingPackMeta,
    data_status: DataStatus | None = None,
) -> PackMetaResponse:
    has_advisories = pack_has_advisories(meta.artifact_path)

    return PackMetaResponse(
        flight_id=meta.flight_id,
        fetch_timestamp=meta.fetch_timestamp.isoformat(),
        days_out=meta.days_out,
        is_historical=meta.is_historical,
        has_gramet=meta.has_gramet,
        has_skewt=meta.has_skewt,
        has_digest=meta.has_digest,
        has_advisories=has_advisories,
        has_alt_advisories=meta.has_alt_advisories,
        assessment=meta.assessment,
        assessment_reason=meta.assessment_reason,
        alt_assessment=meta.alt_assessment,
        alt_assessment_reason=meta.alt_assessment_reason,
        model_init_times=meta.model_init_times,
        grib_init_times=meta.grib_init_times,
        models_skipped_region=meta.models_skipped_region,
        diagnostics=[d.to_public() for d in meta.diagnostics],
        data_status=data_status,
        dwd_charts_run_cycle=meta.dwd_charts_run_cycle,
        dwd_charts_default_id=meta.dwd_charts_default_id,
        dwd_charts_in_coverage=meta.dwd_charts_in_coverage,
        dwd_charts_within_horizon=meta.dwd_charts_within_horizon,
    )


@router.get("", response_model=list[PackMetaResponse])
def list_flight_packs(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List all packs (history) for a flight. Any authenticated user can view."""
    _load_flight_or_404(db, flight_id, viewer_id=user_id)
    packs = list_packs(db, flight_id)
    return [_meta_to_response(p) for p in packs]


@router.get("/latest", response_model=PackMetaResponse)
def get_latest_pack(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the most recent pack for a flight. Any authenticated user can view."""
    _load_flight_or_404(db, flight_id, viewer_id=user_id)
    packs = list_packs(db, flight_id)
    if not packs:
        raise HTTPException(status_code=404, detail="No packs yet for this flight")
    return _meta_to_response(packs[0])


@router.get("/freshness", response_model=DataStatus)
def get_freshness(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Check whether the latest pack's data is still fresh."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    packs = list_packs(db, flight_id)
    if not packs:
        return DataStatus(fresh=False)

    status = _build_data_status(packs[0], flight)
    # Attach the tiered-gate outcome for the current lead time so the freshness
    # UI reflects what the refresh button will actually do (and not just the
    # raw min-rule ``fresh`` flag).
    status.refresh_decision = decide_refresh(status, _days_out_now(flight))
    return status


# Pack-model name -> freshness source key when GRIB enrichment succeeds for
# that model.  Single source of truth: used by both ``_finalize_refresh``
# (records ``model_sources`` on new packs) and ``_backfill_sources`` (infers
# source for legacy packs missing the column).
_DIRECT_SOURCE_KEYS: dict[str, str] = {
    "ecmwf": "ecmwf:direct",
    "gfs": "gfs:noaa",
    "icon": "icon_eu:dwd",
}


def model_for_source(source: str) -> str:
    """Return the model name keyed in the marker store for ``source``.

    The store keys by ``(source, model_from_source_prefix)`` -- e.g.
    ``("icon_eu:dwd", "icon_eu")``.  Pack-side ``model_sources`` may map
    pack-model ``"icon"`` to source ``"icon_eu:dwd"``; we strip the suffix
    here to find the matching marker.
    """
    return source.split(":", 1)[0]


# Source-key suffix decides which pack init-time field carries the pack's init.
# Direct sources record into ``grib_init_times``; Open-Meteo records into
# ``model_init_times``.
def _pack_init_for_source(pack: BriefingPackMeta, model: str, source: str) -> int | None:
    if source.endswith(":openmeteo"):
        return pack.model_init_times.get(model)
    return pack.grib_init_times.get(model) or pack.model_init_times.get(model)


def _backfill_sources(pack: BriefingPackMeta) -> dict[str, str]:
    """Infer ``model_sources`` for legacy packs created before issue #108.

    A model with a ``grib_init_times`` entry was direct-GRIB enriched; the
    rest came from Open-Meteo.  Direct-source mapping comes from the
    module-level :data:`_DIRECT_SOURCE_KEYS` so it stays in sync with
    pack-recording in :func:`_finalize_refresh`.
    """
    if pack.model_sources:
        return dict(pack.model_sources)
    out: dict[str, str] = {}
    all_models = set(pack.model_init_times) | set(pack.grib_init_times)
    for model in all_models:
        if model in pack.grib_init_times and model in _DIRECT_SOURCE_KEYS:
            out[model] = _DIRECT_SOURCE_KEYS[model]
        else:
            out[model] = f"{model}:openmeteo"
    return out


def _inline_check(
    source: str, model: str,
) -> tuple[datetime, datetime, datetime | None] | None:
    """Best-effort one-shot dynamic check used when the marker is suspect.

    Falls back to the registry's expected delivery time if the dynamic
    check fails — better than reporting nothing.  Returns
    ``(init, next_expected, published_at)`` aware UTC datetimes;
    ``published_at`` may still be ``None`` if the dispatch couldn't
    determine one (e.g. ECMWF sentinel race, missing HTTP header).
    """
    from weatherbrief.fetch.freshness import registry
    from weatherbrief.fetch.freshness.sources import check_source as _sync_check

    observed = _sync_check(source, model)
    if observed is None:
        # Last-resort: synthesize from registry
        init, nxt = registry.initial_marker_for(source)
        return init, nxt, None
    return (
        observed.init,
        registry.next_run_after(source, observed.init),
        observed.published_at,
    )


def _build_data_status(pack: BriefingPackMeta, flight: Flight) -> DataStatus:
    """Marker-based freshness for one pack (issue #108).

    Pure compute when markers are healthy: zero HTTP fan-out.  For each
    (model, source) recorded on the pack:

    1. Read the marker (or backfill via inline check if suspect/missing).
    2. Pack is stale for this source iff:
       - ``marker.init > pack.init`` AND
       - the new run's horizon reaches the flight's end time.
    """
    from datetime import timedelta as _td

    from weatherbrief.fetch.freshness import LOOP_INTERVAL, registry
    from weatherbrief.fetch.freshness.markers import get_store

    sources = _backfill_sources(pack)
    if not sources:
        return DataStatus(fresh=False)

    flight_end = flight.departure_time + _td(hours=flight.flight_duration_hours or 0)
    store = get_store()

    models_out: dict[str, ModelStatus] = {}
    sources_out: list[ModelSourceDetail] = []
    next_expected_candidates: list[tuple[datetime, str]] = []
    health = "ok"
    any_stale = False
    stale_models: list[str] = []

    def _resolve_marker(source: str, model: str) -> tuple[datetime, datetime, datetime | None] | None:
        """Return (init, next_expected, published_at) or None on suspect+failure."""
        nonlocal health
        marker = store.get_sync(source, model_for_source(source))
        if marker is None or marker.is_stale(LOOP_INTERVAL):
            health = "suspect"
            return _inline_check(source, model_for_source(source))
        return marker.init, marker.next_expected, marker.published_at

    for model, source in sources.items():
        resolved = _resolve_marker(source, model)
        if resolved is None:
            continue
        init, nxt, published_at = resolved

        pack_init_ts = _pack_init_for_source(pack, model, source)
        horizon = registry.run_horizon(source, init)
        run_covers_flight = (init + horizon) >= flight_end

        if pack_init_ts is None:
            state = "current"
        elif int(init.timestamp()) > pack_init_ts and run_covers_flight:
            state = "stale"
            any_stale = True
            stale_models.append(model)
        elif int(init.timestamp()) > pack_init_ts:
            # Newer run exists but doesn't cover the flight → not actionable.
            state = "current"
        else:
            now = datetime.now(timezone.utc)
            state = "delayed" if now > nxt else "awaiting"

        published_at_iso = published_at.isoformat() if published_at else None
        models_out[model] = ModelStatus(
            source=source,
            pack_init=pack_init_ts,
            latest_available=int(init.timestamp()),
            next_expected=nxt.isoformat(),
            published_at=published_at_iso,
            state=state,
            covers_horizon=run_covers_flight,
        )
        next_expected_candidates.append((nxt, model))

        # Primary row in the popover-friendly list — uses the run that
        # actually landed in the pack (pack_init_ts) so the popover shows
        # what the pilot is looking at, not just what's now-available.
        sources_out.append(ModelSourceDetail(
            model=model,
            source=source,
            provider=_provider_label(source),
            role="primary",
            init=pack_init_ts if pack_init_ts is not None else int(init.timestamp()),
            published_at=published_at_iso,
            next_expected=nxt.isoformat(),
            state=state,
        ))

        # When the primary is direct GRIB, OM also contributed (it's always
        # fetched first — see fetch.py:244-247) and supplied the surface base
        # layer.  Surface that as a "base" row so the popover honestly shows
        # both contributing sources.
        if not source.endswith(":openmeteo"):
            om_source = f"{model}:openmeteo"
            om_pack_init = pack.model_init_times.get(model)
            if om_pack_init is not None:
                om_resolved = _resolve_marker(om_source, model)
                if om_resolved is not None:
                    om_init, om_nxt, om_pub = om_resolved
                    om_pub_iso = om_pub.isoformat() if om_pub else None
                    # Compute the OM base's actual marker state instead of
                    # hardcoding "current" — the popover should honestly
                    # reflect a delayed OM publish, even when the pack's
                    # primary direct source is fine.
                    om_now = datetime.now(timezone.utc)
                    if int(om_init.timestamp()) > om_pack_init:
                        om_horizon = registry.run_horizon(om_source, om_init)
                        om_state = (
                            "stale" if (om_init + om_horizon) >= flight_end
                            else "current"
                        )
                    elif om_now > om_nxt:
                        om_state = "delayed"
                    else:
                        om_state = "current" if int(om_init.timestamp()) == om_pack_init else "awaiting"
                    sources_out.append(ModelSourceDetail(
                        model=model,
                        source=om_source,
                        provider=_provider_label(om_source),
                        role="base",
                        init=om_pack_init,
                        published_at=om_pub_iso,
                        next_expected=om_nxt.isoformat(),
                        state=om_state,
                    ))

    next_time = None
    next_model = None
    if next_expected_candidates:
        next_expected_candidates.sort(key=lambda c: c[0])
        next_time, next_model = next_expected_candidates[0]

    return DataStatus(
        fresh=not any_stale,
        stale_models=stale_models,
        model_init_times={m: s.latest_available for m, s in models_out.items()},
        next_expected_update=next_time.isoformat() if next_time else None,
        next_expected_model=next_model,
        marker_health=health,
        models=models_out,
        sources=sources_out,
    )




# ---------------------------------------------------------------------------
# Tiered refresh gating
# ---------------------------------------------------------------------------

# How many *eligible* models (latest run covers the flight) must have a newer
# covering run before a manual refresh runs the full pipeline.  Stricter the
# further out the flight is — a single model tick shouldn't trigger a full
# token+compute+disk refresh of a multi-day-out picture.  Capped by the number
# of eligible models so small selections still work (see ``decide_refresh``).
_REFRESH_THRESHOLD_DEFAULT = 3  # days_out >= 2
_REFRESH_THRESHOLD_BY_DAYS_OUT = {0: 1, 1: 2}


def _refresh_threshold(days_out: int) -> int:
    """Models-updated threshold for a given lead time (uncapped)."""
    if days_out <= 0:
        return _REFRESH_THRESHOLD_BY_DAYS_OUT[0]
    return _REFRESH_THRESHOLD_BY_DAYS_OUT.get(days_out, _REFRESH_THRESHOLD_DEFAULT)


def _days_out_now(flight: Flight) -> int:
    """Lead time in whole days from now (UTC) to the flight's departure date."""
    now = datetime.now(timezone.utc)
    return (flight.departure_time.date() - now.date()).days


def decide_refresh(status: DataStatus, days_out: int) -> RefreshDecision:
    """Decide whether a manual refresh runs the full pipeline, a cheap
    real-time observations refresh, or nothing.

    Pure function of the per-model freshness states already computed by
    :func:`_build_data_status` (which encodes flight-horizon coverage in each
    ``ModelStatus.covers_horizon``).  The gate is progressively stricter the
    further out the flight is:

    - ``needed = min(threshold[days_out], n_eligible)`` with threshold
      ``{>=2: 3, 1: 2, 0: 1}``.
    - ``full`` when ``n_updated >= needed`` (enough covering models have a
      newer run to be worth a full re-run).
    - ``realtime`` at D-0 when not ``full`` — a D-0 press always at least
      pulls fresh METAR/TAF (the caller runs ``run_realtime_refresh``).
    - ``none`` otherwise — a no-op carrying a reason and the ETA of the next
      useful update.

    The ``min(..., n_eligible)`` cap means small model selections still work:
    2 models selected -> D-2/D-1 both need both; 1 model -> every press runs.
    """
    items = list(status.models.items())
    n_eligible = sum(1 for _, ms in items if ms.covers_horizon)
    n_updated = sum(1 for _, ms in items if ms.covers_horizon and ms.state == "stale")
    threshold = _refresh_threshold(days_out)

    # No model's latest run reaches the flight horizon yet — a full refresh
    # can't help, so never "full" (n_updated is necessarily 0 here too).
    if n_eligible == 0:
        if days_out == 0:
            return RefreshDecision(
                mode="realtime",
                reason="D-0: refreshing live METAR/TAF (no model run covers the flight horizon)",
                needed=threshold, n_eligible=0, n_updated=0, days_out=days_out,
            )
        return RefreshDecision(
            mode="none",
            reason="No model run covers the flight horizon yet",
            needed=threshold, n_eligible=0, n_updated=0, days_out=days_out,
            eta_useful=status.next_expected_update,
        )

    needed = min(threshold, n_eligible)

    # Covering models not yet updated, soonest-first. The k-th entry is when the
    # threshold will be crossed (a full refresh becomes worthwhile); the names
    # are the runs the user is waiting on, surfaced in the freshness bar.
    pending = sorted(
        (datetime.fromisoformat(ms.next_expected), ms.next_expected, name)
        for name, ms in items
        if ms.covers_horizon and ms.state != "stale" and ms.next_expected
    )
    pending_models = [name for _, _, name in pending]

    if n_updated >= needed:
        return RefreshDecision(
            mode="full",
            reason=(
                f"{n_updated} of {n_eligible} covering model(s) have a newer "
                f"run (need {needed}) — running full refresh"
            ),
            needed=needed, n_eligible=n_eligible, n_updated=n_updated, days_out=days_out,
            pending_models=pending_models,
        )

    # Not enough covering models updated: the k-th pending run crosses threshold.
    k = needed - n_updated
    eta_useful = pending[k - 1][1] if 0 < k <= len(pending) else None

    if days_out == 0:
        return RefreshDecision(
            mode="realtime",
            reason=(
                f"D-0: only {n_updated} of {needed} covering model(s) updated "
                f"— refreshing live METAR/TAF"
            ),
            needed=needed, n_eligible=n_eligible, n_updated=n_updated, days_out=days_out,
            eta_useful=eta_useful, pending_models=pending_models,
        )

    return RefreshDecision(
        mode="none",
        reason=(
            f"Only {n_updated} of {needed} covering model(s) updated for a "
            f"D-{days_out} flight — no refresh needed"
        ),
        needed=needed, n_eligible=n_eligible, n_updated=n_updated, days_out=days_out,
        eta_useful=eta_useful, pending_models=pending_models,
    )


def _can_force_refresh(request: Request, db: Session) -> bool:
    """Return True if the user is allowed to force-refresh (admin or dev mode)."""
    if is_dev_mode():
        return True
    try:
        from weatherbrief.api.admin import require_admin
        require_admin(request, db=db)
        return True
    except HTTPException:
        return False


# --- Stage metadata for SSE progress ---

_STAGE_LABELS: dict[str, str] = {
    "route_interpolation": "Interpolating route",
    "elevation_profile": "Fetching terrain data",
    "fetch_forecasts": "Fetching forecasts",
    "grib_enrichment": "Adding cloud & icing detail",
    "waypoint_analysis": "Analyzing waypoints",
    "route_analysis": "Analyzing route points",
    "route_advisories": "Evaluating route advisories",
    "alt_advisories": "Evaluating alt-time advisories",
    "route_weather": "Fetching METAR/TAF observations",
    "save_snapshot": "Saving snapshot",
    "fetch_gramet": "Fetching GRAMET",
    "generate_skewt": "Generating Skew-T",
    "briefing_ready": "Briefing ready",
    "llm_digest": "Generating AI digest",
}

_STAGE_PROGRESS: dict[str, float] = {
    "route_interpolation": 0.05,
    "elevation_profile": 0.08,
    "fetch_forecasts": 0.40,
    "grib_enrichment": 0.45,
    "waypoint_analysis": 0.50,
    "route_analysis": 0.58,
    "route_advisories": 0.62,
    "alt_advisories": 0.63,
    "route_weather": 0.64,
    "save_snapshot": 0.65,
    "fetch_gramet": 0.75,
    "generate_skewt": 0.85,
    # The SSE handler emits a dedicated `briefing_ready` event instead of a
    # generic `progress` event for this stage (see refresh_briefing_stream),
    # so this entry is only consumed by the polling /refresh/status endpoint
    # to surface the stage label between provisional persist and digest.
    "briefing_ready": 0.88,
    "llm_digest": 0.95,
}


def _build_route_config(flight, db_path):
    """Build a RouteConfig from a flight, resolving waypoints."""
    from weatherbrief.airports import resolve_waypoints
    from weatherbrief.models import RouteConfig

    if not flight.waypoints:
        raise ValueError("Flight has no waypoints defined")
    waypoint_objs, rejected = resolve_waypoints(flight.waypoints, db_path)
    if rejected:
        logger.warning(
            "Flight %s: briefing route dropped %d waypoint(s) from %s: %s",
            getattr(flight, "id", "?"), len(rejected), flight.waypoints,
            [(r.name, r.reason) for r in rejected],
        )
    return RouteConfig(
        name=flight.route_name or " \u2192 ".join(flight.waypoints),
        waypoints=waypoint_objs,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
    )


def _prepare_refresh(flight, db_path, user_id, flight_id, db=None, *, is_privileged=False, as_of_time=None):
    """Shared setup for both sync and streaming refresh endpoints.

    If a DB session is provided, loads user preferences (models, autorouter
    credentials) and applies them to the BriefingOptions.
    """
    from weatherbrief.fetch.variables import MODEL_ENDPOINTS
    from weatherbrief.models import ModelSource
    from weatherbrief.pipeline import BriefingOptions

    route = _build_route_config(flight, db_path)

    fetch_ts = datetime.now(tz=timezone.utc)
    pack_path = pack_dir_for(user_id, flight_id, fetch_ts)
    pack_path.mkdir(parents=True, exist_ok=True)

    # Load settings from the flight's profile (falls back to user default profile)
    autorouter_creds = None
    models = None
    advisory_models = None
    flight_rules = None
    adv_config: dict = {}
    do_gramet = True
    do_llm_digest = True
    do_icing_enhance = True
    icing_method = None
    cloud_method = None
    convective_method = None
    digest_guidance = None
    locale = None
    units_region = "auto"
    profile_name_for_digest = None
    if db is not None:
        from weatherbrief.api.preferences import (
            load_autorouter_token,
            load_units_region,
            load_user_locale,
        )
        from weatherbrief.api.profiles import load_profile_context

        autorouter_creds = load_autorouter_token(db, user_id)
        locale = load_user_locale(db, user_id)
        units_region = load_units_region(db, user_id)
        profile_ctx = load_profile_context(db, flight.profile_id, user_id)
        profile_settings = profile_ctx.settings
        profile_name_for_digest = profile_ctx.name

        profile_models = profile_settings.get("models")
        if profile_models:
            valid = {m.value for m in ModelSource} & set(MODEL_ENDPOINTS)
            models = [ModelSource(m) for m in profile_models if m in valid]

        advisory_models = profile_settings.get("advisory_models")  # may be None

        # Service toggles from profile
        do_gramet = profile_settings.get("gramet_enabled", True)
        do_llm_digest = profile_settings.get("llm_digest_enabled", True)
        do_icing_enhance = profile_settings.get("icing_severity_enhance", False)
        icing_method = profile_settings.get("icing_method")
        cloud_method = profile_settings.get("cloud_method")
        convective_method = profile_settings.get("convective_method")
        flight_rules = profile_settings.get("flight_rules")  # "vfr_only" or "vfr_ifr"
        digest_guidance = profile_settings.get("digest_guidance")
        adv_config = profile_settings.get("advisories", {})

    # Check rate limits before running the pipeline
    if db is not None:
        from weatherbrief.api.usage import check_rate_limits, check_service_limits

        check_rate_limits(db, user_id, bypass=is_privileged)

        # Gracefully skip optional services that have hit their daily limit
        limits = check_service_limits(db, user_id)
        if not limits["gramet"]:
            logger.info("GRAMET daily limit reached for %s — skipping", user_id)
            do_gramet = False
        if not limits["llm_digest"]:
            logger.info("LLM digest daily limit reached for %s — skipping", user_id)
            do_llm_digest = False

    # Detect historical mode: flight departure is in the past
    is_historical = flight.departure_time < datetime.now(timezone.utc)
    if is_historical:
        do_gramet = False
        do_llm_digest = False

    options = BriefingOptions(
        enrich_grib=True,
        fetch_gramet=do_gramet,
        generate_skewt=False,
        generate_llm_digest=do_llm_digest,
        output_dir=pack_path,
        autorouter_token=autorouter_creds,
        user_id=user_id,
        airports_db_path=db_path,
        icing_severity_enhance=do_icing_enhance,
        historical_mode=is_historical,
    )
    if as_of_time:
        options.as_of_time = as_of_time
    if flight.alt_departure_time:
        options.alt_departure_time = flight.alt_departure_time
    if icing_method:
        options.icing_method = icing_method
    if cloud_method:
        options.cloud_method = cloud_method
    if convective_method:
        options.convective_method = convective_method
    if models:
        options.models = models
    if advisory_models:
        options.advisory_models = advisory_models
    if flight_rules:
        options.flight_rules = flight_rules
    if adv_config.get("aggregation"):
        options.advisory_aggregation = adv_config["aggregation"]
    if adv_config.get("enabled") is not None:
        options.advisory_enabled = adv_config["enabled"]
    if adv_config.get("params"):
        options.advisory_params = adv_config["params"]
    if locale:
        options.locale = locale
    # Resolve an 'auto' units preference against the route's detected region so
    # the LLM prompt formats visibility in US units for US flights.
    from weatherbrief.fetch.variables import ModelRegion, detect_model_region
    from weatherbrief.units import resolve_units_region

    # Mixed/transatlantic routes (ModelRegion.GLOBAL) resolve to europe here, so a
    # US pilot's 'auto' digest on an EGLL->KJFK leg gets metric visibility. Matches
    # the frontend (regionFromIcaos returns null -> europe). Forced us/europe wins.
    detected = "us" if detect_model_region(route) == ModelRegion.NORTH_AMERICA else "europe"
    options.units_region = resolve_units_region(units_region, detected)
    options.profile_id = flight.profile_id
    options.profile_name = profile_name_for_digest
    if digest_guidance:
        options.guidance_key = digest_guidance

    # Fetch current model metadata to record in the pack (skip for historical)
    model_metadata = None if is_historical else fetch_model_metadata()

    return route, fetch_ts, pack_path, options, model_metadata


def _measure_pack_size(pack_path: Path) -> int:
    """Sum file sizes under a pack directory."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(pack_path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _charge_briefing_cost(
    db: Session,
    user_id: str,
    usage,
    result_size_bytes: int,
    usage_row_id: int,
) -> None:
    """Compute and charge the cost for a briefing. Failures are logged but never block."""
    try:
        from weatherbrief.api.credits import charge_briefing, get_active_cost_config
        from weatherbrief.costs import compute_cost, config_from_row

        config_row = get_active_cost_config(db)
        if not config_row:
            return

        config, config_id = config_from_row(config_row)
        breakdown = compute_cost(
            input_tokens=usage.llm_input_tokens or 0,
            output_tokens=usage.llm_output_tokens or 0,
            result_size_bytes=result_size_bytes,
            config=config,
            config_id=config_id,
        )
        charge_briefing(db, user_id, usage_row_id, breakdown)
        logger.info(
            "Charged $%.4f to %s (usage #%d)",
            breakdown.total_usd, user_id, usage_row_id,
        )
    except Exception:
        logger.warning("Failed to charge briefing cost for %s", user_id, exc_info=True)


def _build_pack_meta(
    flight_id: str,
    flight: Flight,
    fetch_ts: datetime,
    pack_path: Path,
    result: "BriefingResult",
    *,
    model_metadata: dict | None = None,
    as_of_time: datetime | None = None,
    provisional: bool = False,
) -> BriefingPackMeta:
    """Build a ``BriefingPackMeta`` from a (possibly partial) ``BriefingResult``.

    Pure: builds the model object only — does not touch the DB.

    When ``provisional=True`` (called between phase 6 and phase 7), the LLM
    digest hasn't run yet, so ``has_digest`` is forced to False and the
    assessment is derived from the route advisories rather than the digest.

    When ``provisional=False`` (called after phase 7), ``has_digest`` reflects
    whether the digest succeeded and the assessment prefers the digest's
    output (falling back to the advisories-derived value when the digest
    failed).
    """
    if as_of_time:
        days_out = (flight.departure_time.date() - as_of_time.date()).days
    else:
        days_out = (flight.departure_time.date() - datetime.now(timezone.utc).date()).days

    # Only carry init times for models actually fetched. Open-Meteo metadata
    # is queried for every known model up-front (used by the freshness check
    # for staleness comparison), but the basis line should only show models
    # that contributed data — otherwise a model skipped at fetch (e.g.
    # range-skipped Météo-France) appears twice: once with its init time
    # and once tagged "skipped".
    init_times = {}
    if model_metadata:
        fetched = set(result.models_fetched)
        init_times = {
            m: meta.last_init_time
            for m, meta in model_metadata.items()
            if m in fetched
        }

    # model_sources records which freshness source produced each model's data.
    # Default = Open-Meteo for every fetched model; overridden to the matching
    # direct-GRIB source where GRIB enrichment succeeded.  Used by the
    # marker-based freshness check (issue #108).
    model_sources: dict[str, str] = {m: f"{m}:openmeteo" for m in init_times}
    for m in result.grib_init_times:
        key = _DIRECT_SOURCE_KEYS.get(m)
        if key:
            model_sources[m] = key

    # Derive alt assessment from alt advisory result if available
    alt_assessment = None
    alt_assessment_reason = None
    has_alt_advisories = False
    if result.alt_advisory_result and result.alt_advisory_result.manifest:
        has_alt_advisories = True
        from weatherbrief.tasks.advise import derive_assessment_from_advisories
        alt_assessment, alt_assessment_reason = derive_assessment_from_advisories(
            result.alt_advisory_result.manifest,
        )

    # Assessment: prefer the digest's, fall back to the advisories manifest.
    # The manifest is in-memory on the BriefingResult after phase 3; only
    # fall through to the on-disk read when callers (e.g. legacy unit tests)
    # provide a result that doesn't carry the manifest.
    assessment: str | None = None
    assessment_reason: str | None = None
    if not provisional and result.digest:
        assessment = result.digest.assessment
        assessment_reason = result.digest.assessment_reason
    if assessment is None:
        assessment, assessment_reason = _assessment_from_advisories(
            result.route_advisories_manifest, pack_path,
        )

    has_digest = False if provisional else (result.digest_path is not None)

    return BriefingPackMeta(
        flight_id=flight_id,
        fetch_timestamp=fetch_ts,
        days_out=days_out,
        has_gramet=result.gramet_path is not None,
        has_skewt=len(result.skewt_paths) > 0,
        has_digest=has_digest,
        assessment=assessment,
        assessment_reason=assessment_reason,
        artifact_path=str(pack_path),
        model_init_times=init_times,
        grib_init_times=result.grib_init_times,
        model_sources=model_sources,
        models_skipped_region=result.models_skipped_region,
        diagnostics=list(result.diagnostics),
        alt_assessment=alt_assessment,
        alt_assessment_reason=alt_assessment_reason,
        has_alt_advisories=has_alt_advisories,
        dwd_charts_run_cycle=result.dwd_charts_run_cycle,
        dwd_charts_default_id=result.dwd_charts_default_id,
        dwd_charts_in_coverage=result.dwd_charts_in_coverage,
        dwd_charts_within_horizon=result.dwd_charts_within_horizon,
    )


def _assessment_from_advisories(
    manifest: "RouteAdvisoriesManifest | None",
    pack_path: Path,
) -> tuple[str | None, str | None]:
    """Derive (assessment, reason) from advisories.

    Prefers the in-memory manifest; falls back to reading
    ``route_advisories.json`` from disk only when the caller didn't supply
    one (legacy callers / tests that hand-build a partial BriefingResult).
    Returns ``(None, None)`` if no manifest is available so the pack row
    falls back to NULL — the briefing UI handles missing assessments
    gracefully.
    """
    from weatherbrief.tasks.advise import derive_assessment_from_advisories

    if manifest is not None:
        try:
            return derive_assessment_from_advisories(manifest)
        except Exception:
            # Broad catch is intentional: derive_assessment_from_advisories
            # iterates over advisory entries and could raise anything an
            # individual evaluator surfaces (AttributeError on a malformed
            # status enum, KeyError on a missing field, etc.). Provisional
            # persist must never abort the pipeline — fall back to NULL.
            # Don't narrow this to (ValueError, AttributeError); a future
            # change to the manifest format would silently bubble through.
            logger.warning(
                "Could not derive assessment from in-memory manifest — falling back to NULL",
                exc_info=True,
            )
            return (None, None)

    adv_path = Path(pack_path) / "route_advisories.json"
    if not adv_path.exists():
        return (None, None)
    try:
        loaded = _RouteAdvisoriesManifest.model_validate_json(adv_path.read_text())
        return derive_assessment_from_advisories(loaded)
    except Exception:
        # Same broad catch as the in-memory path above: I/O can raise OSError,
        # validation can raise ValueError, and derive_assessment_from_advisories
        # can raise AttributeError/KeyError on malformed entries. Persisting
        # the pack must never abort because of an assessment-derivation hiccup.
        logger.warning(
            "Could not derive assessment from %s — falling back to NULL",
            adv_path, exc_info=True,
        )
        return (None, None)


def _persist_pack_provisional(
    flight_id: str,
    flight: Flight,
    fetch_ts: datetime,
    pack_path: Path,
    result: "BriefingResult",
    db: Session,
    *,
    model_metadata: dict | None = None,
    as_of_time: datetime | None = None,
) -> BriefingPackMeta:
    """Insert a provisional pack row at the briefing_ready milestone.

    The LLM digest hasn't run yet — ``has_digest=False`` and the assessment
    comes from advisories. ``_persist_pack_finalize`` will update this row
    once the digest completes (or update with ``has_digest=False`` if it
    fails). The row is inserted unconditionally; if a row with the same
    ``(flight_id, fetch_timestamp)`` already exists (which shouldn't happen
    in practice — each refresh allocates a fresh timestamp), the SQL layer
    will surface the conflict.
    """
    meta = _build_pack_meta(
        flight_id, flight, fetch_ts, pack_path, result,
        model_metadata=model_metadata,
        as_of_time=as_of_time,
        provisional=True,
    )
    save_pack_meta(db, meta)
    logger.info("Provisional pack persisted for %s: %s", flight_id, fetch_ts)
    return meta


def _persist_pack_finalize(
    flight_id: str,
    flight: Flight,
    fetch_ts: datetime,
    pack_path: Path,
    result: "BriefingResult",
    db: Session,
    *,
    user_id: str | None = None,
    model_metadata: dict | None = None,
    as_of_time: datetime | None = None,
) -> BriefingPackMeta:
    """Finalize the pack row after phase 7 — update if a provisional row
    exists, otherwise insert. Logs usage and charges credits.
    """
    meta = _build_pack_meta(
        flight_id, flight, fetch_ts, pack_path, result,
        model_metadata=model_metadata,
        as_of_time=as_of_time,
        provisional=False,
    )
    if not update_pack_meta(db, meta):
        # Sync /refresh, scheduler, or a provisional persist that failed
        # silently — log so we can correlate with any earlier "Provisional
        # pack persist failed" warning.
        logger.info(
            "No provisional row for %s @ %s — inserting directly",
            flight_id, fetch_ts,
        )
        save_pack_meta(db, meta)

    # Log usage and charge credits
    from weatherbrief.api.usage import log_api_usage, log_briefing_usage

    log_api_usage(
        db,
        service="open_meteo",
        pipeline="briefing",
        api_calls=result.usage.open_meteo_calls,
        user_id=user_id,
        flight_id=flight_id,
    )
    if user_id is not None:
        pack_size = _measure_pack_size(pack_path)
        usage_row_id = log_briefing_usage(
            db, user_id, flight_id, result.usage, result_size_bytes=pack_size,
        )
        _charge_briefing_cost(db, user_id, result.usage, pack_size, usage_row_id)

    logger.info("Briefing refreshed for %s: %s", flight_id, fetch_ts)
    return meta


def _finalize_refresh(
    flight_id: str,
    flight: Flight,
    fetch_ts: datetime,
    pack_path: Path,
    result: "BriefingResult",
    db: Session,
    user_id: str | None = None,
    model_metadata: dict | None = None,
    as_of_time: datetime | None = None,
) -> BriefingPackMeta:
    """Backwards-compatible single-shot persist (no provisional row).

    Used by the synchronous ``/refresh`` endpoint and the auto-refresh
    scheduler — paths that don't speak SSE and so can't render a partial
    briefing. There's no benefit to a provisional row in those paths.
    """
    return _persist_pack_finalize(
        flight_id, flight, fetch_ts, pack_path, result, db,
        user_id=user_id,
        model_metadata=model_metadata,
        as_of_time=as_of_time,
    )


class RefreshAccepted(BaseModel):
    """Response for accepted (queued) or gated refresh requests."""

    status: Literal["queued", "already_fresh", "realtime"]
    flight_id: str
    message: str
    # Tiered refresh gate detail — present when the request was gated to a
    # real-time-only refresh or skipped entirely.  Only ever "realtime" or
    # "none" here (the "full" decision proceeds to the pipeline instead).
    mode: Literal["realtime", "none"] | None = None
    reason: str | None = None
    eta_useful: str | None = None  # ISO wallclock of next useful model update
    observations: RouteObservations | None = None  # updated obs (realtime mode)
    sigmets: RouteSigmets | None = None  # updated SIGMETs (realtime mode)
    delta: RefreshDelta | None = None  # worsening summary (realtime mode)


@router.post(
    "/refresh",
    response_model=RefreshAccepted,
    status_code=202,
    responses={
        200: {"model": RefreshAccepted, "description": "Data already fresh"},
    },
)
async def refresh_briefing(
    flight_id: str,
    request: Request,
    force: bool = False,
    as_of_date: str | None = None,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Trigger a new briefing fetch for a flight.

    Queues the pipeline and returns immediately with 202. Poll
    ``GET .../refresh/status`` or call ``get_briefing`` to check
    progress.

    Checks model freshness first and skips the pipeline if data
    hasn't changed (returns 200).  Pass ``?force=true`` (admin/dev
    only) to bypass.
    Pass ``?as_of_date=YYYY-MM-DD`` for historical flights to fetch
    forecasts as they were on that date.
    Returns 409 if a refresh is already in progress for this flight.
    """
    flight = _load_owned_flight(db, flight_id, user_id)

    is_historical = flight.departure_time < datetime.now(timezone.utc)

    # Parse and validate as_of_date for historical refreshes
    as_of_time = None
    if as_of_date:
        try:
            as_of_time = datetime.fromisoformat(as_of_date + "T23:59:00+00:00")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid as_of_date format (expected YYYY-MM-DD)")
        if as_of_time.date() > flight.departure_time.date():
            raise HTTPException(status_code=400, detail="as_of_date must be on or before the departure date")

    if force and not _can_force_refresh(request, db):
        raise HTTPException(status_code=403, detail="Force refresh requires admin access")

    # Historical flights require admin access
    if is_historical and not _can_force_refresh(request, db):
        raise HTTPException(status_code=403, detail="Historical flight refresh requires admin access")

    db_path = request.app.state.db_path
    if not db_path:
        raise HTTPException(status_code=503, detail="AIRPORTS_DB not configured")

    # Tiered refresh gate: full -> pipeline, realtime -> cheap
    # METAR/TAF refresh, none -> 200 no-op (skip for historical flights).
    if not force and not is_historical:
        packs = list_packs(db, flight_id)
        if packs:
            latest = packs[0]
            status = _build_data_status(latest, flight)
            decision = decide_refresh(status, _days_out_now(flight))
            if decision.mode != "full":
                logger.info(
                    "Refresh gate for %s: mode=%s (%s)",
                    flight_id, decision.mode, decision.reason,
                )
                observations = None
                sigmets = None
                delta = None
                resp_status = "already_fresh"
                resp_mode = decision.mode
                if decision.mode == "realtime":
                    try:
                        result = await asyncio.to_thread(
                            run_realtime_refresh, Path(latest.artifact_path), db_path,
                        )
                        observations = result.observations
                        sigmets = result.sigmets
                        delta = result.delta
                        resp_status = "realtime"
                    except Exception:
                        # Degrade to a no-op so status and mode agree.
                        logger.warning(
                            "Real-time refresh failed for %s — returning no-op",
                            flight_id, exc_info=True,
                        )
                        resp_mode = "none"
                return Response(
                    content=RefreshAccepted(
                        status=resp_status,
                        flight_id=flight_id,
                        message=decision.reason,
                        mode=resp_mode,
                        reason=decision.reason,
                        eta_useful=decision.eta_useful,
                        observations=observations,
                        sigmets=sigmets,
                        delta=delta,
                    ).model_dump_json(),
                    status_code=200,
                    media_type="application/json",
                )

    # Duplicate / queue-depth check
    try:
        entry = refresh_registry.try_register(flight_id, triggered_by="user", user_id=user_id)
    except QueueFullError:
        raise HTTPException(status_code=503, detail="Server busy, too many queued refreshes")
    except UserQueueLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"You already have {exc.current_count} refresh(es) in progress (limit {exc.limit}). Wait for one to finish.",
        )
    if entry is None:
        raise HTTPException(status_code=409, detail="Refresh already in progress")

    # Prepare pipeline inputs while we still have the request-scoped DB
    is_privileged = _can_force_refresh(request, db)
    try:
        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, db_path, user_id, flight_id, db=db,
            is_privileged=is_privileged,
            as_of_time=as_of_time,
        )
    except Exception:
        refresh_registry.unregister(flight_id)
        raise

    # Fire pipeline in background — uses its own DB session (same as /refresh/stream)
    def run_pipeline() -> None:
        # User refresh is the highest-priority decode workload. Set it *inside*
        # run_pipeline: this runs in _refresh_executor and run_in_executor does
        # not copy the caller's context, so the value must be established past
        # that boundary for enrich_forecasts to see it.
        from weatherbrief.fetch.grib import DecodePriority, set_decode_priority
        set_decode_priority(DecodePriority.INTERACTIVE)
        refresh_registry.set_refreshing(flight_id)
        try:
            from weatherbrief.pipeline import execute_briefing

            result = execute_briefing(
                route=route,
                departure_time=flight.departure_time,
                options=options,
            )
            queue_wait, total_elapsed = refresh_registry.get_timing(flight_id)
            result.usage.elapsed_seconds = total_elapsed
            result.usage.queue_wait_seconds = queue_wait
            result.usage.triggered_by = "user"

            thread_db = SessionLocal()
            try:
                _finalize_refresh(
                    flight_id, flight, fetch_ts, pack_path, result, thread_db,
                    user_id=user_id, model_metadata=model_metadata,
                    as_of_time=as_of_time,
                )
                thread_db.commit()
            finally:
                thread_db.close()
            logger.info("Background refresh complete for %s", flight_id)
        except Exception as exc:
            logger.error("Background refresh failed for %s: %s", flight_id, exc, exc_info=True)
        finally:
            refresh_registry.unregister(flight_id)

    asyncio.get_event_loop().run_in_executor(_refresh_executor, run_pipeline)

    return RefreshAccepted(
        status="queued",
        flight_id=flight_id,
        message="Briefing refresh queued. Poll get_briefing or refresh/status for progress.",
    )


_refresh_executor = ThreadPoolExecutor(max_workers=2)


@router.post("/refresh/stream")
async def refresh_briefing_stream(
    flight_id: str,
    request: Request,
    force: bool = False,
    as_of_date: str | None = None,
    notify_email: bool = False,
    user_id: str = Depends(current_user_id),
):
    """Stream briefing refresh progress via Server-Sent Events.

    Checks model freshness first and returns immediately if data
    hasn't changed.  Pass ``?force=true`` (admin/dev only) to bypass.
    Pass ``?as_of_date=YYYY-MM-DD`` for historical flights to fetch
    forecasts as they were on that date.
    Pass ``?notify_email=true`` to receive an email when the refresh completes.
    Returns an SSE error event if a refresh is already in progress.
    """
    # Manage our own DB session — FastAPI's Depends(get_db) cleanup
    # conflicts with the long-lived StreamingResponse.
    db = SessionLocal()
    registered = False
    try:
        flight = _load_owned_flight(db, flight_id, user_id)

        is_historical = flight.departure_time < datetime.now(timezone.utc)

        # Parse and validate as_of_date for historical refreshes
        as_of_time = None
        if as_of_date:
            try:
                as_of_time = datetime.fromisoformat(as_of_date + "T23:59:00+00:00")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid as_of_date format (expected YYYY-MM-DD)")
            if as_of_time.date() > flight.departure_time.date():
                raise HTTPException(status_code=400, detail="as_of_date must be on or before the departure date")

        if force and not _can_force_refresh(request, db):
            raise HTTPException(status_code=403, detail="Force refresh requires admin access")

        # Historical flights require admin access
        if is_historical and not _can_force_refresh(request, db):
            raise HTTPException(status_code=403, detail="Historical flight refresh requires admin access")

        db_path = request.app.state.db_path
        if not db_path:
            raise HTTPException(status_code=503, detail="AIRPORTS_DB not configured")

        # Tiered refresh gate: full -> pipeline, realtime -> cheap
        # METAR/TAF refresh, none -> complete no-op (skip for historical).
        if not force and not is_historical:
            packs = list_packs(db, flight_id)
            if packs:
                latest = packs[0]
                status = _build_data_status(latest, flight)
                decision = decide_refresh(status, _days_out_now(flight))
                if decision.mode != "full":
                    logger.info(
                        "Refresh gate for %s (stream): mode=%s (%s)",
                        flight_id, decision.mode, decision.reason,
                    )
                    obs_payload = None
                    sigmets_payload = None
                    delta_payload = None
                    effective_mode = decision.mode
                    if decision.mode == "realtime":
                        try:
                            result = await asyncio.to_thread(
                                run_realtime_refresh, Path(latest.artifact_path), db_path,
                            )
                            obs_payload = result.observations.model_dump(mode="json")
                            if result.sigmets is not None:
                                sigmets_payload = result.sigmets.model_dump(mode="json")
                            if result.delta is not None:
                                delta_payload = result.delta.model_dump(mode="json")
                        except Exception:
                            # Degrade to a no-op so consumers don't treat the
                            # null observations as a successful realtime refresh.
                            logger.warning(
                                "Real-time refresh failed for %s (stream) — completing no-op",
                                flight_id, exc_info=True,
                            )
                            effective_mode = "none"
                    # Carry the (effective) decision on the pack's data_status
                    # too, so the client's freshness bar reflects the gate
                    # outcome without a follow-up /freshness call.
                    status.refresh_decision = decision.model_copy(
                        update={"mode": effective_mode},
                    )
                    pack_resp = _meta_to_response(latest, data_status=status).model_dump(mode="json")
                    decision_payload = decision.model_dump(mode="json")
                    decision_payload["mode"] = effective_mode

                    async def gate_generator() -> AsyncGenerator[str, None]:
                        event = {
                            "type": "complete",
                            "pack": pack_resp,
                            "refresh_decision": decision_payload,
                            "observations": obs_payload,
                            "sigmets": sigmets_payload,
                            "delta": delta_payload,
                        }
                        yield f"event: complete\ndata: {json_mod.dumps(event, default=str)}\n\n"

                    return StreamingResponse(
                        gate_generator(),
                        media_type="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                    )

        # Duplicate / queue-depth check — return SSE error so clients
        # that already opened the stream can handle it gracefully.
        try:
            entry = refresh_registry.try_register(flight_id, triggered_by="user", user_id=user_id)
        except QueueFullError:
            async def busy_generator() -> AsyncGenerator[str, None]:
                event = {"type": "error", "message": "Server busy, too many queued refreshes"}
                yield f"event: error\ndata: {json_mod.dumps(event, default=str)}\n\n"

            return StreamingResponse(
                busy_generator(),
                media_type="text/event-stream",
                status_code=503,
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except UserQueueLimitError as exc:
            raise HTTPException(
                status_code=429,
                detail=f"You already have {exc.current_count} refresh(es) in progress (limit {exc.limit}). Wait for one to finish.",
            )
        if entry is None:
            async def duplicate_generator() -> AsyncGenerator[str, None]:
                event = {"type": "error", "message": "Refresh already in progress"}
                yield f"event: error\ndata: {json_mod.dumps(event, default=str)}\n\n"

            return StreamingResponse(
                duplicate_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        registered = True

        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, db_path, user_id, flight_id, db=db,
            is_privileged=_can_force_refresh(request, db),
            as_of_time=as_of_time,
        )

        # Capture base_url for optional email notification (before db/request close)
        base_url = str(request.base_url).rstrip("/") if notify_email else None
    except Exception:
        if registered:
            refresh_registry.unregister(flight_id)
        raise
    finally:
        db.close()

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def progress_callback(stage: str, detail: str | None = None) -> None:
        refresh_registry.update_progress(flight_id, stage, detail)
        # briefing_ready emits a dedicated SSE event with the provisional pack
        # metadata (handled by briefing_ready_callback below). Skip the generic
        # progress event so clients don't see two notifications for the same
        # milestone.
        if stage == "briefing_ready":
            return
        label = _STAGE_LABELS.get(stage, stage)
        progress = _STAGE_PROGRESS.get(stage, 0.0)
        event = {
            "type": "progress",
            "stage": stage,
            "detail": detail,
            "label": label,
            "progress": progress,
        }
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def briefing_ready_callback(result: "BriefingResult") -> None:
        """Persist the provisional pack row and emit the briefing_ready SSE event.

        Runs in the pipeline worker thread, between phase 6 and phase 7.
        Failures here are logged but never bubble back into the pipeline —
        the digest still runs and ``_persist_pack_finalize`` will insert
        the row at the end if the provisional persist failed.
        """
        thread_db = SessionLocal()
        try:
            try:
                meta = _persist_pack_provisional(
                    flight_id, flight, fetch_ts, pack_path, result, thread_db,
                    model_metadata=model_metadata,
                    as_of_time=as_of_time,
                )
                thread_db.commit()
            except Exception:
                # Explicit rollback before close so a failed flush/commit
                # leaves no half-written state lingering in the session.
                thread_db.rollback()
                logger.warning(
                    "Provisional pack persist failed for %s — digest will still run",
                    flight_id, exc_info=True,
                )
                return
        finally:
            thread_db.close()
        event = {
            "type": "briefing_ready",
            "pack": _meta_to_response(meta).model_dump(mode="json"),
        }
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def run_pipeline() -> None:
        # User refresh (streaming): INTERACTIVE. Set past the _refresh_executor
        # boundary — see the non-streaming run_pipeline for why the priority
        # must be established here rather than inherited.
        from weatherbrief.fetch.grib import DecodePriority, set_decode_priority
        set_decode_priority(DecodePriority.INTERACTIVE)
        refresh_registry.set_refreshing(flight_id)
        try:
            from weatherbrief.pipeline import execute_briefing

            result = execute_briefing(
                route=route,
                departure_time=flight.departure_time,
                options=options,
                progress_callback=progress_callback,
                briefing_ready_callback=briefing_ready_callback,
            )
            # Capture timing before unregister
            queue_wait, total_elapsed = refresh_registry.get_timing(flight_id)
            result.usage.elapsed_seconds = total_elapsed
            result.usage.queue_wait_seconds = queue_wait
            result.usage.triggered_by = "user"

            # Use a dedicated DB session — the request-scoped one isn't thread-safe
            thread_db = SessionLocal()
            try:
                meta = _persist_pack_finalize(
                    flight_id, flight, fetch_ts, pack_path, result, thread_db,
                    user_id=user_id, model_metadata=model_metadata,
                    as_of_time=as_of_time,
                )
                thread_db.commit()
            finally:
                thread_db.close()
            complete_event = {
                "type": "complete",
                "pack": _meta_to_response(meta).model_dump(mode="json"),
                "elapsed_seconds": total_elapsed,
            }
            asyncio.run_coroutine_threadsafe(queue.put(complete_event), loop)

            # Send email notification if requested
            if notify_email and base_url:
                from weatherbrief.db.models import UserRow
                from weatherbrief.notify.email import send_briefing_email

                try:
                    email_db = SessionLocal()
                    try:
                        user = email_db.get(UserRow, user_id)
                        if user and user.email:
                            pack_dir = Path(pack_path)
                            send_briefing_email([user.email], flight, meta, pack_dir, base_url=base_url)
                            logger.info("Refresh completion email sent to %s for %s", user.email, flight_id)
                    finally:
                        email_db.close()
                except Exception as email_exc:
                    logger.warning("Failed to send refresh notification email: %s", email_exc)
        except Exception as exc:
            logger.error("Streaming refresh failed: %s", exc, exc_info=True)
            error_event = {"type": "error", "message": str(exc)}
            asyncio.run_coroutine_threadsafe(queue.put(error_event), loop)
        finally:
            refresh_registry.unregister(flight_id)

    loop.run_in_executor(_refresh_executor, run_pipeline)

    # Long pipeline phases (fetch, LLM digest) can run 30-60s without emitting a
    # progress event. A POST stream that sits idle that long gets reaped by Safari
    # and reverse proxies, surfacing as "Load failed" on the client. Send a
    # comment-line keepalive on idle so bytes keep flowing.
    HEARTBEAT_INTERVAL = 15.0

    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            event_type = event.get("type", "progress")
            data = json_mod.dumps(event, default=str)
            yield f"event: {event_type}\ndata: {data}\n\n"
            if event_type in ("complete", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/refresh/status")
def get_refresh_status(
    flight_id: str,
    user_id: str = Depends(current_user_id),
):
    """Get the refresh status for a specific flight."""
    entry = refresh_registry.get(flight_id)
    if entry is None:
        return {"active": False}
    label = _STAGE_LABELS.get(entry.stage, entry.stage) if entry.stage else None
    return {
        "active": True,
        "status": entry.status,
        "stage": entry.stage,
        "label": label,
        "detail": entry.detail,
        "triggered_by": entry.triggered_by,
        "queued_at": entry.queued_at,
    }


# --- Global refresh status router (not per-flight) ---

refresh_router = APIRouter(prefix="/refresh", tags=["refresh"])


@refresh_router.get("/active")
def get_active_refreshes(
    user_id: str = Depends(current_user_id),
):
    """Return all currently active refreshes."""
    entries = refresh_registry.get_all_active()
    return [e.model_dump() for e in entries]


@refresh_router.get("/stats")
def get_refresh_stats(
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return average refresh time (7-day window) for the progress hint."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    row = (
        db.query(
            sa_func.count().label("total"),
            sa_func.avg(BriefingUsageRow.elapsed_seconds).label("avg_elapsed"),
        )
        .filter(
            BriefingUsageRow.timestamp >= since,
            BriefingUsageRow.elapsed_seconds.isnot(None),
        )
        .one()
    )
    total = row.total or 0
    avg_elapsed = round(float(row.avg_elapsed), 0) if row.avg_elapsed else None
    return {
        "avg_elapsed_seconds": avg_elapsed,
        "sample_size": total,
    }


@router.get("/{timestamp}", response_model=PackMetaResponse)
def get_pack(
    request: Request,
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get a specific pack's metadata. Any authenticated user can view."""
    _load_flight_or_404(db, flight_id, viewer_id=user_id)
    try:
        meta = load_pack_meta(db, flight_id, timestamp)
    except KeyError:
        raise HTTPException(status_code=404, detail="Pack not found")
    audit_pack_access(user_id, flight_id, "get_pack", request)
    return _meta_to_response(meta)


@router.get("/{timestamp}/snapshot")
def get_snapshot(
    request: Request,
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the briefing JSON for a pack (route + analyses + observations, no forecasts)."""
    audit_pack_access(user_id, flight_id, "get_snapshot", request)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    # Prefer briefing.json, fall back to legacy snapshot.json for old packs
    briefing_path = pack_dir / "briefing.json"
    if briefing_path.exists():
        return FileResponse(briefing_path, media_type="application/json")
    snapshot_path = pack_dir / "snapshot.json"
    if snapshot_path.exists():
        return FileResponse(snapshot_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="Snapshot not found")


@router.post("/{timestamp}/observations/refresh")
def refresh_observations(
    flight_id: str,
    timestamp: str,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Re-fetch METAR/TAF observations + route SIGMETs and update snapshot
    without full pipeline.

    Only available for D-0 briefings where observations are meaningful.
    Re-runs route_weather + observation_comparison and route SIGMETs using
    existing forecast data, then patches the snapshot on disk.  Returns
    ``{observations, sigmets}``.
    """
    _load_owned_flight(db, flight_id, user_id)  # authz: owner only
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    from weatherbrief.tasks.artifacts import load_briefing

    briefing_data = load_briefing(pack_dir)
    if not briefing_data:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    days_out = briefing_data.get("days_out")
    if days_out is not None and days_out != 0:
        raise HTTPException(status_code=400, detail="METAR/TAF refresh only available for D-0 briefings")

    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=503, detail="Airport database not configured")

    try:
        result = run_realtime_refresh(pack_dir, db_path)
        return result.model_dump(mode="json")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Observation refresh failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Observation refresh failed")


@router.get("/{timestamp}/gramet")
def get_gramet(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the GRAMET cross-section for a pack (PDF, with PNG fallback for old packs)."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    pdf_path = pack_dir / "gramet.pdf"
    if pdf_path.exists():
        return FileResponse(pdf_path, media_type="application/pdf")
    # Fallback to PNG for packs generated before the PDF switch
    png_path = pack_dir / "gramet.png"
    if png_path.exists():
        return FileResponse(png_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="GRAMET not available")


@router.get("/{timestamp}/gramet.png")
def get_gramet_png(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the GRAMET cross-section as a PNG image.

    If the stored artifact is a PDF, converts the first page to PNG.
    If it's already a PNG, serves it directly.
    """
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    # Prefer existing PNG
    png_path = pack_dir / "gramet.png"
    if png_path.exists():
        return FileResponse(png_path, media_type="image/png")

    # Convert PDF first page to PNG
    pdf_path = pack_dir / "gramet.pdf"
    if pdf_path.exists():
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(pdf_path))
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            png_bytes = pix.tobytes("png")
            doc.close()
            return Response(content=png_bytes, media_type="image/png")
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="PyMuPDF not available for PDF-to-PNG conversion",
            )

    raise HTTPException(status_code=404, detail="GRAMET not available")


@router.get("/{timestamp}/skewt/{icao}/{model}")
def get_skewt(
    flight_id: str, timestamp: str, icao: str, model: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get a Skew-T image for a waypoint, generating on-demand if needed."""
    icao, model = _validate_icao(icao), _validate_model(model)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    skewt_path = pack_dir / "skewt" / f"{icao}_{model}.png"
    if skewt_path.exists():
        return FileResponse(skewt_path, media_type="image/png")

    # Rate limit only uncached (expensive) generations
    plot_limiter.check(user_id)

    # On-demand generation from split files (forecasts.json + briefing.json)
    from weatherbrief.tasks.artifacts import load_briefing, load_forecasts as load_forecasts_json

    briefing_data = load_briefing(pack_dir)
    forecasts_data = load_forecasts_json(pack_dir)
    if not forecasts_data:
        raise HTTPException(status_code=404, detail="Skew-T not available")

    try:
        target_dt = _parse_target_time(briefing_data or forecasts_data)
    except (ValueError, KeyError):
        raise HTTPException(status_code=404, detail="Skew-T not available")

    # Extract analysis data for enhanced overlays
    from weatherbrief.models.analysis import SoundingAnalysis
    cruise_altitude_ft = (briefing_data or forecasts_data).get("route", {}).get("cruise_altitude_ft")
    sa = None
    if briefing_data:
        for wa_data in briefing_data.get("analyses", []):
            if wa_data.get("waypoint", {}).get("icao") == icao:
                sounding_data = wa_data.get("sounding", {}).get(model)
                if sounding_data:
                    try:
                        sa = SoundingAnalysis.model_validate(sounding_data)
                    except Exception:
                        pass
                break

    # Find matching forecast
    from weatherbrief.models import WaypointForecast
    for wf_data in forecasts_data.get("forecasts", []):
        if wf_data.get("waypoint", {}).get("icao") == icao and wf_data.get("model") == model:
            wf = WaypointForecast.model_validate(wf_data)
            hourly = wf.at_time(target_dt)
            if not hourly or not hourly.pressure_levels:
                break
            with generation_slot():
                try:
                    from weatherbrief.digest.skewt import generate_skewt
                    generate_skewt(hourly, icao, model, skewt_path,
                                   analysis=sa, cruise_altitude_ft=cruise_altitude_ft)
                    return FileResponse(skewt_path, media_type="image/png")
                except Exception as exc:
                    logger.warning("Skew-T generation failed for %s/%s: %s", icao, model, exc)
                    raise HTTPException(status_code=500, detail=f"Skew-T generation failed: {exc}" if is_dev_mode() else "Skew-T generation failed")

    raise HTTPException(status_code=404, detail="Skew-T not available")


@router.get("/{timestamp}/hodograph/{icao}/{model}")
def get_hodograph(
    flight_id: str, timestamp: str, icao: str, model: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get a hodograph image for a waypoint, generating on-demand if needed."""
    icao, model = _validate_icao(icao), _validate_model(model)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    hodo_path = pack_dir / "skewt" / f"{icao}_{model}_hodo.png"
    if hodo_path.exists():
        return FileResponse(hodo_path, media_type="image/png")

    # Rate limit only uncached (expensive) generations
    plot_limiter.check(user_id)

    # On-demand generation from forecasts file
    from weatherbrief.tasks.artifacts import load_briefing, load_forecasts as load_forecasts_json

    forecasts_data = load_forecasts_json(pack_dir)
    if not forecasts_data:
        raise HTTPException(status_code=404, detail="Hodograph not available")

    # Parse target time — prefer briefing (has analyses), fall back to forecasts metadata
    briefing_data = load_briefing(pack_dir)
    try:
        target_dt = _parse_target_time(briefing_data or forecasts_data)
    except (ValueError, KeyError):
        raise HTTPException(status_code=404, detail="Hodograph not available")

    # Find matching forecast
    from weatherbrief.models import WaypointForecast
    for wf_data in forecasts_data.get("forecasts", []):
        if wf_data.get("waypoint", {}).get("icao") == icao and wf_data.get("model") == model:
            wf = WaypointForecast.model_validate(wf_data)
            hourly = wf.at_time(target_dt)
            if not hourly or not hourly.pressure_levels:
                break
            with generation_slot():
                try:
                    from weatherbrief.digest.skewt import generate_hodograph
                    generate_hodograph(hourly, icao, model, hodo_path)
                    return FileResponse(hodo_path, media_type="image/png")
                except Exception as exc:
                    logger.warning("Hodograph generation failed for %s/%s: %s", icao, model, exc)
                    raise HTTPException(status_code=500, detail=f"Hodograph generation failed: {exc}" if is_dev_mode() else "Hodograph generation failed")

    raise HTTPException(status_code=404, detail="Hodograph not available")


@router.get("/{timestamp}/route-analyses")
def get_route_analyses(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the route analyses JSON for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")
    return FileResponse(ra_path, media_type="application/json")


@router.get("/{timestamp}/advisories")
def get_advisories(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the route advisories JSON for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    adv_path = pack_dir / "route_advisories.json"
    if not adv_path.exists():
        raise HTTPException(status_code=404, detail="Route advisories not available")
    return FileResponse(adv_path, media_type="application/json")


@router.get("/{timestamp}/advisories/alt")
def get_alt_advisories(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the alt-departure route advisories JSON for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    adv_path = pack_dir / "route_advisories_alt.json"
    if not adv_path.exists():
        raise HTTPException(status_code=404, detail="Alt advisories not available")
    return FileResponse(adv_path, media_type="application/json")


@router.post("/{timestamp}/advisories/alt/compute")
def compute_alt_advisories(
    flight_id: str,
    timestamp: str,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Compute alt-departure advisories on-demand from existing pack data.

    Uses the flight's ``alt_departure_time`` to re-run analysis and
    advisories against the already-fetched forecast data.  Updates
    the pack meta with the alt assessment.
    """
    from weatherbrief.tasks.advise import derive_assessment_from_advisories, run_alt_from_pack

    flight = _load_owned_flight(db, flight_id, user_id)
    if not flight.alt_departure_time:
        raise HTTPException(status_code=422, detail="Flight has no alt departure time set")

    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available for alt computation")

    db_path = getattr(request.app.state, "db_path", "")
    route = _build_route_config(flight, db_path)

    # Load advisory profile
    enabled_ids, user_params, aggregation, adv_models, icing_method, cloud_method, convective_method, recompute_conds, locale = \
        _load_advisory_profile(db, flight, user_id, request, pack_dir)

    advisory_result = run_alt_from_pack(
        pack_dir=pack_dir,
        alt_departure_time=flight.alt_departure_time,
        route=route,
        advisory_models=adv_models,
        enabled_ids=enabled_ids,
        user_params=user_params,
        aggregation=aggregation,
        airport_conditions_recompute=recompute_conds,
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
        locale=locale,
    )

    if advisory_result.manifest is None:
        raise HTTPException(status_code=500, detail=advisory_result.error or "Alt advisory evaluation failed")

    # Update pack meta with alt assessment
    alt_assessment, alt_assessment_reason = derive_assessment_from_advisories(advisory_result.manifest)
    from weatherbrief.db.models import BriefingPackRow
    from sqlalchemy import select

    naive_ts = datetime.fromisoformat(timestamp).replace(tzinfo=None)
    stmt = select(BriefingPackRow).where(
        BriefingPackRow.flight_id == flight_id,
        BriefingPackRow.fetch_timestamp == naive_ts,
    )
    pack_row = db.execute(stmt).scalar_one_or_none()
    if pack_row:
        pack_row.alt_assessment = alt_assessment
        pack_row.alt_assessment_reason = alt_assessment_reason
        pack_row.has_alt_advisories = True
        db.flush()

    return advisory_result.manifest.model_dump()


def _recompute_airport_conditions(
    pack_dir: Path,
    flight,
    manifest,
    cross_sections: list,
    db_path: str = "",
    models: list[str] | None = None,
):
    """Recompute airport conditions from existing analysis data.

    Tries to reuse runway data from the previous advisory manifest.
    Falls back to computing from flight waypoints + airports DB.
    Returns None if computation fails or data is unavailable.

    Args:
        models: Model list to evaluate. Defaults to manifest.models if not set.
    """
    from weatherbrief.analysis.airport_conditions import compute_airport_conditions
    from weatherbrief.models import RouteAdvisoriesManifest

    effective_models = models or manifest.models

    try:
        adv_path = pack_dir / "route_advisories.json"
        prev_manifest = (
            RouteAdvisoriesManifest.model_validate_json(adv_path.read_text())
            if adv_path.exists() else None
        )

        if prev_manifest and prev_manifest.airport_conditions:
            prev_dep = prev_manifest.airport_conditions.departure
            prev_arr = prev_manifest.airport_conditions.arrival
            runway_data = {
                prev_dep.icao: prev_dep.runway_ends,
                prev_arr.icao: prev_arr.runway_ends,
            }
            return compute_airport_conditions(
                analyses=manifest.analyses,
                cross_sections=cross_sections,
                models=effective_models,
                dep_icao=prev_dep.icao,
                dep_name=prev_dep.name,
                arr_icao=prev_arr.icao,
                arr_name=prev_arr.name,
                runway_data=runway_data,
            )

        if flight.waypoints and len(flight.waypoints) >= 2:
            dep_icao = flight.waypoints[0]
            arr_icao = flight.waypoints[-1]
            runway_data = None
            if db_path:
                from weatherbrief.airports import get_runway_ends

                runway_data = get_runway_ends([dep_icao, arr_icao], db_path)
            return compute_airport_conditions(
                analyses=manifest.analyses,
                cross_sections=cross_sections,
                models=effective_models,
                dep_icao=dep_icao,
                dep_name=dep_icao,
                arr_icao=arr_icao,
                arr_name=arr_icao,
                runway_data=runway_data,
            )
    except Exception:
        logger.warning("Airport conditions computation failed in recalculate", exc_info=True)

    return None


def _load_advisory_profile(db, flight, user_id, request, pack_dir):
    """Load advisory profile settings and build a recompute-conditions callback.

    Shared by recalculate_advisories and altitude_table endpoints.
    Returns (enabled_ids, user_params, aggregation, adv_models, icing_method,
    cloud_method, convective_method, recompute_conds_callback, locale).
    """
    from weatherbrief.api.preferences import load_user_locale
    from weatherbrief.api.profiles import load_profile_settings
    from weatherbrief.models import AdvisoryAggregation

    profile_settings = load_profile_settings(db, flight.profile_id, user_id)
    adv_config = profile_settings.get("advisories", {})
    enabled_map = adv_config.get("enabled")
    enabled_ids = None
    if enabled_map:
        enabled_ids = {k for k, v in enabled_map.items() if v}
    user_params = adv_config.get("params") or {}
    aggregation = AdvisoryAggregation(adv_config.get("aggregation", "majority"))

    adv_models = profile_settings.get("advisory_models")
    icing_method = profile_settings.get("icing_method")
    cloud_method = profile_settings.get("cloud_method")
    convective_method = profile_settings.get("convective_method")
    db_path = getattr(request.app.state, "db_path", "")
    locale = load_user_locale(db, user_id)

    def recompute_conds(rp_analyses, cross_sections, advisory_model_names):
        from types import SimpleNamespace

        proxy = SimpleNamespace(analyses=rp_analyses, models=advisory_model_names)
        return _recompute_airport_conditions(
            pack_dir, flight, proxy, cross_sections,
            db_path=db_path, models=advisory_model_names,
        )

    return enabled_ids, user_params, aggregation, adv_models, icing_method, cloud_method, convective_method, recompute_conds, locale


class RecalculateAdvisoriesResponse(BaseModel):
    """Envelope returned by /advisories/recalculate.

    ``wind_overlay`` is populated only when the request altitude differs
    from the manifest's baked cruise altitude — otherwise the manifest's
    existing ``wind_components`` are correct and no overlay is sent.
    """
    manifest: _RouteAdvisoriesManifest
    wind_overlay: RouteWindOverlay | None = None


@router.post("/{timestamp}/advisories/recalculate", response_model=RecalculateAdvisoriesResponse)
def recalculate_advisories(
    flight_id: str,
    timestamp: str,
    request: Request,
    cruise_altitude_ft: int | None = None,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Recalculate route advisories from existing analysis data.

    Re-evaluates all advisory logic without re-fetching forecasts or
    re-running the full analysis pipeline. Useful when advisory logic
    or user parameters change.
    """
    from weatherbrief.tasks.advise import run_advisories_from_pack

    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available for recalculation")

    enabled_ids, user_params, aggregation, adv_models, icing_method, cloud_method, convective_method, recompute_conds, locale = \
        _load_advisory_profile(db, flight, user_id, request, pack_dir)

    advisory_result = run_advisories_from_pack(
        pack_dir,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        advisory_models=adv_models,
        enabled_ids=enabled_ids,
        user_params=user_params,
        aggregation=aggregation,
        airport_conditions_recompute=recompute_conds,
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
        locale=locale,
    )

    if advisory_result.manifest is None:
        raise HTTPException(status_code=500, detail=advisory_result.error or "Advisory evaluation failed")

    return RecalculateAdvisoriesResponse(
        manifest=advisory_result.manifest,
        wind_overlay=advisory_result.wind_overlay,
    )


@router.post("/{timestamp}/advisories/altitude-table")
def altitude_table(
    flight_id: str,
    timestamp: str,
    request: Request,
    step_ft: int = 2000,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Compute altitude advisory table — sweep altitude-dependent advisories across altitudes."""
    from weatherbrief.tasks.advise import run_altitude_table_from_pack

    if step_ft < 500 or step_ft > 5000:
        raise HTTPException(status_code=422, detail="step_ft must be between 500 and 5000")

    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")

    enabled_ids, user_params, aggregation, adv_models, icing_method, cloud_method, convective_method, recompute_conds, locale = \
        _load_advisory_profile(db, flight, user_id, request, pack_dir)

    result = run_altitude_table_from_pack(
        pack_dir,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        step_ft=step_ft,
        advisory_models=adv_models,
        enabled_ids=enabled_ids,
        user_params=user_params,
        aggregation=aggregation,
        airport_conditions_recompute=recompute_conds,
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
        locale=locale,
    )

    return result.model_dump()


@router.get("/{timestamp}/elevation")
def get_elevation(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the elevation profile JSON for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    path = pack_dir / "elevation_profile.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Elevation profile not available")
    return FileResponse(path, media_type="application/json")


@router.get("/{timestamp}/bundle")
def get_bundle(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Download a complete pack as a single gzipped JSON bundle for offline use.

    Returns a JSON object keyed by cache endpoint name, gzip-compressed.
    Includes all data files plus pre-computed sounding profiles
    for every (point, model) combination.
    """
    import gzip
    import json

    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    bundle: dict[str, object] = {}

    # Fixed endpoints — map cache key to server file (with fallbacks)
    file_map = {
        "advisories": ["route_advisories.json"],
        "snapshot": ["briefing.json", "snapshot.json"],
        "route-analyses": ["route_analyses.json"],
        "elevation": ["elevation_profile.json"],
        "digest": ["digest.json"],
    }
    for endpoint, filenames in file_map.items():
        for filename in filenames:
            path = pack_dir / filename
            if path.exists():
                bundle[endpoint] = json.loads(path.read_text())
                break

    # Sounding profiles for every (point, model) combination. Prefer the
    # gzipped sidecar written at refresh time (no MetPy recompute → the slow
    # "Preparing…" phase vanishes); fall back to building them on the fly for
    # packs that predate the sidecar.
    sidecar = read_sounding_sidecar(pack_dir)
    if sidecar is not None:
        bundle.update(sidecar)
    else:
        ra_data = bundle.get("route-analyses")
        cs_path = pack_dir / "cross_section.json"
        if ra_data and cs_path.exists():
            cs_data = json.loads(cs_path.read_text())
            bundle.update(build_sounding_sidecar(ra_data, cs_data))

    payload = json.dumps(bundle, separators=(",", ":")).encode()
    compressed = gzip.compress(payload)

    # X-Uncompressed-Length lets the client show accurate download progress:
    # Content-Length is the compressed size, but URLSession transparently
    # decompresses, so the client counts decompressed bytes against this total.
    return Response(
        content=compressed,
        media_type="application/json",
        headers={
            "Content-Encoding": "gzip",
            "X-Uncompressed-Length": str(len(payload)),
        },
    )


@router.get("/{timestamp}/skewt/route/{point_index}/{model}")
def get_route_skewt(
    flight_id: str, timestamp: str, point_index: int, model: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get an on-demand Skew-T for a route point.

    Generates and caches the PNG on first request.
    """
    model = _validate_model(model)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    # Cache path
    cache_dir = pack_dir / "skewt" / "route"
    cache_path = cache_dir / f"pt{point_index:02d}_{model}.png"
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/png")

    # Rate limit only uncached (expensive) generations
    plot_limiter.check(user_id)

    # Load route analyses to get the interpolated time
    import json
    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")

    ra_data = json.loads(ra_path.read_text())
    analyses = ra_data.get("analyses", [])
    point_data = next((a for a in analyses if a["point_index"] == point_index), None)
    if point_data is None:
        raise HTTPException(status_code=404, detail=f"Point index {point_index} not found")

    # Load cross-section to get forecast data
    cs_path = pack_dir / "cross_section.json"
    if not cs_path.exists():
        raise HTTPException(status_code=404, detail="Cross-section data not available")

    cs_data = json.loads(cs_path.read_text())
    cross_sections = cs_data.get("cross_sections", [])
    cs_match = next((cs for cs in cross_sections if cs["model"] == model), None)
    if cs_match is None:
        raise HTTPException(status_code=404, detail=f"Model {model} not found in cross-section")

    if point_index >= len(cs_match["point_forecasts"]):
        raise HTTPException(status_code=404, detail=f"Point index {point_index} out of range")

    # Find closest forecast hour to the interpolated time
    from weatherbrief.models import WaypointForecast
    wf = WaypointForecast.model_validate(cs_match["point_forecasts"][point_index])
    interp_time_str = point_data["interpolated_time"]
    interp_time = datetime.fromisoformat(interp_time_str)
    hourly = wf.at_time(interp_time)
    if not hourly or not hourly.pressure_levels:
        raise HTTPException(status_code=404, detail="No forecast data at this point/time")

    # Extract analysis data for enhanced overlays
    from weatherbrief.models.analysis import SoundingAnalysis
    sa = None
    sounding_data = point_data.get("sounding", {}).get(model)
    if sounding_data:
        try:
            sa = SoundingAnalysis.model_validate(sounding_data)
        except Exception:
            pass
    cruise_altitude_ft = ra_data.get("cruise_altitude_ft")

    # Generate Skew-T
    with generation_slot():
        try:
            from weatherbrief.digest.skewt import generate_skewt
            dist = point_data.get("distance_from_origin_nm")
            total = ra_data.get("total_distance_nm")
            dist_label = f"{dist:.0f}nm/{total:.0f}nm" if dist is not None and total else None
            icao = point_data.get("waypoint_icao")
            label = f"{icao} ({dist_label})" if icao and dist_label else icao or dist_label or f"pt{point_index:02d}"
            generate_skewt(hourly, label, model, cache_path,
                           analysis=sa, cruise_altitude_ft=cruise_altitude_ft)
        except Exception as exc:
            logger.warning("Route Skew-T generation failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Skew-T generation failed: {exc}" if is_dev_mode() else "Skew-T generation failed")

    return FileResponse(cache_path, media_type="image/png")


@router.get("/{timestamp}/sounding-profile/{point_index}/{model}")
def get_sounding_profile(
    flight_id: str, timestamp: str, point_index: int, model: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get raw sounding profile data (T, Td, wind at pressure levels) for a route point.

    Used by iOS app for client-side Skew-T rendering.
    """
    import json

    model = _validate_model(model)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")
    ra_data = json.loads(ra_path.read_text())

    # Cross-section is only needed for the on-the-fly fallback; when the sidecar
    # is present we serve from it without recompute.
    cs_path = pack_dir / "cross_section.json"
    cs_data = json.loads(cs_path.read_text()) if cs_path.exists() else {}

    result = load_or_build_sounding_profile(pack_dir, ra_data, cs_data, point_index, model)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No sounding data for point {point_index}/{model}")
    return result


@router.get("/{timestamp}/hodograph/route/{point_index}/{model}")
def get_route_hodograph(
    flight_id: str, timestamp: str, point_index: int, model: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get an on-demand hodograph for a route point.

    Generates and caches the PNG on first request.
    """
    model = _validate_model(model)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    # Cache path
    cache_dir = pack_dir / "skewt" / "route"
    cache_path = cache_dir / f"pt{point_index:02d}_{model}_hodo.png"
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/png")

    # Rate limit only uncached (expensive) generations
    plot_limiter.check(user_id)

    # Load route analyses to get the interpolated time
    import json
    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")

    ra_data = json.loads(ra_path.read_text())
    analyses = ra_data.get("analyses", [])
    point_data = next((a for a in analyses if a["point_index"] == point_index), None)
    if point_data is None:
        raise HTTPException(status_code=404, detail=f"Point index {point_index} not found")

    # Load cross-section to get forecast data
    cs_path = pack_dir / "cross_section.json"
    if not cs_path.exists():
        raise HTTPException(status_code=404, detail="Cross-section data not available")

    cs_data = json.loads(cs_path.read_text())
    cross_sections = cs_data.get("cross_sections", [])
    cs_match = next((cs for cs in cross_sections if cs["model"] == model), None)
    if cs_match is None:
        raise HTTPException(status_code=404, detail=f"Model {model} not found in cross-section")

    if point_index >= len(cs_match["point_forecasts"]):
        raise HTTPException(status_code=404, detail=f"Point index {point_index} out of range")

    # Find closest forecast hour to the interpolated time
    from weatherbrief.models import WaypointForecast
    wf = WaypointForecast.model_validate(cs_match["point_forecasts"][point_index])
    interp_time_str = point_data["interpolated_time"]
    interp_time = datetime.fromisoformat(interp_time_str)
    hourly = wf.at_time(interp_time)
    if not hourly or not hourly.pressure_levels:
        raise HTTPException(status_code=404, detail="No forecast data at this point/time")

    # Generate hodograph
    with generation_slot():
        try:
            from weatherbrief.digest.skewt import generate_hodograph
            dist = point_data.get("distance_from_origin_nm")
            total = ra_data.get("total_distance_nm")
            dist_label = f"{dist:.0f}nm/{total:.0f}nm" if dist is not None and total else None
            icao = point_data.get("waypoint_icao")
            label = f"{icao} ({dist_label})" if icao and dist_label else icao or dist_label or f"pt{point_index:02d}"
            generate_hodograph(hourly, label, model, cache_path)
        except Exception as exc:
            logger.warning("Route hodograph generation failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Hodograph generation failed: {exc}" if is_dev_mode() else "Hodograph generation failed")

    return FileResponse(cache_path, media_type="image/png")


@router.get("/{timestamp}/digest")
def get_digest(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the LLM digest markdown for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    digest_path = pack_dir / "digest.md"
    if not digest_path.exists():
        raise HTTPException(status_code=404, detail="Digest not available")
    return FileResponse(digest_path, media_type="text/markdown")


@router.get("/{timestamp}/digest/json")
def get_digest_json(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the structured LLM digest as JSON."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    json_path = pack_dir / "digest.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Structured digest not available")
    return FileResponse(json_path, media_type="application/json")


@router.get("/{timestamp}/dwd-chart/{chart_id}")
def get_dwd_chart(
    flight_id: str,
    timestamp: str,
    chart_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Serve a DWD surface chart PNG for this pack.

    Looks up the bytes in the shared ``DATA_DIR/dwd_charts/<run_cycle>/``
    cache. Returns 410 (Gone) when the cache has been evicted — the
    frontend renders an "unavailable" placeholder for that chart.

    Auth: any authenticated user can view (matches dwd-overview).
    """
    from weatherbrief.fetch.dwd_charts import CHART_IDS, resolve_chart_path

    if chart_id not in CHART_IDS:
        raise HTTPException(status_code=400, detail="Invalid chart id")

    # Auth + flight/pack existence check. We don't need the pack_dir
    # itself — chart bytes live in the shared cache, not the pack — so
    # avoid _get_pack_dir's requirement that the artifact directory exist.
    _load_flight_or_404(db, flight_id, viewer_id=user_id)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    if not meta.dwd_charts_run_cycle:
        raise HTTPException(
            status_code=404, detail="DWD charts not available for this pack",
        )

    import os as _os
    data_dir = Path(_os.environ.get("DATA_DIR", "data"))
    path = resolve_chart_path(data_dir, meta.dwd_charts_run_cycle, chart_id)
    if path is None:
        raise HTTPException(status_code=410, detail="Chart no longer cached")

    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{timestamp}/dwd-chart-overlay")
def get_dwd_chart_overlay(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Compute the route-overlay JSON for the DWD chart section.

    Pre-projects each waypoint into native pixel coordinates for both
    chart types (analysis 4389×3114 and icon 800×653). Frontend renders
    these as an SVG polyline with markers, scaling via SVG ``viewBox``.

    Computed on-demand from the briefing snapshot — projection is cheap
    (sub-ms per waypoint) and this avoids a per-pack file artifact, so
    the overlay works for packs created before this feature shipped.
    Falls back to live waypoint resolution when ``briefing.json`` is
    missing (very old / partial packs).
    """
    from weatherbrief.fetch.dwd_charts import build_route_overlay

    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    waypoints: list[tuple[str, float, float]] = []
    briefing_path = pack_dir / "briefing.json"
    if briefing_path.exists():
        try:
            data = json_mod.loads(briefing_path.read_text())
            for wp in (data.get("route") or {}).get("waypoints") or []:
                if "icao" in wp and "lat" in wp and "lon" in wp:
                    waypoints.append((wp["icao"], float(wp["lat"]), float(wp["lon"])))
        except (json_mod.JSONDecodeError, OSError, TypeError, ValueError):
            logger.warning("Failed to parse briefing.json for overlay", exc_info=True)

    if not waypoints:
        # No briefing.json or no waypoints in it — pack predates the
        # split-storage era. Section will just render without the overlay.
        raise HTTPException(
            status_code=404, detail="No waypoints available for overlay",
        )

    overlay = build_route_overlay(waypoints)
    return JSONResponse(
        content=overlay,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{timestamp}/dwd-overview")
def get_dwd_overview(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the translated DWD synoptic overview for this pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    path = pack_dir / "dwd_overview.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="DWD overview not available")
    return FileResponse(path, media_type="application/json")


@router.get("/{timestamp}/report.html")
def get_report_html(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """View a self-contained HTML briefing report. Any authenticated user can view."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    from weatherbrief.report.render import render_html

    html = render_html(pack_dir, flight, meta)
    return HTMLResponse(content=html)


@router.get("/{timestamp}/report.pdf")
def get_report_pdf(
    flight_id: str,
    timestamp: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Download a PDF briefing report. Any authenticated user can view."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    pdf_limiter.check(user_id)

    from weatherbrief.report.render import render_pdf

    import re

    with generation_slot():
        pdf_bytes = render_pdf(pack_dir, flight, meta)
    route_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", flight.route_name or "-".join(flight.waypoints))[:80]
    filename = f"briefing_{route_slug}_{flight.target_date}_d{meta.days_out}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{timestamp}/email")
def send_email(
    flight_id: str,
    timestamp: str,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Send briefing email to the current user. Any authenticated user can send."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    from weatherbrief.db.models import UserRow
    from weatherbrief.notify.email import send_briefing_email

    # Build base URL for briefing link in email
    base_url = str(request.base_url).rstrip("/")
    if not is_dev_mode():
        base_url = base_url.replace("http://", "https://")

    try:
        user = db.query(UserRow).filter(UserRow.id == user_id).first()
        if not user or not user.email:
            raise HTTPException(
                status_code=400,
                detail="No email address on file. Update your profile to send emails.",
            )
        recipients = [user.email]
        send_briefing_email(recipients, flight, meta, pack_dir, base_url=base_url)
        return {"status": "sent", "recipients": recipients}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Email send failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Email send failed: {exc}" if is_dev_mode() else "Email send failed")


# --- Helpers ---


def _load_pack_meta_or_404(db: Session, flight_id: str, timestamp: str) -> BriefingPackMeta:
    """Load pack metadata or raise 404."""
    try:
        return load_pack_meta(db, flight_id, timestamp)
    except KeyError:
        raise HTTPException(status_code=404, detail="Pack not found")


def _get_pack_dir(db: Session, flight_id: str, timestamp: str, *, viewer_id: str | None = None):
    """Resolve the pack directory for any flight.

    Uses the stored artifact_path from the DB (authoritative), falling back
    to reconstructing the path from user_id/flight_id/timestamp if needed.
    """
    _load_flight_or_404(db, flight_id, viewer_id=viewer_id)
    # Prefer the stored artifact_path — it always matches the real directory.
    try:
        meta = load_pack_meta(db, flight_id, timestamp)
        if meta.artifact_path:
            p = Path(meta.artifact_path)
            if p.exists():
                return p
    except KeyError:
        pass
    # Fallback: reconstruct (works for new packs where timestamp has full precision)
    flight = _load_flight_or_404(db, flight_id, viewer_id=viewer_id)
    pack_path = pack_dir_for(flight.user_id, flight_id, timestamp)
    if not pack_path.exists():
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack_path


