"""Aircraft icing advisory: one icing card graded for the aircraft flown (#827).

Replaces ``icing_escape`` (non-FIKI), ``fiki_icing`` and the icing axis of
``ifr_feasibility``, which graded the same icing three different ways and could
show "RED for icing" next to "warm escape available" on one briefing. The
design and every number in it are recorded in meteorology-decisions §52.

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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    build_cost_model,
    driving_method_id,
    hazardous_icing_zones,
    max_terrain_near_point,
    summarize_evidence,
    to_mitigation_profile,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.advisories.vertical_profile import INF, Blockage, solve
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
    SoundingAnalysis,
)

ADVISORY_ID = "aircraft_icing"

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


def equipage_of(ctx: RouteContext) -> AircraftEquipage:
    """The context's equipage, with "none resolved" as an unknown aircraft."""
    return ctx.equipage if ctx.equipage is not None else AircraftEquipage()


def _worse(a: IcingRisk, b: IcingRisk) -> IcingRisk:
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


# --- the shared per-point icing picture ------------------------------------


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
        # §52 (as agreed in #827): no descent escape under a warm nose either.
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
    points: list[IcingPoint] = []
    for idx, rpa in enumerate(ctx.analyses):
        dist = rpa.distance_from_origin_nm or 0.0
        sounding = rpa.sounding.get(model)
        if sounding is None or not sounding.active_icing_available:
            points.append(IcingPoint(index=idx, distance_nm=dist, assessed=False))
            continue
        zones = hazardous_icing_zones(sounding.icing_zones)
        cruise_zones = tuple(
            z for z in zones if z.base_ft <= cruise + band and z.top_ft >= cruise - band
        )
        # Clipped to the column a climb or descent here flies through.
        column_zones = tuple(
            z.model_copy(update={"base_ft": max(z.base_ft, 0.0), "top_ft": min(z.top_ft, cruise)})
            for z in zones if z.base_ft < cruise and z.top_ft > 0
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
        ))
    return points


def icing_cell_cost(sounding: SoundingAnalysis, alt_ft: float, fiki: bool) -> float:
    """Solver occupancy cost of one altitude at one point, by the card's own rules.

    Membership of a hazardous zone decides, with no freezing-level shortcut:
    the old ``icing_escape`` cost read 0 below the freezing level even under a
    warm nose. SEVERE and SLD are walls for every aircraft; non-FIKI may only be
    priced through light rime (design decision 8 of the solver).
    """
    if not sounding.active_icing_available:
        return INF
    worst = 0.0
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

    def add(self, point: IcingPoint, zones, minutes: float, phase: str, fiki: bool) -> None:
        self.minutes += minutes
        if all(p.index != point.index for p in self.points):
            self.points.append(point)
        self.zones.extend(zones)
        if phase not in self.phases:
            self.phases.append(phase)
        self.escape = self.escape and point.has_escape(fiki)

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


def _graded_label(equipage: AircraftEquipage, loc: str | None) -> str:
    if not equipage.known:
        return adv_t("aircraft_icing.graded_unknown", loc)
    return adv_t(
        "aircraft_icing.graded_fiki" if equipage.fiki else "aircraft_icing.graded_non_fiki", loc,
    )


def _stretch_sentence(stretch: _Stretch, reason: str, fiki: bool, count: int, loc: str | None) -> str:
    phases = ", ".join(adv_t(f"aircraft_icing.phase.{p}", loc) for p in stretch.phases)
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
    point_grade: dict[int, tuple[AdvisoryStatus, str, _Stretch]] = {}
    for s, status, reason in graded:
        for p in s.points:
            prev = point_grade.get(p.index)
            if prev is None or AdvisoryStatus.worst([prev[0], status]) != prev[0]:
                point_grade[p.index] = (status, reason, s)

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
        status, reason, s = hit
        severity = HighlightSeverity.RED if status == AdvisoryStatus.RED else HighlightSeverity.AMBER
        zones = list(p.cruise_zones or p.column_zones)
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
    elif not graded:
        status, detail, worst, worst_reason = (
            AdvisoryStatus.GREEN, f"{label}: {adv_t('aircraft_icing.no_icing', loc)}", None, None,
        )
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
        detail = f"{label}: {_stretch_sentence(worst, worst_reason, fiki, len(graded), loc)}"

    return IcingModelGrade(
        status=status, detail=detail, samples=samples, region_cells=cells,
        assessed=assessed, stretches=stretches, worst=worst, worst_reason=worst_reason,
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
            kind=MitigationKind.ALTITUDE, addresses="icing_escape", detail=detail,
            # Escaping ends the exposure but does not undo the ice already on.
            mitigated_status=AdvisoryStatus.AMBER, altitude_ft=alt,
        ))

    cm = build_cost_model(
        ctx, model, lambda s, a: icing_cell_cost(s, a, fiki),
        params.get("terrain_margin_ft", 1000),
    )
    if cm is not None:
        result = solve(cm, preferred_alt_ft=ctx.cruise_altitude_ft)
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
            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=summary.affected, total=summary.assessed,
                total_distance_nm=ctx.total_distance_nm,
                extent=summary.extent,
                mitigations=_mitigations(ctx, model, grade, params),
                highlights=highlights,
                primary_method_id=driving_method_id(highlights, status),
            ))
        return RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)
