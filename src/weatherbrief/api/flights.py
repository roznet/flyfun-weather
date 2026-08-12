"""API endpoints for flight management."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from flyfun_common.auth import is_dev_mode
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserRow
from weatherbrief.airports import RejectedWaypoint
from weatherbrief.db.models import BriefingPackRow, FlightRow, FlightSubscriptionRow
from weatherbrief.fetch.variables import (
    MAX_BOOKING_LEAD_DAYS,
    coverage_start_date,
    is_beyond_forecast_horizon,
)
from weatherbrief.models import AdvisorySummary, Flight, FlightDebrief
from weatherbrief.notify.badge import unseen_flight_ids
from weatherbrief.storage.debriefs import bulk_get_debriefs, get_debrief as _get_debrief
from weatherbrief.api.debriefs import DebriefResponse
from weatherbrief.api.flight_search import MAX_QUERY_LEN, matches as _search_matches, parse_query
from weatherbrief.api.preferences import load_flight_order
from weatherbrief.storage.flights import (
    SHARE_CODE_RE,
    SubscriptionError,
    bulk_delete_flights as _bulk_delete_flights,
    delete_flight,
    is_subscribed,
    list_flights_with_role,
    load_flight,
    lookup_flight_id_by_share_code,
    pack_has_advisories,
    parse_advisory_summary,
    safe_path_component,
    save_flight,
    subscribe_flight,
    unsubscribe_flight,
)

router = APIRouter(prefix="/flights", tags=["flights"])

logger = logging.getLogger(__name__)

from weatherbrief.api.validation import WAYPOINT_RE, is_valid_waypoint

if TYPE_CHECKING:
    from euro_aip.briefing.models.flight_exchange import FlightExchange

# Upper bound on the number of resolved waypoints a flight route may carry.
# Airways/SIDs/speed-level tokens are stripped by route interpretation before a
# route reaches here, so a well-formed GA cross-country resolves well under this;
# the cap only guards against pathological input. Shared by the create and
# update request models so the two paths stay in lock-step.
MAX_ROUTE_WAYPOINTS = 32

# Shape of a share code, kept in lock-step with the ``/s/{code}`` redirect route
# in ``api/app.py``. Validated before any DB hit so scanner/injection-shaped
# strings never reach the lookup.


class CreateFlightRequest(BaseModel):
    """Request body for creating a new flight.

    Fields default to None, meaning "use user preference" (or system default).
    """

    route_name: str = Field("", max_length=256)  # optional preset name
    waypoints: list[str] = Field(default_factory=list, max_length=MAX_ROUTE_WAYPOINTS)  # ICAO codes, navaids, or fixes
    # Original Field-15 input the pilot typed, when the client captured one
    # (web Save flow). The server stores it verbatim alongside ``waypoints``
    # so future parser improvements can re-derive the route. Optional —
    # iOS/MCP clients pass only ``waypoints``.
    raw_route: str | None = Field(default=None, max_length=4000)
    departure_time: str  # ISO 8601 datetime with timezone (e.g. "2026-02-21T09:00:00Z")
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None
    profile_id: int | None = None  # flight profile to associate
    aircraft_id: int | None = None  # user aircraft to associate
    # Timing-scenario Flexibility mode. "alternate" needs alt_departure_time,
    # which is set via PATCH after creation (mirrors the existing alt flow), so
    # it is rejected here; the web form creates then patches.
    flexibility: Literal["none", "same_day", "prev_day", "next_day"] = "none"

    @field_validator("departure_time")
    @classmethod
    def validate_departure_time(cls, v: str) -> str:
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("departure_time must be a valid ISO 8601 datetime")
        if dt.tzinfo is None:
            raise ValueError("departure_time must include a timezone")
        return v

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: list[str]) -> list[str]:
        for wp in v:
            if not is_valid_waypoint(wp):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be 2-5 alphanumeric "
                    f"characters or an ICAO inline coordinate (e.g. 4830N00210E)"
                )
        return v


class AircraftInfo(BaseModel):
    """Lightweight aircraft info embedded in flight responses.

    Privacy-gated: ``tail_number`` is only set when the viewer is the aircraft
    owner; otherwise it is None.
    """

    id: int
    icao_type: str
    type_name: str  # "Manufacturer Model"
    tail_number: str | None = None
    nickname: str | None = None


class BriefingStatusInfo(BaseModel):
    """Summary of latest briefing pack, included in flight listings.

    Carries everything the flights-list card and the debrief form need so
    the page renders from the single ``/flights`` response — no per-flight
    ``/packs/latest`` round-trips. ``has_advisories`` lets the debrief form
    decide whether to fetch the advisories manifest at all.
    """

    assessment: str | None = None
    assessment_reason: str | None = None
    # Long-range early outlook (beyond the GRIB horizon). Mutually exclusive
    # with ``assessment`` — the flights-list card shows a soft outlook badge
    # instead of the GREEN/AMBER/RED traffic light when this is set.
    outlook: str | None = None
    outlook_reason: str | None = None
    has_digest: bool = False
    days_out: int | None = None
    fetch_timestamp: str | None = None
    has_advisories: bool = False
    # Compact RED/AMBER advisory breakdown + top named categories for the
    # flights-list card chips. Read straight from the denormalized pack column
    # (no per-flight route_advisories.json parse). None for old packs.
    advisory_summary: AdvisorySummary | None = None
    # True when this flight has a notify-qualifying briefing update the viewer
    # hasn't opened yet — the same server-derived predicate that drives the
    # app-icon badge (notify/badge.py::_is_unseen). Lets the flight list mark
    # unopened flights with a red dot, kept exactly consistent with the badge
    # (dot count == badge count). Only set by the list endpoint; False elsewhere.
    unseen: bool = False


class CoveragePending(BaseModel):
    """Weather-coverage status for a flight saved beyond the forecast horizon.

    Present on ``FlightResponse.coverage`` only while no model reaches the
    flight date yet. Clients render a neutral "pending · available dd/mm" state
    instead of a traffic-light assessment, and suppress refresh until the flight
    crosses into range. ``None`` once the flight is bookable.
    """

    available_date: str  # ISO date — first (early-outlook) briefing appears
    full_briefing_date: str | None = None  # ISO date — full GRIB briefing, if resolved
    days_until_available: int  # whole days from today until available_date


class FlightResponse(BaseModel):
    """Flight data in API responses."""

    id: str
    user_id: str
    profile_id: int | None = None
    aircraft_id: int | None = None
    aircraft: AircraftInfo | None = None
    route_name: str
    waypoints: list[str] = []
    departure_time: str
    alt_departure_time: str | None = None
    # Timing-scenario Flexibility mode (timing-scenario-plan.md): what the
    # scenario job grades for this flight. "alternate" uses alt_departure_time.
    flexibility: Literal["none", "alternate", "same_day", "prev_day", "next_day"] = "none"
    # Per-flight briefing-notification override: follow global | notify any | mute.
    notify_override: Literal["default", "notify", "mute"] = "default"
    target_date: str  # backward compat (computed from departure_time)
    target_time_utc: int  # backward compat (computed from departure_time)
    cruise_altitude_ft: int
    flight_ceiling_ft: int
    flight_duration_hours: float
    private: bool = False
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    created_at: str
    latest_briefing: BriefingStatusInfo | None = None
    # Set only when the flight is saved beyond the forecast horizon (no model
    # data yet). Drives the "pending · available dd/mm" list chip and the
    # pending-coverage summary card. None once the flight is within range.
    coverage: CoveragePending | None = None
    role: Literal["owner", "subscriber"] = "owner"
    owner_display_name: str | None = None  # set when role == "subscriber"
    is_subscribed: bool = False  # True when the viewer has subscribed to this flight
    debrief: DebriefResponse | None = None  # owned past flights only
    section: Literal["future", "recent", "past"] = "past"
    # Original Field-15 input the pilot typed, when captured (web Save flow).
    # Surfaced so the flight detail UI can show "Original: <raw string>" —
    # useful when the raw input differs from the resolved waypoint chip strip
    # (DCT/airway/coord shorthand collapsed).
    raw_route: str | None = None
    parser_version: str | None = None
    # 8-char base62 token for the short share link (/s/{code}). Always
    # set for flights created after migration 049; may be None for very
    # old rows that didn't get backfilled (extremely unlikely in
    # practice, but the client falls back to the long ?id= URL).
    share_code: str | None = None


# Number of flights surfaced in the "recent" section as a debrief nudge,
# and how far back to look. Hard-coded for Phase 1; revisit when usage
# patterns suggest a larger window or a per-user setting.
RECENT_SECTION_CAP = 2
RECENT_SECTION_MAX_AGE_DAYS = 30


def _rejected_waypoints_message(rejected: list[RejectedWaypoint]) -> str:
    """Human-readable 422 detail for resolve_waypoints rejections.

    Separates ``unknown`` (typo / not-in-DB) from ``detour`` (off-route)
    so the pilot knows how to fix the input. Accepts a list of
    ``airports.RejectedWaypoint``.
    """
    unknown = [r.name for r in rejected if r.reason == "unknown"]
    detour = [r.name for r in rejected if r.reason == "detour"]
    parts: list[str] = []
    if unknown:
        parts.append(f"We did not find in our database: {', '.join(unknown)}")
    if detour:
        parts.append(f"Waypoint(s) resolved too far from route: {', '.join(detour)}")
    return "; ".join(parts) if parts else "Route could not be resolved"


def _compute_flight_id(
    *,
    route_name: str,
    departure_time: datetime,
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
    flight_duration_hours: float,
    user_id: str,
) -> str:
    """Build the canonical flight ID. Same params → same ID; collisions are rejected by the create endpoint."""
    params_hash = hashlib.sha256(
        json.dumps(
            {
                "time": departure_time.strftime("%H:%M"),
                "alt": cruise_altitude_ft,
                "ceil": flight_ceiling_ft,
                "dur": flight_duration_hours,
                "user": user_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:4]
    target_date_str = departure_time.strftime("%Y-%m-%d")
    return f"{safe_path_component(route_name)}-{target_date_str}-{params_hash}"


def _is_admin_or_dev(request: Request, db: Session) -> bool:
    """Return True if the request comes from an admin user or dev mode is active."""
    if is_dev_mode():
        return True
    try:
        from weatherbrief.api.admin import require_admin
        require_admin(request, db=db)
        return True
    except HTTPException:
        return False


def _reject_if_beyond_booking_cap(departure_time: datetime) -> None:
    """Reject a flight whose date is past the maximum booking lead time.

    Note: this is not the forecast horizon. Flights beyond the horizon but
    within ``MAX_BOOKING_LEAD_DAYS`` are allowed — they save in a pending state
    and brief automatically once a model run reaches the date. Only genuinely
    far-out dates are refused. Shared by create and move so the gate stays
    identical.
    """
    now_utc = datetime.now(timezone.utc)
    if (departure_time.date() - now_utc.date()).days > MAX_BOOKING_LEAD_DAYS:
        latest = (now_utc + timedelta(days=MAX_BOOKING_LEAD_DAYS)).strftime("%Y-%m-%d")
        raise HTTPException(
            status_code=422,
            detail=(
                f"That date is more than {MAX_BOOKING_LEAD_DAYS} days out — beyond "
                f"how far ahead a flight can be saved. Please choose a departure "
                f"date on or before {latest}."
            ),
        )


def _compute_coverage(departure_time: datetime) -> "CoveragePending | None":
    """Coverage status for a flight saved beyond the forecast horizon.

    Returns ``None`` when the flight is already within (or past) the dual-model
    booking horizon — i.e. a briefing can be generated now, so there is nothing
    pending. Otherwise returns the dates on which weather coverage begins:

    - ``available_date`` = departure − ``dual_model_horizon_days`` (the first
      day both global models reach the flight; the early-outlook briefing).
    - ``full_briefing_date`` = the expected delivery of the first full-horizon
      ECMWF GRIB run that reaches the flight (the full traffic-light briefing),
      or ``None`` if it can't be resolved (display degrades to just the former).
    """
    now = datetime.now(timezone.utc)
    if not is_beyond_forecast_horizon(departure_time.date(), now.date()):
        return None

    available = coverage_start_date(departure_time.date())
    full_date: str | None = None
    try:
        from weatherbrief.fetch.freshness.registry import (
            ECMWF_GRIB_SOURCE,
            first_full_coverage,
        )

        # The full-res GRIB feed whose 168h horizon marks the full-briefing
        # boundary (shared registry constant — no duplicated key string).
        _, delivery = first_full_coverage(ECMWF_GRIB_SOURCE, departure_time)
        full_date = delivery.date().isoformat()
    except Exception:
        logger.debug("coverage: could not resolve full-briefing date", exc_info=True)

    return CoveragePending(
        available_date=available.isoformat(),
        full_briefing_date=full_date,
        days_until_available=(available - now.date()).days,
    )


def _resolve_aircraft_info(
    db: Session, aircraft_id: int | None, *, viewer_id: str | None = None,
) -> AircraftInfo | None:
    """Build AircraftInfo for a flight, privacy-gated by viewer."""
    if aircraft_id is None:
        return None
    from weatherbrief.db.models import UserAircraftRow
    from weatherbrief.storage.aircraft_types import get_aircraft_type

    row = db.get(UserAircraftRow, aircraft_id)
    if row is None:
        return None

    type_info = get_aircraft_type(row.icao_type)
    type_name = f"{type_info['manufacturer']} {type_info['model']}" if type_info else row.icao_type

    is_owner = viewer_id is not None and row.user_id == viewer_id
    return AircraftInfo(
        id=row.id,
        icao_type=row.icao_type,
        type_name=type_name,
        tail_number=row.tail_number if is_owner else None,
        nickname=row.nickname if is_owner else None,
    )


def flight_to_exchange(
    flight: Flight, db: Session, db_path: str, *, viewer_id: str
) -> FlightExchange:
    """Map a weather ``Flight`` to a cross-app ``FlightExchange`` envelope.

    Weather stores a route as a single ``waypoints`` list with implicit
    endpoints, and keeps timing/altitude/aircraft alongside it on the flight
    row. The shared ``FlightExchange`` format (and the ``Route`` it wraps) uses
    explicit endpoints, so weather is the side that adapts on emit:

    - resolve ``waypoints`` → a ``Route`` with split endpoints + coordinates
    - layer on ``departure_time``, derived ``arrival_time``, cruise altitude
    - resolve ``aircraft_id`` → ICAO type + registration
    - wrap with provenance (``source`` = weather, native id, share code)

    The aircraft registration is only emitted to the aircraft's owner — it is
    semi-personal, the same privacy gate :func:`_resolve_aircraft_info` applies
    to ``tail_number``.

    Raises:
        KeyError: If the departure or destination can't be resolved (caller
            maps this to a 422). Callers should pre-check ``len(waypoints) >= 2``.
    """
    from euro_aip.briefing.models.flight_exchange import FlightExchange
    from weatherbrief.airports import resolve_route
    from weatherbrief.db.models import UserAircraftRow

    route = resolve_route(flight.waypoints, db_path)

    route.departure_time = flight.departure_time
    if flight.flight_duration_hours:
        route.arrival_time = flight.departure_time + timedelta(
            hours=flight.flight_duration_hours
        )
    route.cruise_altitude_ft = flight.cruise_altitude_ft

    registration = None
    if flight.aircraft_id is not None:
        ac = db.get(UserAircraftRow, flight.aircraft_id)
        if ac is not None:
            route.aircraft_type = ac.icao_type
            if ac.user_id == viewer_id:
                registration = ac.tail_number

    return FlightExchange(
        route=route,
        name=flight.route_name or None,
        aircraft_registration=registration,
        source_app="weather",
        source_flight_id=flight.id,
        source_share_code=flight.share_code,
    )


def _resolve_owner_display_name(db: Session, owner_id: str) -> str | None:
    """Look up a flight owner's display name for the subscriber/non-owner view.

    Returns None when the owner has not set a display name — the frontend
    falls back to `flightDetail.sharedByUnknown`. We deliberately do NOT
    fall back to the email address, which would leak the owner's email to
    every subscriber.
    """
    row = db.get(UserRow, owner_id)
    if row is None:
        return None
    return row.display_name or None


def _flight_to_response(
    flight: Flight,
    db: Session,
    viewer_id: str,
    latest_briefing: BriefingStatusInfo | None = None,
    role: Literal["owner", "subscriber"] | None = None,
    subscribed: bool | None = None,
    owner_display_name: str | None = None,
    debrief: FlightDebrief | None = None,
    section: Literal["future", "recent", "past"] | None = None,
    unseen: bool = False,
) -> FlightResponse:
    # viewer_id is required: the role derivation below compares flight.user_id
    # against it, and with viewer_id=None we would silently classify the
    # owner's own flights as "subscriber". All current callers pass it; the
    # required signature keeps it that way.
    aircraft = None
    if flight.aircraft_id:
        aircraft = _resolve_aircraft_info(db, flight.aircraft_id, viewer_id=viewer_id)

    effective_role = role if role is not None else ("owner" if flight.user_id == viewer_id else "subscriber")

    # Only resolve from DB when caller didn't pre-fetch it (single-flight endpoints).
    # The list endpoint joins users in one query and passes the name in.
    if owner_display_name is None and effective_role == "subscriber":
        owner_display_name = _resolve_owner_display_name(db, flight.user_id)

    if subscribed is None:
        subscribed = (
            is_subscribed(db, flight.id, viewer_id)
            if effective_role == "subscriber"
            else False
        )

    # Owners see their own debriefs; subscribers don't see others' debriefs.
    if debrief is None and effective_role == "owner":
        debrief = _get_debrief(db, flight.id)
    debrief_resp = DebriefResponse.from_model(debrief) if debrief else None

    if section is None:
        section = _classify_section(
            flight,
            has_debrief=debrief is not None,
            recent_set=set(),  # single-flight call: no recent context available
        )

    # Stamp the per-flight unseen flag onto the briefing summary (copy rather
    # than mutate — the caller owns the passed-in object). Only the list
    # endpoint passes unseen=True; single-flight callers leave it False.
    if latest_briefing is not None and unseen:
        latest_briefing = latest_briefing.model_copy(update={"unseen": True})

    return FlightResponse(
        id=flight.id,
        user_id=flight.user_id,
        profile_id=flight.profile_id,
        aircraft_id=flight.aircraft_id,
        aircraft=aircraft,
        route_name=flight.route_name,
        waypoints=flight.waypoints,
        departure_time=flight.departure_time.isoformat(),
        alt_departure_time=flight.alt_departure_time.isoformat() if flight.alt_departure_time else None,
        flexibility=flight.flexibility,
        notify_override=flight.notify_override,
        target_date=flight.target_date,
        target_time_utc=flight.target_time_utc,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
        private=flight.private,
        auto_refresh=flight.auto_refresh,
        auto_refresh_hour=flight.auto_refresh_hour,
        created_at=flight.created_at.isoformat(),
        latest_briefing=latest_briefing,
        coverage=_compute_coverage(flight.departure_time),
        role=effective_role,
        owner_display_name=owner_display_name,
        is_subscribed=subscribed,
        debrief=debrief_resp,
        raw_route=flight.raw_route,
        parser_version=flight.parser_version,
        share_code=flight.share_code,
        section=section,
    )


def _flight_has_ended(flight: Flight, now: datetime) -> bool:
    """Has this flight finished — departure plus its planned duration?

    The single boundary between the ``future`` section and everything behind
    it. A flight that departed 30 minutes ago on a 3-hour trip is in progress,
    not past, so it stays in the upcoming list (and under "soonest first" sits
    at the very top, which is where you want it while flying). This mirrors the
    web's ``isFlightPast`` (``web/ts/utils.ts``), which has always been
    duration-aware.

    ``flight_duration_hours`` defaults to ``0.0`` and zero-duration flights are
    a real case (the add-flight flow has a ``confirmZeroDuration`` dialog) —
    those behave exactly as before: past the instant they depart.

    ``_classify_section`` and ``_compute_recent_section`` must both go through
    here; if they disagree, an airborne flight can be ``future`` by one and a
    ``recent`` candidate by the other.
    """
    duration = flight.flight_duration_hours or 0.0
    return flight.departure_time + timedelta(hours=duration) < now


def _classify_section(
    flight: Flight,
    *,
    has_debrief: bool,
    recent_set: set[str],
    now: datetime | None = None,
) -> Literal["future", "recent", "past"]:
    """Bucket a flight into one of the three list sections.

    The ``recent`` decision needs cross-flight context (the user's debrief
    history), so callers compute ``recent_set`` once via
    ``_compute_recent_section`` and pass it in.
    """
    now = now or datetime.now(timezone.utc)
    if not _flight_has_ended(flight, now):
        return "future"
    if flight.id in recent_set:
        return "recent"
    return "past"


def _compute_recent_section(
    owned_flights_with_debrief: list[tuple[Flight, FlightDebrief | None]],
    *,
    cap: int = RECENT_SECTION_CAP,
    max_age_days: int = RECENT_SECTION_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> set[str]:
    """Pick which owned past undebriefed flights belong in ``recent``.

    Rule: the most recent ``cap`` past flights without a debrief whose
    ``departure_time`` is within the last ``max_age_days``. The cap is
    independent of debrief history — debriefing one flight doesn't push
    the next-most-recent undebriefed one out of the section, so a user
    with two recent flights can debrief them both back-to-back. Anything
    older than the window drops to the Past section so the nudge stays
    bounded.

    Pure function — input is the (flight, debrief) tuples for *owned*
    flights only; subscribed flights never enter the recent section.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    candidates = [
        f
        for f, d in owned_flights_with_debrief
        if d is None
        and _flight_has_ended(f, now)
        and f.departure_time >= cutoff
    ]
    candidates.sort(key=lambda f: f.departure_time, reverse=True)
    return {f.id for f in candidates[:cap]}


