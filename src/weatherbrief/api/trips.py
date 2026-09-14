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
from weatherbrief.db.models import FlightRow, FlightSubscriptionRow, FlightTripRow
from weatherbrief.models import Flight, FlightTrip
from weatherbrief.storage import trips as trip_storage
from weatherbrief.storage.flights import (
    SHARE_CODE_RE,
    SubscriptionError,
    subscribe_flight,
    unsubscribe_flight,
)
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
    #: The trip's own auto-refresh switch. Carried here because for a member
    #: leg it is the trip that decides *whether* to refresh — the briefing
    #: page shows this state and keeps the per-leg *hour* editable.
    auto_refresh: bool = False


class TripResponse(BaseModel):
    """A trip container plus its derived summary.

    Doubles as the *viewer* payload for a shared trip. Everything that is the
    owner's own business — notes, the refresh switches, the notification
    override, the live refresh progress and the AI paragraph — is left unset
    when ``role == "viewer"``; see ``_trip_to_response``.
    """

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
    #: Who is asking. ``viewer`` is a recipient of a share link: read-only, and
    #: the client must not offer refresh, rename, delete or membership edits.
    role: Literal["owner", "viewer"] = "owner"
    #: Set for ``viewer`` only, and only when the owner has a display name —
    #: never their email (same rule as the shared-flight line).
    owner_display_name: str | None = None
    #: The short ``/t/{code}`` token. Owner-only: handing a recipient the link
    #: they already followed is noise, and it is the owner's to give out.
    share_code: str | None = None
    #: Whether *every* member leg is already in the viewer's own list. The
    #: subscribe action is a loop over the legs — there is no trip-level
    #: subscription row, and deliberately so (see ``subscribe_trip``).
    is_subscribed: bool = False
    #: Owner-only: whether the link would actually resolve for a recipient
    #: right now. False when any leg is private, which is the one case the
    #: owner has to fix themselves, so the share control says so rather than
    #: handing out a link that 404s.
    is_shareable: bool = True
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
    # When the last run closed (ISO, UTC); None before any has. The results
    # outlive the run, so this is what lets a client stop showing a finished
    # run's readout once a leg has been refreshed on its own since.
    finished_at: str | None = None


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

    The derived **leg state** is in the key for the same reason, and it is the
    commoner case: a leg flips ``remaining`` → ``flown`` from the clock alone,
    with no debrief and no new pack, and ``_pick_binding_leg`` only considers
    remaining legs. So the binding leg changes identity as a departure passes,
    and without this the page would show a deterministic callout naming one leg
    beside a cached paragraph still naming another.
    """
    import hashlib

    from weatherbrief.trips import summarize_trip

    # Reuse the real state derivation rather than re-implementing the clock
    # rule here — two definitions of "flown" would be exactly the kind of drift
    # the single binding-leg rule exists to avoid.
    states = {leg.flight_id: leg.state for leg in summarize_trip("key", legs).legs}
    parts = sorted(
        f"{leg.flight_id}"
        f":{leg.fetch_timestamp.isoformat() if leg.fetch_timestamp else '-'}"
        f":{leg.debrief_decision or '-'}"
        f":{states.get(leg.flight_id, '-')}"
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
    """The trip, for an action only its owner may take.

    Every mutation goes through this, sharing or not: a recipient must never be
    able to rename, delete, re-group or **refresh** someone else's trip. Refresh
    is the sharp one — it spends the owner's money and is admitted against the
    *owner's* ``MAX_PER_USER`` slots, so a viewer-triggered chain would lock the
    owner out of their own refreshes.
    """
    row = trip_storage.load_trip_row(db, trip_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return row


def _viewable_trip_row(
    db: Session, trip_id: str, user_id: str
) -> tuple[FlightTripRow, Literal["owner", "viewer"]]:
    """The trip for a *read*, by its owner or by someone holding the link.

    Sharing a trip introduces no new permission: a trip is readable exactly
    when every one of its legs is (``storage.trips.is_shareable``), which is the
    same ``flights.private`` switch the per-leg share link already answers to.
    """
    found = trip_storage.load_trip_row_for_viewer(db, trip_id, user_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return found


def _load_trip_any_owner(db: Session, trip_id: str) -> FlightTrip:
    """The trip container, without the ownership filter.

    Access has already been decided by ``_viewable_trip_row``; re-passing the
    *viewer's* id to ``load_trip`` would come back None for a shared trip and
    blow up on the attribute access. Callers must not use this without a
    preceding access check.
    """
    trip = trip_storage.load_trip_unscoped(db, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def subscribed_member_ids(
    db: Session, flight_ids: list[str], user_id: str
) -> set[str]:
    """Which of ``flight_ids`` the caller already follows. One query."""
    if not flight_ids:
        return set()
    rows = db.execute(
        select(FlightSubscriptionRow.flight_id).where(
            FlightSubscriptionRow.flight_id.in_(flight_ids),
            FlightSubscriptionRow.user_id == user_id,
        )
    ).scalars().all()
    return set(rows)


def _subscribed_to_all(db: Session, flight_ids: list[str], user_id: str) -> bool:
    """True when the viewer already follows every member leg."""
    if not flight_ids:
        return False
    return subscribed_member_ids(db, flight_ids, user_id) >= set(flight_ids)


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


def _trip_to_response(
    db: Session,
    trip: FlightTrip,
    *,
    viewer_id: str | None = None,
    role: Literal["owner", "viewer"] = "owner",
    shareable: bool | None = None,
) -> TripResponse:
    """The wire payload, narrowed to what ``role`` is allowed to see.

    A viewer gets the chain and the legs — the whole point of the share — and
    nothing else. Withheld, each for its own reason:

    * ``notes`` — the owner's private scratchpad, not part of the briefing.
    * ``auto_refresh`` / ``auto_refresh_hour`` / ``notify_override`` — switches
      only the owner can flip, so showing their state is at best confusing.
    * ``refresh`` — live progress of a run only the owner can start.
    * ``ai_summary`` — the owner paid for it under *their* AI-digest consent;
      the recipient never agreed to it and the trip page's own deterministic
      headline says the same thing.
    * ``share_code`` — the owner's to hand out.

    ``shareable`` lets a caller with many trips pass the answer in from one
    batched ``shareable_trip_ids`` rather than have this run a grouped query per
    trip — the N+1 the batched helper exists to avoid. Omitted, it is looked up.
    """
    from weatherbrief.api import trip_refresh

    from weatherbrief.digest.trip_summary import legs_allow_ai

    summary, members, leg_inputs = build_trip_summary(db, trip)
    row = db.get(FlightTripRow, trip.id)
    flight_ids = [f.id for f in members]

    if role == "viewer":
        from weatherbrief.api.flights import _resolve_owner_display_name

        return TripResponse(
            id=trip.id,
            user_id=trip.user_id,
            name=trip.name,
            created_at=trip.created_at.isoformat(),
            flight_ids=flight_ids,
            summary=summary,
            role="viewer",
            owner_display_name=_resolve_owner_display_name(db, trip.user_id),
            is_subscribed=(
                _subscribed_to_all(db, flight_ids, viewer_id)
                if viewer_id is not None
                else False
            ),
        )

    stale = bool(trip.ai_summary_text) and trip.ai_summary_key != ai_summary_key(leg_inputs)
    # The consent gate applies to *reads* too, not just to generation. Turning
    # AI off on a leg touches no pack, so the key is unchanged and `stale` is
    # False — without this, every endpoint returning a stored paragraph would
    # keep serving it to a pilot who had switched AI off. `/trip.html` happens
    # to fetch the paragraph through its own endpoint today; the contract
    # should not depend on that staying true.
    ai_text = trip.ai_summary_text if legs_allow_ai(db, members, trip.user_id) else None
    return TripResponse(
        id=trip.id,
        user_id=trip.user_id,
        name=trip.name,
        notes=trip.notes,
        auto_refresh=trip.auto_refresh,
        auto_refresh_hour=trip.auto_refresh_hour,
        notify_override=trip.notify_override,
        created_at=trip.created_at.isoformat(),
        flight_ids=flight_ids,
        summary=summary,
        ai_summary=ai_text,
        ai_summary_at=trip.ai_summary_at.isoformat() if trip.ai_summary_at else None,
        ai_summary_stale=stale,
        refresh=trip_refresh.status(row) if row is not None else None,
        role="owner",
        # Minted lazily so a trip created before sharing existed still shares.
        share_code=trip_storage.ensure_share_code(db, row) if row is not None else None,
        is_shareable=(
            shareable
            if shareable is not None
            else trip_storage.is_shareable(db, trip.id)
        ),
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
        auto_refresh=row.auto_refresh,
    )


def bulk_trip_refs(db: Session, user_id: str) -> dict[str, TripLegRef]:
    """``flight_id -> TripLegRef`` for every grouped flight the user owns.

    Two queries for the whole flights list, computed once per request: the trips
    and their members. Position is derived from ``departure_time`` here, the
    same rule the summary uses.
    """
    trip_rows = db.execute(
        select(
            FlightTripRow.id, FlightTripRow.name, FlightTripRow.auto_refresh
        ).where(FlightTripRow.user_id == user_id)
    ).all()
    if not trip_rows:
        return {}
    names = {trip_id: name for trip_id, name, _ in trip_rows}
    auto = {trip_id: bool(flag) for trip_id, _, flag in trip_rows}
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
                auto_refresh=auto.get(trip_id, False),
            )
    return refs


def shared_trip_refs(db: Session, flight_ids: list[str]) -> dict[str, TripLegRef]:
    """``flight_id -> TripLegRef`` for *subscribed* legs, when the trip is shared.

    The recipient-side sibling of :func:`bulk_trip_refs`, which is scoped to
    trips the caller owns. Without this a shared leg is a dead end: the
    recipient can open the briefing they were sent but has no way back to the
    chain it belongs to, which is the whole reason the trip exists.

    Gated through ``storage.trips.shareable_trip_ids`` — the same single
    definition the trip page reads — so the badge never links to a trip that
    would 404, and a trip with one private leg produces no refs at all.
    """
    if not flight_ids:
        return {}
    linked = db.execute(
        select(FlightRow.id, FlightRow.trip_id).where(
            FlightRow.id.in_(flight_ids), FlightRow.trip_id.is_not(None),
        )
    ).all()
    if not linked:
        return {}
    trip_ids = {trip_id for _, trip_id in linked}
    trips = db.execute(
        select(FlightTripRow.id, FlightTripRow.name).where(
            FlightTripRow.id.in_(trip_ids)
        )
    ).all()
    names = {trip_id: name for trip_id, name in trips}
    # The all-or-nothing gate, from its one definition rather than rebuilt
    # here: a second expression of "no private leg" is a rule that can be
    # tightened in storage and silently missed in this badge.
    shareable = trip_storage.shareable_trip_ids(db, list(names))
    if not shareable:
        return {}
    members = db.execute(
        select(FlightRow.id, FlightRow.trip_id)
        .where(FlightRow.trip_id.in_(sorted(shareable)))
        .order_by(FlightRow.departure_time.asc())
    ).all()

    by_trip: dict[str, list[str]] = {}
    for flight_id, trip_id in members:
        by_trip.setdefault(trip_id, []).append(flight_id)

    wanted = set(flight_ids)
    refs: dict[str, TripLegRef] = {}
    for trip_id, member_ids in by_trip.items():
        for index, flight_id in enumerate(member_ids, start=1):
            if flight_id not in wanted:
                continue
            refs[flight_id] = TripLegRef(
                id=trip_id,
                name=names.get(trip_id) or "",
                position=index,
                total=len(member_ids),
                # The owner's switch, and only they can flip it. Reporting it
                # on a leg the viewer merely follows would read as a control.
                auto_refresh=False,
            )
    return refs


def viewer_trip_ref_for(
    db: Session, trip_id: str, flight_id: str
) -> TripLegRef | None:
    """:func:`trip_ref_for` for a leg the caller only subscribes to."""
    if not trip_storage.is_shareable(db, trip_id):
        return None
    ref = trip_ref_for(db, trip_id, flight_id)
    return ref.model_copy(update={"auto_refresh": False}) if ref else None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TripResponse])
def list_trips(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """All of the caller's trips, each with its derived summary."""
    trips = trip_storage.list_trips(db, user_id)
    # One shareability query for the whole list rather than one per trip: this
    # is the N+1 ``shareable_trip_ids`` was made batched to avoid, and the list
    # endpoint is the only caller that holds more than one trip.
    shareable = trip_storage.shareable_trip_ids(db, [t.id for t in trips])
    return [
        _trip_to_response(db, trip, shareable=trip.id in shareable)
        for trip in trips
    ]


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


