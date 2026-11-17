"""Aircraft icing advisory: one icing card graded for the aircraft flown (#827).

Replaces ``icing_escape`` (non-FIKI), ``fiki_icing`` and the icing axis of
``ifr_feasibility``, which graded the same icing three different ways and could
show "RED for icing" next to "warm escape available" on one briefing. The
design and every number in it are recorded in meteorology-decisions §56.

The flight's aircraft picks the column (``RouteContext.equipage``):

* **FIKI:** may stay in some icing, but not too much.
* **Non-FIKI:** cannot be in icing. An unknown aircraft grades here too, and
  the card says so.

Per model, the planned profile (climb at departure, cruise, descent at arrival)
is walked in route order with an **exposure clock**: minutes spent in icing,
reset only by time in above-freezing air (ice does not shed in cold clear air).
Each run of the clock is a *stretch*, graded with the table below. The model
grade is the worst stretch.

====================================  ========  ==========
Stretch                               FIKI      non-FIKI
====================================  ========  ==========
no icing                              GREEN     GREEN
short, escape available               AMBER     AMBER
short, no escape                      AMBER     RED
long, escape available                AMBER     RED
long, no escape                       RED       RED
SEVERE or SLD anywhere in it          RED       RED
====================================  ========  ==========

*Short* depends on what the ice is: light rime up to ``short_light_rime_min``,
anything else (light clear/mixed, moderate) up to ``short_other_min``. SEVERE
and SLD are beyond certification and not tunable.

*Escape* means an icing-free level the iced aircraft can reach, at **every**
iced point of the stretch:

* down (both columns): below ``max(freezing level, lowest icing cloud base) -
  500 ft`` (§8a), at least ``terrain_margin_ft`` above terrain. Unknown terrain
  blocks it (§51b), and so does freezing rain or a warm nose: the air below is
  not ice-free.
* up (FIKI only): 500 ft above the highest icing top, at or below the aircraft
  ceiling - 1,000 ft (an iced aircraft climbs poorly). Non-FIKI never escapes
  up; on-top is still offered as a planning altitude by the mitigation.

Freezing rain itself is graded by ``freezing_precip``; here it only removes the
downward escape, so it is not counted twice.

**SLD (#411 step 4, §84)** has two levels, read from ``SldZone`` rather than
the dormant ``IcingZone.sld_risk`` flag:

* *confirmed* (a freezing-rain zone that reaches the surface): RED when the
  climb, the cruise or the descent crosses it, and a wall for the altitude
  solver. Here the climb and descent are counted, which ``freezing_precip``
  does not do.
* *unconfirmed aloft* (the model's rain water colder than about -2 °C, no
  surface signal): AMBER at most, priced but never a wall.

``WB_SLD_ICING`` sets how far this goes: ``shadow`` (the default) computes the
SLD grade and records it on ``ModelAdvisoryResult.shadow`` without touching the
card; ``live`` grades it; ``off`` skips it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._flight_path import (
    path_rate_limit,
    path_shadow_ctx,
    record_path_shadow,
)
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    build_cost_model,
    driving_method_id,
    hazardous_icing_zones,
    max_terrain_near_point,
    summarize_evidence,
    terrain_at_distance,
    to_mitigation_profile,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.advisories.vertical_profile import INF, Blockage, solve
from weatherbrief.analysis.flight_path import ICED_RATE_FACTOR
from weatherbrief.analysis.sounding.sld import assess_sld_aloft_zones, sld_aloft_thresholds
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    AircraftEquipage,
    HighlightSeverity,
    IcingRisk,
    IcingType,
    IcingZone,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    ShadowGrade,
    PathSlice,
    SldZone,
    SoundingAnalysis,
    VerticalPart,
)

logger = logging.getLogger(__name__)

ADVISORY_ID = "aircraft_icing"
# ``Mitigation.addresses`` of the escape level. Keeps the retired card's id so
# clients keyed on it still find it; the digest matches on this constant.
ESCAPE_MITIGATION = "icing_escape"

# §8a: the escape level sits this far below the freezing level / icing cloud base.
ESCAPE_MARGIN_FT = 500
# An up-escape clears the highest icing top by this much ...
UP_ESCAPE_TOP_MARGIN_FT = 500
# ... and must stay this far below the aircraft ceiling: an iced aircraft climbs
# poorly, so the last 1,000 ft of its book ceiling are not counted on.
CEILING_MARGIN_FT = 1000
# Vertical rate used to turn an iced climb/descent column into minutes. A
# conservative figure for a light aircraft carrying ice; the climb is where the
# exposure is, and a faster figure would understate it.
CLIMB_DESCENT_FPM = 500

_RISK_ORDER = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]

# Solver occupancy costs for the icing-free-cruise mitigation. Only their order
# matters: the solver prefers a cost-0 profile, and a FIKI aircraft is never
# routed through anything a non-FIKI one could not also be offered.
_LIGHT_COST = 1.0
_MODERATE_COST = 3.0

# §84: how far the two-level SLD grade goes. Shadow until the amber-volume
# replay on stored winter flights has run (decision 1 of 2026-11-15).
SLD_MODE_ENV = "WB_SLD_ICING"
SLD_SHADOW_RULE = "sld_icing"


_warned_sld_modes: set[str] = set()


def sld_icing_mode() -> str:
    """``live``, ``off`` or ``shadow`` (the default, and any other value)."""
    mode = os.environ.get(SLD_MODE_ENV, "").strip().lower()
    if mode in ("live", "off", "shadow"):
        return mode
    if mode and mode not in _warned_sld_modes:
        # Called per solver cell: warn once per bad value, not per call.
        _warned_sld_modes.add(mode)
        logger.warning("%s=%r is not live, shadow or off: running in shadow", SLD_MODE_ENV, mode)
    return "shadow"


def point_sld_zones(sounding: SoundingAnalysis) -> list[SldZone]:
    """Every SLD zone at one point: the stored ones plus the bands aloft."""
    stored = list(sounding.sld_zones)
    confirmed = [z for z in stored if z.confirmed]
    return stored + assess_sld_aloft_zones(sounding.derived_levels, confirmed)


def equipage_of(ctx: RouteContext) -> AircraftEquipage:
    """The context's equipage, with "none resolved" as an unknown aircraft."""
    return ctx.equipage if ctx.equipage is not None else AircraftEquipage()


