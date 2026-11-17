"""Climb, cruise and descent flight path of a briefing (#1005).

The path is computed on the server by ``weatherbrief.analysis.flight_path`` and
stored on the route-analyses manifest so recalculation and replays read the
same path. Old packs have none and read as today's flat cruise profile.
"""

from __future__ import annotations

import bisect
from typing import Literal

from pydantic import BaseModel, Field

FlightPhase = Literal["climb", "cruise", "descent"]


class AircraftPerformance(BaseModel):
    """Climb / descent performance the flight path is flown with.

    Resolved from the aircraft's own overrides, else the class preset of its
    ICAO category, else the unknown-aircraft default (``source`` says which).
    Every figure is a still-air, ISA, POH-order planning number.
    """

    preset: str
    source: Literal["aircraft", "category", "default"] = "default"
    # Climb speed: IAS (kt). Jets: ``climb_ias_kt`` above ``low_alt_cap_ft``,
    # ``low_alt_ias_kt`` below it, and never above ``climb_mach``.
    climb_ias_kt: float
    # Rate of climb (fpm) at sea level and near the ceiling, linear between
    # (flat up to ``critical_alt_ft`` for a turbo piston, then linear).
    climb_rate_low_fpm: float
    climb_rate_high_fpm: float
    ceiling_ft: float
    critical_alt_ft: float | None = None
    # Descent: a fixed rate (pistons) or a fixed path angle (turbine).
    descent_mode: Literal["rate", "angle"] = "rate"
    descent_rate_fpm: float | None = None
    descent_angle_deg: float | None = None
    descent_rate_cap_fpm: float | None = None
    # Jet speed schedule (internal, not a pilot input).
    low_alt_ias_kt: float | None = None
    low_alt_cap_ft: float = 10000.0
    climb_mach: float | None = None
    cruise_mach: float | None = None
    turbo: bool = False


class FlightPathPoint(BaseModel):
    """The path at one route point."""

    point_index: int
    distance_nm: float
    altitude_ft: float
    # Seconds after departure, scaled to the flight's planned duration.
    time_offset_s: float
    phase: FlightPhase


