"""Tests for the serial trip-refresh driver and its coalescing (#602).

The properties worth pinning are the ones a naive implementation gets wrong:
one leg in flight at a time (never a fan-out), a capped trigger, a chain that
keeps going when a leg fails, and one notification for the whole chain.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api import trip_refresh
from weatherbrief.db.models import FlightTripRow
from weatherbrief.models import Flight
from weatherbrief.storage import trips as trip_storage
from weatherbrief.storage.flights import save_flight

_NOW = datetime.now(timezone.utc)


@pytest.fixture
def session():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev", approved=True,
    ))
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def _flight(session, name: str, days: float, waypoints: list[str]) -> Flight:
    dep = (_NOW + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    flight = Flight(
        id=name,
        user_id=DEV_USER_ID,
        route_name="_".join(w.lower() for w in waypoints),
        waypoints=waypoints,
        departure_time=dep,
        flight_duration_hours=1.0,
        created_at=_NOW,
    )
    save_flight(session, flight, DEV_USER_ID)
    return flight


@pytest.fixture
def trip_with_legs(session):
    trip = trip_storage.create_trip(session, DEV_USER_ID, "Sion")
    _flight(session, "leg1", 3, ["EGTF", "LSGS"])
    _flight(session, "leg2", 5, ["LSGS", "LFAT"])
    _flight(session, "leg3", 6, ["LFAT", "EGTF"])
    trip_storage.set_leg_trip(session, ["leg1", "leg2", "leg3"], DEV_USER_ID, trip.id)
    session.commit()
    return session.get(FlightTripRow, trip.id)


class TestAdmission:
    def test_the_trip_trigger_is_capped(self):
        """The tempting one-liner — adding "trip" to UNCAPPED_TRIGGERS — is
        precisely what would let one user's trip monopolise both refresh slots."""
        from weatherbrief.api.packs import _RefreshRegistry

        assert trip_refresh.TRIP_TRIGGER not in _RefreshRegistry.UNCAPPED_TRIGGERS

    def test_only_one_leg_is_ever_in_flight(self):
        assert trip_refresh.TRIP_REFRESH_CONCURRENCY == 1

    def test_start_records_every_remaining_leg_as_pending(self, session, trip_with_legs):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        session.commit()
        assert status.active is True
        assert status.total == 3
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert state["pending"] == ["leg1", "leg2", "leg3"]
        assert state["current"] is None

    def test_flown_legs_are_not_refreshed(self, session):
        trip = trip_storage.create_trip(session, DEV_USER_ID, "Half flown")
        _flight(session, "done", -4, ["EGTF", "LSGS"])
        _flight(session, "ahead", 4, ["LSGS", "EGTF"])
        trip_storage.set_leg_trip(session, ["done", "ahead"], DEV_USER_ID, trip.id)
        session.commit()
        # Re-running the pipeline for a leg already flown spends real money to
        # answer a question nobody is asking.
        assert trip_refresh.remaining_leg_ids(session, trip.id) == ["ahead"]

    def test_a_second_start_while_running_raises(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        session.commit()
        with pytest.raises(trip_refresh.TripRefreshBusy):
            trip_refresh.start(
                session, session.get(FlightTripRow, trip_with_legs.id),
                object(), DEV_USER_ID,
            )

    def test_a_stale_run_does_not_block_forever(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        row = session.get(FlightTripRow, trip_with_legs.id)
        # A process killed mid-chain must not disable the button permanently.
        row.refresh_started_at = _NOW - timedelta(
            seconds=trip_refresh.STALE_RUN_SECONDS + 60,
        )
        session.commit()
        status = trip_refresh.start(
            session, session.get(FlightTripRow, trip_with_legs.id),
            object(), DEV_USER_ID,
        )
        assert status.active is True


class TestProgressReadout:
    def test_says_how_many_legs_actually_had_new_data(self):
        # The gate skips legs with no new model run, and saying so is the
        # point: without it a trip refresh that legitimately did almost
        # nothing looks like one that failed.
        message = trip_refresh._progress_message(
            ["a", "b", "c"], {"a": "succeeded", "b": "skipped", "c": "succeeded"}, None,
        )
        assert "2 of 3 legs had new data" in message
        assert "1 already current" in message

    def test_reports_a_leg_that_could_not_be_refreshed(self):
        message = trip_refresh._progress_message(
            ["a", "b"], {"a": "succeeded", "b": "failed"}, None,
        )
        assert "1 could not be refreshed" in message

    def test_shows_the_leg_in_flight(self):
        message = trip_refresh._progress_message(["a", "b", "c"], {"a": "succeeded"}, "b")
        assert "leg 2 of 3" in message


class TestCoalescing:
    def test_a_member_leg_is_recognised_while_a_run_is_live(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        session.commit()
        assert trip_refresh.active_run_for_flight(session, "leg2") is not None
        # A flight outside the run is not.
        _flight(session, "loner", 2, ["EGTF", "EGLL"])
        session.commit()
        assert trip_refresh.active_run_for_flight(session, "loner") is None

    def test_no_run_means_no_coalescing(self, session, trip_with_legs):
        assert trip_refresh.active_run_for_flight(session, "leg1") is None

    def test_leg_notices_accumulate_for_one_summary(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        session.commit()
        row = session.get(FlightTripRow, trip_with_legs.id)
        trip_refresh.record_leg_notice(
            session, row, "leg1", label="EGTF → LSGS", qualified=True,
            assessment="GREEN", outlook=None, worsened_message=None, badge=1,
        )
        trip_refresh.record_leg_notice(
            session, row, "leg3", label="LFAT → EGTF", qualified=True,
            assessment="RED", outlook=None, worsened_message="was AMBER", badge=2,
        )
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        lines = trip_refresh._leg_lines(state)
        assert "EGTF → LSGS: GREEN" in lines
        assert "LFAT → EGTF: RED (was AMBER)" in lines

    def test_scheduler_run_needs_at_least_two_due_legs(self, session, trip_with_legs):
        """With one due leg there is nothing to coalesce, and the per-leg
        notification is the honest one."""
        trip_with_legs.auto_refresh = True
        session.commit()

        class Row:
            def __init__(self, flight_id):
                self.id = flight_id
                self.trip_id = trip_with_legs.id

        trip_refresh.open_scheduler_run(session, [Row("leg1")])
        assert session.get(FlightTripRow, trip_with_legs.id).refresh_id is None

        trip_refresh.open_scheduler_run(session, [Row("leg1"), Row("leg2")])
        assert session.get(FlightTripRow, trip_with_legs.id).refresh_id is not None

    def test_scheduler_run_skips_a_trip_with_auto_refresh_off(self, session, trip_with_legs):
        class Row:
            def __init__(self, flight_id):
                self.id = flight_id
                self.trip_id = trip_with_legs.id

        trip_refresh.open_scheduler_run(session, [Row("leg1"), Row("leg2")])
        assert session.get(FlightTripRow, trip_with_legs.id).refresh_id is None


class TestStorageState:
    def test_unparseable_state_reads_as_idle_rather_than_raising(self, session, trip_with_legs):
        trip_with_legs.refresh_state_json = "{not json"
        session.commit()
        assert trip_storage.read_refresh_state(trip_with_legs) == {}

    def test_clearing_the_run_drops_the_start_time(self, session, trip_with_legs):
        trip_storage.write_refresh_state(
            session, trip_with_legs, refresh_id="abc", state={"legs": []},
            started_at=_NOW,
        )
        assert trip_with_legs.refresh_started_at is not None
        trip_storage.write_refresh_state(
            session, trip_with_legs, refresh_id=None, state=None,
        )
        assert trip_with_legs.refresh_started_at is None
        assert trip_with_legs.refresh_state_json is None


class TestSchedulerTripMates:
    def test_a_due_leg_pulls_in_its_future_trip_mates(self, session, trip_with_legs):
        from weatherbrief.db.models import FlightRow
        from weatherbrief.scheduler import _with_trip_mates

        trip_with_legs.auto_refresh = True
        session.commit()

        due = [session.get(FlightRow, "leg1")]
        extended = _with_trip_mates(session, due, _NOW)
        assert {row.id for row in extended} == {"leg1", "leg2", "leg3"}

    def test_trip_mates_are_not_pulled_in_when_the_trip_opts_out(self, session, trip_with_legs):
        from weatherbrief.db.models import FlightRow
        from weatherbrief.scheduler import _with_trip_mates

        due = [session.get(FlightRow, "leg1")]
        assert _with_trip_mates(session, due, _NOW) == due

    def test_flown_trip_mates_are_never_pulled_in(self, session):
        from weatherbrief.db.models import FlightRow
        from weatherbrief.scheduler import _with_trip_mates

        trip = trip_storage.create_trip(session, DEV_USER_ID, "Half flown")
        _flight(session, "past", -4, ["EGTF", "LSGS"])
        _flight(session, "future", 4, ["LSGS", "EGTF"])
        trip_storage.set_leg_trip(session, ["past", "future"], DEV_USER_ID, trip.id)
        row = session.get(FlightTripRow, trip.id)
        row.auto_refresh = True
        session.commit()

        due = [session.get(FlightRow, "future")]
        assert {r.id for r in _with_trip_mates(session, due, _NOW)} == {"future"}


class TestJsonState:
    def test_state_round_trips_through_the_column(self, session, trip_with_legs):
        payload = {"legs": ["a"], "pending": [], "results": {"a": "succeeded"}}
        trip_storage.write_refresh_state(
            session, trip_with_legs, refresh_id="run1", state=payload, started_at=_NOW,
        )
        session.commit()
        row = session.get(FlightTripRow, trip_with_legs.id)
        assert json.loads(row.refresh_state_json) == payload
        assert trip_storage.read_refresh_state(row) == payload