def _worse(a: IcingRisk, b: IcingRisk) -> IcingRisk:
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


# --- the shared per-point icing picture ------------------------------------


@dataclass(frozen=True)
class PathIcing:
    """One climbing or descending part of a point's cell on the flight path (#1005)."""

    part: VerticalPart
    zones: tuple[IcingZone, ...] = ()
    sld: tuple[SldZone, ...] = ()

    @property
    def phase(self) -> str:
        return self.part.phase

    def minutes(self, fiki: bool) -> float:
        """Minutes in the iced part of the band at the path's rate.

        Non-FIKI flies the iced band at the clean rate × ``ICED_RATE_FACTOR``
        (#1005 decision 5); FIKI at the clean rate.
        """
        fpm = self.part.fpm
        if fpm <= 0:
            return 0.0
        if not fiki:
            fpm *= ICED_RATE_FACTOR
        return _iced_thickness(self.zones) / fpm


def _iced_thickness(zones) -> float:
    """Feet of the band covered by at least one zone (overlaps counted once)."""
    spans = sorted((z.base_ft, z.top_ft) for z in zones if z.top_ft > z.base_ft)
    total = 0.0
    cur_lo = cur_hi = None
    for lo, hi in spans:
        if cur_hi is None or lo > cur_hi:
            if cur_hi is not None:
                total += cur_hi - cur_lo
            cur_lo, cur_hi = lo, hi
        else:
            cur_hi = max(cur_hi, hi)
    if cur_hi is not None:
        total += cur_hi - cur_lo
    return total


def _clip(zones, base: float, top: float):
    """Zones overlapping ``[base, top]``, clipped to it."""
    return tuple(
        z.model_copy(update={"base_ft": max(z.base_ft, base), "top_ft": min(z.top_ft, top)})
        for z in zones if z.base_ft < top and z.top_ft > base
    )


@dataclass(frozen=True)
class IcingPoint:
    """What one model says about icing at one route point, for the planned profile.

    ``cruise_zones`` are the hazardous zones within ``cruise_band_ft`` of cruise;
    ``column_zones`` those between the ground and cruise (what a climb or descent
    here would pass through). The escape levels are ``None`` when no escape
    exists in that direction; ``down_block`` names why.
    """

    index: int
    distance_nm: float
    assessed: bool
    cruise_zones: tuple[IcingZone, ...] = ()
    column_zones: tuple[IcingZone, ...] = ()
    warm_at_cruise: bool = False
    escape_down_ft: float | None = None
    # Lowest altitude the down-escape may use here (terrain + margin).
    escape_floor_ft: float | None = None
    down_block: str | None = None  # freezing_rain | warm_nose | terrain | terrain_unknown | no_level
    escape_up_ft: float | None = None
    method_id: str | None = None
    # SLD zones (§84) within the cruise band, and in the ground-to-cruise
    # column. Empty when ``WB_SLD_ICING=off``.
    sld_cruise: tuple[SldZone, ...] = ()
    sld_column: tuple[SldZone, ...] = ()
    # Flight path (#1005), when live: what the aircraft flies in this point's
    # cell. ``level_nm`` is None in flat mode (today's endpoint columns);
    # otherwise the level distance at cruise, and ``vertical`` the climb and
    # descent parts crossing the cell, each with its iced zones (clipped to
    # the part's altitude band) and SLD zones.
    level_nm: float | None = None
    vertical: tuple[PathIcing, ...] = ()

    @property
    def on_path(self) -> bool:
        return self.level_nm is not None

    @property
    def iced_at_cruise(self) -> bool:
        return bool(self.cruise_zones)

    def has_escape(self, fiki: bool) -> bool:
        return self.escape_down_ft is not None or (fiki and self.escape_up_ft is not None)


def _lowest_icing_cloud_base(sounding: SoundingAnalysis, zones: list[IcingZone]) -> float | None:
    """Lowest base of a cloud layer that overlaps an icing zone (§8a)."""
    lowest: float | None = None
    for cl in sounding.cloud_layers:
        if any(cl.base_ft < z.top_ft and cl.top_ft > z.base_ft for z in zones):
            if lowest is None or cl.base_ft < lowest:
                lowest = cl.base_ft
    return lowest


def _escape_down(
    sounding: SoundingAnalysis,
    zones: list[IcingZone],
    iced: list[IcingZone],
    terrain_ft: float | None,
    terrain_margin_ft: float,
) -> tuple[float | None, str | None]:
    """The §8a descent escape at one point, or why there is none.

    ``zones`` are all hazardous zones at the point; ``iced`` the ones the
    aircraft is in (at cruise, or in the climb/descent column).
    """
    precip = sounding.precipitation
    if precip is not None and precip.freezing_rain_risk:
        return None, "freezing_rain"
    if precip is not None and precip.warm_nose_base_ft is not None:
        # §56 (as agreed in #827): no descent escape under a warm nose either.
        # Below it the air is sub-zero again, and any precipitation falling
        # through it arrives supercooled, even with no freezing-rain flag yet.
        return None, "warm_nose"
    fz = sounding.indices.freezing_level_ft if sounding.indices else None
    candidates = [v for v in (fz, _lowest_icing_cloud_base(sounding, zones)) if v is not None]
    if candidates:
        level = max(candidates) - ESCAPE_MARGIN_FT
    else:
        level = min(z.base_ft for z in zones) - ESCAPE_MARGIN_FT
    # Never above the icing being escaped: a freezing level above an iced
    # cruise (a zone the method put in warm air) must not read as "escape
    # below a level higher than you are".
    level = min(level, min(z.base_ft for z in iced) - ESCAPE_MARGIN_FT)
    if terrain_ft is None:
        return None, "terrain_unknown"
    if level < terrain_ft + terrain_margin_ft:
        return None, "terrain"
    if any(z.base_ft <= level <= z.top_ft for z in zones):
        # A lower icing layer sits at the would-be escape level.
        return None, "no_level"
    return level, None


