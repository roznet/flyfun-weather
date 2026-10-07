"""Per-flight live observation store (#637).

Layout: ``DATA_DIR/packs/{user}/{flight}/live.json`` (+ a tiny
``live_meta.json``), i.e. next to the flight's pack directories rather than in
any one of them. The pack stays immutable — ``briefing.json`` keeps the
observations the assessment saw, which is exactly the baseline the significance
classifier needs — and the store survives a new full pack only long enough to
be recognised as stale: it records the pack it is relative to, and readers
ignore a layer whose pack is not the one they are showing.

Writers are the cheap realtime refresh (a D-0 ↻ press) and the server
live-window tick. Both go through :func:`commit_live_update`, which holds a
per-flight lock across read-classify-write so the alert memory is never lost to
a race, refuses a write for an older pack than the one already stored (the
baseline switch on a full refresh is therefore atomic from a reader's point of
view), and writes via temp file + ``os.replace``.

``live_meta.json`` exists so list endpoints can surface ``live_updated_at``
without parsing a payload that carries the full observed-conditions block.

``live_history.jsonl`` (#643) is the append-only timeline beside them: what
the pilot was shown, and when, over the whole flight day (pack switches
included). See :func:`_history_records` for the record types.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.live import (
    TRAIL_EXCLUDE,
    LiveChange,
    LiveChanges,
    LiveEvidencePoint,
    LiveGlance,
    LiveLayer,
    LiveRibbon,
    LiveStorms,
)
from weatherbrief.models.observations import RouteObservations, RouteSigmets
from weatherbrief.models.observed import ObservedConditions
from weatherbrief.observed.storms import CellFrames

logger = logging.getLogger(__name__)

LIVE_FILE = "live.json"
LIVE_META_FILE = "live_meta.json"
LIVE_HISTORY_FILE = "live_history.jsonl"
#: #697's review log — defined in ``live_highlight`` and listed here so a
#: flight delete takes it with the rest of the layer.
LIVE_HIGHLIGHT_LOG = "live_highlights.jsonl"
#: Every per-flight live file: what flight delete/move and retention remove.
LIVE_FILES = (LIVE_FILE, LIVE_META_FILE, LIVE_HISTORY_FILE, LIVE_HIGHLIGHT_LOG)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def flight_dir_for_pack(pack_dir: Path | str) -> Path:
    """The per-flight directory a pack lives in (``.../{user}/{flight}``)."""
    return Path(pack_dir).parent


def _lock_for(flight_dir: Path) -> threading.Lock:
    key = str(flight_dir)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _parse_dt(value) -> datetime | None:
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


# --- Read -------------------------------------------------------------------


def load_live(flight_dir: Path | str) -> LiveLayer | None:
    """The stored layer, or None (absent, or unreadable — logged, not raised:
    a corrupt live file must never take the briefing down with it)."""
    path = Path(flight_dir) / LIVE_FILE
    if not path.exists():
        return None
    try:
        return LiveLayer.model_validate_json(path.read_text())
    except Exception:
        logger.warning("Unreadable live layer %s — ignoring", path, exc_info=True)
        return None


def load_live_meta(flight_dir: Path | str) -> dict | None:
    path = Path(flight_dir) / LIVE_META_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def live_for_pack(pack_dir: Path | str) -> LiveLayer | None:
    """The layer if it is relative to *this* pack, else None."""
    pack_dir = Path(pack_dir)
    layer = load_live(flight_dir_for_pack(pack_dir))
    if layer is None or layer.pack_dir_name != pack_dir.name:
        return None
    return layer


def live_updated_at_for_pack(pack_dir: Path | str | None) -> datetime | None:
    """``live_updated_at`` when the flight's live layer belongs to this pack.

    Reads only the small meta file. None for any pack that is not the layer's
    base (older packs never show live data).
    """
    if not pack_dir:
        return None
    pack_dir = Path(pack_dir)
    meta = load_live_meta(flight_dir_for_pack(pack_dir))
    if not meta or meta.get("pack_dir_name") != pack_dir.name:
        return None
    return _parse_dt(meta.get("live_updated_at"))


def live_updated_at_iso(artifact_path: str | None) -> str | None:
    """ISO ``live_updated_at`` for a stored ``artifact_path``, or None.

    The one helper behind every pack-meta / flight-list field: resolves the
    path against the current DATA_DIR and never raises (a live-layer problem
    must not fail a listing).
    """
    if not artifact_path:
        return None
    try:
        from weatherbrief.storage.flights import _resolve_artifact_path

        ts = live_updated_at_for_pack(_resolve_artifact_path(artifact_path))
        return ts.isoformat() if ts is not None else None
    except Exception:
        logger.warning("live_updated_at lookup failed for %s", artifact_path, exc_info=True)
        return None


def overlay_live(briefing_data: dict, layer: LiveLayer | None) -> dict:
    """Briefing JSON with the live blocks laid over the pack's own.

    Keeps clients that only read the snapshot (older app versions, the HTML
    report) on the newest observations, the way the in-place patch used to.
    The on-disk ``briefing.json`` is never touched.
    """
    if layer is None:
        return briefing_data
    out = dict(briefing_data)
    if layer.route_observations is not None:
        out["route_observations"] = layer.route_observations.model_dump(mode="json")
    if layer.route_sigmets is not None:
        out["route_sigmets"] = layer.route_sigmets.model_dump(mode="json")
    if layer.observed_conditions is not None:
        out["observed_conditions"] = layer.observed_conditions.model_dump(mode="json")
    if layer.last_refresh_delta is not None:
        out["last_refresh_delta"] = layer.last_refresh_delta.model_dump(mode="json")
    if layer.changes is not None:
        # Never the read-time trail (#669): the overlay is what it was.
        out["live_changes"] = layer.changes.model_dump(mode="json", exclude=TRAIL_EXCLUDE)
    if layer.live_updated_at is not None:
        out["live_updated_at"] = layer.live_updated_at.isoformat()
    return out


def load_briefing_with_live(pack_dir: Path | str) -> dict | None:
    """``load_briefing`` plus this pack's live layer, if any."""
    from weatherbrief.tasks.artifacts import load_briefing

    pack_dir = Path(pack_dir)
    data = load_briefing(pack_dir)
    if data is None:
        return None
    return overlay_live(data, live_for_pack(pack_dir))


