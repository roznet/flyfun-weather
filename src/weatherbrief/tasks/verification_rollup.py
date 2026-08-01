"""Monthly rollup of verification scores into verification_monthly_stats.

Aggregates raw verification_scores and taf_verification_scores into
per-airport, per-model, per-days_out monthly summaries with category
direction counts and continuous error metrics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    TafVerificationScoreRow,
    VerificationMonthlyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_gust import gust_aggregate_columns

logger = logging.getLogger(__name__)

# Severity ordering: VFR=0, MVFR=1, IFR=2, LIFR=3
# Higher index = worse conditions.
# Optimistic means forecast is better than observed (lower index = dangerous).
# Pessimistic means forecast is worse than observed (higher index = conservative).
_CAT_INDEX = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}

_ADVISORY_INDEX = {"green": 0, "amber": 1, "red": 2}


def category_direction(obs_cat: str | None, fcst_cat: str | None) -> str:
    """Classify forecast error direction and magnitude.

    Returns one of: 'match', 'optimistic_1', 'optimistic_2',
    'pessimistic_1', 'pessimistic_2', or 'skip' if either is None.
    """
    if obs_cat is None or fcst_cat is None:
        return "skip"
    obs_i = _CAT_INDEX.get(obs_cat.upper())
    fcst_i = _CAT_INDEX.get(fcst_cat.upper())
    if obs_i is None or fcst_i is None:
        return "skip"

    diff = fcst_i - obs_i  # negative = optimistic (forecast too good)
    if diff == 0:
        return "match"
    elif diff < 0:
        return "optimistic_1" if abs(diff) == 1 else "optimistic_2"
    else:
        return "pessimistic_1" if diff == 1 else "pessimistic_2"


def advisory_direction(obs_adv: str | None, fcst_adv: str | None) -> str:
    """Classify wind advisory error direction.

    Returns 'match', 'optimistic', 'pessimistic', or 'skip'.
    """
    if obs_adv is None or fcst_adv is None:
        return "skip"
    obs_i = _ADVISORY_INDEX.get(obs_adv.lower())
    fcst_i = _ADVISORY_INDEX.get(fcst_adv.lower())
    if obs_i is None or fcst_i is None:
        return "skip"

    diff = fcst_i - obs_i
    if diff == 0:
        return "match"
    return "optimistic" if diff < 0 else "pessimistic"


def _lead_hours_to_days_out(lead_hours: int) -> int:
    """Bucket lead_hours into days_out (0-24h=0, 24-48h=1, etc.)."""
    return max(0, lead_hours // 24)


def completed_months(db: Session) -> list[datetime]:
    """Return first-of-month datetimes for months with raw scores that
    haven't been rolled up yet.

    A month is considered complete when the current UTC time is past
    the end of that month.
    """
    now = datetime.now(timezone.utc)

    # Find earliest observation across both model and TAF scores
    model_min = db.execute(
        select(func.min(VerificationScoreRow.observation_time))
    ).scalar()
    taf_min = db.execute(
        select(func.min(TafVerificationScoreRow.observation_time))
    ).scalar()

    candidates = [t for t in (model_min, taf_min) if t is not None]
    if not candidates:
        return []

    min_time: datetime = min(candidates)

    # Already rolled up months
    existing = set(
        db.execute(
            select(VerificationMonthlyStatsRow.month).distinct()
        ).scalars().all()
    )
    # Normalize existing to (year, month) for comparison
    existing_ym = set()
    for dt in existing:
        if dt is not None:
            existing_ym.add((dt.year, dt.month))

    # Generate candidate months from min_time to now
    months: list[datetime] = []
    y, m = min_time.year, min_time.month
    while True:
        month_start = datetime(y, m, 1, tzinfo=timezone.utc)
        # Next month start
        if m == 12:
            next_start = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_start = datetime(y, m + 1, 1, tzinfo=timezone.utc)

        # Only include completed months not already rolled up
        if next_start <= now and (y, m) not in existing_ym:
            months.append(month_start)

        if next_start > now:
            break
        y, m = next_start.year, next_start.month

    return months


def rollup_month(db: Session, month_start: datetime) -> int:
    """Aggregate raw scores for a single month into verification_monthly_stats.

    Returns the number of rollup rows created.
    """
    if month_start.month == 12:
        month_end = datetime(month_start.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(month_start.year, month_start.month + 1, 1, tzinfo=timezone.utc)

    # Delete any existing rollup for this month (idempotent re-run).
    # Flush + expunge to clear identity map so re-inserted rows don't conflict.
    stale = db.execute(
        select(VerificationMonthlyStatsRow).where(
            VerificationMonthlyStatsRow.month == month_start
        )
    ).scalars().all()
    for row in stale:
        db.delete(row)
    if stale:
        db.flush()

    rows_created = 0
    rows_created += _rollup_model_scores(db, month_start, month_end)
    rows_created += _rollup_taf_scores(db, month_start, month_end)

    db.flush()
    logger.info(
        "Rolled up %d rows for %s",
        rows_created, month_start.strftime("%Y-%m"),
    )
    return rows_created


def _obs_conditioned_gust_stats(
    db: Session, month_start: datetime, month_end: datetime,
) -> dict[tuple[str, str, int, str], dict]:
    """Per-group gust aggregates that need the observation row (#491).

    The observed gust and the realised peak live on
    ``verification_observations``, so the occurrence contingency and the
    forecast-flagged magnitude can't be derived from a score row alone. One
    grouped SQL query per month keeps that off the Python path — loading a
    month of observations into memory is not an option at watchlist scale.

    Keyed like ``_rollup_model_scores``'s groups: (source, model, days_out,
    icao). TAF rows have no entry — see ``_compute_taf_stats``.
    """
    rows = db.execute(
        select(
            VerificationScoreRow.source,
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.icao,
            *gust_aggregate_columns(),
        )
        .join(
            VerificationObservationRow,
            VerificationScoreRow.observation_id == VerificationObservationRow.id,
        )
        .where(
            VerificationScoreRow.observation_time >= month_start,
            VerificationScoreRow.observation_time < month_end,
        )
        .group_by(
            VerificationScoreRow.source,
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.icao,
        )
    ).all()

    out: dict[tuple[str, str, int, str], dict] = {}
    for r in rows:
        n_flagged_peak = int(r.n_gust_flagged_peak or 0)
        out[(r.source, r.model, r.days_out, r.icao)] = {
            "n_model_gust_flag": int(r.n_model_gust_flag or 0),
            "n_obs_gust": int(r.n_obs_gust or 0),
            "n_gust_flag_hit": int(r.n_gust_flag_hit or 0),
            "n_gust_flagged_peak": n_flagged_peak,
            "gust_over_peak_bias_kt": (
                float(r.sum_gust_flagged_over_peak_kt) / n_flagged_peak
                if n_flagged_peak and r.sum_gust_flagged_over_peak_kt is not None
                else None
            ),
        }
    return out


def _rollup_model_scores(
    db: Session, month_start: datetime, month_end: datetime,
) -> int:
    """Aggregate verification_scores into monthly stats."""
    stmt = (
        select(VerificationScoreRow)
        .where(
            VerificationScoreRow.observation_time >= month_start,
            VerificationScoreRow.observation_time < month_end,
        )
    )
    scores = db.execute(stmt).scalars().all()

    obs_gust_stats = _obs_conditioned_gust_stats(db, month_start, month_end)

    # Group by (source, model, days_out, icao)
    groups: dict[tuple[str, str, int, str], list[VerificationScoreRow]] = {}
    for s in scores:
        key = (s.source, s.model, s.days_out, s.icao)
        groups.setdefault(key, []).append(s)

    rows_created = 0
    for key, group in groups.items():
        source, model, days_out, icao = key
        stats = _compute_model_stats(group)
        stats.update(obs_gust_stats.get(key, {}))
        row = VerificationMonthlyStatsRow(
            month=month_start,
            source=source,
            model=model,
            days_out=days_out,
            icao=icao,
            **stats,
        )
        db.add(row)
        rows_created += 1

    return rows_created


def _rollup_taf_scores(
    db: Session, month_start: datetime, month_end: datetime,
) -> int:
    """Aggregate taf_verification_scores into monthly stats (model='taf')."""
    stmt = (
        select(TafVerificationScoreRow)
        .where(
            TafVerificationScoreRow.observation_time >= month_start,
            TafVerificationScoreRow.observation_time < month_end,
        )
    )
    scores = db.execute(stmt).scalars().all()

    # Group by (source, days_out_bucket, icao)
    groups: dict[tuple[str, int, str], list[TafVerificationScoreRow]] = {}
    for s in scores:
        days_out = _lead_hours_to_days_out(s.lead_hours)
        key = (s.source, days_out, s.icao)
        groups.setdefault(key, []).append(s)

    rows_created = 0
    for (source, days_out, icao), group in groups.items():
        stats = _compute_taf_stats(group)
        row = VerificationMonthlyStatsRow(
            month=month_start,
            source=source,
            model="taf",
            days_out=days_out,
            icao=icao,
            **stats,
        )
        db.add(row)
        rows_created += 1

    return rows_created


def _accumulate_direction_counts(
    scores: Sequence,
    obs_cat_attr: str,
    fcst_cat_attr: str,
    obs_adv_attr: str,
    fcst_adv_attr: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Accumulate category and advisory direction counts from any score type."""
    cat = {"match": 0, "optimistic_1": 0, "optimistic_2": 0,
           "pessimistic_1": 0, "pessimistic_2": 0}
    adv = {"match": 0, "optimistic": 0, "pessimistic": 0}

    for s in scores:
        d = category_direction(getattr(s, obs_cat_attr), getattr(s, fcst_cat_attr))
        if d in cat:
            cat[d] += 1

        ad = advisory_direction(getattr(s, obs_adv_attr), getattr(s, fcst_adv_attr))
        if ad in adv:
            adv[ad] += 1

    return cat, adv


