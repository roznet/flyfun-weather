"""API endpoints for briefing packs (fetch history + refresh)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

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
def list_flight_packs(flight_id: str):
    """List all packs (history) for a flight."""
    _ensure_flight_exists(flight_id)
    packs = list_packs(flight_id)
    return [_meta_to_response(p) for p in packs]


@router.get("/latest", response_model=PackMetaResponse)
def get_latest_pack(flight_id: str):
    """Get the most recent pack for a flight."""
    _ensure_flight_exists(flight_id)
    packs = list_packs(flight_id)
    if not packs:
        raise HTTPException(status_code=404, detail="No packs yet for this flight")
    return _meta_to_response(packs[0])


@router.get("/{timestamp}", response_model=PackMetaResponse)
def get_pack(flight_id: str, timestamp: str):
    """Get a specific pack's metadata."""
    _ensure_flight_exists(flight_id)
    try:
        meta = load_pack_meta(flight_id, timestamp)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Pack not found")
    return _meta_to_response(meta)


@router.get("/{timestamp}/snapshot")
def get_snapshot(flight_id: str, timestamp: str):
    """Get the raw ForecastSnapshot JSON for a pack."""
    pack_dir = _get_pack_dir(flight_id, timestamp)
    snapshot_path = pack_dir / "snapshot.json"
    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(snapshot_path, media_type="application/json")


@router.get("/{timestamp}/gramet")
def get_gramet(flight_id: str, timestamp: str):
    """Get the GRAMET image for a pack."""
    pack_dir = _get_pack_dir(flight_id, timestamp)
    gramet_path = pack_dir / "gramet.png"
    if not gramet_path.exists():
        raise HTTPException(status_code=404, detail="GRAMET not available")
    return FileResponse(gramet_path, media_type="image/png")


@router.get("/{timestamp}/skewt/{icao}/{model}")
def get_skewt(flight_id: str, timestamp: str, icao: str, model: str):
    """Get a specific Skew-T image."""
    pack_dir = _get_pack_dir(flight_id, timestamp)
    skewt_path = pack_dir / "skewt" / f"{icao}_{model}.png"
    if not skewt_path.exists():
        raise HTTPException(status_code=404, detail="Skew-T not available")
    return FileResponse(skewt_path, media_type="image/png")


@router.get("/{timestamp}/digest")
def get_digest(flight_id: str, timestamp: str):
    """Get the LLM digest markdown for a pack."""
    pack_dir = _get_pack_dir(flight_id, timestamp)
    digest_path = pack_dir / "digest.md"
    if not digest_path.exists():
        raise HTTPException(status_code=404, detail="Digest not available")
    return FileResponse(digest_path, media_type="text/markdown")


@router.get("/{timestamp}/digest/json")
def get_digest_json(flight_id: str, timestamp: str):
    """Get the structured LLM digest as JSON."""
    pack_dir = _get_pack_dir(flight_id, timestamp)
    json_path = pack_dir / "digest.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Structured digest not available")
    return FileResponse(json_path, media_type="application/json")


@router.get("/{timestamp}/report.html")
def get_report_html(flight_id: str, timestamp: str):
    """View a self-contained HTML briefing report."""
    flight = _load_flight_or_404(flight_id)
    pack_dir = _get_pack_dir(flight_id, timestamp)
    meta = _load_pack_meta_or_404(flight_id, timestamp)

    from weatherbrief.report.render import render_html

    html = render_html(pack_dir, flight, meta)
    return HTMLResponse(content=html)


@router.get("/{timestamp}/report.pdf")
def get_report_pdf(flight_id: str, timestamp: str):
    """Download a PDF briefing report."""
    flight = _load_flight_or_404(flight_id)
    pack_dir = _get_pack_dir(flight_id, timestamp)
    meta = _load_pack_meta_or_404(flight_id, timestamp)

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
def send_email(flight_id: str, timestamp: str):
    """Send briefing email to configured recipients."""
    flight = _load_flight_or_404(flight_id)
    pack_dir = _get_pack_dir(flight_id, timestamp)
    meta = _load_pack_meta_or_404(flight_id, timestamp)

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


@router.post("/refresh", response_model=PackMetaResponse, status_code=201)
def refresh_briefing(flight_id: str, request: Request):
    """Trigger a new briefing fetch for a flight.

    Runs the pipeline, saves all artifacts as a new pack, returns metadata.
    """
    flight = _load_flight_or_404(flight_id)

    db_path = request.app.state.db_path
    if not db_path:
        raise HTTPException(status_code=503, detail="AIRPORTS_DB not configured")

    try:
        from weatherbrief.airports import resolve_waypoints
        from weatherbrief.models import RouteConfig
        from weatherbrief.pipeline import BriefingOptions, execute_briefing

        # Resolve waypoints from flight definition
        if not flight.waypoints:
            raise ValueError("Flight has no waypoints defined")
        waypoint_objs = resolve_waypoints(flight.waypoints, db_path)
        route = RouteConfig(
            name=flight.route_name or " → ".join(flight.waypoints),
            waypoints=waypoint_objs,
            cruise_altitude_ft=flight.cruise_altitude_ft,
            flight_ceiling_ft=flight.flight_ceiling_ft,
            flight_duration_hours=flight.flight_duration_hours,
        )

        # Determine pack directory up front so pipeline writes directly there
        fetch_ts = datetime.now(tz=timezone.utc).isoformat()
        pack_dir = pack_dir_for(flight_id, fetch_ts)
        pack_dir.mkdir(parents=True, exist_ok=True)

        options = BriefingOptions(
            fetch_gramet=True,
            generate_skewt=True,
            generate_llm_digest=True,
            output_dir=pack_dir,
        )

        result = execute_briefing(
            route=route,
            target_date=flight.target_date,
            target_hour=flight.target_time_utc,
            options=options,
        )

        # Build and save pack metadata — artifacts already in pack_dir
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
        )

        save_pack_meta(meta)
        logger.info("Briefing refreshed for %s: %s", flight_id, fetch_ts)

        return _meta_to_response(meta)

    except ImportError as exc:
        logger.warning("Refresh failed (missing dependency): %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Missing dependency for route resolution: {exc}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Refresh failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Briefing fetch failed: {exc}")


# --- Helpers ---


def _load_flight_or_404(flight_id: str):
    """Load a flight or raise 404."""
    try:
        return load_flight(flight_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")


def _ensure_flight_exists(flight_id: str) -> None:
    _load_flight_or_404(flight_id)


def _load_pack_meta_or_404(flight_id: str, timestamp: str) -> BriefingPackMeta:
    """Load pack metadata or raise 404."""
    try:
        return load_pack_meta(flight_id, timestamp)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Pack not found")


def _get_pack_dir(flight_id: str, timestamp: str):
    _ensure_flight_exists(flight_id)
    pack_dir = pack_dir_for(flight_id, timestamp)
    if not pack_dir.exists():
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack_dir