def _escape_up(zones: list[IcingZone], ceiling_ft: float) -> float | None:
    """The FIKI climb-out escape above the highest icing top, if within reach."""
    level = max(z.top_ft for z in zones) + UP_ESCAPE_TOP_MARGIN_FT
    return level if level <= ceiling_ft - CEILING_MARGIN_FT else None


def icing_picture(
    ctx: RouteContext, model: str, params: dict[str, float],
) -> list[IcingPoint]:
    """One :class:`IcingPoint` per entry of ``ctx.analyses``, index-aligned."""
    cruise = ctx.cruise_altitude_ft
    band = params.get("cruise_band_ft", 1000)
    terrain_margin = params.get("terrain_margin_ft", 1000)
    equipage = equipage_of(ctx)
    ceiling = equipage.ceiling_ft or ctx.flight_ceiling_ft
    with_sld = sld_icing_mode() != "off"
    points: list[IcingPoint] = []
    for idx, rpa in enumerate(ctx.analyses):
        dist = rpa.distance_from_origin_nm or 0.0
        sounding = rpa.sounding.get(model)
        if sounding is None or not sounding.active_icing_available:
            points.append(IcingPoint(index=idx, distance_nm=dist, assessed=False))
            continue
        cell = ctx.path_slice(idx)
        if cell is not None:
            points.append(_path_icing_point(
                ctx, idx, dist, sounding, cell, band, terrain_margin, equipage,
                ceiling, with_sld,
            ))
            continue
        zones = hazardous_icing_zones(sounding.icing_zones)
        cruise_zones = tuple(
            z for z in zones if z.base_ft <= cruise + band and z.top_ft >= cruise - band
        )
        # Clipped to the column a climb or descent here flies through: from
        # the ground at this point (sea level when terrain is unknown) to cruise.
        ground = terrain_at_distance(ctx.elevation, dist) or 0.0
        column_zones = tuple(
            z.model_copy(update={"base_ft": max(z.base_ft, ground), "top_ft": min(z.top_ft, cruise)})
            for z in zones if z.base_ft < cruise and z.top_ft > ground
        )
        sld = point_sld_zones(sounding) if with_sld else []
        sld_cruise = tuple(
            z for z in sld if z.base_ft <= cruise + band and z.top_ft >= cruise - band
        )
        # Clipped to the column like ``column_zones``: the climb or descent
        # crosses only the part between the ground and cruise.
        sld_column = tuple(
            z.model_copy(update={"base_ft": max(z.base_ft, ground), "top_ft": min(z.top_ft, cruise)})
            for z in sld if z.base_ft < cruise and z.top_ft > ground
        )
        fz = sounding.indices.freezing_level_ft if sounding.indices else None
        warm = not cruise_zones and fz is not None and cruise < fz
        down_ft = down_block = up_ft = floor_ft = None
        relevant = list(cruise_zones or column_zones)
        if relevant:
            terrain = max_terrain_near_point(ctx.elevation, dist)
            floor_ft = None if terrain is None else terrain + terrain_margin
            down_ft, down_block = _escape_down(
                sounding, zones, relevant, terrain, terrain_margin,
            )
            if equipage.graded_fiki:
                up_ft = _escape_up(zones, ceiling)
        points.append(IcingPoint(
            index=idx, distance_nm=dist, assessed=True,
            cruise_zones=cruise_zones, column_zones=column_zones,
            warm_at_cruise=warm, escape_down_ft=down_ft, escape_floor_ft=floor_ft,
            down_block=down_block,
            escape_up_ft=up_ft, method_id=sounding.icing_method_effective,
            sld_cruise=sld_cruise, sld_column=sld_column,
        ))
    return points


def _path_icing_point(
    ctx: RouteContext, idx: int, dist: float, sounding: SoundingAnalysis,
    cell: PathSlice, band: float, terrain_margin: float,
    equipage: AircraftEquipage, ceiling: float, with_sld: bool,
) -> IcingPoint:
    """One point on the flight path (#1005): the zones its cell actually flies through.

    The level part reads the cruise band around the path's level (cruise, or
    the peak of a short leg); each climb / descent part reads the zones its
    altitude band crosses at this point's sounding.
    """
    zones = hazardous_icing_zones(sounding.icing_zones)
    sld = point_sld_zones(sounding) if with_sld else []
    level = cell.level_ft
    cruise_zones: tuple[IcingZone, ...] = ()
    sld_cruise: tuple[SldZone, ...] = ()
    if cell.level_nm > 0:
        cruise_zones = tuple(
            z for z in zones if z.base_ft <= level + band and z.top_ft >= level - band
        )
        sld_cruise = tuple(
            z for z in sld if z.base_ft <= level + band and z.top_ft >= level - band
        )
    vertical = tuple(
        PathIcing(
            part=part,
            zones=_clip(zones, part.base_ft, part.top_ft),
            sld=_clip(sld, part.base_ft, part.top_ft),
        )
        for part in cell.vertical
    )
    column_zones = tuple(z for v in vertical for z in v.zones)
    sld_column = tuple(z for v in vertical for z in v.sld)
    fz = sounding.indices.freezing_level_ft if sounding.indices else None
    # Warm: nothing iced anywhere in the cell and the whole cell is flown
    # below the freezing level (the clock resets).
    warm = (
        not cruise_zones and not column_zones and fz is not None and cell.top_ft < fz
    )
    down_ft = down_block = up_ft = floor_ft = None
    relevant = list(cruise_zones) + list(column_zones)
    if relevant:
        terrain = max_terrain_near_point(ctx.elevation, dist)
        floor_ft = None if terrain is None else terrain + terrain_margin
        down_ft, down_block = _escape_down(sounding, zones, relevant, terrain, terrain_margin)
        if equipage.graded_fiki:
            up_ft = _escape_up(zones, ceiling)
    return IcingPoint(
        index=idx, distance_nm=dist, assessed=True,
        cruise_zones=cruise_zones, column_zones=column_zones,
        warm_at_cruise=warm, escape_down_ft=down_ft, escape_floor_ft=floor_ft,
        down_block=down_block, escape_up_ft=up_ft,
        method_id=sounding.icing_method_effective,
        sld_cruise=sld_cruise, sld_column=sld_column,
        level_nm=cell.level_nm, vertical=vertical,
    )


