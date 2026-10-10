"""Radar storms against the route (#688): the droplet's half of the cells.

The home node analyses the field and pushes one display file per radar frame
(``observed-cells.md``); the droplet owns the route. This module turns the
newest display file into :class:`~weatherbrief.models.live.LiveStorms`:

- **one storm per lineage group**: a ``core35`` cell plus the ``core41``
  cells inside it (the node's ``within``), or a ``core41`` on its own. Rain
  areas without a core are not storms;
- **route geometry** per storm: distance off track, which side, the point
  abeam and the planned time there, ahead or passed;
- **observed motion against the track**: the velocity's component toward
  the track (closing / moving away / parallel), and the off-track distance at
  the frames of the last 30 min;
- **the estimate** (closest approach at current motion): a projection,
  computed only when the node's motion is ``available``, logged for scoring
  and never used by the alert rule.

Geometry only: nothing here re-analyses radar (the droplet never runs cell
analysis). Which storms alert is ``live_significance``'s job.

Cells the node marked as non-meteorological echoes (#696) pass through
``operational_cells``, the single gate for every route product. It is a no-op
unless ``WB_CELLS_CLUTTER_SUPPRESS`` is set.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from euro_aip.utils.geometry import haversine_nm

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.live import LiveStorm, LiveStorms, StormEstimate, StormTrackPoint
from weatherbrief.observed.cells.levels import suspect
from weatherbrief.observed.cells_display import clutter_suppress_enabled
from weatherbrief.observed.intensity import classify_dbz, intensity_label

logger = logging.getLogger(__name__)

#: Storms listed on the layer: within this distance of the track, ahead or
#: behind. Wider than the alert and highlight bands so the Observed tab can
#: show the storm the flight just passed and the ones further out.
STORM_CORRIDOR_NM = 30.0
#: Earlier frames read for a storm's observed off-track history, and their
#: spacing: 3 frames, ~10 min apart.
HISTORY_MINUTES = 30
HISTORY_STEP_MINUTES = 10
_HISTORY_SLACK = timedelta(minutes=3)
#: At most this many storms are kept on the layer (nearest the track first):
#: bounds ``live.json`` on a showery day; every storm within the highlight
#: band is far inside it.
STORMS_MAX = 40
#: Below this component toward/away from the track the motion reads "parallel".
PARALLEL_KT = 3.0
#: The estimate looks no further than this past the frame (the planned
#: arrival usually ends it sooner).
ESTIMATE_MAX_MINUTES = 180
_ESTIMATE_STEP_MIN = 1.0

#: Tier names of the node's default policy (``CellPolicy.tiers``).
BASE_TIER = "core35"
CORE_TIER = "core41"

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


@dataclass
class CellFrames:
    """What the droplet holds of the cells feed at one tick.

    ``newest`` is the display file used (None unless ``status`` is
    ``available``); ``earlier`` the frames for the storms' history, oldest
    first. ``received_at`` is when the droplet ingested ``newest`` and
    ``computed_at`` when the home node wrote it (#751; None if ingested before
    the last restart).
    """

    status: str  # available | stale | disabled | unavailable
    newest: dict[str, Any] | None = None
    earlier: list[dict[str, Any]] = field(default_factory=list)
    unavailable_since: datetime | None = None
    received_at: datetime | None = None
    computed_at: datetime | None = None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_cell_frames(now: datetime, store=None) -> CellFrames:
    """The newest display file at or before ``now``, plus earlier frames.

    Without a ``store`` the droplet's own (``DATA_DIR/observed/cells/display``)
    is used, and only when cell ingest is enabled. Never raises: a read
    failure is an unavailable feed, which the classifier words as such.
    """
    from weatherbrief.observed.cells_display import (
        STALE_AFTER,
        DisplayStore,
        cells_ingest_enabled,
        computed_at,
    )

    try:
        if store is None:
            if not cells_ingest_enabled():
                return CellFrames("disabled")
            store = DisplayStore()
        listing = [d for d in store.list() if d.valid_time <= now]
        if not listing:
            return CellFrames("unavailable")
        newest = listing[0]
        if now - newest.valid_time > STALE_AFTER:
            return CellFrames("stale", unavailable_since=newest.valid_time)
        data = store.read(newest.stamp, newest.revision)
        if data is None:
            return CellFrames("unavailable", unavailable_since=newest.valid_time)
        earlier: list[dict[str, Any]] = []
        for k in range(HISTORY_MINUTES // HISTORY_STEP_MINUTES, 0, -1):
            target = newest.valid_time - timedelta(minutes=k * HISTORY_STEP_MINUTES)
            near = [d for d in listing[1:] if abs(d.valid_time - target) <= _HISTORY_SLACK]
            if not near:
                continue
            best = min(near, key=lambda d: abs(d.valid_time - target))
            frame = store.read(best.stamp, best.revision)
            if frame is not None:
                earlier.append(frame)
        return CellFrames("available", newest=data, earlier=earlier, received_at=newest.received_at,
                          computed_at=computed_at(newest.stamp, newest.revision))
    except Exception:
        logger.warning("Cell frames unreadable — storms unavailable this tick", exc_info=True)
        return CellFrames("unavailable")


# --- Grouping ----------------------------------------------------------------


def _equivalent_radius_nm(cell: dict) -> float:
    area = cell.get("area_km2") or 0.0
    return math.sqrt(max(area, 0.0) / math.pi) / 1.852


def operational_cells(cells: list[dict]) -> list[dict]:
    """The cells the route products may speak about (#696).

    With ``WB_CELLS_CLUTTER_SUPPRESS`` off — the default — this is every cell,
    so the suspect block is annotation and nothing more.  With it on, cells the
    node marked suspect or confirmed are dropped **here**, before grouping, so
    one gate covers storms, the §41 alert rows, the glance and the ribbon
    bands rather than each re-deciding.

    Dropping a cell is not a claim that the sky is clear there: it is a
    statement that we have no confident storm to report.  The cell is still in
    the display file with its reasons, and the overlay can draw it.
    """
    if not clutter_suppress_enabled():
        return list(cells)
    kept = [c for c in cells if not suspect(c)]
    dropped = len(cells) - len(kept)
    if dropped:
        # DEBUG, not INFO: this runs for the newest frame and again for every
        # earlier frame of a storm's history, and once more for the ribbon
        # bands, so the same dropped cells would log four times per flight per
        # tick (#696 review).
        logger.debug("cells: %d of %d cells held back as suspect echoes", dropped, len(cells))
    return kept


def group_storms(cells: list[dict]) -> list[list[dict]]:
    """Cells grouped into storms, the storm's own cell first.

    A ``core41`` joins the ``core35`` the node says it is ``within``; a file
    from a node before that field falls back to the ``core35`` whose centroid
    is within its own equivalent radius (+ 3 NM) of the core's. A ``core41``
    with neither stands alone. Rain areas are not storms.
    """
    bases = {c["id"]: [c] for c in cells if c.get("tier") == BASE_TIER}
    alone: list[list[dict]] = []
    for c in cells:
        if c.get("tier") != CORE_TIER:
            continue
        parent = c.get("within")
        if parent is None:
            near = [
                (d, b) for b in bases.values()
                if (d := haversine_nm(c["lat"], c["lon"], b[0]["lat"], b[0]["lon"]))
                <= _equivalent_radius_nm(b[0]) + 3.0
            ]
            parent = min(near, key=lambda x: x[0])[1][0]["id"] if near else None
        if parent in bases:
            bases[parent].append(c)
        else:
            alone.append([c])
    return list(bases.values()) + alone


# --- Geometry ----------------------------------------------------------------


def _compass(bearing: float) -> str:
    return _COMPASS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _move(lat: float, lon: float, toward_deg: float, nm: float) -> tuple[float, float]:
    """Point ``nm`` from (lat, lon) toward ``toward_deg`` (local flat earth)."""
    b = math.radians(toward_deg)
    dlat = nm * math.cos(b) / 60.0
    dlon = nm * math.sin(b) / (60.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


@dataclass
class Schedule:
    """The planned 4-D track: on-time departure, constant speed."""

    track: RouteTrack
    departure: datetime | None
    duration_h: float

    @property
    def timed(self) -> bool:
        return self.departure is not None and self.duration_h > 0 and self.track.total_nm > 0

    def eta(self, along_nm: float) -> datetime | None:
        if not self.timed:
            return None
        frac = max(0.0, min(1.0, along_nm / self.track.total_nm))
        return self.departure + timedelta(hours=frac * self.duration_h)

    def arrival(self) -> datetime | None:
        return self.departure + timedelta(hours=self.duration_h) if self.timed else None

    def position(self, t: datetime) -> tuple[float, float]:
        frac = (t - self.departure).total_seconds() / 3600.0 / self.duration_h
        return self.track.position_at(max(0.0, min(1.0, frac)) * self.track.total_nm)


def _closing_kt(proj, lat: float, lon: float, speed_kt: float, toward_deg: float) -> float:
    """Component of the velocity toward the track (+ closing), kt.

    Toward the nearest point of the track: perpendicular to it beside a
    segment, radial from the airport before the start or past the end."""
    if proj.offtrack_nm <= 1e-6:
        return 0.0
    away = math.radians(_bearing(proj.lat, proj.lon, lat, lon))
    v = math.radians(toward_deg)
    return -speed_kt * math.cos(v - away)


def _relative_motion(closing_kt: float) -> str:
    if abs(closing_kt) < PARALLEL_KT:
        return "parallel"
    return "closing" if closing_kt > 0 else "moving_away"


def estimate(
    lat: float, lon: float, speed_kt: float, toward_deg: float, frame_time: datetime,
    schedule: Schedule, now: datetime, abeam_eta: datetime | None,
) -> StormEstimate | None:
    """Closest approach to the planned 4-D track at current motion.

    Sampled every minute from ``now`` to the planned arrival (at most
    :data:`ESTIMATE_MAX_MINUTES` past the frame). None without a timed
    schedule or once the flight has arrived.
    """
    if not schedule.timed:
        return None
    start = max(now, frame_time)
    end = min(schedule.arrival(), frame_time + timedelta(minutes=ESTIMATE_MAX_MINUTES))
    if end < start:
        return None

    def storm_at(t: datetime) -> tuple[float, float]:
        return _move(lat, lon, toward_deg, speed_kt * (t - frame_time).total_seconds() / 3600.0)

    best: tuple[float, datetime] | None = None
    t = start
    while t <= end:
        a = schedule.position(t)
        s = storm_at(t)
        d = haversine_nm(a[0], a[1], s[0], s[1])
        if best is None or d < best[0]:
            best = (d, t)
        t += timedelta(minutes=_ESTIMATE_STEP_MIN)
    assert best is not None
    at_eta = None
    if abeam_eta is not None and abeam_eta >= frame_time:
        s = storm_at(abeam_eta)
        at_eta = round(schedule.track.project(*s).offtrack_nm, 1)
    return StormEstimate(
        cpa_nm=round(best[0], 1),
        cpa_time=best[1],
        at_eta_offtrack_nm=at_eta,
        horizon_min=round((best[1] - frame_time).total_seconds() / 60.0, 1),
    )


def _storm_trend(group: list[dict]) -> dict:
    """The storm's trend: "developing" when any of its cells is (the safe
    reading), else its own cell's; the numbers come from the cell whose state
    is reported."""
    developing = [c for c in group if (c.get("trend") or {}).get("state") == "developing"]
    src = developing[0] if developing else group[0]
    return src.get("trend") or {}


def _storm_motion(group: list[dict]) -> dict:
    """The storm cell's motion when available (larger, better supported),
    else the first core with an available one, else the storm cell's."""
    for c in group:
        if (c.get("motion") or {}).get("status") == "available":
            return c["motion"]
    return group[0].get("motion") or {}


