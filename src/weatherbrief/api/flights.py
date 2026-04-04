"""API endpoints for flight management."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from flyfun_common.auth import is_dev_mode
from flyfun_common.db import current_user_id, get_db
from weatherbrief.models import Flight
from weatherbrief.storage.flights import (
    safe_path_component,
    delete_flight,
    list_flights,
    load_flight,
    save_flight,
)

router = APIRouter(prefix="/flights", tags=["flights"])

from weatherbrief.api.validation import WAYPOINT_RE


class CreateFlightRequest(BaseModel):
    """Request body for creating a new flight.

    Fields default to None, meaning "use user preference" (or system default).
    """

    route_name: str = Field("", max_length=256)  # optional preset name
    waypoints: list[str] = Field(default_factory=list, max_length=20)  # ICAO codes, navaids, or fixes
    departure_time: str  # ISO 8601 datetime with timezone (e.g. "2026-02-21T09:00:00Z")
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None
    profile_id: int | None = None  # flight profile to associate
    aircraft_id: int | None = None  # user aircraft to associate

    @field_validator("departure_time")
    @classmethod
    def validate_departure_time(cls, v: str) -> str:
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("departure_time must be a valid ISO 8601 datetime")
        if dt.tzinfo is None:
            raise ValueError("departure_time must include a timezone")
        return v

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: list[str]) -> list[str]:
        for wp in v:
            if not WAYPOINT_RE.match(wp.upper()):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be 2-5 alphanumeric characters"
                )
        return v


class AircraftInfo(BaseModel):
    """Lightweight aircraft info embedded in flight responses.

    Privacy-gated: ``tail_number`` is only set when the viewer is the aircraft
    owner; otherwise it is None.
    """

    id: int
    icao_type: str
    type_name: str  # "Manufacturer Model"
    tail_number: str | None = None
    nickname: str | None = None


class FlightResponse(BaseModel):
    """Flight data in API responses."""

    id: str
    user_id: str
    profile_id: int | None = None
    aircraft_id: int | None = None
    aircraft: AircraftInfo | None = None
    route_name: str
    waypoints: list[str] = []
    departure_time: str
    alt_departure_time: str | None = None
    target_date: str  # backward compat (computed from departure_time)
    target_time_utc: int  # backward compat (computed from departure_time)
    cruise_altitude_ft: int
    flight_ceiling_ft: int
    flight_duration_hours: float
    private: bool = False
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    created_at: str


def _is_admin_or_dev(request: Request, db: Session) -> bool:
    """Return True if the request comes from an admin user or dev mode is active."""
    if is_dev_mode():
        return True
    try:
        from weatherbrief.api.admin import require_admin
        require_admin(request, db=db)
        return True
    except HTTPException:
        return False


def _resolve_aircraft_info(
    db: Session, aircraft_id: int | None, *, viewer_id: str | None = None,
) -> AircraftInfo | None:
    """Build AircraftInfo for a flight, privacy-gated by viewer."""
    if aircraft_id is None:
        return None
    from weatherbrief.db.models import UserAircraftRow
    from weatherbrief.storage.aircraft_types import get_aircraft_type

    row = db.get(UserAircraftRow, aircraft_id)
    if row is None:
        return None

    type_info = get_aircraft_type(row.icao_type)
    type_name = f"{type_info['manufacturer']} {type_info['model']}" if type_info else row.icao_type

    is_owner = viewer_id is not None and row.user_id == viewer_id
    return AircraftInfo(
        id=row.id,
        icao_type=row.icao_type,
        type_name=type_name,
        tail_number=row.tail_number if is_owner else None,
        nickname=row.nickname if is_owner else None,
    )


def _flight_to_response(
    flight: Flight,
    db: Session | None = None,
    viewer_id: str | None = None,
) -> FlightResponse:
    aircraft = None
    if db is not None and flight.aircraft_id:
        aircraft = _resolve_aircraft_info(db, flight.aircraft_id, viewer_id=viewer_id)

    return FlightResponse(
        id=flight.id,
        user_id=flight.user_id,
        profile_id=flight.profile_id,
        aircraft_id=flight.aircraft_id,
        aircraft=aircraft,
        route_name=flight.route_name,
        waypoints=flight.waypoints,
        departure_time=flight.departure_time.isoformat(),
        alt_departure_time=flight.alt_departure_time.isoformat() if flight.alt_departure_time else None,
        target_date=flight.target_date,
        target_time_utc=flight.target_time_utc,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
        private=flight.private,
        auto_refresh=flight.auto_refresh,
        auto_refresh_hour=flight.auto_refresh_hour,
        created_at=flight.created_at.isoformat(),
    )


@router.get("", response_model=list[FlightResponse])
def list_all_flights(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List all saved flights."""
    flights = list_flights(db, user_id)
    return [_flight_to_response(f, db, viewer_id=user_id) for f in flights]


