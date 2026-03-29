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

from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow, FlightRow, UserRow

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

    # Query all packs joined to their flight (for departure_time + user_id).
    stmt = (
        select(BriefingPackRow, FlightRow.departure_time, FlightRow.user_id)
        .join(FlightRow, BriefingPackRow.flight_id == FlightRow.id)
    )
    rows = db.execute(stmt).all()

    for pack, departure_time, user_id in rows:
        if departure_time is None:
            continue

        dep = departure_time if departure_time.tzinfo else departure_time.replace(tzinfo=timezone.utc)
        age_days = (now - dep).days

        inactive = user_id in inactive_user_ids
        t2_days = config.t2_inactive_days if inactive else config.t2_active_days

        try:
            pack_dir = Path(pack.artifact_path) if pack.artifact_path else None

            if age_days >= t2_days:
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