@router.get("/by-share/{code}", response_model=TripResponse)
def get_trip_by_share_code(
    code: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Resolve a ``/t/{code}`` token to its trip — the iOS on-ramp.

    A browser follows the redirect in ``api/app.py`` and lands on
    ``/trip.html?id=…`` instead; this is the same resolution for a client that
    holds the code rather than a URL bar. A code for a trip the caller may not
    read is a 404, indistinguishable from an unknown code.

    Sits above ``/{trip_id}`` for readability only. It does **not** need to:
    ``{trip_id}`` matches a single path segment, so a two-segment
    ``/by-share/{code}`` cannot collide with it whatever the declaration order.
    Said plainly because the opposite claim invites a future route to rely on
    registration order for a disambiguation that was never happening.
    """
    if not SHARE_CODE_RE.match(code):
        raise HTTPException(status_code=404, detail="Unknown share link")
    trip_id = trip_storage.lookup_trip_id_by_share_code(db, code)
    if trip_id is None:
        raise HTTPException(status_code=404, detail="Unknown share link")
    _, role = _viewable_trip_row(db, trip_id, user_id)
    return _trip_to_response(
        db, _load_trip_any_owner(db, trip_id), viewer_id=user_id, role=role,
    )


@router.get("/{trip_id}", response_model=TripResponse)
def get_trip(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _, role = _viewable_trip_row(db, trip_id, user_id)
    return _trip_to_response(
        db, _load_trip_any_owner(db, trip_id), viewer_id=user_id, role=role,
    )


@router.get("/{trip_id}/summary", response_model=TripSummary)
def get_trip_summary(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """The pure ``TripSummary`` on its own — computed, never stored."""
    _viewable_trip_row(db, trip_id, user_id)
    summary, _members, _inputs = build_trip_summary(
        db, _load_trip_any_owner(db, trip_id),
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
    # `keep=trip_id` for the same reason `create_trip` needs it: an empty
    # `flight_ids` on an already-empty trip would otherwise prune the trip out
    # from under this request, and the response builder would then dereference
    # None — a 500 for what should have been a no-op, with the trip destroyed
    # as a side effect.
    trip_storage.prune_empty_trips(db, user_id, keep=trip_id)
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


class TripSubscribeResponse(BaseModel):
    """Outcome of following (or unfollowing) a shared trip."""

    trip_id: str
    #: Legs newly added to (or removed from) the caller's list. Zero is a
    #: success, not a no-op error: it means they already followed all of them.
    changed: int = 0
    total_legs: int = 0
    is_subscribed: bool = False


@router.post("/{trip_id}/subscribe", response_model=TripSubscribeResponse)
def subscribe_trip(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Follow a shared trip — i.e. subscribe to each of its legs.

    **There is no trip subscription row, on purpose.** A trip is a grouping of
    flights, and flight subscription already exists and already carries
    everything that matters (the leg shows up in the recipient's list, the
    owner's privacy flip removes it again, a deleted flight takes it with it).
    A second, trip-shaped subscription concept would need all of that
    re-implemented and would then have to be reconciled with the per-leg one
    every time a leg joins or leaves the trip. So this endpoint is a loop, and
    the "am I following this trip" answer is derived, never stored.

    Its consequence is worth stating: membership is a snapshot. A leg added to
    the trip after the recipient subscribed is not followed until they press it
    again, which the client reports by showing the button un-pressed.
    """
    _row, role = _viewable_trip_row(db, trip_id, user_id)
    if role == "owner":
        raise HTTPException(
            status_code=409,
            detail="You own this trip — its legs are already in your list.",
        )
    members = trip_storage.trip_members(db, trip_id)
    changed = 0
    for flight in members:
        try:
            if subscribe_flight(db, flight.id, user_id):
                changed += 1
        except SubscriptionError:
            # A leg of someone else's trip that this user happens to own. Not
            # reachable through the UI, but harmless and not worth failing the
            # whole call over — they already have it.
            continue
    db.commit()
    return TripSubscribeResponse(
        trip_id=trip_id,
        changed=changed,
        total_legs=len(members),
        is_subscribed=_subscribed_to_all(db, [f.id for f in members], user_id),
    )


@router.delete("/{trip_id}/subscribe", response_model=TripSubscribeResponse)
def unsubscribe_trip(
    trip_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Stop following a shared trip: drop the subscription on every leg.

    Deliberately not gated on ``is_shareable``: a trip whose owner has since
    made a leg private is exactly the one a recipient may want to let go of,
    and refusing to unsubscribe from something they can no longer see would
    strand those legs in their list.

    But "not gated on shareability" is not "not gated". The caller must already
    hold a subscription to one of the legs, otherwise this is a 404 like any
    other read they are not entitled to. Answering on leg count alone would
    make ``DELETE`` an existence oracle: a stranger holding only a guessed trip
    id would learn the trip exists and how many legs it has, from the one route
    that did not check — while ``GET`` on the same id correctly told them
    nothing.
    """
    members = trip_storage.trip_members(db, trip_id)
    if not members:
        raise HTTPException(status_code=404, detail="Trip not found")
    subscribed = subscribed_member_ids(db, [f.id for f in members], user_id)
    if not subscribed and trip_storage.load_trip_row_for_viewer(
        db, trip_id, user_id
    ) is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    changed = sum(1 for f in members if unsubscribe_flight(db, f.id, user_id))
    db.commit()
    return TripSubscribeResponse(
        trip_id=trip_id, changed=changed, total_legs=len(members), is_subscribed=False,
    )


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