def _collect_deltas(
    scores: Sequence, attr: str,
) -> list[float]:
    """Collect non-None values for a delta attribute."""
    return [getattr(s, attr) for s in scores if getattr(s, attr) is not None]


def _hit_miss_counts(
    scores: Sequence, obs_attr: str, fcst_attr: str,
) -> tuple[int, int, int, bool]:
    """Count hit/miss/false-alarm for a boolean obs vs forecast pair."""
    hit = miss = false_alarm = 0
    has_data = False
    for s in scores:
        obs_val = getattr(s, obs_attr)
        fcst_val = getattr(s, fcst_attr)
        if obs_val is None or fcst_val is None:
            continue
        has_data = True
        if obs_val and fcst_val:
            hit += 1
        elif obs_val and not fcst_val:
            miss += 1
        elif not obs_val and fcst_val:
            false_alarm += 1
    return hit, miss, false_alarm, has_data


def _base_stats(cat: dict[str, int], adv: dict[str, int], n: int,
                ceiling_deltas: list[float], visibility_deltas: list[float],
                wind_deltas: list[float], gust_deltas: list[float]) -> dict:
    """Build the stats dict shared by model and TAF rollups.

    ``gust_deltas`` is the obs-flagged conditioning only: a non-NULL gust
    delta requires an observed gust, so this is "on the hours the airport
    actually gusted, how far off was the forecast?" (#491). The
    forecast-flagged conditioning needs the observation row and is merged in
    by ``_obs_conditioned_gust_stats``.
    """
    return {
        "n_scores": n,
        "n_cat_match": cat["match"],
        "n_cat_optimistic_1": cat["optimistic_1"],
        "n_cat_optimistic_2": cat["optimistic_2"],
        "n_cat_pessimistic_1": cat["pessimistic_1"],
        "n_cat_pessimistic_2": cat["pessimistic_2"],
        "n_advisory_match": adv["match"],
        "n_advisory_optimistic": adv["optimistic"],
        "n_advisory_pessimistic": adv["pessimistic"],
        "ceiling_mae_ft": _mae(ceiling_deltas),
        "ceiling_bias_ft": _mean(ceiling_deltas),
        "visibility_mae_m": _mae(visibility_deltas),
        "wind_speed_mae_kt": _mae(wind_deltas),
        "wind_gust_mae_kt": _mae(gust_deltas),
        "wind_gust_bias_kt": _mean(gust_deltas),
        "n_gust": len(gust_deltas),
    }


