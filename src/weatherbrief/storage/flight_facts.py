"""The durable per-flight facts record behind the past-flights finder (#1031).

``flight_facts`` (one row per flight, ``db.models.FlightFactsRow``) outlives
T2 retention, which deletes a flight's packs and with them everything the
server knew about the flight's weather. This module is its only writer.

Three write paths, all idempotent:

- :func:`update_flight_facts` — the full refresh: route ends and line from the
  newest readable pack (else the airports DB, else the codes alone), forecast
  tags from the pack ``default_forecast_pack`` chooses, then the outcome. Run
  on every pack build (``api/packs._persist_pack_finalize``), at debrief
  window close with ``freeze=True`` (``tasks/debrief_close``), by retention
  before T2 deletes a flight's packs (``tasks/retention``) and by the backfill
  (``scripts/ops/backfill_flight_facts.py``).
- :func:`update_flight_outcome` — the cheap one, no pack read: reported tags
  and the debrief outcome. Run on every debrief save / delete / answers PUT.

**Forecast tags.** Worst aggregate status per debrief tag over the chosen
pack's advisories, amber or worse only (``{"ICE": "red"}``). Advisory ids map
through :data:`ARCHIVE_TAG_MAP` (``ADVISORY_TAG_MAP`` plus freezing precip and
en-route precip, which the debrief form has no outcome row for). While the
flight is in the future the chosen pack is simply the newest one, so the tags
follow each refresh; once frozen they never change again. ``tags_recorded``
False = no pack could be read, which the clients show as "hazards not
recorded", never as "none".

**Freeze.** ``freeze=True`` (window close, retention, backfill) or the flight
ended more than :data:`FREEZE_AFTER` ago. Only once the departure has passed:
before it a newer pack would still be the right one.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightDebriefReportRow, FlightFactsRow, FlightRow
from weatherbrief.debriefs.taxonomy import ADVISORY_TAG_MAP, ConditionTag

logger = logging.getLogger(__name__)

#: Advisory id -> archive tag. The debrief map, plus the two hazards the
#: archive chips name that the debrief form grades no outcome for.
ARCHIVE_TAG_MAP: dict[str, ConditionTag] = {
    **ADVISORY_TAG_MAP,
    "freezing_precip": ConditionTag.FRZ,
    "enroute_precip": ConditionTag.VIS,
}

#: The weather tags the archive filters on, in chip order.
ARCHIVE_TAGS: list[str] = ["ICE", "TS", "IMC", "WIND", "TURB", "FRZ", "VIS"]

#: Reported tags from debrief answers: (question kind, answer) -> tag.
ANSWER_TAGS: dict[tuple[str, str], str] = {
    ("icing", "light"): "ICE",
    ("icing", "moderate_plus"): "ICE",
    ("turbulence", "moderate_plus"): "TURB",
    ("cloud", "in_cloud"): "IMC",
    ("cloud", "in_and_out"): "IMC",
    ("showers", "build_ups"): "TS",
}

#: A flight that ended longer ago than this is frozen on any refresh.
FREEZE_AFTER = timedelta(hours=24)

_RANK = {"amber": 1, "red": 2}


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# --- Pure derivations ---------------------------------------------------------


def worst_status(statuses) -> str | None:
    """``red`` / ``amber`` / None over status strings (green etc. ignored)."""
    best = None
    for s in statuses:
        if s in _RANK and (best is None or _RANK[s] > _RANK[best]):
            best = s
    return best


def forecast_tags_from_manifest(manifest: dict) -> dict[str, str]:
    """Worst aggregate status per archive tag, amber or worse only."""
    out: dict[str, str] = {}
    for adv in manifest.get("advisories") or []:
        tag = ARCHIVE_TAG_MAP.get(adv.get("advisory_id") or "")
        status = (adv.get("aggregate_status") or "").lower()
        if tag is None or status not in _RANK:
            continue
        cur = out.get(tag.value)
        if cur is None or _RANK[status] > _RANK[cur]:
            out[tag.value] = status
    return out


def reported_tags(decision: str | None, reasons: list[str], answers) -> list[str]:
    """Sorted tags the pilot reported: cancel reasons for a cancelled flight,
    else the answers that say the hazard was there (:data:`ANSWER_TAGS`)."""
    tags: set[str] = set()
    if decision == "cancelled":
        tags.update(r for r in reasons if r)
    for a in answers:
        tag = ANSWER_TAGS.get((a.get("kind") or "", a.get("answer") or ""))
        if tag:
            tags.add(tag)
    return sorted(tags)


def haversine_nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(min(1.0, math.sqrt(h)))


def route_fields(points: list[tuple[str, float, float]]) -> dict:
    """Route columns from resolved ``(code, lat, lon)`` waypoints."""
    line = [[round(lat, 3), round(lon, 3)] for _, lat, lon in points]
    dist = sum(haversine_nm((a[1], a[2]), (b[1], b[2])) for a, b in zip(points, points[1:]))
    return {
        "departure_icao": points[0][0].upper(),
        "destination_icao": points[-1][0].upper(),
        "departure_lat": points[0][1],
        "departure_lon": points[0][2],
        "destination_lat": points[-1][1],
        "destination_lon": points[-1][2],
        "route_line_json": json.dumps(line),
        "distance_nm": round(dist, 1),
    }


# --- Reading the flight ---------------------------------------------------------


def _codes(flight: FlightRow) -> list[str]:
    try:
        wps = json.loads(flight.waypoints_json or "[]")
    except (TypeError, ValueError):
        return []
    return [str(w).upper() for w in wps if w]


def _route_from_packs(packs) -> list[tuple[str, float, float]] | None:
    from weatherbrief.tasks.artifacts import load_briefing
    from weatherbrief.tasks.live_trail import route_context

    for _, pack_dir in packs:
        if pack_dir is None or not pack_dir.is_dir():
            continue
        route, _ = route_context(load_briefing(pack_dir) or {})
        if route is not None:
            return [(w.icao, w.lat, w.lon) for w in route.waypoints]
    return None


def _route_from_db(codes: list[str], airports_db_path: str | None) -> list[tuple[str, float, float]] | None:
    path = airports_db_path or os.environ.get("AIRPORTS_DB", "")
    if len(codes) < 2 or not path:
        return None
    try:
        from weatherbrief.airports import resolve_waypoints

        resolved, _ = resolve_waypoints(codes, path)
        return [(w.icao, w.lat, w.lon) for w in resolved] if len(resolved) >= 2 else None
    except Exception:
        logger.info("Flight facts: could not resolve route %s", codes, exc_info=True)
        return None


def _departed_at(flight: FlightRow, packs, now: datetime, cancelled: bool) -> datetime:
    """The departure ``default_forecast_pack`` is chosen from, as the debrief
    report and recap choose it: the effective one, planned when cancelled."""
    from weatherbrief.tasks.live_recap import flight_progress_for

    try:
        progress = flight_progress_for(packs, now)
        if progress is not None:
            if cancelled:
                progress = progress.planned()
            if progress.departed_at is not None:
                return progress.departed_at
    except Exception:
        logger.info("Flight facts: no progress for %s", flight.id, exc_info=True)
    return _utc(flight.departure_time)


def _get_or_new(db: Session, flight_id: str, now: datetime) -> FlightFactsRow:
    row = db.get(FlightFactsRow, flight_id)
    if row is None:
        row = FlightFactsRow(flight_id=flight_id, tags_recorded=False, debriefed=False,
                             interesting=False, differs_from_forecast=False, updated_at=now)
        db.add(row)
    return row


def _apply_outcome(db: Session, row: FlightFactsRow, flight_id: str) -> None:
    from weatherbrief.storage.debriefs import get_debrief, load_answers

    debrief = get_debrief(db, flight_id)
    decision = debrief.decision.value if debrief is not None else None
    reasons = [t.value for t in debrief.reasons] if debrief is not None else []
    row.decision = decision
    row.debriefed = debrief is not None
    row.reported_tags_json = json.dumps(reported_tags(decision, reasons, load_answers(db, flight_id).values()))
    report = db.get(FlightDebriefReportRow, flight_id)
    row.interesting = bool(report.interesting) if report is not None else False
    row.differs_from_forecast = bool(report.differs_from_forecast) if report is not None else False


def _apply_codes_only(row: FlightFactsRow, flight: FlightRow) -> None:
    codes = _codes(flight)
    if codes and row.departure_icao is None:
        row.departure_icao = codes[0]
        row.destination_icao = codes[-1] if len(codes) >= 2 else None


# --- Writers -----------------------------------------------------------------------


def update_flight_outcome(db: Session, flight_id: str, *, now: datetime | None = None) -> FlightFactsRow | None:
    """Reported tags and outcome only (debrief saves): no pack is read."""
    now = now or datetime.now(timezone.utc)
    flight = db.get(FlightRow, flight_id)
    if flight is None:
        return None
    row = _get_or_new(db, flight_id, now)
    _apply_codes_only(row, flight)
    _apply_outcome(db, row, flight_id)
    row.updated_at = now
    db.flush()
    return row


def update_flight_facts(
    db: Session,
    flight_id: str,
    *,
    now: datetime | None = None,
    freeze: bool = False,
    airports_db_path: str | None = None,
) -> FlightFactsRow | None:
    """Refresh the whole record (module docstring). None when the flight is gone."""
    from weatherbrief.debriefs.report import load_manifest
    from weatherbrief.tasks.live_recap import default_forecast_pack, flight_packs

    now = now or datetime.now(timezone.utc)
    flight = db.get(FlightRow, flight_id)
    if flight is None:
        return None
    row = _get_or_new(db, flight_id, now)
    packs = flight_packs(db, flight_id)

    points = _route_from_packs(packs)
    if points is None and row.route_line_json is None:
        points = _route_from_db(_codes(flight), airports_db_path)
    if points is not None:
        for k, v in route_fields(points).items():
            setattr(row, k, v)
    else:
        _apply_codes_only(row, flight)

    _apply_outcome(db, row, flight_id)

    if row.forecast_frozen_at is None:
        departed = _departed_at(flight, packs, now, cancelled=row.decision == "cancelled")
        chosen = default_forecast_pack(packs, departed)
        read_ok = False  # this run read the chosen pack (not an earlier one)
        if chosen is not None:
            manifest = load_manifest(chosen[1])
            if manifest is not None:
                row.forecast_tags_json = json.dumps(forecast_tags_from_manifest(manifest), sort_keys=True)
                row.forecast_pack_ts = chosen[0]
                row.tags_recorded = True
                read_ok = True
        ended = _utc(flight.departure_time) + timedelta(hours=flight.flight_duration_hours or 0.0)
        # Never freeze over an unreadable chosen pack: a later run (or the
        # retention guard) retries it. No pack before departure freezes as
        # "not recorded", since none can appear later.
        readable = chosen is None or read_ok
        if departed <= now and readable and (freeze or now - ended > FREEZE_AFTER):
            row.forecast_frozen_at = now

    row.updated_at = now
    db.flush()
    return row


def safe_update_flight_facts(db: Session, flight_id: str, **kwargs) -> bool:
    """:func:`update_flight_facts` in a savepoint that never raises: the
    callers (pack build, window close) must not fail on the record."""
    try:
        with db.begin_nested():
            update_flight_facts(db, flight_id, **kwargs)
        return True
    except Exception:
        logger.warning("Flight facts update failed for %s", flight_id, exc_info=True)
        return False


def safe_update_flight_outcome(db: Session, flight_id: str) -> bool:
    try:
        with db.begin_nested():
            update_flight_outcome(db, flight_id)
        return True
    except Exception:
        logger.warning("Flight facts outcome update failed for %s", flight_id, exc_info=True)
        return False


# --- Reading ---------------------------------------------------------------------------


def facts_dict(row: FlightFactsRow | None) -> dict:
    """Plain-dict view of a row for the archive (empty record when None)."""
    if row is None:
        return {"forecast_tags": {}, "reported_tags": [], "tags_recorded": False, "decision": None,
                "debriefed": False, "interesting": False, "differs_from_forecast": False,
                "departure_icao": None, "destination_icao": None, "dep": None, "dest": None,
                "line": None, "distance_nm": None}

    def _load(raw, default):
        try:
            return json.loads(raw) if raw else default
        except (TypeError, ValueError):
            return default

    dep = (row.departure_lat, row.departure_lon) if row.departure_lat is not None else None
    dest = (row.destination_lat, row.destination_lon) if row.destination_lat is not None else None
    return {
        "forecast_tags": _load(row.forecast_tags_json, {}),
        "reported_tags": _load(row.reported_tags_json, []),
        "tags_recorded": bool(row.tags_recorded),
        "decision": row.decision,
        "debriefed": bool(row.debriefed),
        "interesting": bool(row.interesting),
        "differs_from_forecast": bool(row.differs_from_forecast),
        "departure_icao": row.departure_icao,
        "destination_icao": row.destination_icao,
        "dep": dep,
        "dest": dest,
        "line": _load(row.route_line_json, None),
        "distance_nm": row.distance_nm,
    }

