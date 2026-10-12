"""Debrief observed recap (#711): what the instruments saw along the route at
the time the aircraft was there, computed on request.

Inputs, all on disk:

- the per-tick display snapshots (``live_snapshots.py``);
- the live history's ``event`` records, for the alert markers;
- a pack's forecast lane (``forecast_lane.json``, written beside
  ``route_advisories.json``), for the forecast row.

Nothing is stored: the recap is rebuilt from about 35 snapshot lines per
request, so a corrected take-off or a new fix (#759) applies on the next read.

Where the aircraft was comes only from ``FlightProgress``: ``progress.eta``
for each segment, ``progress.flown_at`` for each alert marker. No schedule
arithmetic here. A cancelled flight's recap uses ``progress.planned()``.

For each ribbon segment the recap picks the snapshot whose **radar frame valid
time** is nearest the aircraft's time at the segment's midpoint (the tick time
lags the radar by 6-15 min, more than a segment at cruise speed). A snapshot
without a radar frame is matched on its tick time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.tasks.live_snapshots import parse_utc

logger = logging.getLogger(__name__)

FORECAST_LANE_FILE = "forecast_lane.json"
FORECAST_LANE_VERSION = 1

#: A segment the aircraft reaches more than this after the newest recorded
#: radar frame (or before the oldest) has no observation: it reads
#: ``not_reached`` / ``before_recording``, never the nearest tick's echo.
MATCH_SLACK = timedelta(minutes=20)

RECAP_NOTE = (
    "Observed along the route at the time the aircraft was there, on the "
    "times named by time_base (planned = the flight plan, not a recorded "
    "track). Each segment comes from the radar frame nearest that time. "
    "no_coverage / no_sample / unavailable mean the source could not see "
    "there, not that it was clear. Observations only: the forecast lane is "
    "the pack's own grading at its planned times."
)

STATUS_MESSAGES = {
    # Also what a flight reads once retention removed its live record: the two
    # cannot be told apart, so the message does not claim which.
    "no_live_window": "No observations recorded for this flight (no live window, or removed by retention).",
    "not_recorded": "Observations were not recorded for this flight (flown before per-tick recording started).",
    "untimed": "No departure time or duration to place the flight along the route.",
}


# --- Forecast lane ------------------------------------------------------------


def forecast_lane_summary(manifest) -> dict:
    """The per-segment forecast of a pack: each advisory's ``highlights.ribbon``
    bands per model, with the aggregate. A few KB; every advisory and every
    model is kept until #769 settles what the lane shows."""

    def band(seg) -> list:
        sev = getattr(seg.severity, "value", seg.severity)
        return [round(seg.dist_from_nm, 1), round(seg.dist_to_nm, 1), sev]

    advisories = []
    for adv in manifest.advisories:
        advisories.append({
            "advisory_id": adv.advisory_id,
            "aggregate_status": getattr(adv.aggregate_status, "value", adv.aggregate_status),
            "representative_model": adv.representative_model,
            "per_model": [
                {
                    "model": m.model,
                    "status": getattr(m.status, "value", m.status),
                    "ribbon": [band(s) for s in (m.highlights.ribbon if m.highlights else [])],
                }
                for m in adv.per_model
            ],
        })
    return {
        "v": FORECAST_LANE_VERSION,
        "route_nm": manifest.total_distance_nm,
        "cruise_altitude_ft": manifest.cruise_altitude_ft,
        "models": list(manifest.models),
        "aggregation": manifest.aggregation,
        "advisories": advisories,
    }


def write_forecast_lane(pack_dir: Path | str, manifest) -> None:
    """Write the pack's forecast lane beside ``route_advisories.json``. Never
    raises: a lane problem must not fail the advisories."""
    try:
        path = Path(pack_dir) / FORECAST_LANE_FILE
        path.write_text(json.dumps(forecast_lane_summary(manifest), separators=(",", ":")))
    except Exception:
        logger.warning("Forecast lane write failed for %s", pack_dir, exc_info=True)


