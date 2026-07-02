"""Background runner for the timing-scenario scan (Flexibility).

The scan is a **queued analysis that never blocks the briefing**: every
refresh path (streaming user refresh, synchronous ``/refresh``, scheduler
auto-refresh) finalizes the pack first, then calls :func:`schedule_time_scan`;
the briefing UI shows "Scenarios running…" from the status sidecar and polls
``GET .../time-options`` until it flips to ``done``. There is no task queue in
this codebase — a dedicated single-worker executor IS the queue, and its
``max_workers=1`` is the plan's global-concurrency-1 decision (a fleet of
flights can't stampede the analysis stage; slice 2's decode work will ride the
BACKGROUND priority of the decode dispatcher on top).

``GET .../time-options`` also lazy-schedules: a pilot who sets Flexibility
*after* the briefing ran gets a scan on first poll, without a re-refresh.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from weatherbrief.models import TimeScanStatus

logger = logging.getLogger(__name__)

# The queue (see module docstring). One scan at a time, process-wide.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="time-scan")

# Packs currently pending/running — prevents double-submission when a poll
# races the post-refresh hook. Keyed by str(pack_dir).
_inflight: set[str] = set()
_inflight_lock = Lock()


def _write_status(pack_dir: Path, status: str, flexibility: str, reason: str = "") -> None:
    from weatherbrief.tasks.artifacts import save_time_scan_status

    save_time_scan_status(
        pack_dir,
        TimeScanStatus(
            status=status,  # type: ignore[arg-type]
            flexibility=flexibility,  # type: ignore[arg-type]
            reason=reason,
            updated_at=datetime.now(timezone.utc),
        ),
    )


def schedule_time_scan(
    flight_id: str, pack_dir: Path, fetch_ts: datetime, *, db_path: str = "",
) -> bool:
    """Queue a scan for a pack. Returns True when queued (or already queued).

    Cheap to call unconditionally after any refresh: reads the flight's
    ``flexibility`` and writes a ``skipped`` status for ``none`` so pollers get
    a terminal answer instead of an eternal 404. Never raises — scenario work
    must not fail a refresh.
    """
    try:
        from flyfun_common.db import SessionLocal
        from weatherbrief.storage.flights import load_flight

        db = SessionLocal()
        try:
            flight = load_flight(db, flight_id)
        finally:
            db.close()

        if flight.flexibility == "none":
            _write_status(pack_dir, "skipped", "none", reason="flexibility_none")
            return False

        key = str(pack_dir)
        with _inflight_lock:
            if key in _inflight:
                return True
            _inflight.add(key)

        _write_status(pack_dir, "pending", flight.flexibility)
        _executor.submit(
            _run_scan_job, flight_id, pack_dir, fetch_ts, db_path or _default_db_path(),
        )
        logger.info("time-scan: queued %s for flight %s", flight.flexibility, flight_id)
        return True
    except Exception:
        logger.warning("time-scan: scheduling failed for %s", flight_id, exc_info=True)
        return False


def _default_db_path() -> str:
    return os.environ.get("AIRPORTS_DB", "")


def _run_scan_job(flight_id: str, pack_dir: Path, fetch_ts: datetime, db_path: str) -> None:
    """Worker: grade the scenarios, persist artifacts, update the pack row."""
    from flyfun_common.db import SessionLocal

    try:
        db = SessionLocal()
        try:
            from weatherbrief.api.packs import _build_route_config, _load_advisory_profile
            from weatherbrief.storage.flights import load_flight
            from weatherbrief.tasks.advise import derive_assessment_from_advisories
            from weatherbrief.tasks.artifacts import save_time_options
            from weatherbrief.tasks.time_scan import run_time_scan

            flight = load_flight(db, flight_id)
            # Re-read mode inside the worker — it may have changed while queued.
            if flight.flexibility == "none":
                _write_status(pack_dir, "skipped", "none", reason="flexibility_none")
                return
            if flight.flexibility == "alternate" and flight.alt_departure_time is None:
                _write_status(
                    pack_dir, "skipped", flight.flexibility, reason="no_alternate_time",
                )
                return

            # Decision-H reuse: a scan for the same flexibility mode, planned
            # departure, and ECMWF run is still valid — skip the re-decode.
            existing = _reusable_scan(pack_dir, flight)
            if existing is not None:
                logger.info(
                    "time-scan: reusing existing scan for %s (same ECMWF run %s)",
                    flight_id, existing.ecmwf_run_ts,
                )
                _write_status(pack_dir, "done", flight.flexibility)
                return

            _write_status(pack_dir, "running", flight.flexibility)

            route = _build_route_config(flight, db_path)
            (
                enabled_ids, enabled_map, user_params, aggregation, adv_models,
                icing_method, cloud_method, convective_method, recompute_conds,
                locale, _afd,
            ) = _load_advisory_profile(
                db, flight, flight.user_id, None, pack_dir, db_path=db_path,
            )

            scan = run_time_scan(
                pack_dir, route, flight.departure_time,
                flexibility=flight.flexibility,
                alt_departure_time=flight.alt_departure_time,
                advisory_models=adv_models,
                enabled_ids=enabled_ids,
                advisory_enabled=enabled_map,
                user_params=user_params,
                aggregation=aggregation,
                airports_db_path=db_path,
                airport_conditions_recompute=recompute_conds,
                icing_method=icing_method,
                cloud_method=cloud_method,
                convective_method=convective_method,
                locale=locale,
            )
            if scan is None:
                _write_status(pack_dir, "skipped", flight.flexibility, reason="no_data")
                return

            save_time_options(pack_dir, scan)

            # The pinned alternate row keeps the legacy pack-row alt fields
            # (and the planned↔alt web UI) alive — mirror what the retired
            # in-pipeline alt stage recorded.
            alt_row = next((c for c in scan.candidates if c.is_alternate), None)
            if alt_row is not None:
                _update_pack_alt_fields(db, flight_id, pack_dir, fetch_ts, alt_row)

            # Usage accounting (abuse-limit substrate): one row per executed
            # scan, keyed by mode. The decision-H reuse path above logs
            # nothing — no compute was spent. A future per-user limit (e.g.
            # N full-day scans/week) gates in schedule_time_scan by counting
            # these rows; the data collection starts now.
            from weatherbrief.api.usage import log_api_usage

            log_api_usage(
                db, service="time_scan", pipeline=flight.flexibility,
                api_calls=1, user_id=flight.user_id, flight_id=flight_id,
            )
            db.commit()

            _write_status(pack_dir, "done", flight.flexibility)
        finally:
            db.close()
    except Exception:
        logger.warning("time-scan: job failed for %s", flight_id, exc_info=True)
        try:
            _write_status(pack_dir, "failed", "none", reason="internal_error")
        except Exception:
            pass
    finally:
        with _inflight_lock:
            _inflight.discard(str(pack_dir))


def schedule_time_confirm(
    flight_id: str, pack_dir: Path, candidate_time: datetime, *, db_path: str = "",
) -> str:
    """Queue an on-tap multi-model confirm for one candidate.

    Marks the candidate ``confirm_pending`` in ``time_options.json`` (the
    polling client renders "checking all models…" off that flag) and submits
    the fetch+grade to the same single-worker queue as scans.

    Returns ``"queued"``, ``"busy"`` (a confirm is already running for this
    pack — one at a time, each costs a briefing-equivalent of GFS+ICON fetch,
    so a tap-everything user can't stack five of them), or ``"invalid"`` (no
    such candidate / already confirmed).
    """
    from weatherbrief.tasks.artifacts import load_time_options, save_time_options

    try:
        scan = load_time_options(pack_dir)
        if scan is None:
            return "invalid"
        cand = scan.candidate_at(candidate_time)
        if cand is None or cand.confirmed is not None:
            return "invalid"

        key = f"{pack_dir}#confirm@{candidate_time.isoformat()}"
        pack_prefix = f"{pack_dir}#confirm@"
        with _inflight_lock:
            if key in _inflight:
                return "queued"  # same candidate re-tapped — already on it
            if any(k.startswith(pack_prefix) for k in _inflight):
                return "busy"
            _inflight.add(key)

        if not cand.confirm_pending:
            cand.confirm_pending = True
            save_time_options(pack_dir, scan)
        _executor.submit(
            _run_confirm_job, flight_id, pack_dir, candidate_time,
            db_path or _default_db_path(), key,
        )
        logger.info(
            "time-scan: confirm queued for %s @ %s", flight_id, candidate_time,
        )
        return "queued"
    except Exception:
        logger.warning("time-scan: confirm scheduling failed for %s", flight_id, exc_info=True)
        return "invalid"


def _run_confirm_job(
    flight_id: str, pack_dir: Path, candidate_time: datetime, db_path: str, key: str,
) -> None:
    """Worker: multi-model check of one candidate, cached onto the artifact."""
    import os

    from flyfun_common.db import SessionLocal

    try:
        db = SessionLocal()
        try:
            from weatherbrief.api.packs import _build_route_config, _load_advisory_profile
            from weatherbrief.storage.flights import load_flight
            from weatherbrief.tasks.artifacts import load_time_options, save_time_options
            from weatherbrief.tasks.time_scan import confirm_candidate

            flight = load_flight(db, flight_id)
            route = _build_route_config(flight, db_path)
            (
                enabled_ids, enabled_map, user_params, aggregation, adv_models,
                icing_method, cloud_method, convective_method, recompute_conds,
                locale, _afd,
            ) = _load_advisory_profile(
                db, flight, flight.user_id, None, pack_dir, db_path=db_path,
            )

            confirmation = confirm_candidate(
                pack_dir, route, candidate_time,
                data_dir=Path(os.environ.get("DATA_DIR", "data")),
                advisory_models=adv_models,
                enabled_ids=enabled_ids,
                advisory_enabled=enabled_map,
                user_params=user_params,
                aggregation=aggregation,
                airports_db_path=db_path,
                airport_conditions_recompute=recompute_conds,
                icing_method=icing_method,
                cloud_method=cloud_method,
                convective_method=convective_method,
                locale=locale,
            )

            # Re-load before mutating — the artifact may have moved while the
            # fetch ran (another confirm finishing, never a concurrent write:
            # the single-worker queue serializes all artifact writers).
            scan = load_time_options(pack_dir)
            cand = scan.candidate_at(candidate_time) if scan else None
            if cand is None:
                return
            cand.confirm_pending = False
            if confirmation is not None:
                cand.confirmed = confirmation
                cand.confidence = "confirmed"
            save_time_options(pack_dir, scan)

            # Usage accounting: one row per confirm attempt (the GFS+ICON
            # fetch was spent whether or not grading succeeded). Future
            # per-user limits gate in schedule_time_confirm on these rows.
            from weatherbrief.api.usage import log_api_usage

            log_api_usage(
                db, service="time_scan", pipeline="confirm",
                api_calls=1, user_id=flight.user_id, flight_id=flight_id,
            )
            db.commit()
            logger.info(
                "time-scan: confirm %s for %s @ %s",
                "ok" if confirmation else "failed", flight_id, candidate_time,
            )
        finally:
            db.close()
    except Exception:
        logger.warning("time-scan: confirm job failed for %s", flight_id, exc_info=True)
        try:
            from weatherbrief.tasks.artifacts import load_time_options, save_time_options

            scan = load_time_options(pack_dir)
            cand = scan.candidate_at(candidate_time) if scan else None
            if cand is not None and cand.confirm_pending:
                cand.confirm_pending = False
                save_time_options(pack_dir, scan)
        except Exception:
            pass
    finally:
        with _inflight_lock:
            _inflight.discard(key)


def reconcile_stale_confirms(pack_dir: Path) -> bool:
    """Clear ``confirm_pending`` flags with no live worker behind them.

    A server restart (deploy, dev reload) mid-confirm kills the worker but
    leaves the persisted flag — the UI would show "checking all models…"
    forever and the one-at-a-time guard would block nothing (the in-memory
    inflight set is empty after restart, so this is purely a display/honesty
    fix). Called from the polling GET. Returns True when anything changed.
    """
    from weatherbrief.tasks.artifacts import load_time_options, save_time_options

    scan = load_time_options(pack_dir)
    if scan is None:
        return False
    pending = [c for c in scan.candidates if c.confirm_pending]
    if not pending:
        return False
    prefix = f"{pack_dir}#confirm@"
    with _inflight_lock:
        live = {k for k in _inflight if k.startswith(prefix)}
    changed = False
    for c in pending:
        key = f"{pack_dir}#confirm@{c.departure_time.isoformat()}"
        if key not in live:
            c.confirm_pending = False
            changed = True
    if changed:
        save_time_options(pack_dir, scan)
        logger.info("time-scan: cleared stale confirm_pending flag(s) in %s", pack_dir)
    return changed


def _reusable_scan(pack_dir: Path, flight):
    """Return the existing scan when it's still valid (decision H), else None.

    Valid = same flexibility mode, same planned departure, and — when the scan
    used the ECMWF extension — the run on disk hasn't moved. A scan with no
    ``ecmwf_run_ts`` (pure free-tier) is cheap to redo, so it is NOT reused —
    a fresher grade wins.
    """
    from weatherbrief.tasks.artifacts import load_time_options
    from weatherbrief.tasks.time_scan import _pack_fetched_at, current_ecmwf_run_ts

    scan = load_time_options(pack_dir)
    if scan is None or scan.ecmwf_run_ts is None:
        return None
    if scan.flexibility != flight.flexibility:
        return None
    dep = flight.departure_time
    if abs((scan.baseline.departure_time - dep).total_seconds()) > 60:
        return None
    cover_until = dep
    if scan.window is not None:
        cover_until = max(cover_until, scan.window.end)
    # Compare against the run a re-scan would actually pick: the extension
    # pins run selection to the pack's fetched_at, so use the same pin here —
    # a newer run on disk doesn't invalidate this pack's scan.
    would_use = current_ecmwf_run_ts(
        cover_until, as_of_time=_pack_fetched_at(pack_dir),
    )
    if would_use != scan.ecmwf_run_ts:
        return None
    return scan


def _update_pack_alt_fields(
    db, flight_id: str, pack_dir: Path, fetch_ts: datetime, alt_row,
) -> None:
    """Set has_alt_advisories / alt_assessment on the pack row (post-hoc, the
    same update the on-demand ``/advisories/alt/compute`` endpoint performs)."""
    from sqlalchemy import select

    from weatherbrief.db.models import BriefingPackRow

    naive_ts = fetch_ts.replace(tzinfo=None)
    stmt = select(BriefingPackRow).where(
        BriefingPackRow.flight_id == flight_id,
        BriefingPackRow.fetch_timestamp == naive_ts,
    )
    pack_row = db.execute(stmt).scalar_one_or_none()
    if pack_row is None:
        return
    pack_row.alt_assessment = alt_row.assessment
    pack_row.alt_assessment_reason = alt_row.assessment_reason
    pack_row.has_alt_advisories = (pack_dir / "route_advisories_alt.json").exists()
    db.flush()
