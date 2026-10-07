"""Rain areas and convective cores beside the route (#690): the symbolic
map's weather bands.

The cells feed's display file carries the radar's outlines per tier
(``rain20`` ≥ 20 dBZ, ``core35`` ≥ 35 dBZ) as traced polygons, and the cells
(rain20 areas ≥ 2000 km² and every core) with their peak, flashes and motion.
The outlines are not linked to cells: a polygon is the shape, the cells whose
centre lies inside it lend it a strength and a motion.

Each outline within the corridor becomes one :class:`RibbonWeather`: its
along-route extent, the side of the track it lies on (or across it), how far
off, and its motion relative to the course — the ribbon draws it as a band
above (left) or below (right) the route line with an arrow.

Geometry only, like ``storms``: nothing here re-analyses radar.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.live import LiveStorm, RibbonWeather
from weatherbrief.observed.intensity import classify_dbz, intensity_label
from weatherbrief.observed.storms import STORM_CORRIDOR_NM, operational_cells

#: Outline tier → band tier, drawn in this order (rain under the cores).
TIERS = {"rain20": "rain", "core35": "core"}
#: The cells a band takes its strength and motion from, per outline tier.
_MEMBER_TIERS = {"rain20": ("rain20", "core35", "core41"), "core35": ("core35", "core41")}
#: The outline's own floor, when no listed cell lies inside it (rain areas
#: under 2000 km² are outlined but not listed).
_FLOOR_DBZ = {"rain20": 20.0, "core35": 35.0}
#: Along-route resolution of a band's shape, NM.
BIN_NM = 5.0
#: Longest edge between boundary points once densified, degrees (~1 NM).
_DENSIFY_DEG = 1.0 / 60.0
#: Bands kept, nearest the track first (a squall line can trace hundreds).
BANDS_MAX = 80


def relative_deg(toward_deg: float, course_deg: float) -> float:
    """``toward_deg`` against the course, in (-180, 180]: + toward the right."""
    rel = (toward_deg - course_deg + 180.0) % 360.0 - 180.0
    return 180.0 if rel == -180.0 else rel


def _densify(ring: list[list[float]], step_deg: float = _DENSIFY_DEG) -> list[list[float]]:
    """The ring with points added along long edges, so every stretch of
    route sees the boundary that passes beside it (decimated outlines and
    straight edges would otherwise leave a bin looking unbounded)."""
    out: list[list[float]] = []
    n = len(ring)
    for i in range(n):
        (a_lat, a_lon), (b_lat, b_lon) = ring[i][:2], ring[(i + 1) % n][:2]
        out.append([a_lat, a_lon])
        k = int(max(abs(b_lat - a_lat), abs(b_lon - a_lon)) / step_deg)
        for j in range(1, k + 1):
            t = j / (k + 1)
            out.append([a_lat + t * (b_lat - a_lat), a_lon + t * (b_lon - a_lon)])
    return out


def _inside(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Even-odd test in the lat/lon plane (outlines are small enough)."""
    hit = False
    n = len(ring)
    for i in range(n):
        y1, x1 = ring[i][0], ring[i][1]
        y2, x2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _route_bins_inside(ring: list[list[float]], track: RouteTrack) -> set[int]:
    """The ``BIN_NM`` stretches of route whose midpoint lies inside the
    outline (bounding box first: most outlines are nowhere near the track)."""
    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    s, n, w, e = min(lats), max(lats), min(lons), max(lons)
    out = set()
    for k in range(int(math.ceil(track.total_nm / BIN_NM))):
        lat, lon = track.position_at(min((k + 0.5) * BIN_NM, track.total_nm))
        if s <= lat <= n and w <= lon <= e and _inside(lat, lon, ring):
            out.add(k)
    return out