def icing_cell_cost(
    sounding: SoundingAnalysis, alt_ft: float, fiki: bool,
    sld_cache: dict[int, list[SldZone]] | None = None,
) -> float:
    """Solver occupancy cost of one altitude at one point, by the card's own rules.

    Membership of a hazardous zone decides, with no freezing-level shortcut:
    the old ``icing_escape`` cost read 0 below the freezing level even under a
    warm nose. SEVERE and SLD are walls for every aircraft; non-FIKI may only be
    priced through light rime (design decision 8 of the solver).

    With ``WB_SLD_ICING=live``, a confirmed SLD zone (§84) is a wall too, and
    an unconfirmed band aloft is priced like moderate icing: never a wall, but
    never offered as an icing-free level either. ``sld_cache`` (keyed by
    ``id(sounding)``, valid for one solve) keeps the solver from re-detecting
    the zones at every altitude cell.
    """
    if not sounding.active_icing_available:
        return INF
    worst = 0.0
    if sld_icing_mode() == "live":
        if sld_cache is None:
            zones = point_sld_zones(sounding)
        else:
            zones = sld_cache.get(id(sounding))
            if zones is None:
                zones = sld_cache[id(sounding)] = point_sld_zones(sounding)
        for sz in zones:
            if sz.base_ft <= alt_ft <= sz.top_ft:
                if sz.confirmed:
                    return INF
                worst = max(worst, _MODERATE_COST)
    for z in hazardous_icing_zones(sounding.icing_zones):
        if not (z.base_ft <= alt_ft <= z.top_ft):
            continue
        if z.sld_risk or z.risk == IcingRisk.SEVERE:
            return INF
        if fiki:
            worst = max(worst, _MODERATE_COST if z.risk == IcingRisk.MODERATE else _LIGHT_COST)
        elif z.risk == IcingRisk.LIGHT and z.icing_type == IcingType.RIME:
            worst = max(worst, _LIGHT_COST)
        else:
            return INF
    return worst


# --- the exposure clock ----------------------------------------------------


@dataclass
class _Stretch:
    """One run of the exposure clock."""

    minutes: float = 0.0
    points: list[IcingPoint] = field(default_factory=list)
    zones: list[IcingZone] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    escape: bool = True
    # Where on the route each phase of the stretch is flown (path mode only,
    # #1005): phase -> [lo_nm, hi_nm].
    spans: dict[str, list[float]] = field(default_factory=dict)

    def add(
        self, point: IcingPoint, zones, minutes: float, phase: str, fiki: bool,
        lo_nm: float | None = None, hi_nm: float | None = None,
    ) -> None:
        self.minutes += minutes
        if all(p.index != point.index for p in self.points):
            self.points.append(point)
        self.zones.extend(z for z in zones if z not in self.zones)
        if phase not in self.phases:
            self.phases.append(phase)
        self.escape = self.escape and point.has_escape(fiki)
        if lo_nm is not None and hi_nm is not None:
            span = self.spans.get(phase)
            if span is None:
                self.spans[phase] = [lo_nm, hi_nm]
            else:
                span[0], span[1] = min(span[0], lo_nm), max(span[1], hi_nm)

    @property
    def worst_risk(self) -> IcingRisk:
        worst = IcingRisk.NONE
        for z in self.zones:
            worst = _worse(worst, z.risk)
        return worst

    @property
    def sld(self) -> bool:
        return any(z.sld_risk for z in self.zones)

    @property
    def light_rime_only(self) -> bool:
        return all(
            z.risk == IcingRisk.LIGHT and z.icing_type == IcingType.RIME and not z.sld_risk
            for z in self.zones
        )


def _cell_minutes(points: list[IcingPoint], i: int, total_nm: float, speed_kt: float) -> float:
    """Minutes spent in point ``i``'s midpoint-owned cell at cruise groundspeed."""
    lo = 0.0 if i == 0 else (points[i - 1].distance_nm + points[i].distance_nm) / 2
    hi = total_nm if i == len(points) - 1 else (points[i].distance_nm + points[i + 1].distance_nm) / 2
    return max(hi - lo, 0.0) / max(speed_kt, 1.0) * 60.0


def _column_minutes(zones) -> float:
    """Minutes to climb or descend through the iced part of a column."""
    thickness = sum(max(z.top_ft - z.base_ft, 0.0) for z in zones)
    return thickness / CLIMB_DESCENT_FPM


