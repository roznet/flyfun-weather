"""Significance classifier for the per-flight live layer (#637).

Replaces the worsening-only ``compute_refresh_delta`` (which diffed each
refresh against the previous one). Three things differ, all deliberately:

- **Baseline is the briefing**, not the previous refresh: "what changed since
  the assessment was written" is what a pilot can act on, and it does not reset
  every ten minutes the way a refresh-to-refresh diff does.
- **Both directions.** Fog lifting at the destination matters as much as fog
  forming.
- **No hysteresis.** A METAR flight-category crossing is a change on the first
  report that shows it (meteorology-decisions §35 dropped the two-report rule
  of §34): a pilot would rather see a real deterioration at once than 30 min
  late. A tick without a usable report neither raises nor clears a change.

What counts at an airport goes beyond the flight category (§36): thunderstorm
or CB/TCU appearing, significant weather (FZRA, hail, squall, heavy showers…),
and the wind advisory (runway crosswind / gust, the airport wind advisory's own
thresholds). Which of those are reported, and at which tier, depends on the
airport's role — see :data:`AIRPORT_POLICY` — and a change is only reported
while it can still matter: the departure until take-off, an en-route airport
until it is passed, the destination and alternates until arrival.

Two tiers: ``alert`` (the tier push delivery, #638, consumes) and
``highlight``. Every SIGMET appearing on the route alerts; improvements never
alert. A last-alerted memory makes an alert fire
once per value.

Deterministic and language-neutral (ICAO codes, flight categories, FIR/SIGMET
ids, NM): no LLM per tick, no per-locale strings. The live layer annotates; it
never re-grades the briefing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from euro_aip.utils.geometry import (
    bbox_intersects,
    bbox_of_ring,
    bbox_pad,
    min_distance_point_to_multipolygon_nm,
    point_in_multipolygon,
)

from weatherbrief.models.live import (
    ChangeRole,
    LiveChange,
    LiveChanges,
    LiveEvidencePoint,
    LiveSigmetTrace,
)
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

logger = logging.getLogger(__name__)

#: How many of the ranked divert candidates are labelled "alternate". The
#: briefing ranks candidates closest-first; beyond the first few the pilot is
#: not realistically planning on them.
ALERT_ALTERNATES = 3

#: A SIGMET whose area contains the destination or comes within this distance
#: of it is labelled a destination impact: roughly the terminal area an
#: arrival, a hold or a go-around flies through. (Every SIGMET change in the
#: corridor alerts; this only sets its role.)
DESTINATION_SIGMET_RADIUS_NM = 25.0

#: Two FIRs issue the same phenomenon at their shared boundary as separate
#: SIGMETs (LECB 3 + LECM 3). Same hazard and qualifier, validity starting
#: within this window, and areas within ``_SAME_PHENOMENON_NM`` of each other
#: read as one change.
_SAME_PHENOMENON_START = 30 * 60
_SAME_PHENOMENON_NM = 10.0

#: A FIR reissues a SIGMET for a continuing phenomenon every 1–3 h under a new
#: sequence number (LFMM T01 → T02 → T03, LECB 2 → 3 → 4 → 5). A new SIGMET
#: replaces an earlier one of the same FIR, hazard and qualifier when its
#: validity starts around the predecessor's end (from
#: ``SIGMET_REISSUE_EARLY`` before it to ``SIGMET_REISSUE_WINDOW`` after, and
#: not before the predecessor's own start), the predecessor was seen on this
#: flight (or is in the baseline), and the areas come within ``_REISSUE_NM``
#: of each other (#682). Observed starts: 0 min (T01 → T02,
#: LECB 2 → 3 → 4 → 5), +15 min (T02 → T03). The early bound keeps a second
#: cell issued while the first is still valid apart: LECB 3 on 2026-10-02
#: started 25 min before LECB 2 ended, next to it, and is not its reissue
#: (LECB 4, from LECB 2's end over its area, is).
SIGMET_REISSUE_WINDOW = timedelta(minutes=60)
SIGMET_REISSUE_EARLY = timedelta(minutes=15)
_REISSUE_NM = 20.0

#: A SIGMET that only starts after the flight has landed cannot affect it,
#: but the plan's arrival time is an estimate: a hold, a go-around or a
#: diversion to a nearby alternate all land later. A pending SIGMET starting
#: more than this after the planned arrival (departure +
#: ``flight_duration_hours``) is a highlight, not an alert (#689); one
#: starting within it alerts as any new SIGMET. 30 min covers a hold plus a
#: short divert; a late departure is not covered (the plan's departure time
#: is all the layer knows). The #689 case (arrival 09:10Z, SIGMET from
#: 10:00Z) needs it under 50 min.
SIGMET_AFTER_ARRIVAL_MARGIN = timedelta(minutes=30)

#: Echo class at or above which an observed radar return on the route ahead is
#: significant: VIP 3 "heavy" (41 dBZ), the AIM "avoid level 3 or greater" line.
RADAR_SIGNIFICANT_DBZ = 41.0


@dataclass
class ClassifierMemory:
    """State carried between ticks (persisted on the live layer)."""

    # Change key -> to_value last alerted (alert tier only).
    alerted: dict[str, str] = field(default_factory=dict)
    # SIGMET key -> what was seen of it, for reissue matching (#682).
    sigmets: dict[str, LiveSigmetTrace] = field(default_factory=dict)


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
    hazard = " ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET"
    return f"{_sigmet_name(s)}: {hazard}"


def _sigmet_name(s: SigmetAlongRoute) -> str:
    """"LFMM T02", or the FIR alone when the text carries no sequence."""
    seq = _sigmet_seq(s)
    return f"{s.fir_id} {seq}" if seq else s.fir_id


def _sigmet_key_str(s: SigmetAlongRoute) -> str:
    return "sigmet:" + "|".join("" if p is None else str(p) for p in sigmet_key(s))


# --- Roles ------------------------------------------------------------------


def airport_roles(
    route_icaos: list[str],
    alternate_icaos: list[str] | None = None,
) -> dict[str, ChangeRole]:
    """ICAO -> role: departure, destination, the top alternates.

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


# --- Airport policy ---------------------------------------------------------

