"""The Observed tab's nutshell, route ribbon and map focus (#690).

Built once per live tick from the layer's own blocks (route METAR/TAF, route
SIGMETs, observed radar/lightning, radar storms, the change rows) and stored
on ``LiveLayer.glance`` / ``LiveLayer.ribbon``, so iOS, web and the agent
``live`` block show the same text (``designs/future/observed-tab-presentation.md``
§3–5). Clients render; they don't derive.

Rules that hold everywhere here:

- **Observations only.** Observed motion is shown ("moving away 11 kt"); the
  closest-approach estimate (``LiveStorm.estimate``) never appears in a line.
  Planned ETAs (on-time departure, constant speed) are the plan, not a
  projection, and are shown.
- **Missing is "unavailable", never "clear".** A dark cells feed, a missing
  METAR, a SIGMET fetch that never ran: each is said, and listed in
  ``LiveGlanceLine.unavailable``.
- **Counts are storms**, the lineage groups of ``observed/storms.py``, never
  threshold tiers.
- **No verdict.** The headline counts what moved since the briefing, from
  the classifier's own rows; nothing here re-grades or adds a tier.

Pure: no I/O. :func:`build_glance` never raises into the tick (the caller
logs and carries on without the block).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from euro_aip.utils.geometry import haversine_nm

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.live import (
    ChangeRole,
    LiveChanges,
    LiveFocus,
    LiveGlance,
    LiveGlanceLine,
    LiveLayer,
    LiveRibbon,
    LiveStorm,
    LiveStorms,
    RibbonSegment,
    RibbonSigmet,
    RibbonStation,
    RibbonWaypoint,
)
from weatherbrief.models.observations import AirportObservation, SigmetAlongRoute
from weatherbrief.models.observed import ObservedConditions
from weatherbrief.observed.intensity import classify_dbz, intensity_label

#: Storms and lightning "at" an airport: within this distance of it. The
#: widest radar/lightning ring the sampler draws (``DEFAULT_RADII_NM``), so
#: the storm clause and the lightning clause speak about the same disc.
TERMINAL_NM = 20.0

#: Radar and lightning per ribbon segment: the ring read around each route
#: point. 10 NM is the storm alert band (§41): what is near the track.
RIBBON_RADAR_RADIUS_NM = 10.0

#: Ribbon segments: about this long, at most :data:`RIBBON_MAX_SEGMENTS`
#: (a 600 NM route gets 20 NM stretches).
RIBBON_SEGMENT_NM = 10.0
RIBBON_MAX_SEGMENTS = 30

#: A block older than this at the tick is flagged inline ("METARs as of
#: 13:50Z"): a fetch failed and the layer kept the last good one.
STALE_BLOCK = timedelta(minutes=30)

#: A METAR older than this is flagged with its time ("METAR 12:50Z").
OLD_METAR = timedelta(minutes=75)

#: SIGMETs named in the en-route line; the rest are "+N more".
GLANCE_MAX_SIGMETS = 2

#: Half-width floor of a focus box, NM: a storm or station alone still opens
#: on a readable map, not a one-pixel zoom.
FOCUS_PAD_NM = 10.0

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_COMPASS_DEG = {c: i * 45.0 for i, c in enumerate(_COMPASS)}
_COMPASS_DEG.update({
    "NNE": 22.5, "ENE": 67.5, "ESE": 112.5, "SSE": 157.5,
    "SSW": 202.5, "WSW": 247.5, "WNW": 292.5, "NNW": 337.5,
})
#: SIGMET movement below this component toward/away from the track reads
#: as moving along it (the storms' ``PARALLEL_KT`` is 3 kt; SIGMET MOV
#: speeds are coarse, 5 kt steps).
SIGMET_PARALLEL_KT = 3.0

_STORM_LAYERS = ["route", "radar", "cells", "lightning"]
_SIGMET_LAYERS = ["route", "sigmets"]
_STATION_LAYERS = ["route", "metar"]
_SEGMENT_LAYERS = ["route", "radar", "cells", "lightning", "sigmets"]


# --- Small helpers ------------------------------------------------------------


def _hhmm(t: datetime | None) -> str:
    return t.astimezone(timezone.utc).strftime("%H:%MZ") if t is not None else ""


def _compass(bearing: float) -> str:
    return _COMPASS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _range(lo: float, hi: float) -> str:
    return f"{lo:.0f}" if round(lo) == round(hi) else f"{lo:.0f}–{hi:.0f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _bbox(points: list[tuple[float, float]], pad_nm: float = FOCUS_PAD_NM) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) around (lat, lon) points, padded
    by ``pad_nm`` on every side."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    mid = (min(lats) + max(lats)) / 2.0
    dlat = pad_nm / 60.0
    dlon = pad_nm / (60.0 * max(math.cos(math.radians(mid)), 1e-6))
    return (
        round(min(lons) - dlon, 4), round(min(lats) - dlat, 4),
        round(max(lons) + dlon, 4), round(max(lats) + dlat, 4),
    )


class _Plan:
    """The route and the planned schedule the tick measured against."""

    def __init__(self, route: RouteConfig, departure: datetime | None, now: datetime):
        self.route = route
        self.track = RouteTrack.from_route(route)
        self.total_nm = self.track.total_nm
        self.departure = departure
        self.duration_h = route.flight_duration_hours or 0.0
        self.now = now
        self.dep_icao = route.waypoints[0].icao.upper()
        self.dest_icao = route.waypoints[-1].icao.upper()
        self.dep_pos = (route.waypoints[0].lat, route.waypoints[0].lon)
        self.dest_pos = (route.waypoints[-1].lat, route.waypoints[-1].lon)

    @property
    def timed(self) -> bool:
        return self.departure is not None and self.duration_h > 0 and self.total_nm > 0

    def eta(self, along_nm: float | None) -> datetime | None:
        if not self.timed or along_nm is None:
            return None
        frac = max(0.0, min(1.0, along_nm / self.total_nm))
        return self.departure + timedelta(hours=frac * self.duration_h)

    @property
    def arrival(self) -> datetime | None:
        return self.departure + timedelta(hours=self.duration_h) if self.timed else None

    @property
    def flown_nm(self) -> float | None:
        if not self.timed:
            return None
        frac = (self.now - self.departure).total_seconds() / 3600.0 / self.duration_h
        return max(0.0, min(1.0, frac)) * self.total_nm

    @property
    def departed(self) -> bool:
        return self.departure is not None and self.now >= self.departure

    @property
    def arrived(self) -> bool:
        return self.arrival is not None and self.now >= self.arrival

    def route_points(self, lo_nm: float, hi_nm: float) -> list[tuple[float, float]]:
        """The track between two along-route distances, ends included."""
        pts = [self.track.position_at(lo_nm)]
        pts += [p for p, d in zip(self.track.points, self.track.distances) if lo_nm < d < hi_nm]
        pts.append(self.track.position_at(hi_nm))
        return pts


# --- Focus ------------------------------------------------------------------


def storm_focus(storm: LiveStorm, track: RouteTrack, frame_time: datetime | None) -> LiveFocus:
    """The storm and the track point abeam it, so the map shows the gap."""
    proj = track.project(storm.lat, storm.lon)
    return LiveFocus(
        kind="storm", id=storm.id,
        bbox=_bbox([(storm.lat, storm.lon), (proj.lat, proj.lon)]),
        layers=list(_STORM_LAYERS), time=frame_time,
    )


def _sigmet_focus(key: str, s: SigmetAlongRoute, now: datetime) -> LiveFocus | None:
    if not s.coords:
        return None
    return LiveFocus(
        kind="sigmet", id=key,
        # SIGMET coords are (lon, lat); the area itself frames the map.
        bbox=_bbox([(c[1], c[0]) for c in s.coords], pad_nm=0.0),
        layers=list(_SIGMET_LAYERS),
        # A pending SIGMET opens at its start.
        time=s.valid_from if s.valid_from is not None and s.valid_from > now else None,
    )


def _station_focus(icao: str, lat: float, lon: float, metar_time: datetime | None) -> LiveFocus:
    return LiveFocus(kind="station", id=icao, bbox=_bbox([(lat, lon)]),
                     layers=list(_STATION_LAYERS), time=metar_time)


def _segment_focus(index: int, plan: _Plan, lo: float, hi: float, time: datetime | None) -> LiveFocus:
    return LiveFocus(kind="segment", id=f"seg:{index}", bbox=_bbox(plan.route_points(lo, hi)),
                     layers=list(_SEGMENT_LAYERS), time=time)


def _terminal_focus(plan: _Plan, phase: str) -> LiveFocus:
    """A departure / arrival line opens the map on the airport's disc."""
    icao, (lat, lon) = (plan.dep_icao, plan.dep_pos) if phase == "departure" else (plan.dest_icao, plan.dest_pos)
    return LiveFocus(kind="station", id=icao, bbox=_bbox([(lat, lon)], pad_nm=TERMINAL_NM),
                     layers=list(_SEGMENT_LAYERS), time=None)