def load_forecast_lane(pack_dir: Path | str | None) -> dict | None:
    """The pack's forecast lane, built from ``route_advisories.json`` for a
    pack written before the lane file existed. None when neither is there."""
    if pack_dir is None:
        return None
    pack_dir = Path(pack_dir)
    path = pack_dir / FORECAST_LANE_FILE
    try:
        if path.exists():
            return json.loads(path.read_text())
        adv = pack_dir / "route_advisories.json"
        if not adv.exists():
            return None
        from weatherbrief.models.advisories import RouteAdvisoriesManifest

        return forecast_lane_summary(RouteAdvisoriesManifest.model_validate_json(adv.read_text()))
    except Exception:
        logger.warning("Unreadable forecast lane for %s", pack_dir, exc_info=True)
        return None


def default_forecast_pack(
    packs: list[tuple[datetime, Path | None]], departed_at: datetime | None,
) -> tuple[datetime, Path | None] | None:
    """The newest pack fetched before ``departed_at`` (``FlightProgress``'s
    effective departure; planned for a cancelled flight). None when no pack
    predates it, or the flight has no departure."""
    if departed_at is None:
        return None
    before = [p for p in packs if p[0] < departed_at]
    return max(before, key=lambda p: p[0]) if before else None


def forecast_block(pack_ts: datetime, pack_dir: Path | None, *, chosen_by: str) -> dict:
    """The forecast row for one pack, with that pack's planned departure (its
    advisories are graded at its own planned ETAs, so the UI can show the gap)."""
    from weatherbrief.tasks.artifacts import load_briefing, parse_target_time

    planned = None
    if pack_dir is not None:
        try:
            planned = parse_target_time(load_briefing(pack_dir) or {})
        except Exception:
            planned = None
    lane = load_forecast_lane(pack_dir)
    return {
        "pack_timestamp": pack_ts.isoformat(),
        "chosen_by": chosen_by,
        "planned_departure": planned.isoformat() if planned else None,
        "available": lane is not None,
        **(lane or {}),
    }


# --- Observed lane ------------------------------------------------------------


def _key_time(snap: dict) -> datetime | None:
    return parse_utc(snap.get("radar_time")) or parse_utc(snap.get("tick_at"))


def _segment_at(ribbon: dict, along: float) -> dict | None:
    segs = ribbon.get("segments") or []
    for i, s in enumerate(segs):
        last = i == len(segs) - 1
        if s.get("from_nm", 0) <= along < s.get("to_nm", 0) or (last and along <= s.get("to_nm", 0)):
            return s
    return None