#: What is reported at an airport, by role, kind and direction: the tier, or
#: None (not reported). "terminal" = the destination, and the departure until
#: take-off. Improvements never alert.
#:
#: - Category: a terminal change either way; at an en-route airport only a
#:   move into or out of IFR/LIFR (see :func:`_category_matters`).
#: - Convective (TS/VCTS/CB/TCU) and significant weather: alert at a terminal
#:   and at an en-route airport still ahead — that is weather the route flies
#:   through; highlight at an alternate. Exception (§39): en route, CB / TCU
#:   alone is a highlight — the radar along the route is the better signal for
#:   that cell; a thunderstorm (TS / VCTS) still alerts (:func:`airport_tier`).
#: - Wind (the airport wind advisory, green/amber/red): terminal alert,
#:   alternate highlight, not reported en route (nobody lands there).
AIRPORT_POLICY: dict[str, dict[str, dict[str, str | None]]] = {
    "terminal": {
        "metar_category": {"worse": "alert", "better": "highlight"},
        "taf_category": {"worse": "alert", "better": "highlight"},
        "metar_convective": {"worse": "alert", "better": "highlight"},
        "metar_weather": {"worse": "alert", "better": "highlight"},
        "metar_wind": {"worse": "alert", "better": "highlight"},
    },
    "alternate": {
        "metar_category": {"worse": "highlight", "better": "highlight"},
        "taf_category": {"worse": "highlight", "better": "highlight"},
        "metar_convective": {"worse": "highlight", "better": "highlight"},
        "metar_weather": {"worse": "highlight", "better": "highlight"},
        "metar_wind": {"worse": "highlight", "better": "highlight"},
    },
    "route": {
        "metar_category": {"worse": "highlight", "better": "highlight"},
        "taf_category": {"worse": "highlight", "better": "highlight"},
        # CB / TCU only; a thunderstorm still alerts (see airport_tier, §39).
        "metar_convective": {"worse": "highlight", "better": "highlight"},
        "metar_weather": {"worse": "alert", "better": "highlight"},
        "metar_wind": {"worse": None, "better": None},
    },
}


def _policy_role(role: ChangeRole) -> str:
    return "terminal" if role in ("destination", "departure") else role


def airport_tier(
    role: ChangeRole, kind: str, direction: str, to_value: str | None = None,
) -> str | None:
    """The tier a change of ``kind`` gets at an airport of ``role``, or None.

    En route, CB / TCU reported at a station is a highlight: the radar along
    the route says more about that cell than the station's cloud type
    (§39). A thunderstorm (``TS`` / ``VCTS``, level "TS") still alerts.
    """
    if (
        _policy_role(role) == "route" and kind == "metar_convective"
        and direction == "worse" and to_value == _CONVECTIVE_LABEL[_CONVECTIVE_RANK["TS"]]
    ):
        return "alert"
    return AIRPORT_POLICY[_policy_role(role)].get(kind, {}).get(direction)


def airport_relevant(
    role: ChangeRole,
    enroute_distance_nm: float | None,
    *,
    departed: bool,
    flown_nm: float | None,
) -> bool:
    """Whether a change at this airport can still matter to the flight.

    The departure until take-off; an en-route airport until it is passed (an
    airport with no along-track position is kept — better a spurious row than
    a hidden one); the destination and alternates until arrival (the live
    window itself ends an hour after arrival).
    """
    if role == "departure":
        return not departed
    if role == "route":
        return _ahead(enroute_distance_nm, flown_nm)
    return True


# --- METAR ------------------------------------------------------------------

#: Convective evidence, worst last: towering cumulus, cumulonimbus, thunder
#: (present ``TS`` in any group, or ``VCTS`` in the vicinity).
_CONVECTIVE_RANK = {"TCU": 1, "CB": 2, "VCTS": 3, "TS": 3}
_CONVECTIVE_LABEL = {0: "none", 1: "TCU", 2: "CB", 3: "TS"}
#: A cloud group carrying a type: ``FEW022CB``, ``BKN///TCU``, and the AUTO
#: stations' ``///CB`` / ``//////TCU`` (type detected, amount and height not).
_CLOUD_TYPE_RE = re.compile(
    r"(?<!\S)(?:(?:FEW|SCT|BKN|OVC|VV|///)(?:\d{3}|///)|///)(CB|TCU)(?!\S)"
)
#: Where a METAR's observed part ends: the trend forecast (TEMPO, BECMG,
#: NOSIG, PROB30/40) or the remarks. A ``TEMPO FEW045CB`` is a forecast.
_METAR_BODY_END_RE = re.compile(r"\s(?:TEMPO|BECMG|NOSIG|PROB\d{2}|RMK)(?!\S)")
#: Present-weather phenomena significant on their own, at any intensity.
_SIGNIFICANT_WX = ("FZRA", "FZDZ", "GR", "SQ", "FC")
_WIND_RANK = {"green": 0, "amber": 1, "red": 2}


def _present_weather(obs: AirportObservation) -> list[str]:
    """Present-weather groups, upper-cased; recent weather (``RE…``) excluded."""
    return [c.upper() for c in obs.metar_weather if c and not c.upper().startswith("RE")]


def metar_observed_part(raw: str) -> str:
    """The raw METAR up to its trend forecast or remarks (#682)."""
    m = _METAR_BODY_END_RE.search(raw)
    return raw[: m.start()] if m else raw


def convective_tags(obs: AirportObservation) -> set[str]:
    """``TS`` / ``VCTS`` from present weather, ``CB`` / ``TCU`` from the cloud
    groups (read off the raw report, so packs written before the field
    existed compare the same way). Only what was observed: the trend forecast
    and remarks are cut off first (#682)."""
    tags: set[str] = set()
    for c in _present_weather(obs):
        if "TS" in c:
            tags.add("VCTS" if c.startswith("VC") else "TS")
    tags.update(_CLOUD_TYPE_RE.findall(metar_observed_part(obs.metar_raw or "")))
    return tags


def _convective_level(tags: set[str]) -> int:
    return max((_CONVECTIVE_RANK[t] for t in tags), default=0)


