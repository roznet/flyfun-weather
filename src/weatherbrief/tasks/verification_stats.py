"""Shared query module for verification statistics.

Used by both the daily email digest and the admin web dashboard API.
All functions take a SQLAlchemy Session and a date range, returning
Pydantic models from :mod:`weatherbrief.models.verification`.

The expensive aggregates (per-model accuracy, bias, MAE, wind advisory)
read from :class:`VerificationDailyStatsRow` — the pre-aggregated rollup
populated after each standalone verification cycle. NWP-only.

TAF is rolled up at query time from ``taf_verification_scores`` because
its shape (lead_hours, no model/days_out) doesn't fit the daily rollup —
see issue #154 for the rationale.

``get_notable_misses`` and ``get_missed_warnings`` still read raw
``verification_scores`` because they need individual row data.

Every query filters by ``source`` ('flight' or 'standalone') to ensure
flight-based and standalone verification data are never mixed.
"""

from __future__ import annotations

import logging
import time
from datetime import date as date_t, datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    FlightRow,
    FlightVerificationMapRow,
    TafVerificationScoreRow,
    VerificationCycleRow,
    VerificationDailyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.models.verification import (
    ActivitySummary,
    CategoryAccuracyRow,
    CategoryBiasStats,
    GustAccuracyStats,
    MissedWarning,
    NotableMiss,
    OptimisticBiasLeaderboardRow,
    VerificationDigestData,
    WindAdvisoryStats,
)

logger = logging.getLogger(__name__)

# FORCE INDEX (#448): the activity COUNT(DISTINCT) queries name this index
# explicitly (see get_activity_summary for why). FORCE INDEX raises a hard SQL
# error on MySQL if the index is missing — it is declared on
# VerificationScoreRow.__table_args__ and created by migration 038; keep the
# three in sync.
_SCORES_SOURCE_TIME_HINT = "FORCE INDEX (ix_verif_scores_source_time)"

_DAYS_OUT_COLS = (0, 1, 2, 3)
# Lead times a *miss* is worth surfacing at. An observation is scored once per
# lead time, so without this scope one storm returns as one row per days_out —
# and a model missing a warning six days out is expected, not notable.
_NEAR_TERM_LEADS = (0, 1)
# Ordered from best to worst flying conditions
_CAT_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
# The strongest wind advisory. The vocabulary is green/amber/red — there is no
# "WARNING" (#418).
_WIND_WARNING = "red"


def _icao_clause(col, icao_filter: list[str] | None):
    """Return a WHERE clause for ICAO filtering, or True (no-op) if no filter."""
    if not icao_filter:
        return True  # SQLAlchemy treats literal True as no-op in .where()
    if len(icao_filter) == 1:
        return col == icao_filter[0]
    return col.in_(icao_filter)


def _date_range(since: datetime, until: datetime) -> tuple[date_t, date_t]:
    """Convert datetime range to inclusive UTC date range for rollup queries.

    The rollup is keyed by UTC ``date``, so a datetime range is widened to
    inclusive whole-day boundaries: e.g. ``since=2026-05-11T14:32Z`` becomes
    ``2026-05-11``, picking up that day's earlier observations.

    This widens the result vs the old raw query (which matched
    ``observation_time BETWEEN since AND until``). The widening is largest
    on the ``24h`` period: a call at 14:00 UTC with ``since=now-24h`` becomes
    ``date IN (yesterday, today)`` — up to ~38h of data ("yesterday all day"
    + "today so far") rather than a strict 24h sliding window. For ``7d`` /
    ``30d`` periods the relative widening is rounding noise (<4%).

    The shift is deliberate: dashboard period buttons land on stable UTC-day
    boundaries instead of drifting with request time, and rollup-keyed lookup
    stays cheap. Cache labels (``stats:standalone:24h``) continue to use the
    period name; the actual data window is documented here.
    """
    s = since.date() if isinstance(since, datetime) else since
    u = until.date() if isinstance(until, datetime) else until
    return s, u


