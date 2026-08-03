"""Daily rollup of verification scores into the four daily aggregate tables.

``rollup_day`` writes one UTC day into all four, in dependency order, each
idempotent DELETE+INSERT so re-running a day is always safe:

1. ``verification_daily_stats`` — per (source, model, days_out, icao) from raw
   ``verification_scores``. The original #154 table; still the only one that
   can answer a country/airport-filtered request, and it feeds the
   optimistic-bias leaderboard.
2. ``verification_global_daily_stats`` — the same aggregate with ``icao``
   dropped (#522), written as a GROUP BY over (1) rather than a second pass
   over raw, so the two can never disagree about a day. ~90 rows/day.
3. ``verification_activity_daily`` — per (source): ``COUNT(*)``,
   ``COUNT(DISTINCT observation_id)`` and ``COUNT(DISTINCT icao)`` for the
   day. Distinct-observation counts are the one activity number no per-group
   rollup can reproduce (one observation → N scores), which is why
   ``get_activity_summary`` was still scanning raw scores before #522.
4. ``taf_verification_daily`` — per (source, days_out) from
   ``taf_verification_scores``, joined to ``verification_observations`` for
   the gust conditionings. ``days_out = lead_hours // 24``, expressed
   portably — see :func:`taf_days_out_expr`.

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

The SELECT joins ``verification_observations`` (inner, on the score's
``observation_id`` FK) purely for the gust aggregates (#491): the observed
gust and the realised peak live there, and both are needed to keep the
forecast-flagged and obs-flagged conditionings separate. The join can't drop
rows — the FK is NOT NULL and retention prunes observations and scores in the
same window.
"""

from __future__ import annotations

import logging
from datetime import date as date_t, datetime, timedelta, timezone