def load_live_history(flight_dir: Path | str) -> list[dict]:
    """The flight's history records, oldest first (empty when none).

    A line that does not parse (a write cut short) is skipped, not raised.
    """
    path = Path(flight_dir) / LIVE_HISTORY_FILE
    if not path.exists():
        return []
    out = []
    try:
        text = path.read_text()
    except OSError:
        logger.warning("Unreadable live history %s — ignoring", path, exc_info=True)
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            logger.warning("Skipping unreadable line in %s", path)
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def remove_live(flight_dir: Path | str) -> None:
    """Delete a flight's live files (flight delete / move)."""
    for name in LIVE_FILES:
        try:
            (Path(flight_dir) / name).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s/%s", flight_dir, name, exc_info=True)


# --- Write ------------------------------------------------------------------


def _flown_nm(route: RouteConfig, departure: datetime | None, now: datetime) -> float | None:
    """Distance flown at ``now`` assuming departure on time and constant speed.

    Before departure this is 0 (everything is ahead); None when the route has
    no duration to interpolate on.
    """
    if departure is None or not route.flight_duration_hours:
        return None
    from weatherbrief.tasks.route_weather import _compute_route_distances

    dists = _compute_route_distances(route)
    total = dists[-1] if dists else 0.0
    frac = (now - departure).total_seconds() / 3600.0 / route.flight_duration_hours
    return max(0.0, min(1.0, frac)) * total


def planned_arrival(route: RouteConfig | None, departure: datetime | None) -> datetime | None:
    """Departure + ``flight_duration_hours``: the planned landing, or None
    when either is unknown (#689)."""
    if route is None or departure is None or not route.flight_duration_hours:
        return None
    return departure + timedelta(hours=route.flight_duration_hours)


def _build_storms(
    cells: CellFrames | None,
    route: RouteConfig | None,
    departure: datetime | None,
    briefing_data: dict,
    now: datetime,
) -> LiveStorms:
    """The radar storms against the route for this tick (#688). Never raises:
    a failure is an unavailable feed, which falls back to the station rows."""
    from weatherbrief.observed.storms import STORM_CORRIDOR_NM, Schedule, build_storms

    if route is None:
        return LiveStorms(status="unavailable", corridor_nm=STORM_CORRIDOR_NM)
    try:
        from weatherbrief.analysis.route_geometry import RouteTrack

        schedule = Schedule(RouteTrack.from_route(route), departure, route.flight_duration_hours)
        ends = (route.waypoints[0].icao, route.waypoints[-1].icao)
        return build_storms(
            cells or CellFrames("unavailable"), schedule,
            flown_nm=_flown_nm(route, departure, now), now=now, end_icaos=ends,
        )
    except Exception:
        logger.warning("Storm geometry failed — storms unavailable this tick", exc_info=True)
        return LiveStorms(status="unavailable", corridor_nm=STORM_CORRIDOR_NM)


def _build_glance(
    layer: LiveLayer,
    route: RouteConfig | None,
    departure: datetime | None,
    briefing_data: dict,
    now: datetime,
    cells: CellFrames | None = None,
) -> tuple[LiveGlance | None, LiveRibbon | None]:
    """The Observed tab's nutshell and ribbon for this tick (#690), and the
    storms' map focus. Never raises: a failure leaves both blocks null (the
    clients then show the details only) and the tick carries on."""
    if route is None:
        return None, None
    try:
        from weatherbrief.tasks.live_glance import build_glance

        return build_glance(
            layer, route, departure, alternate_icaos=_alternate_icaos(briefing_data),
            cell_frame=cells.newest if cells is not None else None, now=now,
        )
    except Exception:
        # One distinctive line to alert on: a systematic failure blanks the
        # Observed tab's top block on every tick, and the agent glance then
        # reads null as if not built yet.
        logger.exception("LIVE_GLANCE_FAILED flight=%s — glance/ribbon null this tick", layer.flight_id)
        return None, None


