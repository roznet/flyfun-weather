"""Build and read pre-computed cache for verification dashboard and maps.

The cache is stored in the ``verification_cache`` table.  Each entry is a
JSON blob keyed by a deterministic cache key (e.g. ``stats:standalone:30d``).

Cache is rebuilt after each standalone verification cycle by calling
:func:`rebuild_all`.  API endpoints call :func:`get_cached` to read entries.
On cache miss or staleness the caller falls back to a live query.

Cache key catalogue:

- ``stats:{source}:{period}`` — dashboard digest payload
- ``bias_leaderboard:{model}:{days_out}:{period}`` — top airports model
  over-promises for (#154)
- ``forecast_map:{day}:{hour}`` — pan-European weather overview map

The legacy ``verif_map:*`` keys (verification-bias map view) were removed
in #154; the view that consumed them is also gone.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    VerificationCacheRow,
    VerificationScoreRow,
)

logger = logging.getLogger(__name__)

# Periods and sources to pre-compute for the stats dashboard
_PERIODS = {"24h": 24, "7d": 168, "30d": 720}
_SOURCES = ("flight", "standalone")

# Bias leaderboard: per-(model, days_out, period). Periods kept narrow —
# 24h is too small for stable rankings; 90d is interesting for trend but
# can be computed on demand.
_LEADERBOARD_MODELS = ("gfs", "icon", "ecmwf")
_LEADERBOARD_DAYS_OUT = (0, 1, 2)
_LEADERBOARD_PERIODS = {"7d": 168, "30d": 720, "90d": 2160}

# Forecast map: days ahead × sample hours. The grid is not rectangular — the
# far day carries fewer hours because ECMWF only delivers 6-hourly steps out
# there — so it comes from the horizon policy rather than a literal here.
from weatherbrief.tasks.forecast_grid import forecast_days, sample_hours_for_day


# ---------------------------------------------------------------------------
# Read cache
# ---------------------------------------------------------------------------


def get_cached(db: Session, cache_key: str) -> dict | None:
    """Return cached data as a dict, or None if not found."""
    row = db.execute(
        select(VerificationCacheRow.data_json)
        .where(VerificationCacheRow.cache_key == cache_key)
    ).scalar()
    if row is None:
        return None
    return json.loads(row)


def get_cache_meta(db: Session, cache_key: str) -> tuple[datetime | None, datetime | None]:
    """Return (computed_at, source_max_time) for a cache entry, or (None, None)."""
    row = db.execute(
        select(VerificationCacheRow.computed_at, VerificationCacheRow.source_max_time)
        .where(VerificationCacheRow.cache_key == cache_key)
    ).one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]


def get_source_max_time(db: Session, source: str) -> datetime | None:
    """Quick check: latest observation_time in verification_scores for source."""
    return db.execute(
        select(func.max(VerificationScoreRow.observation_time))
        .where(VerificationScoreRow.source == source)
    ).scalar()


def get_snapshot_max_time(db: Session) -> datetime | None:
    """Quick check: latest fetched_at in airport_forecast_snapshots."""
    return db.execute(
        select(func.max(AirportForecastSnapshotRow.fetched_at))
    ).scalar()


def is_stale(db: Session, cache_key: str, source: str) -> bool:
    """Return True if the cache entry is missing or stale.

    For forecast map keys, compares against snapshot fetched_at instead of
    verification scores.

    Forecast-map keys (``source == "snapshot"``) are *relative-day* indexed
    (``forecast_map:{day}:{hour}``) but the cached payload holds an *absolute*
    forecast date. Snapshots only refresh on the twice-daily fetch cycles, so
    after a UTC-midnight rollover the snapshot max-time is unchanged yet the
    relative day now maps to a new calendar date. Without a date check the
    stale entry would be served — e.g. D-0 showing yesterday's data labelled
    today. So an entry computed on an earlier UTC day is always stale, forcing
    the live path (which re-derives the correct absolute hour). See
    designs/forecast-page.md.
    """
    computed_at, cached_max = get_cache_meta(db, cache_key)
    if cached_max is None:
        return True
    if source == "snapshot":
        if computed_at is not None:
            ca = computed_at.astimezone(timezone.utc) if computed_at.tzinfo else computed_at
            if ca.date() < datetime.now(timezone.utc).date():
                return True
        live_max = get_snapshot_max_time(db)
    else:
        live_max = get_source_max_time(db, source)
    if live_max is None:
        return True
    return live_max > cached_max


# ---------------------------------------------------------------------------
# Write cache
# ---------------------------------------------------------------------------


def _upsert(db: Session, cache_key: str, data: dict | list,
            source_max_time: datetime | None) -> None:
    """Insert or update a cache row."""
    now = datetime.now(timezone.utc)
    row = db.execute(
        select(VerificationCacheRow)
        .where(VerificationCacheRow.cache_key == cache_key)
    ).scalar_one_or_none()
    if row is None:
        row = VerificationCacheRow(cache_key=cache_key)
        db.add(row)
    row.computed_at = now
    row.source_max_time = source_max_time
    row.data_json = json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Rebuild functions
# ---------------------------------------------------------------------------


def rebuild_stats_cache(db: Session) -> int:
    """Rebuild cached stats dashboard responses for all source × period combos.

    Returns the number of cache entries written.
    """
    from weatherbrief.tasks.verification_stats import get_digest_data

    now = datetime.now(timezone.utc)
    count = 0

    for source in _SOURCES:
        source_max = get_source_max_time(db, source)
        for period_label, hours in _PERIODS.items():
            since = now - timedelta(hours=hours)
            data = get_digest_data(
                db, since, now,
                source=source,
                period_label=period_label,
                include_7d=(period_label != "7d"),
            )
            cache_key = f"stats:{source}:{period_label}"
            _upsert(db, cache_key, data.model_dump(mode="json"), source_max)
            count += 1

    db.flush()
    return count


def rebuild_bias_leaderboard_cache(db: Session) -> int:
    """Rebuild cached optimistic-bias leaderboard responses.

    Keyed by ``bias_leaderboard:{model}:{days_out}:{period}``. The
    leaderboard is sourced from ``verification_daily_stats`` so each
    rebuild is a small GROUP BY — orders of magnitude cheaper than the
    legacy ``verif_map`` rebuild it replaces.

    Returns the number of cache entries written.
    """
    from weatherbrief.tasks.verification_stats import (
        get_optimistic_bias_leaderboard,
    )

    now = datetime.now(timezone.utc)
    source_max = get_source_max_time(db, "standalone")
    count = 0

    for period_label, hours in _LEADERBOARD_PERIODS.items():
        since = now - timedelta(hours=hours)
        for model in _LEADERBOARD_MODELS:
            for days_out in _LEADERBOARD_DAYS_OUT:
                rows = get_optimistic_bias_leaderboard(
                    db, since, now,
                    model=model, days_out=days_out,
                    source="standalone",
                )
                payload = [r.model_dump(mode="json") for r in rows]
                cache_key = f"bias_leaderboard:{model}:{days_out}:{period_label}"
                _upsert(db, cache_key, payload, source_max)
                count += 1

    db.flush()
    return count


def rebuild_forecast_map_cache(db: Session, airports_db_path: str) -> int:
    """Rebuild cached forecast map responses for available day × hour combos.

    Returns the number of cache entries written.
    """
    from weatherbrief.tasks.map_queries import (
        enrich_with_observations,
        get_forecast_map_data,
    )

    now = datetime.now(timezone.utc)
    count = 0

    # Source max time: latest fetched_at in snapshots
    snapshot_max = db.execute(
        select(func.max(AirportForecastSnapshotRow.fetched_at))
    ).scalar()

    for day in forecast_days():
        target_date = (now + timedelta(days=day)).date()
        for hour in sample_hours_for_day(day):
            forecast_hour = datetime(
                target_date.year, target_date.month, target_date.day,
                hour, 0, 0, tzinfo=timezone.utc,
            )
            # Only cache hours that have data
            has_data = db.execute(
                select(AirportForecastSnapshotRow.id)
                .where(AirportForecastSnapshotRow.forecast_hour == forecast_hour)
                .limit(1)
            ).scalar()
            if not has_data:
                continue

            data = get_forecast_map_data(db, forecast_hour, airports_db_path)
            # D-0: enrich with latest METAR/TAF observations from verification
            if day == 0:
                data = enrich_with_observations(db, forecast_hour, data)
            cache_key = f"forecast_map:{day}:{hour}"
            _upsert(db, cache_key, data, snapshot_max)
            count += 1

    db.flush()
    return count


def rebuild_all(
    db: Session,
    airports_db_path: str,
    *,
    include_forecast_map: bool = True,
) -> dict:
    """Rebuild all caches. Called after standalone verification cycles.

    ``include_forecast_map=False`` skips the forecast_map rebuild — used by
    light (score-only) cycles where snapshots haven't changed and the
    forecast_map cache would be regenerated to identical content.

    Returns a summary dict with counts.
    """
    t0 = time.monotonic()

    stats_count = rebuild_stats_cache(db)
    leaderboard_count = rebuild_bias_leaderboard_cache(db)
    if include_forecast_map:
        forecast_map_count = rebuild_forecast_map_cache(db, airports_db_path)
    else:
        forecast_map_count = 0

    db.commit()
    duration_ms = int((time.monotonic() - t0) * 1000)

    if include_forecast_map:
        logger.info(
            "Cache rebuild: %d stats + %d bias_leaderboard + %d forecast_map entries (%dms)",
            stats_count, leaderboard_count, forecast_map_count, duration_ms,
        )
    else:
        logger.info(
            "Cache rebuild: %d stats + %d bias_leaderboard entries, "
            "forecast_map skipped (%dms)",
            stats_count, leaderboard_count, duration_ms,
        )
    return {
        "stats": stats_count,
        "bias_leaderboard": leaderboard_count,
        "forecast_map": forecast_map_count,
        "duration_ms": duration_ms,
    }