# --- Observed-conditions readers ------------------------------------------------


def _ring(annuli, radius_nm: float):
    """The annulus at ``radius_nm``, else the nearest smaller, else None."""
    fits = [a for a in annuli if a.radius_nm <= radius_nm + 1e-6]
    return max(fits, key=lambda a: a.radius_nm) if fits else None


def _exact_ring(annuli, radius_nm: float):
    """The annulus at exactly ``radius_nm``, else None: the ribbon states its
    radius (``LiveRibbon.radar_radius_nm``), so it never reads another."""
    return next((a for a in annuli if abs(a.radius_nm - radius_nm) < 1e-6), None)


def _station_along(observed: ObservedConditions | None) -> dict[str, float]:
    if observed is None:
        return {}
    return {s.id: s.enroute_distance_nm for s in observed.stations if s.enroute_distance_nm is not None}


def _terminal_station(observed: ObservedConditions | None, along_nm: float) -> str | None:
    """The sampled route point nearest an airport along track (the route's
    first / last point, normally at 0 / route length)."""
    pos = _station_along(observed)
    if not pos:
        return None
    sid, d = min(pos.items(), key=lambda kv: abs(kv[1] - along_nm))
    return sid if abs(d - along_nm) <= TERMINAL_NM else None


def _lightning_at(observed: ObservedConditions | None, station_id: str | None) -> tuple[str, bool]:
    """The terminal lightning clause, and whether it is unavailable."""
    field = observed.lightning if observed is not None else None
    if field is None or station_id is None:
        return "lightning unavailable", True
    samples = next((s for s in field.stations if s.station_id == station_id), None)
    ring = _ring(samples.annuli, TERMINAL_NM) if samples is not None else None
    if ring is None:
        # Outside the imager's disc: the field leaves the station out.
        return "lightning unavailable", True
    if not ring.flash_count:
        return f"no lightning ≤{ring.radius_nm:.0f} NM", False
    near = f", nearest {ring.nearest_flash_nm:.0f} NM" if ring.nearest_flash_nm is not None else ""
    flashes = f"{ring.flash_count} flash" + ("" if ring.flash_count == 1 else "es")
    return f"lightning ≤{ring.radius_nm:.0f} NM ({flashes}{near})", False


