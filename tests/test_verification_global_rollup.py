"""Tests for the #522 Phase 1 rollups and the dashboard read switch.

Three new daily tables and one gate. The invariant worth protecting is that
flipping ``VERIFICATION_GLOBAL_ROLLUP_READS`` must not change a single
reported number — the global table is the per-airport table with ``icao``
summed away, so gate-on and gate-off answers have to agree exactly.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from weatherbrief.db.models import (
    TafVerificationDailyRow,
    TafVerificationScoreRow,
    VerificationActivityDailyRow,
    VerificationDailyStatsRow,
    VerificationGlobalDailyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_daily_rollup import (
    completed_days,
    rollup_day,
    taf_days_out_expr,
)
from weatherbrief.tasks.verification_stats import (
    get_activity_summary,
    get_category_accuracy,
    get_category_bias_stats,
    get_gust_accuracy,
    get_wind_advisory_accuracy,
)


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


DAY = date(2026, 5, 12)
NOON = _utc(2026, 5, 12, 12, 0)
SINCE = _utc(2026, 5, 12, 0, 0)
UNTIL = _utc(2026, 5, 12, 23, 59)


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("VERIFICATION_GLOBAL_ROLLUP_READS", "1")


def _obs(db, icao: str, when: datetime, *, wind=12, gust=None, taf_gust=None):
    row = VerificationObservationRow(
        icao=icao, observation_time=when, collected_at=when,
        flight_category="MVFR", wind_speed_kt=wind, wind_gust_kt=gust,
        taf_issue_time=when - timedelta(hours=3),
        taf_flight_category="VFR", taf_wind_gust_kt=taf_gust,
    )
    db.add(row)
    db.flush()
    return row


def _score(
    db, obs, *, model="gfs", days_out=0, source="standalone",
    obs_cat="MVFR", model_cat="VFR", obs_adv="amber", model_adv="green",
    ceiling_delta=400.0, wind_delta=3.0, temp_delta=1.0, vis_delta=500.0,
    model_gust=None, gust_delta=None, gust_flag=None, init_offset_h=6,
):
    db.add(VerificationScoreRow(
        observation_id=obs.id, icao=obs.icao,
        observation_time=obs.observation_time, model=model,
        model_init_time=obs.observation_time - timedelta(hours=init_offset_h),
        lead_hours=init_offset_h, days_out=days_out, source=source,
        obs_flight_category=obs_cat, model_flight_category=model_cat,
        category_match=(obs_cat == model_cat),
        ceiling_delta_ft=ceiling_delta, wind_speed_delta_kt=wind_delta,
        temperature_delta_c=temp_delta, visibility_delta_m=vis_delta,
        obs_wind_advisory=obs_adv, model_wind_advisory=model_adv,
        advisory_match=(obs_adv == model_adv),
        model_wind_gust_kt=model_gust, wind_gust_delta_kt=gust_delta,
        model_gust_flag=gust_flag,
        obs_has_precipitation=True, model_has_precipitation=True,
        obs_has_convection=False, model_has_convection=False,
    ))
    db.flush()


def _taf_score(
    db, obs, *, lead_hours=6, source="standalone",
    obs_cat="MVFR", taf_cat="VFR", obs_adv="amber", taf_adv="green",
    ceiling_delta=200.0, wind_delta=2.0, vis_delta=300.0, gust_delta=None,
):
    db.add(TafVerificationScoreRow(
        observation_id=obs.id, icao=obs.icao,
        observation_time=obs.observation_time,
        taf_issue_time=obs.observation_time - timedelta(hours=lead_hours),
        lead_hours=lead_hours, source=source,
        obs_flight_category=obs_cat, taf_flight_category=taf_cat,
        category_match=(obs_cat == taf_cat),
        ceiling_delta_ft=ceiling_delta, wind_speed_delta_kt=wind_delta,
        visibility_delta_m=vis_delta, wind_gust_delta_kt=gust_delta,
        obs_wind_advisory=obs_adv, taf_wind_advisory=taf_adv,
        advisory_match=(obs_adv == taf_adv),
    ))
    db.flush()


class TestGlobalRollup:

    def test_collapses_icao_and_sums_exactly(self, db_session):
        for i, icao in enumerate(("LFPG", "EDDF", "EGLL")):
            obs = _obs(db_session, icao, NOON + timedelta(minutes=i))
            _score(db_session, obs, ceiling_delta=100.0 * (i + 1))

        rollup_day(db_session, DAY)

        per_airport = db_session.execute(
            select(VerificationDailyStatsRow)
        ).scalars().all()
        assert len(per_airport) == 3

        row = db_session.execute(
            select(VerificationGlobalDailyStatsRow)
        ).scalars().one()
        assert row.model == "gfs"
        assert row.days_out == 0
        assert row.n == 3
        assert row.n_ceiling == 3
        # 100 + 200 + 300
        assert row.sum_abs_ceiling_delta_ft == pytest.approx(600.0)
        assert row.n_cat_opt_1 == 3
        assert row.n_advisory_opt == 3
        assert row.n_precip_hit == 3

    def test_splits_by_model_days_out_and_source(self, db_session):
        obs = _obs(db_session, "LFPG", NOON)
        _score(db_session, obs, model="gfs", days_out=0)
        _score(db_session, obs, model="ecmwf", days_out=0, init_offset_h=7)
        _score(db_session, obs, model="gfs", days_out=1, init_offset_h=30)
        _score(db_session, obs, model="gfs", days_out=0, source="flight",
               init_offset_h=8)

        rollup_day(db_session, DAY)

        keys = {
            (r.source, r.model, r.days_out)
            for r in db_session.execute(
                select(VerificationGlobalDailyStatsRow)
            ).scalars().all()
        }
        assert keys == {
            ("standalone", "gfs", 0),
            ("standalone", "ecmwf", 0),
            ("standalone", "gfs", 1),
            ("flight", "gfs", 0),
        }

    def test_rerun_is_idempotent(self, db_session):
        obs = _obs(db_session, "LFPG", NOON)
        _score(db_session, obs)

        rollup_day(db_session, DAY)
        rollup_day(db_session, DAY)

        assert len(db_session.execute(
            select(VerificationGlobalDailyStatsRow)
        ).scalars().all()) == 1

    def test_gust_columns_carry_both_conditionings(self, db_session):
        # Hour 1: forecast flags a gust, airport isn't gusting.
        o1 = _obs(db_session, "LFPG", NOON, wind=12, gust=None)
        _score(db_session, o1, model_gust=25.0, gust_flag=True)
        # Hour 2: forecast flags a gust and the airport gusts.
        o2 = _obs(db_session, "LFPG", NOON + timedelta(hours=1), wind=20, gust=30)
        _score(db_session, o2, model_gust=26.0, gust_delta=-4.0, gust_flag=True,
               init_offset_h=7)

        rollup_day(db_session, DAY)
        row = db_session.execute(
            select(VerificationGlobalDailyStatsRow)
        ).scalars().one()

        assert row.n_gust == 1                     # obs-flagged sample
        assert row.sum_gust_delta_kt == pytest.approx(-4.0)
        assert row.n_model_gust_flag == 2
        assert row.n_obs_gust == 1
        assert row.n_gust_flag_hit == 1
        assert row.n_gust_flagged_peak == 2
        # (25 - 12) + (26 - 30) = 9
        assert row.sum_gust_flagged_over_peak_kt == pytest.approx(9.0)


class TestActivityRollup:

    def test_counts_distinct_observations_not_scores(self, db_session):
        obs = _obs(db_session, "LFPG", NOON)
        _score(db_session, obs, model="gfs")
        _score(db_session, obs, model="ecmwf", init_offset_h=7)
        _score(db_session, obs, model="icon", init_offset_h=8)
        other = _obs(db_session, "EDDF", NOON + timedelta(minutes=5))
        _score(db_session, other, model="gfs")

        rollup_day(db_session, DAY)
        row = db_session.execute(
            select(VerificationActivityDailyRow)
        ).scalars().one()

        assert row.n_scores == 4
        assert row.n_distinct_obs == 2
        assert row.n_airports_day == 2

    def test_one_row_per_source(self, db_session):
        obs = _obs(db_session, "LFPG", NOON)
        _score(db_session, obs, source="standalone")
        _score(db_session, obs, source="flight", init_offset_h=7)

        rollup_day(db_session, DAY)
        rows = {
            r.source: r
            for r in db_session.execute(
                select(VerificationActivityDailyRow)
            ).scalars().all()
        }
        assert set(rows) == {"standalone", "flight"}
        assert rows["standalone"].n_distinct_obs == 1
        assert rows["flight"].n_distinct_obs == 1


class TestTafDailyRollup:

    def test_buckets_lead_hours_into_days_out(self, db_session):
        for i, lead in enumerate((0, 6, 23, 24, 30, 47, 48)):
            obs = _obs(db_session, "LFPG", NOON + timedelta(minutes=i))
            _taf_score(db_session, obs, lead_hours=lead)

        rollup_day(db_session, DAY)
        rows = {
            r.days_out: r.n
            for r in db_session.execute(
                select(TafVerificationDailyRow)
            ).scalars().all()
        }
        assert rows == {0: 3, 1: 3, 2: 1}

    def test_negative_lead_hours_clamp_to_zero(self, db_session):
        """Matches verification_rollup._lead_hours_to_days_out's max(0, ...)."""
        obs = _obs(db_session, "LFPG", NOON)
        _taf_score(db_session, obs, lead_hours=-1)

        rollup_day(db_session, DAY)
        row = db_session.execute(
            select(TafVerificationDailyRow)
        ).scalars().one()
        assert row.days_out == 0

    def test_days_out_expression_matches_python_floor_div(self, db_session):
        """The SQL bucket must equal ``max(0, lead // 24)`` for every lead.

        The whole point of ``taf_days_out_expr`` is that it truncates the same
        way on SQLite and MySQL; this pins it to the Python reference so a
        future "simplification" to ``CAST(x/24 AS INTEGER)`` — which rounds
        half away from zero on MySQL — fails here rather than in production.
        """
        for i, lead in enumerate(range(0, 96)):
            obs = _obs(db_session, "LFPG", NOON + timedelta(minutes=i))
            _taf_score(db_session, obs, lead_hours=lead)

        pairs = db_session.execute(
            select(
                TafVerificationScoreRow.lead_hours,
                taf_days_out_expr(TafVerificationScoreRow.lead_hours),
            )
        ).all()
        for lead, bucket in pairs:
            assert int(bucket) == max(0, lead // 24), lead

    def test_carries_categories_advisories_and_gust(self, db_session):
        o1 = _obs(db_session, "LFPG", NOON, wind=12, gust=None, taf_gust=28)
        _taf_score(db_session, o1, ceiling_delta=200.0)
        o2 = _obs(db_session, "EDDF", NOON, wind=20, gust=30, taf_gust=32)
        _taf_score(db_session, o2, ceiling_delta=-100.0, gust_delta=2.0)

        rollup_day(db_session, DAY)
        row = db_session.execute(
            select(TafVerificationDailyRow)
        ).scalars().one()

        assert row.n == 2
        assert row.n_cat_opt_1 == 2           # MVFR observed, VFR forecast
        assert row.n_advisory_opt == 2        # amber observed, green forecast
        assert row.sum_abs_ceiling_delta_ft == pytest.approx(300.0)
        assert row.sum_ceiling_delta_ft == pytest.approx(100.0)
        assert row.n_gust == 1
        assert row.sum_gust_delta_kt == pytest.approx(2.0)
        assert row.n_taf_gust_flag == 2
        assert row.n_obs_gust == 1
        assert row.n_gust_flag_hit == 1
        # (28 - 12) + (32 - 30) = 18
        assert row.sum_gust_flagged_over_peak_kt == pytest.approx(18.0)


class TestCompletedDays:

    def test_source_matching_is_equality_not_prefix(self, db_session):
        """'standalone' is the only value production writes (#522)."""
        obs = _obs(db_session, "LFPG", NOON)
        _score(db_session, obs, source="standalone")

        assert DAY in completed_days(db_session)

    def test_ignores_flight_only_days(self, db_session):
        obs = _obs(db_session, "LFPG", NOON)
        _score(db_session, obs, source="flight")

        assert completed_days(db_session) == []

    def test_start_point_comes_from_the_rollup_once_rolled(self, db_session):
        """Pruning raw history must not change which days are pending."""
        early = _obs(db_session, "LFPG", _utc(2026, 5, 10, 12, 0))
        _score(db_session, early)
        late = _obs(db_session, "LFPG", NOON)
        _score(db_session, late)

        rollup_day(db_session, date(2026, 5, 10))
        # Delete the early raw score, as Phase 3 pruning eventually would.
        db_session.execute(
            VerificationScoreRow.__table__.delete().where(
                VerificationScoreRow.observation_time < _utc(2026, 5, 11)
            )
        )
        db_session.flush()

        pending = completed_days(db_session)
        # Walk still starts at the first rolled day, so the gap on the 11th is
        # still visited rather than skipped by a moved raw MIN.
        assert date(2026, 5, 11) in pending
        assert DAY in pending
        assert date(2026, 5, 10) not in pending  # already rolled


class TestReadSwitchAgreement:
    """Gate on and gate off must report identical numbers."""

    @pytest.fixture
    def populated(self, db_session):
        for i, icao in enumerate(("LFPG", "EDDF", "EGLL")):
            obs = _obs(
                db_session, icao, NOON + timedelta(minutes=i),
                wind=12 + i, gust=30 if i == 0 else None,
                taf_gust=28 if i < 2 else None,
            )
            _score(
                db_session, obs, ceiling_delta=100.0 * (i + 1),
                model_gust=25.0, gust_delta=-5.0 if i == 0 else None,
                gust_flag=True,
            )
            _score(db_session, obs, model="ecmwf", init_offset_h=7,
                   model_cat="MVFR", model_adv="amber")
            _taf_score(db_session, obs, gust_delta=-2.0 if i == 0 else None)
        rollup_day(db_session, DAY)
        return db_session

    def _all_stats(self, db):
        return (
            {(r.model, r.days_out): (r.accuracy_pct, r.sample_count)
             for r in get_category_accuracy(db, SINCE, UNTIL, "standalone")},
            {(r.model, r.days_out): (r.total_scores, r.optimistic_1)
             for r in get_category_bias_stats(db, SINCE, UNTIL, "standalone")},
            {r.model: (r.accuracy_pct, r.sample_count)
             for r in get_wind_advisory_accuracy(db, SINCE, UNTIL, "standalone")},
            {(r.model, r.days_out): (r.n, r.n_gust, r.n_flagged, r.n_obs_gust,
                                     r.gust_bias_kt, r.flagged_over_peak_kt)
             for r in get_gust_accuracy(db, SINCE, UNTIL, "standalone")},
        )

    def test_aggregates_match_across_the_gate(self, populated, monkeypatch):
        monkeypatch.setenv("VERIFICATION_GLOBAL_ROLLUP_READS", "0")
        before = self._all_stats(populated)
        monkeypatch.setenv("VERIFICATION_GLOBAL_ROLLUP_READS", "1")
        after = self._all_stats(populated)
        assert before == after

    def test_activity_matches_across_the_gate(self, populated, monkeypatch):
        monkeypatch.setenv("VERIFICATION_GLOBAL_ROLLUP_READS", "0")
        raw = get_activity_summary(populated, SINCE, UNTIL, "standalone")
        monkeypatch.setenv("VERIFICATION_GLOBAL_ROLLUP_READS", "1")
        rolled = get_activity_summary(populated, SINCE, UNTIL, "standalone")

        assert raw.observations_collected == rolled.observations_collected == 3
        assert raw.airports_observed == rolled.airports_observed == 3

    def test_gate_on_still_reads_per_airport_when_filtered(
        self, populated, gate_on,
    ):
        """The global table has no icao column, so a filter must not use it.

        Every gated query is covered, not a sample: they all share
        ``_use_global``, so one of them silently reading the global table
        under a filter would report pan-European numbers on an
        airport-filtered dashboard — right-looking, completely wrong.
        """
        rows = get_category_accuracy(
            populated, SINCE, UNTIL, "standalone", ["LFPG"],
        )
        gfs = next(r for r in rows if r.model == "gfs")
        assert gfs.sample_count == 1  # one airport, not three

        bias = get_category_bias_stats(
            populated, SINCE, UNTIL, "standalone", ["LFPG"],
        )
        assert {r.total_scores for r in bias} == {1}

        wind = get_wind_advisory_accuracy(
            populated, SINCE, UNTIL, "standalone", ["LFPG"],
        )
        assert {r.sample_count for r in wind if r.model != "TAF"} == {1}

        gust = get_gust_accuracy(
            populated, SINCE, UNTIL, "standalone", ["LFPG"],
        )
        assert {r.n for r in gust if r.model == "gfs"} == {1}

        activity = get_activity_summary(
            populated, SINCE, UNTIL, "standalone", ["LFPG"],
        )
        assert activity.airports_observed == 1
        assert activity.observations_collected == 1

    def test_taf_stats_also_narrow_under_a_filter(self, populated, gate_on):
        """TAF has its own gated path (taf_verification_daily), same rule."""
        all_taf = next(
            r for r in get_category_accuracy(populated, SINCE, UNTIL, "standalone")
            if r.model == "TAF"
        )
        one_taf = next(
            r for r in get_category_accuracy(
                populated, SINCE, UNTIL, "standalone", ["LFPG"],
            )
            if r.model == "TAF"
        )
        assert all_taf.sample_count == 3
        assert one_taf.sample_count == 1


class TestTafDaysOutOnMySQL:
    """The bucketing expression has to truncate the same way on both dialects.

    The suite runs on SQLite, so MySQL correctness can only be checked by
    compiling. What is asserted is the *shape* the docstring's argument rests
    on — that the numerator is reduced by a modulo before dividing — because
    the failure mode is a future "simplification" to ``CAST(x/24 AS INTEGER)``,
    which MySQL rounds half away from zero (60 h → 3, not 2).
    """

    def _compiled(self, dialect):
        from sqlalchemy import select as sa_select

        stmt = sa_select(
            taf_days_out_expr(TafVerificationScoreRow.lead_hours)
        ).select_from(TafVerificationScoreRow)
        return str(stmt.compile(dialect=dialect))

    def test_compiles_on_mysql(self):
        from sqlalchemy.dialects import mysql

        sql = self._compiled(mysql.dialect())
        # The modulo reduction is the load-bearing part: it makes the
        # numerator an exact multiple of 24, so the CAST has nothing to round.
        assert "lead_hours %%" in sql
        # MySQL renders an integer CAST as SIGNED INTEGER, not INTEGER — a
        # bare "AS INTEGER" would be a syntax error there.
        assert "AS SIGNED INTEGER" in sql

    def test_compiles_on_sqlite(self):
        from sqlalchemy.dialects import sqlite

        sql = self._compiled(sqlite.dialect())
        assert "lead_hours %" in sql
        assert "AS INTEGER" in sql
