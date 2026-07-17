"""Assemble LLM context string from ForecastSnapshot + text forecasts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from weatherbrief.analysis.advisories.altitude_table import (
    diff_altitude_rows,
    row_for_altitude,
)
from weatherbrief.analysis.airport_conditions import format_wind_string
from weatherbrief.analysis.sounding.convective import convective_cross_check
from weatherbrief.digest.format_utils import format_flight_level
from weatherbrief.units import format_visibility
from weatherbrief.models import (
    ALT_AXIS_LABELS,
    AgreementLevel,
    AltitudeAdvisoryChange,
    AltitudeAdvisoryRow,
    AltitudeTableResult,
    ConvectiveRisk,
    ForecastSnapshot,
    MitigationKind,
    ModelDivergence,
    PrecipPhase,
    RouteAdvisoriesManifest,
    RouteAlternates,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
    SoundingAnalysis,
)

if TYPE_CHECKING:
    from weatherbrief.digest.llm_digest import LongRangeDigest, WeatherDigest
    from weatherbrief.fetch.dwd_text import DWDDayBlock
    from weatherbrief.fetch.text_forecasts import TextForecasts

# Advisory IDs excluded from digest context (meta-level, not useful for LLM)
_DIGEST_EXCLUDE_IDS = {"model_agreement"}


def _fmt_coords(lat: float, lon: float) -> str:
    """Format lat/lon with proper N/S E/W hemispheres."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.1f}{ns}, {abs(lon):.1f}{ew}"


