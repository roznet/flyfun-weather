"""Compute airport weather conditions at departure and arrival."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.models import (
    CloudCoverage,
    HourlyForecast,
    RouteCrossSection,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.models.airport_conditions import (
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
    RunwayEnd,
    RunwayWind,
)

# Visibility conversion: meters to statute miles
_M_PER_SM = 1609.34

# Standard aviation flight category thresholds (ceiling ft / visibility SM)
_CEIL_LIFR, _CEIL_IFR, _CEIL_MVFR = 500, 1000, 3000
_VIS_LIFR, _VIS_IFR, _VIS_MVFR = 1, 3, 5


def format_wind_string(
    direction_deg: float | None,
    speed_kt: float | None,
    gust_kt: float | None,
) -> str:
    """Format wind as '230@11G25' (without units).

    Returns empty string if direction or speed are unavailable.
    """
    if direction_deg is None or speed_kt is None:
        return ""
    dir_rounded = round(direction_deg / 10) * 10
    gust_str = f"G{gust_kt:.0f}" if gust_kt else ""
    return f"{dir_rounded:03.0f}@{speed_kt:.0f}{gust_str}"


def classify_flight_category(
    ceiling_ft: float | None,
    visibility_sm: float | None,
) -> FlightCategory:
    """Classify flight category from ceiling and visibility.

    Standard aviation thresholds:
    - LIFR: ceiling < 500ft OR vis < 1sm
    - IFR:  ceiling 500-1000ft OR vis 1-3sm
    - MVFR: ceiling 1000-3000ft OR vis 3-5sm
    - VFR:  ceiling >= 3000ft AND vis >= 5sm

    When visibility is unavailable, classify from ceiling alone.
    When ceiling is unavailable, classify from visibility alone.
    When both unavailable, default to VFR (no data = no restriction).
    """
    cat_from_ceil = FlightCategory.VFR
    if ceiling_ft is not None:
        if ceiling_ft < _CEIL_LIFR:
            cat_from_ceil = FlightCategory.LIFR
        elif ceiling_ft < _CEIL_IFR:
            cat_from_ceil = FlightCategory.IFR
        elif ceiling_ft < _CEIL_MVFR:
            cat_from_ceil = FlightCategory.MVFR

    cat_from_vis = FlightCategory.VFR
    if visibility_sm is not None:
        if visibility_sm < _VIS_LIFR:
            cat_from_vis = FlightCategory.LIFR
        elif visibility_sm < _VIS_IFR:
            cat_from_vis = FlightCategory.IFR
        elif visibility_sm < _VIS_MVFR:
            cat_from_vis = FlightCategory.MVFR

    return FlightCategory.worst([cat_from_ceil, cat_from_vis])


def compute_runway_winds(
    runway_ends: list[RunwayEnd],
    wind_speed_kt: float,
    wind_direction_deg: float,
) -> list[RunwayWind]:
    """Compute crosswind and headwind for each runway end."""
    results: list[RunwayWind] = []
    for rwy in runway_ends:
        wc = compute_wind_components(wind_speed_kt, wind_direction_deg, rwy.heading_deg)
        results.append(RunwayWind(
            runway_id=rwy.id,
            heading_deg=rwy.heading_deg,
            crosswind_kt=round(abs(wc.crosswind_kt), 1),
            headwind_kt=round(wc.headwind_kt, 1),
        ))
    return results


def _ceiling_from_sounding(sounding: SoundingAnalysis) -> float | None:
    """Extract ceiling: lowest BKN or OVC cloud layer base (LCL-corrected)."""
    if sounding.indices and sounding.indices.sounding_ceiling_ft is not None:
        return sounding.indices.sounding_ceiling_ft
    # Fallback: always use DD source so reconcile_ceiling() compares
    # independent estimates (sounding-derived vs NWP-derived).
    ceilings = [
        cl.base_ft
        for cl in sounding.dd_cloud_layers
        if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
    ]
    return min(ceilings) if ceilings else None


def reconcile_ceiling(
    sounding: SoundingAnalysis | None,
    hourly: HourlyForecast | None,
) -> float | None:
    """Reconcile ceiling from sounding-derived cloud layers and NWP diagnostics.

    Strategy:
    - Both available: use the lower (more conservative for flight safety)
    - Only one available: use whichever exists
    - Neither: return None (VFR assumed)
    """
    sounding_ceil = _ceiling_from_sounding(sounding) if sounding else None

    nwp_ceil: float | None = None
    if hourly and hourly.nwp_cloud_diagnostics:
        nwp_ceil = hourly.nwp_cloud_diagnostics.ceiling_ft

    if sounding_ceil is not None and nwp_ceil is not None:
        return min(sounding_ceil, nwp_ceil)
    return sounding_ceil if sounding_ceil is not None else nwp_ceil


def _find_airport_rpa(
    analyses: list[RoutePointAnalysis],
    icao: str,
    is_departure: bool,
) -> RoutePointAnalysis | None:
    """Find the route point analysis for a specific airport."""
    # Try exact match on waypoint_icao
    for rpa in analyses:
        if rpa.waypoint_icao == icao:
            return rpa
    # Fallback: first or last point
    if analyses:
        return analyses[0] if is_departure else analyses[-1]
    return None


def _get_hourly_at_point(
    cross_sections: list[RouteCrossSection],
    model: str,
    point_index: int,
    target_time: datetime,
):
    """Get HourlyForecast for a model at a given route point and time."""
    for cs in cross_sections:
        if cs.model.value == model:
            if point_index < len(cs.point_forecasts):
                return cs.point_forecasts[point_index].at_time(target_time)
    return None


def _compute_for_airport(
    icao: str,
    name: str,
    is_departure: bool,
    analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    models: list[str],
    runway_ends: list[RunwayEnd],
) -> AirportConditionsSummary:
    """Compute conditions at one airport across all models."""
    rpa = _find_airport_rpa(analyses, icao, is_departure)
    if rpa is None:
        return AirportConditionsSummary(icao=icao, name=name, runway_ends=runway_ends)

    conditions: list[AirportModelCondition] = []
    for model in models:
        # Surface data from cross-section hourly forecast (needed for ceiling reconciliation)
        hourly = _get_hourly_at_point(
            cross_sections, model, rpa.point_index, rpa.interpolated_time,
        )

        # Ceiling: reconcile sounding-derived and NWP diagnostics
        sounding = rpa.sounding.get(model)
        ceiling_ft = reconcile_ceiling(sounding, hourly)

        visibility_sm: float | None = None
        wind_speed_kt: float | None = None
        wind_direction_deg: float | None = None
        wind_gust_kt: float | None = None

        if hourly:
            if hourly.visibility_m is not None:
                visibility_sm = round(hourly.visibility_m / _M_PER_SM, 1)
            wind_speed_kt = hourly.wind_speed_10m_kt
            wind_direction_deg = hourly.wind_direction_10m_deg
            wind_gust_kt = hourly.wind_gusts_10m_kt

        # Flight category
        flight_category = classify_flight_category(ceiling_ft, visibility_sm)

        # Runway crosswind
        all_runways: list[RunwayWind] = []
        best_runway: RunwayWind | None = None
        if runway_ends and wind_speed_kt is not None and wind_direction_deg is not None:
            all_runways = compute_runway_winds(runway_ends, wind_speed_kt, wind_direction_deg)
            if all_runways:
                best_runway = min(all_runways, key=lambda r: (r.crosswind_kt, -r.headwind_kt))

        conditions.append(AirportModelCondition(
            model=model,
            flight_category=flight_category,
            ceiling_ft=round(ceiling_ft) if ceiling_ft is not None else None,
            visibility_sm=visibility_sm,
            wind_speed_kt=round(wind_speed_kt, 1) if wind_speed_kt is not None else None,
            wind_direction_deg=round(wind_direction_deg) if wind_direction_deg is not None else None,
            wind_gust_kt=round(wind_gust_kt, 1) if wind_gust_kt is not None else None,
            best_runway=best_runway,
            all_runways=all_runways,
        ))

    return AirportConditionsSummary(
        icao=icao,
        name=name,
        runway_ends=runway_ends,
        conditions=conditions,
    )


def compute_airport_conditions(
    analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    models: list[str],
    dep_icao: str,
    dep_name: str,
    arr_icao: str,
    arr_name: str,
    runway_data: dict[str, list[RunwayEnd]] | None = None,
) -> AirportConditions:
    """Compute airport conditions at departure and arrival.

    Args:
        analyses: Route point analyses (from RouteAnalysesManifest).
        cross_sections: Per-model cross-section forecast data.
        models: Model names to evaluate.
        dep_icao: Departure airport ICAO code.
        dep_name: Departure airport name.
        arr_icao: Arrival airport ICAO code.
        arr_name: Arrival airport name.
        runway_data: Optional dict of ICAO -> list of RunwayEnd.

    Returns:
        AirportConditions with departure and arrival summaries.
    """
    runway_data = runway_data or {}

    departure = _compute_for_airport(
        icao=dep_icao,
        name=dep_name,
        is_departure=True,
        analyses=analyses,
        cross_sections=cross_sections,
        models=models,
        runway_ends=runway_data.get(dep_icao, []),
    )
    arrival = _compute_for_airport(
        icao=arr_icao,
        name=arr_name,
        is_departure=False,
        analyses=analyses,
        cross_sections=cross_sections,
        models=models,
        runway_ends=runway_data.get(arr_icao, []),
    )

    return AirportConditions(departure=departure, arrival=arrival)