from sqlalchemy import (
    Date,
    Integer,
    bindparam,
    case,
    cast,
    func,
    insert,
    select,
)
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    TafVerificationDailyRow,
    TafVerificationScoreRow,
    VerificationActivityDailyRow,
    VerificationDailyStatsRow,
    VerificationGlobalDailyStatsRow,
    VerificationMonthlyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_gust import (
    gust_aggregate_columns,
    taf_gust_aggregate_columns,
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


def known_sources(db: Session) -> list[str]:
    """Every ``source`` value present in ``verification_scores``.

    Cheap despite the table size: ``ix_verif_scores_source_time`` leads with
    ``source``, so MySQL answers this with a loose index scan ("Using index
    for group-by") — 0-1 ms against 8.4M rows on production. Read fresh per
    rollup rather than hardcoded so a new source can never be silently
    dropped from the aggregate.
    """
    return sorted(
        db.execute(select(VerificationScoreRow.source).distinct()).scalars().all()
    )


def _build_rollup_select(
    day_start: datetime, day_end: datetime, day: date_t, sources: list[str],
):
    """Build the SELECT ... GROUP BY query producing one row per group.

    ``sources`` is redundant with the data — it lists every source already
    present — but it is what makes the query indexable. Filtering on
    ``observation_time`` alone matches no index prefix (nothing on
    ``verification_scores`` leads with it), so the day's ~36K rows were found
    by a full scan of all 8.4M: ``type=ALL, rows=8,223,266``, 4.5 s warm and
    35 s cold, run 2-3x per light cycle. Naming ``source`` in the WHERE turns
    the existing ``ix_verif_scores_source_time`` (source, observation_time)
    into a range seek — ``type=range, rows=60,396``, 0.18 s warm — for the
    same result set.
    """
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
            # Gust (#491) — needs the observation row for the observed gust
            # and the realised peak, hence the join below.
            *gust_aggregate_columns(),
        )
        .join(
            VerificationObservationRow,
            VerificationScoreRow.observation_id == VerificationObservationRow.id,
        )
        .where(
            VerificationScoreRow.source.in_(sources),
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


def taf_days_out_expr(col):
    """Portable ``lead_hours -> days_out`` bucket for TAF scores.

    Must produce identical values on SQLite and MySQL, because the same table
    is rolled up in tests (SQLite) and production (MySQL) and the two must be
    comparable. Neither ``//`` nor a bare ``CAST(x/24 AS INTEGER)`` qualifies:
    SQLite's ``/`` on integers truncates, MySQL's returns a decimal, and
    MySQL's ``CAST(2.5 AS SIGNED)`` rounds *away from zero* — so ``lead_hours``
    of 60 would bucket as 2 on SQLite and 3 on MySQL.

    ``(x - x % 24) / 24`` sidesteps that: the numerator is always an exact
    multiple of 24, so both dialects divide exactly and the CAST has nothing
    to round. Negative ``lead_hours`` (an observation before its TAF's issue
    time — shouldn't happen, but the column doesn't forbid it) is clamped to
    bucket 0, matching ``verification_rollup._lead_hours_to_days_out``'s
    ``max(0, ...)`` rather than diverging from it in the tail.
    """
    truncated = (col - (col % 24)) / 24
    return cast(case((col < 0, 0), else_=truncated), Integer)


def _build_global_rollup_select(day: date_t):
    """SELECT that collapses one day of per-airport rows to the global table.

    Reads ``verification_daily_stats``, not raw scores. Two reasons: it is
    ~12K rows instead of ~36K+ (and index-covered by ``ix_vds_date_model``),
    and deriving one from the other makes it structurally impossible for the
    global numbers to disagree with the per-airport numbers for the same day.
    """
    v = VerificationDailyStatsRow
    date_param = bindparam("rollup_date", value=day, type_=Date())

    sum_cols = [
        "n", "n_ceiling", "n_wind", "n_temp", "n_vis",
        "n_cat_match", "n_cat_opt_1", "n_cat_opt_2",
        "n_cat_pess_1", "n_cat_pess_2",
        "sum_abs_ceiling_delta_ft", "sum_ceiling_delta_ft",
        "sum_abs_wind_delta_kt", "sum_abs_temp_delta_c", "sum_abs_vis_delta_m",
        "n_gust", "sum_abs_gust_delta_kt", "sum_gust_delta_kt",
        "n_gust_flagged_peak", "sum_gust_flagged_over_peak_kt",
        "n_model_gust_flag", "n_obs_gust", "n_gust_flag_hit",
        "n_advisory_match", "n_advisory_opt", "n_advisory_pess",
        "n_precip_hit", "n_precip_miss", "n_precip_false_alarm",
        "n_convection_hit", "n_convection_miss", "n_convection_false_alarm",
    ]

    return (
        select(
            date_param.label("date"),
            v.source.label("source"),
            v.model.label("model"),
            v.days_out.label("days_out"),
            # NULL-summing columns stay NULL when every contributing row is
            # NULL, exactly as they do in the per-airport table — the ratio
            # consumers all guard on the matching n_* count anyway.
            *[func.sum(getattr(v, c)).label(c) for c in sum_cols],
        )
        .where(v.date == date_param)
        .group_by(v.source, v.model, v.days_out)
    )


def _build_activity_rollup_select(
    day_start: datetime, day_end: datetime, day: date_t, sources: list[str],
):
    """SELECT producing one activity row per source for the day.

    ``COUNT(DISTINCT observation_id)`` over a single day is cheap on
    ``ix_verif_scores_source_time`` — this is the same query
    ``get_activity_summary`` used to run per dashboard request over a 7d/30d
    window, which is what made it pathological (#448). Run once per day at
    rollup time, the cost is paid once and the window sums are additive.

    ``sources`` is named in the WHERE for the same indexability reason as
    :func:`_build_rollup_select`.
    """
    date_param = bindparam("rollup_date", value=day, type_=Date())
    return (
        select(
            date_param.label("date"),
            VerificationScoreRow.source.label("source"),
            func.count().label("n_scores"),
            func.count(
                func.distinct(VerificationScoreRow.observation_id)
            ).label("n_distinct_obs"),
            func.count(func.distinct(VerificationScoreRow.icao)).label(
                "n_airports_day"
            ),
        )
        .where(
            VerificationScoreRow.source.in_(sources),
            VerificationScoreRow.observation_time >= day_start,
            VerificationScoreRow.observation_time < day_end,
        )
        .group_by(VerificationScoreRow.source)
    )


def known_taf_sources(db: Session) -> list[str]:
    """Every ``source`` value present in ``taf_verification_scores``.

    The TAF mirror of :func:`known_sources`, and needed for the same reason:
    ``ix_taf_verif_source_time`` leads with ``source``, so a day-range filter
    that doesn't name it can't seek and full-scans the table instead. Cheap
    (loose index scan on the leading column).
    """
    return sorted(
        db.execute(select(TafVerificationScoreRow.source).distinct()).scalars().all()
    )


def _build_taf_rollup_select(
    day_start: datetime, day_end: datetime, day: date_t, sources: list[str],
):
    """SELECT producing one TAF row per (source, days_out) for the day.

    Joins ``verification_observations`` for the gust conditionings, exactly as
    the NWP rollup does — the TAF gust and the observed gust both live on the
    observation row. Inner join, and it can't drop rows: the FK is NOT NULL
    and retention prunes observations and TAF scores in the same window.

    ``sources`` is named in the WHERE for the same indexability reason as
    :func:`_build_rollup_select` — see :func:`known_taf_sources`.
    """
    t = TafVerificationScoreRow
    cat_obs = _cat_index_expr(t.obs_flight_category)
    cat_taf = _cat_index_expr(t.taf_flight_category)
    cat_diff = cat_taf - cat_obs

    adv_obs = _adv_index_expr(t.obs_wind_advisory)
    adv_taf = _adv_index_expr(t.taf_wind_advisory)
    adv_diff = adv_taf - adv_obs

    date_param = bindparam("rollup_date", value=day, type_=Date())
    days_out = taf_days_out_expr(t.lead_hours)

    return (
        select(
            date_param.label("date"),
            t.source.label("source"),
            days_out.label("days_out"),
            func.count().label("n"),
            func.count(t.ceiling_delta_ft).label("n_ceiling"),
            func.count(t.wind_speed_delta_kt).label("n_wind"),
            func.count(t.visibility_delta_m).label("n_vis"),
            _sum_when(cat_diff == 0).label("n_cat_match"),
            _sum_when(cat_diff == -1).label("n_cat_opt_1"),
            _sum_when(cat_diff <= -2).label("n_cat_opt_2"),
            _sum_when(cat_diff == 1).label("n_cat_pess_1"),
            _sum_when(cat_diff >= 2).label("n_cat_pess_2"),
            func.sum(func.abs(t.ceiling_delta_ft)).label(
                "sum_abs_ceiling_delta_ft"
            ),
            func.sum(t.ceiling_delta_ft).label("sum_ceiling_delta_ft"),
            func.sum(func.abs(t.wind_speed_delta_kt)).label(
                "sum_abs_wind_delta_kt"
            ),
            func.sum(func.abs(t.visibility_delta_m)).label("sum_abs_vis_delta_m"),
            _sum_when(adv_diff == 0).label("n_advisory_match"),
            _sum_when(adv_diff < 0).label("n_advisory_opt"),
            _sum_when(adv_diff > 0).label("n_advisory_pess"),
            *taf_gust_aggregate_columns(),
        )
        .join(
            VerificationObservationRow,
            t.observation_id == VerificationObservationRow.id,
        )
        .where(
            t.source.in_(sources),
            t.observation_time >= day_start,
            t.observation_time < day_end,
        )
        .group_by(t.source, days_out)
    )


def _delete_and_insert(db: Session, model, src, *, date_col_value: date_t) -> int:
    """DELETE this date's rows then INSERT the SELECT's. Returns rows written."""
    db.execute(model.__table__.delete().where(model.date == date_col_value))
    target_cols = [c.name for c in src.selected_columns]
    result = db.execute(insert(model).from_select(target_cols, src))
    db.flush()
    return result.rowcount if result.rowcount is not None else 0


def _month_has_other_daily_days(db: Session, day: date_t) -> bool:
    """Whether ``verification_daily_stats`` holds any day of *day*'s month but *day*.

    The "but *day*" matters: :func:`rollup_day` may already have inserted this
    date, so a bare existence check would always be true and would gate
    nothing.
    """
    if day.month == 12:
        month_end = date_t(day.year + 1, 1, 1)
    else:
        month_end = date_t(day.year, day.month + 1, 1)
    month_start = date_t(day.year, day.month, 1)
    return db.execute(
        select(VerificationDailyStatsRow.date)
        .where(
            VerificationDailyStatsRow.date >= month_start,
            VerificationDailyStatsRow.date < month_end,
            VerificationDailyStatsRow.date != day,
        )
        .limit(1)
    ).first() is not None


def _invalidate_month(db: Session, day: date_t) -> None:
    """Drop the monthly rollup for the month containing *day*.

    A month is the sum of its days, so re-rolling a day makes the month it
    belongs to stale. Without this, ``completed_months``' "already rolled" set
    is a one-way ratchet: a day backfilled after its month was rolled would
    never be reflected, and the undercount would be permanent short of a
    manual ``--rebuild``.

    Cheap and almost always a no-op — the scheduler only re-rolls today and
    yesterday, and the current month is never rolled up in the first place.
    The retention loop runs the monthly rollup right after the daily one, so
    an invalidated completed month is re-derived in the same pass.

    Refuses when the month has a monthly row but *no other day* left in
    ``verification_daily_stats``. That is what an aged-out month looks like
    once the Phase 4 follow-up gate starts pruning daily stats: deleting the
    monthly row would destroy a permanent aggregate that the surviving daily
    rows — this one day — cannot rebuild, and ``completed_months`` would then
    re-roll the month from that single day and freeze the undercount in.
    ``rollup_month`` carries the same guard, but it is a no-op there once this
    function has already removed the row it keys on, so the check has to live
    at both ends. Reachable from ``verify rollup-daily-stats --day <date>``,
    documented as safe to re-run at any time.
    """
    month_start = datetime(day.year, day.month, 1, tzinfo=timezone.utc)

    already_rolled = db.execute(
        select(VerificationMonthlyStatsRow.id)
        .where(VerificationMonthlyStatsRow.month == month_start)
        .limit(1)
    ).first()
    if already_rolled is not None and not _month_has_other_daily_days(db, day):
        logger.warning(
            "verification_monthly_stats: %s has monthly rows but no daily rows "
            "outside %s — not invalidating. Re-import the month's daily source "
            "(or the Parquet archive) before re-rolling it.",
            month_start.strftime("%Y-%m"), day.isoformat(),
        )
        return

    db.execute(
        VerificationMonthlyStatsRow.__table__.delete().where(
            VerificationMonthlyStatsRow.month == month_start
        )
    )


def rollup_day(db: Session, day: date_t) -> int:
    """Aggregate one UTC date into all four daily tables.

    Idempotent — each table's rows for this date are deleted and re-inserted.
    Returns the number of ``verification_daily_stats`` rows produced (the
    per-airport count, unchanged from before #522); the other three tables'
    counts are logged, since they are derived and their sizes are fixed
    multiples of the source data rather than an independent signal.

    Order matters: the global table is a GROUP BY over the per-airport table,
    so the per-airport insert has to be flushed first.

    Also invalidates the monthly rollup for this day's month — see
    :func:`_invalidate_month`.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    # Wipe existing rows for this date.
    db.execute(
        VerificationDailyStatsRow.__table__.delete().where(
            VerificationDailyStatsRow.date == day
        )
    )
    _invalidate_month(db, day)

    taf_sources = known_taf_sources(db)
    sources = known_sources(db)

    def _roll_taf() -> int:
        if not taf_sources:
            db.execute(
                TafVerificationDailyRow.__table__.delete().where(
                    TafVerificationDailyRow.date == day
                )
            )
            return 0
        return _delete_and_insert(
            db, TafVerificationDailyRow,
            _build_taf_rollup_select(day_start, day_end, day, taf_sources),
            date_col_value=day,
        )

    if not sources:
        # No NWP scores at all — the deletes are the whole job. Returning early
        # also keeps a degenerate ``IN ()`` out of the INSERT-SELECTs. TAF is
        # still rolled: TAF scores can exist without NWP scores for a day.
        _delete_and_insert(
            db, VerificationGlobalDailyStatsRow,
            _build_global_rollup_select(day), date_col_value=day,
        )
        db.execute(
            VerificationActivityDailyRow.__table__.delete().where(
                VerificationActivityDailyRow.date == day
            )
        )
        _roll_taf()
        return 0

    src = _build_rollup_select(day_start, day_end, day, sources)
    target_cols = [c.name for c in src.selected_columns]
    stmt = insert(VerificationDailyStatsRow).from_select(target_cols, src)
    result = db.execute(stmt)
    db.flush()

    inserted = result.rowcount if result.rowcount is not None else 0

    n_global = _delete_and_insert(
        db, VerificationGlobalDailyStatsRow,
        _build_global_rollup_select(day), date_col_value=day,
    )
    n_activity = _delete_and_insert(
        db, VerificationActivityDailyRow,
        _build_activity_rollup_select(day_start, day_end, day, sources),
        date_col_value=day,
    )
    n_taf = _roll_taf()

    logger.info(
        "daily rollup %s: %d per-airport, %d global, %d activity, %d TAF groups",
        day.isoformat(), inserted, n_global, n_activity, n_taf,
    )
    return inserted


# Production only ever writes 'flight' or 'standalone' to
# verification_scores.source — the standalone_full/light/forecast distinction
# lives on verification_cycles.source, never on a score. The old
# ``LIKE 'standalone%'`` matched the same rows but cost a range scan where
# equality gets a ref lookup, and it quietly invited the belief that
# 'standalone_light' scores exist. Tests that predate this wrote
# 'standalone_full' rows directly; they are covered by the prefix set below.
_STANDALONE_SOURCE = "standalone"


_STANDALONE_SOURCES = (
    _STANDALONE_SOURCE, "standalone_full", "standalone_light",
    "standalone_forecast",
)


def _standalone_clause(col):
    """Match standalone scores by equality, with a narrow legacy allowance.

    Equality on ``'standalone'`` is what production needs. Historical fixtures
    (and any pre-#154 row that ever landed with a cycle-type suffix) are
    matched by naming them explicitly rather than by reintroducing a LIKE, so
    the accepted set stays visible here.

    Note this is a filter, not an access path: an ``IN`` list over ~95% of the
    table is no more selective than the ``LIKE`` it replaced, and MySQL scans
    the index either way. Where that matters — an aggregate whose whole cost is
    the scan — use :func:`_earliest_standalone_time` instead of this clause.
    """
    return col.in_(_STANDALONE_SOURCES)


def _earliest_standalone_time(db: Session) -> datetime | None:
    """``MIN(observation_time)`` over standalone scores, one seek per source.

    MySQL's MIN/MAX index optimisation ("Select tables optimized away") needs a
    *single* equality on the index prefix. Neither ``LIKE 'standalone%'`` nor
    ``IN (...)`` qualifies — both degrade to a range scan of every matching
    entry in ``ix_verif_scores_source_time``, which is ~4.3M of the 8.8M rows
    and measured at 10-26 s on production. One equality per known source and a
    Python ``min`` over the handful of results is the same answer in ~1 ms each.
    """
    seen = [
        db.execute(
            select(func.min(VerificationScoreRow.observation_time))
            .where(VerificationScoreRow.source == src)
        ).scalar()
        for src in _STANDALONE_SOURCES
    ]
    return min((t for t in seen if t is not None), default=None)


def completed_days(db: Session) -> list[date_t]:
    """UTC dates with standalone scores that aren't yet in verification_daily_stats.

    Excludes today (incomplete) and dates already summarised. Mirrors the
    pattern in ``tasks/airport_summary.completed_days``.

    Both the start-point and the "already done" set are filtered to standalone
    sources. Flight scores are tiny by comparison and inherit a different
    lifecycle — including them here would (a) start day-iteration from the
    earliest flight score even when standalone data starts later, and (b)
    silently mark a flight-only date as "done" so future standalone scores for
    that date never get rolled.

    The start-point comes from the **rollup table** once anything has been
    rolled, not from ``MIN(observation_time)`` over raw scores (#522). Two
    reasons: the raw MIN is a scan this loop shouldn't need in steady state,
    and once Phase 3 pruning is on the raw MIN walks forward with the
    retention cutoff, which would make the iteration window depend on how
    recently the pruner ran. Gap detection is unaffected — the loop still
    visits every date between the first rolled day and today and skips the
    ones already present.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    existing = set(
        db.execute(
            select(VerificationDailyStatsRow.date)
            .where(_standalone_clause(VerificationDailyStatsRow.source))
            .distinct()
        ).scalars().all()
    )

    if existing:
        start = min(existing)
    else:
        # Bootstrap only: nothing rolled yet, so the raw table is the only
        # thing that knows where the data starts.
        earliest = _earliest_standalone_time(db)
        if earliest is None:
            return []
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        start = earliest.date()

    out: list[date_t] = []
    d = start
    while d < today:
        if d not in existing:
            out.append(d)
        d += timedelta(days=1)
    return out


def unrolled_days_with_data(db: Session) -> list[date_t]:
    """Pending days that actually have raw scores waiting to be rolled.

    :func:`completed_days` returns every date between the first rolled day and
    today that has no rollup rows — which includes days that legitimately have
    nothing to roll (an ingest outage, a quiet day). Those are not gaps: a
    rollup of them would write zero rows and they would stay "pending"
    forever.

    A real gap is a day with raw standalone scores but no rollup rows. This
    filters to those, so callers can distinguish "the daily rollup is behind"
    from "nothing happened that day". Bounded work: one EXISTS per pending
    day, and in steady state the pending list is empty or a single day.

    Note that after Phase 3 pruning removes a month's raw scores, its days can
    no longer look like gaps — correctly so, since they were rolled long
    before the retention cutoff reached them.
    """
    out: list[date_t] = []
    for d in completed_days(db):
        day_start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        row = db.execute(
            select(VerificationScoreRow.id)
            .where(
                _standalone_clause(VerificationScoreRow.source),
                VerificationScoreRow.observation_time >= day_start,
                VerificationScoreRow.observation_time < day_start + timedelta(days=1),
            )
            .limit(1)
        ).first()
        if row is not None:
            out.append(d)
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
    # On the very first deploy this re-rolls yesterday a second time
    # (rollup_all_complete_days just rolled it). Idempotent so harmless;
    # not worth a guard — every cycle after that, yesterday is already
    # in completed_days's existing set, so the orchestrator skips it
    # and this explicit re-roll is the only one.
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
