"""Daily rollup of NWP verification scores into verification_daily_stats.

Replaces query-time aggregation of raw ``verification_scores`` for the
model accuracy dashboard + optimistic-bias leaderboard. One INSERT-SELECT
per UTC day, grouped by (source, model, days_out, icao). Idempotent
DELETE+INSERT — re-running for the same day is safe.

TAF scores are out of scope (different key shape — see issue #154).

Direction encoding mirrors ``tasks/verification_rollup.py`` exactly:

- Category index VFR=0, MVFR=1, IFR=2, LIFR=3. ``diff = fcst_i - obs_i``:
  diff = 0 → match
  diff = -1 → optimistic_1   (forecast better than reality; dangerous)
  diff ≤ -2 → optimistic_2
  diff = 1 → pessimistic_1
  diff ≥ 2 → pessimistic_2

- Advisory index green=0, amber=1, red=2. ``diff = fcst_i - obs_i``:
  diff = 0 → match, diff < 0 → optimistic, diff > 0 → pessimistic.

NULL handling: ``NULL - X = NULL``, and ``NULL = anything`` is FALSE in
the CASE WHEN comparisons below, so rows with missing categories/advisories
don't contribute to any direction bucket.
"""

from __future__ import annotations

import logging
from datetime import date as date_t, datetime, timedelta, timezone

from sqlalchemy import (
    Date,
    bindparam,
    case,
    func,
    insert,
    select,
)
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    VerificationDailyStatsRow,
    VerificationScoreRow,
)

logger = logging.getLogger(__name__)


_CAT_INDEX = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
_ADV_INDEX = {"green": 0, "amber": 1, "red": 2}


def _cat_index_expr(col):
    """SQL CASE expression mapping a flight_category column to a 0-3 index."""
    return case(
        (col == "VFR", 0),
        (col == "MVFR", 1),
        (col == "IFR", 2),
        (col == "LIFR", 3),
        else_=None,
    )


def _adv_index_expr(col):
    """SQL CASE expression mapping a wind_advisory column to a 0-2 index."""
    return case(
        (col == "green", 0),
        (col == "amber", 1),
        (col == "red", 2),
        else_=None,
    )


def _sum_when(condition):
    """SUM of 1 when ``condition`` is TRUE (NULL/FALSE both excluded)."""
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _build_rollup_select(day_start: datetime, day_end: datetime, day: date_t):
    """Build the SELECT ... GROUP BY query producing one row per group."""
    cat_obs = _cat_index_expr(VerificationScoreRow.obs_flight_category)
    cat_mod = _cat_index_expr(VerificationScoreRow.model_flight_category)
    cat_diff = cat_mod - cat_obs

    adv_obs = _adv_index_expr(VerificationScoreRow.obs_wind_advisory)
    adv_mod = _adv_index_expr(VerificationScoreRow.model_wind_advisory)
    adv_diff = adv_mod - adv_obs

    abs_ceiling = func.abs(VerificationScoreRow.ceiling_delta_ft)
    abs_wind = func.abs(VerificationScoreRow.wind_speed_delta_kt)
    abs_temp = func.abs(VerificationScoreRow.temperature_delta_c)
    abs_vis = func.abs(VerificationScoreRow.visibility_delta_m)

    obs_p = VerificationScoreRow.obs_has_precipitation
    mod_p = VerificationScoreRow.model_has_precipitation
    obs_c = VerificationScoreRow.obs_has_convection
    mod_c = VerificationScoreRow.model_has_convection

    # Bind ``day`` as a Date parameter so the dialect's bind processor
    # handles ISO-string conversion. CAST(... AS DATE) in SQLite would
    # coerce to NUMERIC and round-trip as an int — avoid it entirely.
    date_param = bindparam("rollup_date", value=day, type_=Date())

    return (
        select(
            date_param.label("date"),
            VerificationScoreRow.source.label("source"),
            VerificationScoreRow.model.label("model"),
            VerificationScoreRow.days_out.label("days_out"),
            VerificationScoreRow.icao.label("icao"),
            func.count().label("n"),
            # Per-field non-NULL counts
            func.count(VerificationScoreRow.ceiling_delta_ft).label("n_ceiling"),
            func.count(VerificationScoreRow.wind_speed_delta_kt).label("n_wind"),
            func.count(VerificationScoreRow.temperature_delta_c).label("n_temp"),
            func.count(VerificationScoreRow.visibility_delta_m).label("n_vis"),
            # Category direction (cat_diff is NULL if either side is NULL)
            _sum_when(cat_diff == 0).label("n_cat_match"),
            _sum_when(cat_diff == -1).label("n_cat_opt_1"),
            _sum_when(cat_diff <= -2).label("n_cat_opt_2"),
            _sum_when(cat_diff == 1).label("n_cat_pess_1"),
            _sum_when(cat_diff >= 2).label("n_cat_pess_2"),
            # Delta sums (SUM ignores NULL — matches Python's "skip None")
            func.sum(abs_ceiling).label("sum_abs_ceiling_delta_ft"),
            func.sum(VerificationScoreRow.ceiling_delta_ft).label("sum_ceiling_delta_ft"),
            func.sum(abs_wind).label("sum_abs_wind_delta_kt"),
            func.sum(abs_temp).label("sum_abs_temp_delta_c"),
            func.sum(abs_vis).label("sum_abs_vis_delta_m"),
            # Advisory direction
            _sum_when(adv_diff == 0).label("n_advisory_match"),
            _sum_when(adv_diff < 0).label("n_advisory_opt"),
            _sum_when(adv_diff > 0).label("n_advisory_pess"),
            # Precipitation contingency. (NULL AND X) → NULL → FALSE here.
            _sum_when((obs_p.is_(True)) & (mod_p.is_(True))).label("n_precip_hit"),
            _sum_when((obs_p.is_(True)) & (mod_p.is_(False))).label("n_precip_miss"),
            _sum_when((obs_p.is_(False)) & (mod_p.is_(True))).label(
                "n_precip_false_alarm"
            ),
            # Convection contingency
            _sum_when((obs_c.is_(True)) & (mod_c.is_(True))).label("n_convection_hit"),
            _sum_when((obs_c.is_(True)) & (mod_c.is_(False))).label(
                "n_convection_miss"
            ),
            _sum_when((obs_c.is_(False)) & (mod_c.is_(True))).label(
                "n_convection_false_alarm"
            ),
        )
        .where(
            VerificationScoreRow.observation_time >= day_start,
            VerificationScoreRow.observation_time < day_end,
        )
        .group_by(
            VerificationScoreRow.source,
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.icao,
        )
    )


