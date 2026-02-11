"""API endpoints for flight management."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from weatherbrief.models import Flight
from weatherbrief.storage.flights import (
    delete_flight,
    list_flights,
    load_flight,
    save_flight,
)

router = APIRouter(prefix="/flights", tags=["flights"])


class CreateFlightRequest(BaseModel):
    """Request body for creating a new flight."""

    route_name: str
    target_date: str  # YYYY-MM-DD
    target_time_utc: int = 9
    cruise_altitude_ft: int = 8000
    flight_duration_hours: float = 0.0


class FlightResponse(BaseModel):
    """Flight data in API responses."""

    id: str
    route_name: str
    target_date: str
    target_time_utc: int
    cruise_altitude_ft: int
    flight_duration_hours: float
    created_at: str


def _flight_to_response(flight: Flight) -> FlightResponse:
    return FlightResponse(
        id=flight.id,
        route_name=flight.route_name,
        target_date=flight.target_date,
        target_time_utc=flight.target_time_utc,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_duration_hours=flight.flight_duration_hours,
        created_at=flight.created_at.isoformat(),
    )


@router.get("", response_model=list[FlightResponse])
def list_all_flights():
    """List all saved flights."""
    flights = list_flights()
    return [_flight_to_response(f) for f in flights]


@router.post("", response_model=FlightResponse, status_code=201)
def create_flight(req: CreateFlightRequest):
    """Create a new flight."""
    flight_id = f"{req.route_name}-{req.target_date}"

    # Check if already exists
    try:
        load_flight(flight_id)
        raise HTTPException(
            status_code=409,
            detail=f"Flight '{flight_id}' already exists",
        )
    except FileNotFoundError:
        pass

    flight = Flight(
        id=flight_id,
        route_name=req.route_name,
        target_date=req.target_date,
        target_time_utc=req.target_time_utc,
        cruise_altitude_ft=req.cruise_altitude_ft,
        flight_duration_hours=req.flight_duration_hours,
        created_at=datetime.now(tz=timezone.utc),
    )

    save_flight(flight)
    return _flight_to_response(flight)


@router.get("/{flight_id}", response_model=FlightResponse)
def get_flight(flight_id: str):
    """Get flight details."""
    try:
        flight = load_flight(flight_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return _flight_to_response(flight)


@router.delete("/{flight_id}", status_code=204)
def remove_flight(flight_id: str):
    """Delete a flight and all its packs."""
    try:
        delete_flight(flight_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
