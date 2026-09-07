"""Deterministic trip summary — the binding leg of a conjunctive chain (#602).

A trip is a **conjunctive feasibility chain with a shrinking scope**: it happens
only if *all* its legs work, and the set that must work shrinks as legs are
flown. The value the per-flight briefing structurally cannot deliver is
therefore *"which leg decides this trip, and when does that leg become
decidable"* — never a colour for the trip as a whole.

This module is **pure**: no DB session, no FastAPI, no pack-file reads. Callers
hand it one :class:`TripLegInput` per member leg, built from data already
denormalized on ``briefing_packs`` (``assessment`` / ``outlook`` / ``days_out``
/ ``advisory_summary``), and get a :class:`TripSummary` back. That is what lets
web, iOS, MCP, the notification coalescer and the AI-summary guardrail all
share one definition of "the binding leg", the way
``analysis/airport_consensus.py`` is shared between the forecast map and
alternates.

**Never persist the result.** It is stale the moment any leg refreshes; compute
it per read. (The *AI* paragraph is the exception and lives on the trip row,
because it costs money.)

Two aggregations, never one
---------------------------
Folding a D-7 AMBER and a D-1 GREEN through ``worst()`` gives AMBER, and that
D-7 AMBER is mostly *uncertainty* rather than weather. Do that across three
legs and every trip more than four days out is amber-or-red, the signal dies,
and pilots stop reading it. So there are two outputs and the second is the
headline:

1. :attr:`TripSummary.chain_status` — worst traffic light across the
   **gradeable remaining** legs. Beyond-horizon legs (which carry an
   ``outlook``, not an assessment) and pending-coverage legs are reported
   separately and never folded in.
2. :attr:`TripSummary.decision_ripeness_days` — "the binding leg is 5 days out;
   nothing here is decidable until Thursday."
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from weatherbrief.models import AdvisorySummary

# GREEN < AMBER < RED. UNAVAILABLE is deliberately absent: it is not a rung on
# this ladder but the absence of one, so it can never be the binding leg — the
# same convention ``notify/dispatch.py`` uses for its worsened-delta ranking.
_ASSESSMENT_RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}

# Settled < mixed < unsettled. A separate ladder on purpose: an outlook is a
# tendency, not a verdict, and the two are mutually exclusive by design.
_OUTLOOK_RANK = {"TRENDING_SETTLED": 0, "MIXED_SIGNALS": 1, "TRENDING_UNSETTLED": 2}

#: Below this ground gap two consecutive legs are one *sortie* — a fuel or
#: customs stop rather than a decision point. Fixed for v1; deriving it from
#: turnaround time or local night is still open (see the design doc).
SORTIE_GAP_HOURS = 4.0

LegState = Literal["flown", "cancelled", "remaining"]
GradeKind = Literal["assessment", "outlook", "pending_coverage", "needs_briefing", "unavailable"]
BindingBasis = Literal["assessment", "outlook"]


class TripLegInput(BaseModel):
    """One member leg, flattened from ``(FlightRow, latest BriefingPackRow)``.

    Everything here is already denormalized on the pack row, so a trip summary
    costs one DB query and no pack-file reads — cheap enough to render on every
    card in the flights list.
    """

    flight_id: str
    waypoints: list[str] = Field(default_factory=list)
    route_name: str = ""
    departure_time: datetime  # aware UTC
    duration_hours: float = 0.0

    # --- latest pack (all None when the leg has never been briefed) ---
    days_out: int | None = None
    assessment: str | None = None
    assessment_reason: str | None = None
    outlook: str | None = None
    outlook_reason: str | None = None
    advisory_summary: AdvisorySummary | None = None
    fetch_timestamp: datetime | None = None

    #: The leg sits beyond the forecast horizon — no model reaches its date yet.
    #: A *third* state, not a bad grade: it is neither gradeable nor a tendency.
    pending_coverage: bool = False
    #: Pilot debrief decision when one exists ("flown" / "cancelled" / …).
    #: Refines the clock-time reading of "remaining".
    debrief_decision: str | None = None


class ContinuityWarning(BaseModel):
    """Leg *k*'s destination is not leg *k+1*'s origin.

    Soft by design — pilots reposition, and a move is allowed to break the
    chain. Surfaced because a broken chain is usually a mistake worth seeing.
    """

    after_flight_id: str
    before_flight_id: str
    arrives: str
    departs: str


class TripLeg(BaseModel):
    """One leg as the trip view renders it."""

    flight_id: str
    label: str  # "EGTF → LSGS"
    origin: str | None = None
    destination: str | None = None
    departure_time: datetime
    duration_hours: float = 0.0
    state: LegState
    grade_kind: GradeKind
    assessment: str | None = None
    assessment_reason: str | None = None
    outlook: str | None = None
    outlook_reason: str | None = None
    days_out: int | None = None
    advisory_summary: AdvisorySummary | None = None
    fetch_timestamp: datetime | None = None
    #: Ground time since the previous leg landed, in hours (None on leg 1).
    #: Negative is possible if the pilot scheduled overlapping legs; reported
    #: as-is rather than clamped, since it is a data problem worth seeing.
    gap_hours_before: float | None = None
    #: This leg and the previous one are one sortie (gap < SORTIE_GAP_HOURS) —
    #: a stop, not a commit point.
    same_sortie_as_previous: bool = False


class TripSummary(BaseModel):
    """The deterministic trip picture. Computed per read, never persisted."""

    trip_id: str
    name: str = ""
    legs: list[TripLeg] = Field(default_factory=list)
    total_legs: int = 0
    remaining_legs: int = 0

    #: Worst traffic light among the *gradeable remaining* legs. None when no
    #: remaining leg carries one (all beyond horizon / pending / unbriefed).
    chain_status: str | None = None
    #: The leg that decides the trip, and what it was picked on.
    binding_leg_id: str | None = None
    binding_basis: BindingBasis | None = None

    #: Reported separately, never competing with the traffic light.
    beyond_horizon_leg_ids: list[str] = Field(default_factory=list)
    pending_coverage_leg_ids: list[str] = Field(default_factory=list)
    needs_briefing_leg_ids: list[str] = Field(default_factory=list)
    #: Briefed, but the pack came back ungradeable (``assessment`` UNAVAILABLE).
    #: A fourth state, and distinct from "not briefed yet" — saying a leg has no
    #: briefing when it has one that failed to grade is simply false.
    unavailable_leg_ids: list[str] = Field(default_factory=list)

    #: ``days_out`` of the binding leg, and the date its first GRIB-backed
    #: briefing becomes available. The most useful line at booking time.
    decision_ripeness_days: int | None = None
    decidable_from: date | None = None

    is_round_trip: bool = False
    chain_label: str = ""  # "EGTF → LSGS → LFAT → EGTF"
    continuity_warnings: list[ContinuityWarning] = Field(default_factory=list)

    #: One deterministic sentence. The thing the eye should land on — the AI
    #: paragraph is visually secondary to it and can only make it nicer to read.
    headline: str = ""


def _grib_horizon_days() -> int:
    """Whole days the ECMWF GRIB feed reaches — the "decidable" boundary.

    Read from the freshness registry (the same source
    ``digest.llm_digest.ecmwf_grib_horizon_days`` uses) rather than hard-coded,
    but degrading to 7 rather than raising: a trip summary must render even if
    the registry is unavailable, and this only shifts an advisory date by a day.
    """
    try:
        from weatherbrief.fetch.freshness.registry import ECMWF_GRIB_SOURCE, max_horizon

        return int(max_horizon(ECMWF_GRIB_SOURCE).total_seconds() // 86400)
    except Exception:  # pragma: no cover - registry always present in practice
        return 7


def _airport_ends(leg: TripLegInput) -> tuple[str | None, str | None]:
    wps = leg.waypoints or [
        w.upper() for w in leg.route_name.split("_") if w
    ]
    if not wps:
        return None, None
    if len(wps) == 1:
        return wps[0], wps[0]
    return wps[0], wps[-1]


def _leg_label(leg: TripLegInput) -> str:
    origin, dest = _airport_ends(leg)
    if origin and dest:
        return f"{origin} → {dest}"
    return leg.route_name or leg.flight_id


def _leg_state(leg: TripLegInput, now: datetime) -> LegState:
    """Clock time is primary; the debrief refines the label.

    A leg the pilot marked cancelled is not "remaining" even if it is still in
    the future, and a leg marked flown is behind us even if the clock hasn't
    quite caught up (an early departure).
    """
    if leg.debrief_decision == "cancelled":
        return "cancelled"
    if leg.debrief_decision == "flown":
        return "flown"
    ended = leg.departure_time + timedelta(hours=leg.duration_hours or 0.0)
    return "flown" if ended < now else "remaining"


def _grade_kind(leg: TripLegInput) -> GradeKind:
    """Which of the mutually-exclusive grading regimes this leg is in."""
    if leg.pending_coverage:
        return "pending_coverage"
    if leg.outlook:
        return "outlook"
    if leg.assessment:
        return "unavailable" if leg.assessment.upper() not in _ASSESSMENT_RANK else "assessment"
    return "needs_briefing"


def _pick_binding_leg(
    legs: list[TripLeg],
) -> tuple[str | None, BindingBasis | None, str | None]:
    """The leg that decides the trip: ``(flight_id, basis, chain_status)``.

    **This is the rule, and it lives in exactly one place** — the design expects
    it to need iteration, so nothing else should re-derive it.

    Two tiers, and an outlook never competes with a traffic light:

    1. The worst **gradeable remaining** leg (a real GREEN/AMBER/RED), ties
       broken by the earliest departure — the sooner decision is the one that
       binds first. ``UNAVAILABLE`` is not a rung on the ladder and is skipped.
    2. Only when *no* remaining leg is gradeable, the worst remaining
       **outlook**. Reported with ``basis="outlook"`` so the caller can say
       "outlook is mixed" rather than print a colour that does not exist. This
       is what lets a trip booked entirely beyond the horizon still name the leg
       to watch instead of saying nothing.

    Returns ``chain_status`` only for tier 1 — an outlook is never folded into a
    traffic light.
    """
    remaining = [leg for leg in legs if leg.state == "remaining"]

    gradeable = [
        leg for leg in remaining
        if leg.grade_kind == "assessment"
        and (leg.assessment or "").upper() in _ASSESSMENT_RANK
    ]
    if gradeable:
        worst = max(
            gradeable,
            key=lambda leg: (
                _ASSESSMENT_RANK[(leg.assessment or "").upper()],
                -leg.departure_time.timestamp(),
            ),
        )
        return worst.flight_id, "assessment", (worst.assessment or "").upper()

    outlooks = [
        leg for leg in remaining
        if leg.grade_kind == "outlook" and (leg.outlook or "").upper() in _OUTLOOK_RANK
    ]
    if outlooks:
        worst = max(
            outlooks,
            key=lambda leg: (
                _OUTLOOK_RANK[(leg.outlook or "").upper()],
                -leg.departure_time.timestamp(),
            ),
        )
        return worst.flight_id, "outlook", None

    return None, None, None


def _weekday(dt: datetime) -> str:
    return dt.strftime("%A")


def _build_headline(summary: TripSummary, binding: TripLeg | None) -> str:
    """One deterministic sentence: which leg decides, and when it is decidable.

    Never a verdict for the trip. It ranks and directs attention — that is the
    whole contract of this feature, so the vocabulary here stays descriptive
    ("decides", "is the one to watch") and never recommends.
    """
    if summary.total_legs == 0:
        return "This trip has no legs yet."

    if binding is None:
        if summary.remaining_legs == 0:
            return "Every leg of this trip is behind you."
        if summary.pending_coverage_leg_ids:
            return (
                f"No weather model reaches {'this leg' if len(summary.pending_coverage_leg_ids) == 1 else 'these legs'} yet — "
                "nothing to weigh until they come into range."
            )
        # Briefed-but-ungradeable and never-briefed are different claims, and a
        # trip can hold both at once — so describe whatever is actually there
        # rather than falling through to a sentence that is false for half of
        # the legs.
        if summary.unavailable_leg_ids:
            n = len(summary.unavailable_leg_ids)
            parts = [
                f"{'One remaining leg' if n == 1 else f'{n} remaining legs'} "
                f"{'was' if n == 1 else 'were'} briefed, but the forecast could "
                "not be assessed."
            ]
            waiting = len(summary.needs_briefing_leg_ids)
            if waiting:
                parts.append(
                    f"{'Another' if waiting == 1 else f'{waiting} others'} "
                    f"{'has' if waiting == 1 else 'have'} no briefing yet."
                )
            parts.append("Re-check after the next model run.")
            return " ".join(parts)
        return "No leg of this trip has a briefing yet."

    when = f"{_weekday(binding.departure_time)}'s {binding.label}"
    parts = [f"{when} decides this trip."]

    if summary.binding_basis == "assessment":
        parts.append(f"It is {(binding.assessment or '').upper()} at D-{binding.days_out}.")
    else:
        tendency = (binding.outlook or "").replace("_", " ").lower()
        parts.append(
            f"It is {binding.days_out} day{'' if binding.days_out == 1 else 's'} out — "
            f"outlook is {tendency.replace('trending ', '')}."
        )

    if summary.decidable_from is not None:
        parts.append(
            f"Not decidable on high-resolution guidance until {summary.decidable_from.strftime('%a %d %b')}."
        )

    extras = []
    if summary.needs_briefing_leg_ids:
        n = len(summary.needs_briefing_leg_ids)
        extras.append(f"{n} leg{'' if n == 1 else 's'} still needs a briefing")
    if summary.pending_coverage_leg_ids:
        n = len(summary.pending_coverage_leg_ids)
        extras.append(f"{n} beyond coverage")
    if extras:
        parts.append(f"({'; '.join(extras)}.)")

    return " ".join(parts)


def summarize_trip(
    trip_id: str,
    legs: list[TripLegInput],
    *,
    name: str = "",
    now: datetime | None = None,
) -> TripSummary:
    """Build the deterministic trip picture from its member legs.

    ``legs`` may arrive in any order; the chain order is derived here from
    ``departure_time`` (which is why no position is stored).
    """
    now = now or datetime.now(timezone.utc)
    ordered = sorted(legs, key=lambda leg: leg.departure_time)

    built: list[TripLeg] = []
    previous_end: datetime | None = None
    for leg in ordered:
        origin, destination = _airport_ends(leg)
        gap = None
        same_sortie = False
        if previous_end is not None:
            gap = round((leg.departure_time - previous_end).total_seconds() / 3600.0, 2)
            same_sortie = gap < SORTIE_GAP_HOURS
        built.append(
            TripLeg(
                flight_id=leg.flight_id,
                label=_leg_label(leg),
                origin=origin,
                destination=destination,
                departure_time=leg.departure_time,
                duration_hours=leg.duration_hours,
                state=_leg_state(leg, now),
                grade_kind=_grade_kind(leg),
                assessment=leg.assessment,
                assessment_reason=leg.assessment_reason,
                outlook=leg.outlook,
                outlook_reason=leg.outlook_reason,
                days_out=leg.days_out,
                advisory_summary=leg.advisory_summary,
                fetch_timestamp=leg.fetch_timestamp,
                gap_hours_before=gap,
                same_sortie_as_previous=same_sortie,
            )
        )
        previous_end = leg.departure_time + timedelta(hours=leg.duration_hours or 0.0)

    summary = TripSummary(
        trip_id=trip_id,
        name=name,
        legs=built,
        total_legs=len(built),
        remaining_legs=sum(1 for leg in built if leg.state == "remaining"),
    )

    remaining = [leg for leg in built if leg.state == "remaining"]
    summary.beyond_horizon_leg_ids = [
        leg.flight_id for leg in remaining if leg.grade_kind == "outlook"
    ]
    summary.pending_coverage_leg_ids = [
        leg.flight_id for leg in remaining if leg.grade_kind == "pending_coverage"
    ]
    summary.needs_briefing_leg_ids = [
        leg.flight_id for leg in remaining if leg.grade_kind == "needs_briefing"
    ]
    summary.unavailable_leg_ids = [
        leg.flight_id for leg in remaining if leg.grade_kind == "unavailable"
    ]

    binding_id, basis, chain_status = _pick_binding_leg(built)
    summary.binding_leg_id = binding_id
    summary.binding_basis = basis
    summary.chain_status = chain_status

    binding = next((leg for leg in built if leg.flight_id == binding_id), None)
    if binding is not None:
        summary.decision_ripeness_days = binding.days_out
        if basis == "outlook":
            # Only a beyond-horizon leg has a "not yet decidable" date; a leg
            # already inside the GRIB horizon is decidable now.
            summary.decidable_from = (
                binding.departure_time.date() - timedelta(days=_grib_horizon_days())
            )

    # Chain identity + continuity, both derived.
    labels: list[str] = []
    for index, leg in enumerate(built):
        if index == 0 and leg.origin:
            labels.append(leg.origin)
        if leg.destination:
            labels.append(leg.destination)
        if index > 0:
            prev = built[index - 1]
            if prev.destination and leg.origin and prev.destination != leg.origin:
                summary.continuity_warnings.append(
                    ContinuityWarning(
                        after_flight_id=prev.flight_id,
                        before_flight_id=leg.flight_id,
                        arrives=prev.destination,
                        departs=leg.origin,
                    )
                )
    summary.chain_label = " → ".join(labels)
    summary.is_round_trip = bool(
        built and built[0].origin and built[-1].destination
        and built[0].origin == built[-1].destination
        and len(built) > 1
    )

    summary.headline = _build_headline(summary, binding)
    return summary
