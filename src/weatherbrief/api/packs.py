"""API endpoints for briefing packs (fetch history + refresh)."""

from __future__ import annotations

import asyncio
import json as json_mod
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.engine import SessionLocal
from weatherbrief.models import BriefingPackMeta
from weatherbrief.storage.flights import (
    list_packs,
    load_flight,
    load_pack_meta,
    pack_dir_for,
    save_pack_meta,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flights/{flight_id}/packs", tags=["packs"])


class PackMetaResponse(BaseModel):
    """Pack metadata in API responses."""

    flight_id: str
    fetch_timestamp: str
    days_out: int
    has_gramet: bool
    has_skewt: bool
    has_digest: bool
    assessment: str | None
    assessment_reason: str | None


def _meta_to_response(meta: BriefingPackMeta) -> PackMetaResponse:
    return PackMetaResponse(
        flight_id=meta.flight_id,
        fetch_timestamp=meta.fetch_timestamp,
        days_out=meta.days_out,
        has_gramet=meta.has_gramet,
        has_skewt=meta.has_skewt,
        has_digest=meta.has_digest,
        assessment=meta.assessment,
        assessment_reason=meta.assessment_reason,
    )


@router.get("", response_model=list[PackMetaResponse])
def list_flight_packs(flight_id: str, db: Session = Depends(get_db)):
    """List all packs (history) for a flight."""
    _ensure_flight_exists(db, flight_id)
    packs = list_packs(db, flight_id)
    return [_meta_to_response(p) for p in packs]


@router.get("/latest", response_model=PackMetaResponse)
def get_latest_pack(flight_id: str, db: Session = Depends(get_db)):
    """Get the most recent pack for a flight."""
    _ensure_flight_exists(db, flight_id)
    packs = list_packs(db, flight_id)
    if not packs:
        raise HTTPException(status_code=404, detail="No packs yet for this flight")
    return _meta_to_response(packs[0])


# --- Stage metadata for SSE progress ---

_STAGE_LABELS: dict[str, str] = {
    "route_interpolation": "Interpolating route",
    "fetch_forecasts": "Fetching forecasts",
    "waypoint_analysis": "Analyzing waypoints",
    "route_analysis": "Analyzing route points",
    "save_snapshot": "Saving snapshot",
    "fetch_gramet": "Fetching GRAMET",
    "generate_skewt": "Generating Skew-T",
    "llm_digest": "Generating AI digest",
}

_STAGE_PROGRESS: dict[str, float] = {
    "route_interpolation": 0.05,
    "fetch_forecasts": 0.40,
    "waypoint_analysis": 0.50,
    "route_analysis": 0.60,
    "save_snapshot": 0.65,
    "fetch_gramet": 0.75,
    "generate_skewt": 0.85,
    "llm_digest": 0.95,
}


def _prepare_refresh(flight, db_path, user_id, flight_id):
    """Shared setup for both sync and streaming refresh endpoints."""
    from weatherbrief.airports import resolve_waypoints
    from weatherbrief.models import RouteConfig
    from weatherbrief.pipeline import BriefingOptions

    if not flight.waypoints:
        raise ValueError("Flight has no waypoints defined")
    waypoint_objs = resolve_waypoints(flight.waypoints, db_path)
    route = RouteConfig(
        name=flight.route_name or " \u2192 ".join(flight.waypoints),
        waypoints=waypoint_objs,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
    )

    fetch_ts = datetime.now(tz=timezone.utc).isoformat()
    pack_path = pack_dir_for(user_id, flight_id, fetch_ts)
    pack_path.mkdir(parents=True, exist_ok=True)

    options = BriefingOptions(
        fetch_gramet=True,
        generate_skewt=False,
        generate_llm_digest=True,
        output_dir=pack_path,
    )

    return route, fetch_ts, pack_path, options


def _finalize_refresh(flight_id, flight, fetch_ts, pack_path, result, db):
    """Shared finalization: build and save pack metadata, return response."""
    days_out = (date.fromisoformat(flight.target_date) - date.today()).days

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
    )

    save_pack_meta(db, meta)
    logger.info("Briefing refreshed for %s: %s", flight_id, fetch_ts)
    return meta


