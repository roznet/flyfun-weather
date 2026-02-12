"""Tests for SQLAlchemy model relationships and cascades."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    UsageLogRow,
    UserPreferencesRow,
    UserRow,
)


class TestUserModel:
    def test_create_user(self, db_session):
        user = UserRow(
            id="test-user",
            provider="google",
            email="test@example.com",
            display_name="Test User",
            approved=True,
        )
        db_session.add(user)
        db_session.flush()

        loaded = db_session.get(UserRow, "test-user")
        assert loaded is not None
        assert loaded.email == "test@example.com"
        assert loaded.approved is True
        assert loaded.created_at is not None

    def test_user_preferences_relationship(self, db_session, dev_user):
        user = db_session.get(UserRow, dev_user)
        assert user.preferences is not None
        assert user.preferences.user_id == dev_user

    def test_delete_user_cascades_preferences(self, db_session, dev_user):
        user = db_session.get(UserRow, dev_user)
        db_session.delete(user)
        db_session.flush()

        prefs = db_session.get(UserPreferencesRow, dev_user)
        assert prefs is None


class TestFlightModel:
    def test_create_flight(self, db_session, dev_user):
        flight = FlightRow(
            id="test-flight-2026-03-01",
            user_id=dev_user,
            route_name="test",
            waypoints_json='["EGTK", "LSGS"]',
            target_date="2026-03-01",
        )
        db_session.add(flight)
        db_session.flush()

        loaded = db_session.get(FlightRow, "test-flight-2026-03-01")
        assert loaded is not None
        assert loaded.user_id == dev_user
        assert loaded.target_time_utc == 9  # default
        assert loaded.cruise_altitude_ft == 8000  # default

    def test_flight_belongs_to_user(self, db_session, dev_user):
        flight = FlightRow(
            id="owned-flight",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
        )
        db_session.add(flight)
        db_session.flush()

        user = db_session.get(UserRow, dev_user)
        assert len(user.flights) == 1
        assert user.flights[0].id == "owned-flight"


class TestBriefingPackModel:
    def test_create_pack(self, db_session, dev_user):
        flight = FlightRow(
            id="pack-test-flight",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
        )
        db_session.add(flight)
        db_session.flush()

        pack = BriefingPackRow(
            flight_id="pack-test-flight",
            fetch_timestamp="2026-02-28T12:00:00Z",
            days_out=1,
            has_gramet=True,
            assessment="GREEN",
        )
        db_session.add(pack)
        db_session.flush()

        assert pack.id is not None  # auto-generated
        assert pack.has_gramet is True
        assert pack.has_skewt is False  # default

    def test_delete_flight_cascades_packs(self, db_session, dev_user):
        flight = FlightRow(
            id="cascade-flight",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
        )
        db_session.add(flight)
        db_session.flush()

        pack = BriefingPackRow(
            flight_id="cascade-flight",
            fetch_timestamp="2026-02-28T12:00:00Z",
            days_out=1,
        )
        db_session.add(pack)
        db_session.flush()
        pack_id = pack.id

        db_session.delete(flight)
        db_session.flush()

        assert db_session.get(BriefingPackRow, pack_id) is None

    def test_flight_packs_relationship(self, db_session, dev_user):
        flight = FlightRow(
            id="rel-flight",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
        )
        db_session.add(flight)
        db_session.flush()

        for i in range(3):
            db_session.add(BriefingPackRow(
                flight_id="rel-flight",
                fetch_timestamp=f"2026-02-{28-i}T12:00:00Z",
                days_out=i + 1,
            ))
        db_session.flush()

        loaded = db_session.get(FlightRow, "rel-flight")
        assert len(loaded.packs) == 3


class TestUsageLogModel:
    def test_create_usage_log(self, db_session, dev_user):
        log = UsageLogRow(
            user_id=dev_user,
            call_type="briefing_refresh",
            detail_json='{"flight_id": "test"}',
        )
        db_session.add(log)
        db_session.flush()

        assert log.id is not None
        assert log.skipped is False  # default

    def test_delete_user_cascades_logs(self, db_session, dev_user):
        log = UsageLogRow(
            user_id=dev_user,
            call_type="test",
        )
        db_session.add(log)
        db_session.flush()
        log_id = log.id

        user = db_session.get(UserRow, dev_user)
        db_session.delete(user)
        db_session.flush()

        assert db_session.get(UsageLogRow, log_id) is None
