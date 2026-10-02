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
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.live import LiveLayer
from weatherbrief.models.observations import RouteObservations, RouteSigmets
from weatherbrief.models.observed import ObservedConditions

logger = logging.getLogger(__name__)

LIVE_FILE = "live.json"
LIVE_META_FILE = "live_meta.json"

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
        out["live_changes"] = layer.changes.model_dump(mode="json")
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


def remove_live(flight_dir: Path | str) -> None:
    """Delete a flight's live files (flight delete / move)."""
    for name in (LIVE_FILE, LIVE_META_FILE):
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


def _destination(route: RouteConfig | None) -> tuple[float, float] | None:
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
    """
    if base_obs is None and layer.seeded_observations is None and layer.route_observations is not None:
        layer.seeded_observations = layer.route_observations.model_copy(deep=True)
        layer.seeded_at = layer.seeded_at or now
    if base_sigmets is None and layer.seeded_sigmets is None and layer.route_sigmets is not None:
        layer.seeded_sigmets = layer.route_sigmets.model_copy(deep=True)
        layer.seeded_at = layer.seeded_at or now
    if base_observed is None and layer.seeded_observed is None and layer.observed_conditions is not None:
        layer.seeded_observed = layer.observed_conditions.model_copy(deep=True)
        layer.seeded_at = layer.seeded_at or now
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
) -> LiveLayer | None:
    """Fold one refresh into the flight's live layer and persist it.

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
        prior = load_live(flight_dir)
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
        memory = ClassifierMemory(alerted=dict(prior.alerted) if prior else {})
        changes, memory = classify_changes(
            baseline_obs=base_obs,
            latest_obs=layer.route_observations,
            baseline_sigmets=base_sigmets,
            latest_sigmets=layer.route_sigmets,
            baseline_observed=base_observed,
            latest_observed=layer.observed_conditions,
            roles=airport_roles(route_icaos, _alternate_icaos(briefing_data)),
            destination=_destination(route),
            baseline_at=layer.seeded_at if seeded else _parse_dt(pack_timestamp),
            flown_nm=_flown_nm(route, departure, now) if route is not None else None,
            memory=memory,
            now=now,
        )
        if seeded:
            changes.baseline_source = "live_start"
        layer.changes = changes
        layer.last_refresh_delta = worsening_delta(changes)
        layer.alerted = memory.alerted
        layer.live_updated_at = now

        _atomic_write(flight_dir / LIVE_FILE, layer.model_dump_json())
        _atomic_write(flight_dir / LIVE_META_FILE, json.dumps({
            "pack_dir_name": layer.pack_dir_name,
            "pack_timestamp": layer.pack_timestamp,
            "live_updated_at": now.isoformat(),
        }))
        return layer