def route_destination(route: RouteConfig | None) -> tuple[float, float] | None:
    """(lat, lon) of the route's destination, for SIGMET-at-destination."""
    if route is None or not route.waypoints:
        return None
    dest = route.waypoints[-1]
    return dest.lat, dest.lon


def _alternate_icaos(briefing_data: dict) -> list[str]:
    alts = (briefing_data.get("alternates") or {}).get("alternates") or []
    return [a.get("icao") for a in alts if isinstance(a, dict) and a.get("icao")]


def _baseline_blocks(briefing_data: dict):
    def _v(model, key):
        raw = briefing_data.get(key)
        if not raw:
            return None
        try:
            return model.model_validate(raw)
        except Exception:
            logger.warning("Unreadable baseline %s — skipped", key, exc_info=True)
            return None

    return (
        _v(RouteObservations, "route_observations"),
        _v(RouteSigmets, "route_sigmets"),
        _v(ObservedConditions, "observed_conditions"),
    )


def _seed_missing_baselines(
    layer: LiveLayer,
    base_obs: RouteObservations | None,
    base_sigmets: RouteSigmets | None,
    base_observed: ObservedConditions | None,
    now: datetime,
):
    """Fill the baseline blocks the pack lacks from the layer's starting point.

    A pack built before flight day carries no observations or SIGMETs (they
    are D-0 only), so there is nothing to measure a change against. The first
    live fetch of such a block becomes its baseline, kept on the layer: no
    change on that tick, and changes from then on are "since live tracking
    began". Returns the four baselines plus whether the observations baseline
    (the one the panel's "since" refers to) came from the live start.

    ``seeded_at`` is when the *observations* seeded — it becomes the panel's
    ``baseline_at`` — so a SIGMET block seeding a tick earlier (the METAR fetch
    failed that tick) does not make "since live tracking began" too early.
    """
    if base_obs is None and layer.seeded_observations is None and layer.route_observations is not None:
        layer.seeded_observations = layer.route_observations.model_copy(deep=True)
        layer.seeded_at = now
    if base_sigmets is None and layer.seeded_sigmets is None and layer.route_sigmets is not None:
        layer.seeded_sigmets = layer.route_sigmets.model_copy(deep=True)
    if base_observed is None and layer.seeded_observed is None and layer.observed_conditions is not None:
        layer.seeded_observed = layer.observed_conditions.model_copy(deep=True)
    seeded = base_obs is None and layer.seeded_observations is not None
    return (
        base_obs if base_obs is not None else layer.seeded_observations,
        base_sigmets if base_sigmets is not None else layer.seeded_sigmets,
        base_observed if base_observed is not None else layer.seeded_observed,
        seeded,
    )


def _is_older_pack(candidate: str, than: str) -> bool:
    a, b = _parse_dt(candidate), _parse_dt(than)
    if a is None or b is None:
        return False
    return a < b


# --- History (#643) -----------------------------------------------------------


def change_identity(c: LiveChange) -> tuple:
    """A change is the same change while its key, direction, value and tier
    hold: a message-only difference (a SPECI suffix, a new detail) is not a
    new event. The scenario replay uses the same identity."""
    return (c.key, c.direction, c.to_value, c.tier)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _report_records(
    observations: RouteObservations | None, sigmets: RouteSigmets | None,
) -> list[tuple[tuple, dict]]:
    """(dedupe key, record body) for every raw report in these blocks.

    ``seen_at`` is the block's fetch time: when the report was known to us
    (the scenario rebuild uses it as the SIGMET issue time). It is written
    only when it differs from the record's ``tick_at`` (a pack's own
    baseline reports); readers default to ``tick_at``.
    """
    out: list[tuple[tuple, dict]] = []
    if observations is not None:
        seen = _iso(observations.fetch_time)
        for a in observations.airports:
            if a.metar_raw and a.metar_time is not None:
                out.append((("metar", a.icao, _iso(a.metar_time), a.metar_raw), {
                    "kind": "metar", "icao": a.icao, "seen_at": seen,
                    "report_type": a.metar_report_type, "observed_at": _iso(a.metar_time),
                    # For the trail's category strip (#669); files written
                    # before it are re-parsed from ``raw`` on read.
                    "flight_category": a.metar_flight_category,
                    "raw": a.metar_raw,
                }))
            if a.taf_raw:
                issued = _iso(a.taf_issue_time)
                out.append((("taf", a.icao, issued or a.taf_raw), {
                    "kind": "taf", "icao": a.icao, "seen_at": seen,
                    "issued_at": issued, "raw": a.taf_raw,
                }))
    if sigmets is not None:
        seen = _iso(sigmets.fetch_time)
        for sg in sigmets.sigmets:
            # The raw text carries the polygon, but nothing here parses raw
            # SIGMET text back, so the structured fields go along (a few
            # SIGMETs per flight, ~1 KB each).
            out.append((("sigmet", sg.fir_id, sg.raw_text or str(sg.valid_from)), {
                "kind": "sigmet", "fir_id": sg.fir_id, "seen_at": seen,
                "valid_from": _iso(sg.valid_from), "valid_to": _iso(sg.valid_to),
                "raw": sg.raw_text,
                "sigmet": sg.model_dump(mode="json", exclude={"raw_text"}),
            }))
    return out


