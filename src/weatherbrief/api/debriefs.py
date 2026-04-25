"""API endpoints for flight debriefs and per-user summary stats."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

from weatherbrief.db.models import FlightRow
from weatherbrief.debriefs.stats import DEFAULT_WINDOW_DAYS, compute_stats, DebriefStats
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision, OutcomeValue
from weatherbrief.models import FlightDebrief
from weatherbrief.storage.debriefs import (
    delete_debrief,
    get_debrief,
    list_debriefs_for_user,
    upsert_debrief,
)
from weatherbrief.storage.flights import list_flights

logger = logging.getLogger(__name__)

router = APIRouter(tags=["debriefs"])


# --- Request / response models ---


class DebriefRequest(BaseModel):
    """Body for PUT /flights/{id}/debrief — full upsert.

    Validation runs through the FlightDebrief Pydantic model on the storage
    layer; this shape just collects fields off the wire.
    """

    decision: Decision
    reasons: list[ConditionTag] = Field(default_factory=list)
    outcomes: dict[ConditionTag, OutcomeValue] = Field(default_factory=dict)
    note: str | None = None


class DebriefResponse(BaseModel):
    """Wire shape for a stored debrief.

    Tag/outcome enum values serialise as strings ("IMC", "consistent") so
    the TS client doesn't have to know about Python enum names.
    """

    flight_id: str
    decision: str
    reasons: list[str] = Field(default_factory=list)
    outcomes: dict[str, str] = Field(default_factory=dict)
    note: str | None = None
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, d: FlightDebrief) -> "DebriefResponse":
        return cls(
            flight_id=d.flight_id,
            decision=d.decision.value,
            reasons=[t.value for t in d.reasons],
            outcomes={t.value: v.value for t, v in d.outcomes.items()},
            note=d.note,
            created_at=d.created_at.isoformat(),
            updated_at=d.updated_at.isoformat(),
        )


class StatsCategoryResponse(BaseModel):
    queried_count: int
    consistent: int
    better: int
    worse: int


class StatsResponse(BaseModel):
    """Wire shape for /debriefs/stats."""

    window_days: int
    total_flights_in_window: int
    flown_count: int
    cancelled_count: int
    monitoring_count: int
    pending_debrief_count: int
    cancellation_reasons: dict[str, int]
    category_accuracy: dict[str, StatsCategoryResponse]

    @classmethod
    def from_stats(cls, s: DebriefStats) -> "StatsResponse":
        return cls(
            window_days=s.window_days,
            total_flights_in_window=s.total_flights_in_window,
            flown_count=s.flown_count,
            cancelled_count=s.cancelled_count,
            monitoring_count=s.monitoring_count,
            pending_debrief_count=s.pending_debrief_count,
            cancellation_reasons={k.value: v for k, v in s.cancellation_reasons.items()},
            category_accuracy={
                k.value: StatsCategoryResponse(
                    queried_count=v.queried_count,
                    consistent=v.consistent,
                    better=v.better,
                    worse=v.worse,
                )
                for k, v in s.category_accuracy.items()
            },
        )


# --- Helpers ---


def _load_owned_flight_or_404(db: Session, flight_id: str, user_id: str) -> FlightRow:
    row = db.get(FlightRow, flight_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return row


# --- Endpoints ---


@router.get("/flights/{flight_id}/debrief", response_model=DebriefResponse)
def get_flight_debrief(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Load the debrief for a flight. 404 if no row exists."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    d = get_debrief(db, flight_id)
    if d is None:
        raise HTTPException(status_code=404, detail="No debrief recorded for this flight")
    return DebriefResponse.from_model(d)


@router.put("/flights/{flight_id}/debrief", response_model=DebriefResponse)
def upsert_flight_debrief(
    flight_id: str,
    req: DebriefRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Insert or update the debrief for a flight."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    try:
        saved = upsert_debrief(
            db,
            flight_id=flight_id,
            decision=req.decision,
            reasons=req.reasons,
            outcomes=req.outcomes,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return DebriefResponse.from_model(saved)


@router.delete("/flights/{flight_id}/debrief", status_code=204)
def delete_flight_debrief(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Remove the debrief for a flight. Idempotent."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    delete_debrief(db, flight_id)


@router.get("/debriefs/stats", response_model=StatsResponse)
def get_user_debrief_stats(
    window_days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Aggregate stats for the current user's flights within ``window_days``."""
    flights = list_flights(db, user_id)
    debriefs = list_debriefs_for_user(db, user_id)
    stats = compute_stats(flights, debriefs, window_days=window_days)
    return StatsResponse.from_stats(stats)
