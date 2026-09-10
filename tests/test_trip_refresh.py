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
def session(monkeypatch):
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    # The boot-recovery helpers open their own sessions (they run before any
    # request exists), so the module's ``SessionLocal`` is what has to point at
    # the test engine — same wiring as tests/test_refresh_durability.py.
    monkeypatch.setattr(trip_refresh, "SessionLocal", TestSession)
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
        with pytest.raises(trip_refresh.TripRefreshBusy):
            trip_refresh.start(
                session, session.get(FlightTripRow, trip_with_legs.id),
                object(), DEV_USER_ID,
            )

    def test_starts_commit_happens_inside_the_state_lock(self):
        """The sequential double-press test above cannot see this property.

        Both calls share one session, so the second observes the first's write
        whether or not the commit was inside ``_state_lock`` — it would keep
        passing through exactly the regression it looks like it guards. A real
        two-thread race cannot discriminate either: ``make_app_engine`` uses a
        ``StaticPool``, so both threads share one connection and therefore one
        transaction, and the second caller sees the first's *uncommitted* write
        regardless. (A file-backed WAL engine would isolate them, at the price
        of a timing-dependent test in CI, which is worse than none.)

        So the property is asserted where it actually lives: lexically. Every
        ``commit()`` in ``start`` must sit inside the ``with _state_lock``
        block, which is what makes a concurrent caller's read see the write.
        """
        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(inspect.getsource(trip_refresh.start))
        fn = ast.parse(src).body[0]

        def commits(node):
            return [
                n for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "commit"
            ]

        guarded = [
            c
            for stmt in ast.walk(fn)
            if isinstance(stmt, ast.With)
            and any(
                isinstance(i.context_expr, ast.Name)
                and i.context_expr.id == "_state_lock"
                for i in stmt.items
            )
            for c in commits(stmt)
        ]
        all_commits = commits(fn)
        assert all_commits, "start() no longer commits — has the state write moved?"
        outside = [c for c in all_commits if c not in guarded]
        assert not outside, (
            f"{len(outside)} commit(s) in start() are outside _state_lock; a "
            "concurrent press would then read a row without the run marker and "
            "open a second run over the same trip"
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


class TestRunFencing:
    """A task must only ever act on the run it was submitted for.

    Without the fence, a submission left over from a superseded run could claim
    a leg alongside the live chain — two legs of one trip in flight at once,
    holding both of the process's two refresh slots.
    """

    def test_claiming_under_a_stale_run_id_is_refused(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        row, flight_id = trip_refresh._claim_next(session, trip_with_legs.id, "not-the-run")
        assert (row, flight_id) == (None, None)
        # And the live run is untouched.
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert state["pending"] == ["leg1", "leg2", "leg3"]
        assert state["current"] is None

    def test_recording_under_a_stale_run_id_is_refused(self, session, trip_with_legs):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        trip_refresh._claim_next(session, trip_with_legs.id, status.refresh_id)
        # None, not {} — and the distinction is the whole point: an empty state
        # would read as "chain complete" and close out the live run.
        assert trip_refresh._record_result(
            session, trip_with_legs.id, "leg1", "succeeded", "not-the-run",
        ) is None
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert state["results"] == {}

    def test_a_leftover_task_from_a_superseded_run_finishes_nothing(
        self, session, trip_with_legs, monkeypatch,
    ):
        """The real race, end to end — not a hand-typed stale id.

        Run A starts, goes stale, run B starts. A's leftover task then completes
        its leg. It must neither record into B nor *close B out*: treating its
        fenced-out result as "chain complete" would fire B's coalesced
        notification early on incomplete results and regenerate its AI summary.
        """
        run_a = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID).refresh_id
        row = session.get(FlightTripRow, trip_with_legs.id)
        row.refresh_started_at = _NOW - timedelta(
            seconds=trip_refresh.STALE_RUN_SECONDS + 60,
        )
        session.commit()
        run_b = trip_refresh.start(
            session, session.get(FlightTripRow, trip_with_legs.id), object(), DEV_USER_ID,
        ).refresh_id
        assert run_a != run_b

        finished: list[str] = []
        monkeypatch.setattr(
            trip_refresh, "_finish", lambda *a, **k: finished.append("finished"),
        )

        # A's leftover task reports in under its own (now superseded) run id.
        state = trip_refresh._record_result(
            session, trip_with_legs.id, "leg1", "succeeded", run_a,
        )
        assert state is None, "a fenced-out result must be distinguishable from a done chain"
        assert finished == [], "a fenced-out task must not close out the live run"

        # Run B is untouched: still every leg pending, no results recorded.
        live = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert live["pending"] == ["leg1", "leg2", "leg3"]
        assert live["results"] == {}

    def test_a_completed_chain_is_still_distinguishable_from_a_fenced_one(
        self, session, trip_with_legs,
    ):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        for leg in ("leg1", "leg2", "leg3"):
            trip_refresh._claim_next(session, trip_with_legs.id, status.refresh_id)
            state = trip_refresh._record_result(
                session, trip_with_legs.id, leg, "succeeded", status.refresh_id,
            )
        # A real completion returns a state with an empty `pending` — which is
        # what tells the caller to close the run out.
        assert state is not None
        assert state["pending"] == []

    def test_the_live_run_id_claims_normally(self, session, trip_with_legs):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        _row, flight_id = trip_refresh._claim_next(
            session, trip_with_legs.id, status.refresh_id,
        )
        assert flight_id == "leg1"
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert state["current"] == "leg1"
        assert state["pending"] == ["leg2", "leg3"]


class TestFinishIsFenced:
    """`_finish` closing the *wrong* run was the round-3 Critical.

    `_record_result` commits `pending=[]` and releases the lock; `start()`'s
    busy check then sees an idle trip and can open a new run. An unfenced
    `_finish` arriving late closes that one — dropping the first run's
    notification and killing the second before it claims a leg.
    """

    def test_finish_under_a_stale_run_id_leaves_the_live_run_alone(
        self, session, trip_with_legs,
    ):
        run_a = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID).refresh_id
        row = session.get(FlightTripRow, trip_with_legs.id)
        row.refresh_started_at = _NOW - timedelta(
            seconds=trip_refresh.STALE_RUN_SECONDS + 60,
        )
        session.commit()
        run_b = trip_refresh.start(
            session, session.get(FlightTripRow, trip_with_legs.id), object(), DEV_USER_ID,
        ).refresh_id

        trip_refresh._finish(session, trip_with_legs.id, DEV_USER_ID, run_a)

        live = session.get(FlightTripRow, trip_with_legs.id)
        assert live.refresh_id == run_b, "the live run must survive a stale finish"
        state = trip_storage.read_refresh_state(live)
        assert state["pending"] == ["leg1", "leg2", "leg3"]

    def test_finish_under_the_live_run_id_closes_it(self, session, trip_with_legs, monkeypatch):
        monkeypatch.setattr(trip_refresh, "_regenerate_ai_summary", lambda *a: None)
        monkeypatch.setattr(trip_refresh, "_send_coalesced", lambda *a: None)
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)

        trip_refresh._finish(session, trip_with_legs.id, DEV_USER_ID, status.refresh_id)

        assert session.get(FlightTripRow, trip_with_legs.id).refresh_id is None

    def test_every_mutator_goes_through_the_run_scope(self):
        """Fencing must not be opt-in per function.

        The previous version of this test named three functions explicitly —
        and so could not catch the *fourth* mutator, `record_leg_notice`, which
        is exactly the failure it existed to prevent. A test that hard-codes
        the list it is meant to police is not a structural guarantee.

        So the list is now *discovered*: any module-level function that writes
        refresh state must go through the gate. A new mutator added without it
        fails here rather than in production.
        """
        import inspect

        assert hasattr(trip_refresh, "_run_scope")

        offenders = []
        for name, fn in vars(trip_refresh).items():
            if not inspect.isfunction(fn) or fn.__module__ != trip_refresh.__name__:
                continue
            try:
                body = inspect.getsource(fn)
            except OSError:  # pragma: no cover
                continue
            if "write_refresh_state(" not in body:
                continue
            # `_run_scope` itself, `start` and the boot-recovery helpers hold
            # the lock directly and are documented exceptions: they legitimately
            # act with no run of their own (opening one, or reconciling at boot).
            if name in {"start", "_requeue_interrupted", "open_scheduler_run", "note_leg_done"}:
                assert "_state_lock" in body, f"{name} must at least hold the lock"
                continue
            if "_run_scope(" not in body:
                offenders.append(f"{name}: no _run_scope")
                continue
            # Substring presence is not the property. `record_leg_notice`
            # called `_run_scope(db, trip_row.id, None)` — "whatever run is
            # live" — and this test passed it, twice. The fence is only a fence
            # if the caller names the run it observed, so the run-id argument
            # is inspected rather than assumed.
            import ast
            import textwrap

            for call in ast.walk(ast.parse(textwrap.dedent(body))):
                if not isinstance(call, ast.Call):
                    continue
                target = call.func
                if not (isinstance(target, ast.Name) and target.id == "_run_scope"):
                    continue
                if len(call.args) < 3:
                    offenders.append(f"{name}: _run_scope called without a run id")
                elif isinstance(call.args[2], ast.Constant) and call.args[2].value is None:
                    offenders.append(f"{name}: passes a literal None as the run id")

        assert not offenders, (
            f"these mutate refresh state without a real fence: {offenders}"
        )