# ---------------------------------------------------------------------------
# Activity summary
# ---------------------------------------------------------------------------


def get_activity_summary(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
) -> ActivitySummary:
    """High-level counts for a date range, scoped by source.

    ``observations_collected`` and ``airports_observed`` still query
    ``verification_scores`` directly — the rollup groups by (date, source,
    model, days_out, icao) so summing wouldn't give distinct counts, and
    these are one bounded query per dashboard request, not per-group.
    """
    common_where = (
        VerificationScoreRow.observation_time.between(since, until),
        VerificationScoreRow.source == source,
        _icao_clause(VerificationScoreRow.icao, icao_filter),
    )
    # Two COUNT(DISTINCT) calls run as two separate queries (PR #150) so
    # MySQL can plan each with its own index instead of falling into a
    # tmp-table dedup.
    #
    # FORCE INDEX (#448): left to itself, MySQL picks
    # ix_verif_scores_source_model_days with ref=const on `source` alone and
    # scans every standalone row ever (3.8M at time of fix) — the time window
    # never enters the plan, so 7d and 30d both took ~19 min while 24h (which
    # got the range plan) took 2 s. Forcing the (source, observation_time)
    # range index returns in ~6 s on the same data. SQLite ignores the hint.
    obs_count = db.execute(
        select(func.count(func.distinct(VerificationScoreRow.observation_id)))
        .select_from(VerificationScoreRow)
        .with_hint(VerificationScoreRow, _SCORES_SOURCE_TIME_HINT, dialect_name="mysql")
        .where(*common_where)
    ).scalar() or 0
    airport_count = db.execute(
        select(func.count(func.distinct(VerificationScoreRow.icao)))
        .select_from(VerificationScoreRow)
        .with_hint(VerificationScoreRow, _SCORES_SOURCE_TIME_HINT, dialect_name="mysql")
        .where(*common_where)
    ).scalar() or 0

    flights_verified = 0
    flights_completed = 0
    if source == "flight":
        flights_verified = db.execute(
            select(func.count(func.distinct(FlightVerificationMapRow.flight_id))).where(
                FlightVerificationMapRow.observation_id.isnot(None),
                FlightVerificationMapRow.observation_id.in_(
                    select(VerificationObservationRow.id).where(
                        VerificationObservationRow.observation_time.between(since, until)
                    )
                ),
            )
        ).scalar() or 0

        flights_completed = db.execute(
            select(func.count(FlightRow.id)).where(
                FlightRow.verification_status.in_(("complete", "scored")),
            )
        ).scalar() or 0

    cycle_rows = db.execute(
        select(
            func.count(VerificationCycleRow.id),
            func.avg(VerificationCycleRow.duration_ms),
        ).where(
            VerificationCycleRow.started_at.between(since, until),
            VerificationCycleRow.source == source,
        )
    ).one()
    cycles_run = cycle_rows[0] or 0
    avg_duration = round(cycle_rows[1], 1) if cycle_rows[1] is not None else None

    return ActivitySummary(
        flights_verified=flights_verified,
        flights_completed=flights_completed,
        airports_observed=airport_count,
        observations_collected=obs_count,
        cycles_run=cycles_run,
        avg_cycle_duration_ms=avg_duration,
    )


# ---------------------------------------------------------------------------
# Category accuracy
# ---------------------------------------------------------------------------


