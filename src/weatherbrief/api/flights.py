"""API endpoints for flight management."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
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

_ICAO_PATTERN = re.compile(r"^[A-Z]{2,4}$")


class CreateFlightRequest(BaseModel):
    """Request body for creating a new flight.

    Fields default to None, meaning "use user preference" (or system default).
    """

    route_name: str = ""  # optional preset name
    waypoints: list[str] = []  # ICAO codes (e.g. ["EGTK", "LFPB", "LSGS"])
    target_date: str  # YYYY-MM-DD
    target_time_utc: int | None = None
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None

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
                    f"Invalid waypoint '{wp}': must be 2-4 uppercase letters"
                )
        return v


class FlightResponse(BaseModel):
    """Flight data in API responses."""

    id: str
    route_name: str
    waypoints: list[str] = []
    target_date: str
    target_time_utc: int
    cruise_altitude_ft: int
    flight_ceiling_ft: int
    flight_duration_hours: float
    created_at: str


def _flight_to_response(flight: Flight) -> FlightResponse:
    return FlightResponse(
        id=flight.id,
        route_name=flight.route_name,
        waypoints=flight.waypoints,
        target_date=flight.target_date,
        target_time_utc=flight.target_time_utc,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
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
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new flight.

    Request fields that are None are filled from the user's saved preferences,
    then from system defaults.
    """
    from weatherbrief.api.preferences import load_user_defaults

    if not req.waypoints and not req.route_name:
        raise HTTPException(
            status_code=422, detail="Either waypoints or route_name is required"
        )

    # Derive route_name from waypoints if not provided
    route_name = req.route_name or "_".join(w.lower() for w in req.waypoints)
    waypoints = [w.upper().strip() for w in req.waypoints] if req.waypoints else []

    # Apply user defaults for any field left as None
    defaults = load_user_defaults(db, user_id)
    target_time_utc = req.target_time_utc if req.target_time_utc is not None else 9
    cruise_altitude_ft = (
        req.cruise_altitude_ft
        if req.cruise_altitude_ft is not None
        else (defaults.cruise_altitude_ft or 8000)
    )
    flight_ceiling_ft = (
        req.flight_ceiling_ft
        if req.flight_ceiling_ft is not None
        else (defaults.flight_ceiling_ft or 18000)
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


@router.get("/{flight_id}", response_model=FlightResponse)
def get_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get flight details."""
    flight = _load_owned_flight(db, flight_id, user_id)
    return _flight_to_response(flight)


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


def _load_owned_flight(db: Session, flight_id: str, user_id: str) -> Flight:
    """Load a flight, verifying it belongs to the current user. Returns 404 if not found or not owned."""
    try:
        flight = load_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    if flight.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return flight