# --- Nutshell clauses ---------------------------------------------------------


#: Convective tags in severity order, for every surface that lists them.
_CONVECTIVE_ORDER = ("TCU", "CB", "VCTS", "TS")


def _convective(obs: AirportObservation) -> list[str]:
    """The METAR's observed convection, mildest first ("CB TS")."""
    from weatherbrief.tasks.live_significance import convective_tags

    return sorted(convective_tags(obs), key=_CONVECTIVE_ORDER.index)


def _metar_clause(icao: str, obs: AirportObservation | None, now: datetime) -> tuple[str, bool, list[str]]:
    """"LPPR VFR TS" (category + observed convection), or unavailable."""
    if obs is None or not (obs.has_metar or obs.metar_raw):
        return f"{icao} METAR unavailable", True, []
    cat = obs.metar_flight_category or "category unknown"
    tags = _convective(obs)
    text = f"{icao} {cat}" + (f" {' '.join(tags)}" if tags else "")
    if obs.metar_time is not None and now - obs.metar_time > OLD_METAR:
        text += f" (METAR {_hhmm(obs.metar_time)})"
    return text, False, [f"metar:{icao}"]


def _taf_text(obs: AirportObservation) -> str | None:
    """"TAF at ETA VFR, PROB30 MVFR TS CB" or None when no TAF covers it."""
    if not obs.has_taf and not obs.taf_raw:
        return None
    if obs.taf_valid_at_eta is False or obs.taf_flight_category_at_eta is None:
        return None
    text = f"TAF at ETA {obs.taf_prevailing_category_at_eta or obs.taf_flight_category_at_eta}"
    if obs.taf_temporary_category_at_eta:
        text += f", {obs.taf_temporary_type or 'TEMPO'} {obs.taf_temporary_category_at_eta}"
    if obs.taf_significant_weather:
        text += " " + " ".join(obs.taf_significant_weather)
    return text


