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

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow, FlightRow, PirepRow, UserRow
from weatherbrief.storage.debriefs import list_debriefed_flight_ids

logger = logging.getLogger(__name__)

# Files / directories removed during T1.  Everything else in the pack dir
# is kept (briefing.json, route_analyses.json, advisories, digest, etc.).
_HEAVY_FILES = ("cross_section.json", "forecasts.json", "gramet.pdf", "gramet.png")
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

    # Skip if already stripped (no heavy artifacts remain).
    if not pack.has_skewt and not pack.has_gramet:
        return 0

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
# Verification observation retention
#
# Independent of the briefing-pack tiered retention above. Once raw obs are
# rolled up into airport_monthly/daily_summary, we can drop the originals.
# Default retention is intentionally very high (9999 days = effectively
# disabled) until the Patterns/Spotlight UI has been live long enough to
# validate the rollup column set covers everything we query. Once that's
# confirmed, switch to ~180 days via VERIFICATION_RAW_RETENTION_DAYS.
# ---------------------------------------------------------------------------


_DEFAULT_RAW_RETENTION_DAYS = 9999  # disabled by default — see comment above


def prune_raw_observations(
    db: Session,
    retain_days: int | None = None,
) -> dict[str, int]:
    """Delete verification_observations / scores older than retain_days.

    Only deletes data older than the cutoff *AND* whose containing month
    has at least one row in airport_monthly_summary — never delete obs that
    haven't been summarised yet, even if the cutoff has passed (e.g. if
    rollup has been failing). This is a safety belt; in normal operation
    rollup runs nightly and is well ahead of the cutoff.

    Returns a dict with deletion counts per table.
    """
    from weatherbrief.db.models import (
        AirportMonthlySummaryRow,
        FlightVerificationMapRow,
        TafVerificationScoreRow,
        VerificationObservationRow,
        VerificationScoreRow,
    )

    if retain_days is None:
        retain_days = int(
            os.environ.get(
                "VERIFICATION_RAW_RETENTION_DAYS",
                str(_DEFAULT_RAW_RETENTION_DAYS),
            )
        )

    if retain_days >= _DEFAULT_RAW_RETENTION_DAYS:
        # Effectively disabled. Don't even scan.
        logger.debug("Raw observation retention disabled (retain_days=%d)", retain_days)
        return {"observations": 0, "scores": 0, "taf_scores": 0, "map_rows": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)

    # Months that have been summarised (any airport row implies the month
    # was rolled up — rollup is all-or-nothing per month).
    summarised_months: set[tuple[int, int]] = set()
    for dt in db.execute(
        select(AirportMonthlySummaryRow.month).distinct()
    ).scalars().all():
        if dt is not None:
            summarised_months.add((dt.year, dt.month))

    if not summarised_months:
        logger.warning(
            "Raw retention: no months summarised yet — refusing to delete"
        )
        return {"observations": 0, "scores": 0, "taf_scores": 0, "map_rows": 0}

    # Build list of (month_start, month_end) ranges fully inside the cutoff
    # AND in summarised_months.
    safe_ranges: list[tuple[datetime, datetime]] = []
    for (y, m) in sorted(summarised_months):
        month_start = datetime(y, m, 1, tzinfo=timezone.utc)
        month_end = (
            datetime(y + 1, 1, 1, tzinfo=timezone.utc)
            if m == 12
            else datetime(y, m + 1, 1, tzinfo=timezone.utc)
        )
        if month_end <= cutoff:
            safe_ranges.append((month_start, month_end))

    if not safe_ranges:
        return {"observations": 0, "scores": 0, "taf_scores": 0, "map_rows": 0}

    # All three child FKs to verification_observations.id use ON DELETE
    # CASCADE, so a parent-first delete would also work — but the child
    # rows would already be gone by the time we ran the child deletes,
    # making our score/taf counters useless for monitoring. Deleting
    # children first gives us accurate per-table metrics.
    obs_deleted = 0
    score_deleted = 0
    taf_deleted = 0
    map_deleted = 0
    for start, end in safe_ranges:
        # FlightVerificationMapRow has no observation_time of its own —
        # find linked map rows by joining through observation_id.
        obs_id_subquery = (
            select(VerificationObservationRow.id)
            .where(VerificationObservationRow.observation_time >= start)
            .where(VerificationObservationRow.observation_time < end)
            .scalar_subquery()
        )
        r = db.execute(
            FlightVerificationMapRow.__table__.delete()
            .where(FlightVerificationMapRow.observation_id.in_(obs_id_subquery))
        )
        map_deleted += r.rowcount or 0
        r = db.execute(
            VerificationScoreRow.__table__.delete()
            .where(VerificationScoreRow.observation_time >= start)
            .where(VerificationScoreRow.observation_time < end)
        )
        score_deleted += r.rowcount or 0
        r = db.execute(
            TafVerificationScoreRow.__table__.delete()
            .where(TafVerificationScoreRow.observation_time >= start)
            .where(TafVerificationScoreRow.observation_time < end)
        )
        taf_deleted += r.rowcount or 0
        r = db.execute(
            VerificationObservationRow.__table__.delete()
            .where(VerificationObservationRow.observation_time >= start)
            .where(VerificationObservationRow.observation_time < end)
        )
        obs_deleted += r.rowcount or 0

    # Caller commits — matches the rollup convention. expire_all so any
    # cached ORM objects representing the deleted rows reflect their absence.
    db.flush()
    db.expire_all()
    logger.info(
        "Raw retention: pruned %d observations, %d scores, %d TAF scores, "
        "%d map rows (retain_days=%d, cutoff=%s)",
        obs_deleted, score_deleted, taf_deleted, map_deleted,
        retain_days, cutoff.isoformat(),
    )
    return {
        "observations": obs_deleted,
        "scores": score_deleted,
        "taf_scores": taf_deleted,
        "map_rows": map_deleted,
    }