def _overlaps(lo: float, hi: float, a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return min(a, b) < hi and max(a, b) > lo


_SEGMENT_FIELDS = (
    "radar_max_dbz", "radar_intensity", "radar_status", "lightning", "sigmet_ids", "storm_ids",
)


def _observed_segments(snapshots: list[dict], progress) -> tuple[list[dict], list[dict], list[dict], dict | None]:
    """(segments, weather bands, storms, base ribbon) along the diagonal."""
    with_ribbon = [s for s in snapshots if s.get("ribbon") and _key_time(s) is not None]
    if not with_ribbon:
        return [], [], [], None
    base = with_ribbon[-1]["ribbon"]
    times = [_key_time(s) for s in with_ribbon]
    oldest, newest = min(times), max(times)

    segments, weather, storms = [], [], []
    seen_bands: set[tuple] = set()
    seen_storms: set[tuple] = set()
    for seg in base.get("segments") or []:
        lo, hi = seg.get("from_nm", 0.0), seg.get("to_nm", 0.0)
        mid = (lo + hi) / 2
        at = progress.eta(mid)
        out = {"index": seg.get("index"), "from_nm": lo, "to_nm": hi, "at": at.isoformat() if at else None}
        if at is None:
            segments.append({**out, "status": "untimed"})
            continue
        if at > newest + MATCH_SLACK:
            segments.append({**out, "status": "not_reached"})
            continue
        if at < oldest - MATCH_SLACK:
            segments.append({**out, "status": "before_recording"})
            continue
        i = min(range(len(with_ribbon)), key=lambda k: abs((times[k] - at).total_seconds()))
        snap = with_ribbon[i]
        ribbon = snap["ribbon"]
        src = _segment_at(ribbon, mid) or {}
        out.update({
            "status": "observed",
            "tick_at": snap.get("tick_at"),
            "radar_time": snap.get("radar_time"),
            "pack_timestamp": snap.get("pack_timestamp"),
            "gap_min": round((times[i] - at).total_seconds() / 60.0, 1),
            "weather_status": ribbon.get("weather_status"),
            **{k: src.get(k) for k in _SEGMENT_FIELDS},
        })
        if not src:
            out["radar_status"] = "no_sample"
        segments.append(out)
        for b in ribbon.get("weather") or []:
            if _overlaps(lo, hi, b.get("from_nm"), b.get("to_nm")):
                key = (snap.get("tick_at"), b.get("id"))
                if key not in seen_bands:
                    seen_bands.add(key)
                    weather.append({**b, "tick_at": snap.get("tick_at"), "segment_index": out["index"]})
        for st in ((snap.get("storms") or {}).get("storms") or []):
            along = st.get("along_nm")
            if along is not None and lo <= along < hi:
                key = (snap.get("tick_at"), st.get("id"))
                if key not in seen_storms:
                    seen_storms.add(key)
                    storms.append({
                        k: st.get(k) for k in (
                            "id", "lat", "lon", "peak_dbz", "intensity", "flashes", "top_fl",
                            "trend", "along_nm", "offtrack_nm", "cross_nm", "side", "relative_motion",
                        )
                    } | {"tick_at": snap.get("tick_at"), "segment_index": out["index"]})
    return segments, weather, storms, base


def alert_markers(history: list[dict], progress) -> list[dict]:
    """One marker per change raised or cleared over the flight day (the
    history ``event`` records), placed at the aircraft's position then."""
    out = []
    for r in history:
        if r.get("type") != "event":
            continue
        c = r.get("change") or {}
        at = parse_utc(r.get("tick_at"))
        flown = progress.flown_at(at) if (progress is not None and at is not None) else None
        out.append({
            "at": r.get("tick_at"),
            "event": "raised" if r.get("event") == "appeared" else "cleared",
            "tier": c.get("tier"),
            "direction": c.get("direction"),
            "key": c.get("key"),
            "kind": c.get("kind"),
            "role": c.get("role"),
            "icao": c.get("icao"),
            "message": c.get("message"),
            "observed_at": c.get("observed_at"),
            "enroute_distance_nm": c.get("enroute_distance_nm"),
            "aircraft_nm": round(flown, 1) if flown is not None else None,
        })
    return out


def build_recap(
    snapshots: list[dict],
    history: list[dict],
    progress,
    *,
    cancelled: bool = False,
    forecast: dict | None = None,
) -> dict:
    """The recap from its inputs. ``progress`` is the flight's
    ``FlightProgress`` (None without a route); ``forecast`` the chosen pack's
    :func:`forecast_block`, or None."""
    if cancelled and progress is not None:
        progress = progress.planned()
    out: dict = {
        "note": RECAP_NOTE,
        "cancelled": cancelled,
        "time_base": progress.time_base if progress is not None else None,
        "progress_source": progress.source if progress is not None else None,
        "departed_at": progress.departed_at.isoformat() if progress is not None and progress.departed_at else None,
        "arrival_at": progress.arrival.isoformat() if progress is not None and progress.arrival else None,
        "ticks": len(snapshots),
        "first_tick_at": snapshots[0].get("tick_at") if snapshots else None,
        "last_tick_at": snapshots[-1].get("tick_at") if snapshots else None,
        "forecast": forecast,
    }
    if not snapshots:
        status = "not_recorded" if history else "no_live_window"
    elif progress is None or not progress.timed:
        status = "untimed"
    else:
        status = "ok"
    out["status"] = status
    out["message"] = STATUS_MESSAGES.get(status)
    out["alerts"] = alert_markers(history, progress if status == "ok" else None)
    if status != "ok":
        out.update(segments=[], weather=[], storms=[], waypoints=[], route_nm=None, segment_nm=None)
        return out

    segments, weather, storms, base = _observed_segments(snapshots, progress)
    out.update(
        route_nm=base.get("route_nm") if base else None,
        segment_nm=base.get("segment_nm") if base else None,
        waypoints=[
            {**wp, "eta": (lambda t: t.isoformat() if t else None)(progress.eta(wp.get("along_nm")))}
            for wp in ((base or {}).get("waypoints") or [])
        ],
        segments=segments,
        weather=weather,
        storms=storms,
    )
    return out


# --- Compact form for agents -------------------------------------------------

RECAP_SUMMARY_MAX_ALERTS = 12


def summarize_recap(recap: dict) -> dict | None:
    """The compact ``recap`` block next to ``live`` in ``get_briefing``.
    None when there is nothing recorded to say (never an empty "clear")."""
    if recap.get("status") in (None, "no_live_window"):
        return None
    out = {
        "note": recap.get("note"),
        "status": recap.get("status"),
        "message": recap.get("message"),
        "time_base": recap.get("time_base"),
        "cancelled": recap.get("cancelled"),
        "departed_at": recap.get("departed_at"),
        "arrival_at": recap.get("arrival_at"),
    }
    if recap.get("status") != "ok":
        return out
    segs = recap.get("segments") or []
    observed = [s for s in segs if s.get("status") == "observed"]
    measured = [s for s in observed if s.get("radar_status") == "measured" and s.get("radar_max_dbz") is not None]
    worst = max(measured, key=lambda s: s["radar_max_dbz"], default=None)
    out.update({
        "segments_total": len(segs),
        "segments_observed": len(observed),
        "segments_radar_unavailable": sum(1 for s in observed if s.get("radar_status") != "measured"),
        "segments_not_reached": sum(1 for s in segs if s.get("status") == "not_reached"),
        "max_radar": None if worst is None else {
            "dbz": worst["radar_max_dbz"], "intensity": worst.get("radar_intensity"),
            "from_nm": worst["from_nm"], "to_nm": worst["to_nm"], "at": worst.get("at"),
        },
        "lightning_segments": sum(1 for s in observed if s.get("lightning")),
        "storms_seen": len({st.get("id") for st in recap.get("storms") or []}),
        "sigmets_en_route": sorted({i for s in observed for i in (s.get("sigmet_ids") or [])}),
    })
    alerts = [a for a in recap.get("alerts") or [] if a.get("tier") == "alert"]
    out["alerts_total"] = len(alerts)
    out["alerts"] = [
        {k: a.get(k) for k in ("at", "event", "direction", "message", "aircraft_nm")}
        for a in alerts[:RECAP_SUMMARY_MAX_ALERTS]
    ]
    fc = recap.get("forecast")
    out["forecast_pack"] = fc.get("pack_timestamp") if fc else None
    return out


# --- Per flight (API and agents) --------------------------------------------


def flight_packs(db, flight_id: str) -> list[tuple[datetime, Path | None]]:
    """(fetch timestamp, pack dir) of every pack of the flight, newest first."""
    from weatherbrief.db.models import FlightRow
    from weatherbrief.storage.flights import _resolve_artifact_path, ensure_utc, list_packs, pack_dir_for

    row = db.get(FlightRow, flight_id)
    out = []
    for p in list_packs(db, flight_id):
        ts = ensure_utc(p.fetch_timestamp)
        if p.artifact_path:
            pack_dir = Path(_resolve_artifact_path(p.artifact_path))
        else:
            # Same fallback as the pack endpoints' ``_get_pack_dir``.
            pack_dir = Path(pack_dir_for(row.user_id, flight_id, ts)) if row is not None else None
        out.append((ts, pack_dir))
    return out


def flight_dir_of(packs: list[tuple[datetime, Path | None]]) -> Path | None:
    """The flight directory the live files sit in (any pack's parent)."""
    return next((d.parent for _, d in packs if d is not None), None)


def flight_progress_for(packs: list[tuple[datetime, Path | None]], as_of: datetime):
    """The flight's ``FlightProgress`` from its newest readable pack (the
    route and departure the live layer last measured against)."""
    from weatherbrief.tasks.artifacts import load_briefing
    from weatherbrief.tasks.live_layer import flight_progress
    from weatherbrief.tasks.live_trail import route_context

    for _, pack_dir in packs:
        if pack_dir is None or not pack_dir.is_dir():
            continue
        route, departure = route_context(load_briefing(pack_dir) or {})
        if route is not None:
            return flight_progress(route, departure, as_of)
    return None


def _match_pack(packs, pack_ts: str) -> tuple[datetime, Path | None]:
    want = parse_utc(pack_ts)
    for p in packs:
        if want is not None and p[0] == want:
            return p
    raise KeyError(pack_ts)


def recap_for_flight(
    db, flight_id: str, *, pack_ts: str | None = None, now: datetime | None = None,
    packs: list[tuple[datetime, Path | None]] | None = None, progress=None,
) -> dict:
    """The recap of ``flight_id`` with the forecast row from ``pack_ts`` (or
    the default pack). Raises ``KeyError`` for a ``pack_ts`` the flight lacks.
    ``packs`` / ``progress`` skip resolving them again when the caller has
    them (:func:`recap_summary`)."""
    from weatherbrief.debriefs.taxonomy import Decision
    from weatherbrief.storage.debriefs import get_debrief
    from weatherbrief.tasks.live_layer import load_live_history
    from weatherbrief.tasks.live_snapshots import load_snapshots, snapshots_capped

    now = now or datetime.now(timezone.utc)
    packs = packs if packs is not None else flight_packs(db, flight_id)
    flight_dir = flight_dir_of(packs)
    debrief = get_debrief(db, flight_id)
    cancelled = debrief is not None and debrief.decision == Decision.CANCELLED
    progress = progress if progress is not None else flight_progress_for(packs, now)
    # Also applied inside build_recap (the pure entry point); needed here
    # first because the default forecast pack is chosen from this departure.
    if cancelled and progress is not None:
        progress = progress.planned()

    if pack_ts:
        ts, pack_dir = _match_pack(packs, pack_ts)
        forecast = forecast_block(ts, pack_dir, chosen_by="request")
    else:
        chosen = default_forecast_pack(packs, progress.departed_at if progress is not None else None)
        forecast = forecast_block(*chosen, chosen_by="default") if chosen else None

    snapshots = load_snapshots(flight_dir) if flight_dir is not None else []
    history = load_live_history(flight_dir) if flight_dir is not None else []
    recap = build_recap(snapshots, history, progress, cancelled=cancelled, forecast=forecast)
    # Ticks past the size cap were not recorded: "not_reached" after the last
    # snapshot then means "not recorded", and the recap says so.
    recap["capped"] = flight_dir is not None and snapshots_capped(flight_dir)
    if recap["capped"]:
        recap["message"] = " ".join(filter(None, [
            recap.get("message"),
            "Recording stopped at the size cap after the last tick; later segments were not recorded.",
        ]))
    recap["flight_id"] = flight_id
    recap["packs"] = [ts.isoformat() for ts, _ in packs]
    return recap


def recap_summary(db, flight_id: str) -> dict | None:
    """:func:`summarize_recap` for the flight, or None. Never raises: a recap
    problem must not fail a briefing read. Only once the flight's (planned or
    observed) departure has passed: before it there is no diagonal yet."""
    from weatherbrief.tasks.live_layer import LIVE_HISTORY_FILE
    from weatherbrief.tasks.live_snapshots import has_snapshots

    try:
        now = datetime.now(timezone.utc)
        # Cheap checks first: this runs on every agent get_briefing.
        packs = flight_packs(db, flight_id)
        flight_dir = flight_dir_of(packs)
        progress = flight_progress_for(packs, now)
        departed = progress.departed_at if progress is not None else None
        if flight_dir is None or departed is None or departed > now:
            return None
        if not has_snapshots(flight_dir) and not (flight_dir / LIVE_HISTORY_FILE).exists():
            return None
        return summarize_recap(recap_for_flight(db, flight_id, now=now, packs=packs, progress=progress))
    except Exception:
        logger.warning("Recap summary failed for %s", flight_id, exc_info=True)
        return None
