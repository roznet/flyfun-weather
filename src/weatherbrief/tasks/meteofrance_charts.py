"""Météo-France TEMSI chart pipeline task.

Sibling of :mod:`weatherbrief.tasks.dwd_charts` and
:mod:`weatherbrief.tasks.metoffice_charts`, with two differences that follow
from what AEROWEB actually publishes.

**Eligibility is a licence question, not just a coverage one.** The other two
sources are gated on "is this route in Europe". Météo-France charts may be
redistributed only to users operating in French airspace, so the gate is
:func:`~weatherbrief.fetch.meteofrance_charts.route_licence_allows` and it
fails closed. A route that never touches France gets no chart even though the
bytes are sitting in the cache.

**Selection is per zone, not per offset.** DWD and Met Office pick a forecast
offset inside one run. AEROWEB has no run/offset split — it publishes absolute
validities — so this picks the *validity* nearest the ETD, separately for each
zone, because the zones do not publish in lockstep.

Bytes are NOT stored on the pack: the briefing records only the chosen
validity per zone plus the eligibility flags, and the renderer reads bytes
from ``DATA_DIR/meteofrance_charts/<validity>/`` at request time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from weatherbrief.fetch.meteofrance_charts import (
    CHART_IDS,
    enabled,
    refresh_charts,
    route_licence_allows,
    select_cycle_for_time,
)
from weatherbrief.models import RouteConfig

logger = logging.getLogger(__name__)


@dataclass
class MeteofranceChartsResult:
    """Per-briefing references that flow onto BriefingPackMeta."""

    zone_cycles: dict[str, str] = field(default_factory=dict)
    in_coverage: bool = False
    within_horizon: bool = False


def run_meteofrance_charts(
    *,
    route: RouteConfig,
    departure_time: datetime,
    data_dir: Path,
) -> MeteofranceChartsResult:
    """Refresh the TEMSI cache and pick the validity nearest the ETD per zone.

    Returns an empty result — which renders as "unavailable" — when the source
    isn't configured, the route falls outside the licence, the refresh fails,
    or no cached validity is near enough to the ETD.

    That last case is normal rather than exceptional: TEMSI runs ~3h ahead, so
    any briefing built more than a few hours before departure will legitimately
    have no chart. It is reported as ``within_horizon=False`` (distinct from
    ``in_coverage=False``) so the UI can say "no TEMSI for this time" rather
    than implying the route is ineligible.
    """
    if not enabled():
        logger.debug("Météo-France charts not configured; skipping")
        return MeteofranceChartsResult()

    if not route_licence_allows(route):
        logger.debug("Route outside French airspace; Météo-France charts withheld")
        return MeteofranceChartsResult(in_coverage=False)

    try:
        report = refresh_charts(data_dir)
    except Exception:
        logger.warning("Météo-France chart refresh raised", exc_info=True)
        return MeteofranceChartsResult(in_coverage=True)

    if report.error:
        logger.info("Météo-France chart refresh failed: %s", report.error)
        return MeteofranceChartsResult(in_coverage=True)

    zone_cycles: dict[str, str] = {}
    for zone in CHART_IDS:
        cycle = select_cycle_for_time(data_dir, departure_time, chart_id=zone)
        if cycle is not None:
            zone_cycles[zone] = cycle

    if report.charts_failed:
        logger.info(
            "Météo-France chart refresh: %d fetched, %d unchanged, failed: %s",
            len(report.charts_refreshed), len(report.charts_unchanged),
            report.charts_failed,
        )

    return MeteofranceChartsResult(
        zone_cycles=zone_cycles,
        in_coverage=True,
        within_horizon=bool(zone_cycles),
    )
