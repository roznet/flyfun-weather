"""Server live-window tick (#637, phase 2).

Every verification cycle (10 min), each flight inside its live window —
``departure - WB_LIVE_WINDOW_BEFORE_H`` (3 h) to ``departure + duration +
WB_LIVE_WINDOW_AFTER_H`` (1 h) — gets its live layer refreshed through the same
seam a D-0 ↻ press uses (:func:`run_realtime_refresh`), without that press.
The end moves with the flight's manual entries (#760, :func:`live_window_end`):
a late take-off pushes it back, a recorded landing closes it.

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
the number of flights.

**Started flights every 5 min (#812).** Between two verification cycles the
loop runs a lighter pass over the started flights only (``started_only``:
Start pressed or a take-off known, not yet arrived). It has no verification
fetch to share, so it costs one METAR/TAF batch and one SIGMET call, again
whatever the number of flights. Started flights also ride the 10-min tick, so
they tick every 5 min; the others stay on 10.

**Start (#812).** The first ground fix at the departure (the app posts one
right after Start) runs :func:`run_start_update` at once, from the position
endpoint: the ↻ path with a fresh fetch, and the highlight regenerated at the
new times. Like a ↻ press it never pushes. Observed radar/lightning/tops are re-sampled from local
frames as usual (no network).

After the commits, each committed flight's new alerts (and pushed alerts that
cleared) go to the live-alert push (#754, ``notify/live_alerts.py``); only this
writer pushes, never a ↻ press.

The tick never runs the model pipeline or an LLM and never changes a grade.
Disabled with ``DISABLE_LIVE_LAYER=1``; also off whenever the verification
loop is (``DISABLE_VERIFICATION=1``) since it rides that loop.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, FlightRow
from weatherbrief.tasks.route_weather import SigmetSourceUnavailable

if TYPE_CHECKING:
    from euro_aip.briefing.weather.sigmet import IsigmetFetch

logger = logging.getLogger(__name__)

_BATCH_SIZE = 400  # aviationweather.gov limit per request
# Planning (local corridor discovery + a briefing.json read) and the cheap
# refresh run per flight per tick. Fine for tens of flights; above this, warn
# so a growing user base shows up in the logs before it shows up as a slow
# verification cycle.
_SCALE_WARN_FLIGHTS = 50
# #697's highlight calls are pure network wait — measured on prod, 1, 4 and 10
# concurrent Haiku calls all return in ~1.2 s — so the cap is about not opening
# an unbounded number of sockets, not about throughput.
_HIGHLIGHT_WORKERS = 8


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


#: A take-off entry is accepted up to this long after the planned departure
#: (#760). A later one is a different flight day: the pilot should move the
#: flight's departure time (which re-briefs it) rather than drag a stale pack
#: along. It also bounds how far a take-off can push the window back.
MAX_TAKEOFF_DELAY_H = 6.0
#: After a recorded landing the window stays open this long past the moment
#: the landing became known, so one tick (10 min cadence) commits the landed
#: state (no highlight, glance passed) before the flight leaves the window.
LANDED_GRACE = timedelta(minutes=15)


def live_window_end(
    departure: datetime, duration_h: float | None, after_h: float,
    events: tuple = (), now: datetime | None = None,
) -> datetime:
    """When the live window closes (#760), as known at ``now``.

    The effective arrival (the planned one, moved by a take-off or fix) +
    ``after_h``, never before the planned arrival + ``after_h``: a Start
    pressed well before the planned time moves the departure earlier
    (#812), which must not close the window before the flight could have
    flown. Once a landing is known (entered, app-detected or inferred from a
    fix), ``LANDED_GRACE`` after the later of the landing and the moment the
    server learned it.
    """
    from weatherbrief.analysis.flight_progress import EVENT_LANDED, FlightProgress

    dep = _as_utc(departure)
    progress = FlightProgress.schedule(dep, duration_h, now or datetime.now(timezone.utc), events)
    if progress.landed_at is not None:
        evidence = progress.latest_event(EVENT_LANDED) if progress.landing_event_at is not None else progress.fix
        known = evidence.recorded_at if evidence is not None else progress.landed_at
        return max(progress.landed_at, known) + LANDED_GRACE
    arrival = progress.arrival or progress.departed_at or dep
    planned = progress.planned_arrival or dep
    return max(arrival, planned) + timedelta(hours=after_h)


def in_live_window(
    departure: datetime, duration_h: float | None, now: datetime,
    before_h: float, after_h: float, events: tuple = (),
) -> bool:
    dep = _as_utc(departure)
    if now < dep - timedelta(hours=before_h):
        return False
    return now <= live_window_end(dep, duration_h, after_h, events, now)


def is_started(flight: FlightRow, events: tuple, now: datetime) -> bool:
    """The flight is started and not yet arrived (#812,
    ``FlightProgress.started``), by its stored fix projections."""
    from weatherbrief.analysis.flight_progress import FlightProgress

    if flight.departure_time is None:
        return False
    return FlightProgress.schedule(
        _as_utc(flight.departure_time), flight.flight_duration_hours, now, events,
    ).started


def find_live_flights(
    db: Session, now: datetime | None = None, *, started_only: bool = False,
) -> list[FlightRow]:
    """Flights with at least one pack whose live window contains ``now``;
    with ``started_only``, only the started ones (#812, :func:`is_started`).

    The planned window, widened by ``MAX_TAKEOFF_DELAY_H``, picks the
    candidates; only those read their progress events (#760) for the real
    end, so finished flights never touch the disk.
    """
    from weatherbrief.storage.flights import _resolve_artifact_path
    from weatherbrief.tasks.live_layer import flight_dir_for_pack, load_progress_events

    now = now or datetime.now(timezone.utc)
    before_h, after_h = live_window_hours()
    stmt = (
        select(FlightRow)
        .join(BriefingPackRow)
        .where(FlightRow.departure_time <= now + timedelta(hours=before_h))
        .distinct()
    )
    out = []
    for row in db.execute(stmt).scalars().all():
        if row.departure_time is None or not in_live_window(
            row.departure_time, row.flight_duration_hours, now, before_h, after_h + MAX_TAKEOFF_DELAY_H,
        ):
            continue
        # The entries live beside the packs, where the endpoint wrote them.
        artifact = db.execute(
            select(BriefingPackRow.artifact_path)
            .where(BriefingPackRow.flight_id == row.id, BriefingPackRow.artifact_path.is_not(None))
            .order_by(BriefingPackRow.fetch_timestamp.desc())
            .limit(1)
        ).scalar()
        events = load_progress_events(flight_dir_for_pack(_resolve_artifact_path(artifact))) if artifact else ()
        if not in_live_window(row.departure_time, row.flight_duration_hours, now, before_h, after_h, events):
            continue
        if started_only and not is_started(row, events, now):
            continue
        out.append(row)
    return out


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
    flight (each flight keeps its stored SIGMETs). A failed base query that
    euro_aip reports rather than raises (``IsigmetFetch.base_ok`` False, #686)
    is a failure too: its empty list would read as every SIGMET gone.
    """

    def __init__(self, upstream=None) -> None:
        self._upstream = upstream
        self._cache: dict[tuple, IsigmetFetch | BaseException] = {}
        #: When the first successful fetch returned (#751's fetch hop).
        self.fetched_at: datetime | None = None

    def fetch_isigmet_result(self, region: str = "eur", hazard=None, level=None, date=None, lookahead=None):
        key = (region, hazard, level, date, lookahead)
        if key not in self._cache:
            try:
                if self._upstream is None:
                    from euro_aip.briefing.sources.avwx import AvWxSource

                    self._upstream = AvWxSource()
                # The lookahead (#683) only when asked for, so an upstream
                # written without it keeps working.
                extra = {"lookahead": lookahead} if lookahead is not None else {}
                fetched = self._upstream.fetch_isigmet_result(
                    region=region, hazard=hazard, level=level, date=date, **extra,
                )
                if not fetched.base_ok:
                    raise RuntimeError("isigmet base query failed")
                self._cache[key] = fetched
                if self.fetched_at is None:
                    self.fetched_at = datetime.now(timezone.utc)
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

    def fetch_isigmet(self, region: str = "eur", hazard=None, level=None, date=None, lookahead=None):
        return self.fetch_isigmet_result(
            region=region, hazard=hazard, level=level, date=date, lookahead=lookahead,
        ).reports


# --- The tick ----------------------------------------------------------------


@dataclass(frozen=True)
class HighlightTiming:
    """One flight's highlight pass, for its latency row (#751). ``outcome``
    is ``HighlightOutcome.outcome``, or ``gated`` when the highlight was
    carried forward (nothing significant changed, no call made)."""

    outcome: str
    requested_at: datetime | None = None
    written_at: datetime | None = None
    latency_ms: int | None = None


class LiveTick:
    """One live-window pass. Create per cycle; hand :meth:`sink` to the
    verification fetch, then call :meth:`run`."""

    def __init__(self, *, upstream=None, sigmet_upstream=None) -> None:
        self._reports: dict[str, list] = {}
        self._covered: set[str] = set()
        # When each airport's reports reached us: verification's fetch or the
        # top-up (#751's fetch hop; the block's own fetch_time is later, when
        # the refresh assembled it from this cache).
        self._fetched_at: dict[str, datetime] = {}
        self._upstream = upstream
        self._sigmets = SharedSigmetSource(sigmet_upstream)

    def sink(self, icao: str, reports: list) -> None:
        """Verification's fetch reports each airport here (possibly empty)."""
        key = icao.upper()
        self._covered.add(key)
        self._fetched_at.setdefault(key, datetime.now(timezone.utc))
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
            fetched_at = datetime.now(timezone.utc)
            for r in reports:
                self._reports.setdefault(r.icao.upper(), []).append(r)
            for icao in chunk:
                self._fetched_at.setdefault(icao, fetched_at)
            self._covered.update(chunk)
        return len(missing)

    def run(
        self, db: Session, airports_db_path: str, now: datetime | None = None,
        *, started_only: bool = False,
    ) -> dict:
        """Refresh the live layer of every flight in its live window; with
        ``started_only``, of the started ones only (#812: the 5-min pass
        between two verification cycles, which fetches its own reports)."""
        from euro_aip.briefing.weather.route_weather import RouteWeatherService

        from weatherbrief.airports import _load_airport_model, route_navpoints
        from weatherbrief.models.analysis import RouteConfig
        from weatherbrief.storage.flights import _resolve_artifact_path, list_packs
        from weatherbrief.tasks.live_layer import LIVE_FROZEN_FILE, CommitTrace
        from weatherbrief.tasks.live_timing import build_tick_row, write_tick_rows
        from weatherbrief.tasks.route_weather import run_realtime_refresh

        t0 = time.monotonic()
        tick_started_at = datetime.now(timezone.utc)
        flights = find_live_flights(db, now, started_only=started_only)
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
                if (pack_dir.parent / LIVE_FROZEN_FILE).exists():
                    continue
                briefing = json.loads((pack_dir / "briefing.json").read_text())
                route = RouteConfig.model_validate(briefing["route"])
                corridor = (briefing.get("route_observations") or {}).get("corridor_nm", 30.0)
                recorder = _RecordingSource()
                RouteWeatherService(source=recorder).fetch_route_weather(
                    route_icaos=route_navpoints(route.waypoints),
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
        committed: list[tuple[FlightRow, Path]] = []
        pushes: list[tuple[FlightRow, Path, object]] = []
        timing_rows = []
        for flight, latest, pack_dir in plans:
            try:
                trace = CommitTrace()
                f0 = time.monotonic()
                pack_timestamp = _as_utc(latest.fetch_timestamp).isoformat()
                result = run_realtime_refresh(
                    pack_dir, airports_db_path,
                    cloud_source=_cloud_source(db, flight),
                    flight_id=flight.id,
                    pack_timestamp=pack_timestamp,
                    report_source=source,
                    sigmet_source=self._sigmets,
                    trace=trace,
                )
                updated += 1
                committed.append((flight, pack_dir))
                # Only a commit that wrote owns its new_alert flags: a refused
                # one (stale, or a ↻ landed first) leaves trace.layer None.
                pushes.append((flight, pack_dir, trace.layer))
                timing_rows.append(self._timing_row(
                    build_tick_row, flight, pack_timestamp, tick_started_at,
                    int((time.monotonic() - f0) * 1000), trace, result,
                ))
            except UncoveredAirportsError as exc:
                # The shared fetch failed for this flight's airports: keep its
                # stored observations rather than blanking them.
                logger.warning("Live tick: skipped flight %s — %s", flight.id, exc)
            except Exception:
                logger.warning("Live tick: refresh failed for flight %s", flight.id, exc_info=True)

        # Alongside the highlights, not before them: the push text is
        # deterministic so it need not wait on a model call (#754), and a slow
        # APNs host must not hold up every flight's highlight either. Its own
        # thread and session (a Session is not thread-safe); joined before the
        # commit below.
        push_result: dict[str, int] = {}
        push_thread = threading.Thread(
            target=self._push_alerts_own_session,
            args=(db.get_bind(), [(f.id, d, l) for f, d, l in pushes], push_result),
            name="live-push", daemon=True,
        )
        push_thread.start()
        try:
            highlights = self._highlights(db, committed)
        finally:
            push_thread.join()
        pushed = push_result.get("pushed", 0)
        highlighted = sum(1 for h in highlights.values() if h.outcome == "written")
        # The highlight cost rows (charge_highlight) are committed on their own
        # session (#815). Close this one's transaction before the latency rows
        # so they start clean.
        try:
            db.commit()
        except Exception:
            logger.warning("Live tick: tick session not committed before the latency rows", exc_info=True)
            db.rollback()

        tick_ms = int((time.monotonic() - t0) * 1000)
        rows = [r for r in timing_rows if r is not None]
        for row in rows:
            row.tick_ms = tick_ms
            h = highlights.get(row.flight_id)
            if h is not None:
                row.highlight_outcome = h.outcome
                row.highlight_requested_at = h.requested_at
                row.highlight_written_at = h.written_at
                row.highlight_latency_ms = h.latency_ms
        write_tick_rows(db, rows)

        logger.info(
            "Live tick%s: %d flight(s) in window, %d updated, %d highlighted, %d airport(s) topped up, %d ms",
            " (started)" if started_only else "", len(flights), updated, highlighted, fetched, tick_ms,
        )
        return {"flights": len(flights), "updated": updated, "fetched": fetched,
                "highlighted": highlighted, "pushed": pushed}

    def _push_alerts_own_session(self, bind, pushes: list[tuple[str, Path, object]], out: dict) -> None:
        """``_push_alerts`` on its own session, for the push thread: each
        flight row is re-read there, never shared with the tick's session."""
        try:
            with Session(bind=bind) as own:
                rows = []
                for flight_id, pack_dir, layer in pushes:
                    row = own.get(FlightRow, flight_id)
                    if row is not None:
                        rows.append((row, pack_dir, layer))
                out["pushed"] = self._push_alerts(own, rows)
        except Exception:
            logger.warning("Live tick: push phase failed", exc_info=True)

    def _push_alerts(self, db: Session, pushes: list[tuple[FlightRow, Path, object]]) -> int:
        """The live-alert push sink (#754), one call per committed flight.
        Returns how many flights got (or, in shadow mode, would have got) a
        push. Never raises: a push is not worth a tick."""
        from weatherbrief.notify.live_alerts import notify_live_alerts

        count = 0
        for flight, pack_dir, layer in pushes:
            outcome = notify_live_alerts(db, flight, layer, pack_dir=pack_dir, trigger="tick")
            count += outcome in ("sent", "shadow")
        return count

    def _timing_row(self, build, flight, pack_timestamp, tick_started_at, flight_ms, trace, result):
        """This flight's ``live_tick_timing`` row (#751), or None. Never raises:
        a latency row is not worth a flight's refresh."""
        try:
            return build(
                flight_id=flight.id,
                pack_timestamp=pack_timestamp,
                tick_started_at=tick_started_at,
                flight_ms=flight_ms,
                trace=trace,
                observations=result.observations,
                sigmets=result.sigmets,
                observed=result.observed,
                fetched_at=self._fetched_at,
                sigmet_fetched_at=self._sigmets.fetched_at,
            )
        except Exception:
            logger.warning("Live tick: latency row failed for flight %s", flight.id, exc_info=True)
            return None

    def _highlights(self, db: Session, committed: list[tuple[FlightRow, Path]]) -> dict[str, HighlightTiming]:
        """Write each committed flight's Observed highlight (#697).

        Returns each committed flight's :class:`HighlightTiming` (#751), empty
        when highlights are off.

        Runs after every layer is committed, so the deterministic blocks were
        servable ~1 s before this starts and a model failure cannot roll a tick
        back. Fanned out because the calls are pure network wait: measured on
        prod, 1, 4 and 10 concurrent Haiku calls all return in ~1.2 s, so the
        tick grows by one call's latency rather than by the flight count.

        Most ticks do no work at all — unchanged facts carry the previous
        highlight forward during the commit.
        """
        from weatherbrief.tasks.live_highlight import (
            HighlightOutcome,
            charge_highlight,
            ensure_highlight,
            highlight_enabled,
        )
        from weatherbrief.tasks.live_layer import flight_dir_for_pack, live_for_pack

        if not committed or not highlight_enabled():
            return {}

        timings: dict[str, HighlightTiming] = {}
        work = []
        for flight, pack_dir in committed:
            # Read back what was stored rather than trust an in-memory layer:
            # a ↻ press may have committed over this tick's write.
            layer = live_for_pack(pack_dir)
            if layer is not None and layer.glance is not None and layer.glance.highlight is None:
                work.append((flight, flight_dir_for_pack(pack_dir), layer))
            elif layer is not None and layer.glance is not None:
                # Carried forward: the #706 gate saw nothing significant change.
                timings[flight.id] = HighlightTiming("gated")
            else:
                timings[flight.id] = HighlightTiming("skipped")
        if not work:
            return timings

        def run(item) -> tuple[FlightRow, HighlightOutcome, datetime, datetime]:
            flight, flight_dir, layer = item
            requested_at = datetime.now(timezone.utc)
            try:
                outcome = ensure_highlight(flight_dir, layer)
            except Exception:
                # ensure_highlight already swallows its own failures; this is
                # the belt-and-braces one thread death would otherwise hide.
                logger.warning("Live tick: highlight failed for flight %s", flight.id, exc_info=True)
                outcome = HighlightOutcome("skipped")
            return flight, outcome, requested_at, datetime.now(timezone.utc)

        with ThreadPoolExecutor(max_workers=min(len(work), _HIGHLIGHT_WORKERS)) as pool:
            results = list(pool.map(run, work))

        # Back on the tick's own thread: a Session is not thread-safe.
        for flight, outcome, requested_at, done_at in results:
            charge_highlight(db, flight.user_id, flight.id, outcome.usage)
            timings[flight.id] = HighlightTiming(
                outcome.outcome, requested_at,
                done_at if outcome.written else None, outcome.latency_ms,
            )
        return timings


def _cloud_source(db: Session, flight: FlightRow) -> str | None:
    """The flight profile's cloud grading source — the ↻ path's own helper, so
    a lookup failure degrades (and logs) the same way."""
    from weatherbrief.api.packs import _profile_cloud_source

    return _profile_cloud_source(db, flight, flight.user_id)


def run_start_update(flight_id: str, airports_db_path: str) -> bool:
    """The immediate update a Start triggers (#812): the first ground fix at
    the departure re-times the flight, so the pilot, who is looking at that
    moment, gets the live layer recomputed at the new times instead of
    waiting for the next tick.

    The ↻ path (``run_realtime_refresh``, persisting, fresh METAR/TAF and
    SIGMET fetch, no model run), then the highlight written anew whatever
    the gate says, with what changed at the new times against the one the
    pilot had (``ensure_highlight(retimed_from=…)``). Never pushes, like a
    ↻ press. Own session: runs after the position endpoint has answered.
    Never raises; returns True when the layer was refreshed.
    """
    from flyfun_common.db import SessionLocal

    from weatherbrief.storage.flights import _resolve_artifact_path, list_packs
    from weatherbrief.tasks.live_layer import LIVE_FROZEN_FILE, flight_dir_for_pack, live_for_pack
    from weatherbrief.tasks.route_weather import run_realtime_refresh

    db = None
    try:
        db = SessionLocal()
        flight = db.get(FlightRow, flight_id)
        packs = list_packs(db, flight_id) if flight is not None else []
        if not packs or not packs[0].artifact_path or packs[0].is_historical:
            return False
        latest = packs[0]
        pack_dir = Path(_resolve_artifact_path(latest.artifact_path))
        flight_dir = flight_dir_for_pack(pack_dir)
        if (flight_dir / LIVE_FROZEN_FILE).exists():
            return False
        before = live_for_pack(pack_dir)
        previous = before.glance.highlight if before is not None and before.glance is not None else None
        retimed_from = (previous.gate or {}) if previous is not None else {}
        try:
            run_realtime_refresh(
                pack_dir, airports_db_path,
                cloud_source=_cloud_source(db, flight),
                flight_id=flight_id,
                pack_timestamp=_as_utc(latest.fetch_timestamp).isoformat(),
            )
        except Exception:
            logger.warning("Start update: refresh failed for flight %s", flight_id, exc_info=True)
            return False
        try:
            from weatherbrief.tasks.live_highlight import charge_highlight, ensure_highlight, highlight_enabled

            layer = live_for_pack(pack_dir)
            if highlight_enabled() and layer is not None:
                outcome = ensure_highlight(flight_dir, layer, retimed_from=retimed_from)
                charge_highlight(db, flight.user_id, flight_id, outcome.usage)
                db.commit()
        except Exception:
            logger.warning("Start update: highlight failed for flight %s", flight_id, exc_info=True)
            db.rollback()
        logger.info("LIVE_START flight=%s refreshed", flight_id)
        return True
    except Exception:
        logger.warning("Start update failed for flight %s", flight_id, exc_info=True)
        return False
    finally:
        if db is not None:
            db.close()
