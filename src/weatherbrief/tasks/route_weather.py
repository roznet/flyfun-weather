"""Route METAR/TAF fetch and observation-vs-model comparison.

Only used on D-0 (day of flight) when real observations add value.
"""

from __future__ import annotations

import logging
from datetime import datetime

from weatherbrief.models.analysis import RouteConfig, WaypointForecast
from weatherbrief.models.observations import (
    AirportObservation,
    ObservationComparison,
    RouteObservations,
)

logger = logging.getLogger(__name__)

# Flight category severity: higher index = worse conditions
_CATEGORY_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def _worst_category(categories: list[str]) -> str | None:
    """Return the most restrictive flight category from a list."""
    valid = [c for c in categories if c in _CATEGORY_ORDER]
    if not valid:
        return None
    return max(valid, key=lambda c: _CATEGORY_ORDER[c])


def _find_nearest_waypoint(
    enroute_distance_nm: float | None,
    route: RouteConfig,
    route_distances: list[float],
) -> str:
    """Find the route waypoint closest to a given enroute distance."""
    if enroute_distance_nm is None or not route_distances:
        return route.origin.icao
    best_idx = 0
    best_delta = abs(route_distances[0] - enroute_distance_nm)
    for i, d in enumerate(route_distances[1:], 1):
        delta = abs(d - enroute_distance_nm)
        if delta < best_delta:
            best_delta = delta
            best_idx = i
    return route.waypoints[best_idx].icao


def _compute_route_distances(route: RouteConfig) -> list[float]:
    """Compute cumulative great-circle distance for each waypoint in NM."""
    import math

    distances = [0.0]
    for i in range(1, len(route.waypoints)):
        prev = route.waypoints[i - 1]
        curr = route.waypoints[i]
        lat1, lon1 = math.radians(prev.lat), math.radians(prev.lon)
        lat2, lon2 = math.radians(curr.lat), math.radians(curr.lon)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        nm = c * 3440.065  # Earth radius in NM
        distances.append(distances[-1] + nm)
    return distances


def run_route_weather(
    route: RouteConfig,
    target_time: datetime,
    corridor_nm: float,
    airports_db_path: str,
) -> RouteObservations:
    """Fetch METAR/TAF for airports along a route via euro_aip RouteWeatherService.

    Args:
        route: Flight route configuration.
        target_time: Flight departure/target time (for TAF matching).
        corridor_nm: Corridor half-width for airport search.
        airports_db_path: Path to euro_aip SQLite database.

    Returns:
        RouteObservations with per-airport METAR/TAF data.
    """
    from euro_aip.briefing.weather.analysis import WeatherAnalyzer
    from euro_aip.briefing.weather.route_weather import RouteWeatherService
    from euro_aip.storage.database_storage import DatabaseStorage

    storage = DatabaseStorage(airports_db_path)
    model = storage.load_model()

    route_icaos = [wp.icao for wp in route.waypoints]
    route_distances = _compute_route_distances(route)

    service = RouteWeatherService()
    result = service.fetch_route_weather(
        route_icaos=route_icaos,
        corridor_nm=corridor_nm,
        model=model,
    )

    airports: list[AirportObservation] = []
    metar_categories: list[str] = []
    taf_categories: list[str] = []
    all_phenomena: list[str] = []

    for raw in result.airports:
        nearest_wp = _find_nearest_waypoint(
            raw.enroute_distance_nm, route, route_distances,
        )

        obs = AirportObservation(
            icao=raw.icao,
            name=raw.name,
            distance_from_route_nm=raw.distance_from_route_nm,
            enroute_distance_nm=raw.enroute_distance_nm,
            nearest_waypoint_icao=nearest_wp,
        )

        metar = raw.latest_metar
        if metar is not None:
            obs.has_metar = True
            obs.metar_raw = metar.raw_text
            obs.metar_time = metar.observation_time
            if metar.flight_category is not None:
                obs.metar_flight_category = metar.flight_category.value
                metar_categories.append(metar.flight_category.value)
            obs.metar_ceiling_ft = metar.ceiling_ft
            obs.metar_visibility_m = metar.visibility_meters
            obs.metar_wind_dir = metar.wind_direction
            obs.metar_wind_speed_kt = metar.wind_speed
            obs.metar_wind_gust_kt = metar.wind_gust
            obs.metar_weather = list(metar.weather_conditions)
            obs.metar_temperature_c = metar.temperature
            obs.metar_dewpoint_c = metar.dewpoint
            obs.metar_qnh = metar.altimeter
            all_phenomena.extend(metar.weather_conditions)

        taf = raw.latest_taf
        if taf is not None:
            obs.has_taf = True
            obs.taf_raw = taf.raw_text
            applicable = WeatherAnalyzer.find_applicable_taf(taf, target_time)
            if applicable is not None and applicable.flight_category is not None:
                obs.taf_flight_category_at_eta = applicable.flight_category.value
                obs.taf_trend_type = applicable.trend_type
                taf_categories.append(applicable.flight_category.value)

        airports.append(obs)

    # De-duplicate phenomena
    unique_phenomena = sorted(set(all_phenomena))

    return RouteObservations(
        corridor_nm=corridor_nm,
        fetch_time=datetime.utcnow(),
        airports_found=len(airports),
        airports_with_metar=sum(1 for a in airports if a.has_metar),
        airports_with_taf=sum(1 for a in airports if a.has_taf),
        airports=airports,
        worst_metar_category=_worst_category(metar_categories),
        worst_taf_category=_worst_category(taf_categories),
        phenomena_along_route=unique_phenomena,
    )


