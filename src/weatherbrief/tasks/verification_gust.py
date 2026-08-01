"""Gust verification: standardised definitions, rollup aggregates, backfill.

Wind *gust* is scored alongside steady wind since #491. Every gust number the
scoring path, the rollups, the dashboard and any ad-hoc analysis produce comes
from the definitions below, so the two conditionings never get silently
averaged into one self-contradictory figure.

Definitions
-----------

- **Sign** — ``delta = forecast - observed``. Negative means the forecast sits
  below reality.
- **Realised peak** — the observed gust when the METAR reports a gust group,
  else the observed mean wind. A METAR only carries a gust group when the peak
  exceeds the mean by ~10 kt, so "no gust group" means the peak *is* the mean;
  it does not mean the peak is unknown.
- **"The forecast shows a gust"** — ``forecast_gust - forecast_wind >=
  GUST_FLAG_THRESHOLD_KT``, i.e. the same ~10 kt criterion that puts a gust
  group on a METAR. Persisted per model score as ``model_gust_flag``. The TAF
  equivalent is "the applicable TAF trend carries a gust group"
  (``verification_observations.taf_wind_gust_kt`` is non-NULL).
- **Report both conditionings, never a single blended number:**

  * *forecast-flagged* hours — on the hours a forecast calls a gust, how far
    above the realised peak does it sit? (the "why does Windy's gust layer sit
    above the TAFs?" view — models run ~+7 kt high here, and ~80% of those
    hours the airport wasn't gusting at all.)
  * *obs-flagged* hours — on the hours the airport actually gusted, how far
    below the true peak does the forecast sit? (the extreme-day view — models
    run 4–13 kt *low* here.)

  The two select different, mostly non-overlapping samples. Collapsing them is
  what made the first ad-hoc pass look self-contradictory.

Occurrence is a 2×2 contingency built from three stored counts:
``n_model_gust_flag`` (forecast called a gust), ``n_obs_gust`` (airport
gusted), ``n_gust_flag_hit`` (both). False alarms are ``flag - hit``, misses
are ``obs - hit``, and the over-warn ratio is ``flag / obs``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, exists, func, select, update
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)

logger = logging.getLogger(__name__)

# A METAR reports a gust group when the peak exceeds the mean by ~10 kt. The
# same threshold is applied to forecasts so "the forecast shows a gust" and
# "the airport was gusting" mean the same thing.
GUST_FLAG_THRESHOLD_KT = 10.0

# Backfill pairs a score with a snapshot only inside the same window
# `_score_cycle` uses live, so a backfilled row never says more than a live one.
_MATCH_WINDOW = timedelta(minutes=90)


# ---------------------------------------------------------------------------
# Scalar definitions (scoring path)
# ---------------------------------------------------------------------------


def forecast_shows_gust(
    wind_kt: float | None, gust_kt: float | None,
) -> bool | None:
    """Whether a forecast's gust is a *gust* by the METAR ~10 kt criterion.

    Returns None when either component is missing — absence of data, not a
    negative.
    """
    if wind_kt is None or gust_kt is None:
        return None
    return (float(gust_kt) - float(wind_kt)) >= GUST_FLAG_THRESHOLD_KT


def realised_peak_kt(
    obs_wind_kt: float | None, obs_gust_kt: float | None,
) -> float | None:
    """The observed peak wind: the gust if reported, else the mean wind."""
    if obs_gust_kt is not None:
        return float(obs_gust_kt)
    if obs_wind_kt is not None:
        return float(obs_wind_kt)
    return None


# ---------------------------------------------------------------------------
# SQL aggregates (shared by the daily and monthly rollups)
# ---------------------------------------------------------------------------


def _sum_when(condition):
    """SUM of 1 when ``condition`` is TRUE (NULL/FALSE both excluded)."""
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def realised_peak_expr():
    """SQL ``realised_peak_kt`` over a joined ``verification_observations``."""
    return func.coalesce(
        VerificationObservationRow.wind_gust_kt,
        VerificationObservationRow.wind_speed_kt,
    )


def gust_aggregate_columns() -> list:
    """Labelled gust aggregates for a GROUP BY over scores joined to obs.

    The caller must join :class:`VerificationScoreRow` to
    :class:`VerificationObservationRow` on ``observation_id``. Column names
    match ``verification_daily_stats``; the monthly rollup consumes a subset
    by label.
    """
    delta = VerificationScoreRow.wind_gust_delta_kt
    flag = VerificationScoreRow.model_gust_flag.is_(True)
    obs_gusting = VerificationObservationRow.wind_gust_kt.isnot(None)
    peak = realised_peak_expr()
    over_peak = VerificationScoreRow.model_wind_gust_kt - peak

    return [
        # obs-flagged magnitude — a non-NULL delta means both gusts exist,
        # i.e. the airport gusted and the forecast had a gust value.
        func.count(delta).label("n_gust"),
        func.sum(func.abs(delta)).label("sum_abs_gust_delta_kt"),
        func.sum(delta).label("sum_gust_delta_kt"),
        # forecast-flagged magnitude — measured against the realised peak, so
        # the ~80% of flagged hours with no observed gust still contribute.
        _sum_when(
            flag
            & VerificationScoreRow.model_wind_gust_kt.isnot(None)
            & peak.isnot(None)
        ).label("n_gust_flagged_peak"),
        func.sum(
            case(
                (
                    flag
                    & VerificationScoreRow.model_wind_gust_kt.isnot(None)
                    & peak.isnot(None),
                    over_peak,
                ),
                else_=None,
            )
        ).label("sum_gust_flagged_over_peak_kt"),
        # occurrence contingency
        _sum_when(flag).label("n_model_gust_flag"),
        _sum_when(obs_gusting).label("n_obs_gust"),
        _sum_when(flag & obs_gusting).label("n_gust_flag_hit"),
    ]


def taf_gust_aggregate_columns() -> list:
    """Labelled TAF gust aggregates for a GROUP BY over TAF scores joined to obs.

    The TAF mirror of :func:`gust_aggregate_columns`, with two differences that
    follow from where the data lives:

    - **"The TAF shows a gust"** is "the applicable trend carried a gust group",
      i.e. ``verification_observations.taf_wind_gust_kt`` is non-NULL — not the
      ~10 kt forecast criterion used for NWP. A TAF gust group is *already*
      that criterion, applied by whoever wrote the TAF.
    - The forecast gust itself is read off the observation row rather than the
      score row: ``taf_verification_scores`` stores only the delta, because
      unlike an NWP snapshot the TAF gust is permanent on the observation.

    The caller must join :class:`TafVerificationScoreRow` to
    :class:`VerificationObservationRow` on ``observation_id``. Column labels
    match ``taf_verification_daily``; ``verification_stats.get_gust_accuracy``
    consumes the same labels on its raw query-time path so the rollup-backed
    and raw-backed TAF numbers are the same numbers by construction.
    """
    delta = TafVerificationScoreRow.wind_gust_delta_kt
    taf_flagged = VerificationObservationRow.taf_wind_gust_kt.isnot(None)
    obs_gusting = VerificationObservationRow.wind_gust_kt.isnot(None)
    peak = realised_peak_expr()
    over_peak = VerificationObservationRow.taf_wind_gust_kt - peak

    return [
        func.count(delta).label("n_gust"),
        func.sum(func.abs(delta)).label("sum_abs_gust_delta_kt"),
        func.sum(delta).label("sum_gust_delta_kt"),
        _sum_when(taf_flagged & peak.isnot(None)).label("n_gust_flagged_peak"),
        func.sum(
            case((taf_flagged & peak.isnot(None), over_peak), else_=None)
        ).label("sum_gust_flagged_over_peak_kt"),
        _sum_when(taf_flagged).label("n_taf_gust_flag"),
        _sum_when(obs_gusting).label("n_obs_gust"),
        _sum_when(taf_flagged & obs_gusting).label("n_gust_flag_hit"),
    ]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def backfill_taf_gust_deltas(
    db: Session, *, batch_size: int = 50_000,
) -> int:
    """Fill ``taf_verification_scores.wind_gust_delta_kt`` from stored obs.

    Fully backfillable for all history: both the TAF gust
    (``taf_wind_gust_kt``) and the observed gust live permanently on
    ``verification_observations``, reachable via ``observation_id``.

    Idempotent — only rows whose observation carries both gusts and whose
    delta is still NULL are touched. Commits per id batch so a long backfill
    doesn't hold one transaction open.
    """
    max_id = db.execute(
        select(func.max(TafVerificationScoreRow.id))
    ).scalar() or 0

    obs_delta = (
        select(
            VerificationObservationRow.taf_wind_gust_kt
            - VerificationObservationRow.wind_gust_kt
        )
        .where(
            VerificationObservationRow.id
            == TafVerificationScoreRow.observation_id
        )
        .scalar_subquery()
    )
    has_both = exists(
        select(1).where(
            VerificationObservationRow.id
            == TafVerificationScoreRow.observation_id,
            VerificationObservationRow.taf_wind_gust_kt.isnot(None),
            VerificationObservationRow.wind_gust_kt.isnot(None),
        )
    )

    updated = 0
    lo = 0
    while lo <= max_id:
        result = db.execute(
            update(TafVerificationScoreRow)
            .where(
                TafVerificationScoreRow.id >= lo,
                TafVerificationScoreRow.id < lo + batch_size,
                TafVerificationScoreRow.wind_gust_delta_kt.is_(None),
                has_both,
            )
            .values(wind_gust_delta_kt=obs_delta)
            .execution_options(synchronize_session=False)
        )
        updated += result.rowcount or 0
        db.commit()
        lo += batch_size

    logger.info("TAF gust backfill: updated %d scores", updated)
    return updated


def backfill_model_gust(db: Session, *, days: int = 10) -> int:
    """Fill model gust fields on ``verification_scores`` from snapshots.

    Only the un-pruned ``airport_forecast_snapshots`` window is recoverable
    (retention is 10 days) — beyond that, gust history simply accumulates
    going forward from the permanent score row.

    Works one UTC day at a time to bound memory: for each day it loads the
    day's un-backfilled scores, the observations they point at, and the
    snapshots that could match on ``(icao, model, model_init_time)``.

    Which candidate to take is currently never in doubt, but only by
    coincidence. Snapshots are written on the synoptic buckets
    (``VERIFICATION_HOURS_UTC`` — 06/09/12/15/18Z, so a 180 min cadence) and
    ``_score_cycle`` pairs on a ±90 min window, which is exactly 180 min wide.
    One snapshot per ``(icao, model, model_init_time)`` therefore falls in the
    window — verified across the whole snapshot table: minimum gap between
    consecutive forecast hours is 180 min, and a real 12Z cycle yields exactly
    one candidate for all 8047 groups.

    That coincidence is load-bearing and unasserted. ``_score_cycle`` orders
    snapshots by ``(icao, model, model_init_time)`` with no ``forecast_hour``
    tiebreak and dedups on a key that omits it too, so the *first* row returned
    wins. Densify the snapshot cadence to hourly and several candidates land in
    the window, the scored one becomes whatever the DB returned first, and
    "nearest forecast hour" would be a guess that could attach a gust from a
    different valid time than the ``wind_speed_delta`` / ``ceiling_delta``
    already stored on that same row.

    So the snapshot is *identified* rather than guessed: ``wind_speed_delta_kt``
    is exactly ``snapshot.wind_speed_10m_kt - obs.wind_speed_kt``, so the
    candidate reproducing the stored delta is the one the score was written
    from. Today this is a no-op (one candidate, taken directly); it earns its
    keep only if the cadence changes. When identification is impossible (delta
    NULL, or candidates tie on wind) the row is left NULL rather than filled
    from a coin flip — see :func:`_pick_scored_snapshot`. Commits per day, and
    only ever touches rows whose ``model_wind_gust_kt`` is still NULL.
    """
    now = datetime.now(timezone.utc)
    start_day = (now - timedelta(days=days)).date()
    end_day = now.date()

    updated = 0
    day = start_day
    while day <= end_day:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        updated += _backfill_model_gust_day(db, day_start, day_end)
        db.commit()
        day += timedelta(days=1)

    logger.info(
        "Model gust backfill: updated %d scores over %d days", updated, days,
    )
    return updated


def _pick_scored_snapshot(
    candidates: list[tuple],
    *,
    obs_time: datetime,
    obs_wind_kt: float | None,
    stored_wind_delta_kt: float | None,
) -> tuple | None:
    """The snapshot a score row was written from, or None if not identifiable.

    ``candidates`` are ``(forecast_hour, wind_kt, gust_kt)`` for one
    ``(icao, model, model_init_time)`` key. Only those inside the ±90 min
    window ``_score_cycle`` pairs on are eligible.

    On the current 180 min snapshot cadence exactly one candidate is ever
    eligible, so this returns it directly and the rest is dormant. The
    multi-candidate path exists for a denser cadence, where ``_score_cycle``'s
    missing ``forecast_hour`` tiebreak would otherwise make the choice
    arbitrary.

    Identification, not proximity: the stored ``wind_speed_delta_kt`` is
    ``snapshot.wind_speed_10m_kt - obs.wind_speed_kt``, so the candidate that
    reproduces it is the one that was scored. Returning None (leaving the row
    NULL) is the correct outcome whenever that is ambiguous — a wrong gust is
    worse than a missing one for a feature whose entire purpose is measuring
    gust error.
    """
    eligible = [c for c in candidates if abs(c[0] - obs_time) <= _MATCH_WINDOW]
    if not eligible:
        return None
    if len(eligible) == 1:
        return eligible[0]

    # Several forecast hours fall in the window — the hourly-model case. Only
    # the stored wind delta can say which one _score_cycle actually scored.
    if stored_wind_delta_kt is None or obs_wind_kt is None:
        return None
    implied = float(stored_wind_delta_kt) + float(obs_wind_kt)
    matches = [
        c for c in eligible
        if c[1] is not None and abs(float(c[1]) - implied) < 0.01
    ]
    if len(matches) != 1:
        # Zero matches, or two candidates with the same wind but possibly
        # different gusts — either way the gust cannot be pinned down.
        return None
    return matches[0]


def _backfill_model_gust_day(
    db: Session, day_start: datetime, day_end: datetime,
) -> int:
    """Backfill one UTC day's model gust fields. Caller commits."""
    scores = db.execute(
        select(
            VerificationScoreRow.id,
            VerificationScoreRow.observation_id,
            VerificationScoreRow.icao,
            VerificationScoreRow.model,
            VerificationScoreRow.model_init_time,
            VerificationScoreRow.observation_time,
            VerificationScoreRow.wind_speed_delta_kt,
        ).where(
            VerificationScoreRow.observation_time >= day_start,
            VerificationScoreRow.observation_time < day_end,
            VerificationScoreRow.model_wind_gust_kt.is_(None),
        )
    ).all()
    if not scores:
        return 0

    # Snapshots are matched within the same ±90 min window _score_cycle uses.
    snap_rows = db.execute(
        select(
            AirportForecastSnapshotRow.icao,
            AirportForecastSnapshotRow.model,
            AirportForecastSnapshotRow.model_init_time,
            AirportForecastSnapshotRow.forecast_hour,
            AirportForecastSnapshotRow.wind_speed_10m_kt,
            AirportForecastSnapshotRow.wind_gusts_10m_kt,
        ).where(
            AirportForecastSnapshotRow.forecast_hour >= day_start - _MATCH_WINDOW,
            AirportForecastSnapshotRow.forecast_hour < day_end + _MATCH_WINDOW,
            AirportForecastSnapshotRow.wind_gusts_10m_kt.isnot(None),
        )
    ).all()
    if not snap_rows:
        return 0

    by_key: dict[tuple, list[tuple]] = {}
    for icao, model, init_time, fhour, wind, gust in snap_rows:
        key = (icao, model, init_time)
        by_key.setdefault(key, []).append((fhour, wind, gust))

    obs_ids = sorted({s.observation_id for s in scores})
    obs_gusts: dict[int, float | None] = {}
    obs_winds: dict[int, float | None] = {}
    for chunk_start in range(0, len(obs_ids), 5000):
        chunk = obs_ids[chunk_start : chunk_start + 5000]
        for oid, gust, wind_kt in db.execute(
            select(
                VerificationObservationRow.id,
                VerificationObservationRow.wind_gust_kt,
                VerificationObservationRow.wind_speed_kt,
            ).where(VerificationObservationRow.id.in_(chunk))
        ).all():
            obs_gusts[oid] = gust
            obs_winds[oid] = wind_kt

    mappings: list[dict] = []
    for s in scores:
        candidates = by_key.get((s.icao, s.model, s.model_init_time))
        if not candidates:
            continue
        picked = _pick_scored_snapshot(
            candidates,
            obs_time=s.observation_time,
            obs_wind_kt=obs_winds.get(s.observation_id),
            stored_wind_delta_kt=s.wind_speed_delta_kt,
        )
        if picked is None:
            continue
        _fhour, wind, gust = picked
        obs_gust = obs_gusts.get(s.observation_id)
        mappings.append({
            "id": s.id,
            "model_wind_gust_kt": gust,
            "wind_gust_delta_kt": (
                float(gust) - float(obs_gust) if obs_gust is not None else None
            ),
            "model_gust_flag": forecast_shows_gust(wind, gust),
        })

    if not mappings:
        return 0

    # ORM bulk UPDATE by primary key — one executemany, no per-row SELECT.
    db.execute(update(VerificationScoreRow), mappings)
    db.flush()
    return len(mappings)