@router.post("/refresh", response_model=PackMetaResponse, status_code=201)
def refresh_briefing(flight_id: str, request: Request, db: Session = Depends(get_db)):
    """Trigger a new briefing fetch for a flight."""
    flight = _load_flight_or_404(db, flight_id)
    user_id = current_user_id()

    db_path = request.app.state.db_path
    if not db_path:
        raise HTTPException(status_code=503, detail="AIRPORTS_DB not configured")

    try:
        from weatherbrief.pipeline import execute_briefing

        route, fetch_ts, pack_path, options = _prepare_refresh(
            flight, db_path, user_id, flight_id,
        )
        result = execute_briefing(
            route=route,
            target_date=flight.target_date,
            target_hour=flight.target_time_utc,
            options=options,
        )
        meta = _finalize_refresh(flight_id, flight, fetch_ts, pack_path, result, db)
        return _meta_to_response(meta)

    except ImportError as exc:
        logger.warning("Refresh failed (missing dependency): %s", exc)
        raise HTTPException(status_code=503, detail=f"Missing dependency: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Refresh failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Briefing fetch failed: {exc}")


_refresh_executor = ThreadPoolExecutor(max_workers=2)


@router.post("/refresh/stream")
async def refresh_briefing_stream(flight_id: str, request: Request):
    """Stream briefing refresh progress via Server-Sent Events."""
    # Manage our own DB session — FastAPI's Depends(get_db) cleanup
    # conflicts with the long-lived StreamingResponse.
    db = SessionLocal()
    try:
        flight = _load_flight_or_404(db, flight_id)
        user_id = current_user_id()

        db_path = request.app.state.db_path
        if not db_path:
            raise HTTPException(status_code=503, detail="AIRPORTS_DB not configured")

        route, fetch_ts, pack_path, options = _prepare_refresh(
            flight, db_path, user_id, flight_id,
        )
    except Exception:
        db.close()
        raise
    db.close()  # flight data is in memory; free the session before streaming

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
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def run_pipeline() -> None:
        try:
            from weatherbrief.pipeline import execute_briefing

            result = execute_briefing(
                route=route,
                target_date=flight.target_date,
                target_hour=flight.target_time_utc,
                options=options,
                progress_callback=progress_callback,
            )
            # Use a dedicated DB session — the request-scoped one isn't thread-safe
            thread_db = SessionLocal()
            try:
                meta = _finalize_refresh(flight_id, flight, fetch_ts, pack_path, result, thread_db)
                thread_db.commit()
            finally:
                thread_db.close()
            complete_event = {
                "type": "complete",
                "pack": _meta_to_response(meta).model_dump(),
            }
            asyncio.run_coroutine_threadsafe(queue.put(complete_event), loop)
        except Exception as exc:
            logger.error("Streaming refresh failed: %s", exc, exc_info=True)
            error_event = {"type": "error", "message": str(exc)}
            asyncio.run_coroutine_threadsafe(queue.put(error_event), loop)

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


