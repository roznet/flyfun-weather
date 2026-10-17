"""The route locator (#790): a route position in the pilot's words.

Live and observed text used to place things in the system's coordinates
("nearest 1 NM left at 143 NM ~10:24Z", "0–166 NM along route"), each surface
with its own wording. A pilot cannot map "143 NM along" to anything in the
cockpit. One locator, built once per route and tick, turns a position into
"10 NM before Middelburg (EHMZ)" so the same point reads the same way on the
glance, the changes, the highlight, the ribbon, the Radar & lightning cards
and the MCP ``live`` block.

**Anchors**, ordered along the route (the "ladder"):

1. the route's waypoints (lat/lon fixes like ``5117N00009E`` are not names
   and are skipped);
2. the METAR stations on the "Along the route" ribbon, so the words match what
   the pilot sees there and the anchor is also where they can read a METAR;
3. coast-out / coast-in points (``analysis/coastline.py``), so a sea crossing
   with no airfield on it still has something to read against.

**Rules** (the table in #790):

=========================================  ====================================
within ±:data:`NEAR_NM` of an anchor        "near Middelburg (EHMZ)"
nearest anchor within :data:`ANCHOR_NM`     "15 NM after Rijen" / "10 NM before
                                            Middelburg" (closer wins; one
                                            anchor per phrase)
over water, coast point nearest             "over the sea, 25 NM after
                                            coast-out (Middelburg)",
                                            "over the sea, mid-crossing"
land, nothing within ANCHOR_NM              "between Uden and Rijen"
before the departure / past the destination "29 NM east of departure",
                                            "15 NM past the destination"
lateral, past :data:`LATERAL_NM`            ", 5 NM left of track" (separate)
=========================================  ====================================

A named waypoint inside a crossing (an island field, a platform) is an anchor
like any other: within :data:`ANCHOR_NM` it wins "N NM after X" over the
over-the-sea wording, which only takes over beyond that.

Names: town + ICAO in ``text`` ("Middelburg (EHMZ)"), ICAO alone in
``short``. The town is nav.db's ``municipality``; without one the ICAO alone.

**Stability:** a moving storm would flip between "after Rijen" and "before
Hoeven" every tick at the midpoint. :meth:`RouteLocator.locate` takes the
previous place and keeps its anchor while it is within :data:`HYSTERESIS_NM`
of being the nearest, and keeps "near" until :data:`HYSTERESIS_NM` past the
near radius.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from euro_aip.utils.geometry import haversine_nm

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.live import AnchorKind, RouteAnchor, RoutePlace

logger = logging.getLogger(__name__)

#: Within this along-track distance of an anchor a position is "near" it.
NEAR_NM = 5.0
#: An anchor further than this is not used for "N NM after/before X".
ANCHOR_NM = 20.0
#: Off-track distance below which the lateral offset is not worth saying.
LATERAL_NM = 3.0
#: The previous anchor is kept while it is at most this much further than the
#: nearest (and "near" while within NEAR_NM + this).
HYSTERESIS_NM = 3.0
#: A coast point is named after the nearest named anchor within this distance.
COAST_NAME_NM = 25.0
#: "mid-crossing" when the position is within this fraction of the crossing's
#: length of its middle (and more than ANCHOR_NM from both coasts).
MID_CROSSING_FRACTION = 0.15

_COMPASS_WORDS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def _compass_word(bearing: float) -> str:
    return _COMPASS_WORDS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _nm(d: float) -> str:
    return f"{abs(d):.0f} NM"


@dataclass(frozen=True)
class Anchor:
    key: str
    kind: AnchorKind
    along_nm: float
    lat: float
    lon: float
    icao: str | None = None
    town: str | None = None

    @property
    def label(self) -> str:
        """First-mention name: "Middelburg (EHMZ)", "coast-out (Middelburg)"."""
        if self.kind in ("coast_out", "coast_in"):
            word = "coast-out" if self.kind == "coast_out" else "coast-in"
            return f"{word} ({self.town})" if self.town else word
        if self.town and self.icao and self.town.upper() != self.icao.upper():
            return f"{self.town} ({self.icao})"
        return self.icao or self.key

    @property
    def short(self) -> str:
        if self.kind in ("coast_out", "coast_in"):
            return "coast-out" if self.kind == "coast_out" else "coast-in"
        return self.icao or self.key

    def model(self) -> RouteAnchor:
        return RouteAnchor(key=self.key, kind=self.kind, along_nm=round(self.along_nm, 1),
                           lat=round(self.lat, 4), lon=round(self.lon, 4), icao=self.icao, town=self.town)

    @classmethod
    def of(cls, m: RouteAnchor | dict) -> Anchor:
        m = m if isinstance(m, RouteAnchor) else RouteAnchor.model_validate(m)
        return cls(m.key, m.kind, m.along_nm, m.lat, m.lon, m.icao, m.town)


@dataclass(frozen=True)
class StationAnchor:
    """A ribbon station offered as an anchor: only those inside the route
    (0 ≤ along ≤ route length) join the ladder."""

    icao: str
    lat: float
    lon: float
    along_nm: float
    town: str | None = None


def _is_named_waypoint(ident: str) -> bool:
    """A waypoint a pilot reads by name: an ICAO, a navaid, a five-letter
    fix. A lat/lon point ("5117N00009E") is not one."""
    return bool(ident) and ident.isalpha() and len(ident) <= 5


class RouteLocator:
    """Pilot wording for positions on one route. Build with :meth:`build`, or
    from a serialized ladder with :meth:`from_ladder` (positions along the
    route only: :meth:`locate` needs the track)."""

    def __init__(
        self, track: RouteTrack | None, anchors: Sequence[Anchor], dep: Anchor, dest: Anchor,
        total_nm: float | None = None,
    ):
        self.track = track
        self.total_nm = track.total_nm if track is not None else float(total_nm or 0.0)
        self.anchors = sorted(anchors, key=lambda a: a.along_nm)
        self.dep = dep
        self.dest = dest
        self._by_key = {a.key: a for a in self.anchors}
        self._waters = self._water_stretches()

    @classmethod
    def build(
        cls,
        track: RouteTrack,
        waypoints: Sequence[tuple[str, float, float]],
        stations: Iterable[StationAnchor] = (),
        *,
        towns: dict[str, str] | None = None,
        coast: bool = True,
    ) -> RouteLocator:
        """The ladder for one route.

        ``waypoints`` are the route's (ident, lat, lon), in order, one per
        track point. ``towns`` maps ICAO → town for the waypoints (the ribbon
        stations bring their own). ``coast=False`` skips the land/sea pass
        (tests, or a route the coast data cannot help).
        """
        if len(waypoints) < 2:
            raise ValueError("a route locator needs the route's departure and destination")
        towns = {k.upper(): v for k, v in (towns or {}).items() if v}
        by_key: dict[str, Anchor] = {}
        for (ident, lat, lon), along in zip(waypoints, track.distances):
            name = ident.upper()
            if not _is_named_waypoint(name):
                continue
            # A route that doubles back passes the same waypoint twice: each
            # pass is its own rung ("EHMZ", then "EHMZ@234").
            key = name if name not in by_key else f"{name}@{along:.0f}"
            by_key[key] = Anchor(key=key, kind="waypoint", along_nm=along, lat=lat, lon=lon,
                                 icao=name, town=towns.get(name))
        for st in stations:
            key = st.icao.upper()
            if st.along_nm is None or not (0.0 <= st.along_nm <= track.total_nm):
                continue
            passes = [k for k, a in by_key.items() if a.icao == key]
            if passes:
                for k in passes:
                    old = by_key[k]
                    if old.town is None and st.town:
                        by_key[k] = Anchor(old.key, old.kind, old.along_nm, old.lat, old.lon, old.icao, st.town)
                continue
            by_key[key] = Anchor(key=key, kind="station", along_nm=st.along_nm, lat=st.lat, lon=st.lon,
                                 icao=key, town=st.town)
        named = list(by_key.values())
        anchors = list(named)
        crossings = ()
        if coast:
            from weatherbrief.analysis.coastline import coast_crossings

            # Broken coast data costs the coast points, not the waypoint and
            # station anchors (callers would otherwise drop to "N NM along").
            try:
                crossings = coast_crossings(tuple(track.points), tuple(track.distances))
            except Exception:
                logger.warning("Coast data unreadable — route locator without coast points", exc_info=True)
        for c in crossings:
            town = None
            near = [(haversine_nm(c.lat, c.lon, a.lat, a.lon), a) for a in named]
            near = [x for x in near if x[0] <= COAST_NAME_NM]
            if near:
                a = min(near, key=lambda x: x[0])[1]
                town = a.town or a.icao
            anchors.append(Anchor(key=f"{c.kind}@{c.along_nm:.0f}", kind=c.kind,
                                  along_nm=c.along_nm, lat=c.lat, lon=c.lon, town=town))
        (dlat, dlon), (alat, alon) = track.points[0], track.points[-1]
        d_id, a_id = waypoints[0][0].upper(), waypoints[-1][0].upper()
        first = by_key.get(d_id)
        last = next((by_key[k] for k in reversed(list(by_key)) if by_key[k].icao == a_id
                     and by_key[k].along_nm >= track.total_nm - 1e-6), None)
        dep = first if first is not None and first.along_nm <= 1e-6 else Anchor(
            d_id, "waypoint", 0.0, dlat, dlon, d_id, towns.get(d_id))
        dest = last or Anchor(a_id if a_id not in by_key else f"{a_id}@{track.total_nm:.0f}", "waypoint",
                              track.total_nm, alat, alon, a_id, towns.get(a_id))
        return cls(track, anchors, dep, dest)

    def ladder(self) -> list[RouteAnchor]:
        """The anchors, departure and destination first, for the ribbon."""
        seen = {self.dep.key, self.dest.key}
        return [self.dep.model(), self.dest.model()] + [a.model() for a in self.anchors if a.key not in seen]

    @classmethod
    def from_ladder(cls, ladder: Sequence[RouteAnchor | dict], total_nm: float) -> RouteLocator | None:
        """The locator a :meth:`ladder` describes, None when it has no ends."""
        anchors = [Anchor.of(a) for a in ladder]
        if len(anchors) < 2:
            return None
        dep, dest, *_ = anchors
        rest = anchors if dep.key != dest.key else anchors[1:]
        return cls(None, rest, dep, dest, total_nm=total_nm)

    # --- Positions ----------------------------------------------------------

    def locate(
        self, lat: float, lon: float, *, prior: RoutePlace | None = None, exclude: str | None = None,
    ) -> RoutePlace:
        """A point anywhere near the route. ``prior`` is this thing's place on
        the previous tick (hysteresis); ``exclude`` an anchor key not to read
        against (a station placed against the others, not itself)."""
        if self.track is None:
            raise ValueError("locate() needs the route track; this locator was built from a ladder")
        proj = self.track.project(lat, lon)
        cross = proj.cross_nm if proj.offtrack_nm > 0 else 0.0
        if proj.end == "departure":
            d = haversine_nm(self.dep.lat, self.dep.lon, lat, lon)
            if d > NEAR_NM:
                word = _compass_word(_bearing(self.dep.lat, self.dep.lon, lat, lon))
                return RoutePlace(
                    text=f"{_nm(d)} {word} of departure", short=f"{_nm(d)} {word} of {self.dep.short}",
                    relation="off_departure", anchor=self.dep.key, anchor_kind=self.dep.kind,
                    anchor_icao=self.dep.icao, anchor_name=self.dep.town,
                    distance_nm=round(d, 1), along_nm=round(-d, 1), cross_nm=None,
                )
        if proj.end == "destination":
            d = haversine_nm(self.dest.lat, self.dest.lon, lat, lon)
            if d > NEAR_NM:
                return RoutePlace(
                    text=f"{_nm(d)} past the destination", short=f"{_nm(d)} past {self.dest.short}",
                    relation="past_destination", anchor=self.dest.key, anchor_kind=self.dest.kind,
                    anchor_icao=self.dest.icao, anchor_name=self.dest.town,
                    distance_nm=round(d, 1), along_nm=round(self.total_nm + d, 1), cross_nm=None,
                )
        return self.locate_along(proj.along_nm, cross, prior=prior, exclude=exclude)

    def locate_along(
        self, along_nm: float, cross_nm: float | None = None, *,
        prior: RoutePlace | None = None, exclude: str | None = None,
    ) -> RoutePlace:
        """A position given along the route (clamped to it)."""
        along = max(0.0, min(along_nm, self.total_nm))
        lateral, side = None, None
        if cross_nm is not None and abs(cross_nm) > LATERAL_NM:
            side = "right" if cross_nm > 0 else "left"
            lateral = f", {_nm(cross_nm)} {side} of track"
        base = dict(along_nm=round(along, 1), cross_nm=round(cross_nm, 1) if cross_nm is not None else None,
                    side=side, lateral=lateral)
        anchors = [a for a in self.anchors if a.key != exclude]
        if not anchors:
            return RoutePlace(text=f"{along:.0f} NM along the route", short=f"{along:.0f} NM along",
                              relation="between", **base)
        anchor = self._pick(anchors, along, prior)
        delta = along - anchor.along_nm
        water = self._water_at(along)
        near_nm = NEAR_NM + (HYSTERESIS_NM if prior is not None and prior.relation == "near"
                             and prior.anchor == anchor.key else 0.0)
        if abs(delta) <= near_nm:
            return self._place("near", anchor, delta, f"near {anchor.label}", f"near {anchor.short}", base)
        if water is not None and (abs(delta) > ANCHOR_NM or anchor.kind in ("coast_out", "coast_in")):
            return self._over_sea(water, along, base)
        if abs(delta) <= ANCHOR_NM:
            rel = "after" if delta > 0 else "before"
            return self._place(rel, anchor, delta, f"{_nm(delta)} {rel} {anchor.label}",
                               f"{_nm(delta)} {rel} {anchor.short}", base)
        # Between two named places: a coast point is a last resort here, the
        # land side of a crossing reads better against an airfield.
        named = [a for a in anchors if a.kind in ("waypoint", "station")] or anchors
        before = [a for a in named if a.along_nm <= along] or [a for a in anchors if a.along_nm <= along]
        after = [a for a in named if a.along_nm > along] or [a for a in anchors if a.along_nm > along]
        if before and after:
            a, b = before[-1], after[0]
            return self._place("between", a, along - a.along_nm, f"between {a.label} and {b.label}",
                               f"between {a.short} and {b.short}", base)
        rel = "after" if delta > 0 else "before"
        return self._place(rel, anchor, delta, f"{_nm(delta)} {rel} {anchor.label}",
                           f"{_nm(delta)} {rel} {anchor.short}", base)

    def span(self, lo_nm: float, hi_nm: float) -> str:
        """A stretch: "from Uden (EHVK) to Rijen (EHGR)", "along the whole route"."""
        total = self.total_nm
        lo, hi = max(0.0, min(lo_nm, hi_nm)), min(total, max(lo_nm, hi_nm))
        if lo <= 1.0 and hi >= total - 1.0:
            return "along the whole route"
        a, b = self.end_label(lo), self.end_label(hi)
        if a == b:
            return self.locate_along((lo + hi) / 2.0).text
        return f"from {a} to {b}"

    # --- Internals -----------------------------------------------------------

    def _pick(self, anchors: Sequence[Anchor], along: float, prior: RoutePlace | None) -> Anchor:
        best = min(anchors, key=lambda a: abs(along - a.along_nm))
        kept = self._by_key.get(prior.anchor) if prior is not None and prior.anchor else None
        if kept is not None and kept in anchors and kept is not best:
            if abs(along - kept.along_nm) <= abs(along - best.along_nm) + HYSTERESIS_NM:
                return kept
        return best

    def _place(self, relation, anchor: Anchor, delta: float, text: str, short: str, base: dict) -> RoutePlace:
        return RoutePlace(
            text=text, short=short, relation=relation, anchor=anchor.key, anchor_kind=anchor.kind,
            anchor_icao=anchor.icao, anchor_name=anchor.town, distance_nm=round(delta, 1), **base,
        )

    def _water_stretches(self) -> list[tuple[Anchor, Anchor]]:
        out, open_ = [], None
        for a in self.anchors:
            if a.kind == "coast_out":
                open_ = a
            elif a.kind == "coast_in" and open_ is not None:
                out.append((open_, a))
                open_ = None
        return out

    def _water_at(self, along: float) -> tuple[Anchor, Anchor] | None:
        for out, in_ in self._waters:
            if out.along_nm < along < in_.along_nm:
                return out, in_
        return None

    def _over_sea(self, water: tuple[Anchor, Anchor], along: float, base: dict) -> RoutePlace:
        out, in_ = water
        d_out, d_in = along - out.along_nm, in_.along_nm - along
        width = in_.along_nm - out.along_nm
        if min(d_out, d_in) > ANCHOR_NM and abs(d_out - d_in) <= 2 * MID_CROSSING_FRACTION * width:
            return self._place("mid_crossing", out, d_out, "over the sea, mid-crossing",
                               "mid-crossing", base)
        if d_out <= d_in:
            return self._place("over_sea", out, d_out, f"over the sea, {_nm(d_out)} after {out.label}",
                               f"{_nm(d_out)} after coast-out", base)
        return self._place("over_sea", in_, -d_in, f"over the sea, {_nm(d_in)} before {in_.label}",
                           f"{_nm(d_in)} before coast-in", base)

    def end_label(self, along: float) -> str:
        """One end of a stretch: the anchor if near, else "N NM after X"
        (no "over the sea": it is half of a "from … to …")."""
        if along <= NEAR_NM:
            return self.dep.label
        if along >= self.total_nm - NEAR_NM:
            return self.dest.label
        a = min(self.anchors, key=lambda x: abs(along - x.along_nm))
        d = along - a.along_nm
        if abs(d) <= NEAR_NM:
            return a.label
        return f"{_nm(d)} {'after' if d > 0 else 'before'} {a.label}"


def for_route(route, observations=None, *, track: RouteTrack | None = None, coast: bool = True) -> RouteLocator:
    """The locator for a briefing route: its waypoints, plus — when the live
    layer has them — the route airports with a METAR or TAF (the ribbon's
    stations), with nav.db towns. ``observations`` is a
    :class:`~weatherbrief.models.observations.RouteObservations`."""
    track = track or RouteTrack.from_route(route)
    towns: dict[str, str] = {}
    stations: list[StationAnchor] = []
    for a in (observations.airports if observations is not None else ()):
        if a.municipality:
            towns[a.icao.upper()] = a.municipality
        if a.lat is None or a.lon is None or not (a.has_metar or a.metar_raw or a.has_taf or a.taf_raw):
            continue
        proj = track.project(a.lat, a.lon)
        if proj.end is None:
            stations.append(StationAnchor(a.icao.upper(), a.lat, a.lon, proj.along_nm, a.municipality))
    waypoints = [(wp.icao, wp.lat, wp.lon) for wp in route.waypoints]
    return RouteLocator.build(track, waypoints, stations, towns=towns, coast=coast)


def when_text(t: datetime | None, now: datetime, *, departed: bool) -> str:
    """The time coordinate beside a place (#790): the clock ("~10:20Z")
    before departure, "in ~25 min" in flight (to the nearest 5 min), "now"
    within 2 min. Empty without a time."""
    if t is None:
        return ""
    if not departed:
        return f"~{t.astimezone(timezone.utc):%H:%MZ}"
    minutes = (t - now).total_seconds() / 60.0
    if minutes <= 2:
        return "now"
    if minutes < 10:
        return f"in ~{minutes:.0f} min"
    return f"in ~{5 * round(minutes / 5):.0f} min"