def build_stretches(
    picture: list[IcingPoint], total_nm: float, speed_kt: float, fiki: bool,
) -> list[_Stretch]:
    """Walk the planned profile in route order and cut it into icing stretches."""
    if any(p.on_path for p in picture):
        return _build_path_stretches(picture, speed_kt, fiki)
    ordered = sorted(picture, key=lambda p: p.distance_nm)
    stretches: list[_Stretch] = []
    current: _Stretch | None = None

    first = next((p for p in ordered if p.assessed), None)
    last = next((p for p in reversed(ordered) if p.assessed), None)

    # The first and last cells are flown climbing and descending, not at
    # cruise: their column minutes already cover that time, so the cruise
    # cell there only adds what the column does not (max of the two, not the
    # sum, which counted the same minutes twice).
    column_min: dict[int, float] = {}
    descent_min = 0.0
    if first is not None and first.column_zones:
        current = _Stretch()
        column_min[first.index] = _column_minutes(first.column_zones)
        current.add(first, first.column_zones, column_min[first.index], "climb", fiki)
    if last is not None and last.column_zones:
        descent_min = _column_minutes(last.column_zones)
        column_min[last.index] = column_min.get(last.index, 0.0) + descent_min

    for i, point in enumerate(ordered):
        if not point.assessed:
            continue  # unknown: the clock neither runs nor resets
        if point.iced_at_cruise:
            if current is None:
                current = _Stretch()
            cell = _cell_minutes(ordered, i, total_nm, speed_kt)
            current.add(
                point, point.cruise_zones,
                max(cell - column_min.get(point.index, 0.0), 0.0), "cruise", fiki,
            )
        elif point.warm_at_cruise and current is not None:
            stretches.append(current)
            current = None

    if last is not None and last.column_zones:
        if current is None:
            current = _Stretch()
        current.add(last, last.column_zones, descent_min, "descent", fiki)
    if current is not None:
        stretches.append(current)
    return stretches


def _build_path_stretches(
    picture: list[IcingPoint], speed_kt: float, fiki: bool,
) -> list[_Stretch]:
    """The exposure clock on the flight path (#1005).

    Each point's cell is flown in route order: its climb part, its level
    part, its descent part. Vertical minutes come from the path's rate over
    the iced thickness (× ``ICED_RATE_FACTOR`` for non-FIKI); level minutes
    from the cruise groundspeed, as in flat mode.
    """
    ordered = sorted(picture, key=lambda p: p.distance_nm)
    stretches: list[_Stretch] = []
    current: _Stretch | None = None

    def add(point, zones, minutes, phase, lo, hi):
        nonlocal current
        if current is None:
            current = _Stretch()
        current.add(point, zones, minutes, phase, fiki, lo, hi)

    for point in ordered:
        if not point.assessed:
            continue  # unknown: the clock neither runs nor resets
        climb = [v for v in point.vertical if v.phase == "climb" and v.zones]
        descent = [v for v in point.vertical if v.phase == "descent" and v.zones]
        for v in climb:
            add(point, v.zones, v.minutes(fiki), "climb", v.part.lo_nm, v.part.hi_nm)
        if point.cruise_zones and point.level_nm:
            add(
                point, point.cruise_zones, point.level_nm / max(speed_kt, 1.0) * 60.0,
                "cruise", point.distance_nm, point.distance_nm,
            )
        for v in descent:
            add(point, v.zones, v.minutes(fiki), "descent", v.part.lo_nm, v.part.hi_nm)
        if point.warm_at_cruise and current is not None:
            stretches.append(current)
            current = None
    if current is not None:
        stretches.append(current)
    return stretches


def grade_stretch(
    stretch: _Stretch, fiki: bool, params: dict[str, float],
) -> tuple[AdvisoryStatus, str]:
    """The grade table, for one stretch. Returns (status, reason_code)."""
    if stretch.sld:
        return AdvisoryStatus.RED, "sld"
    if stretch.worst_risk == IcingRisk.SEVERE:
        return AdvisoryStatus.RED, "severe_icing"
    limit = (
        params.get("short_light_rime_min", 15) if stretch.light_rime_only
        else params.get("short_other_min", 5)
    )
    long = stretch.minutes > limit
    if fiki:
        if long and not stretch.escape:
            return AdvisoryStatus.RED, "long_no_escape"
        return AdvisoryStatus.AMBER, "long_escape" if long else (
            "short_escape" if stretch.escape else "short_no_escape"
        )
    if not long and stretch.escape:
        return AdvisoryStatus.AMBER, "short_escape"
    if long:
        return AdvisoryStatus.RED, "long_escape" if stretch.escape else "long_no_escape"
    return AdvisoryStatus.RED, "short_no_escape"


# --- SLD (§84) ---------------------------------------------------------------


@dataclass
class _SldHit:
    """Where the planned profile crosses SLD at one point, and its grade."""

    point: IcingPoint
    zones: list[SldZone] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return any(z.confirmed for z in self.zones)

    @property
    def grade(self) -> tuple[AdvisoryStatus, str]:
        # Decision 2 of 2026-11-15: only confirmed SLD is RED; aloft is AMBER at most.
        if self.confirmed:
            return AdvisoryStatus.RED, "sld"
        return AdvisoryStatus.AMBER, "sld_aloft"


def sld_hits(picture: list[IcingPoint]) -> list[_SldHit]:
    """The points where the climb, cruise or descent crosses an SLD zone.

    The climb is flown at the first assessed point and the descent at the
    last, as in :func:`build_stretches`; cruise everywhere.
    """
    assessed = [p for p in sorted(picture, key=lambda p: p.distance_nm) if p.assessed]
    if not assessed:
        return []
    first, last = assessed[0], assessed[-1]
    hits: list[_SldHit] = []
    for p in assessed:
        hit = _SldHit(point=p)

        def take(zones, phase: str) -> None:
            if not zones:
                return
            hit.zones.extend(z for z in zones if z not in hit.zones)
            if phase not in hit.phases:
                hit.phases.append(phase)

        if p.on_path:
            # Flight path (#1005): the parts this point's cell actually flies.
            for v in p.vertical:
                if v.phase == "climb":
                    take(v.sld, "climb")
            take(p.sld_cruise, "cruise")
            for v in p.vertical:
                if v.phase == "descent":
                    take(v.sld, "descent")
            if hit.zones:
                hits.append(hit)
            continue
        if p is first:
            take(p.sld_column, "climb")
        take(p.sld_cruise, "cruise")
        if p is last:
            take(p.sld_column, "descent")
        if hit.zones:
            hits.append(hit)
    return hits


