"""Disk-space retention: tiered cleanup of old briefing pack artifacts.

Tier 1 (T1) — strip heavy artifacts (Skew-T PNGs, GRAMET, cross_section.json,
forecasts.json) after a configurable number of days post-departure.  The
lightweight JSON files that power the main UI (briefing, advisories,
route_analyses, digest) are preserved.

Tier 2 (T2) — delete the entire pack directory and its DB row after a longer
window.  FlightRow is kept for route history.

Inactive users (no login or briefing usage within a threshold) get a shorter
T2 window so stale data doesn't linger.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow, FlightRow, PirepRow, UserRow
from weatherbrief.storage.debriefs import list_debriefed_flight_ids

logger = logging.getLogger(__name__)

# Files / directories removed during T1.  Everything else in the pack dir
# is kept (briefing.json, route_analyses.json, advisories, digest, etc.).
# sounding_profiles.json.gz is derived from cross_section.json and the
# on-the-fly fallback also needs cross_section.json — so the sidecar must die
# in the same T1 sweep that strips cross_section.json, not linger to T2.
_HEAVY_FILES = (
    "cross_section.json",
    "forecasts.json",
    "gramet.pdf",
    "gramet.png",
    "sounding_profiles.json.gz",
)
_HEAVY_DIRS = ("skewt",)


@dataclass
class RetentionConfig:
    """Retention thresholds — all values in days."""

    t1_days: int = 30
    t2_active_days: int = 180
    t2_inactive_days: int = 90
    inactive_threshold_days: int = 30
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> RetentionConfig:
        """Build config from environment variables (with sensible defaults)."""
        return cls(
            t1_days=int(os.environ.get("RETENTION_T1_DAYS", "30")),
            t2_active_days=int(os.environ.get("RETENTION_T2_ACTIVE_DAYS", "180")),
            t2_inactive_days=int(os.environ.get("RETENTION_T2_INACTIVE_DAYS", "90")),
            inactive_threshold_days=int(os.environ.get("RETENTION_INACTIVE_DAYS", "30")),
            dry_run=os.environ.get("RETENTION_DRY_RUN", "0") == "1",
        )


@dataclass
class RetentionStats:
    """Counters returned by a single retention run."""

    packs_t1: int = 0
    packs_t2: int = 0
    bytes_freed: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_retention(db: Session, config: RetentionConfig | None = None) -> RetentionStats:
    """Scan all packs and apply T1/T2 retention rules.

    Returns aggregate stats for logging.
    """
    if config is None:
        config = RetentionConfig.from_env()

    now = datetime.now(timezone.utc)
    stats = RetentionStats()

    inactive_user_ids = _inactive_user_ids(db, now, config.inactive_threshold_days)

    # Pack IDs linked to PIREPs — exempt from all retention.
    pirep_pack_ids: set[int] = set(
        db.execute(
            select(PirepRow.pack_id).where(PirepRow.pack_id.isnot(None)).distinct()
        ).scalars().all()
    )

    # Flight IDs with a debrief — exempt all of their packs from T2 (the
    # lightweight briefing.json is what calibration needs; T1 still strips
    # heavy artifacts to save disk).
    debriefed_flight_ids = list_debriefed_flight_ids(db)

    # Query all packs joined to their flight (for departure_time + user_id).
    stmt = (
        select(BriefingPackRow, FlightRow.departure_time, FlightRow.user_id)
        .join(FlightRow, BriefingPackRow.flight_id == FlightRow.id)
    )
    rows = db.execute(stmt).all()

    for pack, departure_time, user_id in rows:
        if pack.id in pirep_pack_ids:
            continue  # exempt: linked PIREP needs full forecast data
        if departure_time is None:
            continue

        dep = departure_time if departure_time.tzinfo else departure_time.replace(tzinfo=timezone.utc)
        age_days = (now - dep).days

        inactive = user_id in inactive_user_ids
        t2_days = config.t2_inactive_days if inactive else config.t2_active_days
        is_debriefed = pack.flight_id in debriefed_flight_ids

        try:
            pack_dir = Path(pack.artifact_path) if pack.artifact_path else None

            if age_days >= t2_days and not is_debriefed:
                freed = _purge_full_pack(pack, pack_dir, config.dry_run)
                stats.packs_t2 += 1
                stats.bytes_freed += freed
            elif age_days >= config.t1_days:
                freed = _purge_heavy_artifacts(pack, pack_dir, config.dry_run)
                if freed > 0:
                    stats.packs_t1 += 1
                    stats.bytes_freed += freed
        except Exception:
            logger.error("Retention error for pack %s (flight %s)", pack.id, pack.flight_id, exc_info=True)
            stats.errors += 1

    if not config.dry_run:
        db.flush()

    action = "DRY-RUN" if config.dry_run else "applied"
    logger.info(
        "Retention %s: T1=%d packs, T2=%d packs, freed=%.1f MB, errors=%d",
        action, stats.packs_t1, stats.packs_t2,
        stats.bytes_freed / (1024 * 1024), stats.errors,
    )
    return stats


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


def _purge_heavy_artifacts(pack: BriefingPackRow, pack_dir: Path | None, dry_run: bool) -> int:
    """T1: remove large files from a pack directory, update DB flags.

    Returns bytes freed.
    """
    if pack_dir is None or not pack_dir.exists():
        return 0

    # NB: don't gate on has_skewt/has_gramet here. Those flags only track
    # Skew-T PNGs / GRAMET, but the bulk heavy files (forecasts.json,
    # cross_section.json, sounding_profiles.json.gz) exist independently of
    # them — and with generate_skewt=False by default, both flags are commonly
    # False, which previously skipped the whole purge and leaked those files
    # past T1 until T2. The per-file exists() checks below already make this
    # idempotent (a re-run on an already-stripped pack frees 0 bytes cheaply).
    freed = 0

    for name in _HEAVY_FILES:
        p = pack_dir / name
        if p.exists():
            size = p.stat().st_size
            if dry_run:
                logger.info("DRY-RUN: would delete %s (%d bytes)", p, size)
            else:
                p.unlink()
            freed += size

    for name in _HEAVY_DIRS:
        d = pack_dir / name
        if d.is_dir():
            size = _dir_size(d)
            if dry_run:
                logger.info("DRY-RUN: would rmtree %s (%d bytes)", d, size)
            else:
                shutil.rmtree(d)
            freed += size

    if not dry_run:
        pack.has_skewt = False
        pack.has_gramet = False

    return freed


def _purge_full_pack(pack: BriefingPackRow, pack_dir: Path | None, dry_run: bool) -> int:
    """T2: remove entire pack directory and delete the DB row.

    Returns bytes freed.
    """
    freed = 0
    if pack_dir is not None and pack_dir.exists():
        freed = _dir_size(pack_dir)
        if dry_run:
            logger.info("DRY-RUN: would rmtree %s (%d bytes)", pack_dir, freed)
        else:
            shutil.rmtree(pack_dir)

    if not dry_run:
        session = Session.object_session(pack)
        if session is not None:
            session.delete(pack)

    return freed


# ---------------------------------------------------------------------------
# Inactive user detection
# ---------------------------------------------------------------------------


def _inactive_user_ids(db: Session, now: datetime, threshold_days: int) -> set[str]:
    """Return user IDs whose last activity is older than *threshold_days*.

    Activity = max(last_login_at, latest briefing_usage.timestamp).
    """
    cutoff = now - timedelta(days=threshold_days)

    # Last briefing usage per user
    usage_stmt = (
        select(
            BriefingUsageRow.user_id,
            func.max(BriefingUsageRow.timestamp).label("last_usage"),
        )
        .group_by(BriefingUsageRow.user_id)
    )
    usage_map: dict[str, datetime] = {}
    for row in db.execute(usage_stmt):
        ts = row.last_usage
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        usage_map[row.user_id] = ts

    # Check each user
    inactive: set[str] = set()
    users = db.execute(select(UserRow.id, UserRow.last_login_at)).all()
    for user_id, last_login in users:
        login_ts = last_login
        if login_ts and login_ts.tzinfo is None:
            login_ts = login_ts.replace(tzinfo=timezone.utc)

        last_activity = max(filter(None, [login_ts, usage_map.get(user_id)]), default=None)
        if last_activity is None or last_activity < cutoff:
            inactive.add(user_id)

    return inactive


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _dir_size(path: Path) -> int:
    """Total size of all files under *path*."""
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


# ---------------------------------------------------------------------------
# Verification raw-data retention (#522, Phase 3)
#
# Independent of the briefing-pack tiered retention above. The raw
# verification tables are tier 1 of a three-tier design: aggregates live
# forever in the rollup tables, rows live forever as Parquet under
# DATA_DIR/archive/, and MySQL keeps only the online window that live queries
# actually read (every dashboard/digest window is <=90 days).
#
# Nothing here deletes anything until VERIFICATION_RAW_RETENTION_DAYS is set
# below the 9999 sentinel *and* the period passes both gates below. See
# tasks/verification_tiering for the phase gates and
# designs/plans/verification-data-tiering.md for the rollout order.
# ---------------------------------------------------------------------------


# Rows deleted per statement. Deliberately small: this runs against a shared
# MySQL server with a 1 GB buffer pool, where one multi-month DELETE would
# hold a huge transaction, bloat the undo log and stall every other client.
_DELETE_BATCH = 5_000


def _delete_in_batches(db: Session, model, where_clauses, *, batch: int = _DELETE_BATCH) -> int:
    """Delete rows matching *where_clauses*, a bounded batch at a time.

    Selects primary keys with a LIMIT, then deletes by key, committing after
    each batch. Two reasons for the select-then-delete shape rather than
    ``DELETE ... LIMIT``: SQLite only supports that clause when compiled with
    ``SQLITE_ENABLE_UPDATE_DELETE_LIMIT`` (commonly off, including in the
    stock CPython build the tests run on), and deleting by explicit primary
    key gives the server the cheapest possible plan regardless of what the
    matching predicate could use.

    Commits per batch on purpose: a prune that dies halfway leaves a
    consistent, smaller table rather than rolling back an hour of work.
    """
    total = 0
    while True:
        ids = db.execute(
            select(model.id).where(*where_clauses).limit(batch)
        ).scalars().all()
        if not ids:
            return total
        result = db.execute(
            model.__table__.delete().where(model.id.in_(list(ids)))
        )
        db.commit()
        total += result.rowcount or len(ids)
        if len(ids) < batch:
            return total


def _summarised_months(db: Session) -> set[tuple[int, int]]:
    """(year, month) pairs with at least one airport_monthly_summary row.

    Any airport row implies the month was rolled up — the summary rollup is
    all-or-nothing per month.
    """
    from weatherbrief.db.models import AirportMonthlySummaryRow

    out: set[tuple[int, int]] = set()
    for dt in db.execute(
        select(AirportMonthlySummaryRow.month).distinct()
    ).scalars().all():
        if dt is not None:
            out.add((dt.year, dt.month))
    return out


def _month_bounds(y: int, m: int) -> tuple[datetime, datetime]:
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = (
        datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        if m == 12
        else datetime(y, m + 1, 1, tzinfo=timezone.utc)
    )
    return start, end


def prunable_months(
    db: Session, cutoff: datetime, *, require_archive: bool | None = None,
) -> tuple[list[tuple[int, int]], list[str]]:
    """Months that are safe to delete, plus the reasons the others aren't.

    Two independent gates, both required:

    1. **Summarised** — the month has rows in ``airport_monthly_summary``.
       Never delete observations whose climatology hasn't been derived, even
       if the cutoff has passed (e.g. the rollup has been failing).
    2. **Archived** — all three monthly tables have an ``archive_manifest``
       row whose ``row_count`` still matches a live recount. No manifest, no
       delete, and the refusal is logged loudly: once a month is gone from
       MySQL the Parquet file is the only copy, so "the archive job was
       silently broken" must never be discoverable only after the delete.

    Returned reasons are logged by the caller rather than raised — a month
    failing a gate is an operational signal, not an error.
    """
    from weatherbrief.tasks.archive import month_archive_ok
    from weatherbrief.tasks.verification_tiering import prune_requires_archive

    if require_archive is None:
        require_archive = prune_requires_archive()

    safe: list[tuple[int, int]] = []
    blocked: list[str] = []
    for (y, m) in sorted(_summarised_months(db)):
        _, month_end = _month_bounds(y, m)
        if month_end > cutoff:
            continue  # still inside the online window
        if require_archive:
            ok, reason = month_archive_ok(db, f"{y:04d}-{m:02d}")
            if not ok:
                blocked.append(reason)
                continue
        safe.append((y, m))
    return safe, blocked


def prune_raw_observations(
    db: Session,
    retain_days: int | None = None,
) -> dict[str, int]:
    """Delete raw verification rows older than the online-window cutoff.

    Deletes only whole months that pass :func:`prunable_months`' gates, in
    bounded batches with a commit per batch.

    Exemptions, none of which are optional:

    - **``source='flight'`` scores are never pruned.** Tiny volume, and they
      carry pilot/debrief context plus ERA5 re-analysis value — the same
      reasoning that exempts debriefed flights' packs from T2 retention.
    - **Observations referenced by ``flight_verification_map`` survive.**
      Observations are shared between the flight and standalone tracks and
      have no source column of their own, so this mapping is what identifies
      flight-linked ground truth. Deleting such an observation would cascade
      into the exempt flight scores hanging off it.
    - **Observations that still have a flight score survive** — the same
      protection expressed against the scores table, in case an observation
      was linked to a flight without a map row ever being written.

    Returns per-table deletion counts. Commits internally (see
    :func:`_delete_in_batches`); a caller's own commit afterwards is a no-op.
    """
    from weatherbrief.db.models import (
        FlightVerificationMapRow,
        TafVerificationScoreRow,
        VerificationObservationRow,
        VerificationScoreRow,
    )
    from weatherbrief.tasks.verification_tiering import (
        raw_retention_days,
        raw_retention_disabled,
    )

    if retain_days is None:
        retain_days = raw_retention_days()

    empty = {"observations": 0, "scores": 0, "taf_scores": 0, "map_rows": 0}

    if raw_retention_disabled(retain_days):
        # Effectively disabled. Don't even scan.
        logger.debug("Raw observation retention disabled (retain_days=%d)", retain_days)
        return dict(empty)

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)

    if not _summarised_months(db):
        logger.warning(
            "Raw retention: no months summarised yet — refusing to delete"
        )
        return dict(empty)

    safe_months, blocked = prunable_months(db, cutoff)
    for reason in blocked:
        logger.warning("Raw retention: refusing to prune — %s", reason)

    if not safe_months:
        return dict(empty)

    obs_deleted = 0
    score_deleted = 0
    taf_deleted = 0

    for (y, m) in safe_months:
        month_start, month_end = _month_bounds(y, m)
        # Walk the month a day at a time so each batch's predicate stays
        # narrow and index-driven, rather than handing the server a
        # month-wide range to re-evaluate on every batch.
        day = month_start
        while day < month_end:
            day_end = day + timedelta(days=1)

            # source= is not just an exemption, it is what makes the delete
            # indexable: ix_verif_scores_source_time / _source_days_time both
            # lead with source, and nothing on this table leads with
            # observation_time.
            score_deleted += _delete_in_batches(
                db, VerificationScoreRow,
                (
                    VerificationScoreRow.source != "flight",
                    VerificationScoreRow.observation_time >= day,
                    VerificationScoreRow.observation_time < day_end,
                ),
            )
            taf_deleted += _delete_in_batches(
                db, TafVerificationScoreRow,
                (
                    TafVerificationScoreRow.source != "flight",
                    TafVerificationScoreRow.observation_time >= day,
                    TafVerificationScoreRow.observation_time < day_end,
                ),
            )

            flight_linked = (
                select(FlightVerificationMapRow.id)
                .where(
                    FlightVerificationMapRow.observation_id
                    == VerificationObservationRow.id
                )
                .exists()
            )
            has_flight_score = (
                select(VerificationScoreRow.id)
                .where(
                    VerificationScoreRow.observation_id
                    == VerificationObservationRow.id,
                    VerificationScoreRow.source == "flight",
                )
                .exists()
            )
            obs_deleted += _delete_in_batches(
                db, VerificationObservationRow,
                (
                    VerificationObservationRow.observation_time >= day,
                    VerificationObservationRow.observation_time < day_end,
                    ~flight_linked,
                    ~has_flight_score,
                ),
            )
            day = day_end

    # expire_all so any cached ORM objects representing the deleted rows
    # reflect their absence.
    db.expire_all()
    logger.info(
        "Raw retention: pruned %d observations, %d scores, %d TAF scores "
        "across %d month(s) (retain_days=%d, cutoff=%s)",
        obs_deleted, score_deleted, taf_deleted, len(safe_months),
        retain_days, cutoff.isoformat(),
    )
    return {
        "observations": obs_deleted,
        "scores": score_deleted,
        "taf_scores": taf_deleted,
        # Map rows are no longer deleted — they are the exemption that keeps
        # flight-linked observations alive. Kept in the payload so the
        # scheduler's log line and any dashboards reading it don't KeyError.
        "map_rows": 0,
    }


# ---------------------------------------------------------------------------
# Snapshot inbox rotation (#522, Phase 3)
# ---------------------------------------------------------------------------


def rotate_snapshot_inbox(
    inbox_dir: str | Path | None = None, retain_days: int | None = None,
) -> dict[str, int]:
    """Delete transport artifacts in ``SNAPSHOT_INBOX_DIR`` older than N days.

    The off-box compute nodes drop ``eu-*.sqlite`` / ``us-*.sqlite`` files
    here; the ingest reads each one once and never looks at it again
    (confirmed — nothing re-reads an old artifact). Once a cycle's data is in
    both MySQL and the Parquet archive the transport file is redundant, so
    this caps what is otherwise an unbounded disk leak.

    ``retain_days=0`` (the shipping default) keeps everything, matching
    pre-#522 behaviour. Returns ``{"deleted", "bytes_freed"}``.
    """
    from weatherbrief.tasks.verification_tiering import snapshot_inbox_retention_days

    if retain_days is None:
        retain_days = snapshot_inbox_retention_days()
    if retain_days <= 0:
        return {"deleted": 0, "bytes_freed": 0}

    if inbox_dir is None:
        inbox_dir = os.environ.get("SNAPSHOT_INBOX_DIR", "").strip()
    if not inbox_dir:
        return {"deleted": 0, "bytes_freed": 0}

    path = Path(inbox_dir)
    if not path.is_dir():
        return {"deleted": 0, "bytes_freed": 0}

    cutoff = datetime.now(timezone.utc).timestamp() - retain_days * 86400
    deleted = 0
    freed = 0
    for pattern in ("eu-*.sqlite", "us-*.sqlite"):
        for f in path.glob(pattern):
            try:
                st = f.stat()
                if st.st_mtime >= cutoff:
                    continue
                size = st.st_size
                f.unlink()
                deleted += 1
                freed += size
            except OSError:
                logger.warning("Inbox rotation: could not remove %s", f, exc_info=True)

    if deleted:
        logger.info(
            "Inbox rotation: removed %d artifact(s), freed %.1f MB (retain_days=%d)",
            deleted, freed / (1024 * 1024), retain_days,
        )
    return {"deleted": deleted, "bytes_freed": freed}
