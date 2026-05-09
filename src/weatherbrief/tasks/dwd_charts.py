"""DWD Surface Analysis & Forecast pipeline task.

Decides whether the briefing's flight is eligible for the DWD chart
section, refreshes the shared cache (cheap if nothing changed thanks to
conditional GETs), and computes the per-briefing reference fields.

Eligibility:
  - in_coverage: route region is Europe (reuses ``detect_region``).
  - within_horizon: ETD <= run_cycle + 108h at refresh time.

Bytes are NOT stored on the pack — the briefing only stores
(run_cycle, default_chart_id, eligibility flags). The renderer reads
the bytes from ``DATA_DIR/dwd_charts/<run_cycle>/`` at request time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.fetch.dwd_charts import (
    FORECAST_OFFSETS_H,
    parse_run_cycle_dt,
    refresh_charts,
    select_default_chart_id,
)
from weatherbrief.fetch.text_forecasts import ForecastRegion, detect_region
from weatherbrief.models import RouteConfig

logger = logging.getLogger(__name__)


@dataclass
class DwdChartsResult:
    """Per-briefing references that flow onto BriefingPackMeta."""

    run_cycle: str | None = None
    default_chart_id: str | None = None
    in_coverage: bool = False
    within_horizon: bool = False


def run_dwd_charts(
    *,
    route: RouteConfig,
    departure_time: datetime,
    data_dir: Path,
    pack_dir: Path | None = None,
) -> DwdChartsResult:
    """Refresh the DWD chart cache and compute briefing references.

    Always cheap when the cycle hasn't rolled — conditional GETs return
    304 in ~300ms total across 6 parallel requests. The first refresh of
    a new cycle does 6 fresh downloads (~1s in parallel).

    Returns a result with all fields populated; refresh failures leave
    ``run_cycle=None`` and ``in_coverage=False`` so the section renders
    "unavailable" gracefully.
    """
    in_coverage = detect_region(route) == ForecastRegion.EUROPE
    if not in_coverage:
        return DwdChartsResult(in_coverage=False)

    try:
        report = refresh_charts(data_dir)
    except Exception:
        logger.warning("DWD chart refresh raised", exc_info=True)
        return DwdChartsResult(in_coverage=True, within_horizon=False)

    if report.run_cycle is None:
        # Refresh couldn't even establish a cycle (e.g. analysis chart
        # 5xx). Section renders unavailable but we know coverage was OK.
        logger.info("DWD chart refresh failed: %s", report.error)
        return DwdChartsResult(in_coverage=True, within_horizon=False)

    issued = parse_run_cycle_dt(report.run_cycle)
    if issued is None:
        # Defensive — refresh wouldn't have set a malformed cycle, but
        # a malformed run_cycle shouldn't crash the pipeline.
        return DwdChartsResult(in_coverage=True)

    horizon_h = max(FORECAST_OFFSETS_H.values())  # 108
    within_horizon = departure_time <= issued + timedelta(hours=horizon_h)
    if not within_horizon:
        # Still record the cycle for debugging, but the renderer will
        # show the "beyond +108h horizon" placeholder instead of charts.
        return DwdChartsResult(
            run_cycle=report.run_cycle,
            default_chart_id=None,
            in_coverage=True,
            within_horizon=False,
        )

    default_id = select_default_chart_id(departure_time, report.run_cycle)

    if report.charts_failed:
        logger.info(
            "DWD chart refresh: %d refreshed, %d unchanged, failed: %s",
            len(report.charts_refreshed),
            len(report.charts_unchanged),
            report.charts_failed,
        )

    return DwdChartsResult(
        run_cycle=report.run_cycle,
        default_chart_id=default_id,
        in_coverage=True,
        within_horizon=True,
    )