def significant_weather(obs: AirportObservation) -> set[str]:
    """Significant present weather: freezing precipitation, hail, squall,
    funnel cloud, heavy showers (``+SH…``) and heavy snow (``+SN``, ``+RASN``…).
    A thunderstorm (``+TS…``) is reported once, as convective
    (:func:`convective_tags`), not twice."""
    out: set[str] = set()
    for c in _present_weather(obs):
        for code in _SIGNIFICANT_WX:
            if code in c:
                out.add(code)
        if c.startswith("+") and ("SH" in c or "SN" in c) and "TS" not in c:
            out.add(c)
    return out


def _has_report(obs: AirportObservation) -> bool:
    return bool(obs.metar_raw) or obs.metar_flight_category is not None


def _newer(base: AirportObservation, latest: AirportObservation) -> bool:
    """The newest report is not still the one the briefing saw."""
    return not (
        base.metar_time is not None
        and latest.metar_time is not None
        and latest.metar_time <= base.metar_time
    )


def _category_matters(role: ChangeRole, b: int, l_: int) -> bool:
    if _policy_role(role) != "route":
        return True
    ifr = _CATEGORY_RANK["IFR"]
    return b >= ifr or l_ >= ifr


def _wind_detail(a: AirportObservation) -> str:
    parts = []
    if a.metar_crosswind_kt is not None:
        rwy = f" RWY {a.metar_best_runway_id}" if a.metar_best_runway_id else ""
        parts.append(f"crosswind {a.metar_crosswind_kt:.0f} kt{rwy}")
    if a.metar_wind_gust_kt is not None:
        parts.append(f"gust {a.metar_wind_gust_kt} kt")
    return f" ({', '.join(parts)})" if parts else ""


def _airport_metar_changes(
    base: AirportObservation,
    a: AirportObservation,
    role: ChangeRole,
) -> tuple[list[tuple[str, str, str | None, str | None, str]], set[str]]:
    """(kind, direction, from, to, message) candidates for one airport, plus the
    change keys whose state is unknown this tick (no usable report)."""
    icao = a.icao
    if not _has_report(a):
        return [], {f"{p}:{icao}" for p in ("metar", "conv", "wx", "wind")}
    if not _newer(base, a):
        return [], set()
    out: list[tuple[str, str, str | None, str | None, str]] = []
    unknown: set[str] = set()
    is_speci = (a.metar_report_type or "").upper() == "SPECI"
    speci = " (SPECI)" if is_speci else ""

    # Flight category
    b, l_ = category_rank(base.metar_flight_category), category_rank(a.metar_flight_category)
    if b is None or l_ is None:
        unknown.add(f"metar:{icao}")
    elif l_ != b and _category_matters(role, b, l_):
        out.append((
            "metar_category", "worse" if l_ > b else "better",
            base.metar_flight_category, a.metar_flight_category,
            f"{icao} METAR: {base.metar_flight_category} → {a.metar_flight_category}{speci}",
        ))

    # Convective: TS / VCTS / CB / TCU appearing (or a step up), or clearing
    bt, lt = convective_tags(base), convective_tags(a)
    bl, ll = _convective_level(bt), _convective_level(lt)
    if ll > bl:
        out.append((
            "metar_convective", "worse", _CONVECTIVE_LABEL[bl], _CONVECTIVE_LABEL[ll],
            f"{icao} METAR: {', '.join(sorted(lt))} reported{speci}",
        ))
    elif ll < bl:
        now = f"now {', '.join(sorted(lt))}" if lt else "no longer reported"
        out.append((
            "metar_convective", "better", _CONVECTIVE_LABEL[bl], _CONVECTIVE_LABEL[ll],
            f"{icao} METAR: {', '.join(sorted(bt))} {now}{speci}",
        ))

    # Significant weather
    bw, lw = significant_weather(base), significant_weather(a)
    if lw - bw:
        new = ", ".join(sorted(lw - bw))
        out.append(("metar_weather", "worse", ", ".join(sorted(bw)) or None, new,
                    f"{icao} METAR: {new} reported{speci}"))
    elif bw and not lw:
        gone = ", ".join(sorted(bw))
        out.append(("metar_weather", "better", gone, None,
                    f"{icao} METAR: {gone} no longer reported{speci}"))

    # Wind: the airport wind advisory (crosswind on the best runway, gust)
    bwr, lwr = _WIND_RANK.get(base.metar_wind_advisory or ""), _WIND_RANK.get(a.metar_wind_advisory or "")
    if bwr is None or lwr is None:
        # No advisory on one side (an older pack, or no runway data for the
        # airport): wind is not evaluated for this airport — distinct from
        # "no change", so say so at debug level.
        logger.debug("Live wind not evaluated for %s: baseline=%s latest=%s",
                     icao, base.metar_wind_advisory, a.metar_wind_advisory)
        unknown.add(f"wind:{icao}")
    elif lwr != bwr:
        out.append((
            "metar_wind", "worse" if lwr > bwr else "better",
            base.metar_wind_advisory, a.metar_wind_advisory,
            f"{icao} wind: {base.metar_wind_advisory} → {a.metar_wind_advisory}{_wind_detail(a)}",
        ))
    return out, unknown


_KEY_PREFIX = {
    "metar_category": "metar",
    "metar_convective": "conv",
    "metar_weather": "wx",
    "metar_wind": "wind",
}


