"""Tests for verification monthly rollup aggregation.

Since #522 the monthly rollup reads ``verification_daily_stats`` rather than
raw scores, so every integration test here rolls the relevant UTC days first —
that dependency is the point of the rewrite (a month is the sum of its days
and cannot disagree with them), so the tests exercise it rather than mock it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from weatherbrief.db.models import (
    TafVerificationDailyRow,
    TafVerificationScoreRow,
    VerificationDailyStatsRow,
    VerificationMonthlyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_daily_rollup import rollup_day
from weatherbrief.tasks.verification_rollup import (
    advisory_direction,
    category_direction,
    completed_months,
    prune_daily_stats,
    rollup_month,
    run_monthly_rollup,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _roll(db_session, *days: date) -> None:
    """Roll the given UTC days into the daily tables the monthly rollup reads."""
    for d in days:
        rollup_day(db_session, d)


# ---------------------------------------------------------------------------
# Unit tests for classification helpers
# ---------------------------------------------------------------------------


class TestCategoryDirection:
    def test_exact_match(self):
        assert category_direction("VFR", "VFR") == "match"
        assert category_direction("IFR", "IFR") == "match"

    def test_optimistic_1_step(self):
        # Forecast VFR, observed MVFR — forecast too good by 1
        assert category_direction("MVFR", "VFR") == "optimistic_1"

    def test_optimistic_2_steps(self):
        # Forecast VFR, observed IFR — forecast too good by 2
        assert category_direction("IFR", "VFR") == "optimistic_2"
        # Forecast VFR, observed LIFR — 3 steps still maps to optimistic_2
        assert category_direction("LIFR", "VFR") == "optimistic_2"

    def test_pessimistic_1_step(self):
        # Forecast IFR, observed MVFR — forecast too bad by 1
        assert category_direction("MVFR", "IFR") == "pessimistic_1"

    def test_pessimistic_2_steps(self):
        # Forecast LIFR, observed VFR — forecast too bad by 3
        assert category_direction("VFR", "LIFR") == "pessimistic_2"

    def test_none_inputs(self):
        assert category_direction(None, "VFR") == "skip"
        assert category_direction("VFR", None) == "skip"
        assert category_direction(None, None) == "skip"

    def test_case_insensitive(self):
        assert category_direction("vfr", "VFR") == "match"
        assert category_direction("Mvfr", "vfr") == "optimistic_1"

    def test_unknown_category(self):
        assert category_direction("VFR", "UNKNOWN") == "skip"


class TestAdvisoryDirection:
    def test_match(self):
        assert advisory_direction("green", "green") == "match"
        assert advisory_direction("red", "red") == "match"

    def test_optimistic(self):
        # Forecast green, observed amber
        assert advisory_direction("amber", "green") == "optimistic"
        assert advisory_direction("red", "green") == "optimistic"

    def test_pessimistic(self):
        # Forecast red, observed green
        assert advisory_direction("green", "red") == "pessimistic"

    def test_none(self):
        assert advisory_direction(None, "green") == "skip"


# ---------------------------------------------------------------------------
# Integration tests with DB
# ---------------------------------------------------------------------------


def _make_obs(db_session, icao: str, obs_time: datetime) -> VerificationObservationRow:
    """Insert a verification observation and return it."""
    obs = VerificationObservationRow(
        icao=icao,
        observation_time=obs_time,
        flight_category="MVFR",
        ceiling_ft=2000,
        visibility_m=6000,
        wind_speed_kt=15,
    )
    db_session.add(obs)
    db_session.flush()
    return obs


def _make_model_score(
    db_session,
    obs: VerificationObservationRow,
    model: str = "gfs",
    days_out: int = 1,
    source: str = "standalone",
    obs_cat: str = "MVFR",
    model_cat: str = "VFR",
    obs_adv: str | None = "amber",
    model_adv: str | None = "green",
    ceiling_delta: float | None = 500.0,
    visibility_delta: float | None = 1000.0,
    wind_delta: float | None = 3.0,
    temp_delta: float | None = 1.5,
    obs_precip: bool | None = True,
    model_precip: bool | None = False,
    obs_convection: bool | None = False,
    model_convection: bool | None = False,
    model_init_time: datetime | None = None,
) -> VerificationScoreRow:
    if model_init_time is None:
        model_init_time = obs.observation_time
    score = VerificationScoreRow(
        observation_id=obs.id,
        icao=obs.icao,
        observation_time=obs.observation_time,
        model=model,
        model_init_time=model_init_time,
        lead_hours=days_out * 24,
        days_out=days_out,
        source=source,
        obs_flight_category=obs_cat,
        model_flight_category=model_cat,
        category_match=(obs_cat == model_cat),
        ceiling_delta_ft=ceiling_delta,
        visibility_delta_m=visibility_delta,
        wind_speed_delta_kt=wind_delta,
        temperature_delta_c=temp_delta,
        obs_wind_advisory=obs_adv,
        model_wind_advisory=model_adv,
        advisory_match=(obs_adv == model_adv) if obs_adv and model_adv else None,
        obs_has_precipitation=obs_precip,
        model_has_precipitation=model_precip,
        obs_has_convection=obs_convection,
        model_has_convection=model_convection,
    )
    db_session.add(score)
    db_session.flush()
    return score


def _make_taf_score(
    db_session,
    obs: VerificationObservationRow,
    lead_hours: int = 6,
    source: str = "standalone",
    obs_cat: str = "MVFR",
    taf_cat: str = "VFR",
    obs_adv: str | None = "amber",
    taf_adv: str | None = "green",
    ceiling_delta: float | None = 300.0,
    visibility_delta: float | None = 500.0,
    wind_delta: float | None = 2.0,
    taf_issue_time: datetime | None = None,
) -> TafVerificationScoreRow:
    if taf_issue_time is None:
        taf_issue_time = obs.observation_time
    score = TafVerificationScoreRow(
        observation_id=obs.id,
        icao=obs.icao,
        observation_time=obs.observation_time,
        taf_issue_time=taf_issue_time,
        lead_hours=lead_hours,
        source=source,
        obs_flight_category=obs_cat,
        taf_flight_category=taf_cat,
        category_match=(obs_cat == taf_cat),
        ceiling_delta_ft=ceiling_delta,
        visibility_delta_m=visibility_delta,
        wind_speed_delta_kt=wind_delta,
        obs_wind_advisory=obs_adv,
        taf_wind_advisory=taf_adv,
        advisory_match=(obs_adv == taf_adv) if obs_adv and taf_adv else None,
    )
    db_session.add(score)
    db_session.flush()
    return score


MARCH_1 = _utc(2026, 3, 1)
MARCH_15 = _utc(2026, 3, 15, 12)
MARCH_16 = _utc(2026, 3, 16, 12)
MARCH_15_D = date(2026, 3, 15)
MARCH_16_D = date(2026, 3, 16)


class TestRollupMonth:
    """Test full rollup of model + TAF scores for a month."""

    def test_model_scores_rollup(self, db_session):
        obs1 = _make_obs(db_session, "LFPG", MARCH_15)
        obs2 = _make_obs(db_session, "LFPG", MARCH_16)

        # Two scores: both optimistic by 1 step (MVFR observed, VFR forecast)
        _make_model_score(db_session, obs1, model="gfs", days_out=1,
                          obs_cat="MVFR", model_cat="VFR",
                          ceiling_delta=500.0, temp_delta=2.0)
        # One pessimistic by 1 step (MVFR observed, IFR forecast)
        _make_model_score(db_session, obs2, model="gfs", days_out=1,
                          obs_cat="MVFR", model_cat="IFR",
                          ceiling_delta=-300.0, temp_delta=-1.0)

        _roll(db_session, MARCH_15_D, MARCH_16_D)
        n = rollup_month(db_session, MARCH_1)
        assert n == 1  # one group: (standalone, gfs, 1, LFPG)

        row = db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalar_one()

        assert row.icao == "LFPG"
        assert row.model == "gfs"
        assert row.days_out == 1
        assert row.n_scores == 2
        assert row.n_cat_optimistic_1 == 1
        assert row.n_cat_pessimistic_1 == 1
        assert row.n_cat_match == 0
        # MAE: avg(|500|, |-300|) = 400
        assert row.ceiling_mae_ft == pytest.approx(400.0)
        # Bias: avg(500, -300) = 100
        assert row.ceiling_bias_ft == pytest.approx(100.0)
        # Temperature MAE: avg(|2|, |-1|) = 1.5
        assert row.temperature_mae_c == pytest.approx(1.5)

    def test_taf_is_out_of_monthly_scope(self, db_session):
        """TAF lands in taf_verification_daily, never in the monthly table (#522).

        The monthly table is keyed on (model, days_out); TAF carries
        lead_hours, which is what kept it out of the daily NWP rollup in the
        first place. It now has its own daily table and is served from there
        at query time.
        """
        obs = _make_obs(db_session, "EGLL", MARCH_15)

        # TAF optimistic by 1 (MVFR obs, VFR forecast), lead 6h -> days_out=0
        _make_taf_score(db_session, obs, lead_hours=6,
                        obs_cat="MVFR", taf_cat="VFR",
                        ceiling_delta=200.0)

        _roll(db_session, MARCH_15_D)
        assert rollup_month(db_session, MARCH_1) == 0
        assert db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalars().all() == []

        taf_row = db_session.execute(
            select(TafVerificationDailyRow)
        ).scalars().one()
        assert taf_row.days_out == 0
        assert taf_row.n == 1
        assert taf_row.n_cat_opt_1 == 1
        assert taf_row.sum_abs_ceiling_delta_ft == pytest.approx(200.0)

    def test_multiple_airports_and_models(self, db_session):
        obs_pg = _make_obs(db_session, "LFPG", MARCH_15)
        obs_ll = _make_obs(db_session, "EGLL", MARCH_15)

        _make_model_score(db_session, obs_pg, model="gfs", days_out=0)
        _make_model_score(db_session, obs_pg, model="icon", days_out=0,
                          model_init_time=_utc(2026, 3, 15, 6))
        _make_model_score(db_session, obs_ll, model="gfs", days_out=0)

        _roll(db_session, MARCH_15_D)
        n = rollup_month(db_session, MARCH_1)
        # 3 groups: (standalone,gfs,0,LFPG), (standalone,icon,0,LFPG), (standalone,gfs,0,EGLL)
        assert n == 3

    def test_idempotent_rerun(self, db_session):
        obs = _make_obs(db_session, "LFPG", MARCH_15)
        _make_model_score(db_session, obs, model="gfs", days_out=1)

        _roll(db_session, MARCH_15_D)
        rollup_month(db_session, MARCH_1)
        count_1 = db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalars().all()

        # Re-run should replace, not duplicate
        rollup_month(db_session, MARCH_1)
        count_2 = db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalars().all()

        assert len(count_1) == len(count_2) == 1

    def test_precipitation_counts(self, db_session):
        obs = _make_obs(db_session, "LFPG", MARCH_15)

        # Hit: both observed and forecast precip
        _make_model_score(db_session, obs, model="gfs", days_out=0,
                          obs_precip=True, model_precip=True,
                          model_init_time=_utc(2026, 3, 15, 0))
        # Miss: observed precip, model missed it
        _make_model_score(db_session, obs, model="gfs", days_out=0,
                          obs_precip=True, model_precip=False,
                          model_init_time=_utc(2026, 3, 15, 6))
        # False alarm: no observed precip, model said yes
        _make_model_score(db_session, obs, model="gfs", days_out=0,
                          obs_precip=False, model_precip=True,
                          model_init_time=_utc(2026, 3, 15, 12))

        _roll(db_session, MARCH_15_D)
        rollup_month(db_session, MARCH_1)
        row = db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalar_one()

        assert row.n_precip_hit == 1
        assert row.n_precip_miss == 1
        assert row.n_precip_false_alarm == 1


class TestCompletedMonths:
    def test_finds_incomplete_months(self, db_session):
        # Insert a score in March 2026 — it's now April 2026, so March is complete
        obs = _make_obs(db_session, "LFPG", MARCH_15)
        _make_model_score(db_session, obs, model="gfs", days_out=1)

        _roll(db_session, MARCH_15_D)
        months = completed_months(db_session)
        assert MARCH_1 in months

    def test_skips_already_rolled_up(self, db_session):
        obs = _make_obs(db_session, "LFPG", MARCH_15)
        _make_model_score(db_session, obs, model="gfs", days_out=1)

        _roll(db_session, MARCH_15_D)
        # Roll up March
        rollup_month(db_session, MARCH_1)

        months = completed_months(db_session)
        assert MARCH_1 not in months


class TestRunMonthlyRollup:
    def test_end_to_end(self, db_session):
        obs = _make_obs(db_session, "LFPG", MARCH_15)
        _make_model_score(db_session, obs, model="gfs", days_out=1)
        _make_taf_score(db_session, obs, lead_hours=12)

        _roll(db_session, MARCH_15_D)
        total = run_monthly_rollup(db_session)
        assert total == 1  # 1 model group; TAF is served from its own daily table

        rows = db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalars().all()
        models = {r.model for r in rows}
        assert models == {"gfs"}


class TestPruneDailyStats:
    """Phase 4 follow-up — off unless explicitly configured."""

    def test_disabled_by_default(self, db_session, monkeypatch):
        monkeypatch.delenv(
            "VERIFICATION_DAILY_STATS_RETENTION_MONTHS", raising=False,
        )
        obs = _make_obs(db_session, "LFPG", MARCH_15)
        _make_model_score(db_session, obs, model="gfs", days_out=1)
        _roll(db_session, MARCH_15_D)

        assert prune_daily_stats(db_session) == 0
        assert db_session.execute(
            select(VerificationDailyStatsRow)
        ).scalars().all() != []

    def test_prunes_rows_older_than_window(self, db_session):
        obs = _make_obs(db_session, "LFPG", MARCH_15)
        _make_model_score(db_session, obs, model="gfs", days_out=1)
        _roll(db_session, MARCH_15_D)

        # March 2026 is comfortably more than 1 month before "now" in the
        # frozen test clock's future — retain_months is passed explicitly so
        # the test doesn't depend on the env gate.
        deleted = prune_daily_stats(db_session, retain_months=1)
        assert deleted >= 1
        assert db_session.execute(
            select(VerificationDailyStatsRow)
        ).scalars().all() == []