class TestFullChainFiresOnce:
    """The chain's two terminal side effects fire once, at the end, not per leg.

    Every other test in this module monkeypatches ``_regenerate_ai_summary`` and
    ``_send_coalesced`` to no-ops, so a regression that stopped either call —
    or moved it inside the per-leg path, re-billing Haiku and pushing three
    times for one chain — passes the whole suite. This drives a real chain to
    completion and counts.
    """

    def _spies(self, monkeypatch):
        calls: dict[str, list] = {"ai": [], "send": []}
        monkeypatch.setattr(
            trip_refresh, "_regenerate_ai_summary",
            lambda db, trip_id, user_id: calls["ai"].append(trip_id),
        )
        monkeypatch.setattr(
            trip_refresh, "_send_coalesced",
            lambda db, trip_id, trip_name, state, user_id: calls["send"].append(state),
        )
        return calls

    def test_a_full_chain_fires_each_exactly_once(
        self, session, trip_with_legs, monkeypatch,
    ):
        calls = self._spies(monkeypatch)
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)

        trip_refresh.note_leg_done(session, trip_with_legs.id, "leg1", "succeeded")
        trip_refresh.note_leg_done(session, trip_with_legs.id, "leg2", "succeeded")
        # Positive control: an absence assertion is worthless without one. If
        # either call had moved into the per-leg path these would already be 2.
        assert calls["ai"] == [], "the AI paragraph must not regenerate per leg"
        assert calls["send"] == [], "the notification must not fire per leg"

        trip_refresh.note_leg_done(session, trip_with_legs.id, "leg3", "succeeded")

        assert calls["ai"] == [trip_with_legs.id], "exactly one regeneration per chain"
        assert len(calls["send"]) == 1, "exactly one coalesced notification per chain"

    def test_the_coalesced_send_gets_every_legs_result(
        self, session, trip_with_legs, monkeypatch,
    ):
        """The state handed to the notification carries the whole chain.

        Guards the shape the summary line is built from — a send that fired once
        but only knew about the last leg would satisfy the count assertion above.
        """
        calls = self._spies(monkeypatch)
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        for flight_id, outcome in (
            ("leg1", "succeeded"), ("leg2", "skipped"), ("leg3", "succeeded"),
        ):
            trip_refresh.note_leg_done(session, trip_with_legs.id, flight_id, outcome)

        state = calls["send"][0]
        assert state["results"] == {
            "leg1": "succeeded", "leg2": "skipped", "leg3": "succeeded",
        }


