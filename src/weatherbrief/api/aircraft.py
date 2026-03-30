"""API endpoints for user aircraft management and ICAO type search."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from weatherbrief.storage.aircraft import (
    create_aircraft,
    delete_aircraft,
    get_aircraft,
    list_aircraft,
    update_aircraft,
)
from weatherbrief.storage.aircraft_types import get_aircraft_type, search_aircraft_types

router = APIRouter(prefix="/aircraft", tags=["aircraft"])

TAIL_NUMBER_RE = re.compile(r"^[A-Z0-9\-]{2,10}$", re.IGNORECASE)


# --- Response models ---


class AircraftTypeResponse(BaseModel):
    icao: str
    manufacturer: str
    model: str
    category: str | None = None


class AircraftResponse(BaseModel):
    """Aircraft data in API responses.

    The ``tail_number`` field is privacy-gated: it is only included when
    the viewer is the aircraft owner.  Callers that serialise for a
    non-owner audience should set ``tail_number`` to None before
    returning.
    """

    id: int
    icao_type: str
    type_name: str  # pretty name: "Manufacturer Model"
    tail_number: str | None = None
    nickname: str | None = None
    is_ifr: bool = False
    is_fiki: bool = False
    cruise_speed_kt: int | None = None
    ceiling_ft: int | None = None
    is_default: bool = False
    created_at: str


def _row_to_response(row, *, include_tail: bool = True) -> AircraftResponse:
    type_info = get_aircraft_type(row.icao_type)
    if type_info:
        type_name = f"{type_info['manufacturer']} {type_info['model']}"
    else:
        type_name = row.icao_type

    return AircraftResponse(
        id=row.id,
        icao_type=row.icao_type,
        type_name=type_name,
        tail_number=row.tail_number if include_tail else None,
        nickname=row.nickname if include_tail else None,
        is_ifr=row.is_ifr,
        is_fiki=row.is_fiki,
        cruise_speed_kt=row.cruise_speed_kt,
        ceiling_ft=row.ceiling_ft,
        is_default=row.is_default,
        created_at=row.created_at.isoformat(),
    )


# --- Request models ---


class CreateAircraftRequest(BaseModel):
    icao_type: str
    tail_number: str | None = None
    nickname: str | None = None
    is_ifr: bool = False
    is_fiki: bool = False
    cruise_speed_kt: int | None = None
    ceiling_ft: int | None = None
    is_default: bool = False

    @field_validator("icao_type")
    @classmethod
    def validate_icao_type(cls, v: str) -> str:
        v = v.strip().upper()
        if not v or len(v) > 4:
            raise ValueError("ICAO type must be 1-4 characters")
        return v

    @field_validator("tail_number")
    @classmethod
    def validate_tail_number(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        v = v.strip().upper()
        if not TAIL_NUMBER_RE.match(v):
            raise ValueError("Tail number must be 2-10 alphanumeric characters or hyphens")
        return v


class UpdateAircraftRequest(BaseModel):
    icao_type: str | None = None
    tail_number: str | None = None
    nickname: str | None = None
    is_ifr: bool | None = None
    is_fiki: bool | None = None
    cruise_speed_kt: int | None = None
    ceiling_ft: int | None = None
    is_default: bool | None = None

    @field_validator("icao_type")
    @classmethod
    def validate_icao_type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if not v or len(v) > 4:
            raise ValueError("ICAO type must be 1-4 characters")
        return v

    @field_validator("tail_number")
    @classmethod
    def validate_tail_number(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.strip() == "":
            return None
        v = v.strip().upper()
        if not TAIL_NUMBER_RE.match(v):
            raise ValueError("Tail number must be 2-10 alphanumeric characters or hyphens")
        return v


# --- Endpoints ---


@router.get("/types", response_model=list[AircraftTypeResponse])
def search_types(
    q: str = Query("", max_length=20),
    user_id: str = Depends(current_user_id),
):
    """Search ICAO aircraft types for autocomplete."""
    results = search_aircraft_types(q, limit=20)
    return [
        AircraftTypeResponse(
            icao=r["icao"],
            manufacturer=r.get("manufacturer", ""),
            model=r.get("model", ""),
            category=r.get("category"),
        )
        for r in results
    ]


@router.get("", response_model=list[AircraftResponse])
def list_user_aircraft(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List all aircraft for the current user."""
    rows = list_aircraft(db, user_id)
    return [_row_to_response(r) for r in rows]


@router.post("", response_model=AircraftResponse, status_code=201)
def add_aircraft(
    req: CreateAircraftRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Add an aircraft to the user's list."""
    row = create_aircraft(
        db,
        user_id,
        icao_type=req.icao_type,
        tail_number=req.tail_number,
        nickname=req.nickname,
        is_ifr=req.is_ifr,
        is_fiki=req.is_fiki,
        cruise_speed_kt=req.cruise_speed_kt,
        ceiling_ft=req.ceiling_ft,
        is_default=req.is_default,
    )
    return _row_to_response(row)


@router.put("/{aircraft_id}", response_model=AircraftResponse)
def edit_aircraft(
    aircraft_id: int,
    req: UpdateAircraftRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update an aircraft."""
    row = get_aircraft(db, aircraft_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    # Allow explicitly clearing tail_number/nickname with empty string
    if req.tail_number is not None:
        updates["tail_number"] = req.tail_number
    if req.nickname is not None:
        updates["nickname"] = req.nickname

    if updates:
        row = update_aircraft(db, row, **updates)
    return _row_to_response(row)


@router.delete("/{aircraft_id}", status_code=204)
def remove_aircraft(
    aircraft_id: int,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Remove an aircraft from the user's list."""
    row = get_aircraft(db, aircraft_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    delete_aircraft(db, row)
