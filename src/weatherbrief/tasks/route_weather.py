"""Route METAR/TAF fetch and observation-vs-model comparison.

Only used on D-0 (day of flight) when real observations add value.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.models.analysis import RouteConfig, WaypointForecast
from weatherbrief.models.airport_conditions import RunwayEnd
from weatherbrief.models.observations import (
    AirportObservation,
    ObservationComparison,
    RealtimeRefreshResult,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
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


# Route-distance math now lives in analysis.route_geometry so the alternates
# stage doesn't reach into this module's privates. Kept as a module-level alias
# for the existing internal callers (and tests that import it from here).
from weatherbrief.analysis.route_geometry import (  # noqa: E402
    compute_route_distances as _compute_route_distances,
)


def _interpolate_airport_time(
    departure: datetime,
    duration_hours: float,
    enroute_distance_nm: float | None,
    total_distance_nm: float,
) -> datetime:
    """Compute estimated time at an airport based on its enroute distance."""
    if (
        total_distance_nm <= 0
        or duration_hours <= 0
        or enroute_distance_nm is None
    ):
        return departure
    fraction = min(enroute_distance_nm / total_distance_nm, 1.0)
    return departure + timedelta(hours=fraction * duration_hours)


# Default wind advisory thresholds (matching airport_wind evaluator defaults)
_XWIND_GREEN = 15
_XWIND_RED = 25
_GUST_GREEN = 25
_GUST_RED = 35


def _wind_advisory_status(
    crosswind_kt: float | None,
    gust_kt: float | None,
) -> str:
    """Determine wind advisory status: 'green', 'amber', or 'red'."""
    status = "green"
    if crosswind_kt is not None:
        if crosswind_kt >= _XWIND_RED:
            return "red"
        if crosswind_kt >= _XWIND_GREEN:
            status = "amber"
    if gust_kt is not None:
        if gust_kt >= _GUST_RED:
            return "red"
        if gust_kt >= _GUST_GREEN:
            status = "amber"
    return status


def compute_wind_advisory(
    wind_dir: int | float | None,
    wind_speed_kt: int | float | None,
    wind_gust_kt: int | float | None,
    runway_ends: list[RunwayEnd],
) -> tuple[str | None, str | None, float | None, float | None]:
    """Compute wind advisory from wind and runway data.

    Returns:
        (advisory_status, best_runway_id, crosswind_kt, headwind_kt)
        or (None, None, None, None) if data is insufficient.
    """
    if wind_dir is None or wind_speed_kt is None or not runway_ends:
        return None, None, None, None

    from weatherbrief.analysis.airport_conditions import compute_runway_winds

    all_runways = compute_runway_winds(runway_ends, float(wind_speed_kt), float(wind_dir))
    if not all_runways:
        return None, None, None, None

    best = min(all_runways, key=lambda r: (r.crosswind_kt, -r.headwind_kt))
    advisory = _wind_advisory_status(best.crosswind_kt, wind_gust_kt)
    return advisory, best.runway_id, best.crosswind_kt, best.headwind_kt


def _applicable_taf_lines(
    taf,  # WeatherReport
    target_time: datetime,
) -> list[int]:
    """Return 0-based line indices within taf.raw_text that are applicable at target_time.

    Always includes line 0 (base conditions). For each applicable trend,
    finds matching line(s) by validity period string. PROB lines preceding
    a matched TEMPO line are also included.
    """
    from euro_aip.briefing.weather.analysis import WeatherAnalyzer

    raw = taf.raw_text
    if not raw:
        return []

    lines = raw.splitlines()
    if not lines:
        return []

    result = {0}  # base conditions always applicable

    applicable = WeatherAnalyzer.applicable_trends(taf, target_time)

    for trend in applicable:
        vs = trend.validity_start
        ve = trend.validity_end
        if vs is None:
            continue
        # Build validity token like "2409/2411"
        if ve is not None:
            token = f"{vs.day:02d}{vs.hour:02d}/{ve.day:02d}{ve.hour:02d}"
        else:
            # FM group: "FM2409" style
            token = f"FM{vs.day:02d}{vs.hour:02d}"

        for i, line in enumerate(lines):
            stripped = line.strip()
            if token in stripped:
                result.add(i)
                # Include preceding PROB line if present
                if i > 0 and lines[i - 1].strip().startswith("PROB"):
                    result.add(i - 1)

    return sorted(result)


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

    from weatherbrief.airports import _load_airport_model

    model = _load_airport_model(airports_db_path)

    route_icaos = [wp.icao for wp in route.waypoints]
    route_distances = _compute_route_distances(route)
    total_distance = route_distances[-1] if route_distances else 0.0
    duration_hours = route.flight_duration_hours

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

        # Compute per-airport ETA based on enroute distance
        airport_time = _interpolate_airport_time(
            target_time, duration_hours,
            raw.enroute_distance_nm, total_distance,
        )
        if duration_hours > 0 and raw.enroute_distance_nm is not None:
            offset_hours = (airport_time - target_time).total_seconds() / 3600
            eta_hour_offset = round(offset_hours)
        else:
            eta_hour_offset = None

        obs = AirportObservation(
            icao=raw.icao,
            name=raw.name,
            distance_from_route_nm=raw.distance_from_route_nm,
            enroute_distance_nm=raw.enroute_distance_nm,
            nearest_waypoint_icao=nearest_wp,
            eta_hour_offset=eta_hour_offset,
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
            applicable = WeatherAnalyzer.find_applicable_taf(taf, airport_time)
            if applicable is not None:
                if applicable.flight_category is not None:
                    obs.taf_flight_category_at_eta = applicable.flight_category.value
                    obs.taf_trend_type = applicable.trend_type
                    taf_categories.append(applicable.flight_category.value)
                obs.taf_wind_dir = applicable.wind_direction
                obs.taf_wind_speed_kt = applicable.wind_speed
                obs.taf_wind_gust_kt = applicable.wind_gust
                obs.taf_applicable_text = applicable.raw_text

            # Build list of applicable TAF line indices for highlighting
            obs.taf_applicable_lines = _applicable_taf_lines(
                taf, airport_time,
            )

        airports.append(obs)

    # De-duplicate phenomena
    unique_phenomena = sorted(set(all_phenomena))

    # Compute runway-relative wind advisories for METAR and TAF
    try:
        from weatherbrief.airports import get_runway_ends as _get_runway_ends

        obs_icaos = list({a.icao for a in airports})
        runway_data = _get_runway_ends(obs_icaos, airports_db_path)

        for obs in airports:
            rwy_ends = runway_data.get(obs.icao, [])
            if not rwy_ends:
                continue

            # METAR wind advisory
            adv, rwy_id, xw, hw = compute_wind_advisory(
                obs.metar_wind_dir, obs.metar_wind_speed_kt,
                obs.metar_wind_gust_kt, rwy_ends,
            )
            if adv is not None:
                obs.metar_wind_advisory = adv
                obs.metar_best_runway_id = rwy_id
                obs.metar_crosswind_kt = xw
                obs.metar_headwind_kt = hw

            # TAF wind advisory
            adv, rwy_id, xw, hw = compute_wind_advisory(
                obs.taf_wind_dir, obs.taf_wind_speed_kt,
                obs.taf_wind_gust_kt, rwy_ends,
            )
            if adv is not None:
                obs.taf_wind_advisory = adv
                obs.taf_best_runway_id = rwy_id
                obs.taf_crosswind_kt = xw
                obs.taf_headwind_kt = hw
    except Exception:
        logger.warning("Failed to compute observation wind advisories", exc_info=True)

    return RouteObservations(
        corridor_nm=corridor_nm,
        fetch_time=datetime.now(timezone.utc),
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

    Returns one of: CONFIRMING, SIGNIFICANT, CONFLICTING.
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
    runway_data: dict[str, list[RunwayEnd]] | None = None,
    route_analyses: list | None = None,
) -> RouteObservations:
    """Compare METAR observations against model predictions at nearest waypoints.

    Mutates `observations` in-place (adds comparisons and updates flags).

    Args:
        observations: RouteObservations from run_route_weather().
        snapshot_forecasts: All WaypointForecast from the fetch stage.
        target_time: Flight target time.
        route: Flight route configuration.
        runway_data: Optional runway data for wind advisory comparison.
        route_analyses: Optional RoutePointAnalysis list (provides sounding ceiling).

    Returns:
        The same RouteObservations, enriched with comparisons.
    """
    from weatherbrief.analysis.airport_conditions import (
        reconcile_ceiling,
        classify_flight_category,
    )

    _M_PER_SM = 1609.34

    # Build lookup: waypoint_icao -> RoutePointAnalysis (for sounding ceiling)
    rpa_by_icao: dict = {}
    if route_analyses:
        for rpa in route_analyses:
            icao = rpa.waypoint_icao if hasattr(rpa, "waypoint_icao") else rpa.get("waypoint_icao")
            if icao:
                rpa_by_icao[icao] = rpa

    route_distances = _compute_route_distances(route)
    total_distance = route_distances[-1] if route_distances else 0.0
    duration_hours = route.flight_duration_hours

    comparisons: list[ObservationComparison] = []
    has_conflicts = False

    for obs in observations.airports:
        if not obs.has_metar or obs.metar_flight_category is None:
            continue

        # Compute per-airport interpolated time
        airport_time = _interpolate_airport_time(
            target_time, duration_hours,
            obs.enroute_distance_nm, total_distance,
        )

        # Find model forecast for the nearest waypoint
        wp_icao = obs.nearest_waypoint_icao
        wp_forecasts = [
            f for f in snapshot_forecasts if f.waypoint.icao == wp_icao
        ]
        if not wp_forecasts:
            continue

        # Use first model's forecast as reference (typically best_match or primary)
        wf = wp_forecasts[0]
        hourly = wf.at_time(airport_time)
        if hourly is None:
            continue

        # Derive ceiling from sounding + NWP diagnostics (same as advisory system)
        ceiling_ft = None
        rpa = rpa_by_icao.get(wp_icao)
        if rpa is not None:
            model_name = wf.model.value if hasattr(wf.model, "value") else str(wf.model)
            sounding = rpa.sounding.get(model_name) if hasattr(rpa, "sounding") else None
            ceiling_ft = reconcile_ceiling(sounding, hourly)

        vis_sm = round(hourly.visibility_m / _M_PER_SM, 1) if hourly.visibility_m is not None else None
        model_fc = classify_flight_category(ceiling_ft=ceiling_ft, visibility_sm=vis_sm)

        obs_cat = obs.metar_flight_category.upper()
        m_cat = model_fc.value.upper()

        match = _classify_discrepancy(obs_cat, m_cat)

        vis_delta = None
        if obs.metar_visibility_m is not None and hourly.visibility_m is not None:
            vis_delta = float(obs.metar_visibility_m) - hourly.visibility_m

        wind_delta = None
        if obs.metar_wind_speed_kt is not None and hourly.wind_speed_10m_kt is not None:
            wind_delta = float(obs.metar_wind_speed_kt) - hourly.wind_speed_10m_kt

        # Model wind advisory
        model_wind_dir = hourly.wind_direction_10m_deg
        model_wind_speed = hourly.wind_speed_10m_kt
        model_wind_gust = getattr(hourly, "wind_gusts_10m_kt", None)
        model_adv = None
        model_rwy_id = None
        model_xw = None
        rwy_ends = (runway_data or {}).get(obs.icao, [])
        if rwy_ends:
            model_adv, model_rwy_id, model_xw, _ = compute_wind_advisory(
                model_wind_dir, model_wind_speed, model_wind_gust, rwy_ends,
            )

        # Wind advisory match (METAR vs model)
        wind_adv_match = None
        metar_adv = obs.metar_wind_advisory
        if metar_adv is not None and model_adv is not None:
            if metar_adv == model_adv:
                wind_adv_match = "CONFIRMING"
            else:
                _ADV_ORDER = {"green": 0, "amber": 1, "red": 2}
                diff = abs(_ADV_ORDER.get(metar_adv, 0) - _ADV_ORDER.get(model_adv, 0))
                wind_adv_match = "SIGNIFICANT" if diff == 1 else "CONFLICTING"

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
            model_wind_dir=model_wind_dir,
            model_wind_speed_kt=model_wind_speed,
            model_wind_gust_kt=model_wind_gust,
            model_wind_advisory=model_adv,
            model_best_runway_id=model_rwy_id,
            model_crosswind_kt=model_xw,
            wind_advisory_match=wind_adv_match,
            detail="; ".join(detail_parts),
        )
        comparisons.append(comp)

        if match == "CONFLICTING":
            has_conflicts = True

    observations.comparisons = comparisons
    observations.has_conflicts = has_conflicts
    return observations


# --- Route SIGMETs (D-0 real-time area hazards) ---

# SIGMETs are filtered to a band from the surface up to cruise plus this
# buffer (ft), so hazards a GA flight could climb/descend through are kept
# while high-FL-only hazards irrelevant to the route are dropped.
_SIGMET_ALT_BUFFER_FT = 5000


def _sigmet_altitude_band(route: RouteConfig) -> tuple[int, int]:
    """Altitude band (low_ft, high_ft) for SIGMET relevance.

    Surface to cruise plus a climb/descent buffer.
    """
    return (0, int(route.cruise_altitude_ft) + _SIGMET_ALT_BUFFER_FT)


def _departure_day_window(
    target_time: datetime,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """SIGMET validity window: from *now* through the end of the departure day (UTC).

    Wider than the flight window so SIGMETs issued or expiring around the
    flight are still surfaced. Both bounds are aware UTC.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    dep = target_time.astimezone(timezone.utc) if target_time.tzinfo else target_time.replace(tzinfo=timezone.utc)
    end_of_day = datetime(
        dep.year, dep.month, dep.day, 23, 59, 59, tzinfo=timezone.utc,
    )
    # Guard against an inverted window (e.g. a late-evening fetch for a flight
    # that already departed earlier today): clamp the start to end-of-day.
    if now > end_of_day:
        logger.debug(
            "SIGMET window collapsed: now %s is past departure-day end %s; "
            "expect zero SIGMETs", now.isoformat(), end_of_day.isoformat(),
        )
    start = min(now, end_of_day)
    return (start, end_of_day)


