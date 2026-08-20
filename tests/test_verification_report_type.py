"""Classifying stored observations as routine METAR or SPECI (#562, migration 091).

The survey is the measurement that says how much convective truth the pre-091
ingest was discarding; the backfill is the deliberate, off-peak act that
applies it. Both are keyset-paginated, so the batch boundary is worth pinning.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from weatherbrief.db.models import VerificationObservationRow
from weatherbrief.tasks.verification_report_type import (
    backfill_report_type,
    survey_report_types,
)

BASE = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_engine():
    """Per-test engine — `backfill_report_type` commits per batch.

    The shared session-scoped engine relies on the session fixture's rollback
    for isolation, which a real commit defeats: rows would leak into every
    later test, and the survey counts scan the whole table.
    """
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()

_ROUTINE = "METAR EGTK 011200Z 27010KT 9999 FEW040 12/05 Q1013"
_SPECIAL = "SPECI EGTK 011213Z 27025G40KT 3000 TSRA BKN008CB 12/11 Q1005"


def _add(db, n, raw, icao="EGTK", start_hour=0, report_type=None):
    for i in range(n):
        db.add(
            VerificationObservationRow(
                icao=icao,
                observation_time=BASE + timedelta(hours=start_hour + i),
                collected_at=BASE,
                metar_raw=raw,
                report_type=report_type,
            )
        )
    db.flush()


class TestSurvey:
    def test_counts_speci_by_raw_prefix(self, db_session):
        _add(db_session, 8, _ROUTINE, start_hour=0)
        _add(db_session, 2, _SPECIAL, start_hour=100)

        survey = survey_report_types(db_session)

        assert survey.total == 10
        assert survey.speci == 2
        assert survey.metar == 8
        assert survey.already_classified == 0
        assert survey.speci_rate == 20.0

    def test_rows_without_raw_text_are_unclassifiable(self, db_session):
        _add(db_session, 3, _ROUTINE, start_hour=0)
        _add(db_session, 1, None, start_hour=50)

        survey = survey_report_types(db_session)

        assert survey.unknown == 1
        assert survey.classifiable == 3
        assert survey.metar == 3

    def test_empty_table_does_not_divide_by_zero(self, db_session):
        survey = survey_report_types(db_session)

        assert survey.total == 0
        assert survey.speci_rate == 0.0

    def test_paginates_across_batches(self, db_session):
        """A batch smaller than the table must not lose or double-count rows."""
        _add(db_session, 7, _ROUTINE, start_hour=0)
        _add(db_session, 3, _SPECIAL, start_hour=100)

        survey = survey_report_types(db_session, batch_size=2)

        assert survey.total == 10
        assert survey.speci == 3
        assert survey.metar == 7


class TestBackfill:
    def test_classifies_from_the_raw_prefix(self, db_session):
        _add(db_session, 4, _ROUTINE, start_hour=0)
        _add(db_session, 2, _SPECIAL, start_hour=100)

        updated = backfill_report_type(db_session)

        assert updated == 6
        rows = db_session.execute(
            select(VerificationObservationRow).order_by(
                VerificationObservationRow.observation_time
            )
        ).scalars().all()
        assert [r.report_type for r in rows] == ["METAR"] * 4 + ["SPECI"] * 2

    def test_leaves_rows_without_raw_text_null(self, db_session):
        """Nothing can be inferred, and NULL already reads as routine."""
        _add(db_session, 1, None, start_hour=0)

        assert backfill_report_type(db_session) == 0

        row = db_session.execute(
            select(VerificationObservationRow)
        ).scalar_one()
        assert row.report_type is None

    def test_is_resumable_and_idempotent(self, db_session):
        _add(db_session, 3, _ROUTINE, start_hour=0)
        _add(db_session, 1, _SPECIAL, start_hour=100)

        first = backfill_report_type(db_session)
        second = backfill_report_type(db_session)

        assert first == 4
        assert second == 0, "already-classified rows must not be rewritten"

    def test_does_not_overwrite_an_existing_classification(self, db_session):
        """New rows arrive already classified; the backfill is for history."""
        _add(db_session, 1, _ROUTINE, start_hour=0, report_type="SPECI")

        assert backfill_report_type(db_session) == 0

        row = db_session.execute(
            select(VerificationObservationRow)
        ).scalar_one()
        assert row.report_type == "SPECI"

    def test_paginates_across_batches(self, db_session):
        _add(db_session, 5, _ROUTINE, start_hour=0)
        _add(db_session, 4, _SPECIAL, start_hour=100)

        updated = backfill_report_type(db_session, batch_size=2)

        assert updated == 9
        survey = survey_report_types(db_session)
        assert survey.already_classified == 9
        assert survey.speci == 4
