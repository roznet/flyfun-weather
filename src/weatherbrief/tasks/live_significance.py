"""Significance classifier for the per-flight live layer (#637).

Replaces the worsening-only ``compute_refresh_delta`` (which diffed each
refresh against the previous one). Three things differ, all deliberately:

- **Baseline is the briefing**, not the previous refresh: "what changed since
  the assessment was written" is what a pilot can act on, and it does not reset
  every ten minutes the way a refresh-to-refresh diff does.
- **Both directions.** Fog lifting at the destination matters as much as fog
  forming.
- **Hysteresis lives here, and only here.** The display layer always shows the
  newest METAR. A METAR flight-category crossing becomes a *change* only once
  two consecutive reports agree on the side of the baseline it moved to, or the
  newest report is a SPECI (issued *because* conditions crossed a threshold).
  A confirmed change is then held until the return is itself confirmed, so one
  odd report neither raises nor clears it.

Two tiers: ``alert`` for the departure, destination and the top alternates
(the tier push delivery, #638, consumes), ``highlight`` for everything else on
the route. A last-alerted memory makes an alert fire once per value.

Deterministic and language-neutral (ICAO codes, flight categories, FIR/SIGMET
ids, NM): no LLM per tick, no per-locale strings. The live layer annotates; it
never re-grades the briefing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from weatherbrief.models.live import ChangeRole, LiveChange, LiveChanges
from weatherbrief.models.observations import (
    AirportObservation,
    RefreshDelta,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.models.observed import (
    MIN_COVERAGE_FRACTION,
    ObservedConditions,
)

# VFR < MVFR < IFR < LIFR — higher rank is worse.
_CATEGORY_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
_SEQ_RE = re.compile(r"SIGMET\s+(\w+)", re.IGNORECASE)

#: How many of the ranked divert candidates count as "the alternates" for the
#: alert tier. The briefing ranks candidates closest-first; beyond the first
#: few the pilot is not realistically planning on them, and alerting on every
#: candidate would make the alert tier as noisy as the highlight tier.
ALERT_ALTERNATES = 3

#: Echo class at or above which an observed radar return on the route ahead is
#: significant: VIP 3 "heavy" (41 dBZ), the AIM "avoid level 3 or greater" line.
RADAR_SIGNIFICANT_DBZ = 41.0


@dataclass
class ClassifierMemory:
    """State carried between ticks (persisted on the live layer)."""

    # Change key -> to_value last alerted (alert tier only).
    alerted: dict[str, str] = field(default_factory=dict)
    # METAR change key -> last *confirmed* category, so a pending report holds
    # the change rather than flickering it off.
    held: dict[str, str] = field(default_factory=dict)


def category_rank(cat: str | None) -> int | None:
    if not cat:
        return None
    return _CATEGORY_RANK.get(cat.upper())


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


# --- SIGMET identity (shared with the pre-#637 delta) ----------------------


def _is_severe(s: SigmetAlongRoute) -> bool:
    return bool(s.qualifier) and s.qualifier.upper() == "SEV"


def _sigmet_seq(s: SigmetAlongRoute) -> str | None:
    """Sequence id (e.g. ``13`` from "LTBB SIGMET 13"), parsed from raw text."""
    m = _SEQ_RE.search(s.raw_text or "")
    return m.group(1) if m else None


def sigmet_key(s: SigmetAlongRoute) -> tuple:
    """Stable identity across fetches: prefer FIR + sequence, else fall back
    to FIR + hazard + validity so a re-issued SIGMET isn't mistaken for new."""
    seq = _sigmet_seq(s)
    if seq is not None:
        return (s.fir_id, seq)
    return (s.fir_id, s.hazard, s.valid_from)


def _sigmet_label(s: SigmetAlongRoute) -> str:
    seq = _sigmet_seq(s)
    hazard = " ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET"
    fir = f"{s.fir_id} {seq}" if seq else s.fir_id
    return f"{fir}: {hazard}"


def _sigmet_key_str(s: SigmetAlongRoute) -> str:
    return "sigmet:" + "|".join("" if p is None else str(p) for p in sigmet_key(s))


