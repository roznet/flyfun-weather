"""API endpoints for PIREP submission and querying."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from weatherbrief.api.preferences import can_publish_pireps, can_view_pireps
from weatherbrief.api.throttle import pirep_burst_limiter, pirep_daily_limiter
from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    ICING_INTENSITIES,
    ICING_TYPES,
    PIREP_SOURCES,
    PirepRow,
    TOPS_BASES,
    TURBULENCE_INTENSITIES,
    UserAircraftRow,
)
from weatherbrief.storage.aircraft_types import get_aircraft_type
from weatherbrief.storage.pireps import (
    create_pirep,
    list_pireps,
    validate_european_bounds,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pireps", tags=["pireps"])

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PirepResponse(BaseModel):
    id: int
    client_uuid: str | None = None
    submitted_at: str
    observed_at: str
    latitude: float
    longitude: float
    gps_altitude_ft: int | None = None
    reported_altitude_ft: int | None = None
    in_cloud: bool | None = None
    icing_intensity: str | None = None
    icing_type: str | None = None
    turbulence_intensity: str | None = None
    ceiling_msl_ft: int | None = None
    tops_msl_ft: int | None = None
    tops_basis: str | None = None
    temp_c: float | None = None
    wind_dir: int | None = None
    wind_speed_kt: int | None = None
    remarks: str | None = None
    aircraft_type: str | None = None  # ICAO type resolved from aircraft_id
    pack_id: int | None = None
    source: str = "manual"
    is_own: bool = False  # True if viewer is the PIREP author


class PirepListResponse(BaseModel):
    items: list[PirepResponse]
    count: int


def _row_to_response(row: PirepRow, *, viewer_id: str | None = None) -> PirepResponse:
    """Convert a PirepRow to API response, resolving aircraft type."""
    aircraft_type = None
    if row.aircraft_id and row.aircraft:
        aircraft_type = row.aircraft.icao_type

    return PirepResponse(
        id=row.id,
        client_uuid=row.client_uuid,
        submitted_at=row.submitted_at.isoformat() if row.submitted_at else "",
        observed_at=row.observed_at.isoformat() if row.observed_at else "",
        latitude=row.latitude,
        longitude=row.longitude,
        gps_altitude_ft=row.gps_altitude_ft,
        reported_altitude_ft=row.reported_altitude_ft,
        in_cloud=row.in_cloud,
        icing_intensity=row.icing_intensity,
        icing_type=row.icing_type,
        turbulence_intensity=row.turbulence_intensity,
        ceiling_msl_ft=row.ceiling_msl_ft,
        tops_msl_ft=row.tops_msl_ft,
        tops_basis=row.tops_basis,
        temp_c=row.temp_c,
        wind_dir=row.wind_dir,
        wind_speed_kt=row.wind_speed_kt,
        remarks=row.remarks,
        aircraft_type=aircraft_type,
        pack_id=row.pack_id,
        source=row.source,
        is_own=(viewer_id is not None and row.user_id == viewer_id),
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SubmitPirepRequest(BaseModel):
    client_uuid: str | None = None
    observed_at: str  # ISO 8601
    latitude: float
    longitude: float
    gps_altitude_ft: int | None = None
    reported_altitude_ft: int | None = None
    in_cloud: bool | None = None
    icing_intensity: str | None = None
    icing_type: str | None = None
    turbulence_intensity: str | None = None
    ceiling_msl_ft: int | None = None
    tops_msl_ft: int | None = None
    tops_basis: str | None = None
    temp_c: float | None = None
    wind_dir: int | None = None
    wind_speed_kt: int | None = None
    remarks: str | None = None
    aircraft_id: int | None = None
    pack_id: int | None = None
    source: str = "manual"

    @field_validator("client_uuid")
    @classmethod
    def validate_uuid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not UUID_RE.match(v):
            raise ValueError("client_uuid must be a valid UUID v4")
        return v.lower()

    @field_validator("latitude")
    @classmethod
    def validate_lat(cls, v: float) -> float:
        if not (-90 <= v <= 90):
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_lon(cls, v: float) -> float:
        if not (-180 <= v <= 180):
            raise ValueError("Longitude must be between -180 and 180")
        return v

    @field_validator("icing_intensity")
    @classmethod
    def validate_icing_intensity(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.lower()
        if v not in ICING_INTENSITIES:
            raise ValueError(f"icing_intensity must be one of {ICING_INTENSITIES}")
        return v

    @field_validator("icing_type")
    @classmethod
    def validate_icing_type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.lower()
        if v not in ICING_TYPES:
            raise ValueError(f"icing_type must be one of {ICING_TYPES}")
        return v

    @field_validator("turbulence_intensity")
    @classmethod
    def validate_turb(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.lower()
        if v not in TURBULENCE_INTENSITIES:
            raise ValueError(f"turbulence_intensity must be one of {TURBULENCE_INTENSITIES}")
        return v

    @field_validator("tops_basis")
    @classmethod
    def validate_tops_basis(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.lower()
        if v not in TOPS_BASES:
            raise ValueError(f"tops_basis must be one of {TOPS_BASES}")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        v = v.lower()
        if v not in PIREP_SOURCES:
            raise ValueError(f"source must be one of {PIREP_SOURCES}")
        return v

    @field_validator("wind_dir")
    @classmethod
    def validate_wind_dir(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 360):
            raise ValueError("wind_dir must be between 0 and 360")
        return v

    @field_validator("wind_speed_kt")
    @classmethod
    def validate_wind_speed(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 200):
            raise ValueError("wind_speed_kt must be between 0 and 200")
        return v

    @field_validator("temp_c")
    @classmethod
    def validate_temp(cls, v: float | None) -> float | None:
        if v is not None and not (-80 <= v <= 60):
            raise ValueError("temp_c must be between -80 and 60")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_observed_at(iso_str: str) -> datetime:
    """Parse ISO 8601 datetime, ensuring UTC."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_aircraft_ownership(db: Session, aircraft_id: int, user_id: str) -> None:
    """Raise 400 if aircraft_id doesn't belong to user."""
    aircraft = db.get(UserAircraftRow, aircraft_id)
    if aircraft is None or aircraft.user_id != user_id:
        raise HTTPException(status_code=400, detail="Aircraft not found or not owned by you")


