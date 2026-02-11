"""Assemble LLM context string from ForecastSnapshot + DWD text forecasts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from weatherbrief.models import (
    AgreementLevel,
    ConvectiveRisk,
    ForecastSnapshot,
    IcingRisk,
    SoundingAnalysis,
)

if TYPE_CHECKING:
    from weatherbrief.digest.llm_digest import WeatherDigest
    from weatherbrief.fetch.dwd_text import DWDTextForecasts


def build_digest_context(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    text_forecasts: DWDTextForecasts | None = None,
    previous_digest: WeatherDigest | None = None,
) -> str:
    """Build the full context string for the LLM briefer.

    Sections:
    1. Route / date / altitude metadata
    2. Quantitative data per waypoint
    3. Model comparison
    4. DWD text forecasts (German)
    5. Trend from previous digest
    """
    sections: list[str] = []

    # --- Header ---
    waypoints_str = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    days_label = f"D-{snapshot.days_out}" if snapshot.days_out > 0 else "D-0 (today)"
    sections.append(
        f"ROUTE: {waypoints_str}\n"
        f"DATE: {snapshot.target_date} ({days_label})\n"
        f"ALTITUDE: {snapshot.route.cruise_altitude_ft}ft "
        f"(~{snapshot.route.cruise_pressure_hpa}hPa)"
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

        # Band comparisons
        if wp_analysis.band_comparisons:
            quant_lines.append("  Altitude band comparison:")
            for bc in wp_analysis.band_comparisons:
                active = any(
                    s.worst_icing_risk != IcingRisk.NONE or s.cloud_coverage is not None
                    for s in bc.models.values()
                )
                if not active:
                    continue
                agree_notes = []
                if not bc.icing_agreement:
                    agree_notes.append("icing disagree")
                if not bc.cloud_agreement:
                    agree_notes.append("cloud disagree")
                agree_str = f" [{', '.join(agree_notes)}]" if agree_notes else ""
                quant_lines.append(f"    {bc.band.name}{agree_str}:")
                for mk, ms in bc.models.items():
                    parts = []
                    if ms.worst_icing_risk != IcingRisk.NONE:
                        parts.append(f"icing={ms.worst_icing_risk.value}/{ms.worst_icing_type.value}")
                        if ms.sld_risk:
                            parts.append("SLD!")
                    if ms.cloud_coverage is not None:
                        parts.append(f"cloud={ms.cloud_coverage.value}")
                    if parts:
                        quant_lines.append(f"      [{mk}] {', '.join(parts)}")

    sections.append("\n".join(quant_lines))

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

    # --- Text forecasts ---
    if text_forecasts and (text_forecasts.short_range or text_forecasts.medium_range):
        text_lines: list[str] = ["=== TEXT FORECASTS (DWD, German) ==="]
        if text_forecasts.medium_range:
            text_lines.append(
                f"\n--- Mittelfrist (medium-range) ---\n{text_forecasts.medium_range}"
            )
        if text_forecasts.short_range:
            text_lines.append(
                f"\n--- Kurzfrist (short-range) ---\n{text_forecasts.short_range}"
            )
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

    return lines