# --- Roles ------------------------------------------------------------------


def airport_roles(
    route_icaos: list[str],
    alternate_icaos: list[str] | None = None,
) -> dict[str, ChangeRole]:
    """ICAO -> role for the alert tier: departure, destination, alternates.

    Departure/destination win over alternate (a round trip's destination is
    also its departure — it is still that, not an alternate).
    """
    roles: dict[str, ChangeRole] = {}
    for icao in (alternate_icaos or [])[:ALERT_ALTERNATES]:
        if icao:
            roles[icao.upper()] = "alternate"
    if route_icaos:
        roles[route_icaos[-1].upper()] = "destination"
        roles[route_icaos[0].upper()] = "departure"
    return roles


def _tier(role: ChangeRole) -> str:
    return "highlight" if role == "route" else "alert"


# --- METAR ------------------------------------------------------------------


def _metar_state(
    base: AirportObservation, latest: AirportObservation,
) -> tuple[str, int]:
    """Classify the newest report against the baseline category.

    Returns ``(status, side)`` where status is ``changed`` (crossing
    confirmed), ``returned`` (back at the baseline category, confirmed),
    ``pending`` (one report disagrees with the one before it) or ``none``
    (no usable categories). ``side`` is +1 worse / -1 better / 0.
    """
    b = category_rank(base.metar_flight_category)
    l_ = category_rank(latest.metar_flight_category)
    if b is None or l_ is None:
        return "none", 0
    side = _sign(l_ - b)
    is_speci = (latest.metar_report_type or "").upper() == "SPECI"
    if base.metar_time is not None and latest.metar_time is not None and latest.metar_time <= base.metar_time:
        # Still the report the briefing saw (or older): nothing has moved.
        return ("returned" if side == 0 else "none"), side
    p = category_rank(latest.metar_previous_flight_category)
    agrees = p is not None and _sign(p - b) == side
    # The report before the latest *is* the briefing's own report: the
    # baseline is the only prior opinion, so a crossing needs a SPECI.
    if (
        base.metar_time is not None
        and latest.metar_previous_time is not None
        and latest.metar_previous_time <= base.metar_time
    ):
        agrees = side == 0
    if is_speci or agrees:
        return ("returned" if side == 0 else "changed"), side
    return "pending", side


def _metar_changes(
    baseline: RouteObservations,
    latest: RouteObservations,
    roles: dict[str, ChangeRole],
    memory: ClassifierMemory,
    new_held: dict[str, str],
) -> list[LiveChange]:
    out: list[LiveChange] = []
    base_by_icao = {a.icao: a for a in baseline.airports}
    for a in latest.airports:
        base = base_by_icao.get(a.icao)
        if base is None:
            continue
        key = f"metar:{a.icao}"
        status, side = _metar_state(base, a)
        to_cat: str | None = None
        if status == "changed":
            to_cat = a.metar_flight_category
        elif status in ("pending", "none") and key in memory.held:
            # Hold the last confirmed crossing until the return is confirmed
            # (a missing report this tick is not a return either).
            to_cat = memory.held[key]
            side = _sign((category_rank(to_cat) or 0) - (category_rank(base.metar_flight_category) or 0))
        if to_cat is None or side == 0:
            continue
        new_held[key] = to_cat
        role = roles.get(a.icao.upper(), "route")
        source = "SPECI" if (a.metar_report_type or "").upper() == "SPECI" and status == "changed" else "METAR"
        suffix = " (SPECI)" if source == "SPECI" else ""
        out.append(LiveChange(
            key=key,
            kind="metar_category",
            source=source,
            direction="worse" if side > 0 else "better",
            tier=_tier(role),
            role=role,
            icao=a.icao,
            from_value=base.metar_flight_category,
            to_value=to_cat,
            observed_at=a.metar_time,
            enroute_distance_nm=a.enroute_distance_nm,
            message=f"{a.icao} METAR: {base.metar_flight_category} → {to_cat}{suffix}",
        ))
    return out


# --- TAF --------------------------------------------------------------------