def build_digest_context(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    text_forecasts: TextForecasts | None = None,
    previous_digest: WeatherDigest | LongRangeDigest | None = None,
    route_advisories: RouteAdvisoriesManifest | None = None,
    altitude_table: AltitudeTableResult | None = None,
    flight_rules: str | None = None,
    units_region: str | None = None,
    dwd_translated: list[tuple[DWDDayBlock, str]] | None = None,
    dwd_is_synoptic_extract: bool = False,
    longrange: bool = False,
    confidence_note: str | None = None,
) -> str:
    """Build the full context string for the LLM briefer.

    Sections:
    1. Route / date / altitude metadata + pilot capability
    2. Quantitative data per waypoint (includes model divergence)
    3. Route advisories (deterministic hazard assessments)
    4. Text forecasts (NWS AFD or translated DWD, region-dependent)
    5. Trend from previous digest

    ``longrange=True`` produces the trimmed context for the early long-range
    outlook (beyond the ECMWF GRIB horizon): the per-waypoint quantitative
    block is reduced to coarse signals, all model divergence is shown (not just
    moderate/poor) since agreement is the headline, and the precise sounding
    indices are dropped because they are not skilful at that range. The
    advisories, text forecasts and trend sections are unchanged. ``confidence_note``
    is a code-computed line (e.g. "more detail from <date>") prepended so the
    LLM can phrase it without doing its own date arithmetic.
    """
    sections: list[str] = []

    # --- Header ---
    waypoints_str = " -> ".join(
        f"{wp.icao} ({_fmt_coords(wp.lat, wp.lon)})" if wp.lat is not None else wp.icao
        for wp in snapshot.route.waypoints
    )
    days_label = f"D-{snapshot.days_out}" if snapshot.days_out > 0 else "D-0 (today)"
    capability = "VFR only" if flight_rules == "vfr_only" else "VFR + IFR"
    # Include day-of-week so the LLM doesn't miscalculate from the date
    target_dt = datetime.fromisoformat(snapshot.target_date)
    day_name = target_dt.strftime("%A")
    fetch_dt = datetime.fromisoformat(snapshot.fetch_date)
    fetch_day = fetch_dt.strftime("%A")
    sections.append(
        f"ROUTE: {waypoints_str}\n"
        f"DATE: {day_name} {snapshot.target_date} ({days_label})\n"
        f"BRIEFING ISSUED: {fetch_day} {snapshot.fetch_date}\n"
        f"ALTITUDE: {snapshot.route.cruise_altitude_ft}ft "
        f"(~{snapshot.route.cruise_pressure_hpa}hPa)\n"
        f"PILOT CAPABILITY: {capability}"
    )

    # --- Forecast confidence note (long-range only, code-computed) ---
    if confidence_note:
        sections.append(f"=== FORECAST CONFIDENCE ===\n{confidence_note}")

    # --- Options to improve (deterministic): the altitude trade-off plus
    #     tactical (non-altitude) mitigations, consolidated into one section so
    #     the LLM phrases a pre-typed structure rather than reconciling overlaps.
    #     Advice only — never changes the assessment (#330). ---
    options_block = _format_options_to_improve_context(altitude_table, route_advisories)
    if options_block:
        sections.append(options_block)

    # --- Quantitative data per waypoint ---
    if longrange:
        sections.append(_build_coarse_quant(snapshot, target_time))
        # Long range: include DWD only via the strictly-covering blocks the
        # caller already filtered (``dwd_translated``); drop the raw text block
        # entirely, since NWS AFD (~24-48h) and any non-covering text would
        # describe a day other than the flight and confuse the outlook.
        return _append_shared_sections(
            sections,
            snapshot=snapshot,
            route_advisories=route_advisories,
            text_forecasts=None,
            previous_digest=previous_digest,
            dwd_translated=dwd_translated,
            dwd_is_synoptic_extract=dwd_is_synoptic_extract,
        )

    quant_lines: list[str] = ["=== QUANTITATIVE DATA ==="]
    for wp in snapshot.route.waypoints:
        coord_str = f" [{_fmt_coords(wp.lat, wp.lon)}]" if wp.lat is not None else ""
        quant_lines.append(f"\n--- {wp.icao} ({wp.name}){coord_str} ---")

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
            sfc_wind = format_wind_string(
                hourly.wind_direction_10m_deg,
                hourly.wind_speed_10m_kt,
                hourly.wind_gusts_10m_kt,
            )
            if sfc_wind:
                sfc_parts.append(f"Wind {sfc_wind}kt")
            if sfc_parts:
                quant_lines.append(f"  Surface: {', '.join(sfc_parts)}")

            # Weather
            wx_parts = []
            if hourly.cloud_cover_pct is not None:
                wx_parts.append(f"Cloud={hourly.cloud_cover_pct:.0f}%")
            if hourly.visibility_m is not None:
                wx_parts.append(f"Vis={format_visibility(hourly.visibility_m, units_region)}")
            if hourly.precipitation_mm is not None:
                wx_parts.append(f"Precip={hourly.precipitation_mm:.1f}mm")
                if hourly.rain_mm is not None:
                    wx_parts.append(f"Rain={hourly.rain_mm:.1f}mm")
                if hourly.showers_mm is not None and hourly.showers_mm > 0:
                    wx_parts.append(f"Showers={hourly.showers_mm:.1f}mm")
                if hourly.snowfall_cm is not None and hourly.snowfall_cm > 0:
                    wx_parts.append(f"Snow={hourly.snowfall_cm:.1f}cm")
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
                cruise_parts = [f"{level.pressure_hpa}hPa"]
                cruise_wind = format_wind_string(
                    level.wind_direction_deg, level.wind_speed_kt, None
                )
                if cruise_wind:
                    cruise_parts.append(f"Wind {cruise_wind}kt")
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

        # Model divergence (only moderate/poor — good agreement is implicit)
        if wp_analysis.model_divergence:
            quant_lines.extend(
                _format_divergence_context(wp_analysis.model_divergence)
            )

    sections.append("\n".join(quant_lines))
    return _append_shared_sections(
        sections,
        snapshot=snapshot,
        route_advisories=route_advisories,
        text_forecasts=text_forecasts,
        previous_digest=previous_digest,
        dwd_translated=dwd_translated,
        dwd_is_synoptic_extract=dwd_is_synoptic_extract,
    )


