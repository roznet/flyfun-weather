"""Area outlook — "is a flight even worth briefing?" from the forecast map (#836).

An indicative, pre-briefing read of the forecast-map snapshots around a base
airport (radius mode) or along a route corridor (route mode), over a few days
and sample hours. Everything heavy happens here so the calling LLM only
narrates: airport selection, a GO / MARGINAL / NO-GO grade per airport and
slot, per-sector or per-route summaries, out-and-back pairing, best slots and
a per-day trend.

Posture: an attention-director, never a briefing. Grades are point forecasts
at monitored airports at 3-hourly snapshots, nothing between airports, no
en-route icing (snapshots carry no freezing level), no operational suitability.
The output says so and points to ``create_flight`` for the slot the pilot
picks.

Structure:

* :func:`build_area_outlook` — the I/O shell: resolves the query against the
  watchlist / airport DB and loads each slot via ``load_forecast_slot`` (the
  same cache-first path as ``/maps/airport-weather``).
* :func:`compute_outlook` — pure: takes the resolved query and the per-slot map
  payloads and returns the compact result. Unit-tested on synthetic slots.

Grades read the map's own consensus blocks (``consensus`` /
``consensus_majority`` from ``analysis.airport_consensus``), so an airport's
category here is exactly the one the forecast map shows for the same slot and
mode. Wind thresholds are the Airport Wind advisory's catalog defaults, read
from the catalog rather than copied. See ``designs/area-outlook.md``.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from euro_aip.utils.solar import CIVIL_TWILIGHT_DEG, solar_elevation

from weatherbrief.analysis.route_geometry import RouteTrack

Rules = Literal["vfr", "ifr"]
Mode = Literal["worst", "majority"]

GO, MARGINAL, NOGO = "GO", "MARGINAL", "NO-GO"
_GRADE_RANK = {GO: 0, MARGINAL: 1, NOGO: 2}
_CONF_RANK = {"high": 0, "medium": 1, "low": 2}
_WEEKDAY = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

# Selection caps: keep the answer compact (target ≤ ~4k tokens for a weekend).
MAX_AREA_AIRPORTS = 16
MAX_CORRIDOR_AIRPORTS = 12
# A base/destination resolved further than this from the requested airport is
# flagged as unrepresentative (mirrors the tool's "nearest monitored" note).
UNREPRESENTATIVE_NM = 20.0
DEFAULT_RADIUS_NM = 60.0
DEFAULT_CORRIDOR_NM = 20.0
DEFAULT_TAS_KT = 110.0

# Convective risk (``airport_consensus._RISK_ORDER``): "marginal" is the
# benign floor the map shows for small CAPE, so it does not cost a grade.
_CONVECTIVE_MARGINAL = frozenset({"low"})
_CONVECTIVE_NOGO = frozenset({"moderate", "high", "extreme"})


# ---------------------------------------------------------------------------
# Minima
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Minima:
    """GO / NO-GO lines. Between the two is MARGINAL.

    ``ceiling_go_ft`` / ``vis_go_m``: at or above → GO on cloud/visibility.
    ``ceiling_floor_ft`` / ``vis_floor_m``: below → NO-GO.
    Wind follows the Airport Wind advisory: below ``*_green`` GO, at or above
    ``*_red`` NO-GO, between MARGINAL.
    """

    ceiling_go_ft: float
    ceiling_floor_ft: float
    vis_go_m: float
    vis_floor_m: float
    crosswind_green_kt: float
    crosswind_red_kt: float
    gust_green_kt: float
    gust_red_kt: float


def _airport_wind_defaults() -> dict[str, float]:
    """The Airport Wind advisory's catalog defaults (one source for both)."""
    from weatherbrief.analysis.advisories.airport_wind import AirportWindEvaluator

    return {
        p.key: float(p.default)
        for p in AirportWindEvaluator.catalog_entry().parameters
    }


# VFR preset (issue #836): GO at 1500 ft / 5 km, NO-GO in IFR-category cloud
# (< 1000 ft) or below 3 km. Presets for an indicative tool, not new
# meteorology — see designs/meteorology-decisions.md (area outlook pointer).
VFR_CEILING_GO_FT = 1500.0
VFR_CEILING_FLOOR_FT = 1000.0
VFR_VIS_GO_M = 5000.0
VFR_VIS_FLOOR_M = 3000.0