def rollup_day(db: Session, day: date_t) -> int:
    """Aggregate raw NWP scores for one UTC date into verification_daily_stats.

    Idempotent — rows for this date are deleted and re-inserted in one
    INSERT-SELECT. Returns the number of rows produced.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    # Wipe existing rows for this date.
    db.execute(
        VerificationDailyStatsRow.__table__.delete().where(
            VerificationDailyStatsRow.date == day
        )
    )

    src = _build_rollup_select(day_start, day_end, day)
    target_cols = [c.name for c in src.selected_columns]
    stmt = insert(VerificationDailyStatsRow).from_select(target_cols, src)
    result = db.execute(stmt)
    db.flush()

    inserted = result.rowcount if result.rowcount is not None else 0
    logger.info(
        "verification_daily_stats: rolled up %d groups for %s",
        inserted, day.isoformat(),
    )
    return inserted


def completed_days(db: Session) -> list[date_t]:
    """UTC dates with standalone scores that aren't yet in verification_daily_stats.

    Excludes today (incomplete) and dates already summarised. Mirrors the
    pattern in ``tasks/airport_summary.completed_days``.

    Both the start-point (``MIN(observation_time)``) and the "already done"
    set (``date`` in rollup) are filtered to standalone sources. Flight scores
    are tiny by comparison and inherit a different lifecycle — including them
    here would (a) start day-iteration from the earliest flight score even
    when standalone data starts later, and (b) silently mark a flight-only
    date as "done" so future standalone scores for that date never get rolled.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    earliest = db.execute(
        select(func.min(VerificationScoreRow.observation_time))
        .where(VerificationScoreRow.source.like("standalone%"))
    ).scalar()
    if earliest is None:
        return []
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)

    existing = set(
        db.execute(
            select(VerificationDailyStatsRow.date)
            .where(VerificationDailyStatsRow.source.like("standalone%"))
            .distinct()
        ).scalars().all()
    )

    out: list[date_t] = []
    d = earliest.date()
    while d < today:
        if d not in existing:
            out.append(d)
        d += timedelta(days=1)
    return out


def rollup_all_complete_days(db: Session) -> int:
    """Roll up every completed UTC day not yet summarised.

    Caller commits. On first deploy this performs the full backfill of
    historic standalone data (~43 days at issue-write time → ~500K rows).
    """
    pending = completed_days(db)
    if len(pending) > 7:
        logger.info(
            "verification_daily_stats: backfilling %d days (%s..%s)",
            len(pending), pending[0].isoformat(), pending[-1].isoformat(),
        )
    total = 0
    for d in pending:
        total += rollup_day(db, d)
    return total


def rollup_today_and_pending(db: Session) -> int:
    """Roll up today (partial), yesterday (re-roll), plus any pending days.

    The cache rebuild after each standalone cycle calls this so the 24h
    dashboard reflects scores collected today, not just up-to-yesterday.

    Today's rollup is idempotent DELETE+INSERT — refreshed each call until
    the day completes and the pending-days loop takes over.

    Yesterday is re-rolled too as a one-day trailing buffer: METARs and
    scores that arrived after the previous cycle's rollup (e.g. across a
    UTC-midnight boundary, or after a temporary network failure) get
    picked up rather than being silently missed because yesterday is
    already in the "existing" set.

    Caller commits.
    """
    n = rollup_all_complete_days(db)
    today = datetime.now(timezone.utc).date()
    n += rollup_day(db, today - timedelta(days=1))
    n += rollup_day(db, today)
    return n


def rebuild_all_days(db: Session) -> int:
    """Re-roll every UTC date that already has rows in verification_daily_stats.

    Used when the schema or aggregation logic changes — idempotent
    DELETE+INSERT per day. Caller commits.
    """
    existing = sorted(
        db.execute(select(VerificationDailyStatsRow.date).distinct()).scalars().all()
    )
    if not existing:
        return 0
    logger.info(
        "verification_daily_stats: rebuilding %d existing days (%s..%s)",
        len(existing), existing[0].isoformat(), existing[-1].isoformat(),
    )
    total = 0
    for d in existing:
        total += rollup_day(db, d)
    return total
