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