class FlightPath(BaseModel):
    """One climb, cruise and descent profile for the whole briefing.

    ``profile`` is the dense path ``[distance_nm, altitude_ft, time_s]`` in
    route order, in the path's own (unscaled) time. ``time_scale`` stretches it
    to the flight's planned duration when the pilot's duration differs from the
    computed one (#1005 decision 3); vertical rates are read unscaled, because
    they are the aircraft's performance, not a wind effect.
    """

    performance: AircraftPerformance
    cruise_altitude_ft: float
    cruise_ias_kt: float
    departure_elevation_ft: float = 0.0
    arrival_elevation_ft: float = 0.0
    total_distance_nm: float
    toc_nm: float
    tod_nm: float
    cruise_reached: bool = True
    peak_ft: float
    # Computed (still-air in v1) duration, hours.
    duration_h: float
    time_scale: float = 1.0
    profile: list[list[float]] = Field(default_factory=list)
    points: list[FlightPathPoint] = Field(default_factory=list)

    # --- reading the path -------------------------------------------------

    def _column(self, i: int) -> list[float]:
        return [p[i] for p in self.profile]

    def _interp(self, distance_nm: float, col: int) -> float:
        if not self.profile:
            return self.cruise_altitude_ft if col == 1 else 0.0
        dists = [p[0] for p in self.profile]
        d = min(max(distance_nm, dists[0]), dists[-1])
        j = bisect.bisect_left(dists, d)
        if j <= 0:
            return self.profile[0][col]
        if j >= len(dists):
            return self.profile[-1][col]
        d0, d1 = dists[j - 1], dists[j]
        v0, v1 = self.profile[j - 1][col], self.profile[j][col]
        if d1 <= d0:
            return v1
        return v0 + (v1 - v0) * (d - d0) / (d1 - d0)

    def altitude_at(self, distance_nm: float) -> float:
        """Path altitude (ft MSL) at a route distance."""
        return self._interp(distance_nm, 1)

    def time_at_s(self, distance_nm: float) -> float:
        """Seconds after departure at a route distance, scaled to the planned duration."""
        return self._interp(distance_nm, 2) * self.time_scale

    def phase_at(self, distance_nm: float) -> FlightPhase:
        if distance_nm < self.toc_nm - 1e-6:
            return "climb"
        if distance_nm > self.tod_nm + 1e-6:
            return "descent"
        return "cruise"

    def point(self, point_index: int) -> FlightPathPoint | None:
        for p in self.points:
            if p.point_index == point_index:
                return p
        return None

    def slice(self, lo_nm: float, hi_nm: float) -> "PathSlice":
        """What the aircraft flies between two route distances.

        Splits the stretch into its climb, level and descent parts: the
        altitude band each vertical part spans, its (unscaled) minutes, and the
        level distance at cruise / peak.
        """
        lo_nm, hi_nm = min(lo_nm, hi_nm), max(lo_nm, hi_nm)
        toc, tod = self.toc_nm, self.tod_nm
        climb = descent = None
        level_nm = 0.0
        c_lo, c_hi = lo_nm, min(hi_nm, toc)
        if c_hi > c_lo:
            a0, a1 = self.altitude_at(c_lo), self.altitude_at(c_hi)
            minutes = (self._interp(c_hi, 2) - self._interp(c_lo, 2)) / 60.0
            climb = VerticalPart(
                phase="climb", lo_nm=c_lo, hi_nm=c_hi,
                base_ft=min(a0, a1), top_ft=max(a0, a1), minutes=max(minutes, 0.0),
            )
        l_lo, l_hi = max(lo_nm, toc), min(hi_nm, tod)
        if l_hi > l_lo:
            level_nm = l_hi - l_lo
        d_lo, d_hi = max(lo_nm, tod), hi_nm
        if d_hi > d_lo:
            a0, a1 = self.altitude_at(d_lo), self.altitude_at(d_hi)
            minutes = (self._interp(d_hi, 2) - self._interp(d_lo, 2)) / 60.0
            descent = VerticalPart(
                phase="descent", lo_nm=d_lo, hi_nm=d_hi,
                base_ft=min(a0, a1), top_ft=max(a0, a1), minutes=max(minutes, 0.0),
            )
        return PathSlice(
            climb=climb, level_nm=level_nm, level_ft=self.peak_ft, descent=descent,
        )


class FlightPathSummary(BaseModel):
    """The path in a few numbers, on the advisories manifest (digest, MCP)."""

    preset: str
    live: bool = False
    toc_nm: float
    tod_nm: float
    total_distance_nm: float
    peak_ft: float
    cruise_reached: bool = True
    duration_h: float

    @classmethod
    def of(cls, path: "FlightPath", live: bool) -> "FlightPathSummary":
        return cls(
            preset=path.performance.preset, live=live, toc_nm=path.toc_nm,
            tod_nm=path.tod_nm, total_distance_nm=path.total_distance_nm,
            peak_ft=path.peak_ft, cruise_reached=path.cruise_reached,
            duration_h=path.duration_h * path.time_scale,
        )


class VerticalPart(BaseModel):
    """One climbing or descending part of a stretch of route."""

    phase: Literal["climb", "descent"]
    lo_nm: float
    hi_nm: float
    base_ft: float
    top_ft: float
    minutes: float

    @property
    def fpm(self) -> float:
        """Mean vertical rate over the part (ft/min, unscaled path time)."""
        if self.minutes <= 0:
            return 0.0
        return (self.top_ft - self.base_ft) / self.minutes


class PathSlice(BaseModel):
    climb: VerticalPart | None = None
    level_nm: float = 0.0
    level_ft: float = 0.0
    descent: VerticalPart | None = None

    @property
    def vertical(self) -> list[VerticalPart]:
        return [p for p in (self.climb, self.descent) if p is not None]

    @property
    def top_ft(self) -> float:
        tops = [p.top_ft for p in self.vertical]
        if self.level_nm > 0:
            tops.append(self.level_ft)
        return max(tops) if tops else self.level_ft
