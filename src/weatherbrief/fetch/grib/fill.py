"""Time-axis interpolation of GRIB-enriched fields.

GRIB enrichment writes data at native model forecast steps. Beyond ~90h lead
time, ECMWF / ICON-EU step out to 3-hourly cadence (then 6-hourly past 144h).
Open-Meteo provides hourly data interpolated from its own pipeline, but mixing
GRIB-anchor hours with OM-interpolated gap hours produces an inconsistent
single-source story.

Interpolation policy:

  - **Surface scalars** (HourlyForecast surface fields, ECMWF GRIB-anchored):
    linear in time between bracketing GRIB-anchor hours. Wind direction uses
    shortest-arc circular interpolation, with speed-gating below ~1 kt.
  - **Pressure-level soundings** (ECMWF / ICON-EU GRIB replacement): linear
    in time per field per level; dewpoint is **derived** from interpolated
    (T, RH) via the Magnus formula rather than interpolated directly. This
    matches operational practice (ECMWF MARS, WRF post-processing) and keeps
    derived quantities consistent with primitives.
  - **Cloud diagnostics** (NWPCloudDiagnostics): forward-fill (persistence) —
    cloud-layer geometry is categorical and slowly varying; linear interp of
    base/top altitudes would be misleading mid-window.
  - **GFS CLW/ICMR overlays** (CLW/ICMR added onto OM pressure_levels without
    list replacement): forward-fill, since GFS doesn't replace the level set.

Interpolation rules (see also spatial_interpolation.py for the spatial axis):

    Time axis  — linear between bracketing native GRIB hours (this module)
    Spatial axis — linear between neighboring route points
    Vertical axis — linear in pressure, handled in sounding analysis
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from weatherbrief.fetch.open_meteo import magnus_dewpoint

if TYPE_CHECKING:
    from weatherbrief.models import (
        HourlyForecast,
        NWPCloudDiagnostics,
        PressureLevelData,
        RouteCrossSection,
        WaypointForecast,
    )

logger = logging.getLogger(__name__)


def propagate_all(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Apply all time-axis fills: linear interp where appropriate, forward-fill
    where persistence is the right semantic.

    Called once after all GRIB enrichment (GFS + ICON-EU + ECMWF) completes,
    before the analysis stage.
    """
    # ECMWF surface linear interp must run BEFORE cloud-diag forward-fill: it
    # uses ``nwp_cloud_diagnostics is not None`` as the GRIB-anchor detector,
    # and cloud-diag fill propagates the diagnostics onto gap hours, making
    # every hour look like an anchor afterwards.
    _linear_interp_ecmwf_surface(sections, all_forecasts)
    _forward_fill_cloud_diagnostics(sections, all_forecasts)
    _linear_interp_pressure_levels(sections, all_forecasts)
    # GFS-only path: CLW/ICMR overlay onto OM pressure_levels (no list
    # replacement). For ECMWF/ICON the linear interp above already populates
    # CLW/ICMR within the rebuilt PressureLevelData, so this becomes a no-op.
    _forward_fill_cloud_water(sections, all_forecasts)


# ---------------------------------------------------------------------------
# Cloud diagnostics (NWPCloudDiagnostics on HourlyForecast)
# ---------------------------------------------------------------------------

