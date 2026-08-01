"""Tests for gust verification (#491).

Covers the standardised definitions, the scoring path that persists them,
the daily/monthly rollup aggregates, the dashboard query, and the two
backfills.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from conftest import make_app_engine
from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    TafVerificationScoreRow,
    VerificationDailyStatsRow,
    VerificationMonthlyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.scoring import _score_model_vs_metar, _score_taf_vs_metar
from weatherbrief.tasks.verification_daily_rollup import rollup_day
from weatherbrief.tasks.verification_gust import (
    GUST_FLAG_THRESHOLD_KT,
    backfill_model_gust,
    backfill_taf_gust_deltas,
    forecast_shows_gust,
    realised_peak_kt,
)
from weatherbrief.tasks.verification_rollup import rollup_month
from weatherbrief.tasks.verification_stats import get_gust_accuracy


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = _utc(2026, 4, 1, 12, 0)


# ---------------------------------------------------------------------------
# Standardised definitions
# ---------------------------------------------------------------------------


class TestForecastShowsGust:

    def test_at_threshold_counts(self):
        assert forecast_shows_gust(15.0, 15.0 + GUST_FLAG_THRESHOLD_KT) is True

    def test_below_threshold_does_not(self):
        assert forecast_shows_gust(15.0, 24.0) is False

    def test_missing_component_is_unknown_not_false(self):
        assert forecast_shows_gust(None, 30.0) is None
        assert forecast_shows_gust(15.0, None) is None


class TestRealisedPeak:

    def test_reported_gust_wins(self):
        assert realised_peak_kt(12.0, 25.0) == 25.0

    def test_no_gust_group_means_peak_is_the_mean(self):
        assert realised_peak_kt(12.0, None) == 12.0

    def test_nothing_observed(self):
        assert realised_peak_kt(None, None) is None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoringPersistsGust:

    def _obs_row(self, **overrides):
        defaults = dict(
            id=1, icao="EGTK", observation_time=NOW, flight_category="VFR",
            ceiling_ft=4000, visibility_m=9999, wind_dir=270, wind_speed_kt=20,
            wind_gust_kt=28, temperature_c=12,
            taf_issue_time=NOW - timedelta(hours=4), taf_flight_category="VFR",
            taf_ceiling_ft=4000, taf_visibility_m=9999, taf_wind_dir=270,
            taf_wind_speed_kt=18, taf_wind_gust_kt=30,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _hourly(self, **overrides):
        defaults = dict(
            visibility_m=10000, wind_direction_10m_deg=260,
            wind_speed_10m_kt=18.0, wind_gusts_10m_kt=30.0,
            precipitation_mm=0.0, snowfall_cm=0.0, temperature_2m_c=11.5,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _score(self, obs_row, hourly):
        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling",
            return_value=3800.0,
        ), patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
            return_value=SimpleNamespace(value="VFR"),
        ), patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            side_effect=[("green", "R27", 2.0, 12.0), ("green", "R27", 1.5, 10.0)],
        ):
            return _score_model_vs_metar(
                obs_row=obs_row, obs_weather=[], sounding=None, hourly=hourly,
                runway_ends=[], model="gfs",
                model_init_time=NOW - timedelta(hours=6), days_out=0,
            )

    def test_model_gust_fields(self):
        result = self._score(self._obs_row(), self._hourly())
        assert result.model_wind_gust_kt == 30.0
        assert result.wind_gust_delta_kt == pytest.approx(2.0)  # 30 - 28
        assert result.model_gust_flag is True  # 30 - 18 >= 10

    def test_raw_gust_survives_when_the_airport_was_not_gusting(self):
        """The row the whole issue exists for: model called a gust, METAR
        reported none. The delta is NULL but the forecast gust is kept."""
        result = self._score(self._obs_row(wind_gust_kt=None), self._hourly())
        assert result.model_wind_gust_kt == 30.0
        assert result.wind_gust_delta_kt is None
        assert result.model_gust_flag is True

    def test_forecast_without_a_gust_is_flagged_false(self):
        result = self._score(
            self._obs_row(), self._hourly(wind_speed_10m_kt=18.0, wind_gusts_10m_kt=24.0),
        )
        assert result.model_gust_flag is False
        assert result.model_wind_gust_kt == 24.0

    def test_no_model_gust_at_all(self):
        result = self._score(self._obs_row(), self._hourly(wind_gusts_10m_kt=None))
        assert result.model_wind_gust_kt is None
        assert result.wind_gust_delta_kt is None
        assert result.model_gust_flag is None

    def test_taf_gust_delta(self):
        with patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            side_effect=[("green", "R27", 2.0, 12.0), ("green", "R27", 1.5, 10.0)],
        ):
            result = _score_taf_vs_metar(self._obs_row(), [], [], source="standalone")
        assert result.wind_gust_delta_kt == pytest.approx(2.0)  # 30 - 28

    def test_taf_gust_delta_null_without_observed_gust(self):
        with patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            side_effect=[("green", "R27", 2.0, 12.0), ("green", "R27", 1.5, 10.0)],
        ):
            result = _score_taf_vs_metar(
                self._obs_row(wind_gust_kt=None), [], [], source="standalone",
            )
        assert result.wind_gust_delta_kt is None


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------


def _add_obs(db, icao, when, *, wind=None, gust=None, taf_gust=None) -> int:
    o = VerificationObservationRow(
        icao=icao, observation_time=when, collected_at=when,
        wind_speed_kt=wind, wind_gust_kt=gust, taf_wind_gust_kt=taf_gust,
    )
    db.add(o)
    db.flush()
    return o.id


def _add_score(db, obs_id, icao, when, **kwargs):
    row = VerificationScoreRow(
        observation_id=obs_id, icao=icao, observation_time=when,
        model=kwargs.pop("model", "gfs"),
        model_init_time=kwargs.pop("init_time", when),
        lead_hours=0, days_out=kwargs.pop("days_out", 0),
        source=kwargs.pop("source", "standalone"),
        **kwargs,
    )
    db.add(row)
    db.flush()
    return row


class TestDailyRollupGust:

    def test_both_conditionings_are_aggregated_separately(self, db_session):
        day = _utc(2026, 5, 4, 12, 0)
        # Hour 1: model flags a gust, airport not gusting (mean wind 12 kt).
        oid1 = _add_obs(db_session, "LFPG", day, wind=12, gust=None)
        _add_score(
            db_session, oid1, "LFPG", day,
            model_wind_gust_kt=25.0, wind_gust_delta_kt=None, model_gust_flag=True,
        )
        # Hour 2: model flags a gust and the airport gusted to 30 (model 26).
        oid2 = _add_obs(db_session, "LFPG", day + timedelta(hours=3), wind=20, gust=30)
        _add_score(
            db_session, oid2, "LFPG", day + timedelta(hours=3),
            model_wind_gust_kt=26.0, wind_gust_delta_kt=-4.0, model_gust_flag=True,
        )
        # Hour 3: no gust either side.
        oid3 = _add_obs(db_session, "LFPG", day + timedelta(hours=6), wind=8, gust=None)
        _add_score(
            db_session, oid3, "LFPG", day + timedelta(hours=6),
            model_wind_gust_kt=12.0, wind_gust_delta_kt=None, model_gust_flag=False,
        )

        rollup_day(db_session, date(2026, 5, 4))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalars().one()

        # obs-flagged magnitude: only hour 2 has both gusts
        assert row.n_gust == 1
        assert row.sum_gust_delta_kt == pytest.approx(-4.0)
        assert row.sum_abs_gust_delta_kt == pytest.approx(4.0)
        # forecast-flagged magnitude: hours 1 and 2, each vs its realised peak
        # (12 kt mean, then the 30 kt gust) → (25-12) + (26-30) = 9
        assert row.n_gust_flagged_peak == 2
        assert row.sum_gust_flagged_over_peak_kt == pytest.approx(9.0)
        # occurrence
        assert row.n_model_gust_flag == 2
        assert row.n_obs_gust == 1
        assert row.n_gust_flag_hit == 1

    def test_rows_without_gust_data_contribute_nothing(self, db_session):
        day = _utc(2026, 5, 6, 12, 0)
        oid = _add_obs(db_session, "EDDF", day, wind=None, gust=None)
        _add_score(db_session, oid, "EDDF", day)

        rollup_day(db_session, date(2026, 5, 6))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalars().one()
        assert row.n == 1
        assert row.n_gust == 0
        assert row.n_model_gust_flag == 0
        assert row.n_obs_gust == 0
        assert row.n_gust_flagged_peak == 0
        assert row.sum_gust_delta_kt is None
        assert row.sum_gust_flagged_over_peak_kt is None

    def test_join_does_not_drop_scores(self, db_session):
        """The gust join is inner — every score must still be counted."""
        day = _utc(2026, 5, 7, 12, 0)
        for i in range(3):
            oid = _add_obs(db_session, "LSZH", day + timedelta(hours=i), wind=10)
            _add_score(db_session, oid, "LSZH", day + timedelta(hours=i))

        rollup_day(db_session, date(2026, 5, 7))
        row = db_session.execute(select(VerificationDailyStatsRow)).scalars().one()
        assert row.n == 3


class TestMonthlyRollupGust:

    def test_model_row_carries_both_conditionings(self, db_session):
        when = _utc(2026, 3, 10, 12, 0)
        oid1 = _add_obs(db_session, "LFPG", when, wind=12, gust=None)
        _add_score(
            db_session, oid1, "LFPG", when,
            model_wind_gust_kt=25.0, model_gust_flag=True,
        )
        oid2 = _add_obs(db_session, "LFPG", when + timedelta(hours=3), wind=20, gust=30)
        _add_score(
            db_session, oid2, "LFPG", when + timedelta(hours=3),
            model_wind_gust_kt=26.0, wind_gust_delta_kt=-4.0, model_gust_flag=True,
        )

        # The monthly rollup sums the daily table (#522), so the days have to
        # be rolled first — that dependency is what keeps month and day from
        # ever disagreeing.
        rollup_day(db_session, date(2026, 3, 10))
        rollup_month(db_session, _utc(2026, 3, 1))
        row = db_session.execute(
            select(VerificationMonthlyStatsRow).where(
                VerificationMonthlyStatsRow.model == "gfs"
            )
        ).scalars().one()

        assert row.n_gust == 1
        assert row.wind_gust_mae_kt == pytest.approx(4.0)
        assert row.wind_gust_bias_kt == pytest.approx(-4.0)
        assert row.n_model_gust_flag == 2
        assert row.n_obs_gust == 1
        assert row.n_gust_flag_hit == 1
        assert row.n_gust_flagged_peak == 2
        assert row.gust_over_peak_bias_kt == pytest.approx(4.5)  # (13 + -4) / 2


# ---------------------------------------------------------------------------
# Dashboard query
# ---------------------------------------------------------------------------


class TestGetGustAccuracy:

    def _daily(self, db, **overrides):
        defaults = dict(
            date=date(2026, 5, 4), source="standalone", model="gfs", days_out=0,
            icao="LFPG", n=100, n_gust=10, sum_abs_gust_delta_kt=60.0,
            sum_gust_delta_kt=-60.0, n_gust_flagged_peak=40,
            sum_gust_flagged_over_peak_kt=280.0, n_model_gust_flag=40,
            n_obs_gust=10, n_gust_flag_hit=8,
        )
        defaults.update(overrides)
        row = VerificationDailyStatsRow(**defaults)
        db.add(row)
        db.flush()
        return row

    def test_nwp_reports_both_views(self, db_session):
        self._daily(db_session)
        stats = get_gust_accuracy(
            db_session, _utc(2026, 5, 4), _utc(2026, 5, 5), source="standalone",
        )
        assert len(stats) == 1
        s = stats[0]
        assert s.model == "gfs"
        # obs-flagged: model runs low on the hours the airport gusted
        assert s.n_gust == 10
        assert s.gust_mae_kt == pytest.approx(6.0)
        assert s.gust_bias_kt == pytest.approx(-6.0)
        # forecast-flagged: model sits above the realised peak, and over-warns
        assert s.n_flagged == 40
        assert s.flagged_over_peak_kt == pytest.approx(7.0)
        assert s.over_warn_ratio == pytest.approx(4.0)
        assert s.n_flag_hit == 8

    def test_sums_are_additive_across_days(self, db_session):
        self._daily(db_session)
        self._daily(db_session, date=date(2026, 5, 5))
        stats = get_gust_accuracy(
            db_session, _utc(2026, 5, 4), _utc(2026, 5, 6), source="standalone",
        )
        assert len(stats) == 1
        assert stats[0].n_flagged == 80
        # Ratios stay ratios — they are derived after summing.
        assert stats[0].flagged_over_peak_kt == pytest.approx(7.0)
        assert stats[0].over_warn_ratio == pytest.approx(4.0)

    def test_groups_with_no_gust_activity_are_dropped(self, db_session):
        self._daily(
            db_session, n_gust=0, sum_abs_gust_delta_kt=None,
            sum_gust_delta_kt=None, n_gust_flagged_peak=0,
            sum_gust_flagged_over_peak_kt=None, n_model_gust_flag=0,
            n_obs_gust=0, n_gust_flag_hit=0,
        )
        assert get_gust_accuracy(
            db_session, _utc(2026, 5, 4), _utc(2026, 5, 5), source="standalone",
        ) == []

    def test_taf_comes_from_raw_scores(self, db_session):
        when = _utc(2026, 5, 4, 12, 0)
        # TAF carries a gust group, airport isn't gusting (mean 12 kt).
        oid1 = _add_obs(db_session, "LFPG", when, wind=12, gust=None, taf_gust=25)
        # TAF carries a gust group and the airport gusted to 30.
        oid2 = _add_obs(
            db_session, "LFPG", when + timedelta(hours=3),
            wind=20, gust=30, taf_gust=29,
        )
        for oid, obs_time, delta in (
            (oid1, when, None), (oid2, when + timedelta(hours=3), -1.0),
        ):
            db_session.add(TafVerificationScoreRow(
                observation_id=oid, icao="LFPG", observation_time=obs_time,
                taf_issue_time=when - timedelta(hours=4), lead_hours=4,
                source="standalone", wind_gust_delta_kt=delta,
            ))
        db_session.flush()

        stats = get_gust_accuracy(
            db_session, _utc(2026, 5, 4), _utc(2026, 5, 5), source="standalone",
        )
        taf = [s for s in stats if s.model == "TAF"]
        assert len(taf) == 1
        assert taf[0].n_flagged == 2
        assert taf[0].n_obs_gust == 1
        assert taf[0].over_warn_ratio == pytest.approx(2.0)
        # (25 - 12) + (29 - 30) = 12 over 2 flagged hours
        assert taf[0].flagged_over_peak_kt == pytest.approx(6.0)
        assert taf[0].n_gust == 1
        assert taf[0].gust_bias_kt == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Digest rendering
# ---------------------------------------------------------------------------


class TestDigestGustSection:

    def _data(self, **overrides):
        from weatherbrief.models.verification import (
            GustAccuracyStats,
            VerificationDigestData,
        )
        defaults = dict(
            model="ecmwf", days_out=0, n=1000, n_gust=40, gust_mae_kt=6.2,
            gust_bias_kt=-5.1, n_flagged=160, flagged_over_peak_kt=7.1,
            n_obs_gust=40, n_flag_hit=32, over_warn_ratio=4.0,
        )
        defaults.update(overrides)
        return VerificationDigestData(
            period_label="24h", gust_accuracy=[GustAccuracyStats(**defaults)],
        )

    def test_html_reports_both_conditionings(self):
        from weatherbrief.notify.verification_email import _render_gust_section

        html = _render_gust_section(self._data())
        assert "+7.1 kt" in html   # forecast-flagged, above the realised peak
        assert "4.0×" in html      # over-warn ratio
        assert "-5.1 kt" in html   # obs-flagged, running low

    def test_html_scoped_to_d0(self):
        from weatherbrief.notify.verification_email import _render_gust_section

        assert _render_gust_section(self._data(days_out=2)) == ""

    def test_plaintext_includes_gust(self):
        from weatherbrief.notify.verification_email import _build_digest_plain

        text = _build_digest_plain(self._data())
        assert "GUST ACCURACY (D-0)" in text
        assert "over-warn  4.0x" in text


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


@pytest.fixture
def committing_db():
    """A session with its own engine — the backfills commit per batch."""
    engine = make_app_engine()
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


class TestTafGustBackfill:

    def test_fills_delta_from_stored_observations(self, committing_db):
        db = committing_db
        when = _utc(2026, 4, 20, 12, 0)
        oid = _add_obs(db, "LFPG", when, wind=20, gust=30, taf_gust=26)
        db.add(TafVerificationScoreRow(
            observation_id=oid, icao="LFPG", observation_time=when,
            taf_issue_time=when - timedelta(hours=5), lead_hours=5,
            source="standalone",
        ))
        db.flush()

        assert backfill_taf_gust_deltas(db) == 1
        row = db.execute(select(TafVerificationScoreRow)).scalars().one()
        assert row.wind_gust_delta_kt == pytest.approx(-4.0)

    def test_skips_rows_without_both_gusts_and_is_idempotent(self, committing_db):
        db = committing_db
        when = _utc(2026, 4, 21, 12, 0)
        oid = _add_obs(db, "EDDF", when, wind=20, gust=None, taf_gust=26)
        db.add(TafVerificationScoreRow(
            observation_id=oid, icao="EDDF", observation_time=when,
            taf_issue_time=when - timedelta(hours=5), lead_hours=5,
            source="standalone",
        ))
        db.flush()

        assert backfill_taf_gust_deltas(db) == 0
        assert backfill_taf_gust_deltas(db) == 0
        row = db.execute(select(TafVerificationScoreRow)).scalars().one()
        assert row.wind_gust_delta_kt is None


class TestModelGustBackfill:

    def test_fills_from_the_unpruned_snapshot_window(self, committing_db):
        db = committing_db
        when = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        ) - timedelta(hours=3)
        init = when - timedelta(hours=6)

        oid = _add_obs(db, "LFPG", when, wind=20, gust=28)
        score = _add_score(db, oid, "LFPG", when, init_time=init, model="icon")
        db.add(AirportForecastSnapshotRow(
            icao="LFPG", model="icon", model_init_time=init, forecast_hour=when,
            wind_speed_10m_kt=18.0, wind_gusts_10m_kt=32.0,
        ))
        db.flush()

        assert backfill_model_gust(db, days=2) == 1
        db.refresh(score)
        assert score.model_wind_gust_kt == pytest.approx(32.0)
        assert score.wind_gust_delta_kt == pytest.approx(4.0)  # 32 - 28
        assert score.model_gust_flag is True

    def test_the_stored_wind_delta_picks_between_forecast_hours(
        self, committing_db,
    ):
        """Identify the scored snapshot, don't assume the nearest hour won.

        `_score_cycle` has no forecast_hour tiebreak, so the neighbouring hour
        can be the one that got scored. The stored wind delta says which.
        """
        db = committing_db
        when = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        ) - timedelta(hours=3)
        init = when - timedelta(hours=6)

        oid = _add_obs(db, "EGKK", when, wind=20, gust=28)
        # delta 19 - 20 = -1.0 -> the +1 h snapshot was the one scored, even
        # though the exact-hour snapshot is nearer in time.
        score = _add_score(
            db, oid, "EGKK", when, init_time=init, model="icon",
            wind_speed_delta_kt=-1.0,
        )
        db.add(AirportForecastSnapshotRow(
            icao="EGKK", model="icon", model_init_time=init, forecast_hour=when,
            wind_speed_10m_kt=18.0, wind_gusts_10m_kt=32.0,
        ))
        db.add(AirportForecastSnapshotRow(
            icao="EGKK", model="icon", model_init_time=init,
            forecast_hour=when + timedelta(hours=1),
            wind_speed_10m_kt=19.0, wind_gusts_10m_kt=40.0,
        ))
        db.flush()

        assert backfill_model_gust(db, days=2) == 1
        db.refresh(score)
        assert score.model_wind_gust_kt == pytest.approx(40.0)
        assert score.wind_gust_delta_kt == pytest.approx(12.0)  # 40 - 28

    def test_ambiguous_forecast_hours_leave_the_row_null(self, committing_db):
        """Two candidates and no stored delta to separate them -> stay NULL."""
        db = committing_db
        when = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        ) - timedelta(hours=3)
        init = when - timedelta(hours=6)

        oid = _add_obs(db, "LFBO", when, wind=20, gust=28)
        _add_score(db, oid, "LFBO", when, init_time=init, model="icon")
        for offset, wind, gust in (
            (timedelta(0), 18.0, 32.0),
            (timedelta(hours=1), 19.0, 40.0),
        ):
            db.add(AirportForecastSnapshotRow(
                icao="LFBO", model="icon", model_init_time=init,
                forecast_hour=when + offset,
                wind_speed_10m_kt=wind, wind_gusts_10m_kt=gust,
            ))
        db.flush()

        assert backfill_model_gust(db, days=2) == 0
        row = db.execute(select(VerificationScoreRow)).scalars().one()
        assert row.model_wind_gust_kt is None

    def test_leaves_already_populated_scores_alone(self, committing_db):
        db = committing_db
        when = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        ) - timedelta(hours=3)
        init = when - timedelta(hours=6)

        oid = _add_obs(db, "EGLL", when, wind=20, gust=28)
        _add_score(
            db, oid, "EGLL", when, init_time=init, model="gfs",
            model_wind_gust_kt=99.0,
        )
        db.add(AirportForecastSnapshotRow(
            icao="EGLL", model="gfs", model_init_time=init, forecast_hour=when,
            wind_speed_10m_kt=18.0, wind_gusts_10m_kt=32.0,
        ))
        db.flush()

        assert backfill_model_gust(db, days=2) == 0
        row = db.execute(select(VerificationScoreRow)).scalars().one()
        assert row.model_wind_gust_kt == pytest.approx(99.0)

    def test_a_snapshot_gap_leaves_the_score_untouched(self, committing_db):
        """Nearest-wins must not reach past the window live scoring uses."""
        db = committing_db
        when = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        ) - timedelta(hours=4)
        init = when - timedelta(hours=6)

        oid = _add_obs(db, "EHAM", when, wind=20, gust=28)
        _add_score(db, oid, "EHAM", when, init_time=init, model="icon")
        # The only snapshot for this key sits 3 h away — a gap, not a match.
        db.add(AirportForecastSnapshotRow(
            icao="EHAM", model="icon", model_init_time=init,
            forecast_hour=when + timedelta(hours=3),
            wind_speed_10m_kt=19.0, wind_gusts_10m_kt=40.0,
        ))
        db.flush()

        assert backfill_model_gust(db, days=2) == 0
        row = db.execute(select(VerificationScoreRow)).scalars().one()
        assert row.model_wind_gust_kt is None
        assert row.wind_gust_delta_kt is None
        assert row.model_gust_flag is None

    def test_no_snapshot_leaves_the_score_untouched(self, committing_db):
        db = committing_db
        when = datetime.now(timezone.utc) - timedelta(hours=3)
        oid = _add_obs(db, "LSZH", when, wind=20, gust=28)
        _add_score(db, oid, "LSZH", when, init_time=when - timedelta(hours=6))
        db.flush()

        assert backfill_model_gust(db, days=2) == 0
        row = db.execute(select(VerificationScoreRow)).scalars().one()
        assert row.model_wind_gust_kt is None
