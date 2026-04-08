"""Unified forward-fill for GRIB-enriched fields across time.

GRIB enrichment targets native model forecast hours only (e.g. every 3h for
GFS at longer lead times).  Open-Meteo provides interpolated hourly data
between those native steps, but the GRIB-specific fields remain None on
non-native hours.

This module forward-fills all GRIB-enriched fields from the preceding native
hour to close those gaps.  Cloud layer geometry and microphysics change slowly
between 3-hour GFS steps, so the preceding step's values are a reasonable
approximation.

Interpolation rules (see also spatial_interpolation.py for the spatial axis):

    Time axis  — forward-fill from preceding native GRIB hour (this module)
    Spatial axis — linear interpolation between neighboring route points
    Vertical axis — linear in pressure, handled in sounding analysis

When adding new GRIB-enriched fields, add a forward-fill call in
``propagate_all`` and a spatial interpolation function in
``spatial_interpolation.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weatherbrief.models import (
        HourlyForecast,
        NWPCloudDiagnostics,
        RouteCrossSection,
        WaypointForecast,
    )

logger = logging.getLogger(__name__)


def propagate_all(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Forward-fill all GRIB-enriched fields from native hours to interpolated hours.

    Called once after all GRIB enrichment (GFS + ICON-EU + ECMWF) completes,
    before the analysis stage.
    """
    _forward_fill_cloud_diagnostics(sections, all_forecasts)
    _forward_fill_pressure_levels(sections, all_forecasts)
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
# Full pressure-level list forward-fill (ICON-EU / ECMWF sounding replacement)
# ---------------------------------------------------------------------------

def _forward_fill_pressure_levels(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Forward-fill GRIB sounding across 3-hourly gaps within the flight window.

    Beyond 78h lead time, ICON-EU and GFS produce 3-hourly native steps.
    Within the flight window, GRIB replacement covers native steps but leaves
    intermediate hours with Open-Meteo data at lower vertical resolution.
    This fills those short gaps (up to 3h) so the flight window has consistent
    high-resolution levels.

    Hours outside the flight window keep Open-Meteo data — it's the correct
    forecast for those hours, just at fewer levels.
    """
    total = 0
    for cs in sections:
        for wf in cs.point_forecasts:
            total += _fill_levels_hourly(wf.hourly)
    for wf in all_forecasts:
        total += _fill_levels_hourly(wf.hourly)

    if total:
        logger.info(
            "Full pressure levels propagated to %d hourly entries (3h gap fill)",
            total,
        )


def _fill_levels_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Forward-fill pressure_levels only across short gaps between GRIB hours.

    Only fills hours that sit between two GRIB-replaced hours (i.e. within
    the flight window's 3-hourly gaps).  Does not propagate beyond the last
    GRIB hour or before the first — those hours keep Open-Meteo data.
    """
    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    # Determine the baseline level count (Open-Meteo).
    level_counts = [len(h.pressure_levels) for h in sorted_hours]
    baseline = min(level_counts)
    max_levels = max(level_counts)
    if max_levels <= baseline:
        return 0

    is_grib = [len(h.pressure_levels) > baseline for h in sorted_hours]

    # Find the first and last GRIB-replaced indices
    first_grib = next((i for i, g in enumerate(is_grib) if g), None)
    last_grib = next((i for i in range(len(is_grib) - 1, -1, -1) if is_grib[i]), None)
    if first_grib is None or last_grib is None or first_grib == last_grib:
        return 0

    # Only fill gaps BETWEEN GRIB hours (within the flight window)
    filled = 0
    last_grib_levels = None
    for i in range(first_grib, last_grib + 1):
        if is_grib[i]:
            last_grib_levels = sorted_hours[i].pressure_levels
        elif last_grib_levels is not None:
            sorted_hours[i].pressure_levels = last_grib_levels
            filled += 1

    return filled
