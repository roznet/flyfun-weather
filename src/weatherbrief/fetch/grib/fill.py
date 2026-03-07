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

    Called once after all GRIB enrichment (GFS + ICON-EU) completes,
    before the analysis stage.
    """
    _forward_fill_cloud_diagnostics(sections, all_forecasts)
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