def _classify_discrepancy(
    obs_cat: str | None,
    model_cat: str | None,
) -> str:
    """Classify the discrepancy between observed and model flight categories.

    Returns one of: CONFIRMING, MINOR_DELTA, SIGNIFICANT, CONFLICTING.
    """
    if obs_cat is None or model_cat is None:
        return "CONFIRMING"  # can't compare
    obs_idx = _CATEGORY_ORDER.get(obs_cat.upper(), -1)
    model_idx = _CATEGORY_ORDER.get(model_cat.upper(), -1)
    if obs_idx < 0 or model_idx < 0:
        return "CONFIRMING"
    diff = abs(obs_idx - model_idx)
    if diff == 0:
        return "CONFIRMING"
    if diff == 1:
        return "SIGNIFICANT"
    return "CONFLICTING"


def run_observation_comparison(
    observations: RouteObservations,
    snapshot_forecasts: list[WaypointForecast],
    target_time: datetime,
    route: RouteConfig,
) -> RouteObservations:
    """Compare METAR observations against model predictions at nearest waypoints.

    Mutates `observations` in-place (adds comparisons and updates flags).

    Args:
        observations: RouteObservations from run_route_weather().
        snapshot_forecasts: All WaypointForecast from the fetch stage.
        target_time: Flight target time.
        route: Flight route configuration.

    Returns:
        The same RouteObservations, enriched with comparisons.
    """
    from weatherbrief.analysis.airport_conditions import classify_flight_category

    _M_PER_SM = 1609.34

    comparisons: list[ObservationComparison] = []
    has_conflicts = False

    for obs in observations.airports:
        if not obs.has_metar or obs.metar_flight_category is None:
            continue

        # Find model forecast for the nearest waypoint
        wp_icao = obs.nearest_waypoint_icao
        wp_forecasts = [
            f for f in snapshot_forecasts if f.waypoint.icao == wp_icao
        ]
        if not wp_forecasts:
            continue

        # Use first model's forecast as reference (typically best_match or primary)
        wf = wp_forecasts[0]
        hourly = wf.at_time(target_time)
        if hourly is None:
            continue

        # Derive model flight category from visibility (ceiling not available from HourlyForecast)
        vis_sm = round(hourly.visibility_m / _M_PER_SM, 1) if hourly.visibility_m is not None else None
        model_fc = classify_flight_category(ceiling_ft=None, visibility_sm=vis_sm)

        obs_cat = obs.metar_flight_category.upper()
        m_cat = model_fc.value.upper()

        match = _classify_discrepancy(obs_cat, m_cat)

        vis_delta = None
        if obs.metar_visibility_m is not None and hourly.visibility_m is not None:
            vis_delta = float(obs.metar_visibility_m) - hourly.visibility_m

        wind_delta = None
        if obs.metar_wind_speed_kt is not None and hourly.wind_speed_10m_kt is not None:
            wind_delta = float(obs.metar_wind_speed_kt) - hourly.wind_speed_10m_kt

        detail_parts = []
        if obs_cat and m_cat and obs_cat != m_cat:
            detail_parts.append(f"METAR {obs_cat} vs model {m_cat}")
        if vis_delta is not None and abs(vis_delta) > 2000:
            detail_parts.append(f"vis delta {vis_delta:+.0f}m")
        if wind_delta is not None and abs(wind_delta) > 5:
            detail_parts.append(f"wind delta {wind_delta:+.0f}kt")

        comp = ObservationComparison(
            icao=obs.icao,
            obs_category=obs_cat,
            model_category=m_cat,
            category_match=match,
            visibility_delta_m=vis_delta,
            wind_speed_delta_kt=wind_delta,
            detail="; ".join(detail_parts),
        )
        comparisons.append(comp)

        if match == "CONFLICTING":
            has_conflicts = True

    observations.comparisons = comparisons
    observations.has_conflicts = has_conflicts
    return observations
