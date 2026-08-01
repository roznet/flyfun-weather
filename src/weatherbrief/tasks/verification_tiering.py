"""Configuration gates for the verification data tiering (#522).

The tiering work lands as one codebase but rolls out over four phases, each
of which needs production observation before the next is safe. Rather than
shipping four branches, every phase-switching decision reads a gate from
here, so enabling a phase is an env-var change plus a restart — and rolling
one back is the same change in reverse.

Three explicit tiers once all phases are live:

===================  ==========================================  ===========
Tier                 Store                                       Retention
===================  ==========================================  ===========
Raw operational      MySQL (observations, scores, taf_scores,    180 days
                     airport_forecast_snapshots)                 (snapshots 10)
Aggregates           MySQL rollup tables (per-airport daily,     forever
                     global daily, TAF daily, monthly)
Row-level archive    Parquet under ``DATA_DIR/archive/``         forever
===================  ==========================================  ===========

Gate summary (all default to the pre-#522 behaviour, so a deploy that only
ships the code changes nothing):

``VERIFICATION_GLOBAL_ROLLUP_READS``
    Phase 1. ``1`` points the unfiltered dashboard/digest aggregates at
    ``verification_global_daily_stats`` / ``verification_activity_daily`` /
    ``taf_verification_daily`` instead of ``verification_daily_stats`` and
    raw scores. Requires the backfill to have run first — flipping it on an
    un-backfilled database shows empty history, not wrong numbers.

``VERIFICATION_ARCHIVE_ENABLED``
    Phase 2. ``1`` runs the Parquet archive writer from the daily retention
    loop. The CLI (``verify archive ...``) works regardless — the gate only
    controls scheduling.

``VERIFICATION_RAW_RETENTION_DAYS``
    Phase 3. Online window for raw obs/scores/taf-scores. Left at the
    pre-#522 sentinel (9999 = pruning disabled) so the code can ship dark;
    Phase 3 sets it to 180.

``VERIFICATION_PRUNE_REQUIRE_ARCHIVE``
    Phase 3 safety belt, on by default. No archive manifest for a period →
    that period is never deleted, and the snapshot prune stalls on days with
    no snapshot manifest. Only turn this off if the archive is deliberately
    abandoned.

``SNAPSHOT_INBOX_RETENTION_DAYS``
    Phase 3. Rotates ``eu-*/us-*.sqlite`` transport artifacts out of
    ``SNAPSHOT_INBOX_DIR``. ``0`` (the shipping default) keeps them forever,
    matching today's behaviour; Phase 3 sets it to 30.

``VERIFICATION_MONTHLY_ROLLUP_ENABLED``
    Phase 4. ``1`` rolls completed months into ``verification_monthly_stats``
    from the daily table in the retention loop.

``VERIFICATION_DAILY_STATS_RETENTION_MONTHS``
    Phase 4 follow-up, ``0`` = disabled. Prunes ``verification_daily_stats``
    rows older than N months once the monthly rollup has been validated
    against the daily data it summarises. Daily stats are re-derivable from
    the raw Parquet archive, so they are pruned rather than archived.
"""

from __future__ import annotations

import os
from pathlib import Path

# Pre-#522 sentinel: retention is *disabled* at or above this value. Kept as
# the default so shipping the code prunes nothing until Phase 3 sets 180.
RAW_RETENTION_DISABLED_DAYS = 9999

# Phase 3's target value, for documentation and for the plan's flip step.
RAW_RETENTION_TARGET_DAYS = 180


def _flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var. ``1``/``true``/``yes``/``on`` are true."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    """Read an int env var, falling back to *default* on anything unparsable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Phase 1 — global rollups + read switch
# ---------------------------------------------------------------------------


def global_rollup_reads_enabled() -> bool:
    """Whether unfiltered aggregates read the global rollup tables.

    Only affects *unfiltered* reads. A country/airport-filtered dashboard
    request keeps reading the per-airport ``verification_daily_stats``, which
    is the only table that can answer it.
    """
    return _flag("VERIFICATION_GLOBAL_ROLLUP_READS", False)


# ---------------------------------------------------------------------------
# Phase 2 — Parquet archive
# ---------------------------------------------------------------------------


def archive_enabled() -> bool:
    """Whether the daily retention loop runs the Parquet archive writer."""
    return _flag("VERIFICATION_ARCHIVE_ENABLED", False)


def data_dir() -> Path:
    """The shared artifact root (``DATA_DIR``, default ``data``)."""
    return Path(os.environ.get("DATA_DIR", "data"))


def archive_root() -> Path:
    """Root of the row-level Parquet archive: ``DATA_DIR/archive/verification``."""
    return data_dir() / "archive" / "verification"


# ---------------------------------------------------------------------------
# Phase 3 — pruning
# ---------------------------------------------------------------------------


def raw_retention_days() -> int:
    """Online window (days) for raw observations/scores/TAF scores."""
    return _int("VERIFICATION_RAW_RETENTION_DAYS", RAW_RETENTION_DISABLED_DAYS)


def raw_retention_disabled(retain_days: int | None = None) -> bool:
    """Whether raw pruning is switched off entirely."""
    days = raw_retention_days() if retain_days is None else retain_days
    return days >= RAW_RETENTION_DISABLED_DAYS


def prune_requires_archive() -> bool:
    """Whether a period needs a verified archive manifest before deletion."""
    return _flag("VERIFICATION_PRUNE_REQUIRE_ARCHIVE", True)


def snapshot_prune_requires_archive() -> bool:
    """Whether the 10-day snapshot prune waits for a snapshot manifest.

    Only once archiving is actually running: with the archive off there would
    never be a manifest, and stalling the snapshot prune would turn a bounded
    table into an unbounded one.
    """
    return archive_enabled() and prune_requires_archive()


def snapshot_inbox_retention_days() -> int:
    """Rotation window for ``SNAPSHOT_INBOX_DIR`` artifacts. ``0`` = keep."""
    return _int("SNAPSHOT_INBOX_RETENTION_DAYS", 0)


# ---------------------------------------------------------------------------
# Phase 4 — monthly stats
# ---------------------------------------------------------------------------


def monthly_rollup_enabled() -> bool:
    """Whether the retention loop rolls completed months into monthly stats."""
    return _flag("VERIFICATION_MONTHLY_ROLLUP_ENABLED", False)


def daily_stats_retention_months() -> int:
    """Retention (months) for ``verification_daily_stats``. ``0`` = keep."""
    return _int("VERIFICATION_DAILY_STATS_RETENTION_MONTHS", 0)