def _sld_sentence(hits: list[_SldHit], loc: str | None) -> tuple[AdvisoryStatus, str, str]:
    """(status, reason_code, sentence) of the worst SLD hits."""
    confirmed = [h for h in hits if h.confirmed]
    lead = confirmed or hits
    status, reason = lead[0].grade
    phases: list[str] = []
    for h in lead:
        phases.extend(ph for ph in h.phases if ph not in phases)
    order = ["climb", "cruise", "descent"]
    names = ", ".join(adv_t(f"aircraft_icing.phase.{ph}", loc) for ph in sorted(phases, key=order.index))
    if reason == "sld":
        return status, reason, adv_t("aircraft_icing.sld", loc, phases=names)
    temp = sld_aloft_thresholds()[0]
    return status, reason, adv_t(
        "aircraft_icing.sld_aloft", loc, phases=names, temp=f"{temp:g}",
    )


# --- per-model grade -------------------------------------------------------


@dataclass
class IcingModelGrade:
    """One model's icing grade, shared by the card and ``ifr_feasibility``.

    ``region_cells`` and ``assessed`` are index-aligned with ``ctx.analyses``
    so a composite can merge this axis into its own ribbon (the §22 pattern).
    """

    status: AdvisoryStatus
    detail: str
    samples: list[EvidenceSample]
    region_cells: list[tuple[float, FlaggedCell | None]]
    assessed: list[bool]
    stretches: list[_Stretch]
    worst: _Stretch | None
    worst_reason: str | None
    # §84 shadow record: what the SLD grade would be, when it runs in shadow.
    shadow: list[ShadowGrade] = field(default_factory=list)


def profile_declares_fiki(settings: dict, template_key: str | None) -> bool:
    """Whether a profile still says FIKI, now that FIKI belongs to the aircraft (#850).

    True for the IFR FIKI template, or a profile with the retired FIKI Icing card
    on: a pilot who chose either may not have ticked FIKI on the aircraft form.
    """
    if template_key == "ifr_fiki":
        return True
    enabled = (settings.get("advisories") or {}).get("enabled") or {}
    return isinstance(enabled, dict) and enabled.get("fiki_icing") is True


def _graded_label(equipage: AircraftEquipage, loc: str | None) -> str:
    if not equipage.known:
        return adv_t("aircraft_icing.graded_unknown", loc)
    if equipage.fiki:
        return adv_t("aircraft_icing.graded_fiki", loc)
    if equipage.profile_fiki:
        return adv_t("aircraft_icing.graded_non_fiki_profile", loc)
    return adv_t("aircraft_icing.graded_non_fiki", loc)


def _phase_label(stretch: _Stretch, phase: str, total_nm: float, loc: str | None) -> str:
    """The phase name, with where it is flown when the path placed it (#1005)."""
    span = stretch.spans.get(phase)
    if span is None or phase == "cruise":
        return adv_t(f"aircraft_icing.phase.{phase}", loc)
    lo, hi = span
    if phase == "climb":
        return adv_t("flight_path.where.climb", loc, lo=round(lo), hi=max(round(hi), round(lo) + 1))
    out_lo, out_hi = total_nm - hi, total_nm - lo
    return adv_t(
        "flight_path.where.descent", loc,
        hi=max(round(out_hi), round(out_lo) + 1), lo=round(out_lo),
    )


def _stretch_sentence(
    stretch: _Stretch, reason: str, fiki: bool, count: int, loc: str | None,
    total_nm: float = 0.0,
) -> str:
    phases = ", ".join(_phase_label(stretch, p, total_nm, loc) for p in stretch.phases)
    if reason == "sld":
        text = adv_t("aircraft_icing.sld", loc, phases=phases)
    elif reason == "severe_icing":
        text = adv_t("aircraft_icing.severe", loc, phases=phases)
    else:
        text = adv_t(
            "aircraft_icing.exposure", loc,
            risk=adv_t(f"aircraft_icing.risk.{stretch.worst_risk.value}", loc),
            minutes=max(1, round(stretch.minutes)), phases=phases,
        )
        down_all = all(p.escape_down_ft is not None for p in stretch.points)
        common = common_escape_down_ft(stretch)
        if stretch.escape and common is not None:
            text += adv_t("aircraft_icing.escape_down", loc, alt=common)
        elif stretch.escape and down_all:
            text += adv_t("aircraft_icing.escape_down_varies", loc)
        elif stretch.escape:
            text += adv_t("aircraft_icing.escape_up", loc)
        else:
            text += adv_t("aircraft_icing.no_escape", loc)
    if count > 1:
        text += adv_t("aircraft_icing.more_stretches", loc, n=count - 1)
    return text


def common_escape_down_ft(stretch: _Stretch) -> int | None:
    """One descent level that works at every point of the stretch, if any.

    Each point's escape clears its own terrain; quoting the lowest of them as
    "descend below X" would send the pilot into the hill at a higher-terrain
    point of the same stretch. So a single level is quoted only when it also
    clears every point's floor; otherwise the escape exists point by point.
    """
    down = [p.escape_down_ft for p in stretch.points]
    floors = [p.escape_floor_ft for p in stretch.points]
    if not down or any(v is None for v in down) or any(f is None for f in floors):
        return None
    level = _floor500(min(down))
    return level if level >= max(floors) else None