def _validate_pack_ownership(db: Session, pack_id: int, user_id: str) -> None:
    """Raise 400 if pack_id doesn't belong to user's flight."""
    pack = db.get(BriefingPackRow, pack_id)
    if pack is None:
        raise HTTPException(status_code=400, detail="Briefing pack not found")
    flight = db.get(FlightRow, pack.flight_id)
    if flight is None or flight.user_id != user_id:
        raise HTTPException(status_code=400, detail="Briefing pack not owned by you")


def _resolve_airport(icao: str, request: Request) -> tuple[float, float]:
    """Resolve airport ICAO code to (lat, lon) using the euro_aip database."""
    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=503, detail="Airport database not configured")
    try:
        from weatherbrief.airports import resolve_waypoints
        waypoints = resolve_waypoints([icao.upper()], db_path)
        return (waypoints[0].lat, waypoints[0].lon)
    except (KeyError, IndexError):
        raise HTTPException(status_code=400, detail=f"Unknown airport: {icao}")


def _create_one(
    db: Session, user_id: str, req: SubmitPirepRequest,
) -> PirepRow:
    """Create a single PIREP from a validated request. Raises IntegrityError on dupe."""
    observed_at = _parse_observed_at(req.observed_at)
    return create_pirep(
        db, user_id,
        client_uuid=req.client_uuid,
        submitted_at=datetime.now(timezone.utc),
        observed_at=observed_at,
        latitude=req.latitude,
        longitude=req.longitude,
        gps_altitude_ft=req.gps_altitude_ft,
        reported_altitude_ft=req.reported_altitude_ft,
        in_cloud=req.in_cloud,
        icing_intensity=req.icing_intensity,
        icing_type=req.icing_type,
        turbulence_intensity=req.turbulence_intensity,
        ceiling_msl_ft=req.ceiling_msl_ft,
        tops_msl_ft=req.tops_msl_ft,
        tops_basis=req.tops_basis,
        temp_c=req.temp_c,
        wind_dir=req.wind_dir,
        wind_speed_kt=req.wind_speed_kt,
        remarks=req.remarks,
        aircraft_id=req.aircraft_id,
        pack_id=req.pack_id,
        source=req.source,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=PirepResponse, status_code=201)
