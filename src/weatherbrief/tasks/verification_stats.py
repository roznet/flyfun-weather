"""Shared query module for verification statistics.

Used by both the daily email digest and the admin web dashboard API.
All functions take a SQLAlchemy Session and a date range, returning
Pydantic models from :mod:`weatherbrief.models.verification`.

Every query filters by ``source`` ('flight' or 'standalone') to ensure
flight-based and standalone verification data are never mixed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    FlightRow,
    FlightVerificationMapRow,
    TafVerificationScoreRow,
    VerificationCycleRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.models.verification import (
    ActivitySummary,
    CategoryAccuracyRow,
    CategoryBiasStats,
    MAERow,
    MissedWarning,
    NotableMiss,
    VerificationDigestData,
    WindAdvisoryStats,
)

logger = logging.getLogger(__name__)

_DAYS_OUT_COLS = (0, 1, 2, 3)


# ---------------------------------------------------------------------------
# Activity summary
# ---------------------------------------------------------------------------


def get_activity_summary(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
) -> ActivitySummary:
    """High-level counts for a date range, scoped by source."""
    # Count observations and airports that have scores for this source,
    # so flight vs standalone dashboards show relevant counts only.
    scored_obs_ids = (
        select(func.distinct(VerificationScoreRow.observation_id))
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.source == source,
        )
        .scalar_subquery()
    )
    obs_count = db.execute(
        select(func.count(VerificationObservationRow.id)).where(
            VerificationObservationRow.id.in_(scored_obs_ids),
        )
    ).scalar() or 0

    airport_count = db.execute(
        select(func.count(func.distinct(VerificationObservationRow.icao))).where(
            VerificationObservationRow.id.in_(scored_obs_ids),
        )
    ).scalar() or 0

    # Flight-specific counts (only relevant for source='flight')
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

    # Cycle metrics — filtered by source
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
) -> list[CategoryAccuracyRow]:
    """Flight-category match rate per model and days-out.

    Includes TAF as a pseudo-model with days_out=0.
    """
    rows: list[CategoryAccuracyRow] = []

    # NWP model scores
    model_rows = db.execute(
        select(
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            func.avg(VerificationScoreRow.category_match),
            func.count(VerificationScoreRow.id),
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.category_match.isnot(None),
            VerificationScoreRow.days_out.in_(_DAYS_OUT_COLS),
            VerificationScoreRow.source == source,
        )
        .group_by(VerificationScoreRow.model, VerificationScoreRow.days_out)
    ).all()

    for model, days_out, avg_match, count in model_rows:
        rows.append(CategoryAccuracyRow(
            model=model,
            days_out=days_out,
            accuracy_pct=round(float(avg_match) * 100, 1) if avg_match is not None else None,
            sample_count=count,
        ))

    # TAF scores (always days_out=0)
    taf_row = db.execute(
        select(
            func.avg(TafVerificationScoreRow.category_match),
            func.count(TafVerificationScoreRow.id),
        ).where(
            TafVerificationScoreRow.observation_time.between(since, until),
            TafVerificationScoreRow.category_match.isnot(None),
            TafVerificationScoreRow.source == source,
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
# Notable misses — category busts with direction & severity
# ---------------------------------------------------------------------------

# Ordered from best to worst flying conditions
_CAT_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def _category_delta(obs_cat: str | None, model_cat: str | None) -> tuple[str, int]:
    """Return (direction, severity) for a category mismatch.

    direction: "optimistic" if model predicted better than actual,
               "pessimistic" if model predicted worse.
    severity:  number of category levels apart (1-3).
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
    *, limit: int = 20,
) -> list[NotableMiss]:
    """Category busts at D-0/D-1, prioritising dangerous optimistic misses.

    Includes:
    - All optimistic misses (severity 1+): model said better than actual
    - Pessimistic misses only at severity 2+: model said much worse than actual

    Sorted by severity desc (worst first), then optimistic before pessimistic.
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
            VerificationScoreRow.days_out.in_((0, 1)),
            VerificationScoreRow.obs_flight_category.in_(tuple(_CAT_ORDER)),
            VerificationScoreRow.model_flight_category.in_(tuple(_CAT_ORDER)),
        )
        .order_by(VerificationScoreRow.observation_time.desc())
        .limit(500)  # fetch generously, filter & sort in Python
    )

    raw: list[NotableMiss] = []
    for row in db.execute(stmt).all():
        direction, severity = _category_delta(row[4], row[5])
        if severity == 0:
            continue
        # Include all optimistic, but only severity 2+ pessimistic
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

    # Sort: severity desc, optimistic first
    raw.sort(key=lambda m: (-m.severity, m.direction != "optimistic"))
    return raw[:limit]


def get_category_bias_stats(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
) -> list[CategoryBiasStats]:
    """Per-model bias breakdown: how often each model is optimistic vs pessimistic."""
    stmt = (
        select(
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.obs_flight_category,
            VerificationScoreRow.model_flight_category,
            func.count(VerificationScoreRow.id),
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.source == source,
            VerificationScoreRow.days_out.in_((0, 1)),
            VerificationScoreRow.obs_flight_category.in_(tuple(_CAT_ORDER)),
            VerificationScoreRow.model_flight_category.in_(tuple(_CAT_ORDER)),
        )
        .group_by(
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.obs_flight_category,
            VerificationScoreRow.model_flight_category,
        )
    )

    # Accumulate per (model, days_out)
    accum: dict[tuple[str, int], CategoryBiasStats] = {}
    for model, days_out, obs_cat, model_cat, count in db.execute(stmt).all():
        key = (model, days_out)
        if key not in accum:
            accum[key] = CategoryBiasStats(model=model, days_out=days_out)
        stats = accum[key]
        stats.total_scores += count

        direction, severity = _category_delta(obs_cat, model_cat)
        if severity == 0:
            continue
        field = f"{direction}_{severity}"
        setattr(stats, field, getattr(stats, field) + count)

    return sorted(accum.values(), key=lambda s: (s.model, s.days_out))


# ---------------------------------------------------------------------------
# Wind advisory accuracy
# ---------------------------------------------------------------------------


def get_wind_advisory_accuracy(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
) -> list[WindAdvisoryStats]:
    """Per-model wind advisory match rate."""
    rows = db.execute(
        select(
            VerificationScoreRow.model,
            func.avg(VerificationScoreRow.advisory_match),
            func.count(VerificationScoreRow.id),
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.advisory_match.isnot(None),
            VerificationScoreRow.source == source,
        )
        .group_by(VerificationScoreRow.model)
    ).all()

    results = [
        WindAdvisoryStats(
            model=model,
            accuracy_pct=round(float(avg_match) * 100, 1) if avg_match is not None else None,
            sample_count=count,
        )
        for model, avg_match, count in rows
    ]

    # TAF wind advisory
    taf_row = db.execute(
        select(
            func.avg(TafVerificationScoreRow.advisory_match),
            func.count(TafVerificationScoreRow.id),
        ).where(
            TafVerificationScoreRow.observation_time.between(since, until),
            TafVerificationScoreRow.advisory_match.isnot(None),
            TafVerificationScoreRow.source == source,
        )
    ).one()

    if taf_row[1] > 0:
        results.append(WindAdvisoryStats(
            model="TAF",
            accuracy_pct=round(float(taf_row[0]) * 100, 1) if taf_row[0] is not None else None,
            sample_count=taf_row[1],
        ))

    return results


def get_missed_warnings(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
    *, limit: int = 10,
) -> list[MissedWarning]:
    """Wind WARNINGs that models failed to predict."""
    stmt = (
        select(
            VerificationScoreRow.icao,
            VerificationScoreRow.observation_time,
            VerificationScoreRow.model,
            VerificationScoreRow.obs_wind_advisory,
            VerificationScoreRow.model_wind_advisory,
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.obs_wind_advisory == "WARNING",
            VerificationScoreRow.model_wind_advisory != "WARNING",
            VerificationScoreRow.source == source,
        )
        .order_by(VerificationScoreRow.observation_time.desc())
        .limit(limit)
    )

    return [
        MissedWarning(
            icao=row[0],
            observation_time=row[1],
            model=row[2],
            obs_wind_advisory=row[3],
            model_wind_advisory=row[4],
        )
        for row in db.execute(stmt).all()
    ]


# ---------------------------------------------------------------------------
# MAE stats (web-only)
# ---------------------------------------------------------------------------


def get_mae_stats(
    db: Session, since: datetime, until: datetime,
    source: str = "flight",
) -> list[MAERow]:
    """Mean absolute error per model at D-0 and D-1."""
    rows = db.execute(
        select(
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            func.avg(func.abs(VerificationScoreRow.ceiling_delta_ft)),
            func.avg(func.abs(VerificationScoreRow.visibility_delta_m)),
            func.avg(func.abs(VerificationScoreRow.wind_speed_delta_kt)),
            func.avg(func.abs(VerificationScoreRow.temperature_delta_c)),
            func.count(VerificationScoreRow.id),
        )
        .where(
            VerificationScoreRow.observation_time.between(since, until),
            VerificationScoreRow.days_out.in_((0, 1)),
            VerificationScoreRow.source == source,
        )
        .group_by(VerificationScoreRow.model, VerificationScoreRow.days_out)
    ).all()

    return [
        MAERow(
            model=row[0],
            days_out=row[1],
            ceiling_mae_ft=round(float(row[2]), 0) if row[2] is not None else None,
            visibility_mae_m=round(float(row[3]), 0) if row[3] is not None else None,
            wind_speed_mae_kt=round(float(row[4]), 1) if row[4] is not None else None,
            temperature_mae_c=round(float(row[5]), 1) if row[5] is not None else None,
            sample_count=row[6],
        )
        for row in rows
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
) -> VerificationDigestData:
    """Build complete digest payload for email or web dashboard."""
    activity = get_activity_summary(db, since, until, source)
    category_today = get_category_accuracy(db, since, until, source)
    notable = get_notable_misses(db, since, until, source)
    bias = get_category_bias_stats(db, since, until, source)
    wind = get_wind_advisory_accuracy(db, since, until, source)
    missed = get_missed_warnings(db, since, until, source)
    mae = get_mae_stats(db, since, until, source)

    # 7-day rolling for comparison
    category_7d: list[CategoryAccuracyRow] = []
    if include_7d:
        seven_days_ago = until - timedelta(days=7)
        category_7d = get_category_accuracy(db, seven_days_ago, until, source)

    return VerificationDigestData(
        period_label=period_label,
        activity=activity,
        category_accuracy_today=category_today,
        category_accuracy_7d=category_7d,
        notable_misses=notable,
        category_bias=bias,
        wind_advisory=wind,
        missed_warnings=missed,
        mae_stats=mae,
    )
