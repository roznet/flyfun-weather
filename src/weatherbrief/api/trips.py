"""API endpoints for flight trips (issue #602).

A trip groups flights whose viability is conjunctive — all remaining legs must
work — and the endpoints here are deliberately thin. The interesting logic lives
in two places neither of which is FastAPI-aware:

* :mod:`weatherbrief.trips` — the pure deterministic summary (binding leg,
  chain status, decision ripeness). Never persisted.
* :mod:`weatherbrief.api.trip_refresh` — the serial one-leg-at-a-time refresh
  driver.

The one thing this module owns is the DB→pure-input adaptation, so that "what
the trip summary is computed from" has a single definition shared by the trip
page, the flights list, the notification coalescer and the AI guardrail.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from weatherbrief.db.models import FlightRow, FlightTripRow
from weatherbrief.models import Flight, FlightTrip
from weatherbrief.storage import trips as trip_storage
from weatherbrief.storage.debriefs import bulk_get_debriefs
from weatherbrief.trips import TripLegInput, TripSummary, summarize_trip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])

#: Upper bound on legs in one trip. A trip refresh is serial, so this is also
#: the worst-case latency multiplier on the "Refresh trip" button; well above
#: any realistic chain (the worked examples are 2-3 legs) and low enough that a
#: pathological request can't queue an hour of pipeline work.
MAX_TRIP_LEGS = 12


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TripLegRef(BaseModel):
    """The ``trip`` block embedded on every member flight's ``FlightResponse``.

    Small on purpose — it is what lets the iOS list group flights and show a
    trip badge without a new screen or a second request.
    """

    id: str
    name: str
    position: int  # 1-based index in departure-time order
    total: int


class TripResponse(BaseModel):
    """A trip container plus its derived summary."""

    id: str
    user_id: str
    name: str
    notes: str | None = None
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    notify_override: Literal["default", "notify", "mute"] = "default"
    created_at: str
    flight_ids: list[str] = Field(default_factory=list)
    summary: TripSummary
    # Persisted Haiku paragraph, when one has been generated and is still
    # keyed to the current member packs. Deliberately secondary to
    # ``summary.headline`` — see designs/flight-trips.md.
    ai_summary: str | None = None
    ai_summary_at: str | None = None
    ai_summary_stale: bool = False
    # Live serial-refresh progress, when a trip refresh is in flight.
    refresh: "TripRefreshStatus | None" = None


class TripRefreshStatus(BaseModel):
    """Progress of the serial trip-refresh driver."""

    trip_id: str
    refresh_id: str | None = None
    active: bool = False
    total: int = 0
    completed: int = 0
    current_flight_id: str | None = None
    # flight_id -> "succeeded" | "skipped" | "failed" | "busy"
    results: dict[str, str] = Field(default_factory=dict)
    # Human line for the "2 of 3 legs had new data" readout.
    message: str = ""


class CreateTripRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    flight_ids: list[str] = Field(default_factory=list)


class UpdateTripRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    auto_refresh: bool | None = None
    auto_refresh_hour: int | None = Field(default=None, ge=0, le=23)
    notify_override: Literal["default", "notify", "mute"] | None = None


class AddLegsRequest(BaseModel):
    flight_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DB → pure input adaptation
# ---------------------------------------------------------------------------


def build_leg_inputs(db: Session, flights: list[Flight]) -> list[TripLegInput]:
    """Flatten member flights + their latest packs into pure summary inputs.

    One query for the packs (the same ``_get_latest_packs`` the flights list
    uses, so the trip card and the flight card can never disagree) and one for
    the debriefs. No pack-file reads — everything comes off the denormalized
    columns on ``briefing_packs``.
    """
    # Imported here rather than at module scope: ``api.flights`` imports this
    # module for the ``trip`` field on FlightResponse, so a top-level import
    # would close the cycle.
    from weatherbrief.api.flights import _compute_coverage, _get_latest_packs

    packs = _get_latest_packs(db, [f.id for f in flights])
    debriefs = bulk_get_debriefs(db, [f.id for f in flights])

    inputs: list[TripLegInput] = []
    for flight in flights:
        status = packs.get(flight.id)
        debrief = debriefs.get(flight.id)
        inputs.append(
            TripLegInput(
                flight_id=flight.id,
                waypoints=flight.waypoints,
                route_name=flight.route_name,
                departure_time=flight.departure_time,
                duration_hours=flight.flight_duration_hours,
                days_out=status.days_out if status else None,
                assessment=status.assessment if status else None,
                assessment_reason=status.assessment_reason if status else None,
                outlook=status.outlook if status else None,
                outlook_reason=status.outlook_reason if status else None,
                advisory_summary=status.advisory_summary if status else None,
                fetch_timestamp=(
                    datetime.fromisoformat(status.fetch_timestamp)
                    if status and status.fetch_timestamp
                    else None
                ),
                pending_coverage=_compute_coverage(flight.departure_time) is not None,
                debrief_decision=debrief.decision if debrief else None,
            )
        )
    return inputs


def build_trip_summary(
    db: Session, trip: FlightTrip,
) -> tuple[TripSummary, list[Flight], list[TripLegInput]]:
    """The deterministic summary, the ordered member legs, and the pure inputs.

    The inputs come back rather than being rebuilt by the caller: they cost a
    packs query and a debriefs query, and every caller that wants the summary
    also wants the AI-summary key derived from the same inputs.
    """
    members = trip_storage.trip_members(db, trip.id)
    leg_inputs = build_leg_inputs(db, members)
    summary = summarize_trip(trip.id, leg_inputs, name=trip.name)
    return summary, members, leg_inputs


def ai_summary_key(legs: list[TripLegInput]) -> str:
    """Content key for the AI paragraph — everything that can change what it says.

    Regenerating on unchanged inputs is the one failure mode that turns a
    fraction-of-a-cent feature into a recurring line item, so the key is what
    the summary was *actually* written from, not a timestamp.

    ``debrief_decision`` is in the key alongside the pack timestamp because it
    feeds ``_pick_binding_leg``: marking the current binding leg cancelled or
    flown changes which leg decides the trip **without changing any**
    ``fetch_timestamp``. Keyed on packs alone, the stale paragraph — still
    naming a leg that no longer matters — would keep being served as fresh.
    """
    import hashlib

    parts = sorted(
        f"{leg.flight_id}"
        f":{leg.fetch_timestamp.isoformat() if leg.fetch_timestamp else '-'}"
        f":{leg.debrief_decision or '-'}"
        for leg in legs
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:64]


def derive_trip_name(summary: TripSummary) -> str:
    """Default name from the chain and its date span: "EGTF → LSGS → EGTF, 20–22 Feb"."""
    if not summary.legs:
        return "New trip"
    first = summary.legs[0].departure_time
    last = summary.legs[-1].departure_time
    # ``%-d`` is a glibc extension; build the day number by hand so the name
    # derivation can't differ by platform.
    def _day_month(dt) -> str:
        return f"{dt.day} {dt.strftime('%b')}"

    if first.date() == last.date():
        span = _day_month(first)
    elif first.month == last.month:
        span = f"{first.day}–{_day_month(last)}"
    else:
        span = f"{_day_month(first)}–{_day_month(last)}"
    chain = summary.chain_label or "Trip"
    return f"{chain}, {span}"[:200]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owned_trip_row(db: Session, trip_id: str, user_id: str) -> FlightTripRow:
    row = trip_storage.load_trip_row(db, trip_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return row


def _validate_owned_flights(db: Session, flight_ids: list[str], user_id: str) -> list[str]:
    """Keep only ids the user owns; 404 if any is unknown.

    Strict rather than silent: "group these 3" quietly grouping 2 is exactly the
    kind of partial success that makes a pilot trust the chain less than they
    should.
    """
    if not flight_ids:
        return []
    rows = db.execute(
        select(FlightRow.id).where(
            FlightRow.id.in_(flight_ids), FlightRow.user_id == user_id,
        )
    ).scalars().all()
    missing = set(flight_ids) - set(rows)
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Flight(s) not found: {', '.join(sorted(missing))}",
        )
    return list(dict.fromkeys(flight_ids))


def _trip_to_response(db: Session, trip: FlightTrip) -> TripResponse:
    from weatherbrief.api import trip_refresh

    summary, members, leg_inputs = build_trip_summary(db, trip)
    stale = bool(trip.ai_summary_text) and trip.ai_summary_key != ai_summary_key(leg_inputs)
    row = db.get(FlightTripRow, trip.id)
    return TripResponse(
        id=trip.id,
        user_id=trip.user_id,
        name=trip.name,
        notes=trip.notes,
        auto_refresh=trip.auto_refresh,
        auto_refresh_hour=trip.auto_refresh_hour,
        notify_override=trip.notify_override,
        created_at=trip.created_at.isoformat(),
        flight_ids=[f.id for f in members],
        summary=summary,
        ai_summary=trip.ai_summary_text,
        ai_summary_at=trip.ai_summary_at.isoformat() if trip.ai_summary_at else None,
        ai_summary_stale=stale,
        refresh=trip_refresh.status(row) if row is not None else None,
    )


def trip_ref_for(db: Session, trip_id: str, flight_id: str) -> TripLegRef | None:
    """The ``trip`` block for one flight — for the single-flight endpoints.

    Deliberately not ``bulk_trip_refs`` narrowed down: that builds the map for
    every trip the user owns, which is right for the list endpoint and wasteful
    for a single ``GET /flights/{id}``.
    """
    row = db.get(FlightTripRow, trip_id)
    if row is None:
        return None
    member_ids = db.execute(
        select(FlightRow.id)
        .where(FlightRow.trip_id == trip_id)
        .order_by(FlightRow.departure_time.asc())
    ).scalars().all()
    if flight_id not in member_ids:
        return None
    return TripLegRef(
        id=trip_id,
        name=row.name or "",
        position=member_ids.index(flight_id) + 1,
        total=len(member_ids),
    )


def bulk_trip_refs(db: Session, user_id: str) -> dict[str, TripLegRef]:
    """``flight_id -> TripLegRef`` for every grouped flight the user owns.

    Two queries for the whole flights list, computed once per request: the trips
    and their members. Position is derived from ``departure_time`` here, the
    same rule the summary uses.
    """
    trip_rows = db.execute(
        select(FlightTripRow.id, FlightTripRow.name).where(
            FlightTripRow.user_id == user_id
        )
    ).all()
    if not trip_rows:
        return {}
    names = {trip_id: name for trip_id, name in trip_rows}
    members = db.execute(
        select(FlightRow.id, FlightRow.trip_id)
        .where(FlightRow.trip_id.in_(list(names)))
        .order_by(FlightRow.departure_time.asc())
    ).all()

    by_trip: dict[str, list[str]] = {}
    for flight_id, trip_id in members:
        by_trip.setdefault(trip_id, []).append(flight_id)

    refs: dict[str, TripLegRef] = {}
    for trip_id, flight_ids in by_trip.items():
        for index, flight_id in enumerate(flight_ids, start=1):
            refs[flight_id] = TripLegRef(
                id=trip_id,
                name=names.get(trip_id) or "",
                position=index,
                total=len(flight_ids),
            )
    return refs


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TripResponse])
def list_trips(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """All of the caller's trips, each with its derived summary."""
    return [_trip_to_response(db, trip) for trip in trip_storage.list_trips(db, user_id)]