def _append_shared_sections(
    sections: list[str],
    *,
    snapshot: ForecastSnapshot,
    route_advisories: RouteAdvisoriesManifest | None,
    text_forecasts: TextForecasts | None,
    previous_digest: WeatherDigest | LongRangeDigest | None,
    dwd_translated: list[tuple[DWDDayBlock, str]] | None,
    dwd_is_synoptic_extract: bool,
) -> str:
    """Append the sections common to short- and long-range context and join.

    Advisories, METAR/TAF, SIGMETs, text forecasts and trend are identical for
    both regimes (METAR/TAF and SIGMETs are D-0 only, so they are simply absent
    at long range).
    """
    # --- Route advisories ---
    if route_advisories:
        sections.append(_format_route_advisories_context(route_advisories))

    # --- METAR/TAF observations (D-0 only) ---
    if snapshot.route_observations:
        sections.append(_format_observations_context(snapshot.route_observations))

    # --- Route SIGMETs (D-0 only) ---
    if snapshot.route_sigmets and snapshot.route_sigmets.count:
        dep = snapshot.departure_time
        arr = (
            dep + timedelta(hours=snapshot.route.flight_duration_hours)
            if dep is not None else None
        )
        sections.append(
            _format_sigmets_context(snapshot.route_sigmets, departure=dep, arrival=arr)
        )

    # NOTE: Weather alternates are intentionally NOT fed to the LLM prompt for
    # now (#210) — they surface in the briefing UI only. Re-enable by appending
    # _format_alternates_context(snapshot.alternates) here.

    # --- Text forecasts ---
    if dwd_translated:
        sections.append(_format_dwd_translated_context(
            dwd_translated, synoptic_extract=dwd_is_synoptic_extract,
        ))
    elif text_forecasts and text_forecasts.entries:
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
        sections.append(_format_previous_digest_context(previous_digest))

    return "\n\n".join(sections)


def _format_previous_digest_context(
    previous_digest: WeatherDigest | LongRangeDigest,
) -> str:
    """Render the previous digest for trend comparison.

    Works for both the short-range ``WeatherDigest`` (assessment/…) and the
    long-range ``LongRangeDigest`` (outlook/…) so trend survives a flight
    crossing the long→short-range boundary.
    """
    # Local import keeps the type out of module scope (llm_digest imports this
    # module — a top-level import would cycle), while still letting us use a
    # precise isinstance check instead of duck-typing on the attribute.
    from weatherbrief.digest.llm_digest import LongRangeDigest

    lines = ["=== PREVIOUS DIGEST (for trend comparison) ==="]
    if isinstance(previous_digest, LongRangeDigest):
        lines.append(f"Previous outlook: {previous_digest.outlook}")
        lines.append(f"Previous reason: {previous_digest.outlook_reason}")
        lines.append(f"Previous synoptic: {previous_digest.synoptic}")
        lines.append(f"Previous model agreement: {previous_digest.model_agreement}")
        lines.append(f"Previous trend: {previous_digest.trend}")
    else:
        lines.append(f"Previous assessment: {previous_digest.assessment}")
        lines.append(f"Previous reason: {previous_digest.assessment_reason}")
        lines.append(f"Previous synoptic: {previous_digest.synoptic}")
        lines.append(f"Previous trend: {previous_digest.trend}")
    return "\n".join(lines)


_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _compass(deg: float) -> str:
    return _COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def _build_coarse_quant(snapshot: ForecastSnapshot, target_time: datetime) -> str:
    """Coarse per-waypoint model data for the long-range outlook.

    Emits only QUALITATIVE bands (light/moderate/strong wind, clear/broken/
    overcast, dry/precip, some/notable instability) — never raw values. At this
    range the underlying numbers (10m wind, CAPE, cloud %) have low skill, and
    the long-range prompt forbids quoting figures; feeding bands instead of
    numbers keeps the context and the prompt consistent and removes the
    anchoring/leak risk. Precise sounding indices are omitted entirely; model
    agreement (shown via :func:`_format_divergence_all`) is the headline signal.
    """
    lines: list[str] = [
        "=== MODEL OUTLOOK DATA (coarse — long range, two/three global models) ==="
    ]
    for wp in snapshot.route.waypoints:
        coord_str = f" [{_fmt_coords(wp.lat, wp.lon)}]" if wp.lat is not None else ""
        lines.append(f"\n--- {wp.icao} ({wp.name}){coord_str} ---")

        for wf in [f for f in snapshot.forecasts if f.waypoint.icao == wp.icao]:
            hourly = wf.at_time(target_time)
            if not hourly:
                continue
            parts: list[str] = []
            ws = hourly.wind_speed_10m_kt
            if ws is not None:
                band = "light" if ws < 10 else ("moderate" if ws <= 20 else "strong")
                dirn = (
                    f" {_compass(hourly.wind_direction_10m_deg)}"
                    if hourly.wind_direction_10m_deg is not None else ""
                )
                gusty = (
                    hourly.wind_gusts_10m_kt is not None
                    and hourly.wind_gusts_10m_kt >= max(25.0, ws + 10)
                )
                parts.append(f"{band}{dirn} wind" + (", gusty" if gusty else ""))
            cc = hourly.cloud_cover_pct
            if cc is not None:
                parts.append("clear" if cc < 25 else ("broken cloud" if cc <= 75 else "overcast"))
            if hourly.precipitation_mm is not None:
                parts.append("precip" if hourly.precipitation_mm > 0.1 else "dry")
            if hourly.cape_jkg is not None and hourly.cape_jkg >= 100:
                parts.append(
                    "notable instability" if hourly.cape_jkg >= 500 else "some instability"
                )
            if parts:
                lines.append(f"[{wf.model.value}]: {', '.join(parts)}")

        wp_analysis = next(
            (a for a in snapshot.analyses if a.waypoint.icao == wp.icao), None
        )
        if wp_analysis and wp_analysis.model_divergence:
            lines.extend(_format_divergence_all(wp_analysis.model_divergence))

    return "\n".join(lines)


