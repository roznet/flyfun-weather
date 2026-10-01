"""Server live-window tick (#637, phase 2).

Every verification cycle (10 min), each flight inside its live window —
``departure - WB_LIVE_WINDOW_BEFORE_H`` (3 h) to ``departure + duration +
WB_LIVE_WINDOW_AFTER_H`` (1 h) — gets its live layer refreshed through the same
seam a D-0 ↻ press uses (:func:`run_realtime_refresh`), without that press.

**Fetch once.** The tick never calls aviationweather.gov per flight:

1. The verification collector's own METAR/TAF fetch reports every airport it
   fetched into :attr:`LiveTick.sink` (no change to what verification stores).
2. The tick works out which airports its live flights need — by running
   euro_aip's own corridor discovery against a recording source, so aliases
   and route airports are resolved exactly as on the network path — and
   fetches only those the verification pass did not cover, in one batched
   call (chunks of 400).
3. Every flight is then served from that shared cache; SIGMETs are fetched
   once per tick and shared the same way.

So a tick costs at most two METAR/TAF batches and one SIGMET call, whatever
the number of flights. Observed radar/lightning/tops are re-sampled from local
frames as usual (no network).

The tick never runs the model pipeline or an LLM and never changes a grade.
Disabled with ``DISABLE_LIVE_LAYER=1``; also off whenever the verification
loop is (``DISABLE_VERIFICATION=1``) since it rides that loop.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, FlightRow
from weatherbrief.tasks.route_weather import SigmetSourceUnavailable

logger = logging.getLogger(__name__)

_BATCH_SIZE = 400  # aviationweather.gov limit per request
# Planning (local corridor discovery + a briefing.json read) and the cheap
# refresh run per flight per tick. Fine for tens of flights; above this, warn
# so a growing user base shows up in the logs before it shows up as a slow
# verification cycle.
_SCALE_WARN_FLIGHTS = 50


def live_enabled() -> bool:
    return os.environ.get("DISABLE_LIVE_LAYER", "").strip().lower() not in ("1", "true", "yes")


def live_window_hours() -> tuple[float, float]:
    """(before departure, after arrival) in hours."""
    def _f(name: str, default: float) -> float:
        try:
            return max(0.0, float(os.environ.get(name, default)))
        except ValueError:
            return default

    return _f("WB_LIVE_WINDOW_BEFORE_H", 3.0), _f("WB_LIVE_WINDOW_AFTER_H", 1.0)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def in_live_window(
    departure: datetime, duration_h: float | None, now: datetime,
    before_h: float, after_h: float,
) -> bool:
    dep = _as_utc(departure)
    end = dep + timedelta(hours=duration_h or 0)
    return dep - timedelta(hours=before_h) <= now <= end + timedelta(hours=after_h)


def find_live_flights(db: Session, now: datetime | None = None) -> list[FlightRow]:
    """Flights with at least one pack whose live window contains ``now``."""
    now = now or datetime.now(timezone.utc)
    before_h, after_h = live_window_hours()
    stmt = (
        select(FlightRow)
        .join(BriefingPackRow)
        .where(FlightRow.departure_time <= now + timedelta(hours=before_h))
        .distinct()
    )
    return [
        row for row in db.execute(stmt).scalars().all()
        if row.departure_time is not None
        and in_live_window(row.departure_time, row.flight_duration_hours, now, before_h, after_h)
    ]


# --- Shared report sources ---------------------------------------------------


class _RecordingSource:
    """A euro_aip weather source that fetches nothing and records what was asked."""

    def __init__(self) -> None:
        self.requested: set[str] = set()

    def fetch_weather(self, icaos, metar_hours: float = 3):
        self.requested.update(i.upper() for i in icaos)
        return []


class UncoveredAirportsError(RuntimeError):
    """The shared fetch did not cover some airports (it failed for them)."""


class SharedReportSource:
    """Serves METAR/TAF reports from the tick's one shared fetch.

    Raises for airports the fetch did not cover. Returning ``[]`` would read
    as "no reports": the flight's METAR table would be blanked and the
    classifier fed empty data on a transient upstream failure. Raising makes
    the tick skip that flight, so its stored observations stay as they were.
    """

    def __init__(self, reports: dict[str, list], covered: set[str] | None = None) -> None:
        self._reports = reports
        self._covered = covered

    def fetch_weather(self, icaos, metar_hours: float = 3):
        if self._covered is not None:
            missing = sorted({i.upper() for i in icaos} - self._covered)
            if missing:
                raise UncoveredAirportsError(
                    f"{len(missing)} airport(s) not fetched this tick: {', '.join(missing[:5])}"
                )
        out: list = []
        seen: set[int] = set()
        for icao in icaos:
            for r in self._reports.get(icao.upper(), ()):
                if id(r) not in seen:
                    seen.add(id(r))
                    out.append(r)
        return out


class SharedSigmetSource:
    """Fetches international SIGMETs once per tick, whatever the flight count.

    A failure is logged once and cached; every flight then gets a
    :class:`SigmetSourceUnavailable`, which the refresh skips quietly. An
    unhealthy upstream costs one call and one traceback per tick, not one per
    flight (each flight keeps its stored SIGMETs).
    """

    def __init__(self, upstream=None) -> None:
        self._upstream = upstream
        self._cache: dict[tuple, list | BaseException] = {}

    def fetch_isigmet(self, region: str = "eur", hazard=None, level=None, date=None):
        key = (region, hazard, level, date)
        if key not in self._cache:
            try:
                if self._upstream is None:
                    from euro_aip.briefing.sources.avwx import AvWxSource

                    self._upstream = AvWxSource()
                self._cache[key] = self._upstream.fetch_isigmet(
                    region=region, hazard=hazard, level=level, date=date,
                )
            except Exception as exc:
                logger.warning(
                    "Live tick SIGMET fetch failed — flights keep stored SIGMETs this tick",
                    exc_info=True,
                )
                self._cache[key] = exc
        cached = self._cache[key]
        if isinstance(cached, BaseException):
            raise SigmetSourceUnavailable(str(cached)) from cached
        return cached


# --- The tick ----------------------------------------------------------------


class LiveTick:
    """One live-window pass. Create per cycle; hand :meth:`sink` to the
    verification fetch, then call :meth:`run`."""

    def __init__(self, *, upstream=None, sigmet_upstream=None) -> None:
        self._reports: dict[str, list] = {}
        self._covered: set[str] = set()
        self._upstream = upstream
        self._sigmets = SharedSigmetSource(sigmet_upstream)

    def sink(self, icao: str, reports: list) -> None:
        """Verification's fetch reports each airport here (possibly empty)."""
        key = icao.upper()
        self._covered.add(key)
        self._reports.setdefault(key, []).extend(reports)

    def _top_up(self, icaos: set[str]) -> int:
        missing = sorted(icaos - self._covered)
        if not missing:
            return 0
        if self._upstream is None:
            from euro_aip.briefing.sources.avwx import AvWxSource

            self._upstream = AvWxSource()
        for i in range(0, len(missing), _BATCH_SIZE):
            chunk = missing[i : i + _BATCH_SIZE]
            try:
                reports = self._upstream.fetch_weather(chunk, metar_hours=3)
            except Exception:
                logger.warning("Live tick METAR/TAF fetch failed for %d airport(s)", len(chunk), exc_info=True)
                continue
            for r in reports:
                self._reports.setdefault(r.icao.upper(), []).append(r)
            self._covered.update(chunk)
        return len(missing)

    def run(self, db: Session, airports_db_path: str, now: datetime | None = None) -> dict:
        """Refresh the live layer of every flight in its live window."""
        from euro_aip.briefing.weather.route_weather import RouteWeatherService

        from weatherbrief.airports import _load_airport_model
        from weatherbrief.models.analysis import RouteConfig
        from weatherbrief.storage.flights import _resolve_artifact_path, list_packs
        from weatherbrief.tasks.route_weather import run_realtime_refresh

        t0 = time.monotonic()
        flights = find_live_flights(db, now)
        if not flights:
            return {"flights": 0, "updated": 0, "fetched": 0}
        if len(flights) > _SCALE_WARN_FLIGHTS:
            logger.warning(
                "Live tick: %d flights in window (> %d) — per-flight planning and "
                "refresh run serially every cycle; consider batching",
                len(flights), _SCALE_WARN_FLIGHTS,
            )

        model = _load_airport_model(airports_db_path)
        plans: list[tuple[FlightRow, object, Path]] = []
        needed: set[str] = set()
        for flight in flights:
            try:
                packs = list_packs(db, flight.id)
                if not packs or not packs[0].artifact_path or packs[0].is_historical:
                    continue
                latest = packs[0]
                pack_dir = Path(_resolve_artifact_path(latest.artifact_path))
                briefing = json.loads((pack_dir / "briefing.json").read_text())
                route = RouteConfig.model_validate(briefing["route"])
                corridor = (briefing.get("route_observations") or {}).get("corridor_nm", 30.0)
                recorder = _RecordingSource()
                RouteWeatherService(source=recorder).fetch_route_weather(
                    route_icaos=[wp.icao for wp in route.waypoints],
                    corridor_nm=corridor,
                    model=model,
                )
                needed |= recorder.requested
                plans.append((flight, latest, pack_dir))
            except Exception:
                logger.warning("Live tick: could not plan flight %s", flight.id, exc_info=True)

        fetched = self._top_up(needed)
        source = SharedReportSource(self._reports, covered=self._covered)

        updated = 0
        for flight, latest, pack_dir in plans:
            try:
                run_realtime_refresh(
                    pack_dir, airports_db_path,
                    cloud_source=_cloud_source(db, flight),
                    flight_id=flight.id,
                    pack_timestamp=_as_utc(latest.fetch_timestamp).isoformat(),
                    report_source=source,
                    sigmet_source=self._sigmets,
                )
                updated += 1
            except UncoveredAirportsError as exc:
                # The shared fetch failed for this flight's airports: keep its
                # stored observations rather than blanking them.
                logger.warning("Live tick: skipped flight %s — %s", flight.id, exc)
            except Exception:
                logger.warning("Live tick: refresh failed for flight %s", flight.id, exc_info=True)

        logger.info(
            "Live tick: %d flight(s) in window, %d updated, %d airport(s) topped up, %d ms",
            len(flights), updated, fetched, int((time.monotonic() - t0) * 1000),
        )
        return {"flights": len(flights), "updated": updated, "fetched": fetched}


def _cloud_source(db: Session, flight: FlightRow) -> str | None:
    """The flight profile's cloud grading source — the ↻ path's own helper, so
    a lookup failure degrades (and logs) the same way."""
    from weatherbrief.api.packs import _profile_cloud_source

    return _profile_cloud_source(db, flight, flight.user_id)