def submit_pirep(
    req: SubmitPirepRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Submit a single PIREP."""
    if not can_publish_pireps(db, user_id):
        raise HTTPException(status_code=403, detail="PIREP publishing not enabled for your account")

    pirep_burst_limiter.check(user_id)
    pirep_daily_limiter.check(user_id)

    try:
        validate_european_bounds(req.latitude, req.longitude)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.aircraft_id is not None:
        _validate_aircraft_ownership(db, req.aircraft_id, user_id)
    if req.pack_id is not None:
        _validate_pack_ownership(db, req.pack_id, user_id)

    try:
        row = _create_one(db, user_id, req)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Duplicate PIREP (client_uuid already exists)")

    return _row_to_response(row, viewer_id=user_id)


@router.post("/batch", response_model=list[PirepResponse], status_code=201)
def submit_pireps_batch(
    items: list[SubmitPirepRequest],
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Submit multiple PIREPs (offline sync).

    Duplicates (by client_uuid) are silently skipped — the previously
    stored PIREP is returned in the response.
    """
    if not can_publish_pireps(db, user_id):
        raise HTTPException(status_code=403, detail="PIREP publishing not enabled for your account")

    if len(items) > 50:
        raise HTTPException(status_code=400, detail="Batch size must be <= 50")

    # Check daily cap has room (don't check burst for batch — it's a sync).
    # Check against actual batch size so the 50/day limit means 50 PIREPs, not 50 calls.
    pirep_daily_limiter.check(user_id, count=len(items))

    results: list[PirepResponse] = []
    for req in items:
        try:
            validate_european_bounds(req.latitude, req.longitude)
        except ValueError:
            continue  # skip out-of-bounds items silently

        if req.aircraft_id is not None:
            aircraft = db.get(UserAircraftRow, req.aircraft_id)
            if aircraft is None or aircraft.user_id != user_id:
                continue

        if req.pack_id is not None:
            pack = db.get(BriefingPackRow, req.pack_id)
            if pack is None:
                continue
            flight = db.get(FlightRow, pack.flight_id)
            if flight is None or flight.user_id != user_id:
                continue

        try:
            nested = db.begin_nested()
            row = _create_one(db, user_id, req)
            results.append(_row_to_response(row, viewer_id=user_id))
        except IntegrityError:
            nested.rollback()
            # Already exists — find and return it
            if req.client_uuid:
                existing = db.query(PirepRow).filter(
                    PirepRow.client_uuid == req.client_uuid
                ).first()
                if existing:
                    results.append(_row_to_response(existing, viewer_id=user_id))

    return results


@router.get("", response_model=PirepListResponse)
def query_pireps(
    request: Request,
    flight_id: str | None = None,
    pack_id: int | None = None,
    airport: str | None = None,
    bounds: str | None = None,
    hours: int = Query(default=6, ge=1, le=48),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    hazard: str | None = None,
    min_severity: str | None = None,
    altitude_min: int | None = None,
    altitude_max: int | None = None,
    aircraft_type: str | None = None,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Query PIREPs with flexible filters."""
    if not can_view_pireps(db, user_id):
        raise HTTPException(status_code=403, detail="PIREP viewing not enabled for your account")

    # Parse scope filters
    kwargs: dict = {}

    if flight_id is not None:
        kwargs["flight_id"] = flight_id
    elif pack_id is not None:
        kwargs["pack_id"] = pack_id
    elif airport is not None:
        lat, lon = _resolve_airport(airport, request)
        kwargs["airport_lat_lon"] = (lat, lon)
    elif bounds is not None:
        try:
            parts = [float(x) for x in bounds.split(",")]
            if len(parts) != 4:
                raise ValueError
            kwargs["bounds"] = tuple(parts)
        except ValueError:
            raise HTTPException(status_code=400, detail="bounds must be sw_lat,sw_lon,ne_lat,ne_lon")

    # Time filters
    if from_dt is not None:
        kwargs["from_dt"] = _parse_observed_at(from_dt)
    if to_dt is not None:
        kwargs["to_dt"] = _parse_observed_at(to_dt)
    if from_dt is None and to_dt is None:
        kwargs["hours"] = hours

    # Optional filters
    if hazard is not None:
        if hazard not in ("icing", "turbulence", "cloud"):
            raise HTTPException(status_code=400, detail="hazard must be icing, turbulence, or cloud")
        kwargs["hazard"] = hazard
    if min_severity is not None:
        kwargs["min_severity"] = min_severity
    if altitude_min is not None:
        kwargs["altitude_min"] = altitude_min
    if altitude_max is not None:
        kwargs["altitude_max"] = altitude_max
    if aircraft_type is not None:
        kwargs["aircraft_type"] = aircraft_type

    rows = list_pireps(db, **kwargs)
    items = [_row_to_response(r, viewer_id=user_id) for r in rows]
    return PirepListResponse(items=items, count=len(items))