# Coarse variables surfaced in the long-range agreement line. The precise
# sounding indices (k_index, total_totals, bulk_shear, lcl, omega, …) are
# excluded — they are not skilful this far out and reintroduce false precision.
_LONGRANGE_DIVERGENCE_VARS = (
    "temperature_c",
    "wind_speed_kt",
    "wind_direction_deg",
    "cloud_cover_pct",
    "precipitation_mm",
    "freezing_level_ft",
    "cape_surface_jkg",
)


def _format_divergence_all(divergences: list[ModelDivergence]) -> list[str]:
    """Format model divergence for long range — agreement shown explicitly.

    Unlike :func:`_format_divergence_context` (which hides GOOD agreement as
    implicit), this surfaces agreement on the coarse variables because "the
    models agree" is itself the key long-range signal. Restricted to
    :data:`_LONGRANGE_DIVERGENCE_VARS` to keep the focus broad.
    """
    # Agreement LEVEL only (good/moderate/poor) — the numeric spread is dropped
    # to keep the long-range context free of raw figures, consistent with the
    # prompt's no-numbers rule.
    parts = [
        f"{d.variable} {d.agreement.value}"
        for d in divergences
        if d.variable in _LONGRANGE_DIVERGENCE_VARS
    ]
    if not parts:
        return []
    return [f"  Model agreement: {'; '.join(parts)}"]


def _format_dwd_translated_context(
    translated: list[tuple[DWDDayBlock, str]],
    *,
    synoptic_extract: bool = False,
) -> str:
    """Format translated DWD text with geographic framing for LLM context."""
    if not synoptic_extract:
        lines: list[str] = [
            "=== TEXT FORECASTS (DWD Synoptic Overview, translated from German) ===",
            "SOURCE: DWD (Deutscher Wetterdienst) — covers Germany and Central Europe.",
            "NOTE: Your route partially crosses Germany — this forecast "
            "is directly relevant for those segments.",
        ]
    else:
        lines = [
            "=== TEXT FORECASTS (DWD Synoptic Overview, large-scale extract) ===",
            "SOURCE: DWD (Deutscher Wetterdienst) — extracted large-scale synoptic "
            "features only. German regional details have been removed.",
            "NOTE: Your route does not cross Germany. The text below contains only "
            "named pressure systems, frontal positions with coordinates and timing, "
            "air mass types, and large-scale flow patterns. Use these as synoptic "
            "context — do NOT extrapolate or reposition features to your route area.",
        ]

    for block, english in translated:
        date_str = block.date_iso.isoformat() if block.date_iso else "?"
        label = f"{block.day_name_de} ({date_str})"
        source_tag = "short-range" if block.source == "kurzfrist" else "medium-range"
        lines.append(f"\n--- {label}, {source_tag} ---\n{english}")

    return "\n".join(lines)


