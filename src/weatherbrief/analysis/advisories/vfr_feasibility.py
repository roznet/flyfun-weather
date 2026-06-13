"""VFR feasibility advisory — overall VFR flight viability assessment.

Composite advisory combining airport conditions, en-route cloud clearance,
VMC compliance, climb-out/descent corridor decks, and en-route precipitation
into a single go/no-go style assessment for VFR flights.

The precipitation axis (shared classifier with the en-route precipitation
advisory) is capped at AMBER here: a pilot VMC-on-top is not directly
affected by surface rain below, but widespread snow or heavy rain degrades
visibility and every descent/divert option, which deserves a composite
caution. The standalone advisory still grades it fully (snow can RED).
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent
from weatherbrief.analysis.advisories.enroute_precip import classify_enroute_precip
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_vfr(
    ctx: RouteContext,
    model: str,
) -> tuple[AdvisoryStatus, str]:
    """Check departure and arrival airport conditions for VFR feasibility.

    Uses the flight category (VFR/MVFR/IFR/LIFR) which already encodes
    ceiling and visibility thresholds per aviation standards.

    Returns (status, detail_fragment).
    """
    if ctx.airport_conditions is None:
        return AdvisoryStatus.GREEN, ""

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    parts: list[str] = []
    worst = AdvisoryStatus.GREEN

    loc = ctx.locale
    for label_key, icao, cond in [("airport.dep", dep.icao, dep_cond), ("airport.arr", arr.icao, arr_cond)]:
        if cond is None:
            continue
        label = adv_t(label_key, loc)
        cat = cond.flight_category
        if cat in (FlightCategory.IFR, FlightCategory.LIFR):
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(f"{label} {icao} {cat.value}")
        elif cat == FlightCategory.MVFR:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(f"{label} {icao} MVFR")

    detail = " | ".join(parts) if parts else ""
    return worst, detail


def _check_enroute_vfr(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
) -> tuple[int, int, int, int]:
    """Check en-route cloud clearance for VFR.

    Returns (total, imc_count, marginal_count, clear_count).
    - imc_count: points where cruise is inside BKN/OVC cloud
    - marginal_count: points where cloud clearance < threshold (but not in cloud)
    """
    total = 0
    imc_count = 0
    marginal_count = 0
    clear_count = 0
    cruise = ctx.cruise_altitude_ft

    for rpa in ctx.analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        total += 1

        in_cloud = False
        marginal = False

        for cl in sounding.cloud_layers:
            if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                continue
            # Check if cruise altitude is inside the cloud layer
            if cl.base_ft <= cruise <= cl.top_ft:
                in_cloud = True
                break
            # Check vertical clearance from cloud base or top
            dist_to_base = abs(cruise - cl.base_ft)
            dist_to_top = abs(cruise - cl.top_ft)
            min_dist = min(dist_to_base, dist_to_top)
            if min_dist < cloud_clearance_ft:
                marginal = True

        if in_cloud:
            imc_count += 1
        elif marginal:
            marginal_count += 1
        else:
            clear_count += 1

    return total, imc_count, marginal_count, clear_count


def _field_elevation_ft(ctx: RouteContext, distance_nm: float) -> float:
    """Terrain elevation (ft MSL) at an along-track distance, 0.0 if unknown.

    Used as the floor for the climb/descent corridor check so a cloud layer
    buried below the field (base above cruise is already excluded) is not
    mistaken for a deck the aircraft must transit.
    """
    if ctx.elevation is None or not ctx.elevation.points:
        return 0.0
    nearest = min(ctx.elevation.points, key=lambda p: abs(p.distance_nm - distance_nm))
    return nearest.elevation_ft


def _check_corridor_vfr(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
) -> tuple[AdvisoryStatus, list[str]]:
    """Check the climb-out and descent corridors for transitable cloud decks.

    Cruise altitude can be clear (handled by ``_check_enroute_vfr``) while a
    BKN/OVC deck still sits between the surface and cruise near an airport — a
    layer the flight must climb up through or descend down through to remain
    VMC. Within ``corridor_nm`` of the origin (climb-out) and destination
    (descent), a BKN/OVC layer lying entirely between field elevation and cruise
    (``floor < top_ft < cruise``) is such a deck. A layer whose top reaches
    cruise is cruise-in-cloud and is left to ``_check_enroute_vfr`` rather than
    double-counted here. OVC -> RED (no holes to climb/descend through),
    BKN -> AMBER (likely gaps, pilot judgement).

    Returns (worst_status, detail_fragments).
    """
    cruise = ctx.cruise_altitude_ft
    total = ctx.total_distance_nm
    worst = AdvisoryStatus.GREEN
    parts: list[str] = []
    loc = ctx.locale

    dep_icao = ctx.airport_conditions.departure.icao if ctx.airport_conditions else None
    arr_icao = ctx.airport_conditions.arrival.icao if ctx.airport_conditions else None

    phases = (
        ("climb", "vfr.corridor_climb", dep_icao, adv_t("airport.dep", loc)),
        ("descent", "vfr.corridor_descent", arr_icao, adv_t("airport.arr", loc)),
    )

    for phase, key, icao, fallback in phases:
        has_ovc = False
        has_bkn = False
        for rpa in ctx.analyses:
            d = rpa.distance_from_origin_nm or 0.0
            # Distance to this phase's airport, plus a nearer-half tiebreak so the
            # climb and descent corridors stay mutually exclusive when they would
            # otherwise overlap (short routes / large terminal_corridor_nm). A
            # point exactly at the midpoint is attributed to the climb-out.
            if phase == "climb":
                in_corridor = d <= corridor_nm and d <= total / 2
            else:
                in_corridor = (total - d) <= corridor_nm and d > total / 2
            if not in_corridor:
                continue
            sounding = rpa.sounding.get(model)
            if sounding is None:
                continue
            floor = _field_elevation_ft(ctx, d)
            for cl in sounding.cloud_layers:
                if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                    continue
                # A deck we must transit on climb/descent: lies entirely between
                # the field and cruise. A layer whose top reaches cruise is
                # cruise-in-cloud — left to _check_enroute_vfr, not double-counted.
                if floor < cl.top_ft < cruise:
                    if cl.coverage == CloudCoverage.OVC:
                        has_ovc = True
                    else:
                        has_bkn = True

        if has_ovc:
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(adv_t(key, loc, cov="OVC", icao=icao or fallback))
        elif has_bkn:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(adv_t(key, loc, cov="BKN", icao=icao or fallback))

    return worst, parts


@register
class VFRFeasibilityEvaluator:
    """Evaluates overall feasibility of VFR flight along the route.

    Combines airport flight category, en-route cloud clearance, and VMC
    compliance into a single advisory. GREEN means VFR conditions throughout;
    AMBER means marginal conditions; RED means IFR conditions detected.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="vfr_feasibility",
            name="VFR Feasibility",
            short_description="Overall VFR flight viability",
            description=(
                "Composite assessment of VFR flight feasibility. Checks airport "
                "conditions (VFR/MVFR/IFR), en-route cloud clearance relative to "
                "cruise altitude, minimum cloud separation, and whether the "
                "climb-out and descent corridors near the airports are clear of "
                "BKN/OVC decks the flight would have to transit. RED indicates IFR "
                "conditions, IMC at cruise, or an OVC deck blocking climb/descent; "
                "AMBER flags marginal conditions or a BKN deck in a corridor."
            ),
            category="flight_rules",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="cloud_clearance_ft",
                    label="Cloud clearance",
                    description="Minimum vertical separation from BKN/OVC cloud layers",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=2000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="imc_pct_amber",
                    label="IMC % (amber)",
                    description="Route percentage in IMC or marginal clearance for amber",
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="imc_pct_red",
                    label="IMC % (red)",
                    description="Route percentage in IMC for red",
                    type="percent",
                    unit="%",
                    default=30,
                    min=10,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="terminal_corridor_nm",
                    label="Terminal corridor",
                    description="Distance from departure/arrival to check for BKN/OVC decks in the climb-out and descent path",
                    type="distance",
                    unit="nm",
                    default=5,
                    min=0,
                    max=20,
                    step=1,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        cloud_clearance_ft = params.get("cloud_clearance_ft", 1000)
        imc_pct_amber = params.get("imc_pct_amber", 15)
        imc_pct_red = params.get("imc_pct_red", 30)
        corridor_nm = params.get("terminal_corridor_nm", 5)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail = _check_airport_vfr(ctx, model)

            # 2. En-route cloud clearance
            total, imc_count, marginal_count, _ = _check_enroute_vfr(
                ctx, model, cloud_clearance_ft
            )

            # 3. Climb-out / descent corridor decks (BKN/OVC below cruise near airports)
            corridor_status, corridor_parts = _check_corridor_vfr(
                ctx, model, corridor_nm
            )

            loc = ctx.locale
            if (
                total == 0
                and airport_status == AdvisoryStatus.GREEN
                and not airport_detail
                and corridor_status == AdvisoryStatus.GREEN
            ):
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            # 4. Determine en-route status
            affected = imc_count + marginal_count
            enroute_status = AdvisoryStatus.GREEN
            enroute_detail = ""

            if total > 0:
                imc_pct = 100.0 * imc_count / total
                affected_pct = 100.0 * affected / total

                ext = format_extent(affected, total, ctx.total_distance_nm)

                if imc_pct >= imc_pct_red:
                    enroute_status = AdvisoryStatus.RED
                    enroute_detail = adv_t("vfr.imc_over", loc, extent=ext)
                elif affected_pct >= imc_pct_amber:
                    enroute_status = AdvisoryStatus.AMBER
                    if marginal_count > 0 and imc_count > 0:
                        enroute_detail = adv_t("vfr.imc_marginal", loc, extent=ext)
                    elif imc_count > 0:
                        enroute_detail = adv_t("vfr.imc_over", loc, extent=ext)
                    else:
                        enroute_detail = adv_t("vfr.marginal", loc, extent=ext)
                elif affected > 0:
                    enroute_detail = adv_t("vfr.minor", loc, extent=format_extent(affected, total, ctx.total_distance_nm))

            # 5. En-route precipitation (visibility proxy) — capped at AMBER
            # in the composite; the standalone advisory grades it fully.
            # Deliberately called WITHOUT params: the composite always uses the
            # fixed precip defaults, independent of any per-user tuning of the
            # standalone EnroutePrecipEvaluator. The params dict here carries
            # only VFR keys, so the two can grade differently if a user tunes
            # the standalone — that divergence is intentional; the composite is
            # a fixed-threshold sanity floor, not a mirror of the standalone.
            precip_status, precip_detail, _, _, precip_signal = (
                classify_enroute_precip(ctx, model)
            )
            # Old pack without precip data (no signal / UNAVAILABLE) → treat as
            # GREEN in the composite rather than penalising a missing field.
            if not precip_signal or precip_status == AdvisoryStatus.UNAVAILABLE:
                precip_status, precip_detail = AdvisoryStatus.GREEN, ""
            elif precip_status == AdvisoryStatus.RED:
                precip_status = AdvisoryStatus.AMBER

            # 6. Combine airport + en-route + corridor + precipitation
            status = _worst_status(
                airport_status, enroute_status, corridor_status, precip_status,
            )

            detail_parts = []
            if airport_detail:
                detail_parts.append(airport_detail)
            if enroute_detail:
                detail_parts.append(enroute_detail)
            detail_parts.extend(corridor_parts)
            if precip_status != AdvisoryStatus.GREEN and precip_detail:
                detail_parts.append(precip_detail)

            if not detail_parts:
                if total > 0:
                    detail = adv_t("vfr.throughout", loc)
                else:
                    detail = adv_t("vfr.airports_ok", loc)
            else:
                detail = " | ".join(detail_parts)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, params)
