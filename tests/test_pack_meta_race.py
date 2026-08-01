"""Race-safe ``save_pack_meta`` against UNIQUE (flight_id, fetch_timestamp).

Why: the briefing-ready milestone inserts a provisional pack row, and a second
refresh (sync ``/refresh`` endpoint vs. scheduler) can race to insert the same
``(flight_id, fetch_timestamp)``. With ``uq_briefing_packs_flight_ts`` in place
the loser of that race hits an IntegrityError; ``save_pack_meta`` must contain
it in a SAVEPOINT and fall back to updating the winner's row — one row left,
outer transaction unharmed — instead of poisoning the request transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import BriefingPackRow
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import load_pack_meta, save_flight, save_pack_meta


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


def _pack_count(session, flight_id: str) -> int:
    return session.scalar(
        select(func.count())
        .select_from(BriefingPackRow)
        .where(BriefingPackRow.flight_id == flight_id)
    )


class TestSavePackMetaRace:
    def test_conflicting_save_becomes_update(
        self, db_session, dev_user, sample_flight
    ):
        save_flight(db_session, sample_flight, dev_user)
        save_pack_meta(db_session, _meta(sample_flight.id))

        # Loser of the insert race: same (flight_id, fetch_timestamp) but with
        # finalized content. Must not raise — it updates the existing row.
        finalized = _meta(
            sample_flight.id,
            has_digest=True,
            assessment="GREEN",
            assessment_reason="digest concluded all clear",
        )
        save_pack_meta(db_session, finalized)

        assert _pack_count(db_session, sample_flight.id) == 1
        loaded = load_pack_meta(
            db_session, sample_flight.id, finalized.fetch_timestamp
        )
        assert loaded.assessment == "GREEN"
        assert loaded.assessment_reason == "digest concluded all clear"
        assert loaded.has_digest is True

    def test_race_fallback_does_not_nest_a_second_retry_loop(
        self, db_session, dev_user, sample_flight, monkeypatch
    ):
        """The IntegrityError fallback runs inside save_pack_meta's own
        db_retry loop, so it must use the non-retrying ``_update_pack_meta_once``
        core: exactly ONE db_retry loop is entered for the whole raced save.
        An inner loop's ``session.rollback()`` could silently discard the
        caller's prior uncommitted work (final review Important #3)."""
        import weatherbrief.storage.flights as flights_mod
        from weatherbrief.db.retry import db_retry as real_db_retry

        calls = 0

        def spy_db_retry(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_db_retry(*args, **kwargs)

        monkeypatch.setattr(flights_mod, "db_retry", spy_db_retry)

        save_flight(db_session, sample_flight, dev_user)
        save_pack_meta(db_session, _meta(sample_flight.id))
        calls = 0  # only the raced (fallback) save counts

        finalized = _meta(sample_flight.id, assessment="GREEN")
        save_pack_meta(db_session, finalized)

        assert calls == 1
        assert _pack_count(db_session, sample_flight.id) == 1
        loaded = load_pack_meta(
            db_session, sample_flight.id, finalized.fetch_timestamp
        )
        assert loaded.assessment == "GREEN"

    def test_session_survives_the_conflict(self, sample_flight):
        """The SAVEPOINT contains the failure: the session stays usable and a
        commit afterwards persists both the raced row and later work.

        Runs on a private engine, not the shared session-scoped ``db_engine``:
        committing there would persist the dev-user row and make every later
        ``dev_user`` fixture setup collide on ``users.id``.
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
            session.flush()
            save_flight(session, sample_flight, DEV_USER_ID)
            save_pack_meta(session, _meta(sample_flight.id))
            save_pack_meta(session, _meta(sample_flight.id, assessment="GREEN"))

            later = _meta(
                sample_flight.id,
                fetch_timestamp=datetime(2026, 2, 20, 9, 0, 0, tzinfo=timezone.utc),
                days_out=1,
            )
            save_pack_meta(session, later)
            session.commit()

            assert _pack_count(session, sample_flight.id) == 2
            loaded = load_pack_meta(
                session, sample_flight.id, later.fetch_timestamp
            )
            assert loaded.days_out == 1
        finally:
            session.close()
            engine.dispose()