def _format_divergence_context(divergences: list[ModelDivergence]) -> list[str]:
    """Format model divergence as compact lines — only moderate/poor agreement."""
    noteworthy = [
        d for d in divergences if d.agreement != AgreementLevel.GOOD
    ]
    if not noteworthy:
        return []

    parts = []
    for d in noteworthy:
        parts.append(f"{d.variable} {d.agreement.value}(spread={d.spread:.1f})")
    return [f"  Divergence: {'; '.join(parts)}"]


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
            if idx.nwp_cin_jkg is not None:
                parts.append(f"NWP-CIN={idx.nwp_cin_jkg:.0f}J/kg")
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

        # Cloud, split by provenance so the LLM (and the MCP client reading this
        # via get_digest_context) can tell the model's *native* cloud envelope
        # from the coarse bulk summary:
        #
        # 1. `nwp_cloud_layers` present → the model's own cloud decks (ECMWF/ICON
        #    3-D cloud fraction or GFS GRIB) — the same signal the app
        #    cross-section's NWP cloud layer draws. Emitted as "NWP cloud".
        # 2. `nwp_cloud_layers is None` → no native envelope. This happens two
        #    ways and both fall through here: ECMWF far-out (no GRIB enrichment
        #    yet) AND Open-Meteo-only models (UKMO/MétéoFrance/GEM) that never
        #    have native diagnostics at any lead time. Fall back to the bulk
        #    Open-Meteo low/mid/high summary — real data, just coarse — under a
        #    label that does NOT claim to be the native NWP layer and does NOT
        #    imply a temporal gap. (The old code mislabeled this bulk triple as
        #    "NWP cloud [ecmwf]: Low/Mid/High", the GFS-native paradigm applied
        #    to a model whose native cloud is per-level 3-D fraction.)
        # 3. `nwp_cloud_layers == []` → a native source exists and reports clear.
        if sa.nwp_cloud_layers is None:
            if sa.cloud_cover_low_pct is not None:
                lines.append(
                    f"  Bulk cloud [{model}] (Open-Meteo summary, no native NWP layer): "
                    f"Low={sa.cloud_cover_low_pct:.0f}%"
                    f", Mid={sa.cloud_cover_mid_pct:.0f}%"
                    f", High={sa.cloud_cover_high_pct:.0f}%"
                )
            else:
                lines.append(f"  NWP cloud [{model}]: no native NWP cloud layer")
        elif not sa.nwp_cloud_layers:
            lines.append(f"  NWP cloud [{model}]: none (model clear)")
        else:
            for cl in sa.nwp_cloud_layers:
                cc_str = (
                    f" CC={cl.mean_cloud_cover_pct:.0f}%"
                    if cl.mean_cloud_cover_pct is not None else ""
                )
                t_str = (
                    f" T={cl.mean_temperature_c:.0f}C"
                    if cl.mean_temperature_c is not None else ""
                )
                lines.append(
                    f"  NWP cloud [{model}]: {cl.coverage.value.upper()} "
                    f"{cl.base_ft:.0f}-{cl.top_ft:.0f}ft{cc_str}{t_str}"
                )

        if sa.convective and sa.convective.risk_level != ConvectiveRisk.NONE:
            conv = sa.convective
            regime = f" [{conv.regime.label}]" if conv.regime else ""
            lines.append(f"  Convective [{model}]: {conv.risk_level.value}{regime}")
            for drv in conv.drivers:
                lines.append(f"    + {drv}")
            for sup in conv.suppressors:
                lines.append(f"    - {sup}")
            for mod in conv.severe_modifiers:
                lines.append(f"    ! {mod}")

        # Model convective-scheme diagnostic — the independent vote on whether
        # convection fires (cover/geometry), distinct from the CAPE-derived risk.
        nwp_conv = sa.convective_nwp
        if nwp_conv is not None:
            mparts: list[str] = []
            if nwp_conv.cover_pct is not None:
                mparts.append(f"cover={nwp_conv.cover_pct:.0f}%")
            if nwp_conv.base_ft is not None and nwp_conv.top_ft is not None:
                mparts.append(f"base={nwp_conv.base_ft:.0f}ft, top={nwp_conv.top_ft:.0f}ft")
            elif nwp_conv.top_ft is not None:
                mparts.append(f"top={nwp_conv.top_ft:.0f}ft")
            if mparts:
                lines.append(
                    f"  Convective model scheme [{model}] ({nwp_conv.method}): "
                    f"{', '.join(mparts)}"
                )

        # DD-vs-model cross-check — emitted even when the thermo risk is NONE so
        # the "model active where DD shows none" direction reaches the LLM.
        xc = convective_cross_check(sa.convective_thermo or sa.convective, sa.convective_nwp)
        if xc is not None:
            lines.append(f"    → cross-check: {xc.note}")

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