def get_category_accuracy(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
) -> list[CategoryAccuracyRow]:
    """Flight-category match rate per model and days-out.

    NWP models read from the daily rollup. TAF is a pseudo-model (days_out=0)
    still aggregated from ``taf_verification_scores``.
    """
    rows: list[CategoryAccuracyRow] = []

    since_d, until_d = _date_range(since, until)
    # Total rows that contributed a category — n_cat_match + opt + pess
    cat_total = (
        VerificationDailyStatsRow.n_cat_match
        + VerificationDailyStatsRow.n_cat_opt_1
        + VerificationDailyStatsRow.n_cat_opt_2
        + VerificationDailyStatsRow.n_cat_pess_1
        + VerificationDailyStatsRow.n_cat_pess_2
    )
    model_rows = db.execute(
        select(
            VerificationDailyStatsRow.model,
            VerificationDailyStatsRow.days_out,
            func.sum(VerificationDailyStatsRow.n_cat_match).label("n_match"),
            func.sum(cat_total).label("n_with_cat"),
        )
        .where(
            VerificationDailyStatsRow.date.between(since_d, until_d),
            VerificationDailyStatsRow.days_out.in_(_DAYS_OUT_COLS),
            VerificationDailyStatsRow.source == source,
            _icao_clause(VerificationDailyStatsRow.icao, icao_filter),
        )
        .group_by(
            VerificationDailyStatsRow.model,
            VerificationDailyStatsRow.days_out,
        )
    ).all()

    for model, days_out, n_match, n_with_cat in model_rows:
        # ``sample_count`` is rows where both categories were present and a
        # category_match comparison was made — identical semantics to the
        # old raw path, which filtered ``category_match IS NOT NULL`` in the
        # WHERE clause (category_match is NULL whenever either category is
        # NULL). Don't be tempted to count ``n`` here — that counts rows
        # with NULL categories too.
        sample = int(n_with_cat or 0)
        accuracy = (
            round(float(n_match) / float(n_with_cat) * 100, 1)
            if n_with_cat else None
        )
        rows.append(CategoryAccuracyRow(
            model=model,
            days_out=days_out,
            accuracy_pct=accuracy,
            sample_count=sample,
        ))

    # TAF — still raw (taf_verification_scores has no model/days_out)
    taf_row = db.execute(
        select(
            func.avg(TafVerificationScoreRow.category_match),
            func.count(TafVerificationScoreRow.id),
        ).where(
            TafVerificationScoreRow.observation_time.between(since, until),
            TafVerificationScoreRow.category_match.isnot(None),
            TafVerificationScoreRow.source == source,
            _icao_clause(TafVerificationScoreRow.icao, icao_filter),
        )
    ).one()

    if taf_row[1] > 0:
        rows.append(CategoryAccuracyRow(
            model="TAF",
            days_out=0,
            accuracy_pct=round(float(taf_row[0]) * 100, 1) if taf_row[0] is not None else None,
            sample_count=taf_row[1],
        ))

    return rows


# ---------------------------------------------------------------------------
# Notable misses — still raw (needs individual rows)
# ---------------------------------------------------------------------------


def _category_delta(obs_cat: str | None, model_cat: str | None) -> tuple[str, int]:
    """Return (direction, severity) for a category mismatch.

    direction: "optimistic" if model predicted better than actual,
               "pessimistic" if model predicted worse.
    severity:  raw step count (1-3); callers that store this against the
               collapsed schema must apply ``min(severity, 2)`` themselves.
    """
    obs_rank = _CAT_ORDER.get(obs_cat or "", -1)
    model_rank = _CAT_ORDER.get(model_cat or "", -1)
    if obs_rank < 0 or model_rank < 0:
        return ("", 0)
    diff = obs_rank - model_rank  # positive = actual worse = optimistic
    if diff > 0:
        return ("optimistic", diff)
    elif diff < 0:
        return ("pessimistic", -diff)
    return ("", 0)


