"""ChatGPT Custom GPT / OpenAPI front-door for the weather briefing tools.

This is the ChatGPT-native sibling of the Claude MCP server
(``weatherbrief.mcp.server``): the same seven capabilities, exposed as a small,
self-contained REST surface that a ChatGPT Custom GPT calls via an OpenAPI
*Action*. The two front-doors share their response *shaping* and meteorological
guardrails through ``weatherbrief.connectors.views`` so the Claude and ChatGPT
integrations cannot drift.

Reuse strategy (in-process, no localhost loopback):

* **Read** endpoints call the same helpers the main REST handlers use
  (``_get_pack_dir``, ``list_packs``, ``_build_data_status``, ``decide_refresh``)
  and read the pack JSON artifacts directly, then apply the shared shapers.
* **Write** endpoints (create / refresh) call the existing route handlers
  directly with every dependency passed explicitly — reusing all the
  throttling, gating and background-task machinery with zero duplication.

Mounted at ``/agent/v1``. Its OpenAPI schema (served at ``/agent/v1/openapi.json``
by ``app.py``) is the artifact pasted into the Custom GPT builder. Auth is the
same OAuth-bearer / api-token stack as the rest of the app, using the existing
``mcp`` scope.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from weatherbrief.api import flights as flights_api
from weatherbrief.api import maps as maps_api
from weatherbrief.api import packs as packs_api
from weatherbrief.connectors import views

WEATHER_BASE_URL = os.getenv("WEATHER_BASE_URL", "https://weather.flyfun.aero")

router = APIRouter(prefix="/agent/v1", tags=["agent"])


# ---------------------------------------------------------------------------
# Small in-process helpers (shared shaping lives in connectors.views)
# ---------------------------------------------------------------------------

def _flight_web_url(flight_id: str) -> str:
    return f"{WEATHER_BASE_URL}/briefing.html?flight={flight_id}"


def _read_json(pack_dir: Path, name: str) -> Any | None:
    """Read and parse a pack JSON artifact, or None if absent."""
    path = pack_dir / name
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _read_text(pack_dir: Path, name: str) -> str | None:
    path = pack_dir / name
    if not path.exists():
        return None
    return path.read_text()


def _processing(flight_id: str) -> dict[str, Any]:
    return {
        "status": "processing",
        "flight_id": flight_id,
        "message": "Briefing is being generated. Check again in ~1-2 minutes.",
        "web_url": _flight_web_url(flight_id),
    }


def _none_status(flight_id: str) -> dict[str, Any]:
    return {
        "status": "none",
        "flight_id": flight_id,
        "message": "No briefing exists yet. Call refresh_briefing to generate one.",
        "web_url": _flight_web_url(flight_id),
    }


def _resolve_latest_pack(db: Session, user_id: str, flight_id: str):
    """Resolve the latest pack for a flight, or a status dict.

    Returns ``(pack_meta, timestamp, None)`` when a pack exists, or
    ``(None, None, status)`` when a refresh is running / no pack exists — the
    status dict mirrors the MCP early-return shapes so callers return it
    directly. Mirrors ``mcp.server._resolve_pack`` but in-process.
    """
    refresh = packs_api.get_refresh_status(flight_id=flight_id, user_id=user_id)
    if refresh.get("active"):
        return None, None, _processing(flight_id)

    packs = packs_api.list_packs(db, flight_id)
    if not packs:
        return None, None, _none_status(flight_id)
    pack = packs[0]
    return pack, pack.fetch_timestamp.isoformat(), None


def _freshness_dict(db: Session, user_id: str, flight_id: str) -> dict[str, Any]:
    """Build the ``/packs/freshness`` payload in-process (DataStatus + gate)."""
    flight = flights_api._load_flight_or_404(db, flight_id, viewer_id=user_id)
    packs = packs_api.list_packs(db, flight_id)
    if not packs:
        return {"fresh": False}
    status = packs_api._build_data_status(packs[0], flight)
    status.refresh_decision = packs_api.decide_refresh(status, packs_api._days_out_now(flight))
    return status.model_dump()


# ---------------------------------------------------------------------------
# Request / lightweight response models (for a clean OpenAPI)
# ---------------------------------------------------------------------------

class CreateFlightInput(BaseModel):
    """A new flight to plan a briefing for."""

    waypoints: list[str] = Field(
        ...,
        min_length=2,
        description="Route waypoints as ICAO codes or navaid names, e.g. ['LFBO', 'LFML']. Minimum 2.",
    )
    departure_time: str = Field(
        ...,
        description="Departure time as ISO 8601 with timezone, e.g. '2026-04-10T14:00:00+02:00'.",
    )
    flight_duration_hours: float = Field(
        ..., description="Expected flight duration in hours, e.g. 1.5.",
    )
    cruise_altitude_ft: int | None = Field(
        None,
        description="Cruise altitude in feet (default: from user profile, typically 8000).",
    )


# ---------------------------------------------------------------------------
# GET /agent/v1/flights  — list_flights
# ---------------------------------------------------------------------------

@router.get("/flights", operation_id="listFlights")
def list_flights(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List the user's upcoming flights with briefing status.

    Returns each flight's route, departure time, and weather assessment
    (GREEN/AMBER/RED) from the latest briefing. Use this to see which flights
    need attention or have stale briefings.
    """
    flights = flights_api.list_all_flights(
        response=Response(), past_limit=None, past_offset=0, user_id=user_id, db=db,
    )
    result = []
    for f in flights:
        entry: dict[str, Any] = {
            "id": f.id,
            "route_name": f.route_name,
            "waypoints": f.waypoints,
            "departure_time": f.departure_time,
            "cruise_altitude_ft": f.cruise_altitude_ft,
            "web_url": _flight_web_url(f.id),
        }
        lb = f.latest_briefing
        if lb is not None:
            entry["assessment"] = lb.assessment
            entry["assessment_reason"] = lb.assessment_reason
            entry["has_digest"] = lb.has_digest
            entry["briefing_timestamp"] = lb.fetch_timestamp
            entry["days_out"] = lb.days_out
        else:
            entry["assessment"] = None
            entry["briefing_timestamp"] = None
        result.append(entry)
    return {"flights": result}