def _airport_changes(
    baseline: RouteObservations,
    latest: RouteObservations,
    roles: dict[str, ChangeRole],
    unknown: set[str],
    *,
    departed: bool,
    flown_nm: float | None,
) -> list[LiveChange]:
    """METAR and TAF-at-ETA changes at every airport still relevant, filtered
    and tiered by :data:`AIRPORT_POLICY`. No confirmation wait (§35)."""
    out: list[LiveChange] = []
    base_by_icao = {a.icao: a for a in baseline.airports}
    for a in latest.airports:
        base = base_by_icao.get(a.icao)
        if base is None:
            continue
        role = roles.get(a.icao.upper(), "route")
        if not airport_relevant(role, a.enroute_distance_nm, departed=departed, flown_nm=flown_nm):
            continue
        candidates, unk = _airport_metar_changes(base, a, role)
        unknown |= unk
        source = "SPECI" if (a.metar_report_type or "").upper() == "SPECI" else "METAR"
        for kind, direction, from_v, to_v, message in candidates:
            tier = airport_tier(role, kind, direction, to_v)
            if tier is None:
                continue
            out.append(LiveChange(
                key=f"{_KEY_PREFIX[kind]}:{a.icao}",
                kind=kind, source=source, direction=direction, tier=tier, role=role,
                icao=a.icao, from_value=from_v, to_value=to_v,
                observed_at=a.metar_time, enroute_distance_nm=a.enroute_distance_nm,
                message=message,
            ))

        # TAF at ETA: a deliberate issuance, not a noisy sample. Both sides
        # must have a reading valid at ETA — a TAF appearing or lapsing is not
        # a crossing.
        b, l_ = category_rank(base.taf_flight_category_at_eta), category_rank(a.taf_flight_category_at_eta)
        if b is None or l_ is None or b == l_:
            continue
        direction = "worse" if l_ > b else "better"
        tier = airport_tier(role, "taf_category", direction)
        if tier is None:
            continue
        out.append(LiveChange(
            key=f"taf:{a.icao}",
            kind="taf_category", source="TAF", direction=direction, tier=tier, role=role,
            icao=a.icao,
            from_value=base.taf_flight_category_at_eta, to_value=a.taf_flight_category_at_eta,
            observed_at=a.taf_issue_time, enroute_distance_nm=a.enroute_distance_nm,
            message=(
                f"{a.icao} TAF at ETA: {base.taf_flight_category_at_eta}"
                f" → {a.taf_flight_category_at_eta}"
            ),
        ))
    return out


# --- SIGMET -----------------------------------------------------------------


def _sigmet_rings(s: SigmetAlongRoute) -> list | None:
    return [[list(s.coords)]] if len(s.coords) >= 3 else None


def _near_point(s: SigmetAlongRoute, point: tuple[float, float] | None, radius_nm: float) -> bool:
    """The SIGMET area contains ``point`` (lat, lon) or comes within ``radius_nm``."""
    rings = _sigmet_rings(s)
    if point is None or rings is None:
        return False
    lat, lon = point
    return (
        point_in_multipolygon(lon, lat, rings)
        or min_distance_point_to_multipolygon_nm(lon, lat, rings) <= radius_nm
    )


def _same_phenomenon(a: SigmetAlongRoute, b: SigmetAlongRoute) -> bool:
    """Neighbouring FIRs' SIGMETs for one phenomenon (see _SAME_PHENOMENON_*)."""
    if a.fir_id == b.fir_id:
        return False
    if (a.hazard or "", a.qualifier or "") != (b.hazard or "", b.qualifier or ""):
        return False
    if a.valid_from is None or b.valid_from is None:
        return False
    if abs((a.valid_from - b.valid_from).total_seconds()) > _SAME_PHENOMENON_START:
        return False
    if len(a.coords) < 3 or len(b.coords) < 3:
        return False
    return bbox_intersects(
        bbox_pad(bbox_of_ring(a.coords), _SAME_PHENOMENON_NM), bbox_of_ring(b.coords),
    )


def _group_same_phenomenon(sigmets: list[SigmetAlongRoute]) -> list[list[SigmetAlongRoute]]:
    groups: list[list[SigmetAlongRoute]] = []
    for s in sigmets:
        for g in groups:
            if any(_same_phenomenon(s, m) for m in g):
                g.append(s)
                break
        else:
            groups.append([s])
    return groups


def _pending(s: SigmetAlongRoute, now: datetime) -> bool:
    """Issued but not yet valid (#683): the fetch looks ahead for these."""
    return s.valid_from is not None and s.valid_from > now


def _after_arrival(group: list[SigmetAlongRoute], arrival_at: datetime | None) -> bool:
    """Every SIGMET of the row starts after the planned arrival plus
    :data:`SIGMET_AFTER_ARRIVAL_MARGIN` (#689). False without an arrival
    time or a start (alert as before: the louder reading)."""
    if arrival_at is None or not group:
        return False
    limit = arrival_at + SIGMET_AFTER_ARRIVAL_MARGIN
    after = all(m.valid_from is not None and m.valid_from > limit for m in group)
    if after:
        # The plan's arrival is all the layer knows, so a slipped departure
        # would make this demotion wrong (§40). Debug: it repeats every tick;
        # the audit trail is the row's tier in live_history.jsonl.
        logger.debug(
            "Live SIGMET %s starts after planned arrival %s + margin: highlight, not alert",
            _group_label(group), f"{arrival_at:%H:%MZ}",
        )
    return after


def _from_suffix(group: list[SigmetAlongRoute], now: datetime) -> str:
    """" from 07:00Z" while every SIGMET of the row is still pending, else ""."""
    if not group or not all(_pending(m, now) for m in group):
        return ""
    return f" from {min(m.valid_from for m in group):%H:%MZ}"


def _hazard_text(s: SigmetAlongRoute, default: str = "SIGMET") -> str:
    return " ".join(p for p in (s.qualifier, s.hazard) if p) or default


def _group_label(group: list[SigmetAlongRoute]) -> str:
    """"LECB 3 / LECM 3: EMBD TS" — every issuing FIR, one hazard."""
    firs = " / ".join(
        f"{m.fir_id} {_sigmet_seq(m)}" if _sigmet_seq(m) else m.fir_id for m in group
    )
    return f"{firs}: {_hazard_text(group[0])}"


def _sigmet_change(
    group: list[SigmetAlongRoute],
    *,
    kind: str,
    direction: str,
    from_value: str | None,
    to_value: str | None,
    observed_at,
    message: str,
    destination: tuple[float, float] | None,
) -> LiveChange:
    at_dest = any(_near_point(m, destination, DESTINATION_SIGMET_RADIUS_NM) for m in group)
    froms = [m.enroute_distance_from_nm for m in group if m.enroute_distance_from_nm is not None]
    return LiveChange(
        key="+".join(sorted(_sigmet_key_str(m) for m in group)),
        kind=kind,
        source="SIGMET",
        direction=direction,
        # A SIGMET appearing or escalating on the route alerts; one ending is
        # good news (highlight). The role says where.
        tier="alert" if direction == "worse" else "highlight",
        role="destination" if at_dest else "route",
        from_value=from_value,
        to_value=to_value,
        observed_at=observed_at,
        enroute_distance_nm=min(froms) if froms else None,
        message=message + (" (at destination)" if at_dest else ""),
    )