def get_notable_misses(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
    *, limit: int = 20,
) -> list[NotableMiss]:
    """Category busts at D-0/D-1, prioritising dangerous optimistic misses.

    Reads individual rows from ``verification_scores`` because every
    surfaced row needs its observation_time, icao, model, and the two
    category values — none of that survives aggregation. Bounded by
    ``limit`` so the query stays cheap.
    """
    stmt = (
        select(
            VerificationScoreRow.icao,
            VerificationScoreRow.observation_time,
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.obs_flight_category,
            VerificationScoreRow.model_flight_category,
            VerificationScoreRow.ceiling_delta_ft,
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.category_match == False,  # noqa: E712
            VerificationScoreRow.source == source,
            VerificationScoreRow.days_out.in_(_NEAR_TERM_LEADS),
            VerificationScoreRow.obs_flight_category.in_(tuple(_CAT_ORDER)),
            VerificationScoreRow.model_flight_category.in_(tuple(_CAT_ORDER)),
            _icao_clause(VerificationScoreRow.icao, icao_filter),
        )
        .order_by(VerificationScoreRow.observation_time.desc())
        .limit(500)  # fetch generously, filter & sort in Python
    )

    raw: list[NotableMiss] = []
    for row in db.execute(stmt).all():
        direction, severity = _category_delta(row[4], row[5])
        if severity == 0:
            continue
        if direction == "pessimistic" and severity < 2:
            continue
        raw.append(NotableMiss(
            icao=row[0],
            observation_time=row[1],
            model=row[2],
            days_out=row[3],
            obs_category=row[4],
            model_category=row[5],
            ceiling_delta_ft=int(row[6]) if row[6] is not None else None,
            direction=direction,
            severity=severity,
        ))

    raw.sort(key=lambda m: (m.direction != "optimistic", -m.severity))
    return raw[:limit]


# ---------------------------------------------------------------------------
# Category bias — from rollup
# ---------------------------------------------------------------------------


def get_category_bias_stats(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
) -> list[CategoryBiasStats]:
    """Per-model bias breakdown: how often each model is optimistic vs pessimistic.

    Restricted to D-0 / D-1 to match the original raw query. ``_2`` buckets
    are "2 or more levels off" — see :class:`CategoryBiasStats`.
    """
    since_d, until_d = _date_range(since, until)
    cat_total = (
        VerificationDailyStatsRow.n_cat_match
        + VerificationDailyStatsRow.n_cat_opt_1
        + VerificationDailyStatsRow.n_cat_opt_2
        + VerificationDailyStatsRow.n_cat_pess_1
        + VerificationDailyStatsRow.n_cat_pess_2
    )
    rows = db.execute(
        select(
            VerificationDailyStatsRow.model,
            VerificationDailyStatsRow.days_out,
            func.sum(cat_total).label("total_scores"),
            func.sum(VerificationDailyStatsRow.n_cat_opt_1).label("opt_1"),
            func.sum(VerificationDailyStatsRow.n_cat_opt_2).label("opt_2"),
            func.sum(VerificationDailyStatsRow.n_cat_pess_1).label("pess_1"),
            func.sum(VerificationDailyStatsRow.n_cat_pess_2).label("pess_2"),
        )
        .where(
            VerificationDailyStatsRow.date.between(since_d, until_d),
            VerificationDailyStatsRow.days_out.in_(_NEAR_TERM_LEADS),
            VerificationDailyStatsRow.source == source,
            _icao_clause(VerificationDailyStatsRow.icao, icao_filter),
        )
        .group_by(
            VerificationDailyStatsRow.model,
            VerificationDailyStatsRow.days_out,
        )
    ).all()

    return sorted(
        [
            CategoryBiasStats(
                model=model,
                days_out=days_out,
                total_scores=int(total or 0),
                optimistic_1=int(opt1 or 0),
                optimistic_2=int(opt2 or 0),
                pessimistic_1=int(pess1 or 0),
                pessimistic_2=int(pess2 or 0),
            )
            for model, days_out, total, opt1, opt2, pess1, pess2 in rows
        ],
        key=lambda s: (s.model, s.days_out),
    )


# ---------------------------------------------------------------------------
# Wind advisory accuracy — from rollup
# ---------------------------------------------------------------------------