def _storm_motion_words(st: LiveStorm) -> str:
    if st.relative_motion == "closing" and st.closing_kt is not None:
        return f"closing {st.closing_kt:.0f} kt"
    if st.relative_motion == "moving_away" and st.closing_kt is not None:
        return f"moving away {-st.closing_kt:.0f} kt"
    if st.relative_motion == "parallel":
        return "moving along the track"
    if st.relative_motion == "stationary":
        return "nearly stationary"
    return "motion not yet measured"


def _storms_unavailable(storms: LiveStorms | None) -> str:
    if storms is not None and storms.status == "stale" and storms.unavailable_since is not None:
        return f"radar cells unavailable since {_hhmm(storms.unavailable_since)}"
    return "radar cells unavailable"


def _terminal_storms(
    storms: LiveStorms | None, pos: tuple[float, float],
) -> tuple[str, bool, list[LiveStorm]]:
    """"nearest cell 9 NM NE, moving away 11 kt" around an airport."""
    if storms is None or storms.status != "available":
        return _storms_unavailable(storms), True, []
    near = []
    for st in storms.storms:
        d = haversine_nm(pos[0], pos[1], st.lat, st.lon)
        if d <= TERMINAL_NM:
            near.append((d, st))
    if not near:
        return f"no cell within {TERMINAL_NM:.0f} NM", False, []
    near.sort(key=lambda x: x[0])
    d, st = near[0]
    where = f"{d:.0f} NM {_compass(_bearing(pos[0], pos[1], st.lat, st.lon))}"
    flash = ", lightning" if st.flashes else ""
    head = "nearest cell" if len(near) == 1 else f"{len(near)} cells ≤{TERMINAL_NM:.0f} NM, nearest"
    return (f"{head} {where} ({st.peak_dbz:.0f} dBZ{flash}), {_storm_motion_words(st)}",
            False, [s for _, s in near])


def _enroute_storms(storms: LiveStorms | None, plan: _Plan) -> tuple[str, bool, list[LiveStorm]]:
    """Storms ahead between the terminal discs, counted as storms."""
    if storms is None or storms.status != "available":
        return _storms_unavailable(storms), True, []
    if plan.arrived:
        # A tick after the planned landing: nothing is ahead any more.
        return "flight arrived at plan, no route ahead", False, []

    def terminal(st: LiveStorm) -> bool:
        return (
            haversine_nm(plan.dep_pos[0], plan.dep_pos[1], st.lat, st.lon) <= TERMINAL_NM
            or haversine_nm(plan.dest_pos[0], plan.dest_pos[1], st.lat, st.lon) <= TERMINAL_NM
        )

    ahead = [st for st in storms.storms if st.ahead and st.end is None and not terminal(st)]
    corridor = storms.corridor_nm
    if not ahead:
        return f"no cells within {corridor:.0f} NM of track ahead", False, []
    offs = [st.offtrack_nm for st in ahead]
    sides = {st.side for st in ahead if st.side}
    where = "either side" if len(sides) > 1 else (f"{next(iter(sides))} of track" if sides else "on track")
    text = f"{_plural(len(ahead), 'cell')} {_range(min(offs), max(offs))} NM {where}"
    motions = [st.relative_motion for st in ahead]
    if len(ahead) > 1 and all(m == "moving_away" for m in motions):
        text += ", all moving away"
    else:
        closing = sum(1 for m in motions if m == "closing")
        if closing:
            text += f", {closing} closing"
    lit = sum(1 for st in ahead if st.flashes)
    if lit:
        text += f", {lit} with lightning"
    nearest = min(ahead, key=lambda st: st.offtrack_nm)
    eta = f" ~{_hhmm(nearest.abeam_eta)}" if nearest.abeam_eta is not None else ""
    side = f" {nearest.side}" if nearest.side else ""
    text += (f"; nearest {nearest.offtrack_nm:.0f} NM{side} at {nearest.along_nm:.0f} NM{eta}"
             f" ({nearest.peak_dbz:.0f} dBZ), {_storm_motion_words(nearest)}")
    return text, False, ahead