def reissue_key(chain: str, own: str) -> str:
    """A reissue row's key: the chain's first SIGMET, "+", this SIGMET. The
    "+" joins SIGMET keys as for a merged cross-FIR row, so clients that split
    change keys on "+" (iOS ``issuedSigmetKeys``, web ``live-layer.ts``) still
    mark the listed SIGMET. Read back with :func:`reissue_chain`."""
    return f"{chain}+{own}"


def reissue_chain(key: str) -> str:
    """The chain's first SIGMET from a :func:`reissue_key`."""
    return key.split("+", 1)[0]


def _sigmet_bbox(s: SigmetAlongRoute) -> tuple[float, float, float, float] | None:
    """The area's bounding box. Two points are enough: an "E OF LINE" area
    may come through as its line (LECB 5, 2026-10-05)."""
    return bbox_of_ring(s.coords) if len(s.coords) >= 2 else None


def _trace(
    s: SigmetAlongRoute, now: datetime, *, destination: tuple[float, float] | None, **kw,
) -> LiveSigmetTrace:
    key = _sigmet_key_str(s)
    return LiveSigmetTrace(
        key=key, label=_sigmet_name(s), chain=kw.pop("chain", key),
        fir_id=s.fir_id, hazard=s.hazard, qualifier=s.qualifier,
        valid_from=s.valid_from, valid_to=s.valid_to, bbox=_sigmet_bbox(s),
        at_destination=_near_point(s, destination, DESTINATION_SIGMET_RADIUS_NM),
        base_ft=s.base_ft, top_ft=s.top_ft, min_distance_nm=s.min_distance_nm,
        last_seen=now, **kw,
    )


def _overlaps_band(
    base_ft: int | None, top_ft: int | None, band: tuple[int | None, int | None],
) -> bool:
    """The vertical extent meets the flight's band. A missing bound is
    unbounded on that side (the SIGMET could be anywhere there)."""
    low, high = band
    if low is not None and top_ft is not None and top_ft < low:
        return False
    if high is not None and base_ft is not None and base_ft > high:
        return False
    return True


def _reissue_worse(
    s: SigmetAlongRoute, p: LiveSigmetTrace, band: tuple[int | None, int | None],
) -> bool:
    """The reissue ``s`` is worse for the flight than its predecessor ``p``
    (#689): its area now reaches the route where the predecessor's only came
    near it, or its vertical extent now meets the flight's band. Unknown on
    either side is not worse (no evidence of a change).

    A hazard or qualifier upgrade (EMBD → FRQ, MOD → SEV) never gets here:
    :func:`_reissue_of` requires the same hazard and qualifier, so that
    SIGMET is a "New SIGMET" row, which is already worse. Reaching the
    destination is handled by the caller (it also alerts)."""
    if (
        p.min_distance_nm is not None and s.min_distance_nm is not None
        and p.min_distance_nm > 0 and s.min_distance_nm <= 0
    ):
        return True
    known = (p.base_ft, p.top_ft) != (None, None) and (s.base_ft, s.top_ft) != (None, None)
    return known and not _overlaps_band(p.base_ft, p.top_ft, band) and _overlaps_band(
        s.base_ft, s.top_ft, band,
    )


def _reissue_of(s: SigmetAlongRoute, p: LiveSigmetTrace) -> bool:
    """``s`` is the FIR's reissue of the SIGMET ``p`` traces (see
    :data:`SIGMET_REISSUE_WINDOW`). Without validity or geometry on either
    side it is not: it then shows as a new SIGMET, the louder reading."""
    if s.fir_id != p.fir_id or _sigmet_key_str(s) == p.key:
        return False
    if (s.hazard or "", s.qualifier or "") != (p.hazard or "", p.qualifier or ""):
        return False
    if s.valid_from is None or p.valid_from is None or p.valid_to is None:
        return False
    if not (
        p.valid_to - SIGMET_REISSUE_EARLY <= s.valid_from <= p.valid_to + SIGMET_REISSUE_WINDOW
        and s.valid_from >= p.valid_from
    ):
        return False
    box = _sigmet_bbox(s)
    if box is None or p.bbox is None:
        return False
    return bbox_intersects(bbox_pad(box, _REISSUE_NM), p.bbox)


def _trace_sigmets(
    baseline: RouteSigmets,
    latest: RouteSigmets,
    destination: tuple[float, float] | None,
    seen: dict[str, LiveSigmetTrace],
    now: datetime,
) -> dict[str, LiveSigmetTrace]:
    """A trace for every latest SIGMET, plus the recently seen ones still
    within the reissue window (the predecessors a later reissue can match).

    A SIGMET keeps the trace it got when first seen, so a row never flips
    between "new" and "replaces" from one tick to the next. A SIGMET seen for
    the first time takes the most recent matching predecessor among the
    baseline's SIGMETs and the ones seen before (oldest validity first, so
    two reissues landing in one tick chain in order)."""
    base_keys = {_sigmet_key_str(s) for s in baseline.sigmets}
    # Every trace we hold, however old: a SIGMET still listed long after its
    # validity (a feed keeping it) must keep its own trace, not be re-matched.
    known_traces: dict[str, LiveSigmetTrace] = dict(seen)
    for s in baseline.sigmets:
        k = _sigmet_key_str(s)
        if k not in known_traces:
            known_traces[k] = _trace(s, now, destination=destination, chain_in_baseline=True)
            # A baseline SIGMET no longer listed was last seen by the briefing.
            known_traces[k].last_seen = baseline.fetch_time
    # The predecessors a new SIGMET can match: the window applies only here.
    candidates = {k: t for k, t in known_traces.items() if k in base_keys or _reissuable(t, now)}

    out: dict[str, LiveSigmetTrace] = {}
    latest_sorted = sorted(
        latest.sigmets,
        key=lambda s: s.valid_from or datetime.min.replace(tzinfo=timezone.utc),
    )
    for s in latest_sorted:
        k = _sigmet_key_str(s)
        known = known_traces.get(k)
        if known is not None:
            t = known.model_copy(update={
                "last_seen": now,
                "valid_to": s.valid_to,
                "bbox": _sigmet_bbox(s),
                "at_destination": _near_point(s, destination, DESTINATION_SIGMET_RADIUS_NM),
                "base_ft": s.base_ft,
                "top_ft": s.top_ft,
                "min_distance_nm": s.min_distance_nm,
            })
        else:
            preds = [p for p in candidates.values() if _reissue_of(s, p)]
            pred = max(preds, key=lambda p: (p.valid_from, p.key), default=None)
            if pred is None:
                t = _trace(s, now, destination=destination)
            else:
                t = _trace(
                    s, now, destination=destination,
                    chain=pred.chain, chain_in_baseline=pred.chain_in_baseline,
                    replaces_key=pred.key, replaces_label=pred.label,
                    replaced_at_destination=pred.at_destination,
                    chain_alerted=pred.chain_alerted,
                    reissue_worse=_reissue_worse(
                        s, pred, (latest.altitude_low_ft, latest.altitude_high_ft),
                    ),
                )
        out[k] = t
        candidates[k] = t
    for k, t in candidates.items():
        if k not in out and k not in base_keys and _reissuable(t, now):
            out[k] = t
    return out