# ---------------------------------------------------------------------------
# POST /agent/v1/flights  — create_flight (+ auto-refresh)
# ---------------------------------------------------------------------------

@router.post("/flights", operation_id="createFlight")
async def create_flight(
    body: CreateFlightInput,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a new flight and automatically trigger a weather briefing.

    The briefing generation takes ~2 minutes. The response includes the flight
    details and a processing status. Call get_briefing after a couple of minutes
    to retrieve the results.
    """
    req = flights_api.CreateFlightRequest(
        waypoints=body.waypoints,
        departure_time=body.departure_time,
        flight_duration_hours=body.flight_duration_hours,
        cruise_altitude_ft=body.cruise_altitude_ft,
    )
    flight = flights_api.create_flight(req=req, request=request, user_id=user_id, db=db)
    flight_id = flight.id

    # Auto-trigger the briefing refresh, mirroring the MCP tool's status mapping.
    refresh_status: dict[str, Any]
    try:
        await packs_api.refresh_briefing(
            flight_id=flight_id, request=request, force=False, as_of_date=None,
            user_id=user_id, db=db,
        )
        refresh_status = {
            "status": "processing",
            "estimated_seconds": 120,
            "message": "Briefing is being generated. Call get_briefing in ~2 minutes.",
        }
    except HTTPException as e:
        if e.status_code == 409:
            refresh_status = {"status": "already_in_progress"}
        elif e.status_code == 429:
            refresh_status = {"status": "rate_limited", "message": str(e.detail)}
        else:
            refresh_status = {"status": "failed", "message": str(e.detail)}

    return {
        "flight": {
            "id": flight_id,
            "route_name": flight.route_name,
            "waypoints": flight.waypoints,
            "departure_time": flight.departure_time,
            "cruise_altitude_ft": flight.cruise_altitude_ft,
            "flight_duration_hours": flight.flight_duration_hours,
            "web_url": _flight_web_url(flight_id),
        },
        "briefing": refresh_status,
    }


# ---------------------------------------------------------------------------
# GET /agent/v1/flights/{flight_id}/briefing  — get_briefing
# ---------------------------------------------------------------------------

@router.get("/flights/{flight_id}/briefing", operation_id="getBriefing")
def get_briefing(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get the latest weather briefing for a flight.

    Returns the overall assessment (GREEN/AMBER/RED), route advisories, AI
    weather digest, and a link to the full interactive briefing.

    Status values: ready (fresh), stale (models updated since), processing
    (generating now), none (call refresh_briefing first).
    """
    pack, timestamp, status = _resolve_latest_pack(db, user_id, flight_id)
    if status is not None:
        return status

    fresh = views.briefing_freshness_status(_freshness_dict(db, user_id, flight_id))

    result: dict[str, Any] = {
        "status": fresh["status"],
        "flight_id": flight_id,
        "assessment": pack.assessment,
        "assessment_reason": pack.assessment_reason,
        "days_out": pack.days_out,
        "briefing_timestamp": timestamp,
        "web_url": _flight_web_url(flight_id),
    }
    if not fresh["is_fresh"]:
        result["stale_models"] = fresh.get("stale_models", [])
        result["stale_note"] = fresh["stale_note"]

    pack_dir = packs_api._get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)

    advisories = _read_json(pack_dir, "route_advisories.json")
    if advisories:
        result["advisories"] = views.summarize_advisories(advisories)

    digest_json = _read_json(pack_dir, "digest.json")
    if digest_json:
        result["digest"] = digest_json

    digest_text = _read_text(pack_dir, "digest.md")
    if digest_text:
        result["digest_text"] = digest_text

    # Cheap cached altitude table (the GET path); omitted for packs predating it.
    alt_table = _read_json(pack_dir, "altitude_table.json")
    if alt_table:
        result["altitude_table"] = views.summarize_altitude_table(alt_table)

    return result