def _sigmet_clause(
    layer: LiveLayer, plan: _Plan, ribbon_sigmets: list[RibbonSigmet], stale: bool,
) -> tuple[str, bool, list[str]]:
    if layer.route_sigmets is None:
        return "SIGMETs unavailable", True, []
    listed = list(ribbon_sigmets)
    suffix = f" (SIGMETs as of {_hhmm(layer.sigmets_updated_at)})" if stale else ""
    if not listed:
        return "no SIGMET on route" + suffix, False, []
    listed.sort(key=lambda s: (s.from_nm if s.from_nm is not None else math.inf))
    parts = []
    for s in listed[:GLANCE_MAX_SIGMETS]:
        hazard = " ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET"
        name = s.label.split(":")[0]
        if s.from_nm is not None and s.to_nm is not None:
            if s.from_nm <= 1.0 and s.to_nm >= plan.total_nm - 1.0:
                span = "covers the whole route"
            elif s.from_nm <= 1.0:
                span = f"covers first {s.to_nm:.0f} NM"
            elif s.to_nm >= plan.total_nm - 1.0:
                span = f"covers last {plan.total_nm - s.from_nm:.0f} NM"
            else:
                span = f"covers {_range(s.from_nm, s.to_nm)} NM"
        elif s.min_distance_nm is not None:
            span = f"{s.min_distance_nm:.0f} NM off route"
        else:
            span = "near route"
        text = f"{hazard} SIGMET {name} {span}"
        if s.pending and s.valid_from is not None:
            text += f" from {_hhmm(s.valid_from)}"
        if s.motion in ("toward", "away"):
            text += f", moving {s.motion}" + (" the route" if s.motion == "toward" else "")
        if s.new is True:
            text += " (new)"
        elif s.new is False:
            text += " (briefed)"
        parts.append(text)
    more = len(listed) - GLANCE_MAX_SIGMETS
    if more > 0:
        parts.append(f"+{_plural(more, 'more SIGMET')}")
    return ", ".join(parts) + suffix, False, [s.id for s in listed]


_PHASE_OF_ROLE: dict[str, str] = {
    "departure": "departure", "destination": "arrival", "alternate": "arrival", "route": "enroute",
}
_PHASE_WORDS = {"departure": "departure", "enroute": "en route", "arrival": "arrival"}


def _headline(changes: LiveChanges | None, as_of: datetime) -> tuple[str, str]:
    """"Observed 14:29Z · as briefed, departure improving"."""
    head = f"Observed {_hhmm(as_of)}"
    if changes is None:
        return f"{head} · comparison with the briefing unavailable", "unavailable"
    since = "since the briefing" if changes.baseline_source == "briefing" else "since live tracking began"
    worse: list[str] = []
    better: list[str] = []
    reissued = updated = 0
    for c in changes.changes:
        phase = _PHASE_OF_ROLE.get(c.role, "enroute")
        if c.direction == "worse" and phase not in worse:
            worse.append(phase)
        elif c.direction == "better" and phase not in better:
            better.append(phase)
        elif c.direction == "updated":
            if c.kind == "sigmet_issued":
                reissued += 1
            else:
                updated += 1
    order = ("departure", "enroute", "arrival")
    worse.sort(key=order.index)
    better.sort(key=order.index)
    parts = []
    if worse:
        n = changes.worsened_count
        parts.append(f"{n} worse {since} ({', '.join(_PHASE_WORDS[p] for p in worse)})")
    elif not better:
        parts.append("as briefed" if changes.baseline_source == "briefing" else f"no significant change {since}")
    else:
        parts.append("as briefed" if changes.baseline_source == "briefing" else f"nothing worse {since}")
    if better:
        parts.append(f"{' and '.join(_PHASE_WORDS[p] for p in better)} improving")
    if reissued:
        parts.append(f"{_plural(reissued, 'SIGMET')} reissued")
    if updated:
        parts.append(f"{updated} updated")
    comparison = "mixed" if worse and better else "worse" if worse else "better" if better else "as_briefed"
    return f"{head} · {', '.join(parts)}", comparison


# --- Ribbon -------------------------------------------------------------------


