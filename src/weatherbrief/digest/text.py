"""Plain text digest formatter for forecast snapshots."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.models import (
    AgreementLevel,
    AltitudeAdvisories,
    ConvectiveRisk,
    ForecastSnapshot,
    IcingRisk,
    SoundingAnalysis,
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
        parts.append(f"Precip {hourly.precipitation_mm:.1f}mm")
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
            lines.append(f"  Convective [{model}]: {sa.convective.risk_level.value.upper()}")
            for mod in sa.convective.severe_modifiers:
                lines.append(f"    - {mod}")

        # Icing zones
        if sa.icing_zones:
            for zone in sa.icing_zones:
                sld_str = " SLD!" if zone.sld_risk else ""
                lines.append(
                    f"  Icing [{model}]: {zone.risk.value} {zone.icing_type.value} "
                    f"{zone.base_ft:.0f}-{zone.top_ft:.0f}ft "
                    f"(Tw={zone.mean_wet_bulb_c:.0f}C){sld_str}"
                )

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