def _profile(near, inside: set[int], track: RouteTrack, corridor_nm: float) -> list[tuple[float, float, float]]:
    """Per ``BIN_NM`` of route, the off-track range the outline covers there.

    The boundary points in a bin give the range; where the route point itself
    lies inside the outline, the track is covered too, and a side with no
    boundary in that bin is covered out to the corridor (the outline is wider
    than it there). An outline that encloses the route with no boundary
    within the corridor at all is the full width wherever it encloses it."""
    bins: dict[int, list[float]] = {}
    for p in near:
        bins.setdefault(int(p.along_nm // BIN_NM), []).append(max(-corridor_nm, min(corridor_nm, p.cross_nm)))
    out = []
    for k in sorted(set(bins) | inside):
        mid = min((k + 0.5) * BIN_NM, track.total_nm)
        xs = bins.get(k, [])
        if k in inside:
            left = [x for x in xs if x < 0.0]
            right = [x for x in xs if x > 0.0]
            lo = min(left) if left else -corridor_nm
            hi = max(right) if right else corridor_nm
        else:
            lo, hi = min(xs), max(xs)
        out.append((round(mid, 1), round(lo, 1), round(hi, 1)))
    return out


def _corridor_box(track: RouteTrack, corridor_nm: float) -> tuple[float, float, float, float]:
    lats = [p[0] for p in track.points]
    lons = [p[1] for p in track.points]
    pad_lat = corridor_nm / 60.0
    pad_lon = corridor_nm / (60.0 * max(math.cos(math.radians(max(abs(min(lats)), abs(max(lats))))), 0.05))
    return min(lats) - pad_lat, max(lats) + pad_lat, min(lons) - pad_lon, max(lons) + pad_lon


def _has_member(tier: str, ring: list[list[float]], cells: list[dict]) -> bool:
    """Whether any of ``cells`` lies inside ``ring`` as a member of ``tier``."""
    return any(
        c.get("tier") in _MEMBER_TIERS[tier] and c.get("lat") is not None
        and _inside(c["lat"], c["lon"], ring)
        for c in cells
    )


def _band(
    tier: str, n: int, ring: list[list[float]], cells: list[dict], track: RouteTrack,
    corridor_nm: float, storm_by_cell: dict[str, str],
) -> RibbonWeather | None:
    projected = [track.project(p[0], p[1]) for p in _densify(ring)]
    near = [p for p in projected if p.offtrack_nm <= corridor_nm]
    inside = _route_bins_inside(ring, track)
    if not near and not inside:
        return None
    profile = _profile(near, inside, track, corridor_nm)
    los = [lo for _, lo, _ in profile]
    his = [hi for _, _, hi in profile]
    if inside or min(los) < 0.0 < max(his):
        side, near_nm = "both", 0.0
    else:
        side = "right" if max(his) > 0 else "left"
        near_nm = min(min(abs(lo), abs(hi)) for _, lo, hi in profile)
    far_nm = min(max(max(abs(lo), abs(hi)) for _, lo, hi in profile), corridor_nm)
    alongs = [p.along_nm for p in near] + [
        x for k in inside for x in (k * BIN_NM, min((k + 1) * BIN_NM, track.total_nm))
    ]
    from_nm, to_nm = min(alongs), max(alongs)

    members = [
        c for c in cells
        if c.get("tier") in _MEMBER_TIERS[tier] and c.get("lat") is not None and _inside(c["lat"], c["lon"], ring)
    ]
    peaks = [c["peak_dbz"] for c in members if c.get("peak_dbz") is not None]
    peak = max(peaks) if peaks else _FLOOR_DBZ[tier]
    flashes = [c["flashes"] for c in members if c.get("flashes") is not None]
    # Motion: the largest member of the outline's own tier with an available
    # one (its own tier moves as the outline does), else any member's.
    moving = sorted(
        (c for c in members if (c.get("motion") or {}).get("status") == "available"
         and (c["motion"].get("toward_deg") is not None)),
        key=lambda c: (c.get("tier") != tier, -(c.get("area_km2") or 0.0)),
    )
    rel = speed = None
    if moving:
        m = moving[0]["motion"]
        speed = m.get("speed_kt")
        if speed is not None and speed >= 1.0:
            course = track.project(moving[0]["lat"], moving[0]["lon"]).track_deg
            rel = round(relative_deg(m["toward_deg"], course), 0)
    storm_id = next((storm_by_cell[c["id"]] for c in members if c.get("id") in storm_by_cell), None)
    cls = classify_dbz(peak)
    return RibbonWeather(
        id=f"{TIERS[tier]}:{n}",
        tier=TIERS[tier],
        from_nm=round(from_nm, 1), to_nm=round(to_nm, 1),
        side=side, near_nm=round(near_nm, 1), far_nm=round(far_nm, 1), profile=profile,
        peak_dbz=round(peak, 1),
        intensity=intensity_label(cls) if cls is not None else None,
        flashes=max(flashes) if flashes else None,
        motion_rel_deg=rel,
        speed_kt=round(speed, 0) if speed is not None else None,
        storm_id=storm_id,
    )


def build_weather_bands(
    frame: dict[str, Any] | None,
    track: RouteTrack,
    storms: Sequence[LiveStorm] = (),
    *,
    corridor_nm: float = STORM_CORRIDOR_NM,
) -> list[RibbonWeather]:
    """The rain and core bands within ``corridor_nm`` of the track in
    ``frame`` (a display file), rain first, each tier by along-route start."""
    if not frame or track.total_nm <= 0:
        return []
    outlines = frame.get("outlines") or {}
    # One gate with the storm rows (#696): a suppressed cell must not set a
    # band's peak intensity either, and a `core` band whose every member was
    # suppressed is not reported at all.  The outlines themselves are traced
    # from the tier masks on the node and cannot be filtered per cell, so a
    # suspect echo can still leave a bare rain20 ring on the ribbon at the
    # floor intensity — a known limitation, recorded in observed-cells.md.
    all_cells = [c for c in frame.get("cells") or [] if isinstance(c, dict)]
    cells = operational_cells(all_cells)
    suppressed = len(all_cells) - len(cells)
    storm_by_cell = {cid: st.id for st in storms for cid in st.cell_ids}
    s, n_, w, e = _corridor_box(track, corridor_nm)

    bands: list[RibbonWeather] = []
    for tier in TIERS:
        found = []
        for n, ring in enumerate(outlines.get(tier) or []):
            if len(ring) < 3:
                continue
            lats = [p[0] for p in ring]
            lons = [p[1] for p in ring]
            if max(lats) < s or min(lats) > n_ or max(lons) < w or min(lons) > e:
                continue
            band = _band(tier, n, ring, cells, track, corridor_nm, storm_by_cell)
            if band is None:
                continue
            if suppressed and tier != "rain20" and not _has_member(tier, ring, cells):
                # A core band with no surviving member is the suspect echo's
                # own outline: reporting it at the floor intensity would put
                # back exactly what the storm row just dropped.
                continue
            found.append(band)
        found = sorted(found, key=lambda b: b.near_nm)[:BANDS_MAX]
        bands.extend(sorted(found, key=lambda b: (b.from_nm, b.near_nm)))
    return bands