def _floor500(alt: float) -> int:
    return int(alt // 500 * 500)


def _ceil500(alt: float) -> int:
    return int(-(-alt // 500) * 500)


def grade_aircraft_icing_model(
    ctx: RouteContext, model: str, params: dict[str, float],
) -> IcingModelGrade:
    """Grade one model's icing for the flight's aircraft."""
    equipage = equipage_of(ctx)
    fiki = equipage.graded_fiki
    loc = ctx.locale
    picture = icing_picture(ctx, model, params)
    stretches = build_stretches(
        picture, ctx.total_distance_nm, ctx.cruise_groundspeed_kt, fiki,
    )

    graded = [(s, *grade_stretch(s, fiki, params)) for s in stretches]
    # Per point: (status, reason, the zones that span its highlight cell).
    point_grade: dict[int, tuple[AdvisoryStatus, str, list]] = {}

    def mark(p: IcingPoint, status: AdvisoryStatus, reason: str, zones: list) -> None:
        prev = point_grade.get(p.index)
        if prev is None or AdvisoryStatus.worst([prev[0], status]) != prev[0]:
            point_grade[p.index] = (status, reason, zones)

    for s, status, reason in graded:
        for p in s.points:
            mark(p, status, reason, list(p.cruise_zones or p.column_zones))

    mode = sld_icing_mode()
    hits = sld_hits(picture) if mode != "off" else []
    sld_lead: tuple[AdvisoryStatus, str, str] | None = None
    shadow: list[ShadowGrade] = []
    if hits:
        sld_lead = _sld_sentence(hits, loc)
        if mode == "live":
            for h in hits:
                mark(h.point, *h.grade, list(h.zones))
        else:
            shadow.append(ShadowGrade(
                rule=SLD_SHADOW_RULE, status=sld_lead[0], reason_code=sld_lead[1],
                points=len(hits), detail=sld_lead[2],
            ))
            sld_lead = None

    samples: list[EvidenceSample] = []
    cells: list[tuple[float, FlaggedCell | None]] = []
    for p in picture:
        if not p.assessed:
            samples.append(EvidenceSample(
                distance_nm=p.distance_nm, assessed=False,
                severity=HighlightSeverity.UNAVAILABLE,
            ))
            cells.append((p.distance_nm, None))
            continue
        hit = point_grade.get(p.index)
        if hit is None:
            samples.append(EvidenceSample(
                distance_nm=p.distance_nm, assessed=True, severity=HighlightSeverity.GREEN,
            ))
            cells.append((p.distance_nm, None))
            continue
        status, reason, zones = hit
        severity = HighlightSeverity.RED if status == AdvisoryStatus.RED else HighlightSeverity.AMBER
        cell = FlaggedCell(
            kind="icing_band", severity=severity,
            base_ft=int(min(z.base_ft for z in zones)),
            top_ft=int(max(z.top_ft for z in zones)),
            reason_code=reason, metric_id="icing", method_id=p.method_id,
        )
        samples.append(EvidenceSample(
            distance_nm=p.distance_nm, assessed=True, severity=severity, region=cell,
        ))
        cells.append((p.distance_nm, cell))

    label = _graded_label(equipage, loc)
    assessed = [p.assessed for p in picture]
    if not any(assessed):
        status, detail, worst, worst_reason = (
            AdvisoryStatus.UNAVAILABLE, adv_t("no_data", loc), None, None,
        )
    elif not graded and sld_lead is None:
        status, detail, worst, worst_reason = (
            AdvisoryStatus.GREEN, f"{label}: {adv_t('aircraft_icing.no_icing', loc)}", None, None,
        )
    elif not graded:
        status, worst_reason, sentence = sld_lead
        detail, worst = f"{label}: {sentence}", None
    else:
        status = AdvisoryStatus.worst([g[1] for g in graded])
        # The worst stretch, longest first among equals: the one the pilot
        # most needs named.
        worst, _, worst_reason = max(
            graded,
            key=lambda g: (
                [AdvisoryStatus.GREEN, AdvisoryStatus.AMBER, AdvisoryStatus.RED].index(g[1]),
                g[0].minutes,
            ),
        )
        detail = f"{label}: {_stretch_sentence(worst, worst_reason, fiki, len(graded), loc, ctx.total_distance_nm)}"
        if sld_lead is not None:
            # SLD leads the sentence when it is at least as bad as the worst
            # stretch: it is the more specific hazard. Otherwise it follows.
            sld_status, sld_reason, sentence = sld_lead
            if AdvisoryStatus.worst([status, sld_status]) == sld_status:
                rest = detail[len(label) + 2:]
                detail = f"{label}: {sentence}. {rest[:1].upper()}{rest[1:]}"
                status, worst_reason = sld_status, sld_reason
            else:
                detail = f"{detail}. {sentence[:1].upper()}{sentence[1:]}"

    return IcingModelGrade(
        status=status, detail=detail, samples=samples, region_cells=cells,
        assessed=assessed, stretches=stretches, worst=worst, worst_reason=worst_reason,
        shadow=shadow,
    )


def resolve_aircraft_icing_params(ctx: RouteContext) -> dict[str, float]:
    """This card's parameters as ``evaluate_all`` would hand them to it.

    For a composite that grades its icing axis with this card (``ifr_feasibility``):
    the pilot's overrides when the card is in play, catalog defaults otherwise.
    """
    defaults = {p.key: p.default for p in AircraftIcingEvaluator.catalog_entry().parameters}
    return {**defaults, **ctx.advisory_params.get(ADVISORY_ID, {})}


# --- mitigations -----------------------------------------------------------


def _mitigations(
    ctx: RouteContext, model: str, grade: IcingModelGrade, params: dict[str, float],
) -> list[Mitigation]:
    """Escape level and icing-free cruise level. Advice only; the grade stands."""
    if grade.status not in (AdvisoryStatus.AMBER, AdvisoryStatus.RED):
        return []
    loc = ctx.locale
    fiki = equipage_of(ctx).graded_fiki
    out: list[Mitigation] = []

    worst = grade.worst
    alt: int | None = None
    if worst is not None and worst.escape and worst.points:
        common = common_escape_down_ft(worst)
        if common is not None:
            alt = common
            detail = adv_t("aircraft_icing.mitigation.escape_down", loc, alt=alt)
        elif fiki and all(p.escape_up_ft is not None for p in worst.points):
            alt = _ceil500(max(p.escape_up_ft for p in worst.points))
            detail = adv_t("aircraft_icing.mitigation.escape_up", loc, alt=alt)
    if alt is not None:
        out.append(Mitigation(
            kind=MitigationKind.ALTITUDE, addresses=ESCAPE_MITIGATION, detail=detail,
            # Escaping ends the exposure but does not undo the ice already on.
            mitigated_status=AdvisoryStatus.AMBER, altitude_ft=alt,
        ))

    # One solve: the soundings outlive it, so id() keys stay valid.
    sld_cache: dict[int, list[SldZone]] = {}
    cm = build_cost_model(
        ctx, model, lambda s, a: icing_cell_cost(s, a, fiki, sld_cache),
        params.get("terrain_margin_ft", 1000),
    )
    if cm is not None:
        result = solve(
            cm, preferred_alt_ft=ctx.cruise_altitude_ft, rate_limit=path_rate_limit(ctx),
        )
        if not isinstance(result, Blockage) and result.total_cost == 0:
            alts = {s.alt_ft for s in result.segments}
            cruise = ctx.cruise_altitude_ft
            if alts != {cruise}:
                if len(alts) == 1:
                    alt = next(iter(alts))
                    key = "climb" if alt > cruise else "descend"
                    detail = adv_t(f"aircraft_icing.mitigation.cruise_{key}", loc, alt=alt)
                else:
                    alt = None
                    detail = adv_t("aircraft_icing.mitigation.cruise_profile", loc)
                out.append(Mitigation(
                    kind=MitigationKind.ALTITUDE, addresses="icing_cruise", detail=detail,
                    mitigated_status=AdvisoryStatus.GREEN, altitude_ft=alt,
                    profile=to_mitigation_profile(result),
                ))
    return out


# --- the card --------------------------------------------------------------


@register
class AircraftIcingEvaluator:
    """Icing graded for the flight's aircraft, FIKI or not (#827)."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id=ADVISORY_ID,
            name="Icing",
            short_description="Icing graded for your aircraft (FIKI or not)",
            description=(
                "Icing at cruise and in the climb and descent, graded for the "
                "flight's aircraft. A FIKI aircraft may stay in some icing but "
                "not too much; a non-FIKI aircraft cannot be in icing at all. "
                "Exposure is counted in minutes and only resets in "
                "above-freezing air. An escape is an icing-free level the iced "
                "aircraft can reach: down below the freezing level or cloud "
                "base and clear of terrain, or (FIKI only) up above the tops "
                "within the aircraft ceiling. Severe icing and SLD are always "
                "red. An aircraft not marked FIKI is graded as non-FIKI."
            ),
            category="icing",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="short_light_rime_min",
                    label="Short exposure, light rime",
                    description="Longest light-rime exposure still counted as short",
                    type="number", unit="min", default=15, min=5, max=30, step=5,
                ),
                AdvisoryParameterDef(
                    key="short_other_min",
                    label="Short exposure, other icing",
                    description=(
                        "Longest light clear/mixed or moderate exposure still "
                        "counted as short"
                    ),
                    type="number", unit="min", default=5, min=1, max=15, step=1,
                ),
                AdvisoryParameterDef(
                    key="terrain_margin_ft",
                    label="Terrain margin",
                    description="Minimum clearance above terrain for a descent escape",
                    type="altitude", unit="ft", default=1000, min=500, max=3000, step=500,
                ),
                AdvisoryParameterDef(
                    key="cruise_band_ft",
                    label="Cruise band",
                    description="Icing within this distance of cruise counts as at cruise",
                    type="altitude", unit="ft", default=1000, min=0, max=2000, step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        per_model: list[ModelAdvisoryResult] = []
        for model in ctx.models:
            grade = grade_aircraft_icing_model(ctx, model, params)
            summary = summarize_evidence(
                grade.samples, ctx.total_distance_nm, speed_kt=ctx.cruise_groundspeed_kt,
            )
            status, detail = grade.status, grade.detail
            # Coverage tolerance (#391): a clear verdict from too small a share
            # of the route cannot vouch for the rest. Flagged verdicts stand.
            if status == AdvisoryStatus.GREEN and summary.below_coverage:
                status, detail = AdvisoryStatus.UNAVAILABLE, adv_t("no_data", ctx.locale)
            highlights = summary.highlights if summary.assessed > 0 else None
            result = ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=summary.affected, total=summary.assessed,
                total_distance_nm=ctx.total_distance_nm,
                extent=summary.extent,
                mitigations=_mitigations(ctx, model, grade, params),
                highlights=highlights,
                primary_method_id=driving_method_id(highlights, status),
            )
            result.shadow = grade.shadow
            per_model.append(result)
        out = RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)
        # #1005 shadow: what the card would grade on the flight path. Grade
        # only (no mitigations, no solver), so the shadow stays cheap.
        shadow_ctx = path_shadow_ctx(ctx)
        if shadow_ctx is not None:
            by_model = {}
            for model in ctx.models:
                g = grade_aircraft_icing_model(shadow_ctx, model, params)
                points = sum(1 for smp in g.samples if smp.region is not None)
                by_model[model] = (g.status, g.detail, points, g.worst_reason)
            record_path_shadow(out, by_model)
        return out