def get_wind_advisory_accuracy(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
) -> list[WindAdvisoryStats]:
    """Per-model wind advisory match rate.

    NWP advisory counts come from the rollup. TAF still raw.
    """
    since_d, until_d = _date_range(since, until)
    adv_total = (
        VerificationDailyStatsRow.n_advisory_match
        + VerificationDailyStatsRow.n_advisory_opt
        + VerificationDailyStatsRow.n_advisory_pess
    )
    rows = db.execute(
        select(
            VerificationDailyStatsRow.model,
            func.sum(VerificationDailyStatsRow.n_advisory_match).label("n_match"),
            func.sum(adv_total).label("n_with_adv"),
        )
        .where(
            VerificationDailyStatsRow.date.between(since_d, until_d),
            VerificationDailyStatsRow.source == source,
            # Scoped to the same lead times as get_category_accuracy. A single
            # accuracy number blended over *every* lead time isn't a property of
            # the model — it's a property of whatever mix of lead times happens
            # to be in the table, so it moves whenever the horizon moves (#415).
            VerificationDailyStatsRow.days_out.in_(_DAYS_OUT_COLS),
            _icao_clause(VerificationDailyStatsRow.icao, icao_filter),
        )
        .group_by(VerificationDailyStatsRow.model)
    ).all()

    results = []
    for model, n_match, n_with_adv in rows:
        sample = int(n_with_adv or 0)
        if sample == 0:
            continue
        accuracy = round(float(n_match) / float(sample) * 100, 1)
        results.append(WindAdvisoryStats(
            model=model,
            accuracy_pct=accuracy,
            sample_count=sample,
        ))

    # TAF — still raw
    taf_row = db.execute(
        select(
            func.avg(TafVerificationScoreRow.advisory_match),
            func.count(TafVerificationScoreRow.id),
        ).where(
            TafVerificationScoreRow.observation_time.between(since, until),
            TafVerificationScoreRow.advisory_match.isnot(None),
            TafVerificationScoreRow.source == source,
            _icao_clause(TafVerificationScoreRow.icao, icao_filter),
        )
    ).one()

    if taf_row[1] > 0:
        results.append(WindAdvisoryStats(
            model="TAF",
            accuracy_pct=round(float(taf_row[0]) * 100, 1) if taf_row[0] is not None else None,
            sample_count=taf_row[1],
        ))

    return results


# ---------------------------------------------------------------------------
# Gust accuracy (#491)
# ---------------------------------------------------------------------------


def _gust_stats(
    model: str,
    days_out: int,
    *,
    n: int,
    n_gust: int,
    sum_abs_gust: float | None,
    sum_gust: float | None,
    n_flagged: int,
    n_flagged_peak: int,
    sum_over_peak: float | None,
    n_obs_gust: int,
    n_flag_hit: int,
) -> GustAccuracyStats:
    """Turn additive gust counters into the reported ratios.

    Same ``sum / n`` shape as every other rollup-backed metric — the sums are
    additive across days, the ratios are not, so they're derived here.

    ``n_flagged`` counts every hour the forecast called a gust;
    ``n_flagged_peak`` counts the subset that also has a realised peak to
    measure against (a METAR with neither a gust nor a mean wind has none).
    They are kept apart so the over-warn ratio and the over-peak mean are each
    divided by their own denominator.
    """
    return GustAccuracyStats(
        model=model,
        days_out=days_out,
        n=n,
        n_gust=n_gust,
        gust_mae_kt=(
            round(float(sum_abs_gust) / n_gust, 1)
            if n_gust and sum_abs_gust is not None else None
        ),
        gust_bias_kt=(
            round(float(sum_gust) / n_gust, 1)
            if n_gust and sum_gust is not None else None
        ),
        n_flagged=n_flagged,
        flagged_over_peak_kt=(
            round(float(sum_over_peak) / n_flagged_peak, 1)
            if n_flagged_peak and sum_over_peak is not None else None
        ),
        n_obs_gust=n_obs_gust,
        n_flag_hit=n_flag_hit,
        over_warn_ratio=(
            round(n_flagged / n_obs_gust, 2) if n_obs_gust else None
        ),
    )