def _sigmet_motion(s: SigmetAlongRoute, track: RouteTrack) -> str:
    """The SIGMET's MOV against the track, from its area's centre."""
    if not s.coords:
        return "unknown"
    if not s.direction or not s.speed_kt:
        # euro_aip: direction None = stationary (STNR).
        return "stationary" if s.direction is None else "unknown"
    deg = _COMPASS_DEG.get(s.direction.upper())
    if deg is None:
        return "unknown"
    # coords are (lon, lat).
    lon = sum(c[0] for c in s.coords) / len(s.coords)
    lat = sum(c[1] for c in s.coords) / len(s.coords)
    proj = track.project(lat, lon)
    if proj.offtrack_nm <= 1e-6:
        return "parallel"
    away = math.radians(_bearing(proj.lat, proj.lon, lat, lon))
    closing = -s.speed_kt * math.cos(math.radians(deg) - away)
    if abs(closing) < SIGMET_PARALLEL_KT:
        return "parallel"
    return "toward" if closing > 0 else "away"


def _ribbon_sigmets(layer: LiveLayer, plan: _Plan) -> list[RibbonSigmet]:
    from weatherbrief.tasks.live_significance import _sigmet_key_str, _sigmet_label

    if layer.route_sigmets is None:
        return []
    new_keys = (
        set(layer.changes.new_sigmets)
        if layer.changes is not None and layer.changes.new_sigmets is not None else None
    )
    out = []
    seen: set[str] = set()
    for s in layer.route_sigmets.sigmets:
        key = _sigmet_key_str(s)
        if key in seen:
            continue
        seen.add(key)
        out.append(RibbonSigmet(
            id=key, label=_sigmet_label(s), hazard=s.hazard, qualifier=s.qualifier,
            from_nm=round(s.enroute_distance_from_nm, 1) if s.enroute_distance_from_nm is not None else None,
            to_nm=round(s.enroute_distance_to_nm, 1) if s.enroute_distance_to_nm is not None else None,
            min_distance_nm=round(s.min_distance_nm, 1) if s.min_distance_nm is not None else None,
            valid_from=s.valid_from, valid_to=s.valid_to,
            pending=s.valid_from is not None and s.valid_from > plan.now,
            new=(key in new_keys) if new_keys is not None else None,
            motion=_sigmet_motion(s, plan.track),
            focus=_sigmet_focus(key, s, plan.now),
        ))
    return out


def _ribbon_stations(layer: LiveLayer, plan: _Plan, roles: dict[str, ChangeRole]) -> list[RibbonStation]:
    obs = layer.route_observations
    if obs is None:
        return []
    out = []
    for a in obs.airports:
        if not (a.has_metar or a.metar_raw or a.has_taf or a.taf_raw):
            continue
        icao = a.icao.upper()
        along, cross = a.enroute_distance_nm, None
        if a.lat is not None and a.lon is not None:
            proj = plan.track.project(a.lat, a.lon)
            cross = round(proj.cross_nm, 1)
            if along is None:
                along = proj.along_nm
        role = roles.get(icao, "route")
        if role == "departure":
            along, cross = 0.0, 0.0
        elif role == "destination":
            along, cross = plan.total_nm, 0.0
        taf_ok = a.taf_valid_at_eta is not False
        out.append(RibbonStation(
            icao=icao, role=role,
            along_nm=round(along, 1) if along is not None else None, cross_nm=cross,
            eta=plan.eta(along),
            metar_category=a.metar_flight_category, metar_time=a.metar_time,
            convective=_convective(a) if (a.has_metar or a.metar_raw) else [],
            taf_category_at_eta=(a.taf_prevailing_category_at_eta or a.taf_flight_category_at_eta) if taf_ok else None,
            taf_temporary_type=a.taf_temporary_type if taf_ok and a.taf_temporary_category_at_eta else None,
            taf_temporary_category=a.taf_temporary_category_at_eta if taf_ok else None,
            taf_weather=list(a.taf_significant_weather) if taf_ok else [],
            focus=_station_focus(icao, a.lat, a.lon, a.metar_time) if a.lat is not None and a.lon is not None else None,
        ))
    out.sort(key=lambda s: (s.along_nm if s.along_nm is not None else math.inf))
    return out


