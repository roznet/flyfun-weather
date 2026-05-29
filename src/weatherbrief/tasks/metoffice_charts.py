"""Met Office surface-pressure chart pipeline task.

Sibling of :mod:`weatherbrief.tasks.dwd_charts`. Decides whether the
briefing's flight is eligible for the Met Office chart source, refreshes the
shared cache (cheap thanks to conditional GETs), and computes the
per-briefing reference fields.

Eligibility:
  - in_coverage: route region is Europe (reuses ``detect_region``).
  - within_horizon: ETD <= run_cycle + 84h at refresh time.

Bytes are NOT stored on the pack — the briefing only stores
(run_cycle, default_chart_id, eligibility flags). The renderer reads the
bytes from ``DATA_DIR/metoffice_charts/<run_cycle>/`` at request time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from weatherbrief.fetch.metoffice_charts import (
    FORECAST_OFFSETS_H,
    parse_run_cycle_dt,
    refresh_charts,
    select_default_chart_id,
)
from weatherbrief.fetch.text_forecasts import ForecastRegion, detect_region
from weatherbrief.models import RouteConfig

logger = logging.getLogger(__name__)


@dataclass
class MetofficeChartsResult:
    """Per-briefing references that flow onto BriefingPackMeta."""

    run_cycle: str | None = None
    default_chart_id: str | None = None
    in_coverage: bool = False
    within_horizon: bool = False


def run_metoffice_charts(
    *,
    route: RouteConfig,
    departure_time: datetime,
    data_dir: Path,
    pack_dir: Path | None = None,
) -> MetofficeChartsResult:
    """Refresh the Met Office chart cache and compute briefing references.

    Cheap when the run hasn't rolled — the index call plus conditional GETs
    that all 304. Refresh failures leave ``run_cycle=None`` so the section
    renders "unavailable" gracefully.
    """
    in_coverage = detect_region(route) == ForecastRegion.EUROPE
    if not in_coverage:
        return MetofficeChartsResult(in_coverage=False)

    try:
        report = refresh_charts(data_dir)
    except Exception:
        logger.warning("Met Office chart refresh raised", exc_info=True)
        return MetofficeChartsResult(in_coverage=True, within_horizon=False)

    if report.run_cycle is None:
        logger.info("Met Office chart refresh failed: %s", report.error)
        return MetofficeChartsResult(in_coverage=True, within_horizon=False)

    issued = parse_run_cycle_dt(report.run_cycle)
    if issued is None:
        return MetofficeChartsResult(in_coverage=True)

    horizon_h = max(FORECAST_OFFSETS_H.values())  # 84
    within_horizon = departure_time <= issued + timedelta(hours=horizon_h)
    if not within_horizon:
        return MetofficeChartsResult(
            run_cycle=report.run_cycle,
            default_chart_id=None,
            in_coverage=True,
            within_horizon=False,
        )

    default_id = select_default_chart_id(departure_time, report.run_cycle)

    if report.charts_failed:
        logger.info(
            "Met Office chart refresh: %d refreshed, %d unchanged, failed: %s",
            len(report.charts_refreshed),
            len(report.charts_unchanged),
            report.charts_failed,
        )

    return MetofficeChartsResult(
        run_cycle=report.run_cycle,
        default_chart_id=default_id,
        in_coverage=True,
        within_horizon=True,
    )
