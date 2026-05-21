"""Plain text digest formatter for forecast snapshots."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.digest.format_utils import format_flight_level
from weatherbrief.models import (
    AgreementLevel,
    AltitudeAdvisories,
    ConvectiveRisk,
    ForecastSnapshot,
    IcingRisk,
    PrecipPhase,
    RouteObservations,
    RouteSigmets,
    SoundingAnalysis,
    VerticalMotionClass,
    WaypointAnalysis,
    WaypointForecast,
)

SEPARATOR = "=" * 60


def format_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    output_paths: list[str] | None = None,
) -> str:
    """Format a plain-text weather digest from a forecast snapshot."""
    lines: list[str] = []

    # Header
    waypoints = " -> ".join(w.icao for w in snapshot.route.waypoints)
    lines.append(SEPARATOR)
    lines.append(f"  {waypoints}")
    lines.append(f"  Target: {snapshot.target_date}  FL{snapshot.route.cruise_altitude_ft // 100:03d}")
    lines.append(f"  Digest D-{snapshot.days_out}  Fetched: {snapshot.fetch_date}")
    lines.append(SEPARATOR)
    lines.append("")

    # Per-waypoint forecast summary
    for wp in snapshot.route.waypoints:
        lines.append(f"--- {wp.icao} ({wp.name}) ---")

        wp_forecasts = [f for f in snapshot.forecasts if f.waypoint.icao == wp.icao]
        if not wp_forecasts:
            lines.append("  No forecast data available")
            lines.append("")
            continue

        for wf in wp_forecasts:
            lines.extend(_format_waypoint_forecast(wf, target_time, snapshot.route.cruise_pressure_hpa))

        # Analysis
        wp_analysis = next(
            (a for a in snapshot.analyses if a.waypoint.icao == wp.icao), None
        )
        if wp_analysis:
            lines.extend(_format_waypoint_analysis(wp_analysis))

        lines.append("")

    # METAR/TAF observations (D-0 only)
    if snapshot.route_observations:
        lines.extend(_format_route_observations(snapshot.route_observations))

    # Route SIGMETs (D-0 only)
    if snapshot.route_sigmets and snapshot.route_sigmets.count:
        lines.extend(_format_route_sigmets(snapshot.route_sigmets))

    # Model agreement summary
    lines.extend(_format_model_agreement(snapshot))

    # Output paths footer
    if output_paths:
        lines.append("")
        lines.append("--- Output Files ---")
        for p in output_paths:
            lines.append(f"  {p}")

    lines.append(SEPARATOR)
    return "\n".join(lines)


def _format_waypoint_forecast(
    wf: WaypointForecast, target_time: datetime, cruise_pressure_hpa: int
) -> list[str]:
    """Format forecast data for a waypoint from one model."""
    lines = []
    hourly = wf.at_time(target_time)
    if not hourly:
        lines.append(f"  [{wf.model.value}] No data near target time")
        return lines

    lines.append(f"  [{wf.model.value}] at {hourly.time.strftime('%Y-%m-%d %H:%MZ')}:")

    # Surface
    parts = []
    if hourly.temperature_2m_c is not None:
        parts.append(f"T {hourly.temperature_2m_c:.0f}C")
    if hourly.dewpoint_2m_c is not None:
        parts.append(f"Td {hourly.dewpoint_2m_c:.0f}C")
    if hourly.wind_speed_10m_kt is not None:
        wind_str = f"Wind {hourly.wind_direction_10m_deg:.0f}/{hourly.wind_speed_10m_kt:.0f}kt"
        if hourly.wind_gusts_10m_kt and hourly.wind_gusts_10m_kt > hourly.wind_speed_10m_kt + 5:
            wind_str += f" G{hourly.wind_gusts_10m_kt:.0f}"
        parts.append(wind_str)
    if parts:
        lines.append(f"    Sfc: {', '.join(parts)}")

    parts = []
    if hourly.cloud_cover_pct is not None:
        parts.append(f"Cloud {hourly.cloud_cover_pct:.0f}%")
    if hourly.visibility_m is not None:
        vis_km = hourly.visibility_m / 1000
        parts.append(f"Vis {vis_km:.0f}km")
    if hourly.precipitation_mm is not None and hourly.precipitation_mm > 0:
        precip_detail = [f"Precip {hourly.precipitation_mm:.1f}mm"]
        if hourly.rain_mm is not None:
            precip_detail.append(f"Rain {hourly.rain_mm:.1f}mm")
        if hourly.showers_mm is not None and hourly.showers_mm > 0:
            precip_detail.append(f"Showers {hourly.showers_mm:.1f}mm")
        if hourly.snowfall_cm is not None and hourly.snowfall_cm > 0:
            precip_detail.append(f"Snow {hourly.snowfall_cm:.1f}cm")
        parts.append(f"{' ('.join(precip_detail[:1])}"
                     + (f" ({', '.join(precip_detail[1:])})" if len(precip_detail) > 1 else ""))
    if hourly.freezing_level_m is not None:
        fzl_ft = hourly.freezing_level_m * 3.28084
        parts.append(f"FzLvl {fzl_ft:.0f}ft")
    if parts:
        lines.append(f"    Wx: {', '.join(parts)}")

    # Upper level near cruise
    level = hourly.level_at(cruise_pressure_hpa)
    # If exact cruise pressure not available, try closest standard level
    if level is None:
        for pl in hourly.pressure_levels:
            if pl.wind_speed_kt is not None:
                if level is None or abs(pl.pressure_hpa - cruise_pressure_hpa) < abs(
                    level.pressure_hpa - cruise_pressure_hpa
                ):
                    level = pl

    if level and level.wind_speed_kt is not None:
        alt_str = ""
        if level.geopotential_height_m is not None:
            alt_str = f" ({level.geopotential_height_m * 3.28084:.0f}ft)"
        temp_str = f"T {level.temperature_c:.0f}C" if level.temperature_c is not None else ""
        lines.append(
            f"    {level.pressure_hpa}hPa{alt_str}: "
            f"Wind {level.wind_direction_deg:.0f}/{level.wind_speed_kt:.0f}kt"
            f"{', ' + temp_str if temp_str else ''}"
        )

    return lines


def _format_waypoint_analysis(analysis: WaypointAnalysis) -> list[str]:
    """Format analysis results for a waypoint."""
    lines = []

    # Wind components
    if analysis.wind_components:
        lines.append("  Wind analysis:")
        for model, wc in analysis.wind_components.items():
            if wc.headwind_kt > 0:
                lines.append(f"    [{model}] {wc.headwind_kt:.0f}kt headwind, {abs(wc.crosswind_kt):.0f}kt crosswind")
            else:
                lines.append(f"    [{model}] {abs(wc.headwind_kt):.0f}kt tailwind, {abs(wc.crosswind_kt):.0f}kt crosswind")

    # Sounding-based analysis
    if analysis.sounding:
        lines.extend(_format_sounding_analysis(analysis.sounding))
    else:
        lines.append("  No sounding data available")

    # Altitude advisories
    if analysis.altitude_advisories:
        lines.extend(_format_altitude_advisories(analysis.altitude_advisories))

    return lines


def _format_altitude_advisories(adv: AltitudeAdvisories) -> list[str]:
    """Format altitude regimes and advisories."""
    lines: list[str] = []

    # Cruise icing status
    if adv.cruise_in_icing:
        lines.append(f"  ** CRUISE IN ICING ({adv.cruise_icing_risk.value.upper()}) **")

    # Per-model regimes
    for model, regimes in adv.regimes.items():
        non_clear = [r for r in regimes if r.label != "Clear"]
        if non_clear:
            lines.append(f"  Vertical profile [{model}]:")
            for r in regimes:
                lines.append(f"    {r.floor_ft:.0f}-{r.ceiling_ft:.0f}ft: {r.label}")

    # Advisories
    for advisory in adv.advisories:
        feasible_str = "" if advisory.feasible else " [INFEASIBLE]"
        lines.append(f"  Advisory: {advisory.reason}{feasible_str}")
        if advisory.per_model_ft:
            model_parts = []
            for m, alt in advisory.per_model_ft.items():
                model_parts.append(f"{m}={alt:.0f}ft" if alt is not None else f"{m}=N/A")
            lines.append(f"    Per model: {', '.join(model_parts)}")

    return lines


def _format_sounding_analysis(soundings: dict[str, SoundingAnalysis]) -> list[str]:
    """Format sounding-based analysis for all models at a waypoint."""
    lines = []

    for model, sa in soundings.items():
        # Thermodynamic indices summary
        idx = sa.indices
        if idx is not None:
            idx_parts = []
            if idx.freezing_level_ft is not None:
                idx_parts.append(f"FzLvl {idx.freezing_level_ft:.0f}ft")
            if idx.cape_surface_jkg is not None:
                idx_parts.append(f"CAPE {idx.cape_surface_jkg:.0f}J/kg")
            if idx.lcl_altitude_ft is not None:
                idx_parts.append(f"LCL {idx.lcl_altitude_ft:.0f}ft")
            if idx.k_index is not None:
                idx_parts.append(f"KI {idx.k_index:.0f}")
            if idx_parts:
                lines.append(f"  Indices [{model}]: {', '.join(idx_parts)}")

        # Convective assessment
        if sa.convective and sa.convective.risk_level != ConvectiveRisk.NONE:
            conv = sa.convective
            regime = f" [{conv.regime.label}]" if conv.regime else ""
            lines.append(f"  Convective [{model}]: {conv.risk_level.value.upper()}{regime}")
            for drv in conv.drivers:
                lines.append(f"    + {drv}")
            for sup in conv.suppressors:
                lines.append(f"    - {sup}")
            for mod in conv.severe_modifiers:
                lines.append(f"    ! {mod}")

        # Icing zones
        if sa.icing_zones:
            for zone in sa.icing_zones:
                sld_str = " SLD!" if zone.sld_risk else ""
                lines.append(
                    f"  Icing [{model}]: {zone.risk.value} {zone.icing_type.value} "
                    f"{zone.base_ft:.0f}-{zone.top_ft:.0f}ft "
                    f"(Tw={zone.mean_wet_bulb_c:.0f}C){sld_str}"
                )

        # Precipitation
        precip = sa.precipitation
        if precip and precip.surface_phase != PrecipPhase.DRY:
            phase_label = precip.surface_phase.value.replace("_", " ").title()
            intensity_label = precip.surface_intensity.value
            precip_parts = [f"{phase_label} at surface ({intensity_label}"]
            if precip.total_mm:
                precip_parts.append(f" {precip.total_mm:.1f}mm/h")
            precip_parts.append(")")
            lines.append(f"  Precipitation [{model}]: {''.join(precip_parts)}")
            if precip.precipitation_zones:
                zone_strs = []
                for zone in precip.precipitation_zones:
                    zone_strs.append(
                        f"{zone.phase.value.replace('_', ' ').title()} "
                        f"{zone.base_ft:.0f}-{zone.top_ft:.0f}ft"
                    )
                lines.append(f"    {', '.join(zone_strs)}")
            if precip.freezing_rain_risk:
                lines.append(
                    f"    ** Freezing rain risk: warm nose "
                    f"{precip.warm_nose_base_ft:.0f}-{precip.warm_nose_top_ft:.0f}ft "
                    f"with cold surface layer **"
                )

        # Vertical motion
        vm = sa.vertical_motion
        if vm is not None and vm.classification != VerticalMotionClass.UNAVAILABLE:
            vm_parts = [vm.classification.value.replace("_", " ").title()]
            if vm.max_w_fpm is not None:
                vm_parts.append(f"max {vm.max_w_fpm:+.0f} ft/min at {vm.max_w_level_ft:.0f}ft")
            lines.append(f"  Vertical motion [{model}]: {', '.join(vm_parts)}")
            if vm.cat_risk_layers:
                for layer in vm.cat_risk_layers:
                    lines.append(
                        f"    CAT {layer.risk.value.upper()} "
                        f"{layer.base_ft:.0f}-{layer.top_ft:.0f}ft "
                        f"(Ri={layer.richardson_number:.2f})"
                    )
            if vm.convective_contamination:
                lines.append(f"    ** Mid-level convective contamination **")

        # Cloud layers
        if sa.cloud_layers:
            for cl in sa.cloud_layers:
                lines.append(
                    f"  Cloud [{model}]: {cl.coverage.value.upper()} "
                    f"{cl.base_ft:.0f}-{cl.top_ft:.0f}ft "
                    f"(T={cl.mean_temperature_c:.0f}C)" if cl.mean_temperature_c is not None
                    else f"  Cloud [{model}]: {cl.coverage.value.upper()} "
                    f"{cl.base_ft:.0f}-{cl.top_ft:.0f}ft"
                )
        elif not sa.icing_zones:
            lines.append(f"  [{model}]: Clear, no icing")

    return lines


def _format_route_observations(obs: RouteObservations) -> list[str]:
    """Format METAR/TAF observations for plain-text digest."""
    lines: list[str] = ["--- METAR/TAF Observations ---"]
    lines.append(
        f"  {obs.airports_with_metar} airports with METAR, "
        f"{obs.airports_with_taf} with TAF "
        f"(within {obs.corridor_nm:.0f}nm corridor)"
    )
    if obs.worst_metar_category:
        lines.append(f"  Worst METAR: {obs.worst_metar_category}")
    if obs.phenomena_along_route:
        lines.append(f"  Phenomena: {', '.join(obs.phenomena_along_route)}")
    if obs.has_conflicts:
        lines.append("  ** Obs/model conflicts detected **")
    lines.append("")

    for apt in obs.airports:
        if not apt.has_metar and not apt.has_taf:
            continue
        cat_str = f" [{apt.metar_flight_category}]" if apt.metar_flight_category else ""
        dist_str = f"{apt.distance_from_route_nm:.0f}nm"
        eta_str = f", ETA +{apt.eta_hour_offset}h" if apt.eta_hour_offset is not None else ""
        lines.append(f"  {apt.icao}{cat_str} ({dist_str} from route{eta_str})")
        if apt.metar_raw:
            lines.append(f"    METAR: {apt.metar_raw}")
        if apt.has_taf:
            taf_cat = f" [{apt.taf_flight_category_at_eta}]" if apt.taf_flight_category_at_eta else ""
            lines.append(f"    TAF at ETA{taf_cat}")

    for comp in obs.comparisons:
        if comp.category_match != "CONFIRMING":
            lines.append(f"  [{comp.category_match}] {comp.icao}: {comp.detail}")

    lines.append("")
    return lines


def _format_sigmet_band(base_ft: int | None, top_ft: int | None) -> str:
    """Format a SIGMET vertical band, e.g. 'SFC-FL100' or 'FL080-FL240'."""
    if base_ft is None and top_ft is None:
        return ""
    return f"{format_flight_level(base_ft)}-{format_flight_level(top_ft)}"


def _format_route_sigmets(sig: RouteSigmets) -> list[str]:
    """Format route SIGMETs for plain-text digest."""
    lines: list[str] = ["--- SIGMETs Along Route ---"]
    lines.append(
        f"  {sig.count} SIGMET(s) intersecting route "
        f"(within {sig.corridor_nm:.0f}nm corridor)"
    )
    if sig.hazards:
        lines.append(f"  Hazards: {', '.join(sig.hazards)}")
    if sig.has_severe:
        lines.append("  ** Severe (SEV) SIGMET in effect **")
    lines.append("")

    for s in sig.sigmets:
        head = " ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET"
        band = _format_sigmet_band(s.base_ft, s.top_ft)
        band_str = f" {band}" if band else ""
        fir_str = f" {s.fir_id}" if s.fir_id else ""
        if s.enroute_distance_from_nm is not None and s.enroute_distance_to_nm is not None:
            span = f" @ {s.enroute_distance_from_nm:.0f}-{s.enroute_distance_to_nm:.0f}nm enroute"
        elif s.min_distance_nm is not None:
            span = f" @ {s.min_distance_nm:.0f}nm from route"
        else:
            span = ""
        lines.append(f"  [{head}]{fir_str}{band_str}{span}")
        if s.raw_text:
            lines.append(f"    {s.raw_text}")

    lines.append("")
    return lines


def _format_model_agreement(snapshot: ForecastSnapshot) -> list[str]:
    """Format overall model agreement summary."""
    lines = ["--- Model Agreement ---"]

    has_data = False
    for analysis in snapshot.analyses:
        if not analysis.model_divergence:
            continue
        has_data = True
        poor = [d for d in analysis.model_divergence if d.agreement == AgreementLevel.POOR]
        moderate = [d for d in analysis.model_divergence if d.agreement == AgreementLevel.MODERATE]
        good = [d for d in analysis.model_divergence if d.agreement == AgreementLevel.GOOD]

        lines.append(f"  {analysis.waypoint.icao}:")
        if poor:
            for d in poor:
                models_str = ", ".join(f"{k}={v:.1f}" for k, v in d.model_values.items())
                lines.append(f"    POOR {d.variable}: spread {d.spread:.1f} ({models_str})")
        if moderate:
            names = ", ".join(d.variable for d in moderate)
            lines.append(f"    Moderate: {names}")
        if good:
            lines.append(f"    Good: {len(good)} variables")

    if not has_data:
        lines.append("  No multi-model comparison available")

    return lines
