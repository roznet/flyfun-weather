"""Deadlock/lock-wait retry helper (``weatherbrief.db.retry``).

Why: with several writer roles hitting MySQL concurrently (web endpoints,
scheduler refresh loop, boot resume, METAR ingest, verification, retention,
analytics rollup), InnoDB errno 1213 (deadlock victim) / 1205 (lock wait
timeout) are transient facts of life — without a retry they surface as
failed requests/cycles. SQLite never raises them for real, so the tests
inject fake PyMySQL-shaped ``OperationalError``s; that keeps the suite
DB-agnostic while exercising the exact detection shapes production hits.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from flyfun_common.db import DEV_USER_ID
from flyfun_common.db.models import UserRow
from weatherbrief.db.retry import db_retry
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage import refresh_jobs
from weatherbrief.storage.flights import load_pack_meta, save_flight, save_pack_meta


class _FakeDBAPIError(Exception):
    """Stands in for ``pymysql.err.OperationalError``: ``args[0]`` is the errno."""


def _lock_error(errno: int, message: str) -> OperationalError:
    return OperationalError("INSERT INTO t VALUES (1)", {}, _FakeDBAPIError(errno, message))


def _deadlock() -> OperationalError:
    return _lock_error(
        1213, "Deadlock found when trying to get lock; try restarting transaction"
    )


def _lock_wait_timeout() -> OperationalError:
    return _lock_error(1205, "Lock wait timeout exceeded; try restarting transaction")


def _savepoint_rollback_error() -> OperationalError:
    """The masking shape: a failed ``ROLLBACK TO SAVEPOINT`` (MySQL 1305),
    which SQLAlchemy re-raises in place of the original error."""
    return OperationalError(
        "ROLLBACK TO SAVEPOINT sa_savepoint_1",
        {},
        _FakeDBAPIError(1305, "SAVEPOINT sa_savepoint_1 does not exist"),
    )


def _deadlock_masked_two_levels_deep() -> OperationalError:
    """The shape a 1213 takes when the victim dies inside begin_nested: the
    server already rolled the transaction back, the savepoint rollback fails
    (1305) and is re-raised — the real 1213 survives only on the
    ``__context__`` chain, here two levels deep."""
    try:
        raise _deadlock()
    except OperationalError:
        try:
            raise RuntimeError("savepoint rollback bookkeeping")
        except RuntimeError:
            raise _savepoint_rollback_error()


class TestDbRetry:
    @pytest.mark.parametrize("errno_factory", [_deadlock, _lock_wait_timeout])
    def test_retryable_lock_error_succeeds_on_second_attempt(
        self, db_session, errno_factory
    ):
        calls = 0

        def block() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise errno_factory()
            return "ok"

        for attempt in db_retry(db_session, base_delay_s=0):
            with attempt:
                result = block()

        assert result == "ok"
        assert calls == 2

    def test_gives_up_after_max_attempts_and_reraises(self, db_session):
        calls = 0

        def block() -> None:
            nonlocal calls
            calls += 1
            raise _deadlock()

        with pytest.raises(OperationalError):
            for attempt in db_retry(db_session, max_attempts=3, base_delay_s=0):
                with attempt:
                    block()
        assert calls == 3

    def test_non_deadlock_operational_error_not_retried(self, db_session):
        calls = 0
        gone_away = OperationalError(
            "SELECT 1", {}, _FakeDBAPIError(2006, "MySQL server has gone away")
        )

        def block() -> None:
            nonlocal calls
            calls += 1
            raise gone_away

        with pytest.raises(OperationalError) as exc_info:
            for attempt in db_retry(db_session, base_delay_s=0):
                with attempt:
                    block()
        assert exc_info.value is gone_away
        assert calls == 1

    def test_integrity_error_propagates_untouched(self, db_session):
        calls = 0
        duplicate = IntegrityError(
            "INSERT INTO t VALUES (1)", {}, _FakeDBAPIError(1062, "Duplicate entry")
        )

        def block() -> None:
            nonlocal calls
            calls += 1
            raise duplicate

        with pytest.raises(IntegrityError) as exc_info:
            for attempt in db_retry(db_session, base_delay_s=0):
                with attempt:
                    block()
        assert exc_info.value is duplicate
        assert calls == 1

    def test_detection_falls_back_to_message_text(self, db_session):
        """DBAPIs that don't expose the server errno as ``args[0]`` (unlike
        PyMySQL) still match on the message text."""
        errno_less = OperationalError(
            "INSERT INTO t VALUES (1)",
            {},
            _FakeDBAPIError("Deadlock found when trying to get lock"),
        )
        calls = 0

        def block() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise errno_less

        for attempt in db_retry(db_session, base_delay_s=0):
            with attempt:
                block()
        assert calls == 2

    def test_deadlock_two_levels_deep_in_context_chain_is_retried(self, db_session):
        """A 1213 raised inside ``session.begin_nested()`` arrives masked by
        the savepoint-rollback 1305; the real deadlock sits two levels deep on
        the ``__context__`` chain and must still be detected."""
        calls = 0

        def block() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _deadlock_masked_two_levels_deep()

        for attempt in db_retry(db_session, base_delay_s=0):
            with attempt:
                block()
        assert calls == 2

    def test_deadlock_attached_via_cause_is_retried(self, db_session):
        """Explicit ``raise ... from`` wrappers (``__cause__``) are walked too."""
        wrapped = _savepoint_rollback_error()
        wrapped.__cause__ = _deadlock()
        calls = 0

        def block() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise wrapped

        for attempt in db_retry(db_session, base_delay_s=0):
            with attempt:
                block()
        assert calls == 2

    def test_savepoint_error_without_lock_error_in_chain_not_retried(self, db_session):
        """A bare 1305 — no 1213/1205 anywhere in the chain — is NOT retried."""
        alone = _savepoint_rollback_error()
        calls = 0

        def block() -> None:
            nonlocal calls
            calls += 1
            raise alone

        with pytest.raises(OperationalError) as exc_info:
            for attempt in db_retry(db_session, base_delay_s=0):
                with attempt:
                    block()
        assert exc_info.value is alone
        assert calls == 1

    def test_session_rolled_back_between_attempts(self, db_session, monkeypatch):
        events: list[str] = []
        real_rollback = db_session.rollback

        def spy_rollback():
            events.append("rollback")
            return real_rollback()

        monkeypatch.setattr(db_session, "rollback", spy_rollback)

        def block() -> None:
            events.append("block")
            if events == ["block"]:
                raise _deadlock()

        for attempt in db_retry(db_session, base_delay_s=0):
            with attempt:
                block()

        assert events == ["block", "rollback", "block"]

    def test_backoff_grows_exponentially(self, db_session, monkeypatch):
        """Sleeps follow base × 2^(attempt-1) × jitter, jitter in [0.5, 1.5)."""
        from weatherbrief.db import retry as retry_mod

        sleeps: list[float] = []
        monkeypatch.setattr(retry_mod.time, "sleep", sleeps.append)
        monkeypatch.setattr(retry_mod.random, "random", lambda: 0.0)

        def block() -> None:
            raise _deadlock()

        with pytest.raises(OperationalError):
            for attempt in db_retry(db_session, max_attempts=3, base_delay_s=0.05):
                with attempt:
                    block()
        assert sleeps == [0.05 * 0.5, 0.1 * 0.5]


# --- Applied-path smoke tests ---


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


def _meta(flight_id: str, **overrides) -> BriefingPackMeta:
    base = {
        "flight_id": flight_id,
        "fetch_timestamp": datetime(2026, 2, 19, 18, 0, 0, tzinfo=timezone.utc),
        "days_out": 2,
        "has_gramet": True,
        "has_digest": False,
        "assessment": "AMBER",
        "assessment_reason": "provisional",
    }
    return BriefingPackMeta(**(base | overrides))


class TestAppliedPaths:
    def test_save_pack_meta_survives_one_injected_deadlock(
        self, sample_flight, monkeypatch
    ):
        """One injected 1213 at the insert flush: the attempt rolls back, the
        SAVEPOINT race guard re-runs inside the retry, and the pack lands.

        Runs on a private engine (same reason as the commit test in
        test_pack_meta_race): the retry's ``session.rollback()`` discards ALL
        uncommitted work, so the parent flight must be committed first — and
        committing on the shared session-scoped engine would leak the
        dev-user row into later tests.
        """
        from conftest import make_app_engine

        engine = make_app_engine()
        session = sessionmaker(bind=engine)()
        try:
            session.add(
                UserRow(
                    id=DEV_USER_ID,
                    provider="local",
                    provider_sub="dev",
                    email="dev@localhost",
                    display_name="Dev User",
                    approved=True,
                )
            )
            save_flight(session, sample_flight, DEV_USER_ID)
            session.commit()

            real_flush = session.flush
            fired = False

            def flaky_flush(*args, **kwargs):
                nonlocal fired
                if not fired:
                    fired = True
                    raise _deadlock()
                return real_flush(*args, **kwargs)

            monkeypatch.setattr(session, "flush", flaky_flush)

            meta = _meta(sample_flight.id)
            save_pack_meta(session, meta)

            loaded = load_pack_meta(session, sample_flight.id, meta.fetch_timestamp)
            assert loaded.assessment == "AMBER"
        finally:
            session.close()
            engine.dispose()

    def test_refresh_job_write_through_still_swallows_exhausted_retries(
        self, monkeypatch
    ):
        """``_best_effort`` retries transient lock errors but keeps its
        never-raises contract once they persist: ``record_queued`` returns
        None instead of failing the refresh."""
        from conftest import make_app_engine

        class _DeadlockedCommitSession(Session):
            def commit(self):
                raise _deadlock()

        engine = make_app_engine()
        test_session = sessionmaker(bind=engine, class_=_DeadlockedCommitSession)
        monkeypatch.setattr(refresh_jobs, "SessionLocal", test_session)

        # Default backoff would cost ≤ ~0.5s of sleeps for 3 attempts — keep
        # the test fast and deterministic.
        import weatherbrief.db.retry as retry_mod

        monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)

        assert refresh_jobs.record_queued("flight-x", user_id=DEV_USER_ID) is None
        engine.dispose()