def _report_key(rec: dict) -> tuple | None:
    kind = rec.get("kind")
    if kind == "metar":
        return ("metar", rec.get("icao"), rec.get("observed_at"), rec.get("raw"))
    if kind == "taf":
        return ("taf", rec.get("icao"), rec.get("issued_at") or rec.get("raw"))
    if kind == "sigmet":
        return ("sigmet", rec.get("fir_id"), rec.get("raw") or rec.get("valid_from"))
    return None


def _history_records(
    history: list[dict],
    shown: list[LiveChange],
    layer: LiveLayer,
    *,
    briefing_data: dict,
    observations: RouteObservations | None,
    sigmets: RouteSigmets | None,
    now: datetime,
) -> list[dict]:
    """What this tick adds to the history, in order.

    - ``pack``: the layer's pack is not the last one the history recorded
      (the first write, or a full refresh). The briefing's own observations
      and SIGMETs are recorded as reports with it, so its baseline can be
      rebuilt even if a report was superseded before the next tick.
    - ``report``: each METAR/SPECI, TAF and SIGMET the first time it is seen.
    - ``event``: each change appearing (``appeared``, with ``new_alert``) or
      clearing (``cleared``, carrying the last message shown) since the
      previous tick, regardless of pack: one timeline for the flight day.
    - ``evidence``: after a radar/lightning ``appeared`` event, the route
      points that triggered it.

    ``shown`` is the change list the history last recorded as on screen.
    """
    base = {"tick_at": now.isoformat(), "pack_timestamp": layer.pack_timestamp}
    out: list[dict] = []

    last_pack = next((r for r in reversed(history) if r.get("type") == "pack"), None)
    reports = _report_records(observations, sigmets)
    if last_pack is None or last_pack.get("pack_dir_name") != layer.pack_dir_name:
        base_obs, base_sigmets, _ = _baseline_blocks(briefing_data)
        out.append({
            **base, "type": "pack", "pack_dir_name": layer.pack_dir_name,
            "previous_pack_timestamp": last_pack.get("pack_timestamp") if last_pack else None,
            "has_observations": base_obs is not None,
            "corridor_nm": (observations or base_obs).corridor_nm if (observations or base_obs) else None,
            "sigmet_corridor_nm": (sigmets or base_sigmets).corridor_nm if (sigmets or base_sigmets) else None,
        })
        reports = _report_records(base_obs, base_sigmets) + reports

    seen = {_report_key(r) for r in history if r.get("type") == "report"}
    for key, body in reports:
        if key in seen:
            continue
        seen.add(key)
        if body["seen_at"] == base["tick_at"]:
            del body["seen_at"]  # the common case; readers default to tick_at
        out.append({**base, "type": "report", **body})

    current = layer.changes.changes if layer.changes is not None else []
    before = {change_identity(c): c for c in shown}
    after = {change_identity(c): c for c in current}
    last_evidence = _last_evidence(history)
    for ident, c in after.items():
        if ident in before:
            # Radar/lightning keep one identity while the echo lasts (#682):
            # record its evidence again when the peak or span moves enough.
            if c.evidence and _evidence_moved(c.evidence, last_evidence.get(c.key)):
                out.append(_evidence_record(base, c))
            continue
        out.append({**base, "type": "event", "event": "appeared", "change": c.model_dump(mode="json", exclude_none=True)})
        if c.evidence:
            out.append(_evidence_record(base, c))
    for ident, c in before.items():
        if ident not in after:
            out.append({**base, "type": "event", "event": "cleared", "change": c.model_dump(mode="json", exclude_none=True)})
    out += _estimate_records(base, history, layer)
    return out


#: Estimates logged per tick at most (nearest along-track first): bounds a
#: showery day's history growth (~300 B a record).
ESTIMATES_PER_TICK = 25


