"""Flight debrief storage — sidecar table CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    FlightDebriefAnswerRow,
    FlightDebriefReportRow,
    FlightDebriefRow,
    FlightRow,
)
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision, OutcomeValue
from weatherbrief.models import FlightDebrief


FORM_QUESTIONS = "questions"
FORM_LEGACY = "legacy"
FORMS = (FORM_QUESTIONS, FORM_LEGACY)


def _row_to_model(row: FlightDebriefRow) -> FlightDebrief:
    reasons: list[ConditionTag] = []
    if row.reasons_json:
        reasons = [ConditionTag(t) for t in json.loads(row.reasons_json)]

    outcomes: dict[ConditionTag, OutcomeValue] = {}
    if row.outcomes_json:
        raw = json.loads(row.outcomes_json)
        outcomes = {ConditionTag(k): OutcomeValue(v) for k, v in raw.items()}

    return FlightDebrief(
        flight_id=row.flight_id,
        decision=Decision(row.decision),
        reasons=reasons,
        outcomes=outcomes,
        note=row.note,
        form=row.form,
        created_at=row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc),
        updated_at=row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=timezone.utc),
    )


def get_debrief(db: Session, flight_id: str) -> FlightDebrief | None:
    """Load the debrief for a flight, or None if absent."""
    row = db.get(FlightDebriefRow, flight_id)
    return _row_to_model(row) if row else None


def upsert_debrief(
    db: Session,
    *,
    flight_id: str,
    decision: Decision,
    reasons: list[ConditionTag] | None = None,
    outcomes: dict[ConditionTag, OutcomeValue] | None = None,
    note: str | None = None,
    form: str = FORM_LEGACY,
) -> FlightDebrief:
    """Insert or replace the debrief for a flight.

    ``form`` (#1023): ``legacy`` = the old 8-category form wrote ``outcomes``;
    ``questions`` = for a flown flight the outcomes are derived from the
    stored debrief answers (:func:`save_answers`) and the ``outcomes``
    argument is ignored.

    On insert, ``created_at`` and ``updated_at`` are set to now.
    On update, ``created_at`` is preserved and ``updated_at`` is bumped.
    The Pydantic ``FlightDebrief`` is rebuilt for validation before write.
    """
    if form not in FORMS:
        raise ValueError(f"Unknown debrief form {form!r}")
    reasons_list = reasons or []
    outcomes_dict = outcomes or {}
    if form == FORM_QUESTIONS:
        outcomes_dict = _derived_outcomes(db, flight_id) if decision is Decision.FLOWN else {}

    now = datetime.now(timezone.utc)
    row = db.get(FlightDebriefRow, flight_id)

    # Rebuild via the Pydantic model to enforce all decision-shape invariants
    # (reasons-only-for-cancel, outcomes-only-for-flown, note length, etc.).
    validated = FlightDebrief(
        flight_id=flight_id,
        decision=decision,
        reasons=reasons_list,
        outcomes=outcomes_dict,
        note=note,
        created_at=row.created_at if row else now,
        updated_at=now,
    )

    reasons_json = json.dumps([t.value for t in validated.reasons]) if validated.reasons else None
    outcomes_json = (
        json.dumps({k.value: v.value for k, v in validated.outcomes.items()})
        if validated.outcomes
        else None
    )

    if row is None:
        row = FlightDebriefRow(
            flight_id=flight_id,
            decision=validated.decision.value,
            reasons_json=reasons_json,
            outcomes_json=outcomes_json,
            note=validated.note,
            form=form,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.decision = validated.decision.value
        row.reasons_json = reasons_json
        row.outcomes_json = outcomes_json
        row.note = validated.note
        row.form = form
        row.updated_at = now

    db.flush()
    return _row_to_model(row)


def delete_debrief(db: Session, flight_id: str) -> bool:
    """Delete the debrief for a flight. Returns True if a row was removed."""
    row = db.get(FlightDebriefRow, flight_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def list_debriefs_for_user(
    db: Session,
    user_id: str,
    *,
    since: datetime | None = None,
) -> list[FlightDebrief]:
    """Load all debriefs owned by a user, optionally filtered by created_at >= since."""
    stmt = (
        select(FlightDebriefRow)
        .join(FlightRow, FlightDebriefRow.flight_id == FlightRow.id)
        .where(FlightRow.user_id == user_id)
    )
    if since is not None:
        stmt = stmt.where(FlightDebriefRow.created_at >= since)
    rows = db.execute(stmt).scalars().all()
    return [_row_to_model(r) for r in rows]


def list_debriefed_flight_ids(db: Session) -> set[str]:
    """All flight IDs that have a debrief — used by retention exemption."""
    rows = db.execute(select(FlightDebriefRow.flight_id)).scalars().all()
    return set(rows)


def bulk_get_debriefs(db: Session, flight_ids: list[str]) -> dict[str, FlightDebrief]:
    """Load debriefs for a list of flight IDs in one query."""
    if not flight_ids:
        return {}
    rows = db.execute(
        select(FlightDebriefRow).where(FlightDebriefRow.flight_id.in_(flight_ids))
    ).scalars().all()
    return {r.flight_id: _row_to_model(r) for r in rows}


# --- Debrief answers (#1023) ---------------------------------------------------


def _answer_dict(row: FlightDebriefAnswerRow) -> dict:
    answered = row.answered_at if row.answered_at.tzinfo else row.answered_at.replace(tzinfo=timezone.utc)
    return {
        "kind": row.kind,
        "answer": row.answer,
        "avoided": bool(row.avoided),
        "note": row.note,
        "answered_at": answered.isoformat(),
        "rules_version": row.rules_version,
        "question": json.loads(row.question_json),
    }


def load_answers(db: Session, flight_id: str) -> dict[str, dict]:
    """The flight's stored answers, keyed by question id."""
    rows = db.execute(
        select(FlightDebriefAnswerRow).where(FlightDebriefAnswerRow.flight_id == flight_id)
    ).scalars().all()
    return {r.question_id: _answer_dict(r) for r in rows}


def _derived_outcomes(db: Session, flight_id: str) -> dict[ConditionTag, OutcomeValue]:
    from weatherbrief.debriefs.report import outcomes_from_answers

    derived = outcomes_from_answers(load_answers(db, flight_id).values())
    return {ConditionTag(k): OutcomeValue(v) for k, v in derived.items()}


def save_answers(
    db: Session,
    flight_id: str,
    items: list[tuple[dict, str | None, bool, str | None]],
    *,
    rules_version: str,
    now: datetime | None = None,
) -> FlightDebrief | None:
    """Store (or, with answer None, clear) answers, then fold them into the
    flight's debrief: decision ``flown``, form ``questions``, outcomes
    derived from every stored answer (:func:`outcomes_from_answers`). When
    no answer remains, a derived debrief without a note is removed (returns
    None) rather than left as "flown, nothing to report".

    ``items``: (question as the report asks it, answer, avoided, note). The
    caller has validated each answer against its question. A debrief note
    already written is kept; cancel reasons go (the flight was flown).
    """
    from weatherbrief.debriefs.report import question_as_asked

    now = now or datetime.now(timezone.utc)
    existing = {
        r.question_id: r
        for r in db.execute(
            select(FlightDebriefAnswerRow).where(FlightDebriefAnswerRow.flight_id == flight_id)
        ).scalars().all()
    }
    for question, answer, avoided, note in items:
        row = existing.get(question["id"])
        if answer is None:
            if row is not None:
                db.delete(row)
            continue
        note = (note or "").strip() or None
        fields = dict(
            kind=question["kind"],
            answer=answer,
            avoided=bool(avoided),
            note=note,
            answered_at=now,
            rules_version=rules_version,
            question_json=json.dumps(question_as_asked(question)),
            forecast_json=json.dumps(question.get("forecast")) if question.get("forecast") else None,
        )
        if row is None:
            db.add(FlightDebriefAnswerRow(flight_id=flight_id, question_id=question["id"], **fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
    db.flush()
    current = db.get(FlightDebriefRow, flight_id)
    remaining = db.execute(
        select(FlightDebriefAnswerRow.id).where(FlightDebriefAnswerRow.flight_id == flight_id).limit(1)
    ).first()
    if remaining is None:
        # Every answer cleared: a derived "flown, nothing to report" would
        # read as "all as forecast". Drop the derived debrief unless the
        # pilot wrote a note on it; the flight is back to not debriefed.
        if current is not None and current.form == FORM_QUESTIONS and not current.note:
            db.delete(current)
            db.flush()
            return None
        if current is None:
            return None
    return upsert_debrief(
        db, flight_id=flight_id, decision=Decision.FLOWN,
        note=current.note if current is not None else None, form=FORM_QUESTIONS,
    )


# --- Window-close report (#1023) -------------------------------------------------


def get_report_row(db: Session, flight_id: str) -> FlightDebriefReportRow | None:
    return db.get(FlightDebriefReportRow, flight_id)


def bulk_report_flags(db: Session, flight_ids: list[str]) -> dict[str, dict]:
    """``{flight_id: {"debrief_ready": True, "questions_open": n}}`` for the
    flights whose report was built at window close, in two queries: the
    flights list carries these flags only, never the report."""
    if not flight_ids:
        return {}
    reports = db.execute(
        select(FlightDebriefReportRow.flight_id, FlightDebriefReportRow.questions_json)
        .where(FlightDebriefReportRow.flight_id.in_(flight_ids))
    ).all()
    if not reports:
        return {}
    answered: dict[str, set[str]] = {}
    for fid, qid in db.execute(
        select(FlightDebriefAnswerRow.flight_id, FlightDebriefAnswerRow.question_id)
        .where(FlightDebriefAnswerRow.flight_id.in_([r[0] for r in reports]))
    ).all():
        answered.setdefault(fid, set()).add(qid)
    out = {}
    for fid, questions_json in reports:
        asked = [q["id"] for q in json.loads(questions_json or "[]")]
        out[fid] = {"debrief_ready": True,
                    "questions_open": sum(1 for q in asked if q not in answered.get(fid, set()))}
    return out