def _hhmmz(dt: datetime) -> str:
    """Format an aware/naive datetime as a compact UTC 'HHMMZ' token."""
    d = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return d.strftime("%H%MZ")


def _sigmet_flight_activity(
    s: SigmetAlongRoute,
    departure: datetime | None,
    arrival: datetime | None,
) -> str | None:
    """Explicit active/inactive tag for a SIGMET vs the actual flight window.

    SIGMETs are collected from a wider window (now → end of the departure day),
    so one can intersect the route geographically yet be temporally *inactive*
    during the flight — the flight may depart after it expires, or land before
    it begins. We compute that relationship here and state it plainly rather
    than leaving the LLM to parse validity out of the raw SIGMET text (which
    misled a real briefing: an 0605Z–0800Z SIGMET was narrated as "active" for
    a 1030Z departure).
    """
    if departure is None or (s.valid_from is None and s.valid_to is None):
        return None
    dep = departure
    arr = arrival or departure
    # Overlap test: inactive only if the SIGMET wholly precedes or follows the
    # flight window. Open-ended bounds are treated permissively (still active).
    if s.valid_to is not None and s.valid_to < dep:
        return (
            f"INACTIVE during flight — expired {_hhmmz(s.valid_to)}, "
            f"before {_hhmmz(dep)} departure"
        )
    if s.valid_from is not None and s.valid_from > arr:
        return (
            f"INACTIVE during flight — begins {_hhmmz(s.valid_from)}, "
            f"after {_hhmmz(arr)} arrival"
        )
    win = ""
    if s.valid_from is not None and s.valid_to is not None:
        win = f" ({_hhmmz(s.valid_from)}–{_hhmmz(s.valid_to)})"
    return f"ACTIVE during flight window{win}"


def _format_sigmets_context(
    sig: RouteSigmets,
    departure: datetime | None = None,
    arrival: datetime | None = None,
) -> str:
    """Format route SIGMETs into a compact LLM context section."""
    lines: list[str] = ["=== SIGMETs ALONG ROUTE ==="]
    lines.append(
        f"Corridor: {sig.corridor_nm:.0f}nm | {sig.count} SIGMET(s) intersecting route"
    )
    if departure is not None:
        arr = arrival or departure
        lines.append(
            f"Flight window: {_hhmmz(departure)}–{_hhmmz(arr)}. SIGMETs are pulled "
            "from a wider window, so some below may be INACTIVE during the flight — "
            "treat an INACTIVE tag as authoritative and do NOT describe it as active."
        )
    if sig.hazards:
        lines.append(f"Hazards: {', '.join(sig.hazards)}")
    if sig.has_severe:
        lines.append("** SEVERE (SEV) SIGMET in effect along the route **")

    for s in sig.sigmets:
        head = " ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET"
        band = f"{format_flight_level(s.base_ft)}-{format_flight_level(s.top_ft)}" if (s.base_ft is not None or s.top_ft is not None) else ""
        parts = [f"{head} ({s.fir_id})"]
        if band:
            parts.append(band)
        if s.enroute_distance_from_nm is not None and s.enroute_distance_to_nm is not None:
            parts.append(f"enroute {s.enroute_distance_from_nm:.0f}-{s.enroute_distance_to_nm:.0f}nm")
        if s.direction and s.speed_kt:
            parts.append(f"moving {s.direction} {s.speed_kt}kt")
        lines.append("  " + ", ".join(parts))
        activity = _sigmet_flight_activity(s, departure, arrival)
        if activity:
            lines.append(f"    ** {activity} **")
        if s.raw_text:
            lines.append(f"    {s.raw_text}")

    return "\n".join(lines)