@router.get("/{timestamp}", response_model=PackMetaResponse)
def get_pack(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Get a specific pack's metadata."""
    _ensure_flight_exists(db, flight_id)
    try:
        meta = load_pack_meta(db, flight_id, timestamp)
    except KeyError:
        raise HTTPException(status_code=404, detail="Pack not found")
    return _meta_to_response(meta)


@router.get("/{timestamp}/snapshot")
def get_snapshot(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Get the raw ForecastSnapshot JSON for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    snapshot_path = pack_dir / "snapshot.json"
    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(snapshot_path, media_type="application/json")


@router.get("/{timestamp}/gramet")
def get_gramet(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Get the GRAMET image for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    gramet_path = pack_dir / "gramet.png"
    if not gramet_path.exists():
        raise HTTPException(status_code=404, detail="GRAMET not available")
    return FileResponse(gramet_path, media_type="image/png")


@router.get("/{timestamp}/skewt/{icao}/{model}")
def get_skewt(
    flight_id: str, timestamp: str, icao: str, model: str,
    db: Session = Depends(get_db),
):
    """Get a specific Skew-T image."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    skewt_path = pack_dir / "skewt" / f"{icao}_{model}.png"
    if not skewt_path.exists():
        raise HTTPException(status_code=404, detail="Skew-T not available")
    return FileResponse(skewt_path, media_type="image/png")


@router.get("/{timestamp}/route-analyses")
def get_route_analyses(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Get the route analyses JSON for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    ra_path = pack_dir / "route_analyses.json"
    if not ra_path.exists():
        raise HTTPException(status_code=404, detail="Route analyses not available")
    return FileResponse(ra_path, media_type="application/json")


@router.get("/{timestamp}/skewt/route/{point_index}/{model}")
def get_route_skewt(
    flight_id: str, timestamp: str, point_index: int, model: str,
    db: Session = Depends(get_db),
):
    """Get an on-demand Skew-T for a route point.

    Generates and caches the PNG on first request.
    """
    pack_dir = _get_pack_dir(db, flight_id, timestamp)

    # Cache path
    cache_dir = pack_dir / "skewt" / "route"
    cache_path = cache_dir / f"pt{point_index:02d}_{model}.png"
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/png")

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

    # Generate Skew-T
    try:
        from weatherbrief.digest.skewt import generate_skewt
        label = point_data.get("waypoint_icao") or f"pt{point_index:02d}"
        generate_skewt(hourly, label, model, cache_path)
    except Exception as exc:
        logger.warning("Route Skew-T generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Skew-T generation failed: {exc}")

    return FileResponse(cache_path, media_type="image/png")


@router.get("/{timestamp}/digest")
def get_digest(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Get the LLM digest markdown for a pack."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    digest_path = pack_dir / "digest.md"
    if not digest_path.exists():
        raise HTTPException(status_code=404, detail="Digest not available")
    return FileResponse(digest_path, media_type="text/markdown")


@router.get("/{timestamp}/digest/json")
def get_digest_json(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Get the structured LLM digest as JSON."""
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    json_path = pack_dir / "digest.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Structured digest not available")
    return FileResponse(json_path, media_type="application/json")


@router.get("/{timestamp}/report.html")
def get_report_html(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """View a self-contained HTML briefing report."""
    flight = _load_flight_or_404(db, flight_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    from weatherbrief.report.render import render_html

    html = render_html(pack_dir, flight, meta)
    return HTMLResponse(content=html)


@router.get("/{timestamp}/report.pdf")
def get_report_pdf(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Download a PDF briefing report."""
    flight = _load_flight_or_404(db, flight_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    from weatherbrief.report.render import render_pdf

    import re

    pdf_bytes = render_pdf(pack_dir, flight, meta)
    route_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", flight.route_name or "-".join(flight.waypoints))
    filename = f"briefing_{route_slug}_{flight.target_date}_d{meta.days_out}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{timestamp}/email")
def send_email(flight_id: str, timestamp: str, db: Session = Depends(get_db)):
    """Send briefing email to configured recipients."""
    flight = _load_flight_or_404(db, flight_id)
    pack_dir = _get_pack_dir(db, flight_id, timestamp)
    meta = _load_pack_meta_or_404(db, flight_id, timestamp)

    from weatherbrief.notify.email import SmtpConfig, get_recipients, send_briefing_email

    try:
        recipients = get_recipients()
        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="No recipients configured. Set WEATHERBRIEF_EMAIL_RECIPIENTS.",
            )
        send_briefing_email(recipients, flight, meta, pack_dir)
        return {"status": "sent", "recipients": recipients}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Email send failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Email send failed: {exc}")


# --- Helpers ---


def _load_flight_or_404(db: Session, flight_id: str):
    """Load a flight or raise 404."""
    try:
        return load_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")


def _ensure_flight_exists(db: Session, flight_id: str) -> None:
    _load_flight_or_404(db, flight_id)


def _load_pack_meta_or_404(db: Session, flight_id: str, timestamp: str) -> BriefingPackMeta:
    """Load pack metadata or raise 404."""
    try:
        return load_pack_meta(db, flight_id, timestamp)
    except KeyError:
        raise HTTPException(status_code=404, detail="Pack not found")


def _get_pack_dir(db: Session, flight_id: str, timestamp: str):
    _ensure_flight_exists(db, flight_id)
    user_id = current_user_id()
    pack_path = pack_dir_for(user_id, flight_id, timestamp)
    if not pack_path.exists():
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack_path