def _reissuable(t: LiveSigmetTrace, now: datetime) -> bool:
    """A reissue of this SIGMET could still turn up: within the window after
    its validity ends (after it was last seen, without a validity). Counted
    from validity rather than last seen, so failed fetch ticks or a feed that
    drops a SIGMET early do not lose the link."""
    end = t.valid_to if t.valid_to is not None else t.last_seen
    return now <= end + SIGMET_REISSUE_WINDOW


def _sigmet_changes(
    baseline: RouteSigmets,
    latest: RouteSigmets,
    destination: tuple[float, float] | None = None,
    seen: dict[str, LiveSigmetTrace] | None = None,
    now: datetime | None = None,
    arrival_at: datetime | None = None,
) -> tuple[list[LiveChange], set[str], dict[str, LiveSigmetTrace]]:
    """New, reissued, escalated to SEV, or no longer active — merged across
    FIRs. Also returns the keys of reissue rows that must not raise a fresh
    alert, and the SIGMET traces to remember for the next tick (#682).

    A reissue is one row, "SIGMET LFMM T02 replaces T01: EMBD TS", keyed on
    the first SIGMET of its chain plus its own
    ("sigmet:LFMM|T01+sigmet:LFMM|T02"). Highlight when the chain started from
    a SIGMET the briefing had. When it started from one that was itself new
    since the briefing it keeps that row's alert tier, and does not alert
    again if an earlier row of the chain already alerted (it does alert once
    when the predecessor never had a row: both first seen in one pass). Either
    way it alerts when it now reaches the destination and its predecessor did
    not. The predecessor gets no row of its own (neither its "new" row while
    both are listed, nor "no longer active").

    A reissue of a chain the briefing had is direction "updated" (not
    counted as worse) unless it reaches the destination or is worse than its
    predecessor (:func:`_reissue_worse`); a chain new since the briefing
    stays "worse" (#689). A row whose SIGMETs all start after
    ``arrival_at`` + :data:`SIGMET_AFTER_ARRIVAL_MARGIN` is a highlight and
    does not mark its chain as alerted (#689).
    """
    now = now or datetime.now(timezone.utc)
    traces = _trace_sigmets(baseline, latest, destination, seen or {}, now)
    base_by_key = {sigmet_key(s): s for s in baseline.sigmets}
    latest_keys = {sigmet_key(s) for s in latest.sigmets}
    latest_traces = [traces[_sigmet_key_str(s)] for s in latest.sigmets]
    # SIGMETs a listed reissue replaces, directly or up its chain.
    superseded = {t.replaces_key for t in latest_traces if t.replaces_key}
    replaced_chains = {t.chain for t in latest_traces if t.replaces_key}

    def _live(s: SigmetAlongRoute) -> bool:
        return _sigmet_key_str(s) not in superseded

    reissued = [
        s for s in latest.sigmets
        if sigmet_key(s) not in base_by_key and traces[_sigmet_key_str(s)].replaces_key and _live(s)
    ]
    new = [
        s for s in latest.sigmets
        if sigmet_key(s) not in base_by_key and not traces[_sigmet_key_str(s)].replaces_key and _live(s)
    ]
    escalated = [
        s for s in latest.sigmets
        if sigmet_key(s) in base_by_key and _is_severe(s) and not _is_severe(base_by_key[sigmet_key(s)])
        and _live(s)
    ]
    # A SIGMET that never became valid is not "no longer active": a pending
    # one drops out of the list when a lookahead query fails (#683).
    gone = [
        s for k, s in base_by_key.items()
        if k not in latest_keys and not _pending(s, now)
        and _sigmet_key_str(s) not in superseded and _sigmet_key_str(s) not in replaced_chains
    ]

    out: list[LiveChange] = []
    quiet: set[str] = set()
    for g in _group_same_phenomenon(new):
        prefix = "New SEV SIGMET" if _is_severe(g[0]) else "New SIGMET"
        c = _sigmet_change(
            g, kind="sigmet_issued", direction="worse",
            from_value=None, to_value=_hazard_text(g[0]),
            observed_at=g[0].valid_from,
            message=f"{prefix} {_group_label(g)}{_from_suffix(g, now)}", destination=destination,
        )
        if _after_arrival(g, arrival_at):
            c.tier = "highlight"
        else:
            # An alert-tier "New SIGMET" row: the chain has alerted (now, or
            # on an earlier tick if the memory already holds this value).
            for m in g:
                traces[_sigmet_key_str(m)].chain_alerted = True
        out.append(c)
    for s in reissued:
        t = traces[_sigmet_key_str(s)]
        reaches_destination = t.at_destination and not t.replaced_at_destination
        worse = not t.chain_in_baseline or reaches_destination or t.reissue_worse
        c = _sigmet_change(
            [s], kind="sigmet_issued", direction="worse" if worse else "updated",
            from_value=None, to_value=_hazard_text(s),
            observed_at=s.valid_from,
            message=f"SIGMET {t.label} replaces {_short_label(t)}{_from_suffix([s], now)}: {_hazard_text(s)}",
            destination=destination,
        )
        # The chain's first SIGMET, then this one: clients split the key on
        # "+" to mark the listed SIGMET (this one) as changed, and the trail
        # groups a reissue row on the first part (live_trail._trail_key).
        c.key = reissue_key(t.chain, t.key)
        c.replaces = t.replaces_label
        if (t.chain_in_baseline and not reaches_destination) or _after_arrival([s], arrival_at):
            c.tier = "highlight"
        # Quiet only when the briefing had the chain or an earlier row of it
        # already alerted. A predecessor first seen in this same pass (T01 and
        # T02 both new on the first tick, or after failed fetches) never
        # alerted: the reissue alerts once.
        if not reaches_destination and (t.chain_in_baseline or t.chain_alerted):
            quiet.add(c.key)
        if c.tier == "alert":
            t.chain_alerted = True
        out.append(c)
    for g in _group_same_phenomenon(escalated):
        prev = base_by_key[sigmet_key(g[0])]
        out.append(_sigmet_change(
            g, kind="sigmet_issued", direction="worse",
            from_value=_hazard_text(prev, default="") or None, to_value=_hazard_text(g[0], default="SEV"),
            observed_at=g[0].valid_from,
            message=f"SIGMET {_group_label(g)} escalated to SEV", destination=destination,
        ))
    for g in _group_same_phenomenon(gone):
        out.append(_sigmet_change(
            g, kind="sigmet_cancelled", direction="better",
            from_value=_hazard_text(g[0]), to_value=None,
            observed_at=g[0].valid_to,
            message=f"SIGMET {_group_label(g)} no longer active", destination=destination,
        ))
    return out, quiet, traces


