"""Month-boundary observation_time corruption: guard and repair (#519).

METAR timestamps encode only DDHHMM, so the month is inferred.  The parser
used to assume the *current* month, dating a report collected just after a
month rolled over into the following month.  These tests pin both halves of
the response: the ingestion guard that refuses to store such a row, and the
repair script that corrects rows already written.
"""

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.db.models import (
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification import (
    _OBS_MAX_AHEAD,
    _observation_time_problem,
)

from scripts.repair_metar_month_boundary import (
    apply_repairs,
    apply_taf_repairs,
    find_corrupt,
    find_corrupt_taf,
)


COLLECTED = datetime(2026, 8, 1, 0, 0, 1, tzinfo=timezone.utc)
CORRECT = datetime(2026, 7, 31, 22, 55, tzinfo=timezone.utc)
CORRUPT = datetime(2026, 8, 31, 22, 55, tzinfo=timezone.utc)
RAW = "METAR LIPS 312255Z 00000KT CAVOK 28/22 Q1013"


class TestIngestionGuard:
    """The boundary check in tasks/verification.py."""

    def test_accepts_a_normal_observation(self):
        obs_time = COLLECTED - timedelta(minutes=6)
        assert _observation_time_problem(obs_time, COLLECTED) is None

    def test_rejects_a_future_observation(self):
        problem = _observation_time_problem(CORRUPT, COLLECTED)
        assert problem is not None
        assert "ahead of collection" in problem

    def test_rejects_missing_time(self):
        assert _observation_time_problem(None, COLLECTED) == "missing observation_time"

    def test_tolerates_small_clock_skew(self):
        slightly_ahead = COLLECTED + _OBS_MAX_AHEAD - timedelta(minutes=1)
        assert _observation_time_problem(slightly_ahead, COLLECTED) is None

    def test_rejects_just_beyond_tolerance(self):
        beyond = COLLECTED + _OBS_MAX_AHEAD + timedelta(minutes=1)
        assert _observation_time_problem(beyond, COLLECTED) is not None

    def test_accepts_naive_datetime_as_utc(self):
        """MySQL hands back naive values; they must not be mis-flagged."""
        assert _observation_time_problem(
            (COLLECTED - timedelta(minutes=6)).replace(tzinfo=None), COLLECTED
        ) is None

    def test_stale_observation_is_not_rejected(self):
        """A closed aerodrome may not issue for days — that is legitimate."""
        assert _observation_time_problem(
            COLLECTED - timedelta(days=30), COLLECTED
        ) is None


def _add_obs(session, icao, obs_time, collected_at, raw):
    row = VerificationObservationRow(
        icao=icao,
        observation_time=obs_time.replace(tzinfo=None),
        collected_at=collected_at.replace(tzinfo=None),
        metar_raw=raw,
    )
    session.add(row)
    session.flush()
    return row


class TestFindCorrupt:
    """Detection half of the repair script."""

    def test_finds_and_corrects_a_month_boundary_row(self, db_session):
        row = _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, RAW)

        findings = find_corrupt(db_session, batch=1000, sleep=0)

        assert len(findings) == 1
        assert findings[0]["id"] == row.id
        assert findings[0]["new"] == CORRECT
        assert findings[0]["old"] == CORRUPT

    def test_ignores_a_healthy_row(self, db_session):
        _add_obs(db_session, "LIPS", CORRECT, COLLECTED, RAW)

        assert find_corrupt(db_session, batch=1000, sleep=0) == []

    def test_ignores_rows_within_clock_skew_tolerance(self, db_session):
        obs_time = COLLECTED + timedelta(hours=1)
        _add_obs(db_session, "LIPS", obs_time, COLLECTED, RAW)

        assert find_corrupt(db_session, batch=1000, sleep=0) == []

    def test_reports_but_does_not_correct_unparseable_raw(self, db_session):
        _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, "not a metar at all")

        findings = find_corrupt(db_session, batch=1000, sleep=0)

        assert len(findings) == 1
        assert findings[0]["new"] is None
        assert findings[0]["reason"] is not None

    def test_full_scan_spans_multiple_pk_batches(self, db_session):
        for i in range(5):
            _add_obs(db_session, f"LIP{i}", CORRUPT, COLLECTED, RAW)

        findings = find_corrupt(db_session, batch=2, sleep=0, full=True)

        assert len([f for f in findings if f["new"] == CORRECT]) == 5

    def test_targeted_and_full_scans_agree(self, db_session):
        """The month-end windows must not miss what a full walk would catch."""
        for i in range(3):
            _add_obs(db_session, f"LIP{i}", CORRUPT, COLLECTED, RAW)
        _add_obs(db_session, "LFPG", CORRECT, COLLECTED, RAW)

        targeted = find_corrupt(db_session, batch=1000, sleep=0, full=False)
        full = find_corrupt(db_session, batch=1000, sleep=0, full=True)

        assert {f["id"] for f in targeted} == {f["id"] for f in full}
        assert len(targeted) == 3

    def test_empty_table(self, db_session):
        assert find_corrupt(db_session, batch=1000, sleep=0) == []