def _get_latest_packs(db: Session, flight_ids: list[str]) -> dict[str, BriefingStatusInfo]:
    """Fetch the latest briefing pack per flight in a single query."""
    if not flight_ids:
        return {}

    # Subquery: max fetch_timestamp per flight
    latest_ts = (
        select(
            BriefingPackRow.flight_id,
            func.max(BriefingPackRow.fetch_timestamp).label("max_ts"),
        )
        .where(BriefingPackRow.flight_id.in_(flight_ids))
        .group_by(BriefingPackRow.flight_id)
        .subquery()
    )

    rows = db.execute(
        select(BriefingPackRow)
        .join(
            latest_ts,
            (BriefingPackRow.flight_id == latest_ts.c.flight_id)
            & (BriefingPackRow.fetch_timestamp == latest_ts.c.max_ts),
        )
    ).scalars().all()

    # has_advisories is a per-pack disk stat (route_advisories.json), all in
    # this one request — far cheaper than the per-flight /packs/latest
    # round-trips it replaces.
    return {
        row.flight_id: BriefingStatusInfo(
            assessment=row.assessment,
            assessment_reason=row.assessment_reason,
            outlook=row.outlook,
            outlook_reason=row.outlook_reason,
            has_digest=row.has_digest,
            days_out=row.days_out,
            fetch_timestamp=row.fetch_timestamp.isoformat(),
            has_advisories=pack_has_advisories(row.artifact_path),
            advisory_summary=parse_advisory_summary(row.advisory_summary_json),
        )
        for row in rows
    }


