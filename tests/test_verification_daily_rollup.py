"""Tests for verification_daily_stats rollup (issue #154)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select

from weatherbrief.db.models import (
    VerificationDailyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_daily_rollup import (
    completed_days,
    rebuild_all_days,
    rollup_all_complete_days,
    rollup_day,
)


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def _obs(icao: str, when: datetime) -> VerificationObservationRow:
    return VerificationObservationRow(
        icao=icao, observation_time=when, collected_at=when,
    )


def _score(
    obs_id: int,
    icao: str,
    when: datetime,
    *,
    model: str = "gfs",
    days_out: int = 1,
    source: str = "standalone_full",
    init_time: datetime | None = None,
    obs_cat: str | None = "VFR",
    mod_cat: str | None = "VFR",
    category_match: bool | None = None,
    ceiling_delta: float | None = None,
    wind_delta: float | None = None,
    temp_delta: float | None = None,
    vis_delta: float | None = None,
    obs_adv: str | None = None,
    mod_adv: str | None = None,
    advisory_match: bool | None = None,
    obs_precip: bool | None = None,
    mod_precip: bool | None = None,
    obs_conv: bool | None = None,
    mod_conv: bool | None = None,
) -> VerificationScoreRow:
    if init_time is None:
        init_time = when
    # Auto-fill category_match if both cats supplied and value not given.
    if category_match is None and obs_cat is not None and mod_cat is not None:
        category_match = obs_cat == mod_cat
    if advisory_match is None and obs_adv is not None and mod_adv is not None:
        advisory_match = obs_adv == mod_adv
    return VerificationScoreRow(
        observation_id=obs_id,
        icao=icao,
        observation_time=when,
        model=model,
        model_init_time=init_time,
        lead_hours=24 * days_out,
        days_out=days_out,
        source=source,
        obs_flight_category=obs_cat,
        model_flight_category=mod_cat,
        category_match=category_match,
        ceiling_delta_ft=ceiling_delta,
        wind_speed_delta_kt=wind_delta,
        temperature_delta_c=temp_delta,
        visibility_delta_m=vis_delta,
        obs_wind_advisory=obs_adv,
        model_wind_advisory=mod_adv,
        advisory_match=advisory_match,
        obs_has_precipitation=obs_precip,
        model_has_precipitation=mod_precip,
        obs_has_convection=obs_conv,
        model_has_convection=mod_conv,
    )


def _make_obs(db, icao, when) -> int:
    o = _obs(icao, when)
    db.add(o)
    db.flush()
    return o.id


class TestRollupDayBasics:
    def test_groups_by_source_model_days_out_icao(self, db_session):
        oid_a = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        oid_b = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 18, 0))
        oid_c = _make_obs(db_session, "EDDF", _utc(2026, 4, 5, 12, 0))
        # Same group: 2 GFS d-1 scores at LFPG (different obs_times)
        db_session.add(_score(oid_a, "LFPG", _utc(2026, 4, 5, 12, 0)))
        db_session.add(_score(oid_b, "LFPG", _utc(2026, 4, 5, 18, 0)))
        # Different model at same obs_time (same init_time is fine — model differs)
        db_session.add(_score(oid_a, "LFPG", _utc(2026, 4, 5, 12, 0), model="ecmwf"))
        # Different days_out at same obs_time (uses different init_time)
        db_session.add(_score(
            oid_a, "LFPG", _utc(2026, 4, 5, 12, 0),
            days_out=2, init_time=_utc(2026, 4, 3, 12, 0),
        ))
        # Different icao
        db_session.add(_score(oid_c, "EDDF", _utc(2026, 4, 5, 12, 0)))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        rows = db_session.execute(select(VerificationDailyStatsRow)).scalars().all()
        assert len(rows) == 4

        # The (gfs, d-1, LFPG) group has 2 scores
        gfs = [
            r for r in rows
            if r.model == "gfs" and r.days_out == 1 and r.icao == "LFPG"
        ]
        assert len(gfs) == 1
        assert gfs[0].n == 2

    def test_only_obs_in_target_date(self, db_session):
        oid_pre = _make_obs(db_session, "LFPG", _utc(2026, 4, 4, 23, 30))
        oid_in = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        oid_post = _make_obs(db_session, "LFPG", _utc(2026, 4, 6, 0, 30))
        db_session.add(_score(oid_pre, "LFPG", _utc(2026, 4, 4, 23, 30)))
        db_session.add(_score(oid_in, "LFPG", _utc(2026, 4, 5, 12, 0)))
        db_session.add(_score(oid_post, "LFPG", _utc(2026, 4, 6, 0, 30)))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        rows = db_session.execute(select(VerificationDailyStatsRow)).scalars().all()
        assert len(rows) == 1
        assert rows[0].n == 1

    def test_idempotent(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        db_session.add(_score(oid, "LFPG", _utc(2026, 4, 5, 12, 0)))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        rollup_day(db_session, date(2026, 4, 5))
        rows = db_session.execute(select(VerificationDailyStatsRow)).scalars().all()
        assert len(rows) == 1
        assert rows[0].n == 1


class TestCategoryDirectionEncoding:
    """Verify the SQL CASE WHEN logic matches category_direction() exactly."""

    def test_all_directions_split_into_buckets(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        when = _utc(2026, 4, 5, 12, 0)
        # Each row uses a distinct model_init_time so the UNIQUE constraint
        # doesn't reject the duplicate (icao, obs_time, model, init, source).
        cases = [
            # (obs_cat, mod_cat, expected_bucket)
            ("VFR", "VFR", "match"),
            ("MVFR", "MVFR", "match"),
            ("IFR", "VFR", "opt_2"),         # diff = 0 - 2 = -2
            ("IFR", "MVFR", "opt_1"),        # diff = 1 - 2 = -1
            ("VFR", "MVFR", "pess_1"),       # diff = 1 - 0 = 1
            ("VFR", "IFR", "pess_2"),        # diff = 2 - 0 = 2
            ("LIFR", "VFR", "opt_2"),        # diff = -3
            ("VFR", "LIFR", "pess_2"),       # diff = +3
        ]
        for i, (obs_cat, mod_cat, _bucket) in enumerate(cases):
            db_session.add(_score(
                oid, "LFPG", when,
                init_time=_utc(2026, 4, 5, i, 0),
                obs_cat=obs_cat, mod_cat=mod_cat,
            ))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalar_one()

        expected = {"match": 0, "opt_1": 0, "opt_2": 0, "pess_1": 0, "pess_2": 0}
        for _obs_cat, _mod_cat, bucket in cases:
            expected[bucket] += 1

        assert row.n_cat_match == expected["match"]
        assert row.n_cat_opt_1 == expected["opt_1"]
        assert row.n_cat_opt_2 == expected["opt_2"]
        assert row.n_cat_pess_1 == expected["pess_1"]
        assert row.n_cat_pess_2 == expected["pess_2"]
        # All 8 rows should be in `n`, but only rows with both cats valid
        # land in a category bucket. All 8 have valid cats → all 8 counted.
        assert row.n == 8
        assert (
            row.n_cat_match
            + row.n_cat_opt_1 + row.n_cat_opt_2
            + row.n_cat_pess_1 + row.n_cat_pess_2
            == 8
        )

    def test_null_category_excluded_from_buckets(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        when = _utc(2026, 4, 5, 12, 0)
        db_session.add(_score(
            oid, "LFPG", when, init_time=_utc(2026, 4, 5, 0, 0),
            obs_cat="VFR", mod_cat="VFR",
        ))
        db_session.add(_score(
            oid, "LFPG", when, init_time=_utc(2026, 4, 5, 1, 0),
            obs_cat=None, mod_cat="VFR",
        ))
        db_session.add(_score(
            oid, "LFPG", when, init_time=_utc(2026, 4, 5, 2, 0),
            obs_cat="VFR", mod_cat=None,
        ))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalar_one()
        # n counts all rows; only the row with both cats lands in a bucket.
        assert row.n == 3
        assert row.n_cat_match == 1
        assert (
            row.n_cat_opt_1 + row.n_cat_opt_2
            + row.n_cat_pess_1 + row.n_cat_pess_2 == 0
        )


class TestAdvisoryDirectionEncoding:
    def test_match_opt_pess(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        when = _utc(2026, 4, 5, 12, 0)
        cases = [
            ("green", "green", "match"),
            ("amber", "green", "opt"),       # mod=0 - obs=1 = -1
            ("red", "amber", "opt"),         # -1
            ("green", "amber", "pess"),      # +1
            ("amber", "red", "pess"),        # +1
            ("red", "green", "opt"),         # -2
        ]
        for i, (obs_adv, mod_adv, _b) in enumerate(cases):
            db_session.add(_score(
                oid, "LFPG", when,
                init_time=_utc(2026, 4, 5, i, 0),
                obs_adv=obs_adv, mod_adv=mod_adv,
            ))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalar_one()
        expected = {"match": 0, "opt": 0, "pess": 0}
        for _o, _m, b in cases:
            expected[b] += 1
        assert row.n_advisory_match == expected["match"]
        assert row.n_advisory_opt == expected["opt"]
        assert row.n_advisory_pess == expected["pess"]


class TestDeltaSums:
    def test_signed_and_abs_sums(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        deltas = [200.0, -300.0, 100.0, None, -50.0]
        for i, d in enumerate(deltas):
            db_session.add(_score(
                oid, "LFPG", _utc(2026, 4, 5, 12, 0),
                init_time=_utc(2026, 4, 5, i, 0),
                ceiling_delta=d,
            ))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalar_one()
        non_null = [d for d in deltas if d is not None]
        assert row.n_ceiling == len(non_null)
        assert row.sum_ceiling_delta_ft == sum(non_null)
        assert row.sum_abs_ceiling_delta_ft == sum(abs(d) for d in non_null)
        # n includes the NULL-delta row
        assert row.n == len(deltas)


class TestContingencyCounts:
    def test_precip_hit_miss_false_alarm(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 4, 5, 12, 0))
        when = _utc(2026, 4, 5, 12, 0)
        cases = [
            (True, True),    # hit
            (True, True),    # hit
            (True, False),   # miss
            (False, True),   # false_alarm
            (False, False),  # correct negative (none of the buckets)
            (None, True),    # NULL on either side → excluded entirely
            (True, None),
        ]
        for i, (op, mp) in enumerate(cases):
            db_session.add(_score(
                oid, "LFPG", when,
                init_time=_utc(2026, 4, 5, i, 0),
                obs_precip=op, mod_precip=mp,
            ))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalar_one()
        assert row.n_precip_hit == 2
        assert row.n_precip_miss == 1
        assert row.n_precip_false_alarm == 1


class TestOrchestrators:
    def test_completed_days_excludes_today_and_summarised(self, db_session):
        # A score from a clearly-completed past day
        oid_past = _make_obs(db_session, "LFPG", _utc(2026, 2, 15, 12, 0))
        oid_past2 = _make_obs(db_session, "LFPG", _utc(2026, 2, 16, 12, 0))
        db_session.add(_score(oid_past, "LFPG", _utc(2026, 2, 15, 12, 0)))
        db_session.add(_score(oid_past2, "LFPG", _utc(2026, 2, 16, 12, 0)))
        db_session.flush()

        pending = completed_days(db_session)
        assert date(2026, 2, 15) in pending
        assert date(2026, 2, 16) in pending

        rollup_day(db_session, date(2026, 2, 15))
        pending_after = completed_days(db_session)
        assert date(2026, 2, 15) not in pending_after
        assert date(2026, 2, 16) in pending_after

    def test_rollup_all_walks_forward(self, db_session):
        oid1 = _make_obs(db_session, "LFPG", _utc(2026, 2, 15, 12, 0))
        oid2 = _make_obs(db_session, "LFPG", _utc(2026, 2, 16, 12, 0))
        db_session.add(_score(oid1, "LFPG", _utc(2026, 2, 15, 12, 0)))
        db_session.add(_score(oid2, "LFPG", _utc(2026, 2, 16, 12, 0)))
        db_session.flush()

        n1 = rollup_all_complete_days(db_session)
        rows = db_session.execute(select(VerificationDailyStatsRow)).scalars().all()
        dates = {r.date for r in rows}
        assert date(2026, 2, 15) in dates
        assert date(2026, 2, 16) in dates

        # Second pass is a no-op
        n2 = rollup_all_complete_days(db_session)
        rows2 = db_session.execute(select(VerificationDailyStatsRow)).scalars().all()
        assert len(rows) == len(rows2)
        assert n2 == 0
        # First run produced at least the expected rows
        assert n1 >= 2

    def test_rebuild_all_re_runs_existing_dates(self, db_session):
        oid = _make_obs(db_session, "LFPG", _utc(2026, 2, 15, 12, 0))
        db_session.add(_score(oid, "LFPG", _utc(2026, 2, 15, 12, 0)))
        db_session.flush()
        rollup_day(db_session, date(2026, 2, 15))
        # rebuild_all_days should re-run only the existing date(s).
        n = rebuild_all_days(db_session)
        assert n == 1
        rows = db_session.execute(select(VerificationDailyStatsRow)).scalars().all()
        assert len(rows) == 1