def _taf_changes(
    baseline: RouteObservations,
    latest: RouteObservations,
    roles: dict[str, ChangeRole],
) -> list[LiveChange]:
    """TAF-at-ETA category moved (an amendment or a new TAF).

    No hysteresis: a TAF is a deliberate issuance, not a noisy sample. Both
    sides must have a reading valid at ETA — "no TAF covers ETA" is not a
    category, so a TAF appearing or lapsing is not reported as a crossing.
    """
    out: list[LiveChange] = []
    base_by_icao = {a.icao: a for a in baseline.airports}
    for a in latest.airports:
        base = base_by_icao.get(a.icao)
        if base is None:
            continue
        b = category_rank(base.taf_flight_category_at_eta)
        l_ = category_rank(a.taf_flight_category_at_eta)
        if b is None or l_ is None or b == l_:
            continue
        role = roles.get(a.icao.upper(), "route")
        out.append(LiveChange(
            key=f"taf:{a.icao}",
            kind="taf_category",
            source="TAF",
            direction="worse" if l_ > b else "better",
            tier=_tier(role),
            role=role,
            icao=a.icao,
            from_value=base.taf_flight_category_at_eta,
            to_value=a.taf_flight_category_at_eta,
            observed_at=a.taf_issue_time,
            enroute_distance_nm=a.enroute_distance_nm,
            message=(
                f"{a.icao} TAF at ETA: {base.taf_flight_category_at_eta}"
                f" → {a.taf_flight_category_at_eta}"
            ),
        ))
    return out


# --- SIGMET -----------------------------------------------------------------


def _sigmet_changes(
    baseline: RouteSigmets, latest: RouteSigmets,
) -> list[LiveChange]:
    out: list[LiveChange] = []
    base_by_key = {sigmet_key(s): s for s in baseline.sigmets}
    latest_keys = set()
    for s in latest.sigmets:
        k = sigmet_key(s)
        latest_keys.add(k)
        prev = base_by_key.get(k)
        if prev is None:
            prefix = "New SEV SIGMET" if _is_severe(s) else "New SIGMET"
            out.append(LiveChange(
                key=_sigmet_key_str(s),
                kind="sigmet_issued",
                source="SIGMET",
                direction="worse",
                tier="highlight",
                role="route",
                from_value=None,
                to_value=" ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET",
                observed_at=s.valid_from,
                enroute_distance_nm=s.enroute_distance_from_nm,
                message=f"{prefix} {_sigmet_label(s)}",
            ))
        elif _is_severe(s) and not _is_severe(prev):
            out.append(LiveChange(
                key=_sigmet_key_str(s),
                kind="sigmet_issued",
                source="SIGMET",
                direction="worse",
                tier="highlight",
                role="route",
                from_value=" ".join(p for p in (prev.qualifier, prev.hazard) if p) or None,
                to_value=" ".join(p for p in (s.qualifier, s.hazard) if p) or "SEV",
                observed_at=s.valid_from,
                enroute_distance_nm=s.enroute_distance_from_nm,
                message=f"SIGMET {_sigmet_label(s)} escalated to SEV",
            ))
    for k, s in base_by_key.items():
        if k in latest_keys:
            continue
        out.append(LiveChange(
            key=_sigmet_key_str(s),
            kind="sigmet_cancelled",
            source="SIGMET",
            direction="better",
            tier="highlight",
            role="route",
            from_value=" ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET",
            to_value=None,
            observed_at=s.valid_to,
            enroute_distance_nm=s.enroute_distance_from_nm,
            message=f"SIGMET {_sigmet_label(s)} no longer active",
        ))
    return out


# --- Observed radar / lightning ---------------------------------------------


def _station_positions(observed: ObservedConditions) -> dict[str, float | None]:
    return {st.id: st.enroute_distance_nm for st in observed.stations}


def _ahead(dist: float | None, flown_nm: float | None) -> bool:
    # A station with no along-track position is kept: better a spurious
    # highlight than a hidden echo.
    return flown_nm is None or dist is None or dist >= flown_nm