def _format_alternates_context(alt: RouteAlternates) -> str:
    """Format weather alternates into a compact LLM context section.

    Leads with the nearest-improving pick per deficient axis (the actionable
    "where to go instead") so the LLM can mention it in prose, then a short
    ranked list. Advisory-grade: approach presence is a minima proxy, not a
    guarantee.
    """
    lines: list[str] = [
        "=== WEATHER ALTERNATES ===",
        "(Weather-driven divert candidates that fix the destination's weather "
        "problem — call these \"weather alternates\", NOT operational alternates: "
        "no fuel, minima, NOTAM, customs or PPR has been checked.)",
    ]
    dest_xw = (
        f", {alt.destination_crosswind_kt:.0f}kt crosswind"
        if alt.destination_crosswind_kt is not None else ""
    )
    approach_note = ", approach data unavailable" if alt.approach_filter_relaxed else ""
    lines.append(
        f"Destination {alt.destination_icao}: {alt.destination_category}{dest_xw} "
        f"({alt.candidates_evaluated} candidates evaluated{approach_note})"
    )

    improving = [p for p in alt.nearest_improving if p.icao]
    if improving:
        lines.append("Nearest improving alternate per axis:")
        for p in improving:
            label = ALT_AXIS_LABELS.get(p.axis, p.axis)
            pos = f" ({p.position})" if p.position else ""
            dist = f" {p.distance_from_dest_nm:.0f}nm from dest" if p.distance_from_dest_nm is not None else ""
            lines.append(f"  {label}: {p.icao}{dist}{pos}")
    else:
        lines.append("No alternate improves on the destination across the evaluated candidates.")

    # Short ranked list (closest-first); cap to keep the prompt compact.
    for a in alt.alternates[:8]:
        bits = [f"{a.distance_from_dest_nm:.0f}nm", a.position, a.flight_category]
        if a.wind_speed_kt is not None:
            bits.append(f"wind {a.wind_speed_kt:.0f}kt")
        if a.crosswind_kt is not None:
            bits.append(f"xwind {a.crosswind_kt:.0f}kt")
        if a.has_instrument_approach:
            bits.append(a.best_approach_type or "approach")
        if a.dominates_destination:
            bits.append("dominates dest")
        lines.append(f"  {a.icao}: " + ", ".join(str(b) for b in bits if b))

    return "\n".join(lines)


def _format_altitude_options_context(table: AltitudeTableResult) -> str | None:
    """Build the deterministic Altitude sub-block of OPTIONS TO IMPROVE.

    Compares the planned-cruise row against the best lower/higher options using
    the shared altitude-diff primitive, so the LLM only *phrases* the trade-off
    — it never invents which advisory improves or worsens. The altitude axis
    OWNS the altitude decision: it is the only view that shows the cross-advisory
    trade (improves X, worsens Y), so per-advisory ALTITUDE mitigations are
    dropped from the digest (see ``_format_tactical_mitigations_context``).
    Returns ``None`` when the table has no usable planned row (e.g. degenerate
    single-altitude sweep).
    """
    planned = row_for_altitude(table, table.cruise_altitude_ft)
    if planned is None or not planned.statuses:
        return None

    def _status_summary(row: AltitudeAdvisoryRow) -> str:
        parts = [
            f"{table.advisory_names.get(aid, aid)}={status.value.upper()}"
            for aid, status in sorted(row.statuses.items())
            if status.value != "unavailable"
        ]
        return ", ".join(parts) if parts else "no altitude-dependent advisories"

    def _change_phrase(changes: list[AltitudeAdvisoryChange]) -> str:
        return "; ".join(
            f"{c.name} ({c.from_status.value.upper()}→{c.to_status.value.upper()})"
            for c in changes
        )

    def _option_line(label: str, alt_ft: int | None) -> str:
        if alt_ft is None:
            return f"  {label}: none available."
        if alt_ft == table.cruise_altitude_ft:
            return f"  {label}: the planned altitude is already best."
        cand = row_for_altitude(table, alt_ft)
        if cand is None:
            return f"  {label}: none available."
        delta = diff_altitude_rows(planned, cand, table.advisory_names)
        if delta.is_empty:
            return f"  {label} {alt_ft:,} ft: same advisory picture as planned."
        improves = _change_phrase(delta.improved) if delta.improved else "nothing"
        worsens = _change_phrase(delta.worsened) if delta.worsened else "nothing"
        return f"  {label} {alt_ft:,} ft: improves {improves}; worsens {worsens}."

    lines = [
        "Altitude (one choice, affects all altitude-dependent advisories):",
        f"  Planned {table.cruise_altitude_ft:,} ft: {_status_summary(planned)}.",
        _option_line("Lower option", table.best_below_cruise),
    ]
    # best_above_cruise is the best at/above cruise; the picker can return cruise
    # itself (predicate is altitude_ft >= cruise), so distinguish "no higher row"
    # / "cruise is already best" from a genuinely higher, better option.
    if table.best_above_cruise is None or table.best_above_cruise == table.cruise_altitude_ft:
        lines.append("  Higher option: planned altitude is already best at/above cruise.")
    else:
        lines.append(_option_line("Higher option", table.best_above_cruise))
    return "\n".join(lines)