def _reorder_future_soonest_first(
    flights: list[FlightResponse],
    meta: list[tuple[str, datetime]],
) -> None:
    """Re-sort the ``future`` entries of ``flights`` ascending by departure, in place.

    Only the slots holding future flights are rewritten; ``recent`` entries keep
    both their positions and their most-recent-first order, which is what the
    preference promises (#536). The two sections can interleave in the incoming
    ``departure_time desc`` order — an in-progress flight departed before a
    just-ended short one — so this deliberately permutes within a subset rather
    than partitioning the list.
    """
    slots = [i for i, (section, _) in enumerate(meta) if section == "future"]
    ordered = sorted(slots, key=lambda i: meta[i][1])
    picked = [flights[i] for i in ordered]
    for slot, resp in zip(slots, picked):
        flights[slot] = resp


@router.get("", response_model=list[FlightResponse])
def list_all_flights(
    response: Response,
    past_limit: int | None = Query(default=None, ge=0),
    past_offset: int = Query(default=0, ge=0),
    past_q: str | None = Query(default=None, max_length=MAX_QUERY_LEN),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List owned + subscribed flights with latest briefing status, debrief, and section.

    Subscribed flights are filtered out when the owner has flipped them to
    private (see storage.flights.list_flights_with_role).

    Pagination (opt-in, web flights page): ``future`` + ``recent`` flights are
    always returned in full (small, time-sensitive). The ``past`` section can
    be paginated via ``past_limit`` / ``past_offset`` — when ``past_limit`` is
    omitted (iOS/MCP clients), the full past list is returned unchanged. The
    ``X-Past-Total`` response header always carries the full past count so the
    client knows whether more pages exist.

    Filtering (#542): ``past_q`` filters the ``past`` section by route tokens
    (see ``api/flight_search``). It is past-scoped on purpose — it sits
    alongside ``past_limit``/``past_offset``, and the web client filters the
    always-fully-loaded future + recent sections in the browser instead. The
    filter is applied *before* the header and the slice, so ``X-Past-Total``
    reports the number of **matches** and "show more" pages through the
    filtered set rather than the whole history.

    Section computation runs over the *full* owned set before slicing, so
    ``_compute_recent_section`` still sees every past flight.
    """
    paired = list_flights_with_role(db, user_id)
    pack_status = _get_latest_packs(db, [f.id for f, _, _ in paired])
    debrief_map = _bulk_debriefs_for_owned(db, paired, user_id)
    # Flights with an unopened notify-qualifying update — drives the flight-list
    # red dot, kept consistent with the app-icon badge. One cheap query.
    unseen_ids = unseen_flight_ids(db, user_id)

    owned_pairs: list[tuple[Flight, FlightDebrief | None]] = [
        (f, debrief_map.get(f.id)) for f, role, _ in paired if role == "owner"
    ]
    recent_set = _compute_recent_section(owned_pairs)

    # Build responses, splitting past (paginated) from future+recent (always
    # full). Order within each group follows ``list_flights_with_role``
    # (departure_time desc); the frontend re-buckets by ``section`` so the
    # concatenation order below only needs to preserve per-section order.
    other: list[FlightResponse] = []  # future + recent
    # Parallel to ``other``: (section, departure_time) per entry, so the
    # soonest-first reorder below can pick out the future members without
    # re-parsing the ISO strings on the responses.
    other_meta: list[tuple[str, datetime]] = []
    past: list[FlightResponse] = []
    for f, role, owner_name in paired:
        debrief = debrief_map.get(f.id) if role == "owner" else None
        section = _classify_section(f, has_debrief=debrief is not None, recent_set=recent_set)
        resp = _flight_to_response(
            f,
            db,
            viewer_id=user_id,
            latest_briefing=pack_status.get(f.id),
            role=role,
            subscribed=(role == "subscriber"),
            owner_display_name=owner_name,
            debrief=debrief,
            section=section,
            unseen=f.id in unseen_ids,
        )
        if section == "past":
            past.append(resp)
        else:
            other.append(resp)
            other_meta.append((section, f.departure_time))

    # Upcoming-flights ordering (#536). Opt-in account preference, loaded here
    # in the body rather than via ``Depends()``: ``api/agent.py`` (the ChatGPT
    # connector) calls this function directly, where a Depends default would
    # arrive as a ``fastapi.params.Depends`` instance and never match.
    if load_flight_order(db, user_id) == "soonest_first":
        _reorder_future_soonest_first(other, other_meta)

    # Filter before the count + slice so "show more" pages through matches.
    # Runs after section classification so a filtered-out past flight still
    # counted towards ``_compute_recent_section`` above.
    search_tokens = parse_query(past_q)
    if search_tokens:
        past = [p for p in past if _search_matches(p.waypoints, p.route_name, search_tokens)]

    response.headers["X-Past-Total"] = str(len(past))
    if past_limit is not None:
        # past_offset / past_limit are validated >= 0 by Query(ge=0).
        past = past[past_offset : past_offset + past_limit]

    return other + past


def _bulk_debriefs_for_owned(
    db: Session,
    paired: list[tuple[Flight, str, str | None]],
    viewer_id: str,
) -> dict[str, FlightDebrief]:
    """One query for all owned-flight debriefs in a list response."""
    owned_ids = [f.id for f, role, _ in paired if role == "owner" and f.user_id == viewer_id]
    return bulk_get_debriefs(db, owned_ids)


@router.post("", response_model=FlightResponse, status_code=201)
def create_flight(
    req: CreateFlightRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new flight.

    Request fields that are None are filled from the associated profile's
    settings (or user's default profile), then from system defaults.
    """
    from weatherbrief.api.profiles import load_profile_settings

    if not req.waypoints and not req.route_name:
        raise HTTPException(
            status_code=422, detail="Either waypoints or route_name is required"
        )

    # Derive route_name from waypoints if not provided
    route_name = req.route_name or "_".join(w.lower() for w in req.waypoints)
    waypoints = [w.upper().strip() for w in req.waypoints] if req.waypoints else []

    # Validate waypoints exist in the airport database
    if waypoints:
        db_path = getattr(request.app.state, "db_path", "")
        if db_path:
            from weatherbrief.airports import resolve_waypoints

            try:
                _, _rejected = resolve_waypoints(waypoints, db_path)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=exc.args[0])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if _rejected:
                raise HTTPException(
                    status_code=422,
                    detail=_rejected_waypoints_message(_rejected),
                )

    # Parse departure_time
    departure_time = datetime.fromisoformat(req.departure_time)
    if departure_time.tzinfo is None:
        departure_time = departure_time.replace(tzinfo=timezone.utc)

    # Only admins can create historical flights with past departure times
    if departure_time < datetime.now(timezone.utc):
        if not _is_admin_or_dev(request, db):
            raise HTTPException(
                status_code=403,
                detail="Only admins can create flights with past departure times",
            )

    # Reject only flights beyond the booking cap. Flights past the forecast
    # horizon but within the cap are allowed and save in a pending-coverage
    # state (they brief automatically once a model run reaches the date).
    _reject_if_beyond_booking_cap(departure_time)

    # Derive date string and hour for flight ID and hash (backward compat)
    target_date_str = departure_time.strftime("%Y-%m-%d")
    target_hour = departure_time.hour

    # A referenced aircraft/profile must belong to the caller — don't store
    # a cross-user FK from a guessed integer ID.
    if req.aircraft_id is not None:
        _validate_aircraft_ownership(db, req.aircraft_id, user_id)
    if req.profile_id is not None:
        _validate_profile_ownership(db, req.profile_id, user_id)

    # Load defaults from the selected profile (or the user's default profile)
    profile_settings = load_profile_settings(db, req.profile_id, user_id)
    cruise_altitude_ft = (
        req.cruise_altitude_ft
        if req.cruise_altitude_ft is not None
        else (profile_settings.get("cruise_altitude_ft") or 8000)
    )
    flight_ceiling_ft = (
        req.flight_ceiling_ft
        if req.flight_ceiling_ft is not None
        else (profile_settings.get("flight_ceiling_ft") or 18000)
    )
    flight_duration_hours = req.flight_duration_hours if req.flight_duration_hours is not None else 0.0

    flight_id = _compute_flight_id(
        route_name=route_name,
        departure_time=departure_time,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        flight_duration_hours=flight_duration_hours,
        user_id=user_id,
    )

    # Check if already exists
    try:
        load_flight(db, flight_id)
        raise HTTPException(
            status_code=409,
            detail=f"Flight '{flight_id}' already exists",
        )
    except KeyError:
        pass

    # Stamp the parser version only when we actually have a raw_route.
    # No raw_route → no derivation happened on the server → no version
    # to record (iOS/MCP path). ``strip() or None`` collapses the empty
    # and whitespace-only cases to NULL — same idiom as update/move,
    # avoiding the contradictory ``(raw_route="", parser_version=None)``
    # state that would result from storing a whitespace-only string.
    raw_route = (
        (req.raw_route.strip() or None) if req.raw_route is not None else None
    )
    parser_version = _euro_aip_parser_version() if raw_route else None

    flight = Flight(
        id=flight_id,
        user_id=user_id,
        profile_id=req.profile_id,
        aircraft_id=req.aircraft_id,
        route_name=route_name,
        waypoints=waypoints,
        departure_time=departure_time,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        flight_duration_hours=flight_duration_hours,
        raw_route=raw_route,
        parser_version=parser_version,
        flexibility=req.flexibility,
        created_at=datetime.now(tz=timezone.utc),
    )

    save_flight(db, flight, user_id)
    return _flight_to_response(flight, db, viewer_id=user_id)


def _euro_aip_parser_version() -> str:
    """Stamp tying a stored ``raw_route`` to the parser that derived it."""
    # Read at request time so a hot-reloaded euro_aip in dev picks up the
    # new version on the next save without a server restart.
    import euro_aip
    version = getattr(euro_aip, "__version__", None) or "unknown"
    return f"euro_aip/{version}"


class RouteDistanceRequest(BaseModel):
    """Request body for computing route distance from waypoints."""

    waypoints: list[str]

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("At least 2 waypoints are required")
        normalized = [wp.strip().upper() for wp in v]
        for wp in normalized:
            if not is_valid_waypoint(wp):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be 2-5 alphanumeric "
                    f"characters or an ICAO inline coordinate (e.g. 4830N00210E)"
                )
        return normalized