def minima_for(
    rules: Rules,
    *,
    min_ceiling_ft: float | None = None,
    min_visibility_m: float | None = None,
    max_crosswind_kt: float | None = None,
    max_gust_kt: float | None = None,
) -> Minima:
    """The preset for ``rules`` with the caller's overrides applied.

    Overrides move the GO line (ceiling/vis) or the NO-GO line (wind); the
    other line is clamped so the MARGINAL band never inverts. For IFR the
    ceiling/visibility lines are per-airport (approach-based, see
    :func:`_grade_ifr_cloud`), so ``min_ceiling_ft`` / ``min_visibility_m``
    only apply at airports without an instrument approach.
    """
    w = _airport_wind_defaults()
    m = Minima(
        ceiling_go_ft=VFR_CEILING_GO_FT,
        ceiling_floor_ft=VFR_CEILING_FLOOR_FT,
        vis_go_m=VFR_VIS_GO_M,
        vis_floor_m=VFR_VIS_FLOOR_M,
        crosswind_green_kt=w.get("crosswind_green_kt", 15.0),
        crosswind_red_kt=w.get("crosswind_red_kt", 25.0),
        gust_green_kt=w.get("gust_green_kt", 25.0),
        gust_red_kt=w.get("gust_red_kt", 35.0),
    )
    if min_ceiling_ft is not None:
        m = replace(m, ceiling_go_ft=min_ceiling_ft,
                    ceiling_floor_ft=min(m.ceiling_floor_ft, min_ceiling_ft))
    if min_visibility_m is not None:
        m = replace(m, vis_go_m=min_visibility_m,
                    vis_floor_m=min(m.vis_floor_m, min_visibility_m))
    if max_crosswind_kt is not None:
        m = replace(m, crosswind_red_kt=max_crosswind_kt,
                    crosswind_green_kt=min(m.crosswind_green_kt, max_crosswind_kt))
    if max_gust_kt is not None:
        m = replace(m, gust_red_kt=max_gust_kt,
                    gust_green_kt=min(m.gust_green_kt, max_gust_kt))
    return m


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@dataclass
class Place:
    """A monitored airport standing in for a requested one (or itself)."""

    icao: str
    lat: float
    lon: float
    requested: str | None = None  # set when substituted
    distance_nm: float | None = None  # requested → monitored

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"icao": self.icao}
        if self.requested and self.requested != self.icao:
            out["requested"] = self.requested
            out["substituted_nm"] = round(self.distance_nm or 0.0, 1)
            if (self.distance_nm or 0.0) > UNREPRESENTATIVE_NM:
                out["unrepresentative"] = True
        return out


@dataclass
class Candidate:
    icao: str
    lat: float
    lon: float
    distance_nm: float | None = None  # from base (radius mode)
    bearing_deg: float | None = None
    sector: str | None = None
    along_nm: float | None = None  # route mode
    offtrack_nm: float | None = None


@dataclass
class OutlookQuery:
    mode: Literal["radius", "route"]
    rules: Rules
    consensus: Mode
    minima: Minima
    slots: list[tuple[int, int]]  # (day, hour_utc)
    base: Place | None = None  # radius mode
    radius_nm: float | None = None
    departure: Place | None = None  # route mode, or radius + destination
    destination: Place | None = None
    corridor_nm: float | None = None
    route_points: list[tuple[float, float]] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    tas_kt: float = DEFAULT_TAS_KT
    dropped_waypoints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