def build_storm(
    group: list[dict], frame_time: datetime, schedule: Schedule, *,
    flown_nm: float | None, now: datetime, history_frames: list[tuple[datetime, dict[str, dict]]],
    end_icaos: tuple[str | None, str | None] = (None, None),
) -> LiveStorm:
    track = schedule.track
    # Position: the member nearest the track (a big core35's centroid can sit
    # well off its strongest core).
    projected = [(track.project(c["lat"], c["lon"]), c) for c in group]
    proj, ref = min(projected, key=lambda pc: pc[0].offtrack_nm)
    peaks = [c["peak_dbz"] for c in group if c.get("peak_dbz") is not None]
    peak = max(peaks) if peaks else 0.0
    flashes = [c["flashes"] for c in group if c.get("flashes") is not None]
    tops = [c["top_fl"] for c in group if c.get("top_fl") is not None]
    trend = _storm_trend(group)
    motion = _storm_motion(group)
    available = motion.get("status") == "available"
    speed, toward = motion.get("speed_kt"), motion.get("toward_deg")
    closing = None
    relative = "unknown"
    if available and speed is not None and speed < 1.0:
        closing, relative = 0.0, "stationary"  # no heading to speak of
    elif available and speed is not None and toward is not None:
        closing = round(_closing_kt(proj, ref["lat"], ref["lon"], speed, toward), 1)
        relative = _relative_motion(closing)

    abeam = schedule.eta(proj.along_nm)
    end_icao = end_bearing = None
    if proj.end is not None:
        end_icao = end_icaos[0] if proj.end == "departure" else end_icaos[1]
        end_bearing = _compass(_bearing(proj.lat, proj.lon, ref["lat"], ref["lon"]))
    ids = {c["id"] for c in group}
    history: list[StormTrackPoint] = []
    for at, by_id in history_frames:
        same = [by_id[i] for i in ids if i in by_id]
        if not same:
            continue
        p = min((track.project(c["lat"], c["lon"]) for c in same), key=lambda x: x.offtrack_nm)
        history.append(StormTrackPoint(at=at, offtrack_nm=round(p.offtrack_nm, 1), cross_nm=round(p.cross_nm, 1)))

    ahead = flown_nm is None or proj.along_nm >= flown_nm or (proj.end == "departure" and flown_nm <= 0)
    est = None
    # Only a storm still ahead: one already passed has no closest approach
    # worth scoring, and each estimate costs up to ESTIMATE_MAX_MINUTES samples.
    if ahead and available and speed is not None and toward is not None:
        est = estimate(ref["lat"], ref["lon"], speed, toward, frame_time, schedule, now, abeam)

    intensity = classify_dbz(peak)
    return LiveStorm(
        id=group[0]["id"],
        cell_ids=[c["id"] for c in group],
        lat=ref["lat"], lon=ref["lon"],
        peak_dbz=round(peak, 1),
        intensity=intensity_label(intensity) if peak >= 41.0 else None,
        flashes=max(flashes) if flashes else None,
        flashes_pending=any(c.get("flashes_pending") for c in group),
        top_fl=max(tops) if tops else None,
        truncated=any(c.get("truncated") for c in group),
        trend=trend.get("state"),
        d_peak_db=trend.get("d_peak_db"),
        area_ratio=trend.get("area_ratio"),
        d_flashes=trend.get("d_flashes"),
        motion_status=motion.get("status"),
        speed_kt=speed,
        toward_deg=toward,
        along_nm=round(proj.along_nm, 1),
        offtrack_nm=round(proj.offtrack_nm, 1),
        cross_nm=round(proj.cross_nm, 1),
        side=None if proj.end is not None or proj.offtrack_nm < 0.5 else ("right" if proj.cross_nm > 0 else "left"),
        end=proj.end,
        end_icao=end_icao,
        end_bearing=end_bearing,
        abeam_eta=abeam,
        minutes_to_abeam=round((abeam - now).total_seconds() / 60.0, 1) if abeam is not None else None,
        ahead=ahead,
        relative_motion=relative,
        closing_kt=closing,
        history=history,
        estimate=est,
    )