def get_gust_accuracy(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
) -> list[GustAccuracyStats]:
    """Per-model gust accuracy under both conditionings (#491).

    NWP reads the daily rollup (one row per model/days_out); TAF is
    aggregated at query time from ``taf_verification_scores`` joined to
    ``verification_observations``, like every other TAF stat — the TAF gust
    and the observed gust both live on the observation row.

    See ``tasks/verification_gust`` for what "flagged" and "realised peak"
    mean, and why the forecast-flagged and obs-flagged numbers are reported
    side by side rather than averaged.
    """
    since_d, until_d = _date_range(since, until)
    v = VerificationDailyStatsRow
    rows = db.execute(
        select(
            v.model,
            v.days_out,
            func.sum(v.n).label("n"),
            func.sum(v.n_gust).label("n_gust"),
            func.sum(v.sum_abs_gust_delta_kt).label("sum_abs_gust"),
            func.sum(v.sum_gust_delta_kt).label("sum_gust"),
            func.sum(v.n_gust_flagged_peak).label("n_flagged_peak"),
            func.sum(v.sum_gust_flagged_over_peak_kt).label("sum_over_peak"),
            func.sum(v.n_model_gust_flag).label("n_model_gust_flag"),
            func.sum(v.n_obs_gust).label("n_obs_gust"),
            func.sum(v.n_gust_flag_hit).label("n_flag_hit"),
        )
        .where(
            v.date.between(since_d, until_d),
            v.source == source,
            v.days_out.in_(_DAYS_OUT_COLS),
            _icao_clause(v.icao, icao_filter),
        )
        .group_by(v.model, v.days_out)
    ).all()

    results: list[GustAccuracyStats] = []
    for r in rows:
        stats = _gust_stats(
            r.model, r.days_out,
            n=int(r.n or 0),
            n_gust=int(r.n_gust or 0),
            sum_abs_gust=r.sum_abs_gust,
            sum_gust=r.sum_gust,
            n_flagged=int(r.n_model_gust_flag or 0),
            n_flagged_peak=int(r.n_flagged_peak or 0),
            sum_over_peak=r.sum_over_peak,
            n_obs_gust=int(r.n_obs_gust or 0),
            n_flag_hit=int(r.n_flag_hit or 0),
        )
        if stats.n_gust or stats.n_flagged or stats.n_obs_gust:
            results.append(stats)

    results.sort(key=lambda s: (s.model, s.days_out))

    # TAF — raw, joined to the observation for the gust group and peak.
    t = TafVerificationScoreRow
    o = VerificationObservationRow
    peak = func.coalesce(o.wind_gust_kt, o.wind_speed_kt)
    taf_flagged = o.taf_wind_gust_kt.isnot(None)
    obs_gusting = o.wind_gust_kt.isnot(None)
    taf_row = db.execute(
        select(
            func.count(t.id).label("n"),
            func.count(t.wind_gust_delta_kt).label("n_gust"),
            func.sum(func.abs(t.wind_gust_delta_kt)).label("sum_abs_gust"),
            func.sum(t.wind_gust_delta_kt).label("sum_gust"),
            func.coalesce(
                func.sum(case((taf_flagged, 1), else_=0)), 0,
            ).label("n_flagged"),
            func.coalesce(
                func.sum(case((taf_flagged & peak.isnot(None), 1), else_=0)), 0,
            ).label("n_flagged_peak"),
            func.sum(
                case(
                    (taf_flagged & peak.isnot(None), o.taf_wind_gust_kt - peak),
                    else_=None,
                )
            ).label("sum_over_peak"),
            func.coalesce(
                func.sum(case((obs_gusting, 1), else_=0)), 0,
            ).label("n_obs_gust"),
            func.coalesce(
                func.sum(case((taf_flagged & obs_gusting, 1), else_=0)), 0,
            ).label("n_flag_hit"),
        )
        .join(o, t.observation_id == o.id)
        .where(
            t.observation_time.between(since, until),
            t.source == source,
            _icao_clause(t.icao, icao_filter),
        )
    ).one()

    if taf_row.n:
        taf_stats = _gust_stats(
            "TAF", 0,
            n=int(taf_row.n or 0),
            n_gust=int(taf_row.n_gust or 0),
            sum_abs_gust=taf_row.sum_abs_gust,
            sum_gust=taf_row.sum_gust,
            n_flagged=int(taf_row.n_flagged or 0),
            n_flagged_peak=int(taf_row.n_flagged_peak or 0),
            sum_over_peak=taf_row.sum_over_peak,
            n_obs_gust=int(taf_row.n_obs_gust or 0),
            n_flag_hit=int(taf_row.n_flag_hit or 0),
        )
        if taf_stats.n_gust or taf_stats.n_flagged or taf_stats.n_obs_gust:
            results.append(taf_stats)

    return results