def run_route_sigmets(
    route: RouteConfig,
    target_time: datetime,
    corridor_nm: float,
    airports_db_path: str,
    *,
    now: datetime | None = None,
) -> RouteSigmets:
    """Fetch SIGMETs affecting a route via euro_aip RouteSigmetService.

    Mirrors :func:`run_route_weather` but for area hazards: resolves the route
    geometry, queries live international SIGMETs, and keeps those whose polygon
    intersects the route corridor within the altitude band and departure-day
    time window.

    Args:
        route: Flight route configuration.
        target_time: Flight departure/target time (drives the time window).
        corridor_nm: Corridor half-width for the intersection test.
        airports_db_path: Path to euro_aip SQLite database.
        now: Override for "now" (testing).

    Returns:
        RouteSigmets listing route-relevant SIGMETs, sorted by enroute distance.
    """
    from euro_aip.briefing.weather.route_sigmet import RouteSigmetService

    from weatherbrief.airports import _load_airport_model

    model = _load_airport_model(airports_db_path)

    route_icaos = [wp.icao for wp in route.waypoints]
    low_ft, high_ft = _sigmet_altitude_band(route)
    win_from, win_to = _departure_day_window(target_time, now=now)

    service = RouteSigmetService()
    result = service.fetch_route_sigmets(
        route_icaos=route_icaos,
        corridor_nm=corridor_nm,
        model=model,
        altitude_band_ft=(low_ft, high_ft),
        from_datetime=win_from,
        to_datetime=win_to,
    )

    sigmets: list[SigmetAlongRoute] = []
    for rs in result.sigmets:
        s = rs.sigmet
        sigmets.append(SigmetAlongRoute(
            fir_id=s.fir_id,
            fir_name=s.fir_name,
            hazard=s.hazard,
            qualifier=s.qualifier,
            base_ft=s.base_ft,
            top_ft=s.top_ft,
            valid_from=s.valid_from,
            valid_to=s.valid_to,
            direction=s.direction,
            speed_kt=s.speed_kt,
            raw_text=s.raw_text,
            matched_firs=list(rs.matched_firs),
            min_distance_nm=rs.min_distance_nm,
            enroute_distance_from_nm=rs.enroute_distance_from_nm,
            enroute_distance_to_nm=rs.enroute_distance_to_nm,
            coords=[tuple(c) for c in s.coords],
        ))

    # Order by corridor progression so web + PDF rows scan front-to-back.
    sigmets.sort(key=lambda s: s.enroute_distance_from_nm if s.enroute_distance_from_nm is not None else float("inf"))

    # count / hazards / has_severe are computed from `sigmets` on the model.
    return RouteSigmets(
        corridor_nm=corridor_nm,
        fetch_time=datetime.now(timezone.utc),
        altitude_low_ft=low_ft,
        altitude_high_ft=high_ft,
        time_window_from=win_from,
        time_window_to=win_to,
        route_firs=list(result.route_firs),
        sigmets=sigmets,
    )