def _compute_model_stats(scores: Sequence[VerificationScoreRow]) -> dict:
    """Compute aggregate stats from a group of model verification scores."""
    cat, adv = _accumulate_direction_counts(
        scores, "obs_flight_category", "model_flight_category",
        "obs_wind_advisory", "model_wind_advisory",
    )

    ceiling_deltas = _collect_deltas(scores, "ceiling_delta_ft")
    visibility_deltas = _collect_deltas(scores, "visibility_delta_m")
    wind_deltas = _collect_deltas(scores, "wind_speed_delta_kt")
    gust_deltas = _collect_deltas(scores, "wind_gust_delta_kt")
    temp_deltas = _collect_deltas(scores, "temperature_delta_c")

    p_hit, p_miss, p_fa, has_precip = _hit_miss_counts(
        scores, "obs_has_precipitation", "model_has_precipitation",
    )
    c_hit, c_miss, c_fa, has_convection = _hit_miss_counts(
        scores, "obs_has_convection", "model_has_convection",
    )

    stats = _base_stats(
        cat, adv, len(scores), ceiling_deltas, visibility_deltas,
        wind_deltas, gust_deltas,
    )
    stats.update({
        "temperature_mae_c": _mae(temp_deltas),
        "n_precip_hit": p_hit if has_precip else None,
        "n_precip_miss": p_miss if has_precip else None,
        "n_precip_false_alarm": p_fa if has_precip else None,
        "n_convection_hit": c_hit if has_convection else None,
        "n_convection_miss": c_miss if has_convection else None,
        "n_convection_false_alarm": c_fa if has_convection else None,
    })
    return stats