# ---------------------------------------------------------------------------
# Missed warnings — still raw (needs individual rows)
# ---------------------------------------------------------------------------


def get_missed_warnings(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    icao_filter: list[str] | None = None,
    *, limit: int = 10,
) -> list[MissedWarning]:
    """Observed ``red`` wind advisories that a model called something milder.

    Scoped to D-0/D-1 like its sibling ``get_notable_misses``: a missed warning
    six days out is expected rather than notable, and without the scope one
    storm fills the list with a copy of itself per lead time.

    Rows where the model has no advisory at all (NULL — e.g. no runway data)
    are absence, not a miss, and are excluded explicitly. SQL's three-valued
    logic already drops them from ``!= 'red'``, but ``MissedWarning`` requires a
    ``str``, so we don't leave that to a subtlety of the dialect.
    """
    stmt = (
        select(
            VerificationScoreRow.icao,
            VerificationScoreRow.observation_time,
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.obs_wind_advisory,
            VerificationScoreRow.model_wind_advisory,
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.obs_wind_advisory == _WIND_WARNING,
            VerificationScoreRow.model_wind_advisory != _WIND_WARNING,
            VerificationScoreRow.model_wind_advisory.isnot(None),
            VerificationScoreRow.days_out.in_(_NEAR_TERM_LEADS),
            VerificationScoreRow.source == source,
            _icao_clause(VerificationScoreRow.icao, icao_filter),
        )
        .order_by(VerificationScoreRow.observation_time.desc())
        .limit(limit)
    )

    return [
        MissedWarning(
            icao=row[0],
            observation_time=row[1],
            model=row[2],
            days_out=row[3],
            obs_wind_advisory=row[4],
            model_wind_advisory=row[5],
        )
        for row in db.execute(stmt).all()
    ]


# ---------------------------------------------------------------------------
# Optimistic-bias leaderboard — new (#154)
# ---------------------------------------------------------------------------


