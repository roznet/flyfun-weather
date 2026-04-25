"""Flight debrief storage — sidecar table CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightDebriefRow, FlightRow
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision, OutcomeValue
from weatherbrief.models import FlightDebrief


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
) -> FlightDebrief:
    """Insert or replace the debrief for a flight.

    On insert, ``created_at`` and ``updated_at`` are set to now.
    On update, ``created_at`` is preserved and ``updated_at`` is bumped.
    The Pydantic ``FlightDebrief`` is rebuilt for validation before write.
    """
    reasons_list = reasons or []
    outcomes_dict = outcomes or {}

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
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.decision = validated.decision.value
        row.reasons_json = reasons_json
        row.outcomes_json = outcomes_json
        row.note = validated.note
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