def run_realtime_refresh(
    pack_dir: Path,
    db_path: str,
    *,
    default_corridor_nm: float = 30.0,
    default_sigmet_corridor_nm: float = 50.0,
) -> RealtimeRefreshResult:
    """Re-fetch METAR/TAF and recompute the obs-vs-model comparison from a
    pack's *stored* forecasts, then patch ``briefing.json`` in place.

    This is the cheap real-time path: no model fetch, no
    GRIB, no LLM.  Shared by the observations refresh button endpoint and the
    tiered refresh gate's ``realtime`` mode.

    Reads the pack's route, forecasts and route analyses off disk, fetches
    fresh observations *and route SIGMETs* for the route corridor, recomputes
    the comparison against the stored forecasts, writes both back into
    ``briefing.json`` (or legacy ``snapshot.json``), and returns a
    :class:`RealtimeRefreshResult` carrying the updated observations and SIGMETs.

    Raises :class:`FileNotFoundError` if the pack has no briefing data on disk.
    """
    from weatherbrief.tasks.artifacts import (
        load_briefing,
        load_forecasts,
        load_route_analyses,
        parse_target_time,
    )

    pack_dir = Path(pack_dir)
    briefing_data = load_briefing(pack_dir)
    if not briefing_data:
        raise FileNotFoundError(f"No briefing data found in {pack_dir}")

    route = RouteConfig.model_validate(briefing_data["route"])
    target_dt = parse_target_time(briefing_data)

    corridor_nm = default_corridor_nm
    stored_obs = briefing_data.get("route_observations")
    if stored_obs:
        corridor_nm = stored_obs.get("corridor_nm", default_corridor_nm)

    sigmet_corridor_nm = default_sigmet_corridor_nm
    stored_sigmets = briefing_data.get("route_sigmets")
    if stored_sigmets:
        sigmet_corridor_nm = stored_sigmets.get("corridor_nm", default_sigmet_corridor_nm)

    # Fetch fresh METAR/TAF along the route corridor.
    new_obs = run_route_weather(
        route=route,
        target_time=target_dt,
        corridor_nm=corridor_nm,
        airports_db_path=db_path,
    )

    # Compare observations against the pack's *stored* forecasts.
    forecasts_data = load_forecasts(pack_dir)
    forecasts = [
        WaypointForecast.model_validate(f)
        for f in (forecasts_data or {}).get("forecasts", [])
    ]

    route_analyses = None
    if (pack_dir / "route_analyses.json").exists():
        route_analyses = load_route_analyses(pack_dir).analyses

    obs_runway_data = None
    try:
        from weatherbrief.airports import get_runway_ends

        obs_icaos = list({a.icao for a in new_obs.airports})
        obs_runway_data = get_runway_ends(obs_icaos, db_path)
    except Exception:
        logger.warning("Failed to load runway data for observation comparison", exc_info=True)

    new_obs = run_observation_comparison(
        observations=new_obs,
        snapshot_forecasts=forecasts,
        target_time=target_dt,
        route=route,
        runway_data=obs_runway_data,
        route_analyses=route_analyses,
    )

    # Fetch fresh route SIGMETs (area hazards). Non-fatal: a SIGMET source
    # failure must not block the cheap METAR/TAF refresh.
    new_sigmets = None
    try:
        new_sigmets = run_route_sigmets(
            route=route,
            target_time=target_dt,
            corridor_nm=sigmet_corridor_nm,
            airports_db_path=db_path,
        )
    except Exception:
        logger.warning("Route SIGMET refresh failed", exc_info=True)

    # Diff against the previous on-disk state (before we overwrite it) so the
    # UI can warn when conditions worsened — the digest is NOT regenerated here.
    from weatherbrief.tasks.refresh_delta import compute_refresh_delta

    old_obs = RouteObservations.model_validate(stored_obs) if stored_obs else None
    old_sigmets = RouteSigmets.model_validate(stored_sigmets) if stored_sigmets else None
    delta = compute_refresh_delta(
        old_obs=old_obs,
        new_obs=new_obs,
        old_sigmets=old_sigmets,
        new_sigmets=new_sigmets,
    )

    # Patch observations (and SIGMETs, when fetched) back into the briefing.
    briefing_data["route_observations"] = new_obs.model_dump(mode="json")
    if new_sigmets is not None:
        briefing_data["route_sigmets"] = new_sigmets.model_dump(mode="json")
    # Always persist the delta (even when nothing worsened) so a stale banner
    # from a prior refresh is cleared on the next load.
    briefing_data["last_refresh_delta"] = delta.model_dump(mode="json")
    briefing_path = pack_dir / "briefing.json"
    target_path = briefing_path if briefing_path.exists() else pack_dir / "snapshot.json"
    target_path.write_text(json.dumps(briefing_data, indent=2, default=str))

    return RealtimeRefreshResult(observations=new_obs, sigmets=new_sigmets, delta=delta)