def build_storms(
    frames: CellFrames, schedule: Schedule, *, flown_nm: float | None, now: datetime,
    end_icaos: tuple[str | None, str | None] = (None, None),
    corridor_nm: float = STORM_CORRIDOR_NM,
) -> LiveStorms:
    """The storms within ``corridor_nm`` of the track at the newest frame,
    nearest along-track first."""
    if frames.status != "available" or frames.newest is None:
        return LiveStorms(status=frames.status, unavailable_since=frames.unavailable_since,
                          corridor_nm=corridor_nm)
    data = frames.newest
    frame_time = _parse_time(data.get("valid_time"))
    history = []
    for frame in frames.earlier:
        at = _parse_time(frame.get("valid_time"))
        if at is not None:
            history.append((at, {c["id"]: c for c in operational_cells(frame.get("cells") or [])
                                 if "id" in c}))
    storms: list[LiveStorm] = []
    track = schedule.track
    for group in group_storms(operational_cells(data.get("cells") or [])):
        # The corridor first: the full storm (history, estimate) only for the
        # few near the route, not the hundreds of cores across Europe.
        if min(track.project(c["lat"], c["lon"]).offtrack_nm for c in group) > corridor_nm:
            continue
        storms.append(build_storm(group, frame_time, schedule, flown_nm=flown_nm, now=now,
                                  history_frames=history, end_icaos=end_icaos))
    storms = sorted(storms, key=lambda s: s.offtrack_nm)[:STORMS_MAX]
    storms.sort(key=lambda s: (s.along_nm, s.offtrack_nm))
    return LiveStorms(
        status="available",
        frame_time=frame_time,
        lightning_pending="lightning" in (data.get("pending") or []),
        corridor_nm=corridor_nm,
        route_nm=round(track.total_nm, 1),
        storms=storms,
        policy_version=data.get("policy_version"),
    )