class WaypointInfo(BaseModel):
    """Resolved waypoint with coordinates and timezone."""

    icao: str
    name: str
    lat: float
    lon: float
    timezone: str | None = None


class RouteDistanceResponse(BaseModel):
    """Route distance computed from waypoints."""

    total_distance_nm: float
    waypoints: list[WaypointInfo] = []


@router.post("/route-distance", response_model=RouteDistanceResponse)
def compute_route_distance(
    req: RouteDistanceRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    """Compute total great-circle distance for a list of waypoints."""
    from euro_aip.models.navpoint import NavPoint
    from weatherbrief.airports import get_timezone, resolve_waypoints

    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=500, detail="Airport database not configured")

    try:
        resolved, rejected = resolve_waypoints(req.waypoints, db_path)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=exc.args[0])
    except ValueError as exc:
        # euro_aip's resolver raises ValueError when fewer than two points
        # resolve (e.g. an out-of-coverage route like "KLSE C29" where no
        # waypoint is in the European nav DB). That's a client input problem,
        # not a server fault — surface it as 422 instead of an unhandled 500.
        raise HTTPException(status_code=422, detail=str(exc))

    if rejected:
        logger.warning(
            "compute_route_distance: dropped %d waypoint(s) from %s: %s",
            len(rejected), req.waypoints,
            [(r.name, r.reason) for r in rejected],
        )

    total_nm = 0.0
    for wp_a, wp_b in zip(resolved, resolved[1:]):
        nav_a = NavPoint(latitude=wp_a.lat, longitude=wp_a.lon)
        nav_b = NavPoint(latitude=wp_b.lat, longitude=wp_b.lon)
        _, leg_distance = nav_a.haversine_distance(nav_b)
        total_nm += leg_distance

    waypoint_infos = [
        WaypointInfo(
            icao=wp.icao,
            name=wp.name,
            lat=wp.lat,
            lon=wp.lon,
            timezone=get_timezone(wp.lat, wp.lon),
        )
        for wp in resolved
    ]

    return RouteDistanceResponse(
        total_distance_nm=round(total_nm, 1),
        waypoints=waypoint_infos,
    )


class InterpretRouteRequest(BaseModel):
    """Request body for interpreting a route string with smart waypoint filtering."""

    raw_route: str = Field(..., min_length=1, max_length=2000)


class InterpretRouteResponse(BaseModel):
    """Result of smart route interpretation."""

    original_tokens: list[str] = []  # all tokens extracted from the input
    interpreted: list[str] = []  # waypoints that resolved successfully
    # Tokens we couldn't place on the map: typos, unsupported coord formats,
    # or DB-context misses. Pure ICAO Field-15 syntax (DCT/IFR/VFR/airway
    # labels/speed-level) is silently dropped — those are expected and a
    # "not recognized" label on them confuses pilots.
    skipped: list[str] = []
    # Tokens the resolver recognised but rejected as too far off the direct
    # leg (detour filter). Kept separate from ``skipped`` so the UI can tell
    # pilots *why* a waypoint was dropped — "not on this route" reads very
    # differently from "I don't know this airport."
    off_route: list[str] = []
    waypoints: list[WaypointInfo] = []  # resolved waypoint details


@router.post("/interpret-route", response_model=InterpretRouteResponse)
def interpret_route(
    req: InterpretRouteRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    """Interpret a raw ICAO Field-15 route string into a resolved waypoint list.

    Pipeline:
      1. ``parse_field15`` classifies each token (WAYPOINT / AIRWAY /
         SPEED_LEVEL / FLIGHT_RULE / DIRECT / UNKNOWN).
      2. Keep only WAYPOINT tokens — IFR, DCT, airway labels, speed/level
         suffixes are syntactic and never hit the DB.
      3. ``resolve_waypoints`` does the authoritative pass with route
         context, returning a survivors list plus reason-tagged rejects.

    Rejection routing:
      - ``skipped`` — tokens we couldn't place at all: UNKNOWN parser
        tokens (typos) and late-stage resolver misses with reason
        ``unknown``. Surfaced to the pilot as "not recognized".
      - ``off_route`` — tokens the resolver placed but rejected as too
        far off the direct leg (resolver reason ``detour``). Surfaced
        separately so pilots see *why* the waypoint was dropped.

    Pure Field-15 syntax (DCT, IFR/VFR, airway labels, speed/level
    groups) is silently dropped — surfacing it as "not recognized"
    misleads pilots into thinking they typed something wrong.
    """
    from euro_aip.models.field15 import parse_field15, TokenKind

    from weatherbrief.airports import (
        get_timezone,
        is_known_waypoint,
        resolve_waypoints,
    )

    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=500, detail="Airport database not configured")

    tokens = parse_field15(req.raw_route)
    # Everything parse_field15 extracted — matches the
    # InterpretRouteResponse docstring ("all tokens extracted from the
    # input") and gives consumers (e.g. an iOS prefill-correction UI)
    # the full picture of what was parsed, including airway / flight-rule
    # / direct / speed-level syntax.
    original_tokens = [t.value for t in tokens]

    candidate_waypoints: list[str] = []
    skipped: list[str] = []
    off_route: list[str] = []
    for t in tokens:
        if t.kind in (TokenKind.WAYPOINT, TokenKind.COORDINATE):
            # COORDINATE tokens (e.g. 4629N01541E) are real route points —
            # the resolver places them geometrically without a DB lookup.
            # Treat them like named waypoints from this layer's perspective.
            # Skip consecutive duplicates (e.g. pasted ``ABDIL ABDIL``).
            if candidate_waypoints and candidate_waypoints[-1] == t.value:
                continue
            candidate_waypoints.append(t.value)
        elif t.kind is TokenKind.UNKNOWN:
            # Tokens that don't fit any Field-15 grammar — typos or
            # shapes the parser doesn't recognise. Surface them so the
            # pilot can see what didn't make the cut.
            skipped.append(t.value)
        # AIRWAY / FLIGHT_RULE / DIRECT / SPEED_LEVEL fall through:
        # syntactic noise the parser correctly identified — silently
        # dropped from the user-visible list.

    # Pass-through when we have 0-1 candidates — resolver needs >=2 for a
    # route. The loop below overwrites this from the resolver on the >=2 path.
    interpreted: list[str] = list(candidate_waypoints)
    waypoint_infos: list[WaypointInfo] = []
    if len(candidate_waypoints) >= 2:
        try:
            resolved, rejected = resolve_waypoints(candidate_waypoints, db_path)
        except (KeyError, ValueError):
            # Departure or destination not placed — the route is a non-starter.
            # Fall back to a per-token DB check so the popup shows the pilot
            # which codes are unknown (typo) vs. known-but-not-enough.
            interpreted = [
                c for c in candidate_waypoints if is_known_waypoint(c, db_path)
            ]
            skipped.extend(
                c for c in candidate_waypoints if c not in interpreted
            )
        else:
            if rejected:
                # Split late-stage rejections by reason so the UI can label
                # them distinctly. ``detour`` = recognised airport/fix that
                # would force a large deviation from the direct leg;
                # ``unknown`` = could not be placed at all under route context.
                detour_set = {
                    r.name.upper() for r in rejected if r.reason == "detour"
                }
                unknown_set = {
                    r.name.upper() for r in rejected if r.reason == "unknown"
                }
                off_route.extend(
                    c for c in candidate_waypoints if c.upper() in detour_set
                )
                skipped.extend(
                    c for c in candidate_waypoints if c.upper() in unknown_set
                )
            # Always rebuild from the resolver so ``interpreted`` stays in
            # lock-step with ``waypoint_infos`` (same order, same casing).
            interpreted = [wp.icao for wp in resolved]
            waypoint_infos = [
                WaypointInfo(
                    icao=wp.icao,
                    name=wp.name,
                    lat=wp.lat,
                    lon=wp.lon,
                    timezone=get_timezone(wp.lat, wp.lon),
                )
                for wp in resolved
            ]

    return InterpretRouteResponse(
        original_tokens=original_tokens,
        interpreted=interpreted,
        skipped=skipped,
        off_route=off_route,
        waypoints=waypoint_infos,
    )


