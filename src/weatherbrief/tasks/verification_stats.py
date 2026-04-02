"""Shared query module for verification statistics.

Used by both the daily email digest and the admin web dashboard API.
All functions take a SQLAlchemy Session and a date range, returning
Pydantic models from :mod:`weatherbrief.models.verification`.
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
    MAERow,
    MissedWarning,
    NotableMiss,
    VerificationDigestData,
    WindAdvisoryStats,
)

logger = logging.getLogger(__name__)

_DAYS_OUT_COLS = (0, 1, 2, 3, 5, 7)


# ---------------------------------------------------------------------------
# Activity summary
# ---------------------------------------------------------------------------


def get_activity_summary(
    db: Session, since: datetime, until: datetime,
) -> ActivitySummary:
    """High-level counts for a date range."""
    obs_count = db.execute(
        select(func.count(VerificationObservationRow.id)).where(
            VerificationObservationRow.observation_time.between(since, until)
        )
    ).scalar() or 0

    airport_count = db.execute(
        select(func.count(func.distinct(VerificationObservationRow.icao))).where(
            VerificationObservationRow.observation_time.between(since, until)
        )
    ).scalar() or 0

    # Flights that had observations in this window
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

    # Flights completed (scored) in this window
    flights_completed = db.execute(
        select(func.count(FlightRow.id)).where(
            FlightRow.verification_status.in_(("complete", "scored")),
        )
    ).scalar() or 0

    # Cycle metrics
    cycle_rows = db.execute(
        select(
            func.count(VerificationCycleRow.id),
            func.avg(VerificationCycleRow.duration_ms),
        ).where(VerificationCycleRow.started_at.between(since, until))
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
# Notable misses (IFR/LIFR busts)
# ---------------------------------------------------------------------------

_IFR_CATS = ("IFR", "LIFR")
_VFR_CATS = ("VFR", "MVFR")


def get_notable_misses(
    db: Session, since: datetime, until: datetime, *, limit: int = 10,
) -> list[NotableMiss]:
    """Category busts where model and observation disagree by 2+ levels."""
    # Model missed IFR (predicted VFR/MVFR but actual was IFR/LIFR)
    # or false alarm (predicted IFR/LIFR but actual was VFR/MVFR)
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
            or_(
                # Model missed IFR/LIFR
                (
                    VerificationScoreRow.obs_flight_category.in_(_IFR_CATS)
                    & VerificationScoreRow.model_flight_category.in_(_VFR_CATS)
                ),
                # Model false-alarmed IFR/LIFR
                (
                    VerificationScoreRow.model_flight_category.in_(_IFR_CATS)
                    & VerificationScoreRow.obs_flight_category.in_(_VFR_CATS)
                ),
            ),
        )
        .order_by(VerificationScoreRow.observation_time.desc())
        .limit(limit)
    )

    results = []
    for row in db.execute(stmt).all():
        results.append(NotableMiss(
            icao=row[0],
            observation_time=row[1],
            model=row[2],
            days_out=row[3],
            obs_category=row[4],
            model_category=row[5],
            ceiling_delta_ft=int(row[6]) if row[6] is not None else None,
        ))
    return results


# ---------------------------------------------------------------------------
# Wind advisory accuracy
# ---------------------------------------------------------------------------


def get_wind_advisory_accuracy(
    db: Session, since: datetime, until: datetime,
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
    db: Session, since: datetime, until: datetime, *, limit: int = 10,
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
    period_label: str = "",
    include_7d: bool = True,
) -> VerificationDigestData:
    """Build complete digest payload for email or web dashboard."""
    activity = get_activity_summary(db, since, until)
    category_today = get_category_accuracy(db, since, until)
    notable = get_notable_misses(db, since, until)
    wind = get_wind_advisory_accuracy(db, since, until)
    missed = get_missed_warnings(db, since, until)
    mae = get_mae_stats(db, since, until)

    # 7-day rolling for comparison
    category_7d: list[CategoryAccuracyRow] = []
    if include_7d:
        seven_days_ago = until - timedelta(days=7)
        category_7d = get_category_accuracy(db, seven_days_ago, until)

    return VerificationDigestData(
        period_label=period_label,
        activity=activity,
        category_accuracy_today=category_today,
        category_accuracy_7d=category_7d,
        notable_misses=notable,
        wind_advisory=wind,
        missed_warnings=missed,
        mae_stats=mae,
    )