def _estimate_records(base: dict, history: list[dict], layer: LiveLayer) -> list[dict]:
    """One ``estimate`` record per storm with an estimate (#688 addendum):
    closest approach at current motion, for ``review.py score-estimates``.
    Once per storm per cell frame: a ↻ on the same frame adds nothing."""
    storms = layer.storms
    if storms is None or storms.status != "available" or storms.frame_time is None:
        return []
    frame = storms.frame_time.isoformat()
    logged = {(r.get("storm_id"), r.get("frame_time")) for r in history if r.get("type") == "estimate"}
    out: list[dict] = []
    for st in storms.storms:
        if st.estimate is None or (st.id, frame) in logged:
            continue
        out.append({
            **base, "type": "estimate", "storm_id": st.id, "cell_ids": st.cell_ids,
            "frame_time": frame, "lat": st.lat, "lon": st.lon,
            "peak_dbz": st.peak_dbz, "motion_status": st.motion_status,
            "speed_kt": st.speed_kt, "toward_deg": st.toward_deg,
            "along_nm": st.along_nm, "offtrack_nm": st.offtrack_nm, "cross_nm": st.cross_nm,
            "minutes_to_abeam": st.minutes_to_abeam,
            **st.estimate.model_dump(mode="json"),
        })
        if len(out) >= ESTIMATES_PER_TICK:
            break
    return out


#: A continuing radar/lightning change records its evidence again when the
#: peak echo moves by this much, or either end of its along-route span by
#: ``EVIDENCE_SPAN_NM`` (#682). Smaller moves are tick-to-tick noise.
EVIDENCE_PEAK_DBZ = 5.0
EVIDENCE_SPAN_NM = 10.0


def _evidence_record(base: dict, c: LiveChange) -> dict:
    return {
        **base, "type": "evidence", "key": c.key, "kind": c.kind, "source": c.source,
        "observed_at": _iso(c.observed_at),
        "points": [p.model_dump(mode="json") for p in c.evidence or []],
    }


def _last_evidence(history: list[dict]) -> dict[str, list[dict]]:
    """Change key -> the points of its latest evidence record (one pass)."""
    out: dict[str, list[dict]] = {}
    for r in history:
        if r.get("type") == "evidence" and r.get("key"):
            out[r["key"]] = r.get("points") or []
    return out


def _evidence_moved(points: list[LiveEvidencePoint], last: list[dict] | None) -> bool:
    """The peak dBZ or the along-route span moved materially since the last
    evidence recorded for this change (or none was recorded)."""
    if last is None:
        return True

    def summary(dbz: list[float], dist: list[float]) -> tuple:
        return (max(dbz) if dbz else None, min(dist) if dist else None, max(dist) if dist else None)

    now_peak, now_lo, now_hi = summary(
        [p.max_dbz for p in points if p.max_dbz is not None],
        [p.enroute_distance_nm for p in points if p.enroute_distance_nm is not None],
    )
    was_peak, was_lo, was_hi = summary(
        [p["max_dbz"] for p in last if p.get("max_dbz") is not None],
        [p["enroute_distance_nm"] for p in last if p.get("enroute_distance_nm") is not None],
    )

    def moved(a, b, by):
        if a is None or b is None:
            return (a is None) != (b is None)
        return abs(a - b) >= by

    return (
        moved(now_peak, was_peak, EVIDENCE_PEAK_DBZ)
        or moved(now_lo, was_lo, EVIDENCE_SPAN_NM)
        or moved(now_hi, was_hi, EVIDENCE_SPAN_NM)
    )


def _shown_from_history(history: list[dict], stored: LiveLayer | None) -> list[LiveChange]:
    """The changes on screen as the history last recorded them: every
    ``appeared`` not yet ``cleared``. Diffing against this rather than the
    stored layer alone means a tick whose history write failed is not lost:
    its events land, late, on the next tick that writes. The stored layer
    still supplies each change's latest form (a cleared event carries the
    last message shown, SPECI suffix included)."""
    latest = {change_identity(c): c for c in (stored.changes.changes if stored and stored.changes else [])}
    shown: dict[tuple, LiveChange] = {}
    for r in history:
        if r.get("type") != "event":
            continue
        c = LiveChange.model_validate(r["change"])
        if r.get("event") == "appeared":
            shown[change_identity(c)] = c
        else:
            shown.pop(change_identity(c), None)
    # The display order where the layer has it; history-only changes after.
    return [c for ident, c in latest.items() if ident in shown] + [
        c for ident, c in shown.items() if ident not in latest
    ]


def _append_history(flight_dir: Path, records: list[dict]) -> None:
    if not records:
        return
    path = flight_dir / LIVE_HISTORY_FILE
    lines = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    with open(path, "a+b") as f:
        # A previous append cut short would glue this record onto its tail.
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                lines = "\n" + lines
        f.write(lines.encode())


def _record_history(
    flight_dir: Path,
    stored: LiveLayer | None,
    layer: LiveLayer,
    **kwargs,
) -> None:
    """Append this tick to the history. Never raises: a history problem must
    not fail the tick or the ↻ press."""
    try:
        history = load_live_history(flight_dir)
        # A history only starting (first write, or a flight already live
        # when #643 shipped) shows nothing yet: everything on screen is
        # recorded as appearing now.
        _append_history(flight_dir, _history_records(history, _shown_from_history(history, stored), layer, **kwargs))
    except Exception:
        logger.warning("Live history write failed for %s — tick kept", flight_dir, exc_info=True)


