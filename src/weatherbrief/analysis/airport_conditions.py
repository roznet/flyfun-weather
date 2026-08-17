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

# Visibility conversion: meters to statute miles (single source of truth in units.py)
from weatherbrief.units import M_PER_SM as _M_PER_SM

# Standard aviation flight category thresholds (ceiling ft / visibility SM)
_CEIL_LIFR, _CEIL_IFR, _CEIL_MVFR = 500, 1000, 3000
_VIS_LIFR, _VIS_IFR, _VIS_MVFR = 1, 3, 5


# A gust is only shown when it exceeds the sustained wind by at least this many
# knots (comparing the rounded, displayed numbers). A gust within a few kt of the
# mean isn't operationally meaningful and just adds noise ("5G6" -> "5"). Keep in
# sync with web GUST_DISPLAY_MIN_EXCESS_KT (units.ts) and iOS
# WindFormat.gustDisplayMinExcessKt.
GUST_DISPLAY_MIN_EXCESS_KT = 4


def format_wind_string(
    direction_deg: float | None,
    speed_kt: float | None,
    gust_kt: float | None,
) -> str:
    """Format wind as '273@11G25' (without units).

    Direction is zero-padded to 3 digits (rounded to the nearest degree). The
    gust is dropped when absent or within ``GUST_DISPLAY_MIN_EXCESS_KT`` kt of
    the sustained speed. Returns empty string if direction or speed are
    unavailable.
    """
    if direction_deg is None or speed_kt is None:
        return ""
    dir_rounded = round(direction_deg)
    speed_rounded = round(speed_kt)
    gust_str = ""
    if gust_kt is not None and round(gust_kt) - speed_rounded >= GUST_DISPLAY_MIN_EXCESS_KT:
        gust_str = f"G{round(gust_kt)}"
    return f"{dir_rounded:03d}@{speed_rounded}{gust_str}"


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


def best_runway_of(all_runways: list[RunwayWind]) -> RunwayWind | None:
    """Pick the wind-best runway from precomputed winds: min crosswind, then max headwind.

    Single source of truth for the selection criterion, so callers that already
    have the per-runway winds (e.g. the airport-conditions table, which also
    keeps ``all_runways``) don't recompute them.
    """
    if not all_runways:
        return None
    return min(all_runways, key=lambda r: (r.crosswind_kt, -r.headwind_kt))


def _ceiling_from_sounding(sounding: SoundingAnalysis) -> float | None:
    """Extract ceiling: lowest BKN or OVC cloud layer base (LCL-corrected)."""
    if sounding.indices and sounding.indices.sounding_ceiling_ft is not None:
        return sounding.indices.sounding_ceiling_ft
    # Fallback reads the *active* slot, so it tracks the resolved cloud source
    # (NWP when the model has a native one, DD otherwise) like every other
    # cloud consumer. It used to pin to ``dd_cloud_layers`` to keep the two
    # sides of reconcile_ceiling independent — see meteorology-decisions.md §1,
    # which also records why that independence is no longer worth its cost.
    ceilings = [
        cl.base_ft
        for cl in sounding.cloud_layers
        if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
    ]
    return min(ceilings) if ceilings else None


def _nwp_ceiling_is_agl(model: str | None) -> bool:
    """Whether a model's NWP ceiling is already AGL (height above ground).

    ECMWF ceil/cbh/hcct are AGL (eccodes names + ECMWF Parameter DB); GFS
    ceiling is geopotential height (MSL); ICON CEILING/HBAS_CON/HTOP_CON are
    MSL — the eccodes names read "…above msl" and the DWD ICON database doc
    documents these cloud-geometry heights as MSL. Unknown/aggregate models
    (best_match, …) are treated as MSL — the majority case, and the
    conservative direction at elevated airports. (#441 finding #3)
    """
    return (model or "").lower() == "ecmwf"


def to_agl_ceiling(
    value: float | None,
    field_elevation_ft: float | None,
    *,
    source_is_agl: bool,
) -> float | None:
    """Convert a ceiling value to AGL (feet above field elevation). (#441 #3)

    Sources already AGL (ECMWF NWP ceiling) pass through unchanged; MSL sources
    (sounding geopotential height, GFS/ICON NWP ceiling) have the field
    elevation subtracted, clamped at 0 (a deck at/below the field = surface).
    When ``field_elevation_ft`` is None the value is returned unchanged — the
    legacy datum-naive behaviour for callers without elevation. Single source of
    truth shared by reconcile_ceiling and airport_consensus.best_ceiling.
    """
    if value is None:
        return None
    if field_elevation_ft is None:
        return value
    return value if source_is_agl else max(0.0, value - field_elevation_ft)