@router.post("", response_model=FlightResponse, status_code=201)
def create_flight(
    req: CreateFlightRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new flight.

    Request fields that are None are filled from the associated profile's
    settings (or user's default profile), then from system defaults.
    """
    from weatherbrief.api.profiles import load_profile_settings

    if not req.waypoints and not req.route_name:
        raise HTTPException(
            status_code=422, detail="Either waypoints or route_name is required"
        )

    # Derive route_name from waypoints if not provided
    route_name = req.route_name or "_".join(w.lower() for w in req.waypoints)
    waypoints = [w.upper().strip() for w in req.waypoints] if req.waypoints else []

    # Validate waypoints exist in the airport database
    if waypoints:
        db_path = getattr(request.app.state, "db_path", "")
        if db_path:
            from weatherbrief.airports import resolve_waypoints

            try:
                resolve_waypoints(waypoints, db_path)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=exc.args[0])

    # Parse departure_time
    departure_time = datetime.fromisoformat(req.departure_time)
    if departure_time.tzinfo is None:
        departure_time = departure_time.replace(tzinfo=timezone.utc)

    # Only admins can create historical flights with past departure times
    if departure_time < datetime.now(timezone.utc):
        if not _is_admin_or_dev(request, db):
            raise HTTPException(
                status_code=403,
                detail="Only admins can create flights with past departure times",
            )

    # Derive date string and hour for flight ID and hash (backward compat)
    target_date_str = departure_time.strftime("%Y-%m-%d")
    target_hour = departure_time.hour

    # Load defaults from the selected profile (or the user's default profile)
    profile_settings = load_profile_settings(db, req.profile_id, user_id)
    cruise_altitude_ft = (
        req.cruise_altitude_ft
        if req.cruise_altitude_ft is not None
        else (profile_settings.get("cruise_altitude_ft") or 8000)
    )
    flight_ceiling_ft = (
        req.flight_ceiling_ft
        if req.flight_ceiling_ft is not None
        else (profile_settings.get("flight_ceiling_ft") or 18000)
    )
    flight_duration_hours = req.flight_duration_hours if req.flight_duration_hours is not None else 0.0

    # Build a short hash from distinguishing parameters so the same route+date
    # can have multiple flights with different time/altitude/user.
    params_hash = hashlib.sha256(
        json.dumps(
            {
                "time": departure_time.strftime("%H:%M"),
                "alt": cruise_altitude_ft,
                "ceil": flight_ceiling_ft,
                "dur": flight_duration_hours,
                "user": user_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:4]
    flight_id = f"{safe_path_component(route_name)}-{target_date_str}-{params_hash}"

    # Check if already exists
    try:
        load_flight(db, flight_id)
        raise HTTPException(
            status_code=409,
            detail=f"Flight '{flight_id}' already exists",
        )
    except KeyError:
        pass

    flight = Flight(
        id=flight_id,
        user_id=user_id,
        profile_id=req.profile_id,
        aircraft_id=req.aircraft_id,
        route_name=route_name,
        waypoints=waypoints,
        departure_time=departure_time,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        flight_duration_hours=flight_duration_hours,
        created_at=datetime.now(tz=timezone.utc),
    )

    save_flight(db, flight, user_id)
    return _flight_to_response(flight, db, viewer_id=user_id)


class RouteDistanceRequest(BaseModel):
    """Request body for computing route distance from waypoints."""

    waypoints: list[str]

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("At least 2 waypoints are required")
        normalized = [wp.strip().upper() for wp in v]
        for wp in normalized:
            if not WAYPOINT_RE.match(wp):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be 2-5 alphanumeric characters"
                )
        return normalized


class WaypointInfo(BaseModel):
    """Resolved waypoint with coordinates and timezone."""

    icao: str
    name: str
    lat: float
    lon: float
    timezone: str | None = None


class RouteDistanceResponse(BaseModel):
    """Route distance computed from waypoints."""

    total_distance_nm: float
    waypoints: list[WaypointInfo] = []


@router.post("/route-distance", response_model=RouteDistanceResponse)
def compute_route_distance(
    req: RouteDistanceRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    """Compute total great-circle distance for a list of waypoints."""
    from euro_aip.models.navpoint import NavPoint
    from weatherbrief.airports import get_timezone, resolve_waypoints

    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=500, detail="Airport database not configured")

    try:
        resolved = resolve_waypoints(req.waypoints, db_path)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=exc.args[0])

    total_nm = 0.0
    for wp_a, wp_b in zip(resolved, resolved[1:]):
        nav_a = NavPoint(latitude=wp_a.lat, longitude=wp_a.lon)
        nav_b = NavPoint(latitude=wp_b.lat, longitude=wp_b.lon)
        _, leg_distance = nav_a.haversine_distance(nav_b)
        total_nm += leg_distance

    waypoint_infos = [
        WaypointInfo(
            icao=wp.icao,
            name=wp.name,
            lat=wp.lat,
            lon=wp.lon,
            timezone=get_timezone(wp.lat, wp.lon),
        )
        for wp in resolved
    ]

    return RouteDistanceResponse(
        total_distance_nm=round(total_nm, 1),
        waypoints=waypoint_infos,
    )


class InterpretRouteRequest(BaseModel):
    """Request body for interpreting a route string with smart waypoint filtering."""

    raw_route: str = Field(..., min_length=1, max_length=2000)


class InterpretRouteResponse(BaseModel):
    """Result of smart route interpretation."""

    original_tokens: list[str] = []  # all tokens extracted from the input
    interpreted: list[str] = []  # waypoints that resolved successfully
    skipped: list[str] = []  # tokens that didn't resolve
    waypoints: list[WaypointInfo] = []  # resolved waypoint details


@router.post("/interpret-route", response_model=InterpretRouteResponse)
def interpret_route(
    req: InterpretRouteRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    """Interpret a raw route string, resolving known waypoints and skipping unknown tokens.

    Accepts free-form route text (e.g. from a flight plan paste). Extracts tokens
    matching waypoint patterns, resolves each against the airport/navaid database,
    and returns the list of recognized waypoints plus any skipped tokens.
    """
    from weatherbrief.airports import get_timezone, is_known_waypoint, resolve_waypoints

    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=500, detail="Airport database not configured")

    # Extract tokens that look like waypoint codes
    tokens = [t.upper() for t in re.split(r"[\s,\-/]+", req.raw_route.strip()) if t]
    # Filter to valid waypoint patterns (2-5 alphanumeric chars)
    candidate_tokens = [t for t in tokens if WAYPOINT_RE.match(t)]

    # Validate each candidate: use single-point lookup until we have 2+
    # points, then switch to full route resolution for proper disambiguation
    interpreted: list[str] = []
    skipped: list[str] = []

    for token in candidate_tokens:
        # Skip consecutive duplicates (e.g. "ABDIL ABDIL" after filtering)
        if interpreted and interpreted[-1] == token:
            continue
        test_list = interpreted + [token]
        if len(test_list) < 2:
            # Not enough points for route resolver, validate individually
            if is_known_waypoint(token, db_path):
                interpreted.append(token)
            else:
                skipped.append(token)
        else:
            try:
                resolve_waypoints(test_list, db_path)
                interpreted.append(token)
            except KeyError:
                skipped.append(token)

    # Resolve the interpreted waypoints to get full info
    waypoint_infos: list[WaypointInfo] = []
    if len(interpreted) >= 2:
        try:
            resolved = resolve_waypoints(interpreted, db_path)
            waypoint_infos = [
                WaypointInfo(
                    icao=wp.icao,
                    name=wp.name,
                    lat=wp.lat,
                    lon=wp.lon,
                    timezone=get_timezone(wp.lat, wp.lon),
                )
                for wp in resolved
            ]
        except KeyError:
            # Should not happen since we pre-filtered, but handle gracefully
            pass

    return InterpretRouteResponse(
        original_tokens=candidate_tokens,
        interpreted=interpreted,
        skipped=skipped,
        waypoints=waypoint_infos,
    )


class ParseFplRequest(BaseModel):
    """Request body for parsing an ICAO flight plan string."""

    fpl_text: str = Field(..., min_length=10, max_length=4000)


class ParseFplResponse(BaseModel):
    """Parsed ICAO flight plan fields relevant for flight creation."""

    waypoints: list[str] = []
    date: str | None = None  # YYYY-MM-DD
    time_utc: str | None = None  # HH:MM
    altitude_ft: int | None = None
    duration_hours: float | None = None
    flight_rules: str | None = None  # V, I, Y, Z
    aircraft_type: str | None = None
    raw_route: str | None = None
    error: str | None = None


@router.post("/parse-fpl", response_model=ParseFplResponse)
def parse_flight_plan(
    req: ParseFplRequest,
    user_id: str = Depends(current_user_id),
):
    """Parse an ICAO FPL string and return fields for flight creation."""
    from euro_aip.briefing import parse_icao_fpl

    fpl = parse_icao_fpl(req.fpl_text)
    if fpl is None:
        return ParseFplResponse(error="Could not parse flight plan. Expected (FPL-...) format.")

    # Build waypoint list: departure + route waypoints + destination
    waypoints: list[str] = []
    if fpl.route.departure:
        waypoints.append(fpl.route.departure)
    # Filter route waypoints to those matching our waypoint pattern (skip GPS coords)
    for wp in fpl.route.waypoints:
        if WAYPOINT_RE.match(wp):
            waypoints.append(wp)
    if fpl.route.destination and fpl.route.destination not in waypoints:
        waypoints.append(fpl.route.destination)

    # Date
    date_str = fpl.date_of_flight.isoformat() if fpl.date_of_flight else None

    # Time
    time_str = None
    if fpl.departure_time_utc:
        time_str = fpl.departure_time_utc.strftime("%H:%M")

    # Duration from EET
    duration_hours = None
    if fpl.eet_minutes is not None:
        import math
        duration_hours = float(math.ceil(fpl.eet_minutes / 60))

    return ParseFplResponse(
        waypoints=waypoints,
        date=date_str,
        time_utc=time_str,
        altitude_ft=fpl.altitude_feet,
        duration_hours=duration_hours,
        flight_rules=fpl.flight_rules,
        aircraft_type=fpl.aircraft_type,
        raw_route=fpl.raw_route,
    )


@router.get("/{flight_id}", response_model=FlightResponse)
def get_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get flight details. Any authenticated user can view public flights."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    return _flight_to_response(flight, db, viewer_id=user_id)


class UpdateAutoRefreshRequest(BaseModel):
    """Request body for updating auto-refresh settings."""

    auto_refresh: bool
    auto_refresh_hour: int | None = None  # None = default (target_time_utc - 1)

    @field_validator("auto_refresh_hour")
    @classmethod
    def validate_hour(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 23):
            raise ValueError("auto_refresh_hour must be 0-23")
        return v


@router.patch("/{flight_id}/auto-refresh", response_model=FlightResponse)
def update_auto_refresh(
    flight_id: str,
    req: UpdateAutoRefreshRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update auto-refresh settings for a flight."""
    row = _load_owned_row(db, flight_id, user_id)
    row.auto_refresh = req.auto_refresh
    row.auto_refresh_hour = req.auto_refresh_hour
    db.flush()
    updated = load_flight(db, flight_id)
    return _flight_to_response(updated, db, viewer_id=user_id)


class UpdatePrivacyRequest(BaseModel):
    """Request body for updating flight privacy."""

    private: bool


@router.patch("/{flight_id}/privacy", response_model=FlightResponse)
def update_privacy(
    flight_id: str,
    req: UpdatePrivacyRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Toggle flight visibility. Only the flight owner can change this."""
    row = _load_owned_row(db, flight_id, user_id)
    row.private = req.private
    db.flush()
    updated = load_flight(db, flight_id)
    return _flight_to_response(updated, db, viewer_id=user_id)


class UpdateFlightRequest(BaseModel):
    """Request body for updating editable flight parameters."""

    profile_id: int | None = None  # switch aircraft profile (applies its altitude/ceiling)
    aircraft_id: int | None = None  # switch aircraft (applies speed/ceiling defaults)
    departure_time: str | None = None  # ISO 8601 (time-of-day change only; date must match)
    alt_departure_time: str | None = None  # ISO 8601 or "" to clear
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None
    waypoints: list[str] | None = None  # updated route (origin+destination must match)

    @field_validator("departure_time")
    @classmethod
    def validate_departure_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("departure_time must be a valid ISO 8601 datetime")
        if dt.tzinfo is None:
            raise ValueError("departure_time must include a timezone")
        return v

    @field_validator("alt_departure_time")
    @classmethod
    def validate_alt_departure_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("alt_departure_time must be a valid ISO 8601 datetime or empty string")
        if dt.tzinfo is None:
            raise ValueError("alt_departure_time must include a timezone")
        return v


class UpdateFlightResponse(FlightResponse):
    """Flight response with invalidation hint after an update."""

    invalidation: str  # "none" | "advisories_only" | "refetch_needed"


@router.patch("/{flight_id}", response_model=UpdateFlightResponse)
def update_flight(
    flight_id: str,
    req: UpdateFlightRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update editable flight parameters (time, altitude, ceiling, duration, route).

    The date portion and origin/destination of the flight cannot change.
    Returns an invalidation hint so the frontend knows whether existing
    briefings need a refetch or just an advisory recalculation.
    """
    row = _load_owned_row(db, flight_id, user_id)
    original_flight = load_flight(db, flight_id)

    time_changed = False
    altitude_changed = False
    profile_changed = False
    route_changed = False

    # Route (waypoints) change — origin and destination must remain the same
    if req.waypoints is not None:
        new_waypoints = [w.upper().strip() for w in req.waypoints]
        if len(new_waypoints) < 2:
            raise HTTPException(
                status_code=422,
                detail="At least 2 waypoints are required",
            )
        # Validate waypoint format
        for wp in new_waypoints:
            if not WAYPOINT_RE.match(wp):
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid waypoint '{wp}': must be 2-5 alphanumeric characters",
                )
        # Validate waypoints exist in the database
        db_path = getattr(request.app.state, "db_path", "")
        if db_path:
            from weatherbrief.airports import resolve_waypoints

            try:
                resolve_waypoints(new_waypoints, db_path)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=exc.args[0])
        # Enforce same origin and destination
        old_origin = original_flight.waypoints[0] if original_flight.waypoints else None
        old_dest = original_flight.waypoints[-1] if original_flight.waypoints else None
        new_origin = new_waypoints[0]
        new_dest = new_waypoints[-1]
        if old_origin and new_origin != old_origin:
            raise HTTPException(
                status_code=422,
                detail=f"Origin cannot change (was {old_origin}, got {new_origin}). Create a new flight instead.",
            )
        if old_dest and new_dest != old_dest:
            raise HTTPException(
                status_code=422,
                detail=f"Destination cannot change (was {old_dest}, got {new_dest}). Create a new flight instead.",
            )
        if new_waypoints != original_flight.waypoints:
            row.waypoints_json = json.dumps(new_waypoints)
            # Update route_name to reflect new waypoints
            row.route_name = "_".join(w.lower() for w in new_waypoints)
            route_changed = True

    # Profile change — apply the new profile's altitude/ceiling to the flight
    if req.profile_id is not None and req.profile_id != original_flight.profile_id:
        from weatherbrief.api.profiles import load_profile_settings

        profile_settings = load_profile_settings(db, req.profile_id, user_id)
        row.profile_id = req.profile_id
        profile_changed = True

        # Apply profile's altitude/ceiling unless explicitly overridden in this request
        if req.cruise_altitude_ft is None and profile_settings.get("cruise_altitude_ft"):
            row.cruise_altitude_ft = profile_settings["cruise_altitude_ft"]
            altitude_changed = True
        if req.flight_ceiling_ft is None and profile_settings.get("flight_ceiling_ft"):
            row.flight_ceiling_ft = profile_settings["flight_ceiling_ft"]
            altitude_changed = True

    # Aircraft change
    if req.aircraft_id is not None and req.aircraft_id != original_flight.aircraft_id:
        row.aircraft_id = req.aircraft_id if req.aircraft_id != 0 else None

    if req.departure_time is not None:
        new_dt = datetime.fromisoformat(req.departure_time)
        if new_dt.tzinfo is None:
            new_dt = new_dt.replace(tzinfo=timezone.utc)
        # Enforce same date
        if new_dt.strftime("%Y-%m-%d") != original_flight.target_date:
            raise HTTPException(
                status_code=422,
                detail="Cannot change the flight date. Create a new flight instead.",
            )
        if new_dt != original_flight.departure_time:
            row.departure_time = new_dt
            time_changed = True

    if req.cruise_altitude_ft is not None and req.cruise_altitude_ft != original_flight.cruise_altitude_ft:
        row.cruise_altitude_ft = req.cruise_altitude_ft
        altitude_changed = True

    if req.flight_ceiling_ft is not None and req.flight_ceiling_ft != original_flight.flight_ceiling_ft:
        row.flight_ceiling_ft = req.flight_ceiling_ft
        altitude_changed = True

    if req.flight_duration_hours is not None and req.flight_duration_hours != original_flight.flight_duration_hours:
        row.flight_duration_hours = req.flight_duration_hours
        time_changed = True

    # Alt departure time: "" clears, ISO string sets, None = no change
    if req.alt_departure_time is not None:
        if req.alt_departure_time == "":
            row.alt_departure_time = None
        else:
            alt_dt = datetime.fromisoformat(req.alt_departure_time)
            # Validate same-day constraint
            effective_departure = row.departure_time
            if alt_dt.date() != effective_departure.date():
                raise HTTPException(
                    status_code=422,
                    detail="Alt departure time must be on the same day as the primary departure.",
                )
            # Validate alt ≠ primary
            if alt_dt == effective_departure:
                raise HTTPException(
                    status_code=422,
                    detail="Alt departure time must differ from the primary departure time.",
                )
            row.alt_departure_time = alt_dt

    db.flush()
    updated = load_flight(db, flight_id)

    if time_changed or route_changed:
        invalidation = "refetch_needed"
    elif altitude_changed or profile_changed:
        invalidation = "advisories_only"
    else:
        invalidation = "none"

    resp = _flight_to_response(updated, db, viewer_id=user_id)
    return UpdateFlightResponse(**resp.model_dump(), invalidation=invalidation)


@router.delete("/{flight_id}", status_code=204)
def remove_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Delete a flight and all its packs."""
    _load_owned_flight(db, flight_id, user_id)  # verify ownership
    try:
        delete_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")


def _load_flight_or_404(db: Session, flight_id: str, *, viewer_id: str | None = None) -> Flight:
    """Load a flight by ID. Returns 404 if not found or private and not owned by viewer."""
    try:
        flight = load_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    if flight.private and viewer_id is not None and flight.user_id != viewer_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return flight


def _load_owned_flight(db: Session, flight_id: str, user_id: str) -> Flight:
    """Load a flight, verifying it belongs to the current user. Returns 404 if not found or not owned."""
    flight = _load_flight_or_404(db, flight_id)
    if flight.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return flight


def _load_owned_row(db: Session, flight_id: str, user_id: str):
    """Load the ORM FlightRow, verifying ownership. Single query for PATCH endpoints."""
    from weatherbrief.db.models import FlightRow

    row = db.get(FlightRow, flight_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return row