def commit_live_update(
    pack_dir: Path | str,
    *,
    briefing_data: dict,
    observations: RouteObservations | None,
    sigmets: RouteSigmets | None,
    observed: ObservedConditions | None,
    started_at: datetime,
    flight_id: str | None = None,
    pack_timestamp: str | None = None,
    now: datetime | None = None,
    cells: CellFrames | None = None,
) -> LiveLayer | None:
    """Fold one refresh into the flight's live layer and persist it.

    ``cells`` is the cells feed as of this tick (#688): the radar storms are
    rebuilt from it every tick. None reads as a dark feed (the classifier's
    station and radar-ring fallback).

    ``None`` blocks keep the stored value (a SIGMET fetch failing this tick
    must not blank the SIGMETs). Returns the stored layer, or None when the
    write was refused as stale: the store already belongs to a newer pack, or
    another writer committed after this computation started.
    """
    from weatherbrief.tasks.artifacts import parse_target_time
    from weatherbrief.tasks.live_significance import (
        ClassifierMemory,
        airport_roles,
        classify_changes,
        worsening_delta,
    )

    pack_dir = Path(pack_dir)
    flight_dir = flight_dir_for_pack(pack_dir)
    now = now or datetime.now(timezone.utc)
    pack_timestamp = pack_timestamp or pack_dir.name
    flight_id = flight_id or flight_dir.name

    with _lock_for(flight_dir):
        prior = stored = load_live(flight_dir)
        if prior is not None and prior.pack_dir_name != pack_dir.name:
            if _is_older_pack(pack_timestamp, prior.pack_timestamp):
                logger.info(
                    "Live update for %s dropped: pack %s is older than stored %s",
                    flight_id, pack_timestamp, prior.pack_timestamp,
                )
                return None
            prior = None  # a new pack: fresh baseline, fresh alert memory
        if (
            prior is not None
            and prior.live_updated_at is not None
            and prior.live_updated_at > started_at
        ):
            logger.info("Live update for %s dropped: a newer write landed first", flight_id)
            return None

        layer = prior.model_copy(deep=True) if prior is not None else LiveLayer(
            flight_id=flight_id,
            pack_timestamp=pack_timestamp,
            pack_dir_name=pack_dir.name,
        )
        if observations is not None:
            layer.route_observations = observations
            layer.observations_updated_at = now
        if sigmets is not None:
            layer.route_sigmets = sigmets
            layer.sigmets_updated_at = now
        if observed is not None:
            layer.observed_conditions = observed
            layer.observed_updated_at = now

        try:
            route = RouteConfig.model_validate(briefing_data["route"])
            route_icaos = [wp.icao for wp in route.waypoints]
            departure = parse_target_time(briefing_data)
        except Exception:
            # Without a route there are no roles: every airport would be
            # classified as a corridor field and alerts silently suppressed.
            logger.warning(
                "Live update for %s: briefing route unreadable — alert tier disabled this tick",
                flight_id, exc_info=True,
            )
            route, route_icaos, departure = None, [], None

        base_obs, base_sigmets, base_observed = _baseline_blocks(briefing_data)
        base_obs, base_sigmets, base_observed, seeded = _seed_missing_baselines(
            layer, base_obs, base_sigmets, base_observed, now,
        )
        layer.storms = _build_storms(cells, route, departure, briefing_data, now)
        memory = ClassifierMemory(
            alerted=dict(prior.alerted) if prior else {},
            sigmets={t.key: t for t in prior.sigmet_traces} if prior else {},
        )
        changes, memory = classify_changes(
            baseline_obs=base_obs,
            latest_obs=layer.route_observations,
            baseline_sigmets=base_sigmets,
            latest_sigmets=layer.route_sigmets,
            baseline_observed=base_observed,
            latest_observed=layer.observed_conditions,
            roles=airport_roles(route_icaos, _alternate_icaos(briefing_data)),
            destination=route_destination(route),
            departure_at=departure,
            arrival_at=planned_arrival(route, departure),
            baseline_at=layer.seeded_at if seeded else _parse_dt(pack_timestamp),
            flown_nm=_flown_nm(route, departure, now) if route is not None else None,
            memory=memory,
            now=now,
            storms=layer.storms,
        )
        if seeded:
            changes.baseline_source = "live_start"
        layer.changes = changes
        layer.glance, layer.ribbon = _build_glance(layer, route, departure, briefing_data, now, cells)
        # The glance is rebuilt wholesale, so #697's highlight has to be
        # carried over here or every tick loses it and pays for a new one.
        # Pure computation — the model call happens after this commit returns.
        from weatherbrief.tasks.live_highlight import carry_forward

        carry_forward(stored, layer)
        layer.last_refresh_delta = worsening_delta(changes)
        layer.alerted = memory.alerted
        layer.sigmet_traces = list(memory.sigmets.values())
        layer.live_updated_at = now

        _atomic_write(flight_dir / LIVE_FILE, layer.model_dump_json(exclude={"changes": TRAIL_EXCLUDE}))
        _atomic_write(flight_dir / LIVE_META_FILE, json.dumps({
            "pack_dir_name": layer.pack_dir_name,
            "pack_timestamp": layer.pack_timestamp,
            "live_updated_at": now.isoformat(),
        }))
        _record_history(
            flight_dir, stored, layer, briefing_data=briefing_data,
            observations=observations, sigmets=sigmets, now=now,
        )
        return layer