class ParseFplRequest(BaseModel):
    """Request body for parsing an ICAO flight plan string."""

    fpl_text: str = Field(..., min_length=10, max_length=4000)


class ParseFplResponse(BaseModel):
    """Parsed ICAO flight plan fields relevant for flight creation."""

    waypoints: list[str] = []
    date: str | None = None  # YYYY-MM-DD
    time_utc: str | None = None  # HH:MM
    altitude_ft: int | None = None
    duration_hours: float | None = None
    flight_rules: str | None = None  # V, I, Y, Z
    aircraft_type: str | None = None
    raw_route: str | None = None
    error: str | None = None


@router.post("/parse-fpl", response_model=ParseFplResponse)
def parse_flight_plan(
    req: ParseFplRequest,
    user_id: str = Depends(current_user_id),
):
    """Parse an ICAO FPL string and return fields for flight creation."""
    from euro_aip.briefing import parse_icao_fpl

    # Autorouter (and some other tools) emit FPLs with a space before each
    # field separator ("...EGTF0730 -N0164F100..."). Collapsing " -" to "-"
    # converts those into canonical form so the splitter lands on the right
    # field boundaries — without this, Field 19 (supplementary info) confuses
    # the field-count heuristic and shifts departure/route/destination by one
    # slot. Idempotent on canonical FPLs.
    text = re.sub(r" +-", "-", req.fpl_text)

    fpl = parse_icao_fpl(text)
    if fpl is None:
        return ParseFplResponse(error="Could not parse flight plan. Expected (FPL-...) format.")

    # Build waypoint list: departure + route waypoints + destination
    waypoints: list[str] = []
    if fpl.route.departure:
        waypoints.append(fpl.route.departure)
    # Filter route waypoints to those matching our waypoint pattern.
    # is_valid_waypoint accepts named codes AND inline ICAO coords, so a
    # parsed FPL with a GPS waypoint flows through cleanly.
    for wp in fpl.route.waypoints:
        if is_valid_waypoint(wp):
            waypoints.append(wp)
    if fpl.route.destination and fpl.route.destination not in waypoints:
        waypoints.append(fpl.route.destination)

    # Date
    date_str = fpl.date_of_flight.isoformat() if fpl.date_of_flight else None

    # Time
    time_str = None
    if fpl.departure_time_utc:
        time_str = fpl.departure_time_utc.strftime("%H:%M")

    # Duration from EET
    duration_hours = None
    if fpl.eet_minutes is not None:
        import math
        duration_hours = float(math.ceil(fpl.eet_minutes / 60))

    return ParseFplResponse(
        waypoints=waypoints,
        date=date_str,
        time_utc=time_str,
        altitude_ft=fpl.altitude_feet,
        duration_hours=duration_hours,
        flight_rules=fpl.flight_rules,
        aircraft_type=fpl.aircraft_type,
        raw_route=fpl.raw_route,
    )


AUTOROUTER_LOGS_URL = "https://api.autorouter.aero/v1.0/router/logs"


class AutorouterRouteSummary(BaseModel):
    """One row in the Autorouter recent-routes picker."""

    routeid: str
    departure: str
    destination: str
    departure_name: str | None = None
    destination_name: str | None = None
    departure_time: str | None = None  # ISO 8601 UTC, derived from Unix epoch
    fplan: str  # raw ICAO FPL — fed into /parse-fpl on selection
    route_distance_nm: int | None = None
    aircraft_description: str | None = None
    callsign: str | None = None


class AutorouterRoutesResponse(BaseModel):
    routes: list[AutorouterRouteSummary] = []


def _clear_autorouter_oauth_token(db: Session, user_id: str) -> None:
    """Remove the stored Autorouter OAuth access token after a 401.

    Only touches the ``autorouter`` key inside the encrypted-creds blob, so
    any other per-user secrets (and dev-mode username/password fallbacks)
    are preserved. After this returns, ``has_autorouter_creds`` flips to
    false and the user will be prompted to re-link.
    """
    from flyfun_common.credentials import load_encrypted_creds, save_encrypted_creds

    creds = load_encrypted_creds(db, user_id) or {}
    if "autorouter" in creds:
        del creds["autorouter"]
        save_encrypted_creds(db, user_id, creds)


def _epoch_to_iso(epoch: object) -> str | None:
    """Convert an Autorouter Unix-epoch field to ISO 8601 UTC."""
    if epoch is None:
        return None
    try:
        ts = float(epoch)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@router.get("/autorouter-routes", response_model=AutorouterRoutesResponse)