def _short_label(t: LiveSigmetTrace) -> str:
    """The predecessor as the row names it: "T01" within the same FIR."""
    label = t.replaces_label or ""
    prefix = f"{t.fir_id} "
    return label[len(prefix):] if label.startswith(prefix) and len(label) > len(prefix) else label


# --- Observed radar / lightning ---------------------------------------------


def _station_positions(observed: ObservedConditions) -> dict[str, float | None]:
    return {st.id: st.enroute_distance_nm for st in observed.stations}


def _ahead(dist: float | None, flown_nm: float | None) -> bool:
    # A station with no along-track position is kept: better a spurious
    # highlight than a hidden echo.
    return flown_nm is None or dist is None or dist >= flown_nm


def _lightning_span(
    observed: ObservedConditions | None, flown_nm: float | None,
) -> tuple[list[float], float | None, datetime | None, list[LiveEvidencePoint]] | None:
    """Along-track positions of route points ahead with flashes in the
    innermost ring, plus those points as evidence (#643). None when the
    lightning field is absent (unknown, not clear)."""
    if observed is None or observed.lightning is None:
        return None
    pos = _station_positions(observed)
    hits: list[float] = []
    evidence: list[LiveEvidencePoint] = []
    radius: float | None = None
    for st in observed.lightning.stations:
        if not st.annuli:
            continue
        inner = min(st.annuli, key=lambda a: a.radius_nm)
        radius = inner.radius_nm if radius is None else min(radius, inner.radius_nm)
        d = pos.get(st.station_id)
        if inner.flash_count > 0 and _ahead(d, flown_nm):
            hits.append(d if d is not None else -1.0)
            evidence.append(LiveEvidencePoint(
                station_id=st.station_id, enroute_distance_nm=d,
                radius_nm=inner.radius_nm, flash_count=inner.flash_count,
            ))
    return hits, radius, observed.lightning.valid_time, evidence