def patch_highlight(
    flight_dir: Path | str,
    highlight,
    *,
    pack_timestamp: str,
    as_of: datetime,
) -> bool:
    """Attach a generated highlight to the stored layer (#697). True if written.

    A second, tiny write under the same lock as ``commit_live_update``, so the
    model call sits off the tick's critical path: the deterministic blocks are
    already on disk and servable before this runs.

    Refuses rather than overwrite when the layer has moved on — a different
    pack, or a tick that committed while the model was generating. The
    highlight was written from *those* facts, and attaching it to newer ones is
    exactly the stale-text bug the facts hash exists to prevent. The next tick
    generates its own, so there is nothing to retry.
    """
    flight_dir = Path(flight_dir)
    with _lock_for(flight_dir):
        layer = load_live(flight_dir)
        if layer is None or layer.pack_timestamp != pack_timestamp:
            return False
        if layer.glance is None:
            return False
        if layer.glance.as_of != as_of:
            # A newer tick committed: its own highlight is on the way.
            return False
        layer.glance.highlight = highlight
        _atomic_write(flight_dir / LIVE_FILE, layer.model_dump_json(exclude={"changes": TRAIL_EXCLUDE}))
        return True


# --- Agent summary (#641) ---------------------------------------------------

#: Guardrail the agents see inside the ``live`` block (MCP ``get_briefing`` and
#: the ChatGPT ``getBriefing`` action share it through :func:`live_summary`).
LIVE_NOTE = (
    "Newest observations since the briefing was built (METAR, route SIGMETs) "
    "and the significant changes they show. The digest, advisories and grade "
    "were written before these, at digest_written_at, and are never re-graded "
    "by them: lead with any alert-tier change on flight day, and say the "
    "digest predates it rather than reconciling the two. glance holds the "
    "same at-a-glance lines the app shows (one per flight phase, observations "
    "only, 'unavailable' means the source could not be read, not clear): use "
    "them as the overview. times_today of 2 or "
    "more means the change has come and gone today (bouncing, not building); "
    "recently_cleared lists what cleared in the last hour."
)

#: Size limits — the block rides on every get_briefing call. SIGMETs on a
#: route are few (a dozen is already a busy morning); the cap only bounds a
#: pathological fetch.
LIVE_SUMMARY_MAX_CHANGES = 12
LIVE_SUMMARY_MAX_SIGMETS = 20
#: Changes that cleared in the last hour (#669 D3).
LIVE_SUMMARY_MAX_CLEARED = 6

_TIER_ORDER = {"alert": 0, "highlight": 1}
_ROLE_ORDER = {"destination": 0, "departure": 1, "alternate": 2, "route": 3}