# ---------------------------------------------------------------------------
# MySQL partition maintenance — Phase 4
# ---------------------------------------------------------------------------


def ensure_future_partitions(db: Session, months_ahead: int = 3) -> int:
    """Pre-create monthly partitions for verification_observations on MySQL.

    Without this, an INSERT for a date past the highest existing partition
    would fail. SQLite is a no-op (no partitioning).

    Returns the number of partitions added.
    """
    bind = db.get_bind()
    if bind.dialect.name != "mysql":
        return 0

    # Discover existing partitions
    rows = db.execute(text(
        "SELECT partition_name, partition_description "
        "FROM information_schema.PARTITIONS "
        "WHERE table_schema = DATABASE() "
        "AND table_name = 'verification_observations' "
        "AND partition_name IS NOT NULL"
    )).all()

    existing_names = {r[0] for r in rows}
    if not existing_names:
        # Table isn't partitioned yet (migration 054 hasn't run). Nothing to do.
        logger.debug(
            "ensure_future_partitions: verification_observations not partitioned"
        )
        return 0

    # Compute target month names for the next N months.
    now = datetime.now(timezone.utc)
    targets: list[tuple[int, int]] = []
    y, m = now.year, now.month
    for _ in range(months_ahead):
        m += 1
        if m > 12:
            m = 1
            y += 1
        targets.append((y, m))

    added = 0
    for (y, m) in targets:
        name = f"p_{y:04d}{m:02d}"
        if name in existing_names:
            continue
        # TO_DAYS upper bound = first day of the *following* month
        next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
        upper = f"{next_y:04d}-{next_m:02d}-01"
        # REORGANIZE the catch-all p_future partition to add the new range.
        db.execute(text(
            f"ALTER TABLE verification_observations "
            f"REORGANIZE PARTITION p_future INTO ("
            f"PARTITION {name} VALUES LESS THAN (TO_DAYS('{upper}')), "
            f"PARTITION p_future VALUES LESS THAN MAXVALUE)"
        ))
        added += 1
        logger.info("Added partition %s (upper=%s)", name, upper)

    return added