class TestSingleLegGuard:
    """A manual per-flight refresh must yield to a live trip run too.

    Round 3 filtered only the scheduler's due-leg list. A pilot pressing
    Refresh on an individual trip-mate's briefing page went through an unfenced
    path and could put a second leg of the same trip in flight.
    """

    def test_a_queued_leg_is_reported_as_claimed(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        assert trip_refresh.leg_is_claimed(session, "leg2") is True

    def test_an_unrelated_flight_is_not_claimed(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        _flight(session, "loner", 2, ["EGTF", "EGLL"])
        session.commit()
        assert trip_refresh.leg_is_claimed(session, "loner") is False

    def test_nothing_is_claimed_with_no_live_run(self, session, trip_with_legs):
        assert trip_refresh.leg_is_claimed(session, "leg1") is False


class TestBootRecovery:
    """A container killed mid-chain must not strand the remaining legs.

    The leg-level resume in ``tasks/refresh_resume.py`` recovers the one leg
    that was in flight; nothing there knows about trips, so without this pass
    the rest of the chain never runs and every gathered notice is discarded when
    the staleness window lapses.
    """

    def test_an_open_run_is_visible_at_boot(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        assert any(t == trip_with_legs.id for t, _run, _user in trip_refresh._orphaned_runs())

    def test_no_open_runs_when_idle(self, session, trip_with_legs):
        assert trip_refresh._orphaned_runs() == []

    def test_the_interrupted_leg_goes_back_to_the_head_of_pending(self, session, trip_with_legs):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        trip_refresh._claim_next(session, trip_with_legs.id, status.refresh_id)
        session.commit()

        assert trip_refresh._requeue_interrupted(trip_with_legs.id, status.refresh_id) is True
        session.expire_all()
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        # Re-queued, not dropped: the refresh gate makes a redundant re-run a
        # cheap no-op, while a dropped leg would silently never be briefed.
        assert state["pending"] == ["leg1", "leg2", "leg3"]
        assert state["current"] is None

    def test_resume_restarts_a_chain_that_still_has_legs(
        self, session, trip_with_legs, monkeypatch,
    ):
        """End-to-end through ``run_trip_refresh_resume``, both branches below."""
        import asyncio

        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        trip_refresh._claim_next(session, trip_with_legs.id, status.refresh_id)
        session.commit()

        submitted: list[tuple] = []
        monkeypatch.setattr(trip_refresh, "_submit_next", lambda *a: submitted.append(a))
        monkeypatch.setattr(trip_refresh, "RESUME_STARTUP_DELAY_SECONDS", 0)

        asyncio.run(trip_refresh.run_trip_refresh_resume(object()))

        assert len(submitted) == 1
        trip_id, _app_state, _user, run_id = submitted[0]
        assert trip_id == trip_with_legs.id
        # Restarted under the *original* run id, so the fence still holds.
        assert run_id == status.refresh_id

    def test_resume_closes_out_a_chain_with_nothing_left(
        self, session, trip_with_legs, monkeypatch,
    ):
        import asyncio

        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        row = session.get(FlightTripRow, trip_with_legs.id)
        state = trip_storage.read_refresh_state(row)
        state["pending"] = []
        state["current"] = None
        trip_storage.write_refresh_state(
            session, row, refresh_id=status.refresh_id, state=state,
        )
        session.commit()

        closed: list[tuple] = []
        # Takes the run id too: the boot pass must close out the run it
        # actually inspected, not "whatever is live by the time it gets there".
        monkeypatch.setattr(
            trip_refresh, "_finish_in_new_session",
            lambda trip_id, user_id, run_id=None: closed.append((trip_id, run_id)),
        )
        monkeypatch.setattr(trip_refresh, "_submit_next", lambda *a: pytest.fail("should not resubmit"))
        monkeypatch.setattr(trip_refresh, "RESUME_STARTUP_DELAY_SECONDS", 0)

        asyncio.run(trip_refresh.run_trip_refresh_resume(object()))
        # Closed out so the coalesced notification fires rather than being
        # discarded when the staleness window lapses — under its own run id.
        assert closed == [(trip_with_legs.id, status.refresh_id)]

    def test_a_run_with_nothing_left_reports_no_work(self, session, trip_with_legs):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        row = session.get(FlightTripRow, trip_with_legs.id)
        state = trip_storage.read_refresh_state(row)
        state["pending"] = []
        state["current"] = None
        trip_storage.write_refresh_state(
            session, row, refresh_id=status.refresh_id, state=state,
        )
        session.commit()
        assert trip_refresh._requeue_interrupted(trip_with_legs.id, status.refresh_id) is False


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
        assert trip_refresh.active_run_for_flight(session, "leg2") is not None
        # A flight outside the run is not.
        _flight(session, "loner", 2, ["EGTF", "EGLL"])
        session.commit()
        assert trip_refresh.active_run_for_flight(session, "loner") is None

    def test_no_run_means_no_coalescing(self, session, trip_with_legs):
        assert trip_refresh.active_run_for_flight(session, "leg1") is None

    def test_leg_notices_accumulate_for_one_summary(self, session, trip_with_legs):
        run = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID).refresh_id
        row = session.get(FlightTripRow, trip_with_legs.id)
        trip_refresh.record_leg_notice(
            session, row, "leg1", run_id=run, label="EGTF → LSGS", qualified=True,
            assessment="GREEN", outlook=None, worsened_message=None, badge=1,
        )
        trip_refresh.record_leg_notice(
            session, row, "leg3", run_id=run, label="LFAT → EGTF", qualified=True,
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


class TestSchedulerAdmitsTripLegs:
    """The trip switch has to count on its own.

    ``_find_due_flights`` used to filter on the per-leg ``auto_refresh`` before
    ``_with_trip_mates`` ever ran, and a flight is created with that flag off —
    joining a trip does not turn it on, and neither ``PATCH /trips/{id}`` nor
    ``add_legs`` cascades. So a trip with auto-refresh on and every leg off had
    nothing to hand the trip-mate step and simply never refreshed, while the
    briefing page disabled the only control that could have switched a leg on.
    """

    def _due_ids(self, session):
        from weatherbrief.scheduler import _find_due_flights

        return {row.id for row in _find_due_flights(session)}

    def _refreshed_two_days_ago(self, session, *leg_ids):
        """Anchor the regular slot on a past date, whatever the wall clock says.

        ``_next_due_at`` bases an untouched leg's slot on *today* at
        ``departure − 1 h``, which is still ahead of ``now`` for an hour of the
        run that has not come round yet — so a bare clock-relative fixture
        would pass or fail depending on the time of day.
        """
        from weatherbrief.db.models import FlightRow

        for leg_id in leg_ids:
            session.get(FlightRow, leg_id).last_auto_refresh_at = _NOW - timedelta(days=2)
        session.commit()

    def test_the_trip_switch_alone_makes_its_legs_due(self, session, trip_with_legs):
        self._refreshed_two_days_ago(session, "leg1", "leg2", "leg3")
        trip_with_legs.auto_refresh = True
        session.commit()
        # Every leg still has its own ``auto_refresh`` off — as a created
        # flight does, and as joining a trip leaves it.
        assert self._due_ids(session) == {"leg1", "leg2", "leg3"}

    def test_a_trip_that_opts_out_leaves_its_legs_alone(self, session, trip_with_legs):
        self._refreshed_two_days_ago(session, "leg1", "leg2", "leg3")
        assert self._due_ids(session) == set()

    def test_an_ungrouped_flight_still_needs_its_own_switch(self, session):
        from weatherbrief.db.models import FlightRow

        _flight(session, "solo", 3, ["EGTF", "LSGS"])
        session.commit()
        assert self._due_ids(session) == set()

        row = session.get(FlightRow, "solo")
        row.auto_refresh = True
        row.auto_refresh_hour = _NOW.hour
        session.commit()
        assert self._due_ids(session) == {"solo"}

    def test_a_leg_switched_on_inside_an_opted_out_trip_still_pulls_the_chain(
        self, session, trip_with_legs,
    ):
        """Per-leg on/off keeps working: it is what the leg reverts to if it
        later leaves the trip, so the web control must not clobber it."""
        from weatherbrief.db.models import FlightRow

        leg = session.get(FlightRow, "leg2")
        leg.auto_refresh = True
        leg.auto_refresh_hour = _NOW.hour
        session.commit()
        # Trip opted out — the due leg runs alone, no mates pulled in.
        assert self._due_ids(session) == {"leg2"}

        trip_with_legs.auto_refresh = True
        session.commit()
        assert self._due_ids(session) == {"leg1", "leg2", "leg3"}

    def test_the_earliest_leg_hour_is_what_fires_the_chain(self, session, trip_with_legs):
        """The whole point of keeping the hour per-leg: whichever leg comes due
        first drags the rest along, so setting an early hour on any one leg
        moves the trip. Checked on ``_next_due_at`` because it takes ``now``
        explicitly — reading the wall clock would make the hours wrap-sensitive.
        """
        from weatherbrief.db.models import FlightRow
        from weatherbrief.scheduler import _flight_start_dt, _next_due_at

        noon = _NOW.replace(hour=12, minute=0, second=0, microsecond=0)
        hours = {"leg1": 18, "leg2": 7, "leg3": 15}
        for leg_id, hour in hours.items():
            session.get(FlightRow, leg_id).auto_refresh_hour = hour
        session.commit()

        due_at = {}
        for leg_id in hours:
            row = session.get(FlightRow, leg_id)
            due_at[leg_id] = _next_due_at(row, _flight_start_dt(row), noon)

        # leg2 at 07:00Z is the earliest slot of the day, so it is the leg that
        # brings the whole chain forward.
        assert min(due_at, key=lambda k: due_at[k]) == "leg2"
        assert due_at["leg2"].hour == 7


class TestSchedulerYieldsToTheDriver:
    """A leg queued by a manual trip refresh is the driver's, not the scheduler's.

    Such a leg is not yet in ``refresh_registry`` — nothing claims it until
    ``_claim_next`` picks it up — so the scheduler's admission check cannot see
    it. Running it there, under the uncapped "scheduler" trigger, would put a
    second leg of the same trip in flight beside the driver's current one.
    """

    def test_pending_and_current_legs_are_reported_as_claimed(self, session, trip_with_legs):
        status = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        trip_refresh._claim_next(session, trip_with_legs.id, status.refresh_id)
        session.commit()
        claimed = trip_refresh.legs_claimed_by_a_live_run(
            session, ["leg1", "leg2", "leg3"],
        )
        assert claimed == {"leg1", "leg2", "leg3"}

    def test_nothing_is_claimed_when_no_run_is_live(self, session, trip_with_legs):
        assert trip_refresh.legs_claimed_by_a_live_run(
            session, ["leg1", "leg2", "leg3"],
        ) == set()

    def test_a_stale_run_does_not_hold_its_legs(self, session, trip_with_legs):
        trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID)
        row = session.get(FlightTripRow, trip_with_legs.id)
        row.refresh_started_at = _NOW - timedelta(
            seconds=trip_refresh.STALE_RUN_SECONDS + 60,
        )
        session.commit()
        assert trip_refresh.legs_claimed_by_a_live_run(session, ["leg1"]) == set()

    def test_the_scheduler_drops_legs_the_driver_owns(self, session, trip_with_legs):
        from weatherbrief.db.models import FlightRow
        from weatherbrief.scheduler import _with_trip_mates

        trip_with_legs.auto_refresh = True
        session.commit()
        status = trip_refresh.start(
            session, session.get(FlightTripRow, trip_with_legs.id), object(), DEV_USER_ID,
        )
        assert status.active

        due = [session.get(FlightRow, "leg1")]
        assert _with_trip_mates(session, due, _NOW) == []


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


class TestLegNoticesBelongToTheirRun:
    """A notice must land in the run its leg actually ran under.

    ``record_leg_notice`` fenced on "whatever run is live" and did not check
    leg membership, so a notice from a closed run could be written into the
    next one — and `_send_coalesced` read the whole notices dict, so a run
    none of whose own legs qualified could fire a push on the strength of it.
    """

    def _notice(self, session, trip_id, flight_id, run_id, *, qualified=True):
        trip_refresh.record_leg_notice(
            session, session.get(FlightTripRow, trip_id), flight_id,
            run_id=run_id,
            label=flight_id, qualified=qualified,
            assessment="GREEN", outlook=None, worsened_message=None, badge=1,
        )

    def test_a_notice_from_a_superseded_run_is_dropped(self, session, trip_with_legs):
        run_a = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID).refresh_id
        row = session.get(FlightTripRow, trip_with_legs.id)
        row.refresh_started_at = _NOW - timedelta(
            seconds=trip_refresh.STALE_RUN_SECONDS + 60,
        )
        session.commit()
        run_b = trip_refresh.start(
            session, session.get(FlightTripRow, trip_with_legs.id), object(), DEV_USER_ID,
        ).refresh_id
        assert run_a != run_b

        self._notice(session, trip_with_legs.id, "leg1", run_a)

        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert state["notices"] == {}, "run B must not inherit run A's notice"

    def test_a_leg_outside_the_run_is_dropped(self, session, trip_with_legs):
        run = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID).refresh_id
        _flight(session, "loner", 2, ["EGTF", "EGLL"])
        session.commit()

        self._notice(session, trip_with_legs.id, "loner", run)

        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert "loner" not in state["notices"]

    def test_the_live_run_records_its_own_leg(self, session, trip_with_legs):
        run = trip_refresh.start(session, trip_with_legs, object(), DEV_USER_ID).refresh_id
        self._notice(session, trip_with_legs.id, "leg1", run)
        state = trip_storage.read_refresh_state(session.get(FlightTripRow, trip_with_legs.id))
        assert state["notices"]["leg1"]["qualified"] is True

    def _drive_coalesced(self, session, trip_id, monkeypatch, notices, legs):
        """Run `_send_coalesced` with delivery captured at its real seams."""
        sent: list[str] = []
        import weatherbrief.notify.push as push_mod

        monkeypatch.setattr(
            push_mod, "send_trip_push", lambda *a, **k: sent.append("push"),
        )
        monkeypatch.setattr(
            trip_refresh, "_headline_for", lambda *a, **k: "headline",
        )
        monkeypatch.setattr(
            "weatherbrief.api.preferences.load_notify_prefs",
            lambda *a, **k: {"notify_push": True, "notify_email": False},
        )
        trip_refresh._send_coalesced(
            session, trip_id, "Sion",
            {"legs": legs, "results": {}, "notices": notices},
            DEV_USER_ID,
        )
        return sent

    def test_a_qualifying_leg_of_this_run_does_notify(
        self, session, trip_with_legs, monkeypatch,
    ):
        """Positive control: without this the negative test below proves nothing."""
        sent = self._drive_coalesced(
            session, trip_with_legs.id, monkeypatch,
            notices={"leg1": {"label": "leg1", "qualified": True, "badge": 1}},
            legs=["leg1"],
        )
        assert sent == ["push"]

    def test_a_stray_notice_cannot_trigger_the_coalesced_push(
        self, session, trip_with_legs, monkeypatch,
    ):
        """Defence in depth: even a leaked notice must not decide delivery."""
        sent = self._drive_coalesced(
            session, trip_with_legs.id, monkeypatch,
            notices={
                "leg1": {"label": "leg1", "qualified": False, "badge": 0},
                # Not one of this run's legs.
                "ghost": {"label": "ghost", "qualified": True, "badge": 9},
            },
            legs=["leg1"],
        )
        assert sent == [], "a notice outside state['legs'] must not decide delivery"


class TestAiSummaryIsNotRebilled:
    def test_the_post_refresh_regeneration_respects_the_cache(
        self, session, trip_with_legs, monkeypatch,
    ):
        """A chain can complete having refreshed nothing.

        The refresh gate skips a leg with no new model run, so "the chain
        finished" is not evidence the inputs changed. Forcing regeneration
        re-billed Haiku for provably identical input.
        """
        calls: list[str] = []

        from weatherbrief.digest import trip_summary as ts

        def _fake_generate(*a, **k):
            calls.append("generate")
            return None, None

        monkeypatch.setattr(ts, "generate", _fake_generate)
        monkeypatch.setattr(ts, "legs_allow_ai", lambda *a, **k: True)

        trip_refresh._regenerate_ai_summary(session, trip_with_legs.id, DEV_USER_ID)
        assert len(calls) == 1, "first pass must generate"

        # Nothing about the legs has changed since.
        trip_refresh._regenerate_ai_summary(session, trip_with_legs.id, DEV_USER_ID)
        assert len(calls) == 1, "unchanged inputs must not pay twice"