def _nm(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def summarize_live(layer: LiveLayer, briefing_data: dict, changes: LiveChanges | None = None) -> dict:
    """The compact ``live`` block an agent gets with a briefing.

    ``changes`` overrides the layer's with the read-time trails (#669): each
    change then carries ``times_today`` (how often it has come on screen over
    the flight day, so "bouncing" is visible) and ``recently_cleared`` lists
    what cleared on the weather in the last hour (key, message, cleared_at;
    capped at :data:`LIVE_SUMMARY_MAX_CLEARED`). No trails or report strips:
    agents need the fact, not the picture.

    Changes are ordered alert → highlight, worsening first, destination →
    departure → alternate → route, newest evidence first; capped at
    :data:`LIVE_SUMMARY_MAX_CHANGES` (``changes_total`` keeps the full count).
    SIGMETs carry no polygon or raw text; airports are only the briefing's
    departure, destination and top alternates (the classifier's own roles), in
    that order, when the corridor fetch has them. No observed-conditions
    arrays — this is a hook, the full picture is the web briefing.
    """
    from weatherbrief.tasks.live_significance import (
        _DIRECTION_ORDER,
        _sigmet_key_str,
        _sigmet_label,
        airport_roles,
    )

    changes = changes if changes is not None else layer.changes
    out: dict = {
        "note": LIVE_NOTE,
        "live_updated_at": _iso(layer.live_updated_at),
        "observations_updated_at": _iso(layer.observations_updated_at),
        "sigmets_updated_at": _iso(layer.sigmets_updated_at),
        "baseline_at": _iso(changes.baseline_at) if changes else None,
        "baseline_source": changes.baseline_source if changes else None,
        # The pack time: the digest and advisories were written from it.
        "digest_written_at": _iso(_parse_dt(layer.pack_timestamp)) or layer.pack_timestamp,
        "alert_count": changes.alert_count if changes else 0,
        "worsened_count": changes.worsened_count if changes else 0,
        "improved_count": changes.improved_count if changes else 0,
        # The Observed tab's nutshell (#690), word for word: the agent and
        # both apps say the same thing. Null when the tick could not build it.
        "glance": (
            {
                "as_of": _iso(layer.glance.as_of),
                "headline": layer.glance.headline,
                "lines": [{"phase": ln.phase, "text": ln.text} for ln in layer.glance.lines],
            }
            if layer.glance is not None else None
        ),
    }

    items = list(changes.changes) if changes else []
    # Newest first, then a stable sort on the coarser keys keeps that order
    # within each group.
    items.sort(key=lambda c: c.observed_at.timestamp() if c.observed_at else 0.0, reverse=True)
    items.sort(key=lambda c: (
        _TIER_ORDER.get(c.tier, 9),
        _DIRECTION_ORDER.get(c.direction, 9),
        _ROLE_ORDER.get(c.role, 9),
    ))
    out["changes_total"] = len(items)
    out["changes"] = [
        {
            "tier": c.tier,
            "direction": c.direction,
            "role": c.role,
            "kind": c.kind,
            "icao": c.icao,
            "message": c.message,
            "observed_at": _iso(c.observed_at),
            **({"times_today": c.trail.times_today} if c.trail is not None else {}),
        }
        for c in items[:LIVE_SUMMARY_MAX_CHANGES]
    ]
    cleared = (changes.recently_cleared if changes else None) or []
    out["recently_cleared"] = [
        {"key": c.key, "message": c.message, "cleared_at": _iso(c.cleared_at)}
        for c in cleared[:LIVE_SUMMARY_MAX_CLEARED]
    ]

    sigmets = list(layer.route_sigmets.sigmets) if layer.route_sigmets else []
    new_keys = set(changes.new_sigmets) if changes and changes.new_sigmets is not None else None
    out["sigmets_total"] = len(sigmets)
    out["sigmets"] = [
        {
            # Same "FIR seq: QUAL HAZARD" label the change messages use, so a
            # change can be matched to its SIGMET.
            "label": _sigmet_label(s),
            "fir_id": s.fir_id,
            "hazard": s.hazard,
            "qualifier": s.qualifier,
            "valid_from": _iso(s.valid_from),
            "valid_to": _iso(s.valid_to),
            "base_ft": s.base_ft,
            "top_ft": s.top_ft,
            "enroute_distance_from_nm": _nm(s.enroute_distance_from_nm),
            "enroute_distance_to_nm": _nm(s.enroute_distance_to_nm),
            # New to the flight since the briefing (its reissue chain did not
            # start there, #689); omitted when not computed.
            **({"new_since_briefing": _sigmet_key_str(s) in new_keys} if new_keys is not None else {}),
        }
        for s in sigmets[:LIVE_SUMMARY_MAX_SIGMETS]
    ]

    route_icaos = [
        (wp or {}).get("icao") for wp in ((briefing_data.get("route") or {}).get("waypoints") or [])
        if isinstance(wp, dict)
    ]
    roles = airport_roles([i for i in route_icaos if i], _alternate_icaos(briefing_data))
    by_icao = {
        a.icao.upper(): a for a in (layer.route_observations.airports if layer.route_observations else [])
    }
    airports = []
    for icao, role in sorted(roles.items(), key=lambda kv: _ROLE_ORDER.get(kv[1], 9)):
        obs = by_icao.get(icao)
        if obs is None or not (obs.has_metar or obs.metar_raw):
            continue
        airports.append({
            "icao": obs.icao,
            "role": role,
            "metar_time": _iso(obs.metar_time),
            "flight_category": obs.metar_flight_category,
            "metar_raw": obs.metar_raw,
        })
    out["airports"] = airports
    return out


def live_summary(pack_dir: Path | str | None) -> dict | None:
    """:func:`summarize_live` for this pack's live layer, or None when the
    flight has none for this pack (never invented). Never raises: a live-layer
    problem must not fail a briefing read."""
    if not pack_dir:
        return None
    try:
        from weatherbrief.tasks.artifacts import load_briefing

        pack_dir = Path(pack_dir)
        from weatherbrief.tasks.live_trail import trails_for_pack

        layer = live_for_pack(pack_dir)
        if layer is None or layer.live_updated_at is None:
            return None
        briefing = load_briefing(pack_dir) or {}
        return summarize_live(
            layer, briefing, trails_for_pack(pack_dir, layer.changes, briefing_data=briefing),
        )
    except Exception:
        logger.warning("Live summary failed for %s", pack_dir, exc_info=True)
        return None