_EARTH_NM = 3440.065


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def sector_for(bearing: float) -> str:
    """Four quadrants centred on the cardinals: N is 315°–45°."""
    return ("N", "E", "S", "W")[int(((bearing + 45.0) % 360.0) // 90.0)]


def nearest_monitored(
    lat: float, lon: float, coords: dict[str, tuple[float, float]],
) -> tuple[str, float] | None:
    best: tuple[str, float] | None = None
    for icao, (alat, alon) in coords.items():
        d = distance_nm(lat, lon, alat, alon)
        if best is None or d < best[1]:
            best = (icao, d)
    return best


def select_radius(
    base: Place,
    radius_nm: float,
    coords: dict[str, tuple[float, float]],
    exclude: set[str] = frozenset(),
    cap: int = MAX_AREA_AIRPORTS,
) -> list[Candidate]:
    """Monitored airports within ``radius_nm`` of the base, spread over sectors.

    Round-robin by sector, nearest first, so one dense quadrant cannot crowd
    out the others when the cap bites. Returned nearest first.
    """
    by_sector: dict[str, list[Candidate]] = {s: [] for s in ("N", "E", "S", "W")}
    for icao, (lat, lon) in coords.items():
        if icao == base.icao or icao in exclude:
            continue
        d = distance_nm(base.lat, base.lon, lat, lon)
        if d > radius_nm:
            continue
        b = bearing_deg(base.lat, base.lon, lat, lon)
        s = sector_for(b)
        by_sector[s].append(Candidate(icao, lat, lon, distance_nm=d, bearing_deg=b, sector=s))
    for lst in by_sector.values():
        lst.sort(key=lambda c: c.distance_nm)
    picked: list[Candidate] = []
    i = 0
    while len(picked) < cap and any(i < len(lst) for lst in by_sector.values()):
        for s in ("N", "E", "S", "W"):
            if i < len(by_sector[s]) and len(picked) < cap:
                picked.append(by_sector[s][i])
        i += 1
    picked.sort(key=lambda c: c.distance_nm)
    return picked


def route_track(points: list[tuple[float, float]]) -> RouteTrack:
    dists = [0.0]
    for (a, b), (c, d) in zip(points, points[1:]):
        dists.append(dists[-1] + distance_nm(a, b, c, d))
    return RouteTrack(points, dists)


def select_corridor(
    points: list[tuple[float, float]],
    corridor_nm: float,
    coords: dict[str, tuple[float, float]],
    exclude: set[str] = frozenset(),
    cap: int = MAX_CORRIDOR_AIRPORTS,
) -> list[Candidate]:
    """Monitored airports within ``corridor_nm`` of the route, by along-track.

    Points past either end of the route are skipped (they are near an
    endpoint, which is graded on its own). Over the cap, the closest to the
    track are kept.
    """
    track = route_track(points)
    found: list[Candidate] = []
    for icao, (lat, lon) in coords.items():
        if icao in exclude:
            continue
        p = track.project(lat, lon)
        if p.end is not None or p.offtrack_nm > corridor_nm:
            continue
        found.append(Candidate(icao, lat, lon, along_nm=p.along_nm, offtrack_nm=p.offtrack_nm))
    if len(found) > cap:
        found = sorted(found, key=lambda c: c.offtrack_nm)[:cap]
    found.sort(key=lambda c: c.along_nm)
    return found


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _worse(a: str, b: str) -> str:
    return a if _GRADE_RANK[a] >= _GRADE_RANK[b] else b


def _reduce(vals: list[float], mode: Mode) -> float | None:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return max(vals) if mode == "worst" else statistics.median(vals)


def _alt_required(models: dict[str, dict], mode: Mode) -> bool | None:
    """FAA-or-EASA alternate required, reduced across models like a category."""
    flags = [
        bool(m["alt_required"].get("faa") or m["alt_required"].get("easa"))
        for m in models.values() if isinstance(m.get("alt_required"), dict)
    ]
    if not flags:
        return None
    if mode == "worst":
        return any(flags)
    return sum(flags) * 2 > len(flags)


def confidence(cons: dict[str, Any], n_models: int) -> str:
    """high / medium / low from model agreement and coverage.

    Divergent category or ceiling → low; mixed → medium. Fewer than two models
    can't agree with anyone → low. Two-model days (D+5/D+6) cap at medium.
    """
    if n_models < 2:
        return "low"
    agr = cons.get("agreement") or {}
    labels = [agr.get("flight_category"), agr.get("ceiling_ft")]
    if "divergent" in labels:
        return "low"
    if "mixed" in labels or n_models < 3:
        return "medium"
    return "high"


def sun_phase(lat: float, lon: float, when: datetime) -> str:
    """'day' / 'twilight' (civil) / 'night', same bands as analysis.sun."""
    elev = solar_elevation(lat, lon, when)
    if elev > 0.0:
        return "day"
    if elev >= -CIVIL_TWILIGHT_DEG:
        return "twilight"
    return "night"


def _grade_band(value: float | None, go: float, floor: float) -> str:
    """Higher-is-better value against GO / NO-GO lines; None = not limiting."""
    if value is None:
        return GO
    if value < floor:
        return NOGO
    if value < go:
        return MARGINAL
    return GO


def _grade_ifr_cloud(
    ceiling: float | None, vis: float | None, alt_req: bool | None,
    approach_type: str | None, m: Minima,
) -> tuple[str, str | None]:
    """IFR terminal grade: approach-class minima floor, alternate trigger band.

    NO-GO below the approach class's worst-case published minima (DH/MDH and
    visibility, ``alternate_requirement.APPROACH_CLASS_PROXY`` hi ends); GO
    when no destination alternate would be required (FAA/EASA); MARGINAL in
    between. An airport with no instrument approach is graded as VFR.
    """
    from weatherbrief.analysis.alternate_requirement import proxy_for_approach

    proxy = proxy_for_approach(approach_type, has_iap=approach_type is not None)
    if proxy is None:
        g = _worse(_grade_band(ceiling, m.ceiling_go_ft, m.ceiling_floor_ft),
                   _grade_band(vis, m.vis_go_m, m.vis_floor_m))
        return g, ("no approach: VFR minima" if g != GO else None)
    if (ceiling is not None and ceiling < proxy.dh_hi) or (vis is not None and vis < proxy.vis_hi):
        return NOGO, f"below est. {approach_type} minima"
    if alt_req:
        return MARGINAL, "alternate required"
    return GO, None


def grade_airport(
    entry: dict[str, Any],
    *,
    rules: Rules,
    mode: Mode,
    minima: Minima,
    valid_time: datetime,
) -> dict[str, Any]:
    """Grade one map airport entry at one slot.

    Returns a dict with ``grade``, ``conf``, ``reasons`` and the compact
    values the row prints. Reads the map's baked consensus for ``mode``.
    """
    models = entry.get("models") or {}
    cons = entry.get("consensus" if mode == "worst" else "consensus_majority") or {}
    out: dict[str, Any] = {"icao": entry.get("icao")}
    if not models or not cons:
        out.update(grade=None, conf="low", reasons=["no data"])
        return out

    cat = cons.get("flight_category")
    ceiling = cons.get("ceiling_ft")
    vis = cons.get("visibility_m")
    xwind = cons.get("crosswind_kt")
    gust = _reduce([m.get("wind_gust_kt") for m in models.values()], mode)
    risk = cons.get("convective_risk") or "none"
    reasons: list[str] = []

    if rules == "ifr":
        grade, why = _grade_ifr_cloud(
            ceiling, vis, _alt_required(models, mode), entry.get("approach_type"), minima,
        )
        if why:
            reasons.append(why)
    else:
        g_ceil = _grade_band(ceiling, minima.ceiling_go_ft, minima.ceiling_floor_ft)
        g_vis = _grade_band(vis, minima.vis_go_m, minima.vis_floor_m)
        grade = _worse(g_ceil, g_vis)
        if g_ceil != GO:
            reasons.append(f"ceiling {int(ceiling)}ft")
        if g_vis != GO:
            reasons.append(f"vis {int(vis)}m")

    if xwind is not None:
        if xwind >= minima.crosswind_red_kt:
            grade = NOGO
            reasons.append(f"xwind {xwind:.0f}kt")
        elif xwind >= minima.crosswind_green_kt:
            grade = _worse(grade, MARGINAL)
            reasons.append(f"xwind {xwind:.0f}kt")
    if gust is not None:
        if gust >= minima.gust_red_kt:
            grade = NOGO
            reasons.append(f"gust {gust:.0f}kt")
        elif gust >= minima.gust_green_kt:
            grade = _worse(grade, MARGINAL)
            reasons.append(f"gust {gust:.0f}kt")

    if risk in _CONVECTIVE_NOGO:
        grade = NOGO
        reasons.append(f"convective {risk}")
    elif risk in _CONVECTIVE_MARGINAL:
        grade = _worse(grade, MARGINAL)
        reasons.append(f"convective {risk}")

    lat, lon = entry.get("lat"), entry.get("lon")
    phase = sun_phase(lat, lon, valid_time) if lat is not None and lon is not None else "day"
    if phase == "night" and rules == "vfr":
        grade = NOGO
        reasons.append("night")
    elif phase != "day":
        reasons.append(phase)

    out.update(
        grade=grade,
        conf=confidence(cons, len(models)),
        reasons=reasons,
        cat=cat,
        ceiling_ft=ceiling,
        vis_m=vis,
        wind_dir=cons.get("wind_dir_deg"),
        wind_kt=cons.get("wind_speed_kt"),
        gust_kt=gust,
        xwind_kt=xwind,
        phase=phase,
    )
    return out


def compact_row(g: dict[str, Any]) -> str:
    """One airport-slot as a short string (see the ``legend`` in the result)."""
    if g.get("grade") is None:
        return "no data"
    parts = [g["grade"], g.get("cat") or "?"]
    c = g.get("ceiling_ft")
    parts.append(f"{int(round(c, -1))}ft" if c is not None else "nocig")
    v = g.get("vis_m")
    parts.append(f"{min(int(round(v, -2)), 9999)}m" if v is not None else "vis?")
    wd, ws, gu = g.get("wind_dir"), g.get("wind_kt"), g.get("gust_kt")
    if ws is not None:
        w = f"{int(round(wd or 0, -1)) % 360:03d}/{int(round(ws))}"
        if gu is not None and gu >= ws + 5:
            w += f"G{int(round(gu))}"
        parts.append(w)
    if g.get("xwind_kt") is not None:
        parts.append(f"xw{int(round(g['xwind_kt']))}")
    parts.append(g["conf"])
    if g.get("phase") in ("night", "twilight"):
        parts.append(g["phase"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def slot_label(valid: datetime) -> str:
    return f"{_WEEKDAY[valid.weekday()]}{valid.hour:02d}"


def _grade_counts(grades: list[dict[str, Any]]) -> dict[str, int]:
    out = {GO: 0, MARGINAL: 0, NOGO: 0}
    for g in grades:
        if g.get("grade") in out:
            out[g["grade"]] += 1
    return out


def _summary(grades: list[dict[str, Any]]) -> dict[str, Any] | None:
    graded = [g for g in grades if g.get("grade")]
    if not graded:
        return None
    worst = max(graded, key=lambda g: (_GRADE_RANK[g["grade"]], -(g.get("ceiling_ft") or 1e9)))
    ceilings = [g["ceiling_ft"] for g in graded if g.get("ceiling_ft") is not None]
    gusts = [g["gust_kt"] for g in graded if g.get("gust_kt") is not None]
    c = _grade_counts(graded)
    out: dict[str, Any] = {
        "go/marg/nogo": f"{c[GO]}/{c[MARGINAL]}/{c[NOGO]}",
        "min_ceiling_ft": int(min(ceilings)) if ceilings else None,
        "max_gust_kt": round(max(gusts)) if gusts else None,
        "conf": max((g["conf"] for g in graded), key=lambda c: _CONF_RANK[c]),
    }
    if worst["grade"] != GO:
        out["worst"] = f"{worst['icao']} {worst['grade']}"
    return out


def _go_fraction(grades: list[dict[str, Any]]) -> float:
    graded = [g for g in grades if g.get("grade")]
    if not graded:
        return 0.0
    score = sum({GO: 1.0, MARGINAL: 0.5, NOGO: 0.0}[g["grade"]] for g in graded)
    return score / len(graded)


def _trend(scores: list[float]) -> str:
    if len(scores) < 2:
        return "steady"
    half = max(1, len(scores) // 2)
    early = sum(scores[:half]) / half
    late = sum(scores[-half:]) / half
    if late - early >= 0.25:
        return "improving"
    if early - late >= 0.25:
        return "deteriorating"
    return "steady"


def _leg_ok(*grades: dict[str, Any] | None) -> str | None:
    """Worst grade over the leg's points, or None if any is NO-GO / missing."""
    worst = GO
    for g in grades:
        if g is None or g.get("grade") is None or g["grade"] == NOGO:
            return None
        worst = _worse(worst, g["grade"])
    return worst


def corridor_point(grades: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The corridor as one point of a leg, or None when it has no airports.

    A corridor airport can sit up to ``corridor_nm`` off track, and a pilot
    can route around one bad field, so a minority of NO-GO airports makes the
    leg MARGINAL; it blocks the leg only when at least half are NO-GO.
    """
    graded = [g for g in grades if g.get("grade")]
    if not graded:
        return None
    n_nogo = sum(1 for g in graded if g["grade"] == NOGO)
    if n_nogo * 2 >= len(graded):
        grade = NOGO
    elif n_nogo:
        grade = MARGINAL
    else:
        grade = max((g["grade"] for g in graded), key=lambda x: _GRADE_RANK[x])
    conf = max((g["conf"] for g in graded), key=lambda c: _CONF_RANK[c])
    return {"grade": grade, "conf": conf}


def _nearest_slot(slots: list[tuple[int, int]], day: int, hour: float) -> tuple[int, int] | None:
    same = [s for s in slots if s[0] == day]
    if not same:
        return None
    return min(same, key=lambda s: abs(s[1] - hour))


def compute_outlook(
    q: OutlookQuery,
    slot_data: dict[tuple[int, int], dict[str, Any]],
    valid_times: dict[tuple[int, int], datetime],
    map_base_url: str | None = None,
) -> dict[str, Any]:
    """Pure core: grade, aggregate and pair. See module docstring.

    With ``map_base_url``, the result, each best slot and each pair's
    outbound slot carry a ``map_url`` to the forecast map at that time.
    """
    focus: list[Place] = [p for p in (q.base, q.departure, q.destination) if p is not None]
    focus_icaos = list(dict.fromkeys(p.icao for p in focus))
    all_icaos = focus_icaos + [c.icao for c in q.candidates if c.icao not in focus_icaos]

    # grades[(slot)][icao]
    grades: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    available: list[tuple[int, int]] = []
    model_counts: dict[str, int] = {}
    for slot in q.slots:
        data = slot_data.get(slot) or {}
        entries = {a["icao"]: a for a in data.get("airports") or []}
        if not entries:
            continue
        available.append(slot)
        label = slot_label(valid_times[slot])
        model_counts[label] = len(data.get("model_init_times") or {})
        grades[slot] = {
            icao: grade_airport(
                entries[icao], rules=q.rules, mode=q.consensus, minima=q.minima,
                valid_time=valid_times[slot],
            )
            for icao in all_icaos if icao in entries
        }

    slots_out: list[dict[str, Any]] = []
    for slot in available:
        g = grades[slot]
        label = slot_label(valid_times[slot])
        row: dict[str, Any] = {"slot": label}
        if q.mode == "radius":
            row["base"] = compact_row(g.get(q.base.icao, {}))
            sectors = {}
            for s in ("N", "E", "S", "W"):
                summ = _summary([g[c.icao] for c in q.candidates if c.sector == s and c.icao in g])
                if summ:
                    sectors[s] = summ
            row["sectors"] = sectors
            if q.destination is not None:
                row["destination"] = compact_row(g.get(q.destination.icao, {}))
        else:
            row["departure"] = compact_row(g.get(q.departure.icao, {}))
            row["destination"] = compact_row(g.get(q.destination.icao, {}))
            corridor = _summary([g[c.icao] for c in q.candidates if c.icao in g])
            if corridor:
                row["corridor"] = corridor
        slots_out.append(row)

    # Compact per-airport rows for the area / corridor airports (the base,
    # departure and destination rows are already in ``slots``): one string per
    # slot, in ``slot_order``.
    airports_out: list[dict[str, Any]] = []
    for c in q.candidates:
        if c.icao in focus_icaos:
            continue
        rows = [compact_row(grades[s][c.icao]) if c.icao in grades[s] else "no data"
                for s in available]
        if all(r == "no data" for r in rows):
            continue
        at = (f"{c.distance_nm:.0f}nm {c.sector}" if c.distance_nm is not None
              else f"{c.along_nm:.0f}nm along, {c.offtrack_nm:.0f}nm off")
        airports_out.append({"icao": c.icao, "at": at, "rows": rows})

    # Best slots + trend. Radius: base grade first, then the area GO fraction.
    # Route: the outbound leg (departure, corridor, destination) worst grade.
    def slot_score(slot: tuple[int, int]) -> tuple[int, float, int]:
        g = grades[slot]
        if q.mode == "radius":
            key = g.get(q.base.icao, {}).get("grade")
            pool = [g[c.icao] for c in q.candidates if c.icao in g]
        else:
            ends = [g.get(q.departure.icao), g.get(q.destination.icao)]
            corridor = [g[c.icao] for c in q.candidates if c.icao in g]
            cp = corridor_point(corridor)
            key = _leg_ok(*ends, *([cp] if cp else [])) or NOGO
            pool = [x for x in ends if x] + corridor
        focus_g = [x for x in pool if x.get("grade")]
        conf = max((x["conf"] for x in focus_g), key=lambda c: _CONF_RANK[c]) if focus_g else "low"
        return (_GRADE_RANK.get(key, 2), -_go_fraction(pool), _CONF_RANK[conf])

    ranked = sorted(available, key=lambda s: (slot_score(s), s))
    best: list[dict[str, Any]] = []
    for s in ranked[:3]:
        sc = slot_score(s)
        if sc[0] == 2:
            break
        b = {
            "slot": slot_label(valid_times[s]),
            "grade": [GO, MARGINAL, NOGO][sc[0]],
            "area_go_score": round(-sc[1], 2),
            "conf": ("high", "medium", "low")[sc[2]],
        }
        if map_base_url:
            b["map_url"] = map_url(map_base_url, q, s)
        best.append(b)

    days = sorted({d for d, _ in available})
    trend: dict[str, str] = {}
    for d in days:
        ds = [s for s in available if s[0] == d]
        pool_scores = []
        for s in ds:
            pool = list(grades[s].values())
            pool_scores.append(_go_fraction(pool))
        trend[_WEEKDAY[valid_times[ds[0]].weekday()]] = _trend(pool_scores)

    result: dict[str, Any] = {
        "dates": {
            _WEEKDAY[valid_times[s].weekday()]: valid_times[s].date().isoformat()
            for s in available
        },
        "slot_order": [slot_label(valid_times[s]) for s in available],
        "slots": slots_out,
        "airports": airports_out,
        "best_slots": best,
        "trend": trend,
        "models_per_slot": model_counts,
        "missing_slots": [f"D+{d} {h:02d}Z" for d, h in q.slots if (d, h) not in available],
    }

    # Out-and-back pairs (needs a departure and destination).
    dep = q.departure or q.base
    dest = q.destination
    if dep is not None and dest is not None:
        result["pairs"], result["round_trip"] = _pairs(q, dep, dest, available, grades, valid_times)
        if map_base_url:
            by_label = {slot_label(valid_times[s]): s for s in available}
            for p in result["pairs"]:
                p["map_url"] = map_url(map_base_url, q, by_label[p["out"]])

    # The forecast map at the best slot (or the first one), framed on the query.
    if map_base_url and available:
        top = ranked[0] if best else available[0]
        result["map_url"] = map_url(map_base_url, q, top)
    return result


def _pairs(
    q: OutlookQuery, dep: Place, dest: Place,
    available: list[tuple[int, int]],
    grades: dict[tuple[int, int], dict[str, dict[str, Any]]],
    valid_times: dict[tuple[int, int], datetime],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Same-day (outbound, return) slot pairs where both legs are flyable.

    Leg time is great-circle distance at ``tas_kt``. A leg is graded at its
    take-off slot for the departure end and the corridor, and at the slot
    nearest the arrival time for the landing end. The return must start no
    earlier than the outbound arrival.
    """
    leg_h = distance_nm(dep.lat, dep.lon, dest.lat, dest.lon) / max(q.tas_kt, 1.0)
    corridor = [c.icao for c in q.candidates] if q.mode == "route" else []

    def leg(slot: tuple[int, int], frm: str, to: str) -> tuple[str | None, str]:
        g = grades[slot]
        arr_slot = _nearest_slot(available, slot[0], slot[1] + leg_h) or slot
        pts = [g.get(frm), grades[arr_slot].get(to)]
        cp = corridor_point([g[c] for c in corridor if c in g])
        if cp is not None:
            pts.append(cp)
        conf_pts = [p for p in pts if p and p.get("grade")]
        conf = max((p["conf"] for p in conf_pts), key=lambda c: _CONF_RANK[c]) if conf_pts else "low"
        return _leg_ok(*pts), conf

    out_ok = {s: leg(s, dep.icao, dest.icao) for s in available}
    back_ok = {s: leg(s, dest.icao, dep.icao) for s in available}

    pairs: list[tuple[tuple, dict[str, Any]]] = []
    round_trip: dict[str, str] = {}
    for d in sorted({s[0] for s in available}):
        ds = [s for s in available if s[0] == d]
        day_name = _WEEKDAY[valid_times[ds[0]].weekday()]
        any_out = any(out_ok[s][0] for s in ds)
        any_back = any(back_ok[s][0] for s in ds)
        found = False
        for o in ds:
            og, oc = out_ok[o]
            if not og:
                continue
            for r in ds:
                rg, rc = back_ok[r]
                if not rg or r[1] < o[1] + leg_h:
                    continue
                found = True
                worst = _worse(og, rg)
                conf = max((oc, rc), key=lambda c: _CONF_RANK[c])
                pairs.append((
                    (_GRADE_RANK[worst], _CONF_RANK[conf], d, o[1]),
                    {"out": slot_label(valid_times[o]), "back": slot_label(valid_times[r]),
                     "grade": worst, "conf": conf},
                ))
        if found:
            round_trip[day_name] = "possible"
        elif any_out:
            round_trip[day_name] = "outbound only"
        elif any_back:
            round_trip[day_name] = "return only"
        else:
            round_trip[day_name] = "no"
    pairs.sort(key=lambda p: p[0])
    # One pair per outbound slot is enough to read; keep the best few.
    seen: set[str] = set()
    top: list[dict[str, Any]] = []
    for _, p in pairs:
        if p["out"] in seen:
            continue
        seen.add(p["out"])
        top.append(p)
        if len(top) == 5:
            break
    return top, round_trip


# ---------------------------------------------------------------------------
# Forecast-map deep links
# ---------------------------------------------------------------------------

# Leaflet: 256 px tiles, a world of 256·2^z px is 21600 NM of longitude. Size
# the zoom so the area spans about this many pixels of the viewer's map.
_MAP_VIEW_PX = 700.0
_MAP_MIN_ZOOM, _MAP_MAX_ZOOM = 4, 10


def map_view(q: OutlookQuery) -> str:
    """``lat,lon,zoom`` (the web/iOS ``fc.view`` format) framing the query."""
    if q.mode == "radius":
        lat, lon = q.base.lat, q.base.lon
        span_nm = 2 * (q.radius_nm or DEFAULT_RADIUS_NM)
    else:
        lats = [p[0] for p in q.route_points]
        lons = [p[1] for p in q.route_points]
        lat, lon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
        span_nm = max(
            (max(lats) - min(lats)) * 60.0,
            (max(lons) - min(lons)) * 60.0 * math.cos(math.radians(lat)),
        ) + 2 * (q.corridor_nm or DEFAULT_CORRIDOR_NM)
    px_per_nm_z0 = 256.0 / (21600.0 * math.cos(math.radians(lat)))
    zoom = math.floor(math.log2(_MAP_VIEW_PX / (max(span_nm, 1.0) * px_per_nm_z0)))
    zoom = max(_MAP_MIN_ZOOM, min(_MAP_MAX_ZOOM, zoom))
    return f"{lat:.2f},{lon:.2f},{zoom}"


def map_url(base_url: str, q: OutlookQuery, slot: tuple[int, int]) -> str:
    """Forecast-map link at ``slot``, framed on the query, panel open.

    Keys are the forecast tab's URL contract (``web/ts/visualization/
    forecast-url.ts``, also read by the iOS app). ``fc.day`` is relative to
    the day the link is opened, like every forecast-map link: a link opened
    on a later day shows a later date.
    """
    from urllib.parse import urlencode

    params = {"tab": "forecast", "fc.day": slot[0], "fc.hour": slot[1]}
    if q.consensus != "worst":
        params["fc.model"] = q.consensus
    focus = q.destination or q.base
    if focus is not None:
        params["fc.apt"] = focus.icao
    params["fc.view"] = map_view(q)
    return f"{base_url.rstrip('/')}/maps.html?{urlencode(params, safe=',')}"


# ---------------------------------------------------------------------------
# I/O shell
# ---------------------------------------------------------------------------

LEGEND = (
    "airport row (airports[].rows follow slot_order): GRADE CAT ceiling vis "
    "wind[Ggust] xw<crosswind> conf [night|twilight]; "
    "ceiling 'nocig' = no ceiling; vis capped at 9999m; "
    "conf from model agreement (divergent = low)"
)

NOTE = (
    "Indicative only: point forecasts at monitored airports from the forecast "
    "map's 3-hourly snapshots. Nothing between airports is assessed, and "
    "operational suitability (hours, PPR, customs, NOTAMs) is not checked. "
    "This is not a briefing."
)

NEXT_STEP = (
    "To confirm a slot, call create_flight with that departure time for the "
    "full route briefing (en-route cloud, icing, convection, advisories)."
)

IFR_SCOPE_NOTE = (
    "IFR grades are terminal only: map snapshots carry no freezing level or "
    "cloud tops, so en-route icing is NOT assessed."
)


class OutlookInputError(ValueError):
    """Bad query (unknown airport, empty route, nothing monitored nearby)."""


def _airport_coords(icao: str, airports_db_path: str) -> tuple[float, float] | None:
    from weatherbrief.airports import _load_airport_model

    apt = _load_airport_model(airports_db_path).airports.get(icao)
    if apt is None or apt.latitude_deg is None:
        return None
    return float(apt.latitude_deg), float(apt.longitude_deg)


def _place(
    icao: str, lat: float, lon: float, coords: dict[str, tuple[float, float]],
) -> Place:
    if icao in coords:
        return Place(icao, *coords[icao])
    near = nearest_monitored(lat, lon, coords)
    if near is None:
        raise OutlookInputError(f"No monitored airport near {icao}")
    m_icao, d = near
    return Place(m_icao, *coords[m_icao], requested=icao, distance_nm=d)


def _route_points(route: str, airports_db_path: str) -> tuple[list[tuple[str, float, float]], list[str]]:
    """(code, lat, lon) for the route's points, plus codes that were dropped.

    Uses the same Field-15 tokenizer + resolver as ``interpret_route``, so a
    filed route (SIDs, airways, DCT, speed/level groups) reads the same here.
    """
    from euro_aip.models.field15 import TokenKind, parse_field15

    from weatherbrief.airports import resolve_waypoints

    codes: list[str] = []
    dropped: list[str] = []
    for t in parse_field15(route.upper()):
        if t.kind in (TokenKind.WAYPOINT, TokenKind.COORDINATE):
            if not codes or codes[-1] != t.value:
                codes.append(t.value)
        elif t.kind is TokenKind.UNKNOWN:
            dropped.append(t.value)
    if len(codes) < 2:
        raise OutlookInputError("route needs a departure and a destination airport")
    try:
        wps, rejected = resolve_waypoints(codes, airports_db_path)
    except KeyError as e:
        raise OutlookInputError(str(e).strip("'\"")) from e
    dropped += [r.name for r in rejected]
    return [(w.icao, w.lat, w.lon) for w in wps], dropped


def resolve_slots(days: list[int], hours: list[int] | None) -> list[tuple[int, int]]:
    """(day, hour) pairs on the map grid; out-of-grid hours snap like the map."""
    from weatherbrief.tasks.forecast_grid import MAX_FORECAST_DAY, sample_hours_for_day

    out: list[tuple[int, int]] = []
    for d in sorted(set(days)):
        if d < 0 or d > MAX_FORECAST_DAY:
            raise OutlookInputError(f"day must be 0..{MAX_FORECAST_DAY}")
        offered = sample_hours_for_day(d)
        want = offered if not hours else sorted({min(offered, key=lambda h: abs(h - x)) for x in hours})
        out.extend((d, h) for h in want)
    return out


def resolve_query(
    *,
    airports_db_path: str,
    coords: dict[str, tuple[float, float]],
    days: list[int],
    hours: list[int] | None = None,
    base: str | None = None,
    radius_nm: float | None = None,
    destination: str | None = None,
    route: str | None = None,
    corridor_nm: float | None = None,
    rules: Rules = "vfr",
    consensus: Mode = "worst",
    min_ceiling_ft: float | None = None,
    min_visibility_m: float | None = None,
    max_crosswind_kt: float | None = None,
    max_gust_kt: float | None = None,
    tas_kt: float | None = None,
) -> OutlookQuery:
    if bool(base) == bool(route):
        raise OutlookInputError("give exactly one of base (+radius) or route")
    if not days:
        raise OutlookInputError("days is required, e.g. [3, 4]")
    minima = minima_for(
        rules, min_ceiling_ft=min_ceiling_ft, min_visibility_m=min_visibility_m,
        max_crosswind_kt=max_crosswind_kt, max_gust_kt=max_gust_kt,
    )
    q = OutlookQuery(
        mode="radius" if base else "route", rules=rules, consensus=consensus,
        minima=minima, slots=resolve_slots(days, hours),
        tas_kt=tas_kt or DEFAULT_TAS_KT,
    )
    if base:
        icao = base.strip().upper()
        ll = coords.get(icao) or _airport_coords(icao, airports_db_path)
        if ll is None:
            raise OutlookInputError(f"Unknown airport: {icao}")
        q.base = _place(icao, *ll, coords)
        q.radius_nm = radius_nm or DEFAULT_RADIUS_NM
        exclude = {q.base.icao}
        if destination:
            d_icao = destination.strip().upper()
            dll = coords.get(d_icao) or _airport_coords(d_icao, airports_db_path)
            if dll is None:
                raise OutlookInputError(f"Unknown airport: {d_icao}")
            q.destination = _place(d_icao, *dll, coords)
            exclude.add(q.destination.icao)
        q.candidates = select_radius(q.base, q.radius_nm, coords, exclude)
    else:
        pts, dropped = _route_points(route, airports_db_path)
        q.dropped_waypoints = dropped
        q.departure = _place(pts[0][0], pts[0][1], pts[0][2], coords)
        q.destination = _place(pts[-1][0], pts[-1][1], pts[-1][2], coords)
        q.route_points = [(lat, lon) for _, lat, lon in pts]
        q.corridor_nm = corridor_nm or DEFAULT_CORRIDOR_NM
        q.candidates = select_corridor(
            q.route_points, q.corridor_nm, coords,
            exclude={q.departure.icao, q.destination.icao},
        )
    return q


def describe_query(q: OutlookQuery) -> dict[str, Any]:
    m = q.minima
    out: dict[str, Any] = {
        "mode": q.mode,
        "rules": q.rules,
        "consensus": q.consensus,
        "minima": {
            "ceiling_go_ft": m.ceiling_go_ft, "ceiling_nogo_below_ft": m.ceiling_floor_ft,
            "vis_go_m": m.vis_go_m, "vis_nogo_below_m": m.vis_floor_m,
            "crosswind_marginal_kt": m.crosswind_green_kt, "crosswind_nogo_kt": m.crosswind_red_kt,
            "gust_marginal_kt": m.gust_green_kt, "gust_nogo_kt": m.gust_red_kt,
        },
    }
    if q.rules == "ifr":
        out["minima"]["ifr_cloud"] = (
            "per airport: NO-GO below estimated approach minima, MARGINAL when a "
            "destination alternate would be required, VFR lines if no approach"
        )
        out["scope"] = "terminal_only"
    if q.mode == "radius":
        out["base"] = q.base.describe()
        out["radius_nm"] = q.radius_nm
    else:
        out["departure"] = q.departure.describe()
        out["corridor_nm"] = q.corridor_nm
        out["route_nm"] = round(route_track(q.route_points).total_nm)
        if q.dropped_waypoints:
            out["dropped_waypoints"] = q.dropped_waypoints
    if q.destination is not None:
        out["destination"] = q.destination.describe()
        dep = q.departure or q.base
        out["leg_time_h"] = round(
            distance_nm(dep.lat, dep.lon, q.destination.lat, q.destination.lon) / q.tas_kt, 1,
        )
        out["tas_kt"] = q.tas_kt
    return out


def build_area_outlook(
    db: Any,
    airports_db_path: str,
    *,
    days: list[int],
    hours: list[int] | None = None,
    base: str | None = None,
    radius_nm: float | None = None,
    destination: str | None = None,
    route: str | None = None,
    corridor_nm: float | None = None,
    rules: Rules = "vfr",
    consensus: Mode = "worst",
    min_ceiling_ft: float | None = None,
    min_visibility_m: float | None = None,
    max_crosswind_kt: float | None = None,
    max_gust_kt: float | None = None,
    tas_kt: float | None = None,
    map_base_url: str | None = None,
) -> dict[str, Any]:
    """Resolve, load each slot (cache first) and compute the outlook.

    ``map_base_url`` (the web app's origin) adds forecast-map ``map_url``
    links; see :func:`map_url`.

    Plain function, no FastAPI dependencies: the ``/maps`` and ``/agent/v1``
    endpoints both call it. Raises :class:`OutlookInputError` on bad input.
    """
    from weatherbrief.tasks.forecast_grid import forecast_hour_for
    from weatherbrief.tasks.map_queries import load_forecast_slot, watchlist_coords

    coords = watchlist_coords(airports_db_path)
    q = resolve_query(
        airports_db_path=airports_db_path, coords=coords, days=days, hours=hours,
        base=base, radius_nm=radius_nm, destination=destination, route=route,
        corridor_nm=corridor_nm, rules=rules, consensus=consensus,
        min_ceiling_ft=min_ceiling_ft, min_visibility_m=min_visibility_m,
        max_crosswind_kt=max_crosswind_kt, max_gust_kt=max_gust_kt, tas_kt=tas_kt,
    )
    slot_data = {s: load_forecast_slot(db, s[0], s[1], airports_db_path) for s in q.slots}
    valid_times = {s: forecast_hour_for(*s) for s in q.slots}
    result = {
        "query": describe_query(q),
        **compute_outlook(q, slot_data, valid_times, map_base_url=map_base_url),
    }
    result["legend"] = LEGEND
    result["note"] = NOTE + (" " + IFR_SCOPE_NOTE if rules == "ifr" else "")
    result["next_step"] = NEXT_STEP
    return result
