"""Plain text digest formatter for forecast snapshots."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.models import (
    AgreementLevel,
    ForecastSnapshot,
    IcingRisk,
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

    # Icing
    if analysis.icing_bands:
        has_risk = False
        for model, bands in analysis.icing_bands.items():
            risky = [b for b in bands if b.risk != IcingRisk.NONE]
            if risky:
                if not has_risk:
                    lines.append("  Icing:")
                    has_risk = True
                for b in risky:
                    alt_str = f"{b.altitude_ft:.0f}ft" if b.altitude_ft else f"{b.pressure_hpa}hPa"
                    lines.append(f"    [{model}] {b.risk.value} at {alt_str} (T={b.temperature_c:.0f}C)")
        if not has_risk:
            lines.append("  Icing: None detected")

    # Clouds
    if analysis.cloud_layers:
        for model, layers in analysis.cloud_layers.items():
            if layers:
                lines.append(f"  Clouds [{model}]:")
                for cl in layers:
                    top_str = f"-{cl.top_ft:.0f}ft" if cl.top_ft else " top unknown"
                    lines.append(f"    {cl.base_ft:.0f}{top_str}")
            else:
                lines.append(f"  Clouds [{model}]: Clear")

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