def _forward_fill_cloud_diagnostics(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Forward-fill ``nwp_cloud_diagnostics`` from native GRIB hours.

    Open-Meteo provides hourly data interpolated between native GFS steps (3h
    at longer lead times).  GRIB enrichment only targets native steps, leaving
    interpolated hours without diagnostics.  Without diagnostics the icing
    fallback applies the bulk NWP cloud percentage across the full altitude
    band, causing false positives.

    This fills each gap by copying diagnostics from the preceding enriched
    hour.  Cloud layer geometry (base/top) changes slowly between 3-hour GFS
    steps, so the earlier step's diagnostics are a reasonable approximation.
    """
    total = 0
    for cs in sections:
        for wf in cs.point_forecasts:
            total += _fill_diag_hourly(wf.hourly)
    for wf in all_forecasts:
        total += _fill_diag_hourly(wf.hourly)

    if total:
        logger.info(
            "Cloud diagnostics propagated to %d interpolated hourly entries",
            total,
        )


def _fill_diag_hourly(hourly_list: list[HourlyForecast]) -> int:
    filled = 0
    last_diag: NWPCloudDiagnostics | None = None
    for h in sorted(hourly_list, key=lambda h: h.time):
        if h.nwp_cloud_diagnostics is not None:
            last_diag = h.nwp_cloud_diagnostics
        elif last_diag is not None:
            h.nwp_cloud_diagnostics = last_diag
            filled += 1
    return filled


# ---------------------------------------------------------------------------
# Cloud water / ice mixing ratio (per-pressure-level on HourlyForecast)
# ---------------------------------------------------------------------------

def _forward_fill_cloud_water(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Forward-fill CLW and ICMR from native GRIB hours to interpolated hours.

    For each route point and each pressure level, walks hourly entries
    chronologically.  If CLW is None but a preceding native hour had a value,
    copies it forward.  Same for ICMR independently.

    Microphysics values change slowly between GFS 3-hour steps, so this is
    a reasonable approximation and prevents SFIP from falling back to the
    less accurate "proxy" variant on interpolated hours.
    """
    total = 0
    for cs in sections:
        for wf in cs.point_forecasts:
            total += _fill_clw_hourly(wf.hourly)
    for wf in all_forecasts:
        total += _fill_clw_hourly(wf.hourly)

    if total:
        logger.info(
            "Cloud water (CLW/ICMR) propagated to %d interpolated"
            " (hour, level) entries",
            total,
        )


def _fill_clw_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Forward-fill CLW/ICMR per pressure level across time."""
    filled = 0
    # Track last known values per pressure level: {hpa: (clw, icmr)}
    last: dict[int, tuple[float | None, float | None]] = {}

    for h in sorted(hourly_list, key=lambda h: h.time):
        for pl in h.pressure_levels:
            p = pl.pressure_hpa
            if pl.cloud_liquid_water_kg_kg is not None:
                # Anchor — record this native-hour value
                last[p] = (
                    pl.cloud_liquid_water_kg_kg,
                    pl.ice_mixing_ratio_kg_kg,
                )
            elif p in last:
                prev_clw, prev_icmr = last[p]
                if prev_clw is not None:
                    pl.cloud_liquid_water_kg_kg = prev_clw
                    if prev_icmr is not None and pl.ice_mixing_ratio_kg_kg is None:
                        pl.ice_mixing_ratio_kg_kg = prev_icmr
                    filled += 1
    return filled


# ---------------------------------------------------------------------------
# Linear interpolation helpers
# ---------------------------------------------------------------------------

# Threshold below which wind direction is unreliable. Used to gate circular
# interp at 10m where winds can drop to truly calm values; pressure-level
# winds are typically meaningful at any non-None speed.
_CALM_WIND_KT = 1.0


def _lerp(a: float | None, b: float | None, frac: float) -> float | None:
    """Linear interpolation; returns None if either endpoint is None."""
    if a is None or b is None:
        return None
    return a + (b - a) * frac


def _lerp_circ(a: float | None, b: float | None, frac: float) -> float | None:
    """Shortest-arc linear interpolation in degrees (0..360)."""
    if a is None or b is None:
        return None
    diff = ((b - a + 540) % 360) - 180
    return (a + diff * frac) % 360


# ---------------------------------------------------------------------------
# ECMWF surface scalars (HourlyForecast surface fields) — linear interp
# ---------------------------------------------------------------------------

# Instantaneous fields written by ``_apply_ecmwf_surface_to_hourly``.
# Precip/snow are *window-rate* — distributed at apply time across every hour
# in the differencing window, so they don't need temporal interpolation.
_ECMWF_SURFACE_INSTANT_FIELDS: tuple[str, ...] = (
    "temperature_2m_c",
    "dewpoint_2m_c",
    "wind_speed_10m_kt",
    "wind_gusts_10m_kt",
    "visibility_m",
    "cape_jkg",
    "surface_pressure_hpa",
)


def _linear_interp_ecmwf_surface(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Linearly interpolate ECMWF GRIB-derived surface scalars across intra-window gaps.

    GRIB delivers surface fields at native step times (1h cadence to 90h, then
    3h, then 6h past 144h). Open-Meteo provides hourly values from its own
    pipeline; mixing them with GRIB-anchor hours produces inconsistent sources.

    We use ``nwp_cloud_diagnostics is not None`` as the GRIB-anchor detector:
    surface scalars and cloud diagnostics are written together at the same
    ``valid_utc`` from the same a1 file in
    ``_apply_ecmwf_surface_to_hourly`` / ``_apply_cloud_diagnostics_to_sections``,
    so the two are coupled. Only ECMWF cross-sections / waypoint forecasts are
    touched.

    INVARIANT: every hour written by ``_apply_ecmwf_surface_to_hourly`` must
    also have ``nwp_cloud_diagnostics`` set. This holds because both writes
    run together inside the same ECMWF a1 loop iteration, and ECMWF a1
    always carries cloud-cover fields (cc / lcc / mcc / hcc / ceiling).
    If a future a1 schema ever drops cloud fields,
    ``build_ecmwf_cloud_diagnostics`` returns None and this anchor signal
    will silently miss those steps — surface scalars at those hours would
    then get overwritten by interpolation from neighbouring anchors. Keep
    the two writes coupled, or replace this detector with an explicit
    anchor list passed from the caller.

    Wind direction uses circular linear interpolation (shortest arc); when the
    interpolated speed is below ``_CALM_WIND_KT``, direction is copied from
    the nearest anchor since the interpolated direction would be meaningless.
    """
    total = 0
    for cs in sections:
        if cs.model.value != "ecmwf":
            continue
        for wf in cs.point_forecasts:
            total += _interp_surface_hourly(wf.hourly)
    for wf in all_forecasts:
        if wf.model.value != "ecmwf":
            continue
        total += _interp_surface_hourly(wf.hourly)

    if total:
        logger.info(
            "ECMWF surface scalars linearly interpolated for %d gap hourly entries",
            total,
        )


def _interp_surface_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Linearly interpolate surface scalars on gap hours between two GRIB anchors.

    Anchors: hours with ``nwp_cloud_diagnostics`` set. Only fills gaps strictly
    between the first and last anchor; hours outside that range keep their
    existing values (Open-Meteo, in practice).
    """
    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    is_anchor = [h.nwp_cloud_diagnostics is not None for h in sorted_hours]
    anchor_indices = [i for i, a in enumerate(is_anchor) if a]
    if len(anchor_indices) < 2:
        return 0

    filled = 0
    for k in range(len(anchor_indices) - 1):
        prev_i = anchor_indices[k]
        next_i = anchor_indices[k + 1]
        if next_i - prev_i <= 1:
            continue  # adjacent anchors, no gap
        prev_h = sorted_hours[prev_i]
        next_h = sorted_hours[next_i]
        span = (next_h.time - prev_h.time).total_seconds()
        if span <= 0:
            continue
        for i in range(prev_i + 1, next_i):
            h = sorted_hours[i]
            frac = (h.time - prev_h.time).total_seconds() / span
            for f in _ECMWF_SURFACE_INSTANT_FIELDS:
                v = _lerp(getattr(prev_h, f), getattr(next_h, f), frac)
                if v is not None:
                    setattr(h, f, v)
            # Wind direction: circular interp, gated by interpolated speed
            wd = _lerp_circ(prev_h.wind_direction_10m_deg, next_h.wind_direction_10m_deg, frac)
            ws = h.wind_speed_10m_kt
            if wd is not None:
                if ws is not None and ws < _CALM_WIND_KT:
                    # Calm: pick the closer anchor's direction (avoids a
                    # spurious mid-arc direction when both endpoints are calm).
                    nearer = prev_h if frac < 0.5 else next_h
                    h.wind_direction_10m_deg = nearer.wind_direction_10m_deg
                else:
                    h.wind_direction_10m_deg = wd
            filled += 1
    return filled


# ---------------------------------------------------------------------------
# Pressure-level sounding linear interp (ECMWF / ICON-EU GRIB replacement)
# ---------------------------------------------------------------------------

def _linear_interp_pressure_levels(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Linearly interpolate the GRIB-replaced sounding across 3-hourly gaps.

    Beyond 90h lead time, ECMWF / ICON-EU step at 3-hourly cadence; past 144h,
    6-hourly. Within the flight window, GRIB replacement covers native steps
    while gap hours retain Open-Meteo data at lower vertical resolution.

    We rebuild ``pressure_levels`` on each gap hour by per-level linear
    interpolation of T, RH, wind speed/direction, geopotential height, vertical
    velocity, CLW/ICMR, and cloud cover. Dewpoint is **derived** from the
    interpolated (T, RH) via the Magnus formula rather than interpolated
    directly — this matches operational meteorology practice and keeps
    derived quantities consistent with primitives.

    Hours outside the flight window (before the first or after the last GRIB
    anchor) keep their existing values.
    """
    total = 0
    for cs in sections:
        for wf in cs.point_forecasts:
            total += _interp_levels_hourly(wf.hourly)
    for wf in all_forecasts:
        total += _interp_levels_hourly(wf.hourly)

    if total:
        logger.info(
            "GRIB sounding linearly interpolated for %d gap hourly entries",
            total,
        )


def _interp_levels_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Linearly interpolate the full pressure_levels list on gap hours.

    Anchor detection mirrors the previous forward-fill: any hour whose
    pressure_levels count exceeds the baseline (Open-Meteo level count) is a
    GRIB-replaced anchor. Only fills gaps strictly between two anchors; hours
    outside the bracketing range are untouched.
    """
    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    level_counts = [len(h.pressure_levels) for h in sorted_hours]
    baseline = min(level_counts)
    max_levels = max(level_counts)
    if max_levels <= baseline:
        return 0

    is_anchor = [c > baseline for c in level_counts]
    anchor_indices = [i for i, a in enumerate(is_anchor) if a]
    if len(anchor_indices) < 2:
        return 0

    filled = 0
    for k in range(len(anchor_indices) - 1):
        prev_i = anchor_indices[k]
        next_i = anchor_indices[k + 1]
        if next_i - prev_i <= 1:
            continue
        prev_h = sorted_hours[prev_i]
        next_h = sorted_hours[next_i]
        span = (next_h.time - prev_h.time).total_seconds()
        if span <= 0:
            continue
        # Index next anchor's levels by pressure for matching
        next_by_p = {pl.pressure_hpa: pl for pl in next_h.pressure_levels}
        for i in range(prev_i + 1, next_i):
            h = sorted_hours[i]
            frac = (h.time - prev_h.time).total_seconds() / span
            new_levels = _interp_levels_at(prev_h.pressure_levels, next_by_p, frac)
            if new_levels:
                h.pressure_levels = new_levels
                filled += 1
    return filled


def _interp_levels_at(
    prev_levels: list[PressureLevelData],
    next_by_p: dict[int, PressureLevelData],
    frac: float,
) -> list[PressureLevelData]:
    """Build a new pressure_levels list by per-level linear interp at ``frac``."""
    from weatherbrief.models import PressureLevelData

    out: list[PressureLevelData] = []
    for prev in prev_levels:
        nxt = next_by_p.get(prev.pressure_hpa)
        if nxt is None:
            # Level present in prev anchor but not next — drop it on the gap
            # hour to keep the rebuilt level set self-consistent. Both
            # GRIB-replaced anchors normally carry the same level set; if
            # this branch fires repeatedly, anchor delivery has diverged
            # and is worth investigating.
            continue

        t_c = _lerp(prev.temperature_c, nxt.temperature_c, frac)
        rh = _lerp(prev.relative_humidity_pct, nxt.relative_humidity_pct, frac)
        # Dewpoint: derive from (T, RH) when both available; otherwise fall
        # back to direct linear interp of dewpoint.
        if t_c is not None and rh is not None:
            try:
                td_c = magnus_dewpoint(t_c, rh)
            except (ValueError, ZeroDivisionError):
                td_c = _lerp(prev.dewpoint_c, nxt.dewpoint_c, frac)
        else:
            td_c = _lerp(prev.dewpoint_c, nxt.dewpoint_c, frac)

        ws = _lerp(prev.wind_speed_kt, nxt.wind_speed_kt, frac)
        wd = _lerp_circ(prev.wind_direction_deg, nxt.wind_direction_deg, frac)

        out.append(PressureLevelData(
            pressure_hpa=prev.pressure_hpa,
            temperature_c=t_c,
            relative_humidity_pct=rh,
            dewpoint_c=td_c,
            wind_speed_kt=ws,
            wind_direction_deg=wd,
            geopotential_height_m=_lerp(prev.geopotential_height_m, nxt.geopotential_height_m, frac),
            vertical_velocity_pa_s=_lerp(prev.vertical_velocity_pa_s, nxt.vertical_velocity_pa_s, frac),
            cloud_liquid_water_kg_kg=_lerp(prev.cloud_liquid_water_kg_kg, nxt.cloud_liquid_water_kg_kg, frac),
            ice_mixing_ratio_kg_kg=_lerp(prev.ice_mixing_ratio_kg_kg, nxt.ice_mixing_ratio_kg_kg, frac),
            cloud_area_fraction_pct=_lerp(prev.cloud_area_fraction_pct, nxt.cloud_area_fraction_pct, frac),
            # ``clw_interpolated`` flags spatial fill, not temporal. Preserve
            # from prev so spatially-filled levels stay flagged across the
            # time-axis interp.
            clw_interpolated=prev.clw_interpolated or nxt.clw_interpolated,
        ))
    return out