def _ribbon_segments(
    layer: LiveLayer, plan: _Plan, sigmets: list[RibbonSigmet],
) -> tuple[list[RibbonSegment], float, datetime | None]:
    n = max(1, min(RIBBON_MAX_SEGMENTS, round(plan.total_nm / RIBBON_SEGMENT_NM)))
    seg = plan.total_nm / n
    observed = layer.observed_conditions
    along = _station_along(observed)
    refl = observed.reflectivity if observed is not None else None
    light = observed.lightning if observed is not None else None
    refl_by = {s.station_id: s for s in refl.stations} if refl is not None else {}
    light_by = {s.station_id: s for s in light.stations} if light is not None else {}
    radar_time = refl.valid_time if refl is not None else None
    storms = layer.storms.storms if layer.storms is not None and layer.storms.status == "available" else []

    out = []
    for i in range(n):
        lo, hi = i * seg, (i + 1) * seg
        last = i == n - 1
        ids = [sid for sid, d in along.items() if lo <= d < hi or (last and d == hi)]
        status, peak, lightning = "no_sample", None, None
        if refl is not None:
            rings = [_exact_ring(refl_by[sid].annuli, RIBBON_RADAR_RADIUS_NM) for sid in ids if sid in refl_by]
            rings = [r for r in rings if r is not None]
            if rings:
                covered = [r for r in rings if not r.insufficient_coverage]
                if covered:
                    status = "measured"
                    values = [r.max_value for r in covered if r.max_value is not None and r.detected_px > 0]
                    peak = round(max(values), 1) if values else None
                else:
                    status = "no_coverage"
        if light is not None:
            rings = [_exact_ring(light_by[sid].annuli, RIBBON_RADAR_RADIUS_NM) for sid in ids if sid in light_by]
            rings = [r for r in rings if r is not None]
            if rings:
                lightning = any(r.flash_count for r in rings)
        cls = classify_dbz(peak) if peak is not None else None
        out.append(RibbonSegment(
            index=i, from_nm=round(lo, 1), to_nm=round(hi, 1),
            eta_from=plan.eta(lo), eta_to=plan.eta(hi),
            radar_max_dbz=peak, radar_intensity=intensity_label(cls) if cls is not None else None,
            radar_status=status, lightning=lightning,
            sigmet_ids=[
                s.id for s in sigmets
                if s.from_nm is not None and s.to_nm is not None and s.from_nm <= hi and s.to_nm >= lo
            ],
            storm_ids=[st.id for st in storms if lo <= st.along_nm < hi or (last and st.along_nm >= hi)],
            focus=_segment_focus(i, plan, lo, hi, radar_time),
        ))
    return out, round(seg, 1), radar_time


# --- Entry point ----------------------------------------------------------------