def reconcile_ceiling(
    sounding: SoundingAnalysis | None,
    hourly: HourlyForecast | None,
    *,
    field_elevation_ft: float | None = None,
    model: str | None = None,
) -> float | None:
    """Reconcile ceiling from sounding-derived cloud layers and NWP diagnostics.

    Strategy:
    - Both available: use the lower (more conservative for flight safety)
    - Only one available: use whichever exists
    - Neither: return None (VFR assumed)

    Datum (#441 finding #3): the sounding ceiling is geopotential-height MSL and
    the NWP ceiling is MSL for GFS/ICON but AGL for ECMWF. When
    ``field_elevation_ft`` is given, both are converted to **AGL** so the
    returned value (and the flight-category threshold applied to it) are on the
    same above-ground datum as METAR ceilings. Without it, the legacy
    datum-naive ``min()`` is returned for callers not yet migrated.
    """
    sounding_ceil = _ceiling_from_sounding(sounding) if sounding else None

    nwp_ceil: float | None = None
    if hourly and hourly.nwp_cloud_diagnostics:
        nwp_ceil = hourly.nwp_cloud_diagnostics.ceiling_ft

    # Convert each to AGL (no-op when field_elevation_ft is None → legacy min),
    # then take the lower on a single datum. Sounding is MSL; NWP is MSL for
    # GFS/ICON, AGL for ECMWF.
    s = to_agl_ceiling(sounding_ceil, field_elevation_ft, source_is_agl=False)
    n = to_agl_ceiling(
        nwp_ceil, field_elevation_ft, source_is_agl=_nwp_ceiling_is_agl(model),
    )
    candidates = [c for c in (s, n) if c is not None]
    return min(candidates) if candidates else None


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
    field_elevation_ft: float | None = None,
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

        # Ceiling: reconcile sounding-derived and NWP diagnostics, converted to
        # AGL for flight-category classification when field elevation is known.
        sounding = rpa.sounding.get(model)
        ceiling_ft = reconcile_ceiling(
            sounding, hourly, field_elevation_ft=field_elevation_ft, model=model,
        )

        visibility_m: float | None = None
        visibility_sm: float | None = None
        wind_speed_kt: float | None = None
        wind_direction_deg: float | None = None
        wind_gust_kt: float | None = None
        temperature_c: float | None = None
        dewpoint_c: float | None = None
        qnh_hpa: float | None = None

        if hourly:
            if hourly.visibility_m is not None:
                visibility_m = float(round(hourly.visibility_m))
                visibility_sm = round(hourly.visibility_m / _M_PER_SM, 1)
            wind_speed_kt = hourly.wind_speed_10m_kt
            wind_direction_deg = hourly.wind_direction_10m_deg
            wind_gust_kt = hourly.wind_gusts_10m_kt
            temperature_c = hourly.temperature_2m_c
            dewpoint_c = hourly.dewpoint_2m_c
            qnh_hpa = hourly.pressure_msl_hpa

        # Flight category
        flight_category = classify_flight_category(ceiling_ft, visibility_sm)

        # Runway crosswind
        all_runways: list[RunwayWind] = []
        best_runway: RunwayWind | None = None
        if runway_ends and wind_speed_kt is not None and wind_direction_deg is not None:
            all_runways = compute_runway_winds(runway_ends, wind_speed_kt, wind_direction_deg)
            best_runway = best_runway_of(all_runways)

        conditions.append(AirportModelCondition(
            model=model,
            flight_category=flight_category,
            ceiling_ft=round(ceiling_ft) if ceiling_ft is not None else None,
            visibility_m=visibility_m,
            visibility_sm=visibility_sm,
            wind_speed_kt=round(wind_speed_kt, 1) if wind_speed_kt is not None else None,
            wind_direction_deg=round(wind_direction_deg) if wind_direction_deg is not None else None,
            wind_gust_kt=round(wind_gust_kt, 1) if wind_gust_kt is not None else None,
            temperature_c=round(temperature_c, 1) if temperature_c is not None else None,
            dewpoint_c=round(dewpoint_c, 1) if dewpoint_c is not None else None,
            qnh_hpa=round(qnh_hpa, 1) if qnh_hpa is not None else None,
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
    airport_elevations: dict[str, float | None] | None = None,
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
        airport_elevations: Optional dict of ICAO -> published field elevation
            (ft AMSL). When present, the reconciled ceiling is converted to AGL
            for flight-category classification; absent, the legacy datum-naive
            ceiling is used. (#441 finding #3)

    Returns:
        AirportConditions with departure and arrival summaries.
    """
    runway_data = runway_data or {}
    elevations = airport_elevations or {}

    departure = _compute_for_airport(
        icao=dep_icao,
        name=dep_name,
        is_departure=True,
        analyses=analyses,
        cross_sections=cross_sections,
        models=models,
        runway_ends=runway_data.get(dep_icao, []),
        field_elevation_ft=elevations.get(dep_icao),
    )
    arrival = _compute_for_airport(
        icao=arr_icao,
        name=arr_name,
        is_departure=False,
        analyses=analyses,
        cross_sections=cross_sections,
        models=models,
        runway_ends=runway_data.get(arr_icao, []),
        field_elevation_ft=elevations.get(arr_icao),
    )

    return AirportConditions(departure=departure, arrival=arrival)
