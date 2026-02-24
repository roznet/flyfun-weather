"""Assemble LLM context string from ForecastSnapshot + text forecasts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from weatherbrief.models import (
    ConvectiveRisk,
    ForecastSnapshot,
    PrecipPhase,
    RouteAdvisoriesManifest,
    RouteObservations,
    SoundingAnalysis,
)

if TYPE_CHECKING:
    from weatherbrief.digest.llm_digest import WeatherDigest
    from weatherbrief.fetch.text_forecasts import TextForecasts

# Advisory IDs excluded from digest context (meta-level, not useful for LLM)
_DIGEST_EXCLUDE_IDS = {"model_agreement"}


def build_digest_context(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    text_forecasts: TextForecasts | None = None,
    previous_digest: WeatherDigest | None = None,
    route_advisories: RouteAdvisoriesManifest | None = None,
    flight_rules: str | None = None,
) -> str:
    """Build the full context string for the LLM briefer.

    Sections:
    1. Route / date / altitude metadata + pilot capability
    2. Quantitative data per waypoint
    3. Route advisories (deterministic hazard assessments)
    4. Model comparison
    5. Text forecasts (NWS AFD or DWD, region-dependent)
    6. Trend from previous digest
    """
    sections: list[str] = []

    # --- Header ---
    waypoints_str = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    days_label = f"D-{snapshot.days_out}" if snapshot.days_out > 0 else "D-0 (today)"
    capability = "VFR only" if flight_rules == "vfr_only" else "VFR + IFR"
    sections.append(
        f"ROUTE: {waypoints_str}\n"
        f"DATE: {snapshot.target_date} ({days_label})\n"
        f"ALTITUDE: {snapshot.route.cruise_altitude_ft}ft "
        f"(~{snapshot.route.cruise_pressure_hpa}hPa)\n"
        f"PILOT CAPABILITY: {capability}"
    )

    # --- Quantitative data per waypoint ---
    quant_lines: list[str] = ["=== QUANTITATIVE DATA ==="]
    for wp in snapshot.route.waypoints:
        quant_lines.append(f"\n--- {wp.icao} ({wp.name}) ---")

        wp_forecasts = [f for f in snapshot.forecasts if f.waypoint.icao == wp.icao]
        for wf in wp_forecasts:
            hourly = wf.at_time(target_time)
            if not hourly:
                continue

            quant_lines.append(f"[{wf.model.value}]:")

            # Surface conditions
            sfc_parts = []
            if hourly.temperature_2m_c is not None:
                sfc_parts.append(f"T={hourly.temperature_2m_c:.1f}C")
            if hourly.dewpoint_2m_c is not None:
                sfc_parts.append(f"Td={hourly.dewpoint_2m_c:.1f}C")
            if hourly.wind_speed_10m_kt is not None:
                sfc_parts.append(
                    f"Wind {hourly.wind_direction_10m_deg:.0f}/{hourly.wind_speed_10m_kt:.0f}kt"
                )
            if hourly.wind_gusts_10m_kt is not None:
                sfc_parts.append(f"G{hourly.wind_gusts_10m_kt:.0f}kt")
            if sfc_parts:
                quant_lines.append(f"  Surface: {', '.join(sfc_parts)}")

            # Weather
            wx_parts = []
            if hourly.cloud_cover_pct is not None:
                wx_parts.append(f"Cloud={hourly.cloud_cover_pct:.0f}%")
            if hourly.visibility_m is not None:
                wx_parts.append(f"Vis={hourly.visibility_m/1000:.1f}km")
            if hourly.precipitation_mm is not None:
                wx_parts.append(f"Precip={hourly.precipitation_mm:.1f}mm")
                if hourly.rain_mm is not None:
                    wx_parts.append(f"Rain={hourly.rain_mm:.1f}mm")
                if hourly.showers_mm is not None and hourly.showers_mm > 0:
                    wx_parts.append(f"Showers={hourly.showers_mm:.1f}mm")
                if hourly.snowfall_cm is not None and hourly.snowfall_cm > 0:
                    wx_parts.append(f"Snow={hourly.snowfall_cm:.1f}cm")
                if hourly.weather_code is not None:
                    wx_parts.append(f"WMO={hourly.weather_code}")
            if hourly.freezing_level_m is not None:
                fzl_ft = hourly.freezing_level_m * 3.28084
                wx_parts.append(f"FzLvl={fzl_ft:.0f}ft")
            if hourly.cape_jkg is not None:
                wx_parts.append(f"CAPE={hourly.cape_jkg:.0f}J/kg")
            if wx_parts:
                quant_lines.append(f"  Wx: {', '.join(wx_parts)}")

            # Cruise-level data (closest pressure level to cruise)
            cruise_p = snapshot.route.cruise_pressure_hpa
            level = hourly.level_at(cruise_p)
            if level is None:
                # Find closest available level
                for pl in hourly.pressure_levels:
                    if pl.wind_speed_kt is not None:
                        if level is None or abs(pl.pressure_hpa - cruise_p) < abs(
                            level.pressure_hpa - cruise_p
                        ):
                            level = pl
            if level and level.wind_speed_kt is not None:
                cruise_parts = [
                    f"{level.pressure_hpa}hPa",
                    f"Wind {level.wind_direction_deg:.0f}/{level.wind_speed_kt:.0f}kt",
                ]
                if level.temperature_c is not None:
                    cruise_parts.append(f"T={level.temperature_c:.1f}C")
                if level.relative_humidity_pct is not None:
                    cruise_parts.append(f"RH={level.relative_humidity_pct:.0f}%")
                quant_lines.append(f"  Cruise: {', '.join(cruise_parts)}")

        # Analysis results
        wp_analysis = next(
            (a for a in snapshot.analyses if a.waypoint.icao == wp.icao), None
        )
        if not wp_analysis:
            continue

        # Wind components
        if wp_analysis.wind_components:
            wc_parts = []
            for model, wc in wp_analysis.wind_components.items():
                if wc.headwind_kt > 0:
                    wc_parts.append(f"[{model}] {wc.headwind_kt:.0f}kt headwind")
                else:
                    wc_parts.append(f"[{model}] {abs(wc.headwind_kt):.0f}kt tailwind")
            quant_lines.append(f"  Wind components: {'; '.join(wc_parts)}")

        # Sounding analysis
        if wp_analysis.sounding:
            quant_lines.extend(_format_sounding_context(wp_analysis.sounding))

    sections.append("\n".join(quant_lines))

    # --- Route advisories ---
    if route_advisories:
        sections.append(_format_route_advisories_context(route_advisories))

    # --- Model comparison ---
    comp_lines: list[str] = ["=== MODEL COMPARISON ==="]
    has_comparison = False
    for analysis in snapshot.analyses:
        if not analysis.model_divergence:
            continue
        has_comparison = True
        comp_lines.append(f"\n{analysis.waypoint.icao}:")
        for div in analysis.model_divergence:
            values_str = ", ".join(f"{k}={v:.1f}" for k, v in div.model_values.items())
            comp_lines.append(
                f"  {div.variable}: {div.agreement.value} agreement "
                f"(spread={div.spread:.1f}, {values_str})"
            )
    if not has_comparison:
        comp_lines.append("No multi-model comparison available.")
    sections.append("\n".join(comp_lines))

    # --- METAR/TAF observations (D-0 only) ---
    if snapshot.route_observations:
        sections.append(_format_observations_context(snapshot.route_observations))

    # --- Text forecasts ---
    if text_forecasts and text_forecasts.entries:
        header = (
            f"=== TEXT FORECASTS ({text_forecasts.source_label}, "
            f"{text_forecasts.language_note}) ==="
        )
        text_lines: list[str] = [header]
        for entry in text_forecasts.entries:
            text_lines.append(f"\n--- {entry.label} ---\n{entry.text}")
        sections.append("\n".join(text_lines))

    # --- Trend ---
    if previous_digest:
        trend_lines: list[str] = ["=== PREVIOUS DIGEST (for trend comparison) ==="]
        trend_lines.append(f"Previous assessment: {previous_digest.assessment}")
        trend_lines.append(f"Reason: {previous_digest.assessment_reason}")
        trend_lines.append(f"Synoptic: {previous_digest.synoptic}")
        sections.append("\n".join(trend_lines))

    return "\n\n".join(sections)


def _format_sounding_context(soundings: dict[str, SoundingAnalysis]) -> list[str]:
    """Format sounding analysis data for LLM context."""
    lines: list[str] = []
    for model, sa in soundings.items():
        idx = sa.indices
        if idx is not None:
            parts = []
            if idx.freezing_level_ft is not None:
                parts.append(f"FzLvl={idx.freezing_level_ft:.0f}ft")
            if idx.minus10c_level_ft is not None:
                parts.append(f"-10C={idx.minus10c_level_ft:.0f}ft")
            if idx.cape_surface_jkg is not None:
                parts.append(f"CAPE={idx.cape_surface_jkg:.0f}J/kg")
            if idx.cin_surface_jkg is not None:
                parts.append(f"CIN={idx.cin_surface_jkg:.0f}J/kg")
            if idx.lcl_altitude_ft is not None:
                parts.append(f"LCL={idx.lcl_altitude_ft:.0f}ft")
            if idx.k_index is not None:
                parts.append(f"KI={idx.k_index:.0f}")
            if idx.total_totals is not None:
                parts.append(f"TT={idx.total_totals:.0f}")
            if idx.precipitable_water_mm is not None:
                parts.append(f"PW={idx.precipitable_water_mm:.1f}mm")
            if idx.bulk_shear_0_6km_kt is not None:
                parts.append(f"Shear0-6km={idx.bulk_shear_0_6km_kt:.0f}kt")
            if parts:
                lines.append(f"  Sounding [{model}]: {', '.join(parts)}")

        # NWP 3-level cloud cover (not available for ECMWF)
        if sa.cloud_cover_low_pct is not None:
            lines.append(
                f"  NWP cloud [{model}]: Low={sa.cloud_cover_low_pct:.0f}%"
                f", Mid={sa.cloud_cover_mid_pct:.0f}%"
                f", High={sa.cloud_cover_high_pct:.0f}%"
            )

        if sa.convective and sa.convective.risk_level != ConvectiveRisk.NONE:
            lines.append(f"  Convective [{model}]: {sa.convective.risk_level.value}")
            for mod in sa.convective.severe_modifiers:
                lines.append(f"    - {mod}")

        for zone in sa.icing_zones:
            sld = " SLD!" if zone.sld_risk else ""
            lines.append(
                f"  Icing zone [{model}]: {zone.risk.value} {zone.icing_type.value} "
                f"{zone.base_ft:.0f}-{zone.top_ft:.0f}ft (Tw={zone.mean_wet_bulb_c:.0f}C){sld}"
            )

        for cl in sa.cloud_layers:
            t_str = f" T={cl.mean_temperature_c:.0f}C" if cl.mean_temperature_c is not None else ""
            lines.append(
                f"  Cloud [{model}]: {cl.coverage.value.upper()} "
                f"{cl.base_ft:.0f}-{cl.top_ft:.0f}ft{t_str}"
            )

        if sa.precipitation and sa.precipitation.surface_phase != PrecipPhase.DRY:
            p = sa.precipitation
            parts = [p.surface_phase.value]
            parts.append(f"intensity={p.surface_intensity.value}")
            if p.rain_mm:
                parts.append(f"rain={p.rain_mm:.1f}mm")
            if p.snow_cm:
                parts.append(f"snow={p.snow_cm:.1f}cm")
            if p.freezing_rain_risk:
                parts.append("FREEZING RAIN RISK")
            lines.append(f"  Precip [{model}]: {', '.join(parts)}")
            for zone in p.precipitation_zones:
                tw_str = f" Tw={zone.mean_wet_bulb_c:.0f}C" if zone.mean_wet_bulb_c is not None else ""
                lines.append(
                    f"    {zone.phase.value} {zone.base_ft:.0f}-{zone.top_ft:.0f}ft{tw_str}"
                )

    return lines


def _format_observations_context(obs: RouteObservations) -> str:
    """Format METAR/TAF observations into a compact LLM context section."""
    lines: list[str] = ["=== METAR/TAF OBSERVATIONS ==="]
    lines.append(
        f"Corridor: {obs.corridor_nm}nm | "
        f"Airports: {obs.airports_found} found, "
        f"{obs.airports_with_metar} with METAR, "
        f"{obs.airports_with_taf} with TAF"
    )
    if obs.worst_metar_category:
        lines.append(f"Worst METAR category: {obs.worst_metar_category}")
    if obs.worst_taf_category:
        lines.append(f"Worst TAF category: {obs.worst_taf_category}")
    if obs.phenomena_along_route:
        lines.append(f"Phenomena along route: {', '.join(obs.phenomena_along_route)}")
    if obs.has_conflicts:
        lines.append("** CONFLICTS between observations and model predictions **")

    for apt in obs.airports:
        if not apt.has_metar and not apt.has_taf:
            continue
        dist_str = f"{apt.distance_from_route_nm:.0f}nm from route"
        eta_str = f", ETA +{apt.eta_hour_offset}h" if apt.eta_hour_offset is not None else ""
        parts = [f"{apt.icao} ({dist_str}{eta_str}, near {apt.nearest_waypoint_icao})"]

        if apt.has_metar:
            cat_str = f" [{apt.metar_flight_category}]" if apt.metar_flight_category else ""
            parts.append(f"  METAR{cat_str}: {apt.metar_raw}")
        if apt.has_taf:
            cat_str = f" [{apt.taf_flight_category_at_eta}]" if apt.taf_flight_category_at_eta else ""
            trend_str = f" ({apt.taf_trend_type})" if apt.taf_trend_type else ""
            parts.append(f"  TAF at ETA{cat_str}{trend_str}")

        lines.append("\n".join(parts))

    # Comparison annotations
    for comp in obs.comparisons:
        if comp.category_match != "CONFIRMING":
            lines.append(
                f"  [{comp.category_match}] {comp.icao}: {comp.detail}"
            )

    return "\n".join(lines)


def _format_route_advisories_context(manifest: RouteAdvisoriesManifest) -> str:
    """Format route advisory manifest into a compact LLM context section."""
    # Build advisory_id → name lookup from catalog
    name_map = {entry.id: entry.name for entry in manifest.catalog}

    lines: list[str] = ["=== ROUTE ADVISORIES ==="]

    for result in manifest.advisories:
        if result.advisory_id in _DIGEST_EXCLUDE_IDS:
            continue

        name = name_map.get(result.advisory_id, result.advisory_id)
        status_tag = result.aggregate_status.value.upper()
        detail = result.aggregate_detail

        lines.append(f"[{status_tag}] {name}: {detail}")

        # Per-model breakdown
        model_parts = []
        for m in result.per_model:
            m_status = m.status.value.upper()
            if m.affected_pct > 0:
                model_parts.append(f"{m.model}={m_status}: {m.affected_pct:.0f}% affected")
            else:
                model_parts.append(f"{m.model}={m_status}")
        if model_parts:
            lines.append(f"  {' | '.join(model_parts)}")

    if len(lines) == 1:
        lines.append("No route advisories available.")

    return "\n".join(lines)