def _compute_taf_stats(scores: Sequence[TafVerificationScoreRow]) -> dict:
    """Compute aggregate stats from a group of TAF verification scores."""
    cat, adv = _accumulate_direction_counts(
        scores, "obs_flight_category", "taf_flight_category",
        "obs_wind_advisory", "taf_wind_advisory",
    )

    ceiling_deltas = _collect_deltas(scores, "ceiling_delta_ft")
    visibility_deltas = _collect_deltas(scores, "visibility_delta_m")
    wind_deltas = _collect_deltas(scores, "wind_speed_delta_kt")
    gust_deltas = _collect_deltas(scores, "wind_gust_delta_kt")

    stats = _base_stats(
        cat, adv, len(scores), ceiling_deltas, visibility_deltas,
        wind_deltas, gust_deltas,
    )
    stats.update({
        "temperature_mae_c": None,
        # Occurrence + forecast-flagged magnitude need the observation row,
        # and the TAF group key (a lead_hours bucket) has no SQL expression
        # that truncates identically on SQLite and MySQL. TAF gust occurrence
        # is served by ``verification_stats.get_gust_accuracy``, which reads
        # taf_verification_scores joined to observations at query time — the
        # same treatment every other TAF stat gets (#154).
        "n_model_gust_flag": None,
        "n_obs_gust": None,
        "n_gust_flag_hit": None,
        "n_gust_flagged_peak": None,
        "gust_over_peak_bias_kt": None,
        "n_precip_hit": None,
        "n_precip_miss": None,
        "n_precip_false_alarm": None,
        "n_convection_hit": None,
        "n_convection_miss": None,
        "n_convection_false_alarm": None,
    })
    return stats


def _mae(values: list[float]) -> float | None:
    """Mean absolute error, or None if no values."""
    if not values:
        return None
    return sum(abs(v) for v in values) / len(values)


def _mean(values: list[float]) -> float | None:
    """Signed mean (bias), or None if no values."""
    if not values:
        return None
    return sum(values) / len(values)


def run_monthly_rollup(db: Session) -> int:
    """Find all completed months needing rollup and process them.

    Returns total rollup rows created.
    """
    months = completed_months(db)
    if not months:
        logger.info("No new months to roll up")
        return 0

    total = 0
    for month in months:
        total += rollup_month(db, month)

    return total