def _radar_span(
    observed: ObservedConditions | None, flown_nm: float | None,
) -> tuple[list[float], float | None, float | None, datetime | None, list[LiveEvidencePoint]] | None:
    """Along-track positions of route points ahead whose innermost ring has a
    heavy-or-worse echo on adequate coverage, plus those points as evidence
    (#643). None when the field is absent."""
    if observed is None or observed.reflectivity is None:
        return None
    pos = _station_positions(observed)
    hits: list[float] = []
    evidence: list[LiveEvidencePoint] = []
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
            evidence.append(LiveEvidencePoint(
                station_id=st.station_id, enroute_distance_nm=d,
                radius_nm=inner.radius_nm, max_dbz=inner.max_value,
                valid_px=inner.valid_px, total_px=inner.total_px,
            ))
    return hits, radius, peak, observed.reflectivity.valid_time, evidence


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
        b_hits = b_l[0]
        l_hits, radius, valid, evidence = l_l
        r = f"{radius:g} NM" if radius is not None else "the route"
        if l_hits and not b_hits:
            out.append(LiveChange(
                key="lightning:route", kind="lightning", source="LIGHTNING",
                direction="worse", tier="highlight", role="route",
                # The condition, not its detail: the hit count and span are
                # in the message, so a tick where they move is not a new
                # change (#682).
                from_value="none", to_value="present", observed_at=valid,
                enroute_distance_nm=min((h for h in l_hits if h >= 0), default=None),
                message=f"Lightning within {r} of route{_span_text(l_hits)}",
                evidence=evidence,
            ))
        elif b_hits and not l_hits:
            out.append(LiveChange(
                key="lightning:route", kind="lightning", source="LIGHTNING",
                direction="better", tier="highlight", role="route",
                from_value="present", to_value="none", observed_at=valid,
                message=f"No lightning within {r} of route ahead",
            ))

    b_r = _radar_span(baseline, flown_nm)
    l_r = _radar_span(latest, flown_nm)
    if b_r is not None and l_r is not None:
        b_hits = b_r[0]
        l_hits, radius, peak, valid, evidence = l_r
        r = f"{radius:g} NM" if radius is not None else "the route"
        if l_hits and not b_hits:
            out.append(LiveChange(
                key="radar:route", kind="radar", source="RADAR",
                direction="worse", tier="highlight", role="route",
                # Categorical like the lightning one: the peak dBZ and the
                # span are in the message (#682).
                from_value="none", to_value="heavy",
                observed_at=valid,
                enroute_distance_nm=min((h for h in l_hits if h >= 0), default=None),
                message=(
                    f"Heavy radar echo (peak {peak:.0f} dBZ) within {r} of route"
                    f"{_span_text(l_hits)}"
                ),
                evidence=evidence,
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
_DIRECTION_ORDER = {"worse": 0, "updated": 1, "better": 2}


def classify_changes(
    *,
    baseline_obs: RouteObservations | None,
    latest_obs: RouteObservations | None,
    baseline_sigmets: RouteSigmets | None,
    latest_sigmets: RouteSigmets | None,
    baseline_observed: ObservedConditions | None = None,
    latest_observed: ObservedConditions | None = None,
    roles: dict[str, ChangeRole] | None = None,
    destination: tuple[float, float] | None = None,
    departure_at: datetime | None = None,
    arrival_at: datetime | None = None,
    baseline_at: datetime | None = None,
    flown_nm: float | None = None,
    memory: ClassifierMemory | None = None,
    now: datetime | None = None,
) -> tuple[LiveChanges, ClassifierMemory]:
    """Everything significant since the briefing, plus the updated memory.

    A ``None`` on either side of a dimension skips it: no baseline means
    nothing to compare against (a pre-SIGMET pack), no latest means the fetch
    failed — neither is a change. ``flown_nm`` (distance already flown at
    ``now``) limits radar/lightning to the route still ahead. ``destination``
    (lat, lon) marks SIGMETs over or near it. ``departure_at`` ends the
    departure airport's relevance at take-off. ``arrival_at`` (planned
    landing) makes a SIGMET starting well after it a highlight (#689).
    """
    roles = roles or {}
    memory = memory or ClassifierMemory()
    now = now or datetime.now(timezone.utc)
    departed = departure_at is not None and now >= departure_at
    unknown: set[str] = set()
    changes: list[LiveChange] = []

    # Key prefixes whose dimension was actually evaluated this tick. Memory for
    # a dimension that was skipped (fetch failed) is kept, so a failed tick
    # neither clears an alert nor lets it fire twice.
    evaluated: set[str] = set()
    if baseline_obs is not None and latest_obs is not None:
        changes += _airport_changes(
            baseline_obs, latest_obs, roles, unknown, departed=departed, flown_nm=flown_nm,
        )
        evaluated |= {"metar:", "taf:", "conv:", "wx:", "wind:"}
    quiet: set[str] = set()
    sigmet_traces = dict(memory.sigmets)
    new_sigmets: list[str] | None = None
    if baseline_sigmets is not None and latest_sigmets is not None:
        sigmet_rows, quiet, sigmet_traces = _sigmet_changes(
            baseline_sigmets, latest_sigmets, destination, memory.sigmets, now, arrival_at,
        )
        changes += sigmet_rows
        evaluated.add("sigmet:")
        # One NEW rule for every surface (#689): a listed SIGMET is new to
        # the flight when its reissue chain did not start in the baseline.
        new_sigmets = sorted({
            k for k in (_sigmet_key_str(s) for s in latest_sigmets.sigmets)
            # Every listed SIGMET gets a trace; a missing one reads as new
            # (the louder reading) rather than failing the tick.
            if not getattr(sigmet_traces.get(k), "chain_in_baseline", False)
        })
    changes += _observed_changes(baseline_observed, latest_observed, flown_nm)
    evaluated |= {"lightning:", "radar:"}

    changes.sort(key=lambda c: (
        0 if c.tier == "alert" else 1,
        _ROLE_ORDER.get(c.role, 9),
        _DIRECTION_ORDER.get(c.direction, 9),
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
            # A merged SIGMET's key changes when a partner FIR issues late or
            # one of the pair lapses ("sigmet:A|3" ↔ "sigmet:A|3+sigmet:B|3"):
            # the same phenomenon, already alerted, under its old key.
            previous = _alerted_under_another_key(c.key, value, alerted)
            if previous is not None:
                del alerted[previous]
            elif c.key not in quiet:
                # A SIGMET reissue continuing an alerted row does not alert
                # again, even after a tick's gap between the two (#682).
                c.new_alert = True
            alerted[c.key] = value
    for k in list(alerted):
        if k not in live_alert_keys and k not in unknown and any(k.startswith(p) for p in evaluated):
            if _pending_key(k, sigmet_traces, now):
                # Missing from this fetch before it ever became valid (a failed
                # lookahead query): kept, so its return does not alert twice.
                continue
            del alerted[k]

    result = LiveChanges(
        baseline_at=baseline_at,
        computed_at=now or datetime.now(timezone.utc),
        changes=changes,
        new_sigmets=new_sigmets,
    )
    return result, ClassifierMemory(alerted=alerted, sigmets=sigmet_traces)


def _pending_key(key: str, traces: dict[str, LiveSigmetTrace], now: datetime) -> bool:
    """A SIGMET change key with a member last seen as not yet valid (#683)."""
    if not key.startswith("sigmet:"):
        return False
    for part in key.split("+"):
        t = traces.get(part)
        if t is not None and t.valid_from is not None and t.valid_from > now:
            return True
    return False


def _alerted_under_another_key(key: str, value: str, alerted: dict[str, str]) -> str | None:
    """The alerted SIGMET key sharing a member SIGMET with ``key`` at the same
    value, if any (merged keys join per-FIR keys with "+")."""
    if not key.startswith("sigmet:"):
        return None
    parts = set(key.split("+"))
    for k, v in alerted.items():
        if k != key and k.startswith("sigmet:") and v == value and parts & set(k.split("+")):
            return k
    return None


def worsening_delta(changes: LiveChanges) -> RefreshDelta:
    """The worsening half of ``changes`` in the pre-#637 ``RefreshDelta``
    shape, for clients that still read ``last_refresh_delta``."""
    messages = [c.message for c in changes.changes if c.direction == "worse"]
    return RefreshDelta(
        worsened=bool(messages),
        messages=messages,
        computed_at=changes.computed_at,
    )
