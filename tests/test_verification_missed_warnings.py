"""Missed wind warnings must be queried in the vocabulary that is actually stored.

`get_missed_warnings` filtered on `obs_wind_advisory == "WARNING"` for its whole
life. The column only ever holds green/amber/red, so the query matched nothing
and the "Missed Wind Warnings" section of the admin dashboard and the daily
digest was silently empty from the day it shipped (#418).

That is the trap these tests exist for: a test written against `"WARNING"` would
have passed happily while testing data that cannot exist. Every row seeded here
uses the real vocabulary.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from weatherbrief.db.models import (
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_stats import get_missed_warnings

SOURCE = "standalone_missed"
OBS_TIME = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc)

# The horizon now reaches D+6 (#415), so a single storm is scored at seven leads.
ALL_LEADS = (0, 1, 2, 3, 4, 5, 6)
NEAR_TERM = (0, 1)


def _seed(db_session, icao: str, *, model_says: dict[int, str | None]):
    """One observed `red` wind advisory, scored at every lead in ``model_says``.

    The value at each lead is what the *model* predicted — ``None`` meaning the
    model produced no advisory at all.

    Flushed, never committed. `get_missed_warnings` reads through this same
    session, so the rows are visible without a commit — and the `db_session`
    fixture then rolls them back, so nothing survives into another test. That
    matters: the engine is shared, and `run_monthly_rollup` in
    `test_verification_rollup.py` counts *every* month with observations in it,
    so a committing test anywhere becomes an order-dependent failure over there.
    """
    obs = VerificationObservationRow(
        icao=icao, observation_time=OBS_TIME, collected_at=OBS_TIME,
        flight_category="VFR", ceiling_ft=3000, visibility_m=9999,
        wind_dir=270, wind_speed_kt=35, temperature_c=12,
        weather=json.dumps([]),
    )
    db_session.add(obs)
    db_session.flush()

    for lead, predicted in model_says.items():
        db_session.add(VerificationScoreRow(
            observation_id=obs.id,
            icao=icao,
            observation_time=OBS_TIME,
            model="gfs",
            model_init_time=OBS_TIME - timedelta(hours=lead * 24 + 1),
            lead_hours=lead * 24,
            days_out=lead,
            source=SOURCE,
            obs_flight_category="VFR",
            model_flight_category="VFR",
            category_match=True,
            obs_wind_advisory="red",
            model_wind_advisory=predicted,
        ))
    db_session.flush()


def _fetch(db_session, icao: str):
    return get_missed_warnings(
        db_session, SINCE, UNTIL, source=SOURCE, icao_filter=[icao],
    )


class TestMissedWarningsUseTheRealVocabulary:
    def test_an_observed_red_called_green_is_a_miss(self, db_session):
        """The regression itself: this returned nothing while the filter said WARNING."""
        _seed(db_session, "EGKK", model_says={0: "green"})

        misses = _fetch(db_session, "EGKK")

        assert len(misses) == 1, "a red observation the model called green is a miss"
        assert misses[0].obs_wind_advisory == "red"
        assert misses[0].model_wind_advisory == "green"
        assert misses[0].icao == "EGKK"

    def test_a_model_that_called_the_red_is_not_a_miss(self, db_session):
        _seed(db_session, "EGSS", model_says={0: "red", 1: "red"})

        assert _fetch(db_session, "EGSS") == []

    def test_an_amber_call_is_still_a_miss(self, db_session):
        """Amber is not red. The model under-called the strongest advisory."""
        _seed(db_session, "EGGW", model_says={0: "amber"})

        misses = _fetch(db_session, "EGGW")

        assert [m.model_wind_advisory for m in misses] == ["amber"]


class TestMissedWarningsAreLeadScoped:
    def test_one_storm_does_not_fill_the_list_with_copies_of_itself(self, db_session):
        """Scored at seven leads, a single missed storm must surface only at D-0/D-1.

        Unscoped, this one event would take seven of the ten slots — and a model
        missing a warning six days out is expected, not notable.
        """
        _seed(db_session, "LFPG", model_says={lead: "green" for lead in ALL_LEADS})

        misses = _fetch(db_session, "LFPG")

        assert sorted(m.days_out for m in misses) == list(NEAR_TERM)

    def test_a_miss_only_at_long_lead_is_not_surfaced(self, db_session):
        """Right when it counts, wrong a week out. Not a missed warning."""
        _seed(db_session, "LFMD", model_says={
            0: "red", 1: "red", 2: "green", 3: "green",
            4: "green", 5: "green", 6: "green",
        })

        assert _fetch(db_session, "LFMD") == []


class TestModelsWithNoAdvisoryAreAbsenceNotMiss:
    def test_a_null_model_advisory_is_not_a_miss(self, db_session):
        """No runway data means no advisory to compare — that is absence, not a bust.

        Also guards the DTO: ``MissedWarning.model_wind_advisory`` is a required
        ``str``, so a NULL leaking through would raise a ValidationError rather
        than merely reporting a wrong row.
        """
        _seed(db_session, "EGLF", model_says={0: None, 1: None})

        assert _fetch(db_session, "EGLF") == []

    def test_a_null_lead_does_not_hide_a_real_miss_at_another_lead(self, db_session):
        _seed(db_session, "EGTF", model_says={0: None, 1: "green"})

        misses = _fetch(db_session, "EGTF")

        assert [(m.days_out, m.model_wind_advisory) for m in misses] == [(1, "green")]