def _format_tactical_mitigations_context(
    manifest: RouteAdvisoriesManifest,
) -> str | None:
    """Build the Tactical (non-altitude) sub-block of OPTIONS TO IMPROVE.

    Each advisory's ``aggregate_mitigations`` filtered to ROUTE_POSITION /
    TIMING (``kind != ALTITUDE``), grouped by advisory name, using the already
    localized ``mitigation.detail``. Per-advisory ALTITUDE mitigations are
    intentionally excluded — the Altitude sub-block owns that axis and shows the
    worsens-Y trade-off a single-advisory altitude mitigation would hide.
    Returns ``None`` when no advisory has a non-altitude mitigation.
    """
    name_map = {entry.id: entry.name for entry in manifest.catalog}
    lines: list[str] = []
    for result in manifest.advisories:
        if result.advisory_id in _DIGEST_EXCLUDE_IDS:
            continue
        tactical = [
            m for m in result.aggregate_mitigations
            if m.kind != MitigationKind.ALTITUDE
        ]
        if not tactical:
            continue
        name = name_map.get(result.advisory_id, result.advisory_id)
        for m in tactical:
            lines.append(f"  {name}: {m.detail}")
    if not lines:
        return None
    return "\n".join(["Tactical (per-advisory, no altitude change):", *lines])


def _format_options_to_improve_context(
    altitude_table: AltitudeTableResult | None,
    route_advisories: RouteAdvisoriesManifest | None,
) -> str | None:
    """Consolidated OPTIONS TO IMPROVE block: altitude trade-off + tactical
    mitigations, computed deterministically (the LLM phrases a pre-typed
    structure; it is never asked to reconcile overlaps).

    Advice only — these are optional decisions that would improve a specific
    sub-issue; they never change the assessment. Returns ``None`` when neither
    sub-part has content.
    """
    sub_blocks: list[str] = []
    if altitude_table is not None:
        alt = _format_altitude_options_context(altitude_table)
        if alt:
            sub_blocks.append(alt)
    if route_advisories is not None:
        tactical = _format_tactical_mitigations_context(route_advisories)
        if tactical:
            sub_blocks.append(tactical)
    if not sub_blocks:
        return None
    header = "=== OPTIONS TO IMPROVE (advice only — do NOT change the assessment) ==="
    return "\n".join([header, *sub_blocks])


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

        # Per-model breakdown — only show when models disagree
        statuses = {m.status.value.upper() for m in result.per_model}
        if len(statuses) > 1:
            # Find outlier models (those differing from aggregate)
            outliers = [
                m for m in result.per_model
                if m.status.value.upper() != status_tag
            ]
            if outliers:
                parts = []
                for m in outliers:
                    m_status = m.status.value.upper()
                    if m.affected_pct > 0:
                        parts.append(f"{m.model} sees {m_status} ({m.affected_pct:.0f}% affected)")
                    else:
                        parts.append(f"{m.model} sees {m_status}")
                lines.append(f"  (outlier: {'; '.join(parts)})")

    if len(lines) == 1:
        lines.append("No route advisories available.")

    return "\n".join(lines)
