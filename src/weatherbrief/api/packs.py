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
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from flyfun_common.auth import is_dev_mode
from weatherbrief.api.security import audit_pack_access
from weatherbrief.db.models import BriefingUsageRow
from weatherbrief.api.throttle import generation_slot, pdf_limiter, plot_limiter

# Input validation for path-sensitive parameters
_ICAO_RE = re.compile(r"^[A-Z]{4}$", re.IGNORECASE)
_MODEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_icao(icao: str) -> str:
    """Validate and sanitize ICAO code used in file paths."""
    if not _ICAO_RE.match(icao):
        raise HTTPException(status_code=400, detail="Invalid ICAO code")
    return icao


def _validate_model(model: str) -> str:
    """Validate and sanitize model name used in file paths."""
    if not _MODEL_RE.match(model):
        raise HTTPException(status_code=400, detail="Invalid model name")
    return model
from weatherbrief.api.flights import _load_flight_or_404, _load_owned_flight
from flyfun_common.db import current_user_id, get_db, SessionLocal
from weatherbrief.fetch.model_status import (
    check_freshness,
    compute_next_update,
    fetch_model_metadata,
)
from weatherbrief.models import BriefingPackMeta
from weatherbrief.storage.flights import (
    list_packs,
    load_pack_meta,
    pack_dir_for,
    save_pack_meta,
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


class DataStatus(BaseModel):
    """Model freshness status."""

    fresh: bool  # True = all models up to date
    stale_models: list[str] = Field(default_factory=list)
    model_init_times: dict[str, int] = Field(default_factory=dict)  # current live init times
    next_expected_update: str | None = None  # ISO datetime
    next_expected_model: str | None = None  # which model updates next


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
    diagnostics: list[dict] = Field(default_factory=list)
    data_status: DataStatus | None = None


def _meta_to_response(
    meta: BriefingPackMeta,
    data_status: DataStatus | None = None,
) -> PackMetaResponse:
    # Check for advisories file on disk
    has_advisories = False
    if meta.artifact_path:
        from pathlib import Path
        has_advisories = (Path(meta.artifact_path) / "route_advisories.json").exists()

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
        diagnostics=meta.diagnostics,
        data_status=data_status,
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
    _load_flight_or_404(db, flight_id, viewer_id=user_id)
    packs = list_packs(db, flight_id)
    if not packs:
        return DataStatus(fresh=False)

    latest = packs[0]
    return _build_data_status(latest.model_init_times)


def _build_data_status(stored_init_times: dict[str, int]) -> DataStatus:
    """Fetch live metadata and compare against stored init times."""
    models_to_check = list(stored_init_times.keys()) if stored_init_times else None
    live = fetch_model_metadata(models_to_check)
    is_fresh, stale = check_freshness(stored_init_times, live)
    next_time, next_model = compute_next_update(live)

    return DataStatus(
        fresh=is_fresh,
        stale_models=stale,
        model_init_times={m: meta.last_init_time for m, meta in live.items()},
        next_expected_update=next_time.isoformat() if next_time else None,
        next_expected_model=next_model,
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
    "grib_enrichment": "Enriching with GRIB2 data",
    "waypoint_analysis": "Analyzing waypoints",
    "route_analysis": "Analyzing route points",
    "route_advisories": "Evaluating route advisories",
    "alt_advisories": "Evaluating alt-time advisories",
    "route_weather": "Fetching METAR/TAF observations",
    "save_snapshot": "Saving snapshot",
    "fetch_gramet": "Fetching GRAMET",
    "generate_skewt": "Generating Skew-T",
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
    "llm_digest": 0.95,
}


def _build_route_config(flight, db_path):
    """Build a RouteConfig from a flight, resolving waypoints."""
    from weatherbrief.airports import resolve_waypoints
    from weatherbrief.models import RouteConfig

    if not flight.waypoints:
        raise ValueError("Flight has no waypoints defined")
    waypoint_objs = resolve_waypoints(flight.waypoints, db_path)
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
    locale = None
    if db is not None:
        from weatherbrief.api.preferences import load_autorouter_credentials, load_user_locale
        from weatherbrief.api.profiles import load_profile_settings

        autorouter_creds = load_autorouter_credentials(db, user_id)
        locale = load_user_locale(db, user_id)
        profile_settings = load_profile_settings(db, flight.profile_id, user_id)

        profile_models = profile_settings.get("models")
        if profile_models:
            valid = {m.value for m in ModelSource}
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
        autorouter_credentials=autorouter_creds,
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

        config = config_from_row(config_row)
        breakdown = compute_cost(
            input_tokens=usage.llm_input_tokens or 0,
            output_tokens=usage.llm_output_tokens or 0,
            result_size_bytes=result_size_bytes,
            config=config,
        )
        charge_briefing(db, user_id, usage_row_id, breakdown)
        logger.info(
            "Charged %.2f credits to %s (usage #%d)",
            breakdown.credits_charged, user_id, usage_row_id,
        )
    except Exception:
        logger.warning("Failed to charge briefing cost for %s", user_id, exc_info=True)


def _finalize_refresh(flight_id, flight, fetch_ts, pack_path, result, db,
                      user_id=None, model_metadata=None, as_of_time=None):
    """Shared finalization: build and save pack metadata, log usage, return response."""
    if as_of_time:
        days_out = (flight.departure_time.date() - as_of_time.date()).days
    else:
        days_out = (flight.departure_time.date() - datetime.now(timezone.utc).date()).days

    init_times = {}
    if model_metadata:
        init_times = {m: meta.last_init_time for m, meta in model_metadata.items()}

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

    meta = BriefingPackMeta(
        flight_id=flight_id,
        fetch_timestamp=fetch_ts,
        days_out=days_out,
        has_gramet=result.gramet_path is not None,
        has_skewt=len(result.skewt_paths) > 0,
        has_digest=result.digest_path is not None,
        assessment=result.digest.assessment if result.digest else None,
        assessment_reason=result.digest.assessment_reason if result.digest else None,
        artifact_path=str(pack_path),
        model_init_times=init_times,
        grib_init_times=result.grib_init_times,
        models_skipped_region=result.models_skipped_region,
        diagnostics=result.diagnostics,
        alt_assessment=alt_assessment,
        alt_assessment_reason=alt_assessment_reason,
        has_alt_advisories=has_alt_advisories,
    )

    save_pack_meta(db, meta)

    # Log usage and charge credits
    if user_id is not None:
        from weatherbrief.api.usage import log_briefing_usage

        pack_size = _measure_pack_size(pack_path)
        usage_row_id = log_briefing_usage(
            db, user_id, flight_id, result.usage, result_size_bytes=pack_size,
        )
        _charge_briefing_cost(db, user_id, result.usage, pack_size, usage_row_id)

    logger.info("Briefing refreshed for %s: %s", flight_id, fetch_ts)
    return meta


@router.post("/refresh", response_model=PackMetaResponse)
async def refresh_briefing(
    flight_id: str,
    request: Request,
    force: bool = False,
    as_of_date: str | None = None,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Trigger a new briefing fetch for a flight.

    Checks model freshness first and skips the pipeline if data
    hasn't changed.  Pass ``?force=true`` (admin/dev only) to bypass.
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

    # Smart check: skip pipeline if data is fresh (skip for historical flights)
    if not force and not is_historical:
        packs = list_packs(db, flight_id)
        if packs:
            latest = packs[0]
            status = _build_data_status(latest.model_init_times)
            if status.fresh:
                logger.info("Data is fresh for %s, skipping pipeline", flight_id)
                return _meta_to_response(latest, data_status=status)

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

    try:
        from weatherbrief.pipeline import execute_briefing

        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, db_path, user_id, flight_id, db=db,
            is_privileged=_can_force_refresh(request, db),
            as_of_time=as_of_time,
        )

        refresh_registry.set_refreshing(flight_id)

        result = await asyncio.get_event_loop().run_in_executor(
            _refresh_executor,
            lambda: execute_briefing(
                route=route,
                departure_time=flight.departure_time,
                options=options,
            ),
        )
        # Capture timing before unregister
        queue_wait, total_elapsed = refresh_registry.get_timing(flight_id)
        result.usage.elapsed_seconds = total_elapsed
        result.usage.queue_wait_seconds = queue_wait
        result.usage.triggered_by = "user"

        meta = _finalize_refresh(flight_id, flight, fetch_ts, pack_path, result, db,
                                 user_id=user_id, model_metadata=model_metadata,
                                 as_of_time=as_of_time)
        return _meta_to_response(meta)

    except ImportError as exc:
        logger.warning("Refresh failed (missing dependency): %s", exc)
        raise HTTPException(status_code=503, detail=f"Missing dependency: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Refresh failed: %s", exc, exc_info=True)
        detail = f"Briefing fetch failed: {exc}" if is_dev_mode() else "Briefing fetch failed"
        raise HTTPException(status_code=500, detail=detail)
    finally:
        refresh_registry.unregister(flight_id)


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

        # Smart check: skip pipeline if data is fresh (skip for historical flights)
        if not force and not is_historical:
            packs = list_packs(db, flight_id)
            if packs:
                latest = packs[0]
                status = _build_data_status(latest.model_init_times)
                if status.fresh:
                    logger.info("Data is fresh for %s, skipping pipeline (stream)", flight_id)
                    pack_resp = _meta_to_response(latest, data_status=status).model_dump()

                    async def fresh_generator() -> AsyncGenerator[str, None]:
                        event = {"type": "complete", "pack": pack_resp}
                        yield f"event: complete\ndata: {json_mod.dumps(event)}\n\n"

                    return StreamingResponse(
                        fresh_generator(),
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
                yield f"event: error\ndata: {json_mod.dumps(event)}\n\n"

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
                yield f"event: error\ndata: {json_mod.dumps(event)}\n\n"

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
        label = _STAGE_LABELS.get(stage, stage)
        progress = _STAGE_PROGRESS.get(stage, 0.0)
        event = {
            "type": "progress",
            "stage": stage,
            "detail": detail,
            "label": label,
            "progress": progress,
        }
        refresh_registry.update_progress(flight_id, stage, detail)
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def run_pipeline() -> None:
        refresh_registry.set_refreshing(flight_id)
        try:
            from weatherbrief.pipeline import execute_briefing

            result = execute_briefing(
                route=route,
                departure_time=flight.departure_time,
                options=options,
                progress_callback=progress_callback,
            )
            # Capture timing before unregister
            queue_wait, total_elapsed = refresh_registry.get_timing(flight_id)
            result.usage.elapsed_seconds = total_elapsed
            result.usage.queue_wait_seconds = queue_wait
            result.usage.triggered_by = "user"

            # Use a dedicated DB session — the request-scoped one isn't thread-safe
            thread_db = SessionLocal()
            try:
                meta = _finalize_refresh(flight_id, flight, fetch_ts, pack_path, result, thread_db,
                                         user_id=user_id, model_metadata=model_metadata,
                                         as_of_time=as_of_time)
                thread_db.commit()
            finally:
                thread_db.close()
            complete_event = {
                "type": "complete",
                "pack": _meta_to_response(meta).model_dump(),
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

    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            event = await queue.get()
            event_type = event.get("type", "progress")
            data = json_mod.dumps(event)
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
    """Re-fetch METAR/TAF observations and update snapshot without full pipeline.

    Only available for D-0 briefings where observations are meaningful.
    Re-runs route_weather + observation_comparison using existing forecast data,
    then patches the snapshot on disk.
    """
    flight = _load_owned_flight(db, flight_id, user_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    from weatherbrief.tasks.artifacts import load_briefing, load_forecasts as load_forecasts_json

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
        from weatherbrief.models import ForecastSnapshot, RouteConfig, WaypointForecast
        from weatherbrief.models.observations import RouteObservations
        from weatherbrief.tasks.route_weather import (
            run_observation_comparison,
            run_route_weather,
        )

        route = RouteConfig.model_validate(briefing_data["route"])
        target_dt = _parse_target_time(briefing_data)

        # Options for corridor width
        corridor_nm = 30.0
        if briefing_data.get("route_observations"):
            corridor_nm = briefing_data["route_observations"].get("corridor_nm", 30.0)

        # Fetch fresh METAR/TAF
        new_obs = run_route_weather(
            route=route,
            target_time=target_dt,
            corridor_nm=corridor_nm,
            airports_db_path=db_path,
        )

        # Run observation-vs-model comparison using stored forecasts
        forecasts_data = load_forecasts_json(pack_dir)
        forecasts = [
            WaypointForecast.model_validate(f)
            for f in (forecasts_data or {}).get("forecasts", [])
        ]

        # Load route analyses for sounding ceiling data
        route_analyses = None
        ra_path = pack_dir / "route_analyses.json"
        if ra_path.exists():
            from weatherbrief.tasks.artifacts import load_route_analyses
            ra_manifest = load_route_analyses(pack_dir)
            route_analyses = ra_manifest.analyses

        # Runway data for wind advisory comparison
        obs_runway_data = None
        try:
            from weatherbrief.airports import get_runway_ends
            obs_icaos = list({a.icao for a in new_obs.airports})
            obs_runway_data = get_runway_ends(obs_icaos, db_path)
        except Exception:
            pass

        new_obs = run_observation_comparison(
            observations=new_obs,
            snapshot_forecasts=forecasts,
            target_time=target_dt,
            route=route,
            runway_data=obs_runway_data,
            route_analyses=route_analyses,
        )

        # Patch briefing on disk (prefer briefing.json, fall back to snapshot.json)
        briefing_data["route_observations"] = new_obs.model_dump(mode="json")
        briefing_path = pack_dir / "briefing.json"
        if briefing_path.exists():
            briefing_path.write_text(json_mod.dumps(briefing_data, indent=2))
        else:
            snapshot_path = pack_dir / "snapshot.json"
            snapshot_path.write_text(json_mod.dumps(briefing_data, indent=2))

        return new_obs.model_dump(mode="json")

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


@router.post("/{timestamp}/advisories/recalculate")
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

    return advisory_result.manifest.model_dump()


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


class SoundingProfileLevel(BaseModel):
    """A single pressure level in a sounding profile."""
    pressure_hpa: int
    altitude_ft: float | None = None
    temperature_c: float
    dewpoint_c: float | None = None
    wind_speed_kt: float | None = None
    wind_direction_deg: float | None = None


class SoundingProfileResponse(BaseModel):
    """Raw sounding profile data for client-side Skew-T rendering."""
    point_index: int
    lat: float
    lon: float
    distance_from_origin_nm: float
    waypoint_icao: str | None = None
    model: str
    time: str
    levels: list[SoundingProfileLevel]
    cruise_altitude_ft: int | None = None
    # Overlay data from sounding analysis
    indices: dict | None = None
    cloud_layers: list[dict] = Field(default_factory=list)
    icing_zones: list[dict] = Field(default_factory=list)
    inversion_layers: list[dict] = Field(default_factory=list)


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

    # Load route analyses for point metadata + sounding analysis
    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")
    ra_data = json.loads(ra_path.read_text())
    analyses = ra_data.get("analyses", [])
    point_data = next((a for a in analyses if a["point_index"] == point_index), None)
    if point_data is None:
        raise HTTPException(status_code=404, detail=f"Point index {point_index} not found")

    # Load cross-section for raw pressure level data
    cs_path = pack_dir / "cross_section.json"
    if not cs_path.exists():
        raise HTTPException(status_code=404, detail="Cross-section data not available")
    cs_data = json.loads(cs_path.read_text())
    cross_sections = cs_data.get("cross_sections", [])
    cs_match = next((cs for cs in cross_sections if cs["model"] == model), None)
    if cs_match is None:
        raise HTTPException(status_code=404, detail=f"Model {model} not found")
    if point_index >= len(cs_match["point_forecasts"]):
        raise HTTPException(status_code=404, detail=f"Point index {point_index} out of range")

    # Extract raw profile at interpolated time
    from weatherbrief.models import WaypointForecast
    wf = WaypointForecast.model_validate(cs_match["point_forecasts"][point_index])
    interp_time = datetime.fromisoformat(point_data["interpolated_time"])
    hourly = wf.at_time(interp_time)
    if not hourly or not hourly.pressure_levels:
        raise HTTPException(status_code=404, detail="No forecast data at this point/time")

    # Convert pressure levels to response format
    levels = []
    for pl in sorted(hourly.pressure_levels, key=lambda x: x.pressure_hpa, reverse=True):
        if pl.temperature_c is None:
            continue
        alt_ft = pl.geopotential_height_m * 3.28084 if pl.geopotential_height_m is not None else None
        levels.append(SoundingProfileLevel(
            pressure_hpa=pl.pressure_hpa,
            altitude_ft=alt_ft,
            temperature_c=pl.temperature_c,
            dewpoint_c=pl.dewpoint_c,
            wind_speed_kt=pl.wind_speed_kt,
            wind_direction_deg=pl.wind_direction_deg,
        ))

    # Extract overlay data from sounding analysis
    sounding_data = point_data.get("sounding", {}).get(model, {})

    return SoundingProfileResponse(
        point_index=point_index,
        lat=point_data["lat"],
        lon=point_data["lon"],
        distance_from_origin_nm=point_data["distance_from_origin_nm"],
        waypoint_icao=point_data.get("waypoint_icao"),
        model=model,
        time=point_data["interpolated_time"],
        levels=levels,
        cruise_altitude_ft=ra_data.get("cruise_altitude_ft"),
        indices=sounding_data.get("indices"),
        cloud_layers=sounding_data.get("cloud_layers", []),
        icing_zones=sounding_data.get("icing_zones", []),
        inversion_layers=sounding_data.get("inversion_layers", []),
    )


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


def _parse_target_time(snapshot_data: dict) -> datetime:
    """Extract target datetime from snapshot JSON data.

    Returns a timezone-aware UTC datetime.  Old packs that stored naive
    timestamps are promoted to aware UTC so comparisons against both
    old (naive HourlyForecast.time) and new (aware) data work correctly
    — ``at_time()`` is patched below to handle the mixed case gracefully.
    """
    # Prefer departure_time (new packs)
    dt_str = snapshot_data.get("departure_time")
    if dt_str:
        dt = datetime.fromisoformat(dt_str)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    # Fallback: first analysis target_time
    analyses = snapshot_data.get("analyses", [])
    if analyses and "target_time" in analyses[0]:
        dt = datetime.fromisoformat(analyses[0]["target_time"])
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    target_date = snapshot_data.get("target_date", "")
    year, month, day = (int(x) for x in target_date.split("-"))
    return datetime(year, month, day, 9, tzinfo=timezone.utc)
