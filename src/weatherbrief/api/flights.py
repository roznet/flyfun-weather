"""API endpoints for flight management."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.models import Flight
from weatherbrief.storage.flights import (
    safe_path_component,
    delete_flight,
    list_flights,
    load_flight,
    save_flight,
)

router = APIRouter(prefix="/flights", tags=["flights"])

_ICAO_PATTERN = re.compile(r"^[A-Z]{4}$")


class CreateFlightRequest(BaseModel):
    """Request body for creating a new flight.

    Fields default to None, meaning "use user preference" (or system default).
    """

    route_name: str = Field("", max_length=256)  # optional preset name
    waypoints: list[str] = Field(default_factory=list, max_length=20)  # ICAO codes
    target_date: str  # YYYY-MM-DD
    target_time_utc: int | None = None
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None
    profile_id: int | None = None  # flight profile to associate

    @field_validator("target_date")
    @classmethod
    def validate_target_date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("target_date must be YYYY-MM-DD format")
        return v

    @field_validator("target_time_utc")
    @classmethod
    def validate_target_time(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 23):
            raise ValueError("target_time_utc must be 0-23")
        return v

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: list[str]) -> list[str]:
        for wp in v:
            if not _ICAO_PATTERN.match(wp.upper()):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be a 4-letter ICAO code"
                )
        return v


class FlightResponse(BaseModel):
    """Flight data in API responses."""

    id: str
    user_id: str
    profile_id: int | None = None
    route_name: str
    waypoints: list[str] = []
    target_date: str
    target_time_utc: int
    cruise_altitude_ft: int
    flight_ceiling_ft: int
    flight_duration_hours: float
    private: bool = False
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    created_at: str


def _flight_to_response(flight: Flight) -> FlightResponse:
    return FlightResponse(
        id=flight.id,
        user_id=flight.user_id,
        profile_id=flight.profile_id,
        route_name=flight.route_name,
        waypoints=flight.waypoints,
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
    return [_flight_to_response(f) for f in flights]


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

    # Load defaults from the selected profile (or the user's default profile)
    profile_settings = load_profile_settings(db, req.profile_id, user_id)
    target_time_utc = req.target_time_utc if req.target_time_utc is not None else 9
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
    # can have multiple flights with different time/altitude/etc.
    params_hash = hashlib.sha256(
        json.dumps(
            {
                "time": target_time_utc,
                "alt": cruise_altitude_ft,
                "ceil": flight_ceiling_ft,
                "dur": flight_duration_hours,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:4]
    flight_id = f"{safe_path_component(route_name)}-{req.target_date}-{params_hash}"

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
        route_name=route_name,
        waypoints=waypoints,
        target_date=req.target_date,
        target_time_utc=target_time_utc,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        flight_duration_hours=flight_duration_hours,
        created_at=datetime.now(tz=timezone.utc),
    )

    save_flight(db, flight, user_id)
    return _flight_to_response(flight)


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
            if not _ICAO_PATTERN.match(wp):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be a 4-letter ICAO code"
                )
        return normalized


class RouteDistanceResponse(BaseModel):
    """Route distance computed from waypoints."""

    total_distance_nm: float


@router.post("/route-distance", response_model=RouteDistanceResponse)
def compute_route_distance(
    req: RouteDistanceRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    """Compute total great-circle distance for a list of waypoints."""
    from euro_aip.models.navpoint import NavPoint
    from weatherbrief.airports import resolve_waypoints

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

    return RouteDistanceResponse(total_distance_nm=round(total_nm, 1))


@router.get("/{flight_id}", response_model=FlightResponse)
def get_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get flight details. Any authenticated user can view public flights."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    return _flight_to_response(flight)


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
    return _flight_to_response(updated)


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
    return _flight_to_response(updated)


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