def _lightning_span(
    observed: ObservedConditions | None, flown_nm: float | None,
) -> tuple[list[float], float | None, datetime | None] | None:
    """Along-track positions of route points ahead with flashes in the
    innermost ring. None when the lightning field is absent (unknown, not
    clear)."""
    if observed is None or observed.lightning is None:
        return None
    pos = _station_positions(observed)
    hits: list[float] = []
    radius: float | None = None
    for st in observed.lightning.stations:
        if not st.annuli:
            continue
        inner = min(st.annuli, key=lambda a: a.radius_nm)
        radius = inner.radius_nm if radius is None else min(radius, inner.radius_nm)
        d = pos.get(st.station_id)
        if inner.flash_count > 0 and _ahead(d, flown_nm):
            hits.append(d if d is not None else -1.0)
    return hits, radius, observed.lightning.valid_time


def _radar_span(
    observed: ObservedConditions | None, flown_nm: float | None,
) -> tuple[list[float], float | None, float | None, datetime | None] | None:
    """Along-track positions of route points ahead whose innermost ring has a
    heavy-or-worse echo on adequate coverage. None when the field is absent."""
    if observed is None or observed.reflectivity is None:
        return None
    pos = _station_positions(observed)
    hits: list[float] = []
    radius: float | None = None
    peak: float | None = None
    for st in observed.reflectivity.stations:
        if not st.annuli:
            continue
        inner = min(st.annuli, key=lambda a: a.radius_nm)
        radius = inner.radius_nm if radius is None else min(radius, inner.radius_nm)
        if inner.total_px <= 0 or inner.valid_px / inner.total_px < MIN_COVERAGE_FRACTION:
            continue  # cannot see there: never read as clear, never as an echo
        d = pos.get(st.station_id)
        if inner.max_value is not None and inner.max_value >= RADAR_SIGNIFICANT_DBZ and _ahead(d, flown_nm):
            hits.append(d if d is not None else -1.0)
            peak = inner.max_value if peak is None else max(peak, inner.max_value)
    return hits, radius, peak, observed.reflectivity.valid_time


def _span_text(hits: list[float]) -> str:
    known = [h for h in hits if h >= 0]
    if not known:
        return ""
    lo, hi = min(known), max(known)
    if round(lo) == round(hi):
        return f" at {lo:.0f} NM along route"
    return f" {lo:.0f}–{hi:.0f} NM along route"


def _observed_changes(
    baseline: ObservedConditions | None,
    latest: ObservedConditions | None,
    flown_nm: float | None,
) -> list[LiveChange]:
    out: list[LiveChange] = []

    b_l = _lightning_span(baseline, flown_nm)
    l_l = _lightning_span(latest, flown_nm)
    if b_l is not None and l_l is not None:
        b_hits, _, _ = b_l
        l_hits, radius, valid = l_l
        r = f"{radius:g} NM" if radius is not None else "the route"
        if l_hits and not b_hits:
            out.append(LiveChange(
                key="lightning:route", kind="lightning", source="LIGHTNING",
                direction="worse", tier="highlight", role="route",
                from_value="none", to_value=str(len(l_hits)), observed_at=valid,
                enroute_distance_nm=min((h for h in l_hits if h >= 0), default=None),
                message=f"Lightning within {r} of route{_span_text(l_hits)}",
            ))
        elif b_hits and not l_hits:
            out.append(LiveChange(
                key="lightning:route", kind="lightning", source="LIGHTNING",
                direction="better", tier="highlight", role="route",
                from_value=str(len(b_hits)), to_value="none", observed_at=valid,
                message=f"No lightning within {r} of route ahead",
            ))

    b_r = _radar_span(baseline, flown_nm)
    l_r = _radar_span(latest, flown_nm)
    if b_r is not None and l_r is not None:
        b_hits = b_r[0]
        l_hits, radius, peak, valid = l_r
        r = f"{radius:g} NM" if radius is not None else "the route"
        if l_hits and not b_hits:
            out.append(LiveChange(
                key="radar:route", kind="radar", source="RADAR",
                direction="worse", tier="highlight", role="route",
                from_value="none", to_value=f"{peak:.0f} dBZ" if peak is not None else None,
                observed_at=valid,
                enroute_distance_nm=min((h for h in l_hits if h >= 0), default=None),
                message=(
                    f"Heavy radar echo (peak {peak:.0f} dBZ) within {r} of route"
                    f"{_span_text(l_hits)}"
                ),
            ))
        elif b_hits and not l_hits:
            out.append(LiveChange(
                key="radar:route", kind="radar", source="RADAR",
                direction="better", tier="highlight", role="route",
                from_value="heavy", to_value="none", observed_at=valid,
                message=f"No heavy radar echo within {r} of route ahead",
            ))
    return out


