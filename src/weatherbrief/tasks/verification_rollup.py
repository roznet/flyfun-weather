"""Monthly rollup of NWP verification scores into verification_monthly_stats.

Since #522 this is a **roll of the daily rollup**, not a second pass over raw
scores: one ``DELETE WHERE month=?`` plus one ``INSERT … SELECT … GROUP BY``
over ``verification_daily_stats``. The daily table's SUM columns were designed
to compose exactly this way (see the "SUMs, not averages" note on
:class:`VerificationDailyStatsRow`), so the month is a pure addition of its
days and cannot disagree with the days it summarises.

What that replaced: a loader that pulled every raw score row for the month
into Python (`select(VerificationScoreRow)` unbounded), grouped them in a
dict, and computed means — millions of ORM objects for a table whose whole
point is that it is small. It also meant the monthly numbers were derived
independently of the daily numbers and could drift from them.

Two deliberate scope changes came with the rewrite:

- **TAF monthly is out of scope.** ``taf_verification_scores`` has no
  ``model``/``days_out`` columns, which is why it never fitted this table's
  key shape. It is now served by ``taf_verification_daily`` at query time, so
  the ``model='taf'`` rows this module used to write are no longer produced —
  a re-roll of a month drops any that a previous version left behind.
- **Precip/convection "no data" is no longer distinguishable from "no
  events".** The old Python path stored NULL when no row in the group had
  both an observed and a forecast flag, and 0/0/0 when they were all correct
  negatives. The daily table only carries the three event counts, so both
  now read 0/0/0. Nothing consumes these columns today (the dashboard reads
  the daily tables); they exist for offline analysis, where "0 hits, 0
  misses, 0 false alarms" is the honest summary either way.

Direction classification (``category_direction`` / ``advisory_direction``)
stays here as the reference definition — the SQL CASE expressions in
``verification_daily_rollup`` encode the same rules, and
``tests/test_verification_daily_rollup`` asserts the two agree.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import DateTime, bindparam, func, insert, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    VerificationDailyStatsRow,
    VerificationMonthlyStatsRow,
)

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


def _month_end(month_start: datetime) -> datetime:
    if month_start.month == 12:
        return datetime(month_start.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(
        month_start.year, month_start.month + 1, 1, tzinfo=timezone.utc,
    )


def _ratio(numerator, denominator):
    """``SUM(numerator) / SUM(denominator)``, NULL when the denominator is 0.

    ``NULLIF`` rather than a CASE so both dialects produce NULL (not an error,
    not infinity) for an empty denominator — the monthly table's MAE/bias
    columns are nullable precisely to express "no comparable rows".
    """
    return func.sum(numerator) / func.nullif(func.sum(denominator), 0)


def _build_month_select(month_start: datetime):
    """SELECT that turns a month of daily rows into monthly rows.

    Grouped by (source, model, days_out, icao) — the daily key minus ``date``,
    which is exactly what makes the sums compose.
    """
    v = VerificationDailyStatsRow
    month_param = bindparam(
        "rollup_month", value=month_start, type_=DateTime(timezone=True),
    )
    start_d = month_start.date()
    end_d = _month_end(month_start).date()

    return (
        select(
            month_param.label("month"),
            v.source.label("source"),
            v.model.label("model"),
            v.days_out.label("days_out"),
            v.icao.label("icao"),
            func.sum(v.n).label("n_scores"),
            func.sum(v.n_cat_match).label("n_cat_match"),
            func.sum(v.n_cat_opt_1).label("n_cat_optimistic_1"),
            func.sum(v.n_cat_opt_2).label("n_cat_optimistic_2"),
            func.sum(v.n_cat_pess_1).label("n_cat_pessimistic_1"),
            func.sum(v.n_cat_pess_2).label("n_cat_pessimistic_2"),
            func.sum(v.n_advisory_match).label("n_advisory_match"),
            func.sum(v.n_advisory_opt).label("n_advisory_optimistic"),
            func.sum(v.n_advisory_pess).label("n_advisory_pessimistic"),
            _ratio(v.sum_abs_ceiling_delta_ft, v.n_ceiling).label("ceiling_mae_ft"),
            _ratio(v.sum_ceiling_delta_ft, v.n_ceiling).label("ceiling_bias_ft"),
            _ratio(v.sum_abs_vis_delta_m, v.n_vis).label("visibility_mae_m"),
            _ratio(v.sum_abs_wind_delta_kt, v.n_wind).label("wind_speed_mae_kt"),
            _ratio(v.sum_abs_temp_delta_c, v.n_temp).label("temperature_mae_c"),
            # Gust: two conditionings, never blended — see tasks/verification_gust.
            _ratio(v.sum_abs_gust_delta_kt, v.n_gust).label("wind_gust_mae_kt"),
            _ratio(v.sum_gust_delta_kt, v.n_gust).label("wind_gust_bias_kt"),
            func.sum(v.n_gust).label("n_gust"),
            _ratio(
                v.sum_gust_flagged_over_peak_kt, v.n_gust_flagged_peak,
            ).label("gust_over_peak_bias_kt"),
            func.sum(v.n_gust_flagged_peak).label("n_gust_flagged_peak"),
            func.sum(v.n_model_gust_flag).label("n_model_gust_flag"),
            func.sum(v.n_obs_gust).label("n_obs_gust"),
            func.sum(v.n_gust_flag_hit).label("n_gust_flag_hit"),
            func.sum(v.n_precip_hit).label("n_precip_hit"),
            func.sum(v.n_precip_miss).label("n_precip_miss"),
            func.sum(v.n_precip_false_alarm).label("n_precip_false_alarm"),
            func.sum(v.n_convection_hit).label("n_convection_hit"),
            func.sum(v.n_convection_miss).label("n_convection_miss"),
            func.sum(v.n_convection_false_alarm).label("n_convection_false_alarm"),
        )
        .where(v.date >= start_d, v.date < end_d)
        .group_by(v.source, v.model, v.days_out, v.icao)
    )


def completed_months(db: Session) -> list[datetime]:
    """First-of-month datetimes for complete months not yet rolled up.

    Derived from ``verification_daily_stats``, the table this rollup reads —
    so a month only becomes a candidate once its days have actually been
    rolled, and Phase 3 pruning moving the raw floor forward doesn't change
    which months are eligible.
    """
    now = datetime.now(timezone.utc)

    daily_months = {
        (d.year, d.month)
        for d in db.execute(
            select(VerificationDailyStatsRow.date).distinct()
        ).scalars().all()
        if d is not None
    }
    if not daily_months:
        return []

    existing_ym = {
        (dt.year, dt.month)
        for dt in db.execute(
            select(VerificationMonthlyStatsRow.month).distinct()
        ).scalars().all()
        if dt is not None
    }

    out: list[datetime] = []
    for (y, m) in sorted(daily_months):
        month_start = datetime(y, m, 1, tzinfo=timezone.utc)
        if _month_end(month_start) > now:
            continue  # month still in progress
        if (y, m) in existing_ym:
            continue
        out.append(month_start)
    return out


def rollup_month(db: Session, month_start: datetime) -> int:
    """Aggregate one month of ``verification_daily_stats`` into monthly stats.

    Idempotent DELETE + INSERT-SELECT. Returns the number of rows produced.
    Caller commits.
    """
    db.execute(
        VerificationMonthlyStatsRow.__table__.delete().where(
            VerificationMonthlyStatsRow.month == month_start
        )
    )
    # The delete above may have removed rows the session still holds; expire
    # so a subsequent flush can't try to re-persist them.
    db.expire_all()

    src = _build_month_select(month_start)
    target_cols = [c.name for c in src.selected_columns]
    result = db.execute(
        insert(VerificationMonthlyStatsRow).from_select(target_cols, src)
    )
    db.flush()

    rows_created = result.rowcount if result.rowcount is not None else 0
    logger.info(
        "verification_monthly_stats: rolled up %d rows for %s",
        rows_created, month_start.strftime("%Y-%m"),
    )
    return rows_created


def rebuild_all_months(db: Session) -> int:
    """Re-roll every month already present in the monthly table. Caller commits.

    Used after a change to the aggregation logic or the daily column set —
    existing monthly rows keep their old values until re-rolled.
    """
    months = sorted(
        {
            (dt.year, dt.month)
            for dt in db.execute(
                select(VerificationMonthlyStatsRow.month).distinct()
            ).scalars().all()
            if dt is not None
        }
    )
    total = 0
    for (y, m) in months:
        total += rollup_month(db, datetime(y, m, 1, tzinfo=timezone.utc))
    return total


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


# ---------------------------------------------------------------------------
# Phase 4 follow-up: daily-stats retention
# ---------------------------------------------------------------------------


def prune_daily_stats(db: Session, retain_months: int | None = None) -> int:
    """Delete ``verification_daily_stats`` rows older than *retain_months*.

    Off by default (``VERIFICATION_DAILY_STATS_RETENTION_MONTHS=0``) and
    intended to be enabled only once monthly rollups have been validated
    against the daily data they summarise — the check is "re-derive a month
    from the daily rows and confirm it matches the stored monthly row", which
    is impossible after the daily rows are gone.

    Daily stats are deliberately **not** archived: unlike raw scores they are
    fully re-derivable from the Parquet archive, so a copy would be a second
    representation of the same facts.

    Caller commits. Returns rows deleted.
    """
    from weatherbrief.tasks.verification_tiering import daily_stats_retention_months

    if retain_months is None:
        retain_months = daily_stats_retention_months()
    if retain_months <= 0:
        return 0

    now = datetime.now(timezone.utc)
    total_months = now.year * 12 + (now.month - 1) - retain_months
    cutoff = datetime(
        total_months // 12, total_months % 12 + 1, 1, tzinfo=timezone.utc,
    ).date()

    result = db.execute(
        VerificationDailyStatsRow.__table__.delete().where(
            VerificationDailyStatsRow.date < cutoff
        )
    )
    deleted = result.rowcount or 0
    if deleted:
        logger.info(
            "verification_daily_stats: pruned %d rows older than %s "
            "(retain_months=%d)",
            deleted, cutoff.isoformat(), retain_months,
        )
    return deleted
