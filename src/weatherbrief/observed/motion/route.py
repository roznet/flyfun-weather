"""Pure continuous-leg geometry and planned timing under rigid translation.

Callers must gate source registration before publishing these ground quantities.
Only the agreed tracking fields are consumed; this module neither fits motion
nor reads providers/storage. Analysis footprints are never simplified here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import time
from typing import Protocol

from pyproj import Geod
from shapely.affinity import translate
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from weatherbrief.fetch.route_walk import walk_route
from weatherbrief.models import RouteConfig
from weatherbrief.models.observed_motion import Interval, OverlapInterval, PlannedOverlapResult, RouteRow
from .geometry import AnalysisGrid
from .policy import DEFAULT_POLICY, MotionPolicy

NM_M = 1852.
KT_PER_M_S = 3600. / NM_M
QUALIFIERS = ["grid_discretization", "projected_translation"]
_GEOD = Geod(ellps="WGS84")


class TrackGeometry(Protocol):
    reference_at: datetime
    footprint: BaseGeometry
    velocity_xy_m_s: tuple[float, float] | None
    reason_codes: list[str]


@dataclass(frozen=True)
class _Leg:
    index: int
    coordinates: tuple[tuple[float, float], ...]
    distances: tuple[float, ...]

    @property
    def geometry(self):
        return LineString(self.coordinates)

    @property
    def degenerate(self):
        return self.distances[-1] <= self.distances[0]


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid_time")
    return value.astimezone(timezone.utc)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":"),
                                      allow_nan=False).encode("utf-8")).hexdigest()


def _coordinate_token(value):
    # Hex retains binary float identity; NaN/Inf remain deterministic strings,
    # allowing a refusal envelope to bind the invalid input without validating it.
    if isinstance(value, (int, float)):
        return ["float", float(value).hex()]
    return [type(value).__name__, str(value)]


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError("compute_deadline")


def _dense(route, policy, deadline=None):
    _check_deadline(deadline)
    points = []
    for point in walk_route(route, policy.max_route_segment_nm,
                            max_segment_nm=policy.max_route_segment_nm,
                            max_segments=policy.max_route_segments):
        _check_deadline(deadline)
        points.append(point)
    return points


def _timing(route, departure_time, total_distance, distances):
    try:
        departure = _utc(departure_time)
        duration = float(route.flight_duration_hours) * 3600.
        if not math.isfinite(duration) or duration <= 0 or total_distance <= 0:
            return None
        arrival = departure + timedelta(seconds=duration)
        if arrival <= departure:
            return None
        previous_distance, previous_time = 0., departure
        for distance in distances:
            at = departure + timedelta(seconds=duration * distance / total_distance)
            if distance > previous_distance and at <= previous_time:
                return None
            previous_distance, previous_time = distance, at
        return departure, duration, arrival
    except (ValueError, TypeError, OverflowError):
        return None


def route_identities(route: RouteConfig, departure_time: datetime | None) -> tuple[str, str | None]:
    """One canonical binding, including for rejected coordinates.

    Geometry: SHA256 compact UTF-8 JSON of ["route_geometry_v1", ordered
    [lon-token, lat-token, ICAO, name] rows]. Numerical tokens are tagged float
    hex strings. Timing: ["planned_timing_v1", geometry ID, UTC ISO departure
    with microseconds, duration-seconds.hex(), total-great-circle-NM.hex()].
    Names/coordinates/order bind identity, not cruise altitude or route title.
    Invalid or unsupported geometry/timing yields a null timing ID.
    """
    geometry_id = _digest(["route_geometry_v1", [
        [_coordinate_token(w.lon), _coordinate_token(w.lat), w.icao, w.name]
        for w in route.waypoints]])
    try:
        points = _dense(route, DEFAULT_POLICY)
        total = points[-1][2]
    except (ValueError, TypeError, OverflowError):
        return geometry_id, None
    timing = _timing(route, departure_time, total, (p[2] for p in points))
    if timing is None:
        return geometry_id, None
    departure, duration, _ = timing
    return geometry_id, _digest(["planned_timing_v1", geometry_id,
        departure.isoformat(timespec="microseconds"), duration.hex(), total.hex()])


def _legs(route, grid, policy, deadline=None):
    points = _dense(route, policy, deadline)
    legs, coordinates, distances = [], [], []
    for lat, lon, distance, label, _ in points:
        _check_deadline(deadline)
        xy = tuple(map(float, grid.project(lon, lat)))
        if not all(math.isfinite(v) for v in xy):
            raise ValueError("invalid_route")
        coordinates.append(xy)
        distances.append(distance)
        if label is not None and len(coordinates) > 1:
            legs.append(_Leg(len(legs), tuple(coordinates), tuple(distances)))
            coordinates, distances = [xy], [distance]
    if not legs or points[-1][2] <= 0:
        raise ValueError("invalid_route")
    return legs, points[-1][2]


def build_route_geometry(route: RouteConfig, grid: AnalysisGrid, *, policy=DEFAULT_POLICY,
                         deadline: float | None = None) -> BaseGeometry:
    """Continuous dense original legs; raises a stable refusal reason on limits."""
    legs, _ = _legs(route, grid, policy, deadline)
    return MultiLineString([leg.coordinates for leg in legs if not leg.degenerate])


def _validate_track(track):
    if track.velocity_xy_m_s is None:
        raise ValueError(next(iter(track.reason_codes), "not_evaluated"))
    if len(track.velocity_xy_m_s) != 2 or not all(math.isfinite(v) for v in track.velocity_xy_m_s):
        raise ValueError("invalid_geometry")
    shape = track.footprint
    if shape.is_empty or not shape.is_valid or shape.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("invalid_geometry")
    if not all(math.isfinite(v) for v in shape.bounds):
        raise ValueError("invalid_geometry")
    _utc(track.reference_at)


def ground_velocity(track: TrackGeometry, grid: AnalysisGrid) -> tuple[float, float | None, tuple[float, float]]:
    """Representative Earth-relative velocity, not AEQD-axis direction."""
    _validate_track(track)
    center = track.footprint.centroid
    point = tuple(map(float, grid.inverse(center.x, center.y)))
    vx, vy = track.velocity_xy_m_s
    if not all(math.isfinite(v) for v in point):
        raise ValueError("invalid_geometry")
    if vx == 0 and vy == 0:
        return 0., None, point
    end = grid.inverse(center.x + vx, center.y + vy)
    bearing, _, meters = _GEOD.inv(*point, *end)
    if not math.isfinite(meters) or not math.isfinite(bearing):
        raise ValueError("invalid_geometry")
    return float(meters * KT_PER_M_S), float(bearing % 360.), point


def _translated(track, at):
    seconds = (at - track.reference_at).total_seconds()
    vx, vy = track.velocity_xy_m_s
    return translate(track.footprint, xoff=vx * seconds, yoff=vy * seconds)


def _supported(track, grid, start, end):
    # The domain is a convex box and translation is linear: checking both
    # endpoint bounding boxes with a one-cell square rim proves all times.
    dx0, dy0, dx1, dy1 = grid.domain.bounds
    rim = grid.cell_size_m
    for at in (start, end):
        x0, y0, x1, y1 = _translated(track, at).bounds
        if x0-rim < dx0 or y0-rim < dy0 or x1+rim > dx1 or y1+rim > dy1:
            return False
    return True


def _unavailable(reason, evaluated=None):
    return PlannedOverlapResult(status="unavailable", reason_codes=[reason],
        method="relative_segment_contour_intersection", planned_time_method="distance_proportional_planned",
        evaluated_interval=evaluated, intervals=[], complete=False)


def _position(leg, at, departure, duration, total):
    distance = (at-departure).total_seconds() * total / duration
    for a, b, da, db in zip(leg.coordinates, leg.coordinates[1:], leg.distances, leg.distances[1:]):
        if db > da and da-1e-10 <= distance <= db+1e-10:
            fraction = max(0., min(1., (distance-da)/(db-da)))
            return Point(a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1]))
    return None


def _contact_fractions(geometry, segment):
    if geometry.is_empty:
        return
    if geometry.geom_type == "Point":
        f = segment.project(geometry, normalized=True)
        yield f, f
    elif geometry.geom_type == "LineString":
        fractions = [segment.project(Point(p), normalized=True) for p in geometry.coords]
        yield min(fractions), max(fractions)
    elif geometry.geom_type in ("MultiPoint", "MultiLineString", "GeometryCollection"):
        for part in geometry.geoms:
            yield from _contact_fractions(part, segment)
    else:
        raise ValueError("invalid_geometry")


def _rounded(at, direction):
    seconds = at.second + at.microsecond/1e6
    floor = at.replace(second=0, microsecond=0)
    return floor + timedelta(minutes=int(seconds > 0) if direction == "ceil" else
                             int(seconds >= 30) if direction == "nearest" else 0)


def _overlap(track, legs, total, timing, grid, cutoff, expiry, geometry_id, policy, deadline):
    _check_deadline(deadline)
    if timing is None:
        return _unavailable("invalid_planned_timing")
    if cutoff >= expiry:
        return _unavailable("no_future_lead")
    departure, duration, arrival = timing
    start, end = max(cutoff, track.reference_at, departure), min(expiry, arrival)
    if start > end:
        return _unavailable("outside_planned_interval")
    evaluated = Interval(start_at=start, end_at=end)
    if not _supported(track, grid, start, end):
        return _unavailable("outside_analysis_domain", evaluated)
    vx, vy = track.velocity_xy_m_s
    output = []
    for leg in legs:
        _check_deadline(deadline)
        if leg.degenerate:
            continue
        merged = []
        for a, b, da, db in zip(leg.coordinates,leg.coordinates[1:],leg.distances,leg.distances[1:]):
            _check_deadline(deadline)
            sa, sb = departure+timedelta(seconds=duration*da/total), departure+timedelta(seconds=duration*db/total)
            lo, hi = max(start,sa), min(end,sb)
            if sb <= sa:
                return _unavailable("invalid_planned_timing", evaluated)
            if lo > hi:
                continue
            spans = []
            relative = []
            for at in (lo,hi):
                f = (at-sa).total_seconds()/(sb-sa).total_seconds()
                dt = (at-track.reference_at).total_seconds()
                relative.append((a[0]+f*(b[0]-a[0])-vx*dt, a[1]+f*(b[1]-a[1])-vy*dt))
            if math.dist(*relative) <= 1e-8:
                if track.footprint.covers(Point(relative[0])):
                    spans.append((lo,hi))
            else:
                segment = LineString(relative)
                for fa,fb in _contact_fractions(segment.intersection(track.footprint),segment):
                    _check_deadline(deadline)
                    spans.append((lo+(hi-lo)*fa,lo+(hi-lo)*fb))
            # Merge chronological exact segment seams before display rounding,
            # never across legs or genuine gaps/holes. Bound retained intervals
            # per segment rather than accumulating all 2,048 intersections.
            for lo,hi in sorted(spans):
                if merged and lo <= merged[-1][1]:
                    merged[-1] = merged[-1][0], max(hi,merged[-1][1])
                else:
                    merged.append((lo,hi))
                if len(output)+len(merged) > policy.max_overlap_intervals:
                    return _unavailable("overlap_interval_limit",evaluated)
        for lo,hi in merged:
            tangent = lo == hi
            rounded_lo = _rounded(lo,"nearest" if tangent else "floor")
            rounded_hi = rounded_lo if tangent else _rounded(hi,"ceil")
            output.append(OverlapInterval(leg_id=f"{geometry_id}:{leg.index}",leg_index=leg.index,
                start_at=max(start,min(end,rounded_lo)),end_at=max(start,min(end,rounded_hi)),
                contact="tangent" if tangent else "interval",approximate=True))
    return PlannedOverlapResult(status="available",reason_codes=QUALIFIERS,
        method="relative_segment_contour_intersection",planned_time_method="distance_proportional_planned",
        evaluated_interval=evaluated,intervals=output,complete=True)


def route_relationships(track: TrackGeometry, route: RouteConfig, grid: AnalysisGrid,
                        departure_time: datetime | None, cutoff_at: datetime,
                        projection_times, *, policy: MotionPolicy = DEFAULT_POLICY,
                        deadline: float | None = None
                        ) -> tuple[list[RouteRow], PlannedOverlapResult]:
    """Reference/per-tick rows plus a separate complete continuous overlap.

    Registration must be approved by the caller. Bounds/geometry failures are
    explicit unavailable results; no partial overlap list escapes a failed solve.
    """
    try:
        _check_deadline(deadline)
        geometry_id, _ = route_identities(route,departure_time)
        cutoff = _utc(cutoff_at)
        reference = _utc(track.reference_at)
        expiry = reference+timedelta(minutes=policy.projection_horizon_minutes)
        future = [_utc(t) for t in projection_times]
        if len(future) > policy.max_projection_times or future != sorted(set(future)):
            return [], _unavailable("unsupported_time")
        if any(t <= cutoff or t.second or t.microsecond or t.minute % policy.projection_tick_minutes for t in future):
            return [], _unavailable("unsupported_time")
        legs, total = _legs(route,grid,policy,deadline)
        if len(legs)*(1+len(future)) > policy.max_route_rows:
            return [], _unavailable("selection_limit")
        _validate_track(track)
        if reference > cutoff:
            return [], _unavailable("unsupported_time")
    except OverflowError:
        return [], _unavailable("invalid_time")
    except (ValueError, GEOSException) as exc:
        return [], _unavailable("invalid_geometry" if isinstance(exc,GEOSException) else str(exc))
    timing = _timing(route,departure_time,total,
                     (distance for leg in legs for distance in leg.distances))
    rows = []
    try:
        for leg in legs:
            for at in [reference,*future]:
                _check_deadline(deadline)
                reason = "degenerate_leg" if leg.degenerate else "no_future_lead" if at > expiry else None
                a, b = max(reference,at-timedelta(seconds=30)), min(expiry,at+timedelta(seconds=30))
                if reason is None and not _supported(track,grid,a,b):
                    reason = "outside_analysis_domain"
                distance = closure = interval = None
                relation = "unavailable"
                contour = _translated(track,at)
                if reason is None:
                    distance = float(contour.distance(leg.geometry)/NM_M)
                    if distance == 0:
                        relation = "intersecting"
                    else:
                        closure = float(-(_translated(track,b).distance(leg.geometry)-
                                          _translated(track,a).distance(leg.geometry))/(b-a).total_seconds()*KT_PER_M_S)
                        interval = Interval(start_at=a,end_at=b)
                        relation = "approximately_unchanged" if abs(closure) < 1 else "approaching" if closure > 0 else "receding"
                planned_reason = reason or ("invalid_planned_timing" if timing is None else None)
                overlap = None
                if planned_reason is None:
                    departure,duration,arrival = timing
                    point = _position(leg,at,departure,duration,total)
                    if not departure <= at <= arrival:
                        planned_reason = "outside_planned_interval"
                    elif point is None:
                        planned_reason = "outside_leg_interval"
                    else:
                        overlap = bool(contour.covers(point))
                rows.append(RouteRow(leg_id=f"{geometry_id}:{leg.index}",leg_index=leg.index,
                    from_label=route.waypoints[leg.index].icao,to_label=route.waypoints[leg.index+1].icao,
                    at=at,status="unavailable" if reason else "available",reason_codes=[reason] if reason else QUALIFIERS,
                    distance_nm=distance,closure_kt=closure,closure_interval=interval,relationship=relation,
                    planned_time_method="distance_proportional_planned",planned_time_status="unavailable" if planned_reason else "available",
                    planned_time_reason_codes=[planned_reason] if planned_reason else QUALIFIERS,planned_overlap_at_time=overlap))
        return rows,_overlap(track,legs,total,timing,grid,cutoff,expiry,geometry_id,policy,deadline)
    except (GEOSException, ValueError, OverflowError) as exc:
        reason = "compute_deadline" if str(exc) == "compute_deadline" else "invalid_geometry"
        return [],_unavailable(reason)


__all__ = ["route_identities", "build_route_geometry", "route_relationships", "ground_velocity"]
