"""API endpoints for flight debriefs and per-user summary stats."""

from __future__ import annotations

import logging
from typing import Literal

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
    # #1023: ``questions`` = the Debrief section's flown / cancelled tap; a
    # flown flight's outcomes then come from its answers and ``outcomes`` is
    # ignored. Omitted = the old form (``legacy``), which older apps send.
    form: Literal["questions", "legacy"] = "legacy"


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
    form: str | None = None
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
            form=d.form,
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
    db: Session = Depends(get_db, scope="function"),
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
    db: Session = Depends(get_db, scope="function"),
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
            form=req.form,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return DebriefResponse.from_model(saved)


@router.delete("/flights/{flight_id}/debrief", status_code=204)
def delete_flight_debrief(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db, scope="function"),
):
    """Remove the debrief for a flight. Idempotent."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    delete_debrief(db, flight_id)


@router.get("/debriefs/stats", response_model=StatsResponse)
def get_user_debrief_stats(
    window_days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db, scope="function"),
):
    """Aggregate stats for the current user's flights within ``window_days``."""
    flights = list_flights(db, user_id)
    debriefs = list_debriefs_for_user(db, user_id)
    stats = compute_stats(flights, debriefs, window_days=window_days)
    return StatsResponse.from_stats(stats)


# --- Debrief report and answers (#1023) ---


class AnswerItem(BaseModel):
    """One answer. ``answer`` null clears it."""

    question_id: str = Field(max_length=64)
    answer: str | None = None
    avoided: bool = False
    note: str | None = Field(default=None, max_length=300)


class AnswersRequest(BaseModel):
    """Body for PUT /flights/{id}/debrief/answers: one or more answers."""

    answers: list[AnswerItem] = Field(min_length=1, max_length=10)


@router.get("/flights/{flight_id}/debrief/report")
def get_flight_debrief_report(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    """The post-flight debrief report (#1023): headline and detail lines,
    forecast / observed strip, airports at actual times, the questions with
    their answers, and whether the flight earns a push. Built on request from
    the recap, the forecast pack and the live history; owner only."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    from weatherbrief.debriefs.report import report_for_flight

    return report_for_flight(db, flight_id)


@router.put("/flights/{flight_id}/debrief/answers")
def put_flight_debrief_answers(
    flight_id: str,
    req: AnswersRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    """Store answers to questions the report asks now, fold them into the
    flight's debrief (flown, form ``questions``, derived outcomes) and return
    the report. 404 for a question the report does not ask; 422 for an
    answer the question does not offer."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    from weatherbrief.debriefs.report import REPORT_VERSION, report_for_flight, validate_answer
    from weatherbrief.storage.debriefs import save_answers

    report = report_for_flight(db, flight_id)
    asked = {q["id"]: q for q in report.get("questions") or []}
    items = []
    for a in req.answers:
        question = asked.get(a.question_id)
        if question is None:
            raise HTTPException(status_code=404, detail=f"No question {a.question_id!r} for this flight")
        if a.answer is not None:
            try:
                validate_answer(question, a.answer, a.avoided)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        items.append((question, a.answer, a.avoided, a.note))
    save_answers(db, flight_id, items, rules_version=REPORT_VERSION)
    return report_for_flight(db, flight_id)


@router.get("/flights/{flight_id}/debrief/summary")
def get_flight_debrief_summary(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    """``{"debrief": ...}``: the headline and open questions stored at window
    close, for the agents (``get_briefing``); ``null`` before. Owner only."""
    _load_owned_flight_or_404(db, flight_id, user_id)
    from weatherbrief.tasks.debrief_close import debrief_summary

    return {"debrief": debrief_summary(db, flight_id)}