@router.post("", response_model=TripResponse, status_code=201)
def create_trip(
    req: CreateTripRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Group flights into a new trip.

    A **1-leg trip is valid** — adding legs later is a normal flow, not a
    repair, and it is what makes "book the outbound now, add the return when it
    comes into range" work against the booking cap.
    """
    flight_ids = _validate_owned_flights(db, req.flight_ids, user_id)
    if len(flight_ids) > MAX_TRIP_LEGS:
        raise HTTPException(
            status_code=422,
            detail=f"A trip can hold at most {MAX_TRIP_LEGS} legs.",
        )

    trip = trip_storage.create_trip(db, user_id, req.name or "")
    trip_storage.set_leg_trip(db, flight_ids, user_id, trip.id)
    # A leg can leave a previous trip empty on the way in — but the trip we just
    # created is exempt. Without that, `flight_ids: []` creates a row and then
    # deletes it one line later, and the handler goes on to return a 201 for a
    # trip that no longer exists (or 404s mid-request while auto-naming it).
    trip_storage.prune_empty_trips(db, user_id, keep=trip.id)

    if not req.name:
        summary, _members, _inputs = build_trip_summary(db, trip)
        row = _owned_trip_row(db, trip.id, user_id)
        row.name = derive_trip_name(summary)
        db.flush()
        trip = trip_storage.load_trip(db, trip.id, user_id)

    db.commit()
    return _trip_to_response(db, trip)


@router.get("/{trip_id}", response_model=TripResponse)
def get_trip(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _owned_trip_row(db, trip_id, user_id)
    return _trip_to_response(db, trip_storage.load_trip(db, trip_id, user_id))


@router.get("/{trip_id}/summary", response_model=TripSummary)
def get_trip_summary(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """The pure ``TripSummary`` on its own — computed, never stored."""
    _owned_trip_row(db, trip_id, user_id)
    summary, _members, _inputs = build_trip_summary(
        db, trip_storage.load_trip(db, trip_id, user_id),
    )
    return summary


@router.patch("/{trip_id}", response_model=TripResponse)
def update_trip(
    trip_id: str,
    req: UpdateTripRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    row = _owned_trip_row(db, trip_id, user_id)
    if req.name is not None:
        row.name = req.name.strip()[:200]
    if req.notes is not None:
        row.notes = req.notes.strip() or None
    if req.auto_refresh is not None:
        row.auto_refresh = req.auto_refresh
    if req.auto_refresh_hour is not None:
        row.auto_refresh_hour = req.auto_refresh_hour
    if req.notify_override is not None:
        row.notify_override = req.notify_override
    db.commit()
    return _trip_to_response(db, trip_storage.load_trip(db, trip_id, user_id))


@router.delete("/{trip_id}", status_code=204)
def delete_trip(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Delete the trip container. Its legs survive, unlinked — never deleted."""
    if not trip_storage.delete_trip(db, trip_id, user_id):
        raise HTTPException(status_code=404, detail="Trip not found")
    db.commit()


@router.post("/{trip_id}/legs", response_model=TripResponse)
def add_legs(
    trip_id: str,
    req: AddLegsRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _owned_trip_row(db, trip_id, user_id)
    flight_ids = _validate_owned_flights(db, req.flight_ids, user_id)
    member_ids = {f.id for f in trip_storage.trip_members(db, trip_id)}
    # Count only the legs that would actually be *added*: a client retry that
    # re-sends a leg already in the trip must not 422 a trip that isn't growing.
    incoming = [fid for fid in flight_ids if fid not in member_ids]
    if len(member_ids) + len(incoming) > MAX_TRIP_LEGS:
        raise HTTPException(
            status_code=422,
            detail=f"A trip can hold at most {MAX_TRIP_LEGS} legs.",
        )
    trip_storage.set_leg_trip(db, flight_ids, user_id, trip_id)
    trip_storage.prune_empty_trips(db, user_id)
    db.commit()
    return _trip_to_response(db, trip_storage.load_trip(db, trip_id, user_id))


@router.delete("/{trip_id}/legs/{flight_id}", response_model=None)
def remove_leg(
    trip_id: str,
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Unlink a leg from its trip — an unlink, **never** a delete.

    The flight and its packs are untouched; only the membership goes. Removing
    the last leg leaves an empty container, which is pruned here rather than
    lingering as a trip that can never show anything.
    """
    _owned_trip_row(db, trip_id, user_id)
    row = db.get(FlightRow, flight_id)
    if row is None or row.user_id != user_id or row.trip_id != trip_id:
        raise HTTPException(status_code=404, detail="Flight is not a member of this trip")
    row.trip_id = None
    db.flush()
    pruned = trip_storage.prune_empty_trips(db, user_id)
    db.commit()
    if trip_id in pruned:
        # Unlinking the last leg leaves an empty container, which is pruned
        # above. There is no trip left to return — 204 rather than a 404, which
        # would read as "your request failed" for a request that succeeded.
        return Response(status_code=204)
    return _trip_to_response(db, trip_storage.load_trip(db, trip_id, user_id))


@router.post("/{trip_id}/refresh", response_model=TripRefreshStatus)
def refresh_trip(
    trip_id: str,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Refresh every remaining leg — **one at a time, server-driven**.

    Not a fan-out: the refresh admission path rejects rather than queues
    (``MAX_PER_USER = 2``) and the process-wide executor has two slots, so
    enqueueing N legs would both fail on the third and monopolise the server for
    every other user. See :mod:`weatherbrief.api.trip_refresh`.
    """
    from weatherbrief.api import trip_refresh

    row = _owned_trip_row(db, trip_id, user_id)
    try:
        # ``start`` commits the run marker itself, under the driver lock — the
        # commit has to be inside that lock for a concurrent press to see it.
        status = trip_refresh.start(db, row, request.app.state, user_id)
    except trip_refresh.TripRefreshBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if status.active and status.refresh_id:
        trip_refresh.kick(trip_id, request.app.state, user_id, status.refresh_id)
    return status


@router.get("/{trip_id}/refresh/status", response_model=TripRefreshStatus)
def refresh_trip_status(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    from weatherbrief.api import trip_refresh

    row = _owned_trip_row(db, trip_id, user_id)
    return trip_refresh.status(row)


class TripAiSummaryResponse(BaseModel):
    trip_id: str
    text: str | None = None
    generated_at: str | None = None
    #: Why there is no AI paragraph, when there isn't one. ``ai_disabled`` is
    #: the inherited-gate case: any member leg with AI off means the whole trip
    #: gets the deterministic sentence only.
    unavailable_reason: Literal[
        "ai_disabled", "no_legs", "generation_failed", "guardrail_rejected",
    ] | None = None


@router.post("/{trip_id}/ai-summary", response_model=TripAiSummaryResponse)
def generate_ai_summary(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return the trip's AI paragraph, regenerating it only when stale.

    Keyed on the member ``(flight_id, fetch_timestamp)`` tuples, so opening the
    page repeatedly costs nothing.
    """
    from weatherbrief.digest.trip_summary import ensure_trip_ai_summary

    row = _owned_trip_row(db, trip_id, user_id)
    trip = trip_storage.load_trip(db, trip_id, user_id)
    summary, members, leg_inputs = build_trip_summary(db, trip)
    result = ensure_trip_ai_summary(
        db, row, summary, members, user_id=user_id, leg_inputs=leg_inputs,
    )
    db.commit()
    return TripAiSummaryResponse(
        trip_id=trip_id,
        text=result.text,
        generated_at=row.ai_summary_at.isoformat() if row.ai_summary_at else None,
        unavailable_reason=result.unavailable_reason,
    )


TripResponse.model_rebuild()
