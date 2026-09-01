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

**The picker is (zone x validity), not forecast offsets.** DWD and Met Office
offer one run's offsets — "+36h", "+48h". AEROWEB has no run/offset split, so
the options here are pairs: "France 15Z", "France 18Z", "EUROC 18Z". They are
listed as ``(zone, validity)`` and identified by ``zone|validity``.

Bytes are NOT stored on the pack: the briefing records the offered options,
which one to open on, and the eligibility flags; the renderer reads bytes from
``DATA_DIR/meteofrance_charts/<validity>/`` at request time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from weatherbrief.fetch.meteofrance_charts import (
    enabled,
    list_options_for_time,
    refresh_charts,
    route_licence_allows,
)
from weatherbrief.models import RouteConfig

logger = logging.getLogger(__name__)


def option_id(zone: str, run_cycle: str) -> str:
    """Stable id for one picker entry, e.g. ``"france|2026-08-31T15Z"``."""
    return f"{zone}|{run_cycle}"


@dataclass
class MeteofranceChartsResult:
    """Per-briefing references that flow onto BriefingPackMeta."""

    #: Picker entries, nearest-to-ETD first: ``[{"zone", "run_cycle"}, ...]``.
    options: list[dict[str, str]] = field(default_factory=list)
    #: Which entry to open on, as ``zone|run_cycle``.
    default_id: str | None = None
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

    That last case is normal rather than exceptional: AEROWEB offers barely two
    validities ahead of now, so any briefing built more than ~6h before
    departure will legitimately have no chart. It is reported as
    ``within_horizon=False`` (distinct from ``in_coverage=False``) so the UI can
    say "no TEMSI for this time" rather than implying the route is ineligible.
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

    # One window (MAX_VALIDITY_GAP, 6h): every cached validity near the ETD is
    # offered, and having any at all is what makes the section appear. There
    # used to be a second, tighter test for "does a chart represent the ETD" —
    # dropped, because AEROWEB publishes barely two validities ahead, so it hid
    # the section for any flight more than ~90 min out. A chart that is merely
    # early is still worth reading; the UI states each one's offset from
    # departure rather than us withholding it.
    options = [
        {"zone": z, "run_cycle": rc}
        for z, rc in list_options_for_time(data_dir, departure_time)
    ]

    if report.charts_failed:
        logger.info(
            "Météo-France chart refresh: %d fetched, %d unchanged, failed: %s",
            len(report.charts_refreshed), len(report.charts_unchanged),
            report.charts_failed,
        )

    return MeteofranceChartsResult(
        options=options,
        default_id=option_id(options[0]["zone"], options[0]["run_cycle"]) if options else None,
        in_coverage=True,
        within_horizon=bool(options),
    )
