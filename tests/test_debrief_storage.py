"""Tests for flight_debriefs storage (DB-backed)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from flyfun_common.db import DEV_USER_ID
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision, OutcomeValue
from weatherbrief.models import Flight
from weatherbrief.storage.debriefs import (
    delete_debrief,
    get_debrief,
    list_debriefed_flight_ids,
    list_debriefs_for_user,
    upsert_debrief,
)
from weatherbrief.storage.flights import save_flight


@pytest.fixture
def sample_flight():
    return Flight(
        id="egtk_lsgs-2026-02-21",
        user_id=DEV_USER_ID,
        route_name="egtk_lsgs",
        departure_time=datetime(2026, 2, 21, 9, tzinfo=timezone.utc),
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
        created_at=datetime(2026, 2, 14, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def saved_flight(db_session, dev_user, sample_flight):
    save_flight(db_session, sample_flight, dev_user)
    return sample_flight


class TestDebriefCRUD:
    def test_get_returns_none_when_absent(self, db_session, saved_flight):
        assert get_debrief(db_session, saved_flight.id) is None

    def test_insert_cancelled(self, db_session, saved_flight):
        d = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC, ConditionTag.WIND],
            note="too windy at LFAT",
        )
        assert d.flight_id == saved_flight.id
        assert d.decision is Decision.CANCELLED
        assert set(d.reasons) == {ConditionTag.IMC, ConditionTag.WIND}
        assert d.outcomes == {}
        assert d.note == "too windy at LFAT"

        loaded = get_debrief(db_session, saved_flight.id)
        assert loaded is not None
        assert loaded.decision is Decision.CANCELLED
        assert set(loaded.reasons) == {ConditionTag.IMC, ConditionTag.WIND}

    def test_insert_flown(self, db_session, saved_flight):
        d = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.FLOWN,
            outcomes={
                ConditionTag.ICE: OutcomeValue.WORSE,
                ConditionTag.IMC: OutcomeValue.CONSISTENT,
            },
            note="more icing than forecast around FL080",
        )
        assert d.decision is Decision.FLOWN
        assert d.outcomes[ConditionTag.ICE] is OutcomeValue.WORSE
        assert d.outcomes[ConditionTag.IMC] is OutcomeValue.CONSISTENT
        assert d.reasons == []

    def test_update_replaces_fields(self, db_session, saved_flight):
        first = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC],
        )
        time.sleep(0.01)  # ensure updated_at advances on systems with coarse clocks
        second = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.WIND, ConditionTag.TS],
            note="storm coming through",
        )
        assert set(second.reasons) == {ConditionTag.WIND, ConditionTag.TS}
        assert second.note == "storm coming through"
        assert second.created_at == first.created_at  # preserved
        assert second.updated_at >= first.updated_at  # bumped

    def test_decision_change_clears_other_field(self, db_session, saved_flight):
        # Cancelled → flown: reasons must clear, outcomes can be set.
        upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC],
        )
        flown = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.FLOWN,
            outcomes={ConditionTag.ICE: OutcomeValue.BETTER},
        )
        assert flown.reasons == []
        assert flown.outcomes[ConditionTag.ICE] is OutcomeValue.BETTER

    def test_delete(self, db_session, saved_flight):
        upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC],
        )
        assert delete_debrief(db_session, saved_flight.id) is True
        assert get_debrief(db_session, saved_flight.id) is None
        # Idempotent: second delete returns False.
        assert delete_debrief(db_session, saved_flight.id) is False


class TestValidation:
    def test_reasons_with_flown_rejected(self, db_session, saved_flight):
        with pytest.raises(ValidationError):
            upsert_debrief(
                db_session,
                flight_id=saved_flight.id,
                decision=Decision.FLOWN,
                reasons=[ConditionTag.IMC],
            )

    def test_outcomes_with_cancelled_rejected(self, db_session, saved_flight):
        with pytest.raises(ValidationError):
            upsert_debrief(
                db_session,
                flight_id=saved_flight.id,
                decision=Decision.CANCELLED,
                outcomes={ConditionTag.ICE: OutcomeValue.WORSE},
            )

    def test_monitoring_with_reasons_rejected(self, db_session, saved_flight):
        with pytest.raises(ValidationError):
            upsert_debrief(
                db_session,
                flight_id=saved_flight.id,
                decision=Decision.MONITORING,
                reasons=[ConditionTag.IMC],
            )

    def test_monitoring_with_outcomes_rejected(self, db_session, saved_flight):
        with pytest.raises(ValidationError):
            upsert_debrief(
                db_session,
                flight_id=saved_flight.id,
                decision=Decision.MONITORING,
                outcomes={ConditionTag.ICE: OutcomeValue.WORSE},
            )

    def test_monitoring_with_note_only(self, db_session, saved_flight):
        d = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.MONITORING,
            note="set up to watch the front move through",
        )
        assert d.decision is Decision.MONITORING
        assert d.reasons == []
        assert d.outcomes == {}
        assert d.note == "set up to watch the front move through"

    def test_ops_in_outcomes_rejected(self, db_session, saved_flight):
        with pytest.raises(ValidationError):
            upsert_debrief(
                db_session,
                flight_id=saved_flight.id,
                decision=Decision.FLOWN,
                outcomes={ConditionTag.OPS: OutcomeValue.WORSE},
            )

    def test_note_max_length(self, db_session, saved_flight):
        with pytest.raises(ValidationError):
            upsert_debrief(
                db_session,
                flight_id=saved_flight.id,
                decision=Decision.CANCELLED,
                reasons=[ConditionTag.IMC],
                note="x" * 301,
            )

    def test_reasons_dedupe(self, db_session, saved_flight):
        d = upsert_debrief(
            db_session,
            flight_id=saved_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC, ConditionTag.IMC, ConditionTag.WIND],
        )
        assert d.reasons.count(ConditionTag.IMC) == 1
        assert d.reasons.count(ConditionTag.WIND) == 1


class TestUserListing:
    def test_list_for_user(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)
        upsert_debrief(
            db_session,
            flight_id=sample_flight.id,
            decision=Decision.FLOWN,
            outcomes={ConditionTag.IMC: OutcomeValue.CONSISTENT},
        )
        debriefs = list_debriefs_for_user(db_session, dev_user)
        assert len(debriefs) == 1
        assert debriefs[0].flight_id == sample_flight.id

    def test_list_filters_by_since(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)
        upsert_debrief(
            db_session,
            flight_id=sample_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC],
        )
        # Future cutoff → no rows.
        future = datetime.now(timezone.utc).replace(year=2099)
        assert list_debriefs_for_user(db_session, dev_user, since=future) == []

    def test_debriefed_ids_set(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)
        assert list_debriefed_flight_ids(db_session) == set()
        upsert_debrief(
            db_session,
            flight_id=sample_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC],
        )
        assert list_debriefed_flight_ids(db_session) == {sample_flight.id}

    def test_cascade_on_flight_delete(self, db_session, dev_user, sample_flight):
        from weatherbrief.storage.flights import delete_flight

        save_flight(db_session, sample_flight, dev_user)
        upsert_debrief(
            db_session,
            flight_id=sample_flight.id,
            decision=Decision.CANCELLED,
            reasons=[ConditionTag.IMC],
        )
        delete_flight(db_session, sample_flight.id)
        assert list_debriefed_flight_ids(db_session) == set()