# --- Entry point ------------------------------------------------------------


_ROLE_ORDER = {"departure": 0, "destination": 1, "alternate": 2, "route": 3}


def classify_changes(
    *,
    baseline_obs: RouteObservations | None,
    latest_obs: RouteObservations | None,
    baseline_sigmets: RouteSigmets | None,
    latest_sigmets: RouteSigmets | None,
    baseline_observed: ObservedConditions | None = None,
    latest_observed: ObservedConditions | None = None,
    roles: dict[str, ChangeRole] | None = None,
    baseline_at: datetime | None = None,
    flown_nm: float | None = None,
    memory: ClassifierMemory | None = None,
    now: datetime | None = None,
) -> tuple[LiveChanges, ClassifierMemory]:
    """Everything significant since the briefing, plus the updated memory.

    A ``None`` on either side of a dimension skips it: no baseline means
    nothing to compare against (a pre-SIGMET pack), no latest means the fetch
    failed — neither is a change. ``flown_nm`` (distance already flown at
    ``now``) limits radar/lightning to the route still ahead.
    """
    roles = roles or {}
    memory = memory or ClassifierMemory()
    new_held: dict[str, str] = {}
    changes: list[LiveChange] = []

    # Key prefixes whose dimension was actually evaluated this tick. Memory for
    # a dimension that was skipped (fetch failed) is kept, so a failed tick
    # neither clears an alert nor lets it fire twice.
    evaluated: set[str] = set()
    if baseline_obs is not None and latest_obs is not None:
        changes += _metar_changes(baseline_obs, latest_obs, roles, memory, new_held)
        changes += _taf_changes(baseline_obs, latest_obs, roles)
        evaluated |= {"metar:", "taf:"}
    else:
        new_held = dict(memory.held)
    if baseline_sigmets is not None and latest_sigmets is not None:
        changes += _sigmet_changes(baseline_sigmets, latest_sigmets)
        evaluated.add("sigmet:")
    changes += _observed_changes(baseline_observed, latest_observed, flown_nm)
    evaluated |= {"lightning:", "radar:"}

    changes.sort(key=lambda c: (
        0 if c.tier == "alert" else 1,
        _ROLE_ORDER.get(c.role, 9),
        0 if c.direction == "worse" else 1,
        c.enroute_distance_nm if c.enroute_distance_nm is not None else float("inf"),
        c.key,
    ))

    # Alert once per value; forget keys that are no longer changed so a
    # recurrence alerts afresh.
    alerted = dict(memory.alerted)
    live_alert_keys: set[str] = set()
    for c in changes:
        if c.tier != "alert":
            continue
        live_alert_keys.add(c.key)
        value = c.to_value or ""
        if alerted.get(c.key) != value:
            c.new_alert = True
            alerted[c.key] = value
    for k in list(alerted):
        if k not in live_alert_keys and any(k.startswith(p) for p in evaluated):
            del alerted[k]

    result = LiveChanges(
        baseline_at=baseline_at,
        computed_at=now or datetime.now(timezone.utc),
        changes=changes,
    )
    return result, ClassifierMemory(alerted=alerted, held=new_held)


def worsening_delta(changes: LiveChanges) -> RefreshDelta:
    """The worsening half of ``changes`` in the pre-#637 ``RefreshDelta``
    shape, for clients that still read ``last_refresh_delta``."""
    messages = [c.message for c in changes.changes if c.direction == "worse"]
    return RefreshDelta(
        worsened=bool(messages),
        messages=messages,
        computed_at=changes.computed_at,
    )
