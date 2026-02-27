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
from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.models import (
    AltitudeAdvisories,
    HourlyForecast,
    ModelDivergence,
    RouteAnalysesManifest,
    RouteCrossSection,
    RouteConfig,
    RoutePoint,
    RoutePointAnalysis,
    SoundingAnalysis,
    WaypointAnalysis,
    WaypointForecast,
    WindComponent,
    altitude_to_pressure_hpa,
    bearing_between_coords,
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
    icing_severity_enhance: bool = True,
) -> tuple[
    dict[str, WindComponent],
    dict[str, SoundingAnalysis],
    Optional[AltitudeAdvisories],
    list[ModelDivergence],
]:
    """Core analysis logic shared between waypoint and route-point paths.

    Args:
        forecasts_by_model: model_key -> HourlyForecast at the target time.
        track_deg: Route bearing at this point.
        cruise_altitude_ft: Cruise altitude for wind and advisory computation.
        flight_ceiling_ft: Flight ceiling for advisory computation.

    Returns:
        (wind_components, soundings, altitude_advisories, model_divergence)
    """
    wind_components: dict[str, WindComponent] = {}
    soundings: dict[str, SoundingAnalysis] = {}

    # Comparison accumulators
    comp: dict[str, dict[str, float]] = {
        "temperature_c": {}, "wind_speed_kt": {}, "wind_direction_deg": {},
        "cloud_cover_pct": {}, "precipitation_mm": {}, "freezing_level_m": {},
        "freezing_level_ft": {}, "cape_surface_jkg": {}, "lcl_altitude_ft": {},
        "k_index": {}, "total_totals": {}, "precipitable_water_mm": {},
        "lifted_index": {}, "bulk_shear_0_6km_kt": {}, "max_omega_pa_s": {},
        "snowfall_cm": {}, "rain_mm": {},
    }

    target_pressure = altitude_to_pressure_hpa(cruise_altitude_ft)
    for model_key, hourly in forecasts_by_model.items():
        # Cruise-altitude wind (closest level to target pressure)
        cruise_wind = None
        for level in hourly.pressure_levels:
            if level.wind_speed_kt is not None and level.wind_direction_deg is not None:
                if cruise_wind is None or abs(level.pressure_hpa - target_pressure) < abs(
                    cruise_wind.pressure_hpa - target_pressure
                ):
                    cruise_wind = level

        if cruise_wind and cruise_wind.wind_speed_kt is not None:
            wc = compute_wind_components(
                cruise_wind.wind_speed_kt, cruise_wind.wind_direction_deg, track_deg
            )
            wind_components[model_key] = wc
            comp["wind_speed_kt"][model_key] = cruise_wind.wind_speed_kt
            comp["wind_direction_deg"][model_key] = cruise_wind.wind_direction_deg

        # Sounding analysis
        sounding = analyze_sounding(hourly.pressure_levels, hourly, icing_severity_enhance=icing_severity_enhance)
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

    return wind_components, soundings, altitude_advisories, divergences


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
    icing_severity_enhance: bool = True,
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

    wind_components, soundings, alt_advisories, divergences = _run_point_analysis(
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
    )


# --- Route-point analysis helpers ---


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
    icing_severity_enhance: bool = True,
) -> list[RoutePointAnalysis]:
    """Analyze all route points across all models.

    For each route point, gathers the forecast from each model's cross-section
    at the point's interpolated time, then runs the shared analysis.
    """
    if not cross_sections or not route_points:
        return []

    total_distance = route_points[-1].distance_from_origin_nm
    tracks = compute_route_tracks(route_points)

    # Fill CLW/ICMR gaps by spatial interpolation before sounding analysis
    from weatherbrief.analysis.spatial_interpolation import interpolate_cloud_water_spatially
    interpolate_cloud_water_spatially(cross_sections, route_points)

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

        wind_components, soundings, alt_advisories, divergences = _run_point_analysis(
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
        ))

    return analyses


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_analysis(
    route: RouteConfig,
    target_date: str,
    target_hour: int,
    all_forecasts: list[WaypointForecast],
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    icing_severity_enhance: bool = True,
    pack_dir: Path | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
) -> AnalysisResult:
    """Run the analysis stage: waypoint + route-point analyses.

    If *pack_dir* is set, persists analysis artifacts to disk.
    """
    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    target_dt = datetime(*map(int, target_date.split("-")), target_hour, tzinfo=timezone.utc)

    # --- Waypoint analyses ---
    _notify("waypoint_analysis")
    waypoint_analyses: list[WaypointAnalysis] = []
    for waypoint in route.waypoints:
        wp_forecasts = [
            f for f in all_forecasts if f.waypoint.icao == waypoint.icao
        ]
        track_deg = route.waypoint_track(waypoint.icao)
        analysis = analyze_waypoint(
            wp_forecasts, target_dt, track_deg,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
            icing_severity_enhance=icing_severity_enhance,
        )
        waypoint_analyses.append(analysis)

    # --- Route-point analyses ---
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
                target_date=target_date,
                departure_time=target_dt,
                flight_duration_hours=route.flight_duration_hours,
                total_distance_nm=total_distance,
                cruise_altitude_ft=route.cruise_altitude_ft,
                models=model_names,
                analyses=rp_analyses,
            )
            logger.info("Route analyses: %d points", len(rp_analyses))
        except Exception:
            logger.warning("Route-point analysis failed", exc_info=True)

    return AnalysisResult(
        waypoint_analyses=waypoint_analyses,
        route_analyses=rp_analyses,
        route_analyses_manifest=route_analyses_manifest,
        model_names=model_names,
    )


def run_analysis_from_pack(
    pack_dir: Path,
    route: RouteConfig,
    target_date: str,
    target_hour: int,
    icing_severity_enhance: bool = True,
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
        target_date=target_date,
        target_hour=target_hour,
        all_forecasts=all_forecasts,
        cross_sections=cross_sections,
        route_points=route_points,
        icing_severity_enhance=icing_severity_enhance,
        pack_dir=pack_dir,
    )