class TestApplyRepairs:
    """Write half of the repair script."""

    def test_repairs_the_stored_time(self, db_session):
        row = _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, RAW)
        findings = find_corrupt(db_session, batch=1000, sleep=0)

        stats = apply_repairs(db_session, findings, delete_scores=False)

        assert stats["repaired"] == 1
        db_session.refresh(row)
        assert row.observation_time == CORRECT.replace(tzinfo=None)

    def test_deletes_duplicate_when_correct_row_already_exists(self, db_session):
        good = _add_obs(db_session, "LIPS", CORRECT, COLLECTED, RAW)
        bad = _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, RAW)
        bad_id = bad.id
        findings = find_corrupt(db_session, batch=1000, sleep=0)

        stats = apply_repairs(db_session, findings, delete_scores=False)

        assert stats.get("deleted_duplicate") == 1
        assert stats.get("repaired", 0) == 0
        remaining = db_session.query(VerificationObservationRow).all()
        assert [r.id for r in remaining] == [good.id]
        assert db_session.get(VerificationObservationRow, bad_id) is None

    def test_leaves_scores_alone_by_default(self, db_session):
        row = _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, RAW)
        db_session.add(
            VerificationScoreRow(
                observation_id=row.id,
                icao="LIPS",
                observation_time=CORRUPT.replace(tzinfo=None),
                model="icon",
                model_init_time=COLLECTED.replace(tzinfo=None),
                lead_hours=1,
                days_out=0,
                source="standalone",
            )
        )
        db_session.flush()
        findings = find_corrupt(db_session, batch=1000, sleep=0)

        stats = apply_repairs(db_session, findings, delete_scores=False)

        assert stats.get("scores_deleted", 0) == 0
        assert db_session.query(VerificationScoreRow).count() == 1

    def test_deletes_scores_when_requested(self, db_session):
        row = _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, RAW)
        db_session.add(
            VerificationScoreRow(
                observation_id=row.id,
                icao="LIPS",
                observation_time=CORRUPT.replace(tzinfo=None),
                model="icon",
                model_init_time=COLLECTED.replace(tzinfo=None),
                lead_hours=1,
                days_out=0,
                source="standalone",
            )
        )
        db_session.flush()
        findings = find_corrupt(db_session, batch=1000, sleep=0)

        stats = apply_repairs(db_session, findings, delete_scores=True)

        assert stats["scores_deleted"] == 1
        assert db_session.query(VerificationScoreRow).count() == 0

    def test_is_idempotent(self, db_session):
        _add_obs(db_session, "LIPS", CORRUPT, COLLECTED, RAW)
        apply_repairs(
            db_session, find_corrupt(db_session, batch=1000, sleep=0), delete_scores=False
        )

        second = find_corrupt(db_session, batch=1000, sleep=0)

        assert second == []