# ---------------------------------------------------------------------------
# POST /agent/v1/flights/{flight_id}/briefing/refresh  — refresh_briefing
# ---------------------------------------------------------------------------

@router.post("/flights/{flight_id}/briefing/refresh", operation_id="refreshBriefing")
async def refresh_briefing(
    flight_id: str,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Trigger a weather briefing refresh for a flight.

    Checks model freshness first — if all data is current, returns without
    re-running the pipeline. Safe to call repeatedly. The refresh takes ~2
    minutes; call get_briefing to check results.
    """
    try:
        accepted = await packs_api.refresh_briefing(
            flight_id=flight_id, request=request, force=False, as_of_date=None,
            user_id=user_id, db=db,
        )
    except HTTPException as e:
        code = e.status_code
        if code == 409:
            return {
                "status": "already_in_progress", "flight_id": flight_id,
                "message": "A refresh is already running for this flight.",
            }
        if code == 429:
            return {"status": "rate_limited", "flight_id": flight_id, "message": str(e.detail)}
        if code == 503:
            return {
                "status": "server_busy", "flight_id": flight_id,
                "message": "Server is busy with other refreshes. Try again shortly.",
            }
        raise

    # RefreshAccepted: status is "queued" | "already_fresh" | "realtime".
    data = accepted.model_dump() if hasattr(accepted, "model_dump") else dict(accepted)
    resp: dict[str, Any] = {
        "status": data.get("status", "queued"),
        "flight_id": flight_id,
        "message": data.get("message", ""),
        "web_url": _flight_web_url(flight_id),
    }
    if resp["status"] == "queued":
        resp["estimated_seconds"] = 120
    return resp


# ---------------------------------------------------------------------------
# GET /agent/v1/flights/{flight_id}/advisories/{advisory_id}  — get_advisory_detail
# ---------------------------------------------------------------------------

@router.get("/flights/{flight_id}/advisories/{advisory_id}", operation_id="getAdvisoryDetail")
def get_advisory_detail(
    flight_id: str,
    advisory_id: Annotated[
        str,
        Field(description="Advisory id to drill into, e.g. 'convective', 'fiki_icing', 'cloud_top'."),
    ],
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Drill into ONE advisory to explain WHY it is red/amber (or any grade).

    Use this when the user asks why an advisory is red/amber, questions a result
    that looks inconsistent (e.g. "red convective but clear skies"), or wants the
    per-model breakdown and cross-check reasoning. Never explain a red/amber from
    the aggregate status alone — call this first.

    The cross-check is context for discussion, not a downgrade signal — high CAPE
    matters even when the model's own convective scheme is quiet. Use it to
    EXPLAIN the grade, not to argue it down.
    """
    pack, timestamp, status = _resolve_latest_pack(db, user_id, flight_id)
    if status is not None:
        return status

    pack_dir = packs_api._get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    advisories = _read_json(pack_dir, "route_advisories.json")
    if not advisories:
        raise HTTPException(status_code=404, detail="No advisories available for this briefing.")

    adv = next(
        (a for a in advisories.get("advisories", []) if a.get("advisory_id") == advisory_id),
        None,
    )
    if adv is None:
        available = [a.get("advisory_id") for a in advisories.get("advisories", [])]
        raise HTTPException(
            status_code=404,
            detail=f"Advisory '{advisory_id}' not found. Available: {', '.join(available)}",
        )

    catalog = {c.get("id"): c for c in advisories.get("catalog", [])}
    result = views.advisory_detail(adv, catalog.get(advisory_id))

    # Per-briefing staleness caveat — a forensic per-model answer on stale data
    # is confidently-wrong territory. Uses the raw min-rule (matches MCP).
    freshness = _freshness_dict(db, user_id, flight_id)
    if not freshness.get("fresh", True):
        result["stale"] = True
        result["stale_models"] = freshness.get("stale_models", [])
        result["model_init_times"] = freshness.get("model_init_times", {})
        result["stale_note"] = (
            "Models have updated since this briefing was generated. This "
            "drill-down reflects the run used at briefing time, not the latest — "
            "caveat any forensic reasoning and suggest refresh_briefing for "
            "current data."
        )

    if advisory_id == "convective":
        route_analyses = _read_json(pack_dir, "route_analyses.json")
        if route_analyses:
            models = [m.get("model") for m in adv.get("per_model", [])]
            result["convective"] = views.convective_detail(route_analyses, models)
            result["convective_note"] = views.CONVECTIVE_NOTE

    result["flight_id"] = flight_id
    result["web_url"] = _flight_web_url(flight_id)
    return result


# ---------------------------------------------------------------------------
# GET /agent/v1/flights/{flight_id}/digest-context  — get_digest_context
# ---------------------------------------------------------------------------

@router.get("/flights/{flight_id}/digest-context", operation_id="getDigestContext")
def get_digest_context(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get the exact text input the AI weather digest saw for this flight.

    Use when the user wants the deepest possible context behind the briefing —
    e.g. to reconcile a red/amber advisory with the digest narrative. The text
    can be large (a few KB up to tens of KB); prefer get_advisory_detail for a
    targeted drill-down and reach for this only when you need the byte-faithful
    LLM input.
    """
    pack, timestamp, status = _resolve_latest_pack(db, user_id, flight_id)
    if status is not None:
        return status

    pack_dir = packs_api._get_pack_dir(db, flight_id, timestamp, viewer_id=user_id)
    context = _read_text(pack_dir, "digest_context.txt")
    if not context:
        return {
            "status": "none",
            "flight_id": flight_id,
            "message": "No digest context available for this briefing.",
            "web_url": _flight_web_url(flight_id),
        }

    return {
        "status": "ready",
        "flight_id": flight_id,
        "briefing_timestamp": timestamp,
        "digest_context": context,
        "web_url": _flight_web_url(flight_id),
    }


# ---------------------------------------------------------------------------
# GET /agent/v1/airport-weather  — get_airport_weather
# ---------------------------------------------------------------------------

@router.get("/airport-weather", operation_id="getAirportWeather")
def get_airport_weather(
    icao: Annotated[
        list[str],
        Query(description="Airport ICAO codes, e.g. ['LFBO', 'LFML']. Max 20. European airports only."),
    ],
    day: Annotated[
        int, Query(description="Days from today: 0=today, 1=tomorrow, 2=D+2, 3=D+3", ge=0, le=3),
    ] = 0,
    hour: Annotated[
        int, Query(description="Forecast hour in UTC: 6, 9, 12, 15, or 18", ge=0, le=23),
    ] = 12,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(maps_api._airports_db),
) -> dict[str, Any]:
    """Get weather forecasts and observations for specific airports.

    Returns multi-model predictions (GFS, ICON, ECMWF) with consensus flight
    category and agreement scoring. For today (day=0), also includes the latest
    METAR and TAF from the verification cache (may be up to ~3 hours old).

    Airports not in the monitoring network resolve to the nearest monitored
    airport (with distance noted). Non-European airports return as 'unsupported'.
    """
    return maps_api.get_airport_weather(
        icao=icao, day=day, hour=hour, _user_id=user_id, db=db, airports_db=airports_db,
    )
