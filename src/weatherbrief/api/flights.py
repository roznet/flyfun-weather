"""API endpoints for flight management."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from weatherbrief.db.deps import get_db
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.models import Flight
from weatherbrief.storage.flights import (
    delete_flight,
    list_flights,
    load_flight,
    save_flight,
)

router = APIRouter(prefix="/flights", tags=["flights"])


def _current_user_id() -> str:
    """Return the current user ID. Dev mode uses a hardcoded dev user."""
    # TODO: replace with auth middleware in multi-user phase
    return DEV_USER_ID


class CreateFlightRequest(BaseModel):
    """Request body for creating a new flight."""

    route_name: str = ""  # optional preset name
    waypoints: list[str] = []  # ICAO codes (e.g. ["EGTK", "LFPB", "LSGS"])
    target_date: str  # YYYY-MM-DD
    target_time_utc: int = 9
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    flight_duration_hours: float = 0.0


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
def list_all_flights(db: Session = Depends(get_db)):
    """List all saved flights."""
    user_id = _current_user_id()
    flights = list_flights(db, user_id)
    return [_flight_to_response(f) for f in flights]


@router.post("", response_model=FlightResponse, status_code=201)
def create_flight(req: CreateFlightRequest, db: Session = Depends(get_db)):
    """Create a new flight."""
    if not req.waypoints and not req.route_name:
        raise HTTPException(
            status_code=422, detail="Either waypoints or route_name is required"
        )

    # Derive route_name from waypoints if not provided
    route_name = req.route_name or "_".join(w.lower() for w in req.waypoints)
    waypoints = [w.upper().strip() for w in req.waypoints] if req.waypoints else []

    user_id = _current_user_id()
    flight_id = f"{route_name}-{req.target_date}"

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
        target_time_utc=req.target_time_utc,
        cruise_altitude_ft=req.cruise_altitude_ft,
        flight_ceiling_ft=req.flight_ceiling_ft,
        flight_duration_hours=req.flight_duration_hours,
        created_at=datetime.now(tz=timezone.utc),
    )

    save_flight(db, flight, user_id)
    return _flight_to_response(flight)


@router.get("/{flight_id}", response_model=FlightResponse)
def get_flight(flight_id: str, db: Session = Depends(get_db)):
    """Get flight details."""
    try:
        flight = load_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return _flight_to_response(flight)


@router.delete("/{flight_id}", status_code=204)
def remove_flight(flight_id: str, db: Session = Depends(get_db)):
    """Delete a flight and all its packs."""
    try:
        delete_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