TAF_RAW = "TAF LIPS 302300Z 0100/0109 VRB03KT CAVOK"
TAF_CORRECT = datetime(2026, 6, 30, 23, 0, tzinfo=timezone.utc)
TAF_CORRUPT = datetime(2026, 7, 30, 23, 0, tzinfo=timezone.utc)
TAF_COLLECTED = datetime(2026, 7, 1, 0, 0, 1, tzinfo=timezone.utc)


def _add_obs_with_taf(session, icao, obs_time, collected_at, issue_time):
    row = VerificationObservationRow(
        icao=icao,
        observation_time=obs_time.replace(tzinfo=None),
        collected_at=collected_at.replace(tzinfo=None),
        metar_raw="METAR LIPS 302255Z 00000KT CAVOK 28/22 Q1013",
        taf_raw=TAF_RAW,
        taf_issue_time=issue_time.replace(tzinfo=None) if issue_time else None,
    )
    session.add(row)
    session.flush()
    return row


class TestTafIssueTimeGuard:
    """taf_issue_time is part of uq_taf_verif_key and needs the same guard."""

    def test_rejects_future_taf_issue_time(self):
        problem = _observation_time_problem(TAF_CORRUPT, TAF_COLLECTED)
        assert problem is not None

    def test_accepts_normal_taf_issue_time(self):
        assert _observation_time_problem(TAF_CORRECT, TAF_COLLECTED) is None


class TestTafRepair:
    """Detection and repair of corrupted taf_issue_time."""

    def test_finds_and_corrects_corrupt_issue_time(self, db_session):
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        row = _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, TAF_CORRUPT)

        findings = find_corrupt_taf(db_session, sleep=0)

        assert len(findings) == 1
        assert findings[0]["id"] == row.id
        assert findings[0]["new"] == TAF_CORRECT

    def test_ignores_healthy_issue_time(self, db_session):
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, TAF_CORRECT)

        assert find_corrupt_taf(db_session, sleep=0) == []

    def test_ignores_rows_without_a_taf(self, db_session):
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, None)

        assert find_corrupt_taf(db_session, sleep=0) == []

    def test_applies_the_correction(self, db_session):
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        row = _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, TAF_CORRUPT)
        findings = find_corrupt_taf(db_session, sleep=0)

        stats = apply_taf_repairs(db_session, findings, delete_scores=False)

        assert stats["taf_repaired"] == 1
        db_session.refresh(row)
        assert row.taf_issue_time == TAF_CORRECT.replace(tzinfo=None)

    def test_deletes_taf_scores_when_requested(self, db_session):
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        row = _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, TAF_CORRUPT)
        db_session.add(
            TafVerificationScoreRow(
                observation_id=row.id,
                icao="LIPS",
                observation_time=obs_time.replace(tzinfo=None),
                taf_issue_time=TAF_CORRUPT.replace(tzinfo=None),
                lead_hours=1,
                source="standalone",
            )
        )
        db_session.flush()
        findings = find_corrupt_taf(db_session, sleep=0)

        stats = apply_taf_repairs(db_session, findings, delete_scores=True)

        assert stats["taf_scores_deleted"] == 1
        assert db_session.query(TafVerificationScoreRow).count() == 0

    def test_is_idempotent(self, db_session):
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, TAF_CORRUPT)
        apply_taf_repairs(
            db_session, find_corrupt_taf(db_session, sleep=0), delete_scores=False
        )

        assert find_corrupt_taf(db_session, sleep=0) == []

    def test_observation_time_repair_is_independent(self, db_session):
        """A row can have a good observation_time and a corrupt taf_issue_time."""
        obs_time = TAF_COLLECTED - timedelta(minutes=65)
        _add_obs_with_taf(db_session, "LIPS", obs_time, TAF_COLLECTED, TAF_CORRUPT)

        assert find_corrupt(db_session, batch=1000, sleep=0) == []
        assert len(find_corrupt_taf(db_session, sleep=0)) == 1
