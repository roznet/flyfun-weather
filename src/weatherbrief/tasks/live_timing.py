"""Observed latency (#751): how long a report takes to reach the pilot's screen.

The hops, per item:

    report time ─► fetched ─► available ─► (highlight) ─► delivered
    (METAR obs,    (our       (the tick     (Haiku line    (a client receives a
     TAF issue,    fetch of   committed     written)       ``live_updated_at`` at
     SIGMET        that       ``live.json``)               or after that tick)
     valid-from)   airport)

**Source time is the time on the report.** We do not know when a provider
published it and do not try to; the question is how long after the airport
made the report the pilot sees it.

Two tables (``db/models.py``): ``live_tick_timing`` (one row per flight per
tick, written by :class:`~weatherbrief.tasks.live_tick.LiveTick`) and
``live_delivery`` (one row per new version a client received, written by the
``/live`` endpoints). Durations are derived here, at read time
(:func:`latency_report`), never stored.

Every write here is best effort: a failure is logged and swallowed, never
failing a tick or a ``/live`` request.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from weatherbrief.db.models import LiveDeliveryRow, LiveTickTimingRow

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 180
RETENTION_ENV = "LIVE_LATENCY_RETENTION_DAYS"

#: ``live_delivery.platform`` values. ``ipados`` is not told apart from
#: ``ios``: the app sends URLSession's default agent on both.
PLATFORMS = ("ios", "web", "agent", "other")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000.0


# --- Tick rows ---------------------------------------------------------------


def new_items(
    records: Iterable[dict],
    *,
    observations=None,
    sigmets=None,
    fetched_at: dict[str, datetime] | None = None,
    sigmet_fetched_at: datetime | None = None,
) -> list[dict]:
    """What this tick showed for the first time, from the history records it
    appended (``live_layer._history_records``).

    - a report (METAR/SPECI, TAF, SIGMET) from **this tick's** fetch. A pack
      switch also records the briefing's own reports (with the briefing's
      fetch time as ``seen_at``); those were shown with the briefing, so they
      are not new here.
    - an alert-tier ``appeared`` event, anchored on its evidence time (the
      METAR time, TAF issue, SIGMET valid-from or radar frame behind it).

    Nothing on the flight's first live write (see the body).

    Each item carries ``report_at`` (the time on the report) and
    ``fetched_at``: the shared fetch of that airport when the tick knows it,
    else the block's fetch time.
    """
    records = list(records)
    if any(r.get("type") == "pack" and r.get("previous_pack_timestamp") is None for r in records):
        # The flight's first live write: every report on the corridor and
        # every change on screen is "first seen", however old. Their spans
        # would measure when we started looking, not how fast we are.
        return []
    fetched_at = fetched_at or {}
    this_tick = {_iso(b.fetch_time) for b in (observations, sigmets) if b is not None}
    out: list[dict] = []
    for r in records:
        if r.get("type") == "report":
            seen = r.get("seen_at")
            if seen is not None and seen not in this_tick:
                continue  # a briefing baseline report recorded on a pack switch
            kind = r.get("kind")
            fallback = _parse(seen) or _parse(r.get("tick_at"))
            if kind == "metar":
                report_at = r.get("observed_at")
                fetched = fetched_at.get((r.get("icao") or "").upper()) or fallback
            elif kind == "taf":
                report_at = r.get("issued_at")
                fetched = fetched_at.get((r.get("icao") or "").upper()) or fallback
            elif kind == "sigmet":
                report_at = r.get("valid_from")
                fetched = sigmet_fetched_at or fallback
            else:
                continue
            item = {"kind": kind, "report_at": report_at, "fetched_at": _iso(fetched)}
            if r.get("report_type") == "SPECI":
                item["speci"] = True
            out.append(item)
        elif r.get("type") == "event" and r.get("event") == "appeared":
            change = r.get("change") or {}
            if change.get("tier") == "alert":
                out.append({"kind": "alert", "key": change.get("key"),
                            "report_at": change.get("observed_at")})
    return out


def _newest(pairs: Iterable[tuple[datetime | None, str | None]]) -> tuple[datetime | None, str | None]:
    best: tuple[datetime | None, str | None] = (None, None)
    for t, key in pairs:
        if t is not None and (best[0] is None or t > best[0]):
            best = (t, key)
    return best


def build_tick_row(
    *,
    flight_id: str,
    pack_timestamp: str | None,
    tick_started_at: datetime,
    flight_ms: int | None,
    trace,
    observations=None,
    sigmets=None,
    observed=None,
    fetched_at: dict[str, datetime] | None = None,
    sigmet_fetched_at: datetime | None = None,
) -> LiveTickTimingRow | None:
    """The ``live_tick_timing`` row for one flight's commit, or None when the
    commit did not write (refused as stale: no new version to describe).

    ``trace`` is the ``live_layer.CommitTrace`` the commit filled. The
    highlight and ``tick_ms`` columns are set later, once the tick knows them.
    """
    if trace is None or trace.committed_at is None:
        return None
    fetched_at = fetched_at or {}
    row = LiveTickTimingRow(
        flight_id=flight_id,
        pack_timestamp=pack_timestamp,
        tick_started_at=tick_started_at,
        committed_at=trace.committed_at,
        flight_ms=flight_ms,
        cells_frame_at=trace.cells_frame_at,
        cells_computed_at=trace.cells_computed_at,
        cells_received_at=trace.cells_received_at,
    )
    if observations is not None:
        airports = list(observations.airports or [])
        t, icao = _newest((a.metar_time, a.icao) for a in airports)
        row.metar_observed_at = t
        row.metar_fetched_at = (fetched_at.get((icao or "").upper()) or observations.fetch_time) if t else None
        t, icao = _newest((a.taf_issue_time, a.icao) for a in airports)
        row.taf_issued_at = t
        row.taf_fetched_at = (fetched_at.get((icao or "").upper()) or observations.fetch_time) if t else None
    if sigmets is not None:
        fetched = sigmet_fetched_at or sigmets.fetch_time
        # A pending SIGMET (#683) is valid from later than it was fetched:
        # its valid-from is not an issue time, so it is left out.
        t, _ = _newest((s.valid_from, None) for s in sigmets.sigmets
                       if s.valid_from is not None and s.valid_from <= fetched)
        row.sigmet_issued_at = t
        row.sigmet_fetched_at = fetched if t else None
    if observed is not None and getattr(observed, "reflectivity", None) is not None:
        row.radar_frame_at = observed.reflectivity.valid_time
    items = new_items(trace.records, observations=observations, sigmets=sigmets,
                      fetched_at=fetched_at, sigmet_fetched_at=sigmet_fetched_at)
    row.new_reports = sum(1 for i in items if i["kind"] != "alert")
    row.new_alerts = sum(1 for i in items if i["kind"] == "alert")
    row.new_items_json = json.dumps(items, separators=(",", ":")) if items else None
    return row


def write_tick_rows(db: Session, rows: list[LiveTickTimingRow]) -> int:
    """Insert and commit the tick's rows. Never raises; returns rows written.

    In a savepoint, so a failed insert (a missing table on a deploy that
    skipped the migration, a bad value) rolls back only itself. The caller
    commits its own pending work first (the tick's highlight cost rows), so
    the rollback here never takes anything but these rows with it.
    """
    if not rows:
        return 0
    try:
        with db.begin_nested():
            db.add_all(rows)
        db.commit()
        return len(rows)
    except Exception:
        logger.warning("Live tick: latency rows not written (%d) — tick kept", len(rows), exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return 0


# --- Deliveries --------------------------------------------------------------


def platform_of(client: str | None) -> str:
    """``live_delivery.platform`` from ``api/client_info.classify_user_agent``."""
    return client if client in ("ios", "web") else "other"


# (flight, user, platform) -> newest live_updated_at already recorded. Spares
# the DB read on the common poll (unchanged version); bounded, and a miss falls
# back to the table, so a restart costs one read per key.
_LAST_SERVED: OrderedDict[tuple[str, str, str], datetime] = OrderedDict()
_LAST_SERVED_SIZE = 4096
_LAST_SERVED_LOCK = threading.Lock()


def _remember(key: tuple[str, str, str], served: datetime) -> None:
    with _LAST_SERVED_LOCK:
        _LAST_SERVED[key] = served
        _LAST_SERVED.move_to_end(key)
        while len(_LAST_SERVED) > _LAST_SERVED_SIZE:
            _LAST_SERVED.popitem(last=False)


def _reset_cache() -> None:
    """Tests only."""
    with _LAST_SERVED_LOCK:
        _LAST_SERVED.clear()


def record_delivery(
    db: Session,
    *,
    flight_id: str,
    user_id: str,
    platform: str,
    served: datetime | None,
    now: datetime | None = None,
    via: str = "poll",
) -> bool:
    """Record that this client received ``served`` (a ``live_updated_at``),
    if it is newer than anything it got before for this flight.

    Returns True when a row was written. Never raises. Writes through its own
    short-lived session on ``db``'s engine: it never commits or rolls back
    the caller's session, whose pending work (if any) stays the caller's.
    """
    if served is None or not user_id:
        return False
    key = (flight_id, user_id, platform)
    try:
        with _LAST_SERVED_LOCK:
            last = _LAST_SERVED.get(key)
        if last is not None and served <= last:
            return False
        with Session(bind=db.get_bind()) as own:
            if last is None:
                last = own.execute(
                    select(func.max(LiveDeliveryRow.served_live_updated_at)).where(
                        LiveDeliveryRow.flight_id == flight_id,
                        LiveDeliveryRow.user_id == user_id,
                        LiveDeliveryRow.platform == platform,
                    )
                ).scalar()
                if last is not None and served <= last:
                    _remember(key, last)
                    return False
            own.add(LiveDeliveryRow(
                flight_id=flight_id, user_id=user_id, platform=platform,
                served_live_updated_at=served,
                requested_at=now or datetime.now(timezone.utc),
                delivered_via=via,
            ))
            try:
                own.commit()
            except IntegrityError:
                # A concurrent poll of the same client recorded it first.
                own.rollback()
                _remember(key, served)
                return False
        _remember(key, served)
        return True
    except Exception:
        logger.warning("Live delivery not recorded for %s — request kept", flight_id, exc_info=True)
        return False


def record_delivery_for_pack(
    db: Session, *, flight_id: str, user_id: str, platform: str, pack_dir,
) -> bool:
    """:func:`record_delivery` for the live version an agent read with this
    pack (``live_meta.json`` only). Never raises."""
    try:
        from weatherbrief.tasks.live_layer import live_updated_at_for_pack

        served = live_updated_at_for_pack(pack_dir)
    except Exception:
        logger.warning("Live delivery: version unreadable for %s", flight_id, exc_info=True)
        return False
    return record_delivery(db, flight_id=flight_id, user_id=user_id, platform=platform, served=served)


# --- Retention ---------------------------------------------------------------


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get(RETENTION_ENV, DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def purge_old(db: Session, days: int | None = None, now: datetime | None = None) -> int:
    """Delete latency rows older than the retention window (default 180 d)."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days or retention_days())
    n = db.execute(delete(LiveTickTimingRow).where(LiveTickTimingRow.committed_at < cutoff)).rowcount or 0
    n += db.execute(delete(LiveDeliveryRow).where(LiveDeliveryRow.requested_at < cutoff)).rowcount or 0
    return n


def delete_for_user(db: Session, user_id: str, flight_ids: Iterable[str]) -> None:
    """Account deletion: the user's deliveries and their flights' tick rows."""
    db.execute(delete(LiveDeliveryRow).where(LiveDeliveryRow.user_id == user_id))
    ids = list(flight_ids)
    if ids:
        db.execute(delete(LiveTickTimingRow).where(LiveTickTimingRow.flight_id.in_(ids)))


# --- Report ------------------------------------------------------------------

#: Hops in display order: key -> label. Keys with a ``:<platform>`` or
#: ``:<kind>`` suffix are expanded in :func:`latency_report`.
HOPS: dict[str, str] = {
    "report_to_fetched": "Report time → fetched",
    "fetched_to_available": "Fetched → available",
    "available_to_highlight": "Available → highlight written",
    # Dominated by the client's poll interval (iOS: 5 min), not server work.
    "available_to_delivered": "Available → delivered (poll)",
    "end_to_end": "Report time → delivered",
    "cells_computed_to_received": "Cells frame built → droplet",
    "cells_frame_to_available": "Cells frame time → available",
    "tick": "Tick duration",
}

#: Report kinds the end-to-end chain is reported for.
E2E_KINDS = ("metar", "sigmet", "alert")


@dataclass
class _Stat:
    values: list[float]

    def summary(self) -> dict:
        v = sorted(self.values)
        if not v:
            return {"n": 0, "p50": None, "p95": None, "max": None}
        return {"n": len(v), "p50": _pct(v, 0.50), "p95": _pct(v, 0.95), "max": round(v[-1] / 1000.0, 1)}


def _pct(sorted_ms: list[float], q: float) -> float:
    """Nearest-rank percentile, in seconds (one decimal)."""
    idx = max(0, min(len(sorted_ms) - 1, math.ceil(q * len(sorted_ms)) - 1))
    return round(sorted_ms[idx] / 1000.0, 1)


def _first_delivery(
    deliveries: list[tuple[datetime, datetime]], committed: datetime,
) -> datetime | None:
    """Earliest ``requested_at`` of a delivery whose served version is at or
    after ``committed`` (``deliveries`` sorted by requested_at)."""
    for requested, served in deliveries:
        if served >= committed and requested >= committed:
            return requested
    return None


def latency_report(db: Session, *, days: int = 30, now: datetime | None = None) -> dict:
    """Daily p50 / p95 / max per hop over the last ``days``, with counts.

    Seconds throughout. A day is the UTC day of the tick's commit (delivery
    hops: of the tick the delivery served). Negative spans (clock skew, or a
    SIGMET valid-from after its fetch) are dropped and counted under
    ``dropped``.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    ticks = db.execute(
        select(LiveTickTimingRow).where(LiveTickTimingRow.committed_at >= since)
    ).scalars().all()
    # A delivery can serve a tick committed just before the window.
    deliveries = db.execute(
        select(LiveDeliveryRow).where(LiveDeliveryRow.requested_at >= since)
    ).scalars().all()

    by_flight_platform: dict[tuple[str, str], list[tuple[datetime, datetime]]] = defaultdict(list)
    for d in deliveries:
        by_flight_platform[(d.flight_id, d.platform)].append((d.requested_at, d.served_live_updated_at))
    for v in by_flight_platform.values():
        v.sort()
    platforms_by_flight: dict[str, set[str]] = defaultdict(set)
    for flight_id, platform in by_flight_platform:
        platforms_by_flight[flight_id].add(platform)
    tick_by_version = {(t.flight_id, t.committed_at): t for t in ticks}

    per_day: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall: dict[str, list[float]] = defaultdict(list)
    dropped: dict[str, int] = defaultdict(int)

    def add(day: date, hop: str, ms: float | None) -> None:
        if ms is None:
            return
        if ms < 0:
            dropped[hop] += 1
            return
        per_day[day][hop].append(ms)
        overall[hop].append(ms)

    seen_ticks: set[datetime] = set()
    for t in ticks:
        day = t.committed_at.date()
        if t.tick_started_at not in seen_ticks and t.tick_ms is not None:
            # One duration per tick, whatever the flight count.
            seen_ticks.add(t.tick_started_at)
            add(day, "tick", float(t.tick_ms))
        if t.highlight_outcome == "written":
            add(day, "available_to_highlight", _ms(t.committed_at, t.highlight_written_at))
        add(day, "cells_computed_to_received", _ms(t.cells_computed_at, t.cells_received_at))
        add(day, "cells_frame_to_available", _ms(t.cells_frame_at, t.committed_at))
        try:
            items = json.loads(t.new_items_json) if t.new_items_json else []
        except ValueError:
            items = []
        firsts = {p: _first_delivery(by_flight_platform[(t.flight_id, p)], t.committed_at)
                  for p in platforms_by_flight.get(t.flight_id, ())}
        for item in items:
            kind = item.get("kind")
            report_at = _parse(item.get("report_at"))
            if kind != "alert":
                fetched = _parse(item.get("fetched_at"))
                add(day, f"report_to_fetched:{kind}", _ms(report_at, fetched))
                add(day, f"fetched_to_available:{kind}", _ms(fetched, t.committed_at))
            if kind == "alert" and report_at is None:
                # No evidence time: commit → delivery only, which would
                # understate the series. Counted, not mixed in.
                dropped["end_to_end:alert:no_evidence_time"] += 1
                continue
            if kind in E2E_KINDS:
                for platform, first in firsts.items():
                    if first is not None:
                        add(day, f"end_to_end:{kind}:{platform}", _ms(report_at, first))

    for d in deliveries:
        t = tick_by_version.get((d.flight_id, d.served_live_updated_at))
        if t is None:
            # A version a ↻ press committed, or a tick before the window.
            dropped["available_to_delivered:untracked"] += 1
            continue
        add(t.committed_at.date(), f"available_to_delivered:{d.platform}",
            _ms(t.committed_at, d.requested_at))

    def label(key: str) -> str:
        base, _, rest = key.partition(":")
        return HOPS.get(base, base) + (f" ({rest.replace(':', ', ')})" if rest else "")

    def order(key: str) -> tuple:
        base = key.split(":", 1)[0]
        return (list(HOPS).index(base) if base in HOPS else len(HOPS), key)

    keys = sorted(overall, key=order)
    return {
        "days": days,
        "generated_at": now.isoformat(),
        "hops": [{"key": k, "label": label(k), **_Stat(overall[k]).summary()} for k in keys],
        "daily": [
            {"day": day.isoformat(),
             "hops": {k: _Stat(per_day[day][k]).summary() for k in keys if per_day[day].get(k)}}
            for day in sorted(per_day, reverse=True)
        ],
        "counts": {
            "tick_rows": len(ticks),
            "ticks": len(seen_ticks),
            "deliveries": len(deliveries),
            "dropped": dict(dropped),
        },
    }
