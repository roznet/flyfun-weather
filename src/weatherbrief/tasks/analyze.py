"""Analysis task — waypoint and route-point analysis.

Moved from ``pipeline.py`` for independent execution.  The public functions
``analyze_waypoint``, ``analyze_all_route_points``, ``compute_route_tracks``,
and ``compute_interpolated_time`` are re-exported from ``pipeline.py`` for
backward compatibility.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from weatherbrief.analysis.comparison import compare_models
from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories
from weatherbrief.analysis.wind import compute_wind_components, pick_wind_at_pressure
from weatherbrief.models import (
    AltitudeAdvisories,
    HourlyForecast,
    ModelDivergence,
    RouteAnalysesManifest,
    RouteCrossSection,
    RouteConfig,
    RoutePoint,
    RoutePointAnalysis,
    RoutePointWindOverlay,
    RouteWindOverlay,
    SoundingAnalysis,
    WaypointAnalysis,
    WaypointForecast,
    WindComponent,
    altitude_to_pressure_hpa,
    bearing_between_coords,
    temperature_at_pressure,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """Output of the analysis stage."""

    waypoint_analyses: list[WaypointAnalysis]
    route_analyses: list[RoutePointAnalysis]
    route_analyses_manifest: RouteAnalysesManifest | None
    model_names: list[str]


# ---------------------------------------------------------------------------
# Core analysis helpers (moved from pipeline.py)
# ---------------------------------------------------------------------------

def _run_point_analysis(
    forecasts_by_model: dict[str, HourlyForecast],
    track_deg: float,
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
    icing_severity_enhance: bool = False,
) -> tuple[
    dict[str, WindComponent],
    dict[str, SoundingAnalysis],
    Optional[AltitudeAdvisories],
    list[ModelDivergence],
    dict[str, float],
]:
    """Core analysis logic shared between waypoint and route-point paths.

    Args:
        forecasts_by_model: model_key -> HourlyForecast at the target time.
        track_deg: Route bearing at this point.
        cruise_altitude_ft: Cruise altitude for wind and advisory computation.
        flight_ceiling_ft: Flight ceiling for advisory computation.

    Returns:
        (wind_components, soundings, altitude_advisories, model_divergence,
         cruise_temperature_c) — the last maps model_key -> temperature (°C)
        at the cruise level, used downstream for ISA deviation.
    """
    wind_components: dict[str, WindComponent] = {}
    soundings: dict[str, SoundingAnalysis] = {}
    cruise_temps: dict[str, float] = {}

    # Comparison accumulators
    comp: dict[str, dict[str, float]] = {
        "temperature_c": {}, "wind_speed_kt": {}, "wind_direction_deg": {},
        "cloud_cover_pct": {}, "precipitation_mm": {}, "freezing_level_m": {},
        "freezing_level_ft": {}, "cape_surface_jkg": {}, "nwp_cape_jkg": {},
        "lcl_altitude_ft": {},
        "k_index": {}, "total_totals": {}, "precipitable_water_mm": {},
        "lifted_index": {}, "bulk_shear_0_6km_kt": {}, "max_omega_pa_s": {},
        "snowfall_cm": {}, "rain_mm": {}, "pressure_msl_hpa": {},
    }

    target_pressure = altitude_to_pressure_hpa(cruise_altitude_ft)
    for model_key, hourly in forecasts_by_model.items():
        cruise_wind = pick_wind_at_pressure(hourly, target_pressure)

        cruise_temp = temperature_at_pressure(hourly, target_pressure)
        if cruise_temp is not None:
            cruise_temps[model_key] = round(cruise_temp, 1)

        if cruise_wind and cruise_wind.wind_speed_kt is not None:
            wc = compute_wind_components(
                cruise_wind.wind_speed_kt, cruise_wind.wind_direction_deg, track_deg
            )
            wind_components[model_key] = wc
            comp["wind_speed_kt"][model_key] = cruise_wind.wind_speed_kt
            comp["wind_direction_deg"][model_key] = cruise_wind.wind_direction_deg

        # Sounding analysis
        sounding = analyze_sounding(hourly.pressure_levels, hourly, icing_severity_enhance=icing_severity_enhance, model_key=model_key)
        if sounding is not None:
            soundings[model_key] = sounding

            idx = sounding.indices
            if idx is not None:
                _collect_opt(comp, "freezing_level_ft", model_key, idx.freezing_level_ft)
                _collect_opt(comp, "cape_surface_jkg", model_key, idx.cape_surface_jkg)
                _collect_opt(comp, "lcl_altitude_ft", model_key, idx.lcl_altitude_ft)
                _collect_opt(comp, "k_index", model_key, idx.k_index)
                _collect_opt(comp, "total_totals", model_key, idx.total_totals)
                _collect_opt(comp, "precipitable_water_mm", model_key, idx.precipitable_water_mm)
                _collect_opt(comp, "lifted_index", model_key, idx.lifted_index)
                _collect_opt(comp, "bulk_shear_0_6km_kt", model_key, idx.bulk_shear_0_6km_kt)
                _collect_opt(comp, "nwp_cape_jkg", model_key, idx.nwp_cape_jkg)

            vm = sounding.vertical_motion
            if vm is not None and vm.max_omega_pa_s is not None:
                comp["max_omega_pa_s"][model_key] = abs(vm.max_omega_pa_s)
        else:
            logger.warning(
                "Sounding analysis returned None for %s (%d pressure levels provided)",
                model_key, len(hourly.pressure_levels),
            )

        # Surface comparison values
        _collect_opt(comp, "temperature_c", model_key, hourly.temperature_2m_c)
        _collect_opt(comp, "cloud_cover_pct", model_key, hourly.cloud_cover_pct)
        _collect_opt(comp, "precipitation_mm", model_key, hourly.precipitation_mm)
        _collect_opt(comp, "snowfall_cm", model_key, hourly.snowfall_cm)
        _collect_opt(comp, "rain_mm", model_key, hourly.rain_mm)
        _collect_opt(comp, "freezing_level_m", model_key, hourly.freezing_level_m)
        _collect_opt(comp, "pressure_msl_hpa", model_key, hourly.pressure_msl_hpa)

    # Altitude advisories
    altitude_advisories = None
    if soundings:
        altitude_advisories = compute_altitude_advisories(
            soundings, cruise_altitude_ft, flight_ceiling_ft
        )

    # Model comparison
    divergences: list[ModelDivergence] = []
    for var_name, values in comp.items():
        if len(values) >= 2:
            divergences.append(compare_models(var_name, values))

    return wind_components, soundings, altitude_advisories, divergences, cruise_temps


def _collect_opt(
    comp: dict[str, dict[str, float]], key: str, model_key: str, value: float | None,
) -> None:
    """Add a non-None value to comparison accumulators."""
    if value is not None:
        comp[key][model_key] = value


def analyze_waypoint(
    forecasts: list[WaypointForecast],
    target_time: datetime,
    track_deg: float,
    cruise_altitude_ft: int = 8000,
    flight_ceiling_ft: int = 18000,
    icing_severity_enhance: bool = False,
) -> WaypointAnalysis:
    """Run all analysis on forecasts for a single waypoint."""
    if not forecasts:
        raise ValueError("No forecasts to analyze")

    waypoint = forecasts[0].waypoint
    forecasts_by_model: dict[str, HourlyForecast] = {}
    for wf in forecasts:
        hourly = wf.at_time(target_time)
        if hourly:
            forecasts_by_model[wf.model.value] = hourly

    wind_components, soundings, alt_advisories, divergences, cruise_temps = _run_point_analysis(
        forecasts_by_model, track_deg, cruise_altitude_ft, flight_ceiling_ft,
        icing_severity_enhance=icing_severity_enhance,
    )

    return WaypointAnalysis(
        waypoint=waypoint,
        target_time=target_time,
        wind_components=wind_components,
        sounding=soundings,
        altitude_advisories=alt_advisories,
        model_divergence=divergences,
        cruise_temperature_c=cruise_temps,
    )


# --- Route-point analysis helpers ---


def compute_wind_overlay_at_altitude(
    analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    advisory_models: list[str],
    cruise_altitude_ft: int,
) -> RouteWindOverlay:
    """Recompute per-route-point head/cross-wind at *cruise_altitude_ft*.

    Pairs each route-point analysis with the matching cross-section
    forecast (per model) and picks the wind closest to the target
    pressure level. Used by the advisory recalculate endpoint so that
    a user changing the override altitude sees consistent headwind
    values without re-running the full analysis pipeline.

    Cross-sections are paired with analyses by list-position — both are
    generated in the same route-point order during the fetch stage.
    """
    target_pressure = altitude_to_pressure_hpa(cruise_altitude_ft)
    cs_by_model: dict[str, RouteCrossSection] = {
        cs.model.value: cs for cs in cross_sections
    }

    points: list[RoutePointWindOverlay] = []
    for rpa in analyses:
        per_model: dict[str, WindComponent] = {}
        for model_key in advisory_models:
            cs = cs_by_model.get(model_key)
            if cs is None or rpa.point_index >= len(cs.point_forecasts):
                continue
            wf = cs.point_forecasts[rpa.point_index]
            hourly = wf.at_time(rpa.forecast_hour)
            if hourly is None:
                continue
            level = pick_wind_at_pressure(hourly, target_pressure)
            if level is None or level.wind_speed_kt is None or level.wind_direction_deg is None:
                continue
            per_model[model_key] = compute_wind_components(
                level.wind_speed_kt, level.wind_direction_deg, rpa.track_deg,
            )
        points.append(RoutePointWindOverlay(
            point_index=rpa.point_index,
            wind_components=per_model,
        ))

    return RouteWindOverlay(
        cruise_altitude_ft=cruise_altitude_ft,
        points=points,
    )


def compute_route_tracks(route_points: list[RoutePoint]) -> list[float]:
    """Compute bearing at each route point using neighbor points."""
    n = len(route_points)
    tracks: list[float] = []
    for i in range(n):
        if n == 1:
            tracks.append(0.0)
        elif i == 0:
            tracks.append(bearing_between_coords(
                route_points[0].lat, route_points[0].lon,
                route_points[1].lat, route_points[1].lon,
            ))
        elif i == n - 1:
            tracks.append(bearing_between_coords(
                route_points[-2].lat, route_points[-2].lon,
                route_points[-1].lat, route_points[-1].lon,
            ))
        else:
            # Circular mean of incoming and outgoing bearings
            b1 = bearing_between_coords(
                route_points[i - 1].lat, route_points[i - 1].lon,
                route_points[i].lat, route_points[i].lon,
            )
            b2 = bearing_between_coords(
                route_points[i].lat, route_points[i].lon,
                route_points[i + 1].lat, route_points[i + 1].lon,
            )
            x = math.cos(math.radians(b1)) + math.cos(math.radians(b2))
            y = math.sin(math.radians(b1)) + math.sin(math.radians(b2))
            tracks.append(math.degrees(math.atan2(y, x)) % 360)
    return tracks


def compute_interpolated_time(
    departure: datetime, duration_hours: float,
    distance_nm: float, total_distance_nm: float,
) -> datetime:
    """Compute the flight time at a given distance along the route."""
    if total_distance_nm <= 0 or duration_hours <= 0:
        return departure
    fraction = distance_nm / total_distance_nm
    return departure + timedelta(hours=fraction * duration_hours)


def analyze_all_route_points(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    departure_time: datetime,
    duration_hours: float,
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
    icing_severity_enhance: bool = False,
) -> list[RoutePointAnalysis]:
    """Analyze all route points across all models.

    For each route point, gathers the forecast from each model's cross-section
    at the point's interpolated time, then runs the shared analysis.
    """
    if not cross_sections or not route_points:
        return []

    total_distance = route_points[-1].distance_from_origin_nm
    tracks = compute_route_tracks(route_points)

    # Fill GRIB-enriched field gaps by spatial interpolation before sounding analysis
    from weatherbrief.analysis.spatial_interpolation import interpolate_all_spatially
    interpolate_all_spatially(cross_sections, route_points)

    analyses: list[RoutePointAnalysis] = []

    for i, rp in enumerate(route_points):
        interp_time = compute_interpolated_time(
            departure_time, duration_hours, rp.distance_from_origin_nm, total_distance,
        )

        # Gather closest forecast hour from each model
        forecasts_by_model: dict[str, HourlyForecast] = {}
        forecast_hour = interp_time  # will be updated per-model
        for cs in cross_sections:
            wf = cs.point_forecasts[i]
            hourly = wf.at_time(interp_time)
            if hourly:
                forecasts_by_model[cs.model.value] = hourly
                forecast_hour = hourly.time  # last model's actual hour (they should agree)

        if not forecasts_by_model:
            continue

        wind_components, soundings, alt_advisories, divergences, cruise_temps = _run_point_analysis(
            forecasts_by_model, tracks[i], cruise_altitude_ft, flight_ceiling_ft,
            icing_severity_enhance=icing_severity_enhance,
        )

        analyses.append(RoutePointAnalysis(
            point_index=i,
            lat=rp.lat,
            lon=rp.lon,
            distance_from_origin_nm=rp.distance_from_origin_nm,
            waypoint_icao=rp.waypoint_icao,
            waypoint_name=rp.waypoint_name,
            interpolated_time=interp_time,
            forecast_hour=forecast_hour,
            track_deg=tracks[i],
            wind_components=wind_components,
            sounding=soundings,
            altitude_advisories=alt_advisories,
            model_divergence=divergences,
            cruise_temperature_c=cruise_temps,
        ))

    # Post-processing: compute E-Shear turbulence using horizontal wind shear
    # between adjacent route points (requires all points to be analyzed first)
    _enrich_e_shear(analyses)

    return analyses


def _enrich_e_shear(analyses: list[RoutePointAnalysis]) -> None:
    """Add E-Shear turbulence layers to each route point's soundings.

    Computes horizontal wind shear between adjacent points, then combines
    with vertical wind shear to produce the E parameter per model.
    """
    from weatherbrief.analysis.sounding.e_shear import (
        compute_e_shear_per_sounding,
        compute_hws_between_points,
    )

    if len(analyses) < 2:
        return

    for model in analyses[0].sounding:
        for i, rpa in enumerate(analyses):
            sounding = rpa.sounding.get(model)
            if sounding is None or sounding.vertical_motion is None:
                continue

            levels = sounding.derived_levels

            # Compute HWS from adjacent points
            hws_at_level: dict[int, float] = {}
            prev_sounding = analyses[i - 1].sounding.get(model) if i > 0 else None
            next_sounding = analyses[i + 1].sounding.get(model) if i < len(analyses) - 1 else None

            if prev_sounding and prev_sounding.derived_levels:
                dist = rpa.distance_from_origin_nm - analyses[i - 1].distance_from_origin_nm
                hws_prev = compute_hws_between_points(
                    prev_sounding.derived_levels, levels, dist,
                )
                for p, v in hws_prev.items():
                    hws_at_level[p] = v

            if next_sounding and next_sounding.derived_levels:
                dist = analyses[i + 1].distance_from_origin_nm - rpa.distance_from_origin_nm
                hws_next = compute_hws_between_points(
                    levels, next_sounding.derived_levels, dist,
                )
                # Average with prev if both exist
                for p, v in hws_next.items():
                    if p in hws_at_level:
                        hws_at_level[p] = (hws_at_level[p] + v) / 2
                    else:
                        hws_at_level[p] = v

            e_shear_layers = compute_e_shear_per_sounding(levels, hws_at_level or None)
            sounding.vertical_motion.e_shear_layers = e_shear_layers


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_analysis(
    route: RouteConfig,
    departure_time: datetime,
    all_forecasts: list[WaypointForecast],
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    icing_severity_enhance: bool = False,
    pack_dir: Path | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
) -> AnalysisResult:
    """Run the analysis stage: waypoint + route-point analyses.

    If *pack_dir* is set, persists analysis artifacts to disk.
    """
    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    target_dt = departure_time

    # --- Route-point analyses (includes waypoints) ---
    rp_analyses: list[RoutePointAnalysis] = []
    model_names: list[str] = []
    route_analyses_manifest: RouteAnalysesManifest | None = None

    if cross_sections:
        _notify("route_analysis")
        try:
            model_names = [cs.model.value for cs in cross_sections]
            total_distance = route_points[-1].distance_from_origin_nm
            rp_analyses = analyze_all_route_points(
                cross_sections, route_points, target_dt,
                route.flight_duration_hours, route.cruise_altitude_ft,
                route.flight_ceiling_ft,
                icing_severity_enhance=icing_severity_enhance,
            )
            route_analyses_manifest = RouteAnalysesManifest(
                route_name=route.name,
                target_date=departure_time.strftime("%Y-%m-%d"),
                departure_time=departure_time,
                flight_duration_hours=route.flight_duration_hours,
                total_distance_nm=total_distance,
                cruise_altitude_ft=route.cruise_altitude_ft,
                models=model_names,
                analyses=rp_analyses,
            )
            # Solar analysis (issue #227): night/twilight shading + sun-side note.
            # Glare (takeoff/landing) needs airport wind, only available in the
            # advise stage, so it is filled there onto RouteContext.sun. The
            # cross-section shading reads night_intervals from this manifest, so
            # compute the airport-independent parts here where the manifest lives.
            try:
                from weatherbrief.analysis.sun import compute_route_sun

                route_analyses_manifest.sun = compute_route_sun(rp_analyses)
            except Exception:
                logger.warning("Route sun analysis failed", exc_info=True)
            logger.info("Route analyses: %d points", len(rp_analyses))
        except Exception:
            logger.warning("Route-point analysis failed", exc_info=True)

    # --- Waypoint analyses: extract from route-point results to avoid
    # running _run_point_analysis twice for the same data ---
    _notify("waypoint_analysis")
    waypoint_analyses: list[WaypointAnalysis] = []
    if rp_analyses:
        # Build lookup of route-point analyses by waypoint ICAO
        rpa_by_icao: dict[str, RoutePointAnalysis] = {}
        for rpa in rp_analyses:
            if rpa.waypoint_icao and rpa.waypoint_icao not in rpa_by_icao:
                rpa_by_icao[rpa.waypoint_icao] = rpa

        for waypoint in route.waypoints:
            rpa = rpa_by_icao.get(waypoint.icao)
            if rpa is not None:
                waypoint_analyses.append(WaypointAnalysis(
                    waypoint=waypoint,
                    target_time=rpa.interpolated_time,
                    wind_components=rpa.wind_components,
                    sounding=rpa.sounding,
                    altitude_advisories=rpa.altitude_advisories,
                    model_divergence=rpa.model_divergence,
                ))
            else:
                # Fallback: compute from forecasts (waypoint not in route points).
                # Only attempt for waypoints that have forecasts (airports).
                # Navaids/fixes won't have forecasts — skip them silently.
                wp_forecasts = [
                    f for f in all_forecasts if f.waypoint.icao == waypoint.icao
                ]
                if not wp_forecasts:
                    continue
                track_deg = route.waypoint_track(waypoint.icao)
                analysis = analyze_waypoint(
                    wp_forecasts, target_dt, track_deg,
                    cruise_altitude_ft=route.cruise_altitude_ft,
                    flight_ceiling_ft=route.flight_ceiling_ft,
                    icing_severity_enhance=icing_severity_enhance,
                )
                waypoint_analyses.append(analysis)
    else:
        # No route-point analyses — compute waypoint analyses directly
        for waypoint in route.waypoints:
            wp_forecasts = [
                f for f in all_forecasts if f.waypoint.icao == waypoint.icao
            ]
            if not wp_forecasts:
                continue  # Skip navaids/fixes without forecasts
            track_deg = route.waypoint_track(waypoint.icao)
            analysis = analyze_waypoint(
                wp_forecasts, target_dt, track_deg,
                cruise_altitude_ft=route.cruise_altitude_ft,
                flight_ceiling_ft=route.flight_ceiling_ft,
                icing_severity_enhance=icing_severity_enhance,
            )
            waypoint_analyses.append(analysis)

    return AnalysisResult(
        waypoint_analyses=waypoint_analyses,
        route_analyses=rp_analyses,
        route_analyses_manifest=route_analyses_manifest,
        model_names=model_names,
    )


def run_analysis_from_pack(
    pack_dir: Path,
    route: RouteConfig,
    departure_time: datetime,
    icing_severity_enhance: bool = False,
) -> AnalysisResult:
    """Re-run analysis from persisted pack_dir artifacts.

    Loads cross_sections and route_points from disk, then delegates
    to :func:`run_analysis`.  Requires ``cross_section.json`` and
    ``route_points.json`` in *pack_dir*.
    """
    from weatherbrief.tasks.artifacts import load_cross_sections, load_route_points

    cross_sections = load_cross_sections(pack_dir)
    route_points = load_route_points(pack_dir)
    if route_points is None:
        raise FileNotFoundError(f"route_points.json not found in {pack_dir}")

    # Reconstruct waypoint forecasts from cross-sections
    all_forecasts: list[WaypointForecast] = []
    for cs in cross_sections:
        for rp, fc in zip(route_points, cs.point_forecasts):
            if rp.waypoint_icao:
                all_forecasts.append(fc)

    return run_analysis(
        route=route,
        departure_time=departure_time,
        all_forecasts=all_forecasts,
        cross_sections=cross_sections,
        route_points=route_points,
        icing_severity_enhance=icing_severity_enhance,
        pack_dir=pack_dir,
    )