def list_autorouter_routes(
    limit: int = 25,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> AutorouterRoutesResponse:
    """List the user's recent Autorouter routes for the "Import from Autorouter" picker.

    Maps the upstream ``/v1.0/router/logs`` response onto a slim payload the
    Flights page can render directly. On a 401 from Autorouter we clear the
    stored OAuth token and surface a 409 so the frontend can prompt re-link.
    """
    import httpx

    from weatherbrief.api.preferences import load_autorouter_token

    token = load_autorouter_token(db, user_id)
    if not token:
        raise HTTPException(status_code=409, detail="autorouter_not_linked")

    capped = max(1, min(int(limit), 100))

    try:
        resp = httpx.get(
            AUTOROUTER_LOGS_URL,
            params={"limit": capped, "order": "desc", "sort": "departuretime"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Autorouter /logs request failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=502, detail="autorouter_unreachable")

    if resp.status_code == 401:
        _clear_autorouter_oauth_token(db, user_id)
        raise HTTPException(status_code=409, detail="autorouter_not_linked")

    if resp.status_code >= 400:
        logger.warning(
            "Autorouter /logs returned %s for user %s: %s",
            resp.status_code,
            user_id,
            resp.text[:200],
        )
        raise HTTPException(status_code=502, detail="autorouter_upstream_error")

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("Autorouter /logs returned non-JSON for user %s", user_id)
        raise HTTPException(status_code=502, detail="autorouter_upstream_error")

    # The /logs endpoint historically returned a bare JSON array, but the
    # current implementation wraps it in a dict (e.g. ``{"logs": [...]}`` or
    # ``{"items": [...]}``). Accept either: bare list, or — for a dict — the
    # routes list under one of the known wrapper keys, with a defensive
    # fallback to the first list value if none match. Checking known keys
    # first avoids silently picking up an unrelated list (e.g. pagination
    # links) if Autorouter ever adds one alongside the routes list.
    _ROUTES_LIST_KEYS = ("logs", "items", "routes", "data", "results")
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = next(
            (
                payload[k]
                for k in _ROUTES_LIST_KEYS
                if isinstance(payload.get(k), list)
            ),
            None,
        )
        if entries is None:
            entries = next(
                (v for v in payload.values() if isinstance(v, list)),
                None,
            )
        if entries is None:
            logger.warning(
                "Autorouter /logs returned dict without a list value for user %s; keys=%r",
                user_id,
                list(payload.keys()),
            )
            raise HTTPException(status_code=502, detail="autorouter_upstream_error")
    else:
        logger.warning(
            "Autorouter /logs returned unexpected payload shape for user %s: %r",
            user_id,
            type(payload).__name__,
        )
        raise HTTPException(status_code=502, detail="autorouter_upstream_error")

    routes: list[AutorouterRouteSummary] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fplan = entry.get("fplan")
        routeid = entry.get("routeid")
        departure = entry.get("departure")
        destination = entry.get("destination")
        if not (fplan and routeid and departure and destination):
            # Skip rows without the fields we need to render or import.
            continue
        routes.append(
            AutorouterRouteSummary(
                routeid=str(routeid),
                departure=str(departure),
                destination=str(destination),
                departure_name=entry.get("departurename") or None,
                destination_name=entry.get("destinationname") or None,
                departure_time=_epoch_to_iso(entry.get("departuretime")),
                fplan=str(fplan),
                route_distance_nm=_coerce_int(entry.get("routedistance")),
                aircraft_description=entry.get("aircraftdescription") or None,
                callsign=entry.get("callsign") or None,
            )
        )

    return AutorouterRoutesResponse(routes=routes)


class BulkDeleteRequest(BaseModel):
    """Request body for bulk-deleting flights."""

    ids: list[str] = Field(..., max_length=200)


class BulkDeleteResponse(BaseModel):
    """Result of a bulk delete: which IDs were deleted, which were not found or not owned."""

    deleted: list[str] = []
    not_found: list[str] = []


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
def bulk_delete_flights(
    req: BulkDeleteRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Delete multiple flights in one request. Silently skips IDs that don't exist
    or aren't owned by the current user (returned in `not_found`)."""
    deleted = _bulk_delete_flights(db, req.ids, user_id)
    deleted_set = set(deleted)
    not_found = [fid for fid in req.ids if fid not in deleted_set]
    return BulkDeleteResponse(deleted=deleted, not_found=not_found)


class MoveFlightRequest(BaseModel):
    """Request body for moving a flight (atomic create-new + delete-old).

    All fields are optional; unspecified ones inherit from the source flight.
    The new flight gets a fresh ID computed from the (possibly updated) values.
    """

    departure_time: str | None = None  # ISO 8601 with timezone
    waypoints: list[str] | None = None  # min 2; new origin/dest allowed (unlike PATCH)
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None
    # raw_route follows the same sync rules as PATCH: when waypoints
    # change without a raw_route in the body, the new flight clears
    # the (now-stale) stored value rather than carrying it forward.
    raw_route: str | None = Field(default=None, max_length=4000)

    @field_validator("departure_time")
    @classmethod
    def validate_departure_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("departure_time must be a valid ISO 8601 datetime")
        if dt.tzinfo is None:
            raise ValueError("departure_time must include a timezone")
        return v

    @field_validator("waypoints")
    @classmethod
    def validate_waypoints(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) < 2:
            raise ValueError("At least 2 waypoints are required")
        for wp in v:
            if not is_valid_waypoint(wp):
                raise ValueError(
                    f"Invalid waypoint '{wp}': must be 2-5 alphanumeric "
                    f"characters or an ICAO inline coordinate (e.g. 4830N00210E)"
                )
        return v


def shift_alt_departure(
    source_departure: datetime,
    source_alt: datetime | None,
    new_departure: datetime,
) -> datetime | None:
    """Carry a flight's pinned alternate departure through a move.

    The alternate is shifted by the same delta as the primary departure, so a
    pilot who pushes a flight one day out keeps their "…or two hours later"
    two hours later.

    The stored invariant is stricter than a plain shift, though: ``update_flight``
    requires the alternate to be on the *same calendar day* as the primary and to
    differ from it. A sub-day move can break the first — 23:00 + 2h crosses
    midnight while 01:00 + 2h does not — so a shifted alternate that lands on
    another day is re-anchored to the new departure's day, keeping its time of
    day. If that collides with the primary there is no valid alternate left and
    we return ``None``; the caller then also drops ``flexibility`` from
    ``"alternate"``, because the pair must stay consistent.

    Everything is normalised to UTC first: ``new_departure`` comes straight from
    ``datetime.fromisoformat`` and may carry any offset, while the stored
    alternate is aware-UTC, so comparing their naive ``.date()`` would otherwise
    be comparing days in two different zones.
    """
    if source_alt is None:
        return None
    anchor = new_departure.astimezone(timezone.utc)
    shifted = (source_alt + (new_departure - source_departure)).astimezone(timezone.utc)
    if shifted.date() != anchor.date():
        shifted = shifted.replace(year=anchor.year, month=anchor.month, day=anchor.day)
    return None if shifted == anchor else shifted


@router.post("/{flight_id}/move", response_model=FlightResponse)
def move_flight(
    flight_id: str,
    req: MoveFlightRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Atomically replace a flight with a new one carrying updated structural fields.

    Use this when changing date, origin, or destination — the existing PATCH endpoint
    rejects those because they're encoded in the flight ID. Move creates the new flight,
    deletes the old one (cascading its packs and on-disk artifacts), and commits both
    in a single transaction. If the new ID would collide with another existing flight,
    aborts with 409 and nothing changes.
    """
    source = _load_owned_flight(db, flight_id, user_id)

    # Merge requested updates with source flight's existing values.
    new_waypoints = (
        [w.upper().strip() for w in req.waypoints]
        if req.waypoints is not None
        else list(source.waypoints)
    )
    if req.departure_time is not None:
        new_departure_time = datetime.fromisoformat(req.departure_time)
        if new_departure_time.tzinfo is None:
            new_departure_time = new_departure_time.replace(tzinfo=timezone.utc)
    else:
        new_departure_time = source.departure_time

    new_alt = req.cruise_altitude_ft if req.cruise_altitude_ft is not None else source.cruise_altitude_ft
    new_ceil = req.flight_ceiling_ft if req.flight_ceiling_ft is not None else source.flight_ceiling_ft
    new_dur = req.flight_duration_hours if req.flight_duration_hours is not None else source.flight_duration_hours

    # Validate waypoints exist in the airport DB (only when changed).
    if req.waypoints is not None:
        db_path = getattr(request.app.state, "db_path", "")
        if db_path:
            from weatherbrief.airports import resolve_waypoints

            try:
                _, _rejected = resolve_waypoints(new_waypoints, db_path)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=exc.args[0])
            if _rejected:
                raise HTTPException(
                    status_code=422,
                    detail=_rejected_waypoints_message(_rejected),
                )

    # Past-departure rule mirrors create_flight: only admins can move into the past.
    if new_departure_time < datetime.now(timezone.utc):
        if not _is_admin_or_dev(request, db):
            raise HTTPException(
                status_code=403,
                detail="Only admins can move a flight into the past",
            )

    # And, like create, a move cannot push a flight past the booking cap
    # (beyond-horizon but within the cap is allowed → pending coverage).
    _reject_if_beyond_booking_cap(new_departure_time)

    # Re-derive route_name only when waypoints actually changed; otherwise keep
    # the source's stored name (which may be non-derived, e.g. set explicitly
    # at create time or imported from an FPL).
    new_route_name = (
        "_".join(w.lower() for w in new_waypoints)
        if req.waypoints is not None
        else source.route_name
    )
    new_id = _compute_flight_id(
        route_name=new_route_name,
        departure_time=new_departure_time,
        cruise_altitude_ft=new_alt,
        flight_ceiling_ft=new_ceil,
        flight_duration_hours=new_dur,
        user_id=user_id,
    )

    if new_id == flight_id:
        raise HTTPException(
            status_code=422,
            detail="Move requires at least one structural change (date, origin/dest, altitude, ceiling, duration).",
        )
    # Reject if new ID collides with a different existing flight.
    try:
        load_flight(db, new_id)
        raise HTTPException(
            status_code=409,
            detail=f"A flight with ID '{new_id}' already exists.",
        )
    except KeyError:
        pass

    # raw_route on the moved flight:
    #   raw_route in body                     → store + stamp parser version
    #   raw_route omitted, route changed      → clear (stale, would lie)
    #   raw_route omitted, route unchanged    → preserve (still valid)
    # The "route unchanged" branch covers both "waypoints absent" and
    # "waypoints supplied but equal to source" — without the latter,
    # the web ``moveBtn`` (which always sends ``waypoints``) would lose
    # the source's raw_route on a date-only move.
    if req.raw_route is not None:
        new_raw_route = req.raw_route.strip() or None
        new_parser_version = (
            _euro_aip_parser_version() if new_raw_route else None
        )
    elif new_waypoints != source.waypoints:
        new_raw_route = None
        new_parser_version = None
    else:
        new_raw_route = source.raw_route
        new_parser_version = source.parser_version

    # Timing-scenario state follows the flight. Left to their column defaults
    # these silently reset to "none" / NULL / "default", so a pilot who moved a
    # next_day-scanning flight lost the scan with no warning.
    new_alt_departure_time = shift_alt_departure(
        source.departure_time, source.alt_departure_time, new_departure_time,
    )
    new_flexibility = source.flexibility
    if new_flexibility == "alternate" and new_alt_departure_time is None:
        # The invariant update_flight enforces: "alternate" without an alt time
        # is not a storable state.
        new_flexibility = "none"

    new_flight = Flight(
        id=new_id,
        user_id=user_id,
        profile_id=source.profile_id,
        aircraft_id=source.aircraft_id,
        route_name=new_route_name,
        waypoints=new_waypoints,
        departure_time=new_departure_time,
        cruise_altitude_ft=new_alt,
        flight_ceiling_ft=new_ceil,
        flight_duration_hours=new_dur,
        private=source.private,
        alt_departure_time=new_alt_departure_time,
        flexibility=new_flexibility,
        notify_override=source.notify_override,
        auto_refresh=source.auto_refresh,
        auto_refresh_hour=source.auto_refresh_hour,
        raw_route=new_raw_route,
        parser_version=new_parser_version,
        # Carry the source's share_code over so links recipients already
        # have keep resolving after a move (date/route edit). The unique
        # index is honored because we delete the source row first.
        share_code=source.share_code,
        created_at=datetime.now(tz=timezone.utc),
    )

    # Subscribers follow the flight too. ``delete_flight`` cascades
    # ``flight_subscriptions`` while ``share_code`` is deliberately carried over
    # above — so without this the shared link keeps resolving for everyone who
    # has it, but every existing subscriber is silently dropped. Snapshot before
    # the delete, re-insert after the new row exists, so the FK holds at every
    # step of the transaction.
    subscribers = [
        (row.user_id, row.created_at)
        for row in db.execute(
            select(FlightSubscriptionRow).where(
                FlightSubscriptionRow.flight_id == flight_id
            )
        ).scalars().all()
    ]

    # Single transaction: delete old (cascades packs + artifacts), insert new.
    delete_flight(db, flight_id)
    save_flight(db, new_flight, user_id)
    for subscriber_id, subscribed_at in subscribers:
        db.add(
            FlightSubscriptionRow(
                flight_id=new_id, user_id=subscriber_id, created_at=subscribed_at,
            )
        )
    db.flush()

    return _flight_to_response(new_flight, db, viewer_id=user_id)


# ---------------------------------------------------------------------------
# Frequent airports — the user's most-used departures/destinations (#419, B3)
# ---------------------------------------------------------------------------

# Airport waypoints are 4-letter ICAO location indicators; a route may also
# carry navaids/fixes (which are not airports), so frequency counting keeps
# only ICAO-shaped tokens at the route ends.
_ICAO_RE = re.compile(r"^[A-Z]{4}$")


class FrequentAirport(BaseModel):
    """One airport in a frequency ranking."""

    icao: str
    count: int


class FrequentAirportsResponse(BaseModel):
    """The user's most-used departure and destination airports."""

    departures: list[FrequentAirport]
    destinations: list[FrequentAirport]


def compute_frequent_airports(
    db: Session, user_id: str, *, top_n: int = 5
) -> FrequentAirportsResponse:
    """Rank the user's most-used departure and destination airports.

    Derived from flight history — there is no ``home_base`` concept in the
    schema, and the departure/destination are not stored as columns: the route
    lives in ``FlightRow.waypoints_json`` (a JSON array), so departure is the
    first waypoint and destination the last. Only the user's OWN flights are
    counted (not flights they merely subscribe to), and only ICAO-shaped route
    ends (navaids/fixes are skipped). Ties keep ``Counter.most_common`` order.

    Pure logic (no ``Depends`` defaults) so it is unit-testable and reusable —
    iOS centres the forecast map on cold open from this; the web can prefill
    Add-Flight from it.
    """
    rows = db.execute(
        select(FlightRow.waypoints_json).where(FlightRow.user_id == user_id)
    ).scalars().all()

    departures: Counter[str] = Counter()
    destinations: Counter[str] = Counter()
    for raw in rows:
        try:
            waypoints = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            continue
        codes = [w.upper() for w in waypoints if isinstance(w, str)]
        if not codes:
            continue
        if _ICAO_RE.match(codes[0]):
            departures[codes[0]] += 1
        # Destination only when the route has a distinct end.
        if len(codes) >= 2 and _ICAO_RE.match(codes[-1]):
            destinations[codes[-1]] += 1

    def _rank(counter: Counter[str]) -> list[FrequentAirport]:
        return [
            FrequentAirport(icao=icao, count=n)
            for icao, n in counter.most_common(top_n)
        ]

    return FrequentAirportsResponse(
        departures=_rank(departures), destinations=_rank(destinations)
    )


@router.get("/frequent-airports", response_model=FrequentAirportsResponse)
def get_frequent_airports(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return the current user's top-5 departure and destination airports.

    Discovery ("where can I fly this weekend") needs an origin; this derives one
    from flight history instead of adding a preference + onboarding step. Used
    by the iOS forecast map to centre on cold open, and reusable by the web
    Add-Flight prefill. See ``compute_frequent_airports``.
    """
    return compute_frequent_airports(db, user_id)


@router.get("/by-share/{code}", response_model=FlightResponse)
def get_flight_by_share_code(
    code: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Resolve a share code to a flight — the iOS preview-before-subscribe on-ramp.

    The web ``/s/{code}`` link 302-redirects a *browser* to the briefing page;
    the app can't follow that redirect, so it resolves the code to a flight
    itself. Authenticated like every other ``/api/flights`` route (unauth → 401,
    which the app handles via ``handleUnauthorized``). Visibility is enforced
    identically to ``get_flight`` by delegating to ``_load_flight_or_404`` with
    ``viewer_id``: a private flight owned by someone else — or an unknown/invalid
    code — is a 404. A non-owner viewer gets a normal ``FlightResponse`` with
    ``role: subscriber`` / ``is_subscribed`` / ``owner_display_name`` so the app
    can render the Subscribe affordance.

    Registered before ``/{flight_id}`` so ``by-share`` can never be swallowed as
    a flight id (e.g. a share code that happens to spell ``export``).
    """
    if not SHARE_CODE_RE.match(code):
        # Reject obviously-bogus codes before the DB hit (mirrors /s/{code}).
        raise HTTPException(status_code=404, detail="Unknown share link")
    flight_id = lookup_flight_id_by_share_code(db, code)
    if flight_id is None:
        raise HTTPException(status_code=404, detail="Unknown share link")
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    return _flight_to_response(flight, db, viewer_id=user_id)


@router.get("/{flight_id}", response_model=FlightResponse)
def get_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get flight details. Any authenticated user can view public flights."""
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
    return _flight_to_response(flight, db, viewer_id=user_id)


@router.get("/{flight_id}/export")
def export_flight(
    flight_id: str,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Export a flight as a cross-app ``FlightExchange`` JSON payload.

    The shared interchange format other flyfun apps (forms, brief) consume to
    import a flight's route/time/aircraft — see euro_aip's ``FlightExchange``.
    Authenticated, and respects the same visibility as ``GET /{flight_id}``:
    the owner always, plus any viewer for a non-private flight. Distinct from
    the public ``/s/{code}`` share link, which redirects humans to the briefing
    page and stays unchanged.
    """
    flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)

    if len(flight.waypoints) < 2:
        raise HTTPException(
            status_code=422, detail=f"Flight '{flight_id}' has no route to export"
        )

    db_path = getattr(request.app.state, "db_path", "")
    if not db_path:
        raise HTTPException(status_code=503, detail="Airport database unavailable")

    try:
        exchange = flight_to_exchange(flight, db, db_path, viewer_id=user_id)
    except KeyError as exc:
        detail = exc.args[0] if exc.args else "Route could not be resolved"
        raise HTTPException(status_code=422, detail=detail)

    return exchange.to_dict()


class SubscribeResponse(BaseModel):
    """Result of a subscribe call. ``created`` is False when already subscribed."""

    flight_id: str
    user_id: str
    created: bool


@router.post("/{flight_id}/subscribe", response_model=SubscribeResponse)
def subscribe_to_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Subscribe the current user to another pilot's flight.

    404 — flight not found, or flight is private and viewer is not the owner
    409 — viewer is the flight owner (cannot subscribe to own flights)
    200 — idempotent: new subscription created or already existed
    """
    # Access check first: subscribers can only subscribe to public flights they can see.
    _load_flight_or_404(db, flight_id, viewer_id=user_id)
    try:
        created = subscribe_flight(db, flight_id, user_id)
    except SubscriptionError:
        raise HTTPException(
            status_code=409,
            detail="You own this flight — owners cannot subscribe to their own flights.",
        )
    except KeyError:
        # TOCTOU: flight was deleted between the access check and the insert.
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return SubscribeResponse(flight_id=flight_id, user_id=user_id, created=created)


@router.delete("/{flight_id}/subscribe", status_code=204)
def unsubscribe_from_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Remove the current user's subscription. Idempotent — returns 204 either way.

    Intentionally skips the _load_flight_or_404 access check that subscribe does.
    Unsubscribe only deletes rows matching (flight_id, user_id), so a user can
    only ever drop their own row — no privilege escalation is possible — and the
    204 on an unknown flight_id is the documented idempotent behavior so a
    recipient whose shared flight was deleted or flipped private can still
    clean up their own subscription state.
    """
    unsubscribe_flight(db, flight_id, user_id)


class UpdateAutoRefreshRequest(BaseModel):
    """Request body for updating auto-refresh settings."""

    auto_refresh: bool
    auto_refresh_hour: int | None = None  # None = default (target_time_utc - 1)

    @field_validator("auto_refresh_hour")
    @classmethod
    def validate_hour(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 23):
            raise ValueError("auto_refresh_hour must be 0-23")
        return v


@router.patch("/{flight_id}/auto-refresh", response_model=FlightResponse)
def update_auto_refresh(
    flight_id: str,
    req: UpdateAutoRefreshRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update auto-refresh settings for a flight."""
    row = _load_owned_row(db, flight_id, user_id)
    was_enabled = row.auto_refresh
    row.auto_refresh = req.auto_refresh
    row.auto_refresh_hour = req.auto_refresh_hour
    if req.auto_refresh and not was_enabled:
        # Only on the off→on transition: restore the pre-#366 "tell me whenever a
        # new report is ready" behavior by defaulting this flight's bell to
        # "always" so every scheduled refresh notifies, even when the assessment
        # is unchanged but the detail moved. Still fully overridable afterward —
        # so we must NOT re-seed on every auto_refresh=true request (the web
        # hour-select sends auto_refresh=true while already on), which would
        # silently clobber a subsequent "mute". Disabling likewise leaves the
        # bell untouched — we never silently undo a notify choice.
        row.notify_override = "notify"
    db.flush()
    updated = load_flight(db, flight_id)
    return _flight_to_response(updated, db, viewer_id=user_id)


class UpdatePrivacyRequest(BaseModel):
    """Request body for updating flight privacy."""

    private: bool


@router.patch("/{flight_id}/privacy", response_model=FlightResponse)
def update_privacy(
    flight_id: str,
    req: UpdatePrivacyRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Toggle flight visibility. Only the flight owner can change this."""
    row = _load_owned_row(db, flight_id, user_id)
    row.private = req.private
    db.flush()
    updated = load_flight(db, flight_id)
    return _flight_to_response(updated, db, viewer_id=user_id)


class UpdateFlightRequest(BaseModel):
    """Request body for updating editable flight parameters."""

    profile_id: int | None = None  # switch aircraft profile (applies its altitude/ceiling)
    aircraft_id: int | None = None  # switch aircraft (applies speed/ceiling defaults)
    departure_time: str | None = None  # ISO 8601 (time-of-day change only; date must match)
    alt_departure_time: str | None = None  # ISO 8601 or "" to clear
    # Timing-scenario Flexibility mode; None = no change. "alternate" grades
    # alt_departure_time; day modes run the departure-window scan.
    flexibility: Literal["none", "alternate", "same_day", "prev_day", "next_day"] | None = None
    # Per-flight briefing-notification override; None = no change.
    notify_override: Literal["default", "notify", "mute"] | None = None
    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    flight_duration_hours: float | None = None
    waypoints: list[str] | None = Field(default=None, max_length=MAX_ROUTE_WAYPOINTS)  # updated route (origin+destination must match)
    # Optional original Field-15 input. When supplied alongside ``waypoints``
    # (web Save flow), both are stored and ``parser_version`` is re-stamped.
    # When ``waypoints`` change but ``raw_route`` is omitted, the stored
    # raw_route is cleared — the previous string no longer matches the
    # resolved list, and a stale value would lie about what produced it.
    raw_route: str | None = Field(default=None, max_length=4000)

    @field_validator("departure_time")
    @classmethod
    def validate_departure_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("departure_time must be a valid ISO 8601 datetime")
        if dt.tzinfo is None:
            raise ValueError("departure_time must include a timezone")
        return v

    @field_validator("alt_departure_time")
    @classmethod
    def validate_alt_departure_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("alt_departure_time must be a valid ISO 8601 datetime or empty string")
        if dt.tzinfo is None:
            raise ValueError("alt_departure_time must include a timezone")
        return v


class UpdateFlightResponse(FlightResponse):
    """Flight response with invalidation hint after an update."""

    invalidation: str  # "none" | "advisories_only" | "refetch_needed"


@router.patch("/{flight_id}", response_model=UpdateFlightResponse)
def update_flight(
    flight_id: str,
    req: UpdateFlightRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update editable flight parameters (time, altitude, ceiling, duration, route).

    The date portion and origin/destination of the flight cannot change.
    Returns an invalidation hint so the frontend knows whether existing
    briefings need a refetch or just an advisory recalculation.
    """
    row = _load_owned_row(db, flight_id, user_id)
    original_flight = load_flight(db, flight_id)

    time_changed = False
    altitude_changed = False
    profile_changed = False
    route_changed = False

    # Route (waypoints) change — origin and destination must remain the same
    if req.waypoints is not None:
        new_waypoints = [w.upper().strip() for w in req.waypoints]
        if len(new_waypoints) < 2:
            raise HTTPException(
                status_code=422,
                detail="At least 2 waypoints are required",
            )
        # Validate waypoint format
        for wp in new_waypoints:
            if not is_valid_waypoint(wp):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Invalid waypoint '{wp}': must be 2-5 alphanumeric "
                        f"characters or an ICAO inline coordinate (e.g. 4830N00210E)"
                    ),
                )
        # Validate waypoints exist in the database
        db_path = getattr(request.app.state, "db_path", "")
        if db_path:
            from weatherbrief.airports import resolve_waypoints

            try:
                _, _rejected = resolve_waypoints(new_waypoints, db_path)
            except KeyError as exc:
                raise HTTPException(status_code=422, detail=exc.args[0])
            if _rejected:
                raise HTTPException(
                    status_code=422,
                    detail=_rejected_waypoints_message(_rejected),
                )
        # Enforce same origin and destination
        old_origin = original_flight.waypoints[0] if original_flight.waypoints else None
        old_dest = original_flight.waypoints[-1] if original_flight.waypoints else None
        new_origin = new_waypoints[0]
        new_dest = new_waypoints[-1]
        if old_origin and new_origin != old_origin:
            raise HTTPException(
                status_code=422,
                detail=f"Origin cannot change (was {old_origin}, got {new_origin}). Create a new flight instead.",
            )
        if old_dest and new_dest != old_dest:
            raise HTTPException(
                status_code=422,
                detail=f"Destination cannot change (was {old_dest}, got {new_dest}). Create a new flight instead.",
            )
        if new_waypoints != original_flight.waypoints:
            row.waypoints_json = json.dumps(new_waypoints)
            # Update route_name to reflect new waypoints
            row.route_name = "_".join(w.lower() for w in new_waypoints)
            route_changed = True

    # raw_route sync — independent of whether waypoints were in the body,
    # so a client can annotate an existing flight with a fresh raw_route
    # (iOS-then-annotate) or update altitude/etc. without disturbing it.
    #   raw_route in body                  → store + stamp parser version
    #   raw_route omitted, route changed   → clear (stale, would lie)
    #   raw_route omitted, route unchanged → leave alone (still valid)
    if req.raw_route is not None:
        row.raw_route = req.raw_route.strip() or None
        row.parser_version = (
            _euro_aip_parser_version() if row.raw_route else None
        )
    elif route_changed:
        row.raw_route = None
        row.parser_version = None

    # Profile change — apply the new profile's altitude/ceiling to the flight
    if req.profile_id is not None and req.profile_id != original_flight.profile_id:
        from weatherbrief.api.profiles import load_profile_settings

        _validate_profile_ownership(db, req.profile_id, user_id)
        profile_settings = load_profile_settings(db, req.profile_id, user_id)
        row.profile_id = req.profile_id
        profile_changed = True

        # Apply profile's altitude/ceiling unless explicitly overridden in this request
        if req.cruise_altitude_ft is None and profile_settings.get("cruise_altitude_ft"):
            row.cruise_altitude_ft = profile_settings["cruise_altitude_ft"]
            altitude_changed = True
        if req.flight_ceiling_ft is None and profile_settings.get("flight_ceiling_ft"):
            row.flight_ceiling_ft = profile_settings["flight_ceiling_ft"]
            altitude_changed = True

    # Aircraft change (0 is the "clear" sentinel — no ownership to check)
    if req.aircraft_id is not None and req.aircraft_id != original_flight.aircraft_id:
        if req.aircraft_id != 0:
            _validate_aircraft_ownership(db, req.aircraft_id, user_id)
        row.aircraft_id = req.aircraft_id if req.aircraft_id != 0 else None

    if req.departure_time is not None:
        new_dt = datetime.fromisoformat(req.departure_time)
        if new_dt.tzinfo is None:
            new_dt = new_dt.replace(tzinfo=timezone.utc)
        # Enforce same date
        if new_dt.strftime("%Y-%m-%d") != original_flight.target_date:
            raise HTTPException(
                status_code=422,
                detail="Cannot change the flight date. Create a new flight instead.",
            )
        if new_dt != original_flight.departure_time:
            row.departure_time = new_dt
            time_changed = True

    if req.cruise_altitude_ft is not None and req.cruise_altitude_ft != original_flight.cruise_altitude_ft:
        row.cruise_altitude_ft = req.cruise_altitude_ft
        altitude_changed = True

    if req.flight_ceiling_ft is not None and req.flight_ceiling_ft != original_flight.flight_ceiling_ft:
        row.flight_ceiling_ft = req.flight_ceiling_ft
        altitude_changed = True

    if req.flight_duration_hours is not None and req.flight_duration_hours != original_flight.flight_duration_hours:
        row.flight_duration_hours = req.flight_duration_hours
        time_changed = True

    # Alt departure time: "" clears, ISO string sets, None = no change
    if req.alt_departure_time is not None:
        if req.alt_departure_time == "":
            row.alt_departure_time = None
        else:
            alt_dt = datetime.fromisoformat(req.alt_departure_time)
            # Validate same-day constraint
            effective_departure = row.departure_time
            if alt_dt.date() != effective_departure.date():
                raise HTTPException(
                    status_code=422,
                    detail="Alt departure time must be on the same day as the primary departure.",
                )
            # Validate alt ≠ primary
            if alt_dt == effective_departure:
                raise HTTPException(
                    status_code=422,
                    detail="Alt departure time must differ from the primary departure time.",
                )
            row.alt_departure_time = alt_dt

    if req.flexibility is not None:
        # row.alt_departure_time already reflects this request (applied above).
        if req.flexibility == "alternate" and row.alt_departure_time is None:
            raise HTTPException(
                status_code=422,
                detail="Flexibility 'alternate' requires an alt departure time.",
            )
        row.flexibility = req.flexibility

    if req.notify_override is not None:
        row.notify_override = req.notify_override

    db.flush()
    updated = load_flight(db, flight_id)

    if time_changed or route_changed:
        invalidation = "refetch_needed"
    elif altitude_changed or profile_changed:
        invalidation = "advisories_only"
    else:
        invalidation = "none"

    resp = _flight_to_response(updated, db, viewer_id=user_id)
    return UpdateFlightResponse(**resp.model_dump(), invalidation=invalidation)


@router.delete("/{flight_id}", status_code=204)
def remove_flight(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Delete a flight and all its packs."""
    _load_owned_flight(db, flight_id, user_id)  # verify ownership
    try:
        delete_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")


def _load_flight_or_404(db: Session, flight_id: str, *, viewer_id: str | None = None) -> Flight:
    """Load a flight by ID. Returns 404 if not found or private and not owned by viewer."""
    # Dev-only golden-labeling workbench (#254): resolve ``eval-<id>`` flights
    # from the file corpus instead of the DB. Dead code in production (flag off).
    from weatherbrief.eval_workbench.config import eval_workbench_enabled, is_eval_flight_id

    if eval_workbench_enabled() and is_eval_flight_id(flight_id):
        from weatherbrief.eval_workbench.resolver import resolve_eval_flight

        try:
            return resolve_eval_flight(flight_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    try:
        flight = load_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    if flight.private and viewer_id is not None and flight.user_id != viewer_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return flight


def _load_owned_flight(db: Session, flight_id: str, user_id: str) -> Flight:
    """Load a flight, verifying it belongs to the current user. Returns 404 if not found or not owned."""
    flight = _load_flight_or_404(db, flight_id)
    if flight.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return flight


def _load_owned_row(db: Session, flight_id: str, user_id: str):
    """Load the ORM FlightRow, verifying ownership. Single query for PATCH endpoints."""
    from weatherbrief.db.models import FlightRow

    row = db.get(FlightRow, flight_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return row


def _validate_aircraft_ownership(db: Session, aircraft_id: int, user_id: str) -> None:
    """Raise 400 if aircraft_id doesn't belong to the caller (mirrors pireps)."""
    from weatherbrief.db.models import UserAircraftRow

    aircraft = db.get(UserAircraftRow, aircraft_id)
    if aircraft is None or aircraft.user_id != user_id:
        raise HTTPException(status_code=400, detail="Aircraft not found or not owned by you")


def _validate_profile_ownership(db: Session, profile_id: int, user_id: str) -> None:
    """Raise 400 if profile_id doesn't belong to the caller."""
    from weatherbrief.db.models import FlightProfileRow

    profile = db.get(FlightProfileRow, profile_id)
    if profile is None or profile.user_id != user_id:
        raise HTTPException(status_code=400, detail="Profile not found or not owned by you")