def get_optimistic_bias_leaderboard(
    db: Session, since: datetime, until: datetime,
    model: str, days_out: int,
    *, limit: int = 50,
    source: str = "standalone",
) -> list[OptimisticBiasLeaderboardRow]:
    """Top airports where ``model`` at ``days_out`` over-promises.

    Score = ``(n_cat_opt_1 + 2 * n_cat_opt_2) / n_with_cat`` — equally weights
    single-step optimistic misses, double-weights 2+-step misses, normalised
    by sample count of rows with valid category pairs. Higher = more
    dangerously optimistic.

    Issue #154 spec says ``/ n``; we tighten that to ``n_with_cat`` (only
    rows that landed in one of the 5 direction buckets). Using raw ``n``
    silently deflates the score at airports with more NULL-category rows
    (unparsable METARs, broken ceiling derivations) — those rows can't
    contribute to the bias buckets but would still count in the denominator.

    Filters airports with ``n < 10`` to avoid noise (still on raw n so the
    threshold matches the public sample-count display).
    """
    since_d, until_d = _date_range(since, until)

    opt_1 = func.sum(VerificationDailyStatsRow.n_cat_opt_1)
    opt_2 = func.sum(VerificationDailyStatsRow.n_cat_opt_2)
    n_sum = func.sum(VerificationDailyStatsRow.n)
    # Denominator: only rows where the category comparison was made
    n_with_cat = func.sum(
        VerificationDailyStatsRow.n_cat_match
        + VerificationDailyStatsRow.n_cat_opt_1
        + VerificationDailyStatsRow.n_cat_opt_2
        + VerificationDailyStatsRow.n_cat_pess_1
        + VerificationDailyStatsRow.n_cat_pess_2
    )
    score = (opt_1 + 2 * opt_2) / func.nullif(n_with_cat, 0)

    rows = db.execute(
        select(
            VerificationDailyStatsRow.icao,
            n_sum.label("n"),
            opt_1.label("opt_1"),
            opt_2.label("opt_2"),
            score.label("score"),
        )
        .where(
            VerificationDailyStatsRow.date.between(since_d, until_d),
            VerificationDailyStatsRow.source == source,
            VerificationDailyStatsRow.model == model,
            VerificationDailyStatsRow.days_out == days_out,
        )
        .group_by(VerificationDailyStatsRow.icao)
        .having(n_sum >= 10)
        .order_by(score.desc())
        .limit(limit)
    ).all()

    return [
        OptimisticBiasLeaderboardRow(
            icao=icao,
            n=int(n or 0),
            n_cat_opt_1=int(opt1 or 0),
            n_cat_opt_2=int(opt2 or 0),
            score=float(sc) if sc is not None else 0.0,
        )
        for icao, n, opt1, opt2, sc in rows
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def get_digest_data(
    db: Session,
    since: datetime,
    until: datetime,
    *,
    source: str = "flight",
    period_label: str = "",
    include_7d: bool = True,
    icao_filter: list[str] | None = None,
) -> VerificationDigestData:
    """Build complete digest payload for email or web dashboard."""
    timings: dict[str, int] = {}

    def _timed(label: str, fn, *args):
        t = time.monotonic()
        result = fn(*args)
        timings[label] = int((time.monotonic() - t) * 1000)
        return result

    activity = _timed("activity", get_activity_summary, db, since, until, source, icao_filter)
    category_today = _timed("category", get_category_accuracy, db, since, until, source, icao_filter)
    notable = _timed("notable", get_notable_misses, db, since, until, source, icao_filter)
    bias = _timed("bias", get_category_bias_stats, db, since, until, source, icao_filter)
    wind = _timed("wind", get_wind_advisory_accuracy, db, since, until, source, icao_filter)
    gust = _timed("gust", get_gust_accuracy, db, since, until, source, icao_filter)
    missed = _timed("missed", get_missed_warnings, db, since, until, source, icao_filter)

    category_7d: list[CategoryAccuracyRow] = []
    if include_7d:
        seven_days_ago = until - timedelta(days=7)
        category_7d = _timed(
            "category_7d", get_category_accuracy, db, seven_days_ago, until, source, icao_filter,
        )

    # Sub-query breakdown so a pathological plan is visible in one log line
    # (#448 — the activity COUNT(DISTINCT)s ran at ~9.5 min each for months
    # with no trace). INFO when anything is meaningfully slow, DEBUG otherwise.
    total_ms = sum(timings.values())
    log = logger.info if total_ms > 5000 else logger.debug
    log(
        "get_digest_data(%s, %s): %dms total (%s)",
        source, period_label or "-", total_ms,
        " ".join(f"{k}={v}ms" for k, v in timings.items()),
    )

    return VerificationDigestData(
        period_label=period_label,
        activity=activity,
        category_accuracy_today=category_today,
        category_accuracy_7d=category_7d,
        notable_misses=notable,
        category_bias=bias,
        wind_advisory=wind,
        gust_accuracy=gust,
        missed_warnings=missed,
    )
