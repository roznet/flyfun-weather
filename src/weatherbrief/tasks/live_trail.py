"""Change trails (#669): each live change's recent history, for display.

The "since this briefing" panel shows what is true now. On its own it cannot
tell a change that is *building* from one that is *bouncing*, and a change that
cleared vanishes without a trace. This module reads the flight's
``live_history.jsonl`` (#643) and gives every current change a ``trail`` (the
periods it was on screen, how many times today, and for a category change the
airport's category per report) plus the list of changes that cleared on the
weather in the last hour.

Display only. Nothing here feeds the classifier, the tiers, ``new_alert``, the
alert memory or the counts: a flip-flop is shown, never suppressed (§35).

Computed at read time (``GET /live``, realtime refresh responses, the agent
summary), never stored: ``live.json`` is written *before* the history is
appended, so a stored trail would always be one tick behind.

What is and is not "the weather":

- **Grouping** is by change ``key`` (a SIGMET reissue's on its chain's first
  SIGMET, ``_trail_key``) and direction. Identity also holds the
  value and tier (``change_identity``), so LFBZ going MVFR → IFR is a new
  identity on the same key and must continue the span, not start a new one. A
  direction flip (CB reported → CB no longer reported) is a different row.
- **Same tick**: a clear and an appear of the same key and direction at one
  tick is one continuous span (a value change, or a pack switch that re-shows
  the change against the new briefing).
- **Pack switch**: a clear at a tick that recorded a ``pack`` with no
  re-appear means the new briefing absorbed the change. No cleared row.
- **Relevance drop-out** (§36): the departure after take-off, an en-route
  airport once passed. Cleared by the clock, not the weather. No cleared row.
- **No newer report**: an airport change that cleared without a newer report
  for that airport (the corridor fetch missed it) is a data gap, not weather.
  No cleared row, and a re-appear continues the span.
- **SIGMET expiry, radar/lightning** clears are weather news and kept —
  except that a SIGMET reissue (#682) appearing on the cleared row's key
  within the reissue window continues the span (the FIR's gap between
  expiry and reissue).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from itertools import groupby
from pathlib import Path

from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.live import (
    LiveChange,
    LiveChanges,
    LiveChangeTrail,
    LiveTrailReport,
    LiveTrailSpan,
)
from weatherbrief.tasks.live_significance import SIGMET_REISSUE_WINDOW

logger = logging.getLogger(__name__)

#: A change that cleared stays visible this long (D1 on #669).
RECENTLY_CLEARED_MINUTES = 60
#: Reports in a ``metar_category`` strip (the latest ones).
TRAIL_MAX_REPORTS = 6

_METAR_KINDS = {"metar_category", "metar_convective", "metar_weather", "metar_wind"}


@dataclass
class _Span:
    start: datetime
    first: LiveChange  # the change as it first appeared
    last: LiveChange  # the latest form shown (a clear carries it)
    baseline_source: str | None
    end: datetime | None = None
    # Why it ended: "weather", "pack" (absorbed by a new briefing),
    # "dropout" (no longer relevant), "gap" (no newer report).
    reason: str | None = None


@dataclass
class _Context:
    route: RouteConfig | None = None
    departure: datetime | None = None
    # ICAO -> [(tick_at, observed_at/issued_at, record)] in history order.
    metars: dict[str, list[tuple[datetime, datetime | None, dict]]] = field(default_factory=dict)
    tafs: dict[str, list[tuple[datetime, datetime | None]]] = field(default_factory=dict)


def _dt(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _upto(history: list[dict], now: datetime) -> list[dict]:
    """Records up to ``now`` (a read is as of ``now``; tests replay a day)."""
    out = []
    for r in history:
        at = _dt(r.get("tick_at"))
        if at is not None and at <= now:
            out.append(r)
    return out


def _index_reports(history: list[dict], ctx: _Context) -> None:
    for r in history:
        if r.get("type") != "report" or not r.get("icao"):
            continue
        icao = str(r["icao"]).upper()
        tick = _dt(r.get("tick_at"))
        if tick is None:
            continue  # _newer_report compares ticks; an undated record can't count
        if r.get("kind") == "metar":
            ctx.metars.setdefault(icao, []).append((tick, _dt(r.get("observed_at")), r))
        elif r.get("kind") == "taf":
            ctx.tafs.setdefault(icao, []).append((tick, _dt(r.get("issued_at"))))


def _newer_report(c: LiveChange, at: datetime, ctx: _Context) -> bool:
    """A report for the change's airport newer than the one that showed it
    was recorded by ``at``: what an airport change clearing on the weather
    needs. True when there is nothing to check against."""
    if c.observed_at is None or not c.icao:
        return True
    icao = c.icao.upper()
    if c.kind in _METAR_KINDS:
        return any(t <= at and o is not None and o > c.observed_at for t, o, _ in ctx.metars.get(icao, []))
    if c.kind == "taf_category":
        return any(t <= at and o is not None and o > c.observed_at for t, o in ctx.tafs.get(icao, []))
    return True


def _still_relevant(c: LiveChange, at: datetime, ctx: _Context) -> bool:
    """The airport could still matter at ``at`` (the classifier's own rule)."""
    from weatherbrief.tasks.live_layer import _flown_nm
    from weatherbrief.tasks.live_significance import airport_relevant

    if not c.icao or c.role not in ("departure", "route"):
        return True
    departed = ctx.departure is not None and at >= ctx.departure
    flown = _flown_nm(ctx.route, ctx.departure, at) if ctx.route is not None else None
    return airport_relevant(c.role, c.enroute_distance_nm, departed=departed, flown_nm=flown)


def _clear_reason(c: LiveChange, at: datetime, pack_tick: bool, ctx: _Context) -> str:
    if pack_tick:
        return "pack"
    if c.kind in _METAR_KINDS or c.kind == "taf_category":
        if not _still_relevant(c, at, ctx):
            return "dropout"
        if not _newer_report(c, at, ctx):
            return "gap"
    return "weather"


def _trail_key(c: LiveChange) -> str:
    """What a trail groups on: the change key, except that a SIGMET reissue
    row ("sigmet:LFMM|T01+sigmet:LFMM|T02", #682) groups on its chain's first
    SIGMET, so T01's row and every reissue after it are one trail."""
    return c.key.split("+", 1)[0] if c.replaces else c.key


def _reissue_resumes(last: _Span, c: LiveChange, tick: datetime | None) -> bool:
    """A SIGMET reissue (#682) appearing on its chain's key within the reissue
    window of the predecessor's row clearing: the FIR's gap between expiry
    and reissue (LFMM T01 → T02, 10 min on 2026-10-04), not new weather."""
    return (
        c.replaces is not None
        and last.reason == "weather"
        and last.end is not None
        and tick is not None
        and tick - last.end <= SIGMET_REISSUE_WINDOW
    )


def _spans(history: list[dict], ctx: _Context) -> dict[tuple[str, str], list[_Span]]:
    """Every (key, direction)'s on-screen periods over the history."""
    spans: dict[tuple[str, str], list[_Span]] = {}
    baseline: str | None = None
    for tick_s, group in groupby(history, key=lambda r: r.get("tick_at")):
        tick = _dt(tick_s)
        records = list(group)
        packs = [r for r in records if r.get("type") == "pack"]
        if packs:
            baseline = "briefing" if packs[-1].get("has_observations") else "live_start"
        appeared: dict[tuple[str, str], LiveChange] = {}
        cleared: dict[tuple[str, str], LiveChange] = {}
        for r in records:
            if r.get("type") != "event" or not isinstance(r.get("change"), dict):
                continue
            try:
                c = LiveChange.model_validate(r["change"])
            except Exception:
                continue
            (appeared if r.get("event") == "appeared" else cleared)[(_trail_key(c), c.direction)] = c
        for kd, c in cleared.items():
            if kd in appeared:
                continue  # re-shown at the same tick: one continuous span
            open_ = spans.get(kd, [None])[-1]
            if open_ is None or open_.end is not None:
                continue
            open_.end, open_.last = tick, c
            open_.reason = _clear_reason(c, tick, bool(packs), ctx)
        for kd, c in appeared.items():
            kd_spans = spans.setdefault(kd, [])
            last = kd_spans[-1] if kd_spans else None
            if last is not None and (last.end is None or last.reason == "gap" or _reissue_resumes(last, c, tick)):
                # Still on (value/tier change), back after a data gap, or a
                # SIGMET reissue taking over its predecessor's row.
                last.end, last.reason = None, None
                last.last, last.baseline_source = c, baseline
                continue
            kd_spans.append(_Span(start=tick, first=c, last=c, baseline_source=baseline))
    return spans


@lru_cache(maxsize=512)
def _reparse_category(raw: str, observed_at: str | None) -> str | None:
    """Flight category of a METAR recorded before ``flight_category`` was
    (files from 2026-10-03/04), parsed the way the scenario rebuild does."""
    try:
        from euro_aip.briefing.weather.parser import WeatherParser

        rep = WeatherParser.parse_metar(raw, source="live_history", reference=_dt(observed_at))
        if rep is not None and rep.flight_category is not None:
            return rep.flight_category.value
        logger.debug("Live trails: no flight category parsed from %r — strip shows ?", raw)
        return None
    except Exception:
        logger.debug("Live trails: unparseable METAR %r — strip shows ?", raw, exc_info=True)
        return None


def _category(rec: dict) -> str | None:
    if "flight_category" in rec:
        return rec.get("flight_category")
    return _reparse_category(rec.get("raw") or "", rec.get("observed_at")) if rec.get("raw") else None


def _reports(c: LiveChange, since: datetime | None, ctx: _Context) -> list[LiveTrailReport]:
    """The airport's METAR/SPECI from the one that first showed the change,
    one per observation time (a COR replaces it), latest ``TRAIL_MAX_REPORTS``."""
    by_time: dict[datetime, dict] = {}
    for _, observed, rec in ctx.metars.get((c.icao or "").upper(), []):
        if observed is None or (since is not None and observed < since):
            continue
        by_time[observed] = rec
    out = [
        LiveTrailReport(at=t, category=_category(rec), report_type=rec.get("report_type"))
        for t, rec in sorted(by_time.items())
    ]
    return out[-TRAIL_MAX_REPORTS:]


def _trail(
    c: LiveChange, kd_spans: list[_Span], ctx: _Context, *, current: bool, baseline_source: str | None,
) -> LiveChangeTrail:
    # On screen now after a gap-ended span: the history has not recorded the
    # re-appear yet, but it will continue that span, not start a new one.
    gap_open = current and bool(kd_spans) and kd_spans[-1].reason == "gap"
    spans = [LiveTrailSpan(start=s.start, end=s.end) for s in kd_spans]
    if gap_open:
        spans[-1].end = None
    times = len(spans)
    if current and not (kd_spans and (kd_spans[-1].end is None or gap_open)):
        times += 1  # on screen now, but the history has not recorded it (yet)
    trail = LiveChangeTrail(spans=spans, times_today=max(times, 1), baseline_source=baseline_source)
    if c.kind == "metar_category" and c.icao:
        since = kd_spans[0].first.observed_at if kd_spans else c.observed_at
        trail.reports = _reports(c, since, ctx)
    return trail


def change_trails(
    history: list[dict],
    changes: LiveChanges,
    *,
    now: datetime,
    route: RouteConfig | None = None,
    departure: datetime | None = None,
) -> LiveChanges:
    """``changes`` with a ``trail`` on every change and ``recently_cleared``
    filled, from the flight's history records as of ``now``. Pure: the
    stored layer is not touched (a copy is returned).

    ``route`` and ``departure`` (the briefing's) let a relevance drop-out be
    told from a clear on the weather; without them every airport is taken as
    still relevant.
    """
    ctx = _Context(route=route, departure=departure)
    history = _upto(history, now)
    _index_reports(history, ctx)
    spans = _spans(history, ctx)

    out = changes.model_copy(deep=True)
    for c in out.changes:
        c.trail = _trail(
            c, spans.get((_trail_key(c), c.direction), []), ctx, current=True,
            baseline_source=out.baseline_source,
        )

    on_screen = {_trail_key(c) for c in out.changes}
    # Only a key's most recent span can be a cleared row: a better reading
    # that gave way to a worse one (LECH IFR → VFR, then IFR → LIFR) must
    # not resurface when the worse one ends for another reason.
    def _ended(kd: tuple[str, str]) -> datetime:
        end = spans[kd][-1].end
        return end if end is not None else datetime.max.replace(tzinfo=timezone.utc)

    latest: dict[str, tuple[str, str]] = {}
    for kd in spans:
        prev = latest.get(kd[0])
        if prev is None or _ended(kd) >= _ended(prev):
            latest[kd[0]] = kd
    window = timedelta(minutes=RECENTLY_CLEARED_MINUTES)
    cleared: list[LiveChange] = []
    for key, kd in latest.items():
        kd_spans = spans[kd]
        last = kd_spans[-1]
        if last.end is None or last.reason != "weather" or key in on_screen:
            continue
        if now - last.end > window:
            continue
        row = last.last.model_copy(update={"new_alert": False, "cleared_at": last.end})
        row.trail = _trail(row, kd_spans, ctx, current=False, baseline_source=last.baseline_source)
        cleared.append(row)
    cleared.sort(key=lambda c: c.key)
    cleared.sort(key=lambda c: c.cleared_at, reverse=True)
    out.recently_cleared = cleared
    return out


# --- Readers ----------------------------------------------------------------


#: Pack dir -> (route, departure), successful reads only (see below).
_ROUTE_CACHE: dict[str, tuple[RouteConfig, datetime | None]] = {}
_ROUTE_CACHE_MAX = 64


def _route_context(pack_dir: str) -> tuple[RouteConfig | None, datetime | None]:
    """The pack's route and departure. Packs are immutable, so a good read
    is kept rather than redone on every 5-min ``/live`` poll. A failed one
    (unreadable briefing, a read racing the pack write) is *not* kept:
    without a route every airport counts as still relevant, and drop-outs
    would show as cleared rows until the process restarts."""
    from weatherbrief.tasks.artifacts import load_briefing

    cached = _ROUTE_CACHE.get(pack_dir)
    if cached is not None:
        return cached
    route, departure = route_context(load_briefing(Path(pack_dir)) or {})
    if route is None:
        logger.warning("Live trails: no route for %s — relevance drop-outs not detected this read", pack_dir)
        return None, departure
    if len(_ROUTE_CACHE) >= _ROUTE_CACHE_MAX:
        _ROUTE_CACHE.pop(next(iter(_ROUTE_CACHE)))  # oldest first
    _ROUTE_CACHE[pack_dir] = (route, departure)
    return route, departure


def route_context(briefing_data: dict) -> tuple[RouteConfig | None, datetime | None]:
    from weatherbrief.tasks.artifacts import parse_target_time

    try:
        route = RouteConfig.model_validate(briefing_data["route"])
    except Exception:
        route = None
    try:
        departure = parse_target_time(briefing_data)
    except Exception:
        departure = None
    return route, departure


def trails_for_pack(
    pack_dir: Path | str,
    changes: LiveChanges | None,
    *,
    now: datetime | None = None,
    briefing_data: dict | None = None,
) -> LiveChanges | None:
    """:func:`change_trails` for this pack's flight. Never raises: on any
    problem the changes come back as they were (no trail is better than no
    panel)."""
    from weatherbrief.tasks.live_layer import flight_dir_for_pack, load_live_history

    if changes is None:
        return None
    try:
        pack_dir = Path(pack_dir)
        route, departure = (
            route_context(briefing_data) if briefing_data is not None else _route_context(str(pack_dir))
        )
        return change_trails(
            load_live_history(flight_dir_for_pack(pack_dir)), changes,
            now=now or datetime.now(timezone.utc), route=route, departure=departure,
        )
    except Exception:
        logger.warning("Live trails failed for %s — served without", pack_dir, exc_info=True)
        return changes