def build_glance(
    layer: LiveLayer,
    route: RouteConfig,
    departure: datetime | None,
    *,
    alternate_icaos: list[str] | None = None,
    cell_frame: dict | None = None,
    now: datetime,
) -> tuple[LiveGlance, LiveRibbon]:
    """The nutshell and the ribbon for this tick, and ``focus`` on every
    storm of ``layer.storms`` (set in place, last, so a failure part-way
    leaves the storms untouched). ``now`` is the tick time: the glance's
    "as of". ``cell_frame`` is the cells feed's newest display file, the
    source of the ribbon's rain/core bands (used only while ``layer.storms``
    says the feed is available)."""
    from weatherbrief.observed.route_bands import BIN_NM, build_weather_bands
    from weatherbrief.tasks.live_significance import airport_roles

    plan = _Plan(route, departure, now)
    roles = airport_roles([wp.icao for wp in route.waypoints], alternate_icaos)

    storms = layer.storms
    focus_by_storm: dict[str, LiveFocus] = {}
    if storms is not None and storms.status == "available":
        focus_by_storm = {st.id: storm_focus(st, plan.track, storms.frame_time) for st in storms.storms}

    sigmets = _ribbon_sigmets(layer, plan)
    stations = _ribbon_stations(layer, plan, roles)
    segments, seg_nm, radar_time = _ribbon_segments(layer, plan, sigmets)
    ribbon = LiveRibbon(
        route_nm=round(plan.total_nm, 1),
        flown_nm=round(plan.flown_nm, 1) if plan.flown_nm is not None else None,
        departure_at=plan.departure, arrival_at=plan.arrival,
        segment_nm=seg_nm, radar_radius_nm=RIBBON_RADAR_RADIUS_NM, radar_time=radar_time,
        waypoints=[
            RibbonWaypoint(icao=wp.icao, along_nm=round(d, 1), eta=plan.eta(d))
            for wp, d in zip(route.waypoints, plan.track.distances)
        ],
        segments=segments, stations=stations, sigmets=sigmets,
        weather_status=storms.status if storms is not None else None,
        weather_corridor_nm=storms.corridor_nm if storms is not None else None,
        weather_bin_nm=BIN_NM,
    )
    if storms is not None and storms.status == "available":
        ribbon.weather = build_weather_bands(
            cell_frame, plan.track, storms.storms, corridor_nm=storms.corridor_nm,
        )

    by_icao = {
        a.icao.upper(): a for a in (layer.route_observations.airports if layer.route_observations else [])
    }
    obs_stale = (
        layer.observations_updated_at is not None and now - layer.observations_updated_at > STALE_BLOCK
    )
    sig_stale = layer.sigmets_updated_at is not None and now - layer.sigmets_updated_at > STALE_BLOCK
    alert_phases = {
        _PHASE_OF_ROLE.get(c.role, "enroute")
        for c in (layer.changes.changes if layer.changes else []) if c.tier == "alert"
    }

    lines: list[LiveGlanceLine] = []
    for phase, icao, pos, along in (
        ("departure", plan.dep_icao, plan.dep_pos, 0.0),
        ("arrival", plan.dest_icao, plan.dest_pos, plan.total_nm),
    ):
        clauses: list[str] = []
        unavailable: list[str] = []
        sources: list[str] = []
        obs = by_icao.get(icao)
        text, missing, src = _metar_clause(icao, obs, now)
        if obs_stale and not missing:
            text += f" (METARs as of {_hhmm(layer.observations_updated_at)})"
        clauses.append(text)
        sources += src
        if missing:
            unavailable.append("metar")
        if phase == "arrival":
            taf = _taf_text(obs) if obs is not None else None
            if taf is not None:
                clauses.append(taf)
                sources.append(f"taf:{icao}")
            else:
                clauses.append("no TAF for ETA")
                unavailable.append("taf")
        text, missing, near = _terminal_storms(storms, pos)
        clauses.append(text if phase == "departure" else (text + " now" if not missing else text))
        sources += [f"storm:{st.id}" for st in near]
        if missing:
            unavailable.append("storms")
        text, missing = _lightning_at(layer.observed_conditions, _terminal_station(layer.observed_conditions, along))
        clauses.append(text)
        if missing:
            unavailable.append("lightning")
        lines.append(LiveGlanceLine(
            phase=phase, icao=icao, text=" · ".join(clauses),
            alert=phase in alert_phases,
            passed=plan.departed if phase == "departure" else plan.arrived,
            unavailable=unavailable, sources=sources, focus=_terminal_focus(plan, phase),
        ))

    clauses, unavailable, sources = [], [], []
    text, missing, ahead = _enroute_storms(storms, plan)
    clauses.append(text)
    sources += [f"storm:{st.id}" for st in ahead]
    if missing:
        unavailable.append("storms")
    text, missing, ids = _sigmet_clause(layer, plan, sigmets, sig_stale)
    clauses.append(text)
    sources += ids
    if missing:
        unavailable.append("sigmets")
    enroute_focus = None
    if ahead:
        nearest = min(ahead, key=lambda st: st.offtrack_nm)
        enroute_focus = focus_by_storm.get(nearest.id)
    elif segments:
        lo = plan.flown_nm or 0.0
        enroute_focus = LiveFocus(
            kind="segment", id="seg:ahead", bbox=_bbox(plan.route_points(lo, plan.total_nm)),
            layers=list(_SEGMENT_LAYERS), time=radar_time,
        )
    lines.insert(1, LiveGlanceLine(
        phase="enroute", text=" · ".join(clauses), alert="enroute" in alert_phases,
        passed=plan.arrived, unavailable=unavailable, sources=sources, focus=enroute_focus,
    ))

    headline, comparison = _headline(layer.changes, now)
    glance = LiveGlance(as_of=now, headline=headline, comparison=comparison, lines=lines)
    if storms is not None:
        for st in storms.storms:
            st.focus = focus_by_storm.get(st.id)
    return glance, ribbon
