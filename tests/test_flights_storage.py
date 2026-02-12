"""Tests for flight and briefing pack storage (DB-backed)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import (
    delete_flight,
    list_flights,
    list_packs,
    load_flight,
    load_pack_meta,
    pack_dir_for,
    save_flight,
    save_pack_meta,
)


@pytest.fixture
def sample_flight():
    return Flight(
        id="egtk_lsgs-2026-02-21",
        user_id=DEV_USER_ID,
        route_name="egtk_lsgs",
        target_date="2026-02-21",
        target_time_utc=9,
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
        created_at=datetime(2026, 2, 14, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_pack_meta():
    return BriefingPackMeta(
        flight_id="egtk_lsgs-2026-02-21",
        fetch_timestamp="2026-02-19T18:00:00Z",
        days_out=2,
        has_gramet=True,
        has_skewt=True,
        has_digest=True,
        assessment="GREEN",
        assessment_reason="Ridge established, models converging",
    )


# --- Flight CRUD tests ---


class TestFlightCRUD:
    def test_save_and_load(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)

        loaded = load_flight(db_session, sample_flight.id)
        assert loaded.id == sample_flight.id
        assert loaded.route_name == sample_flight.route_name
        assert loaded.target_date == sample_flight.target_date
        assert loaded.target_time_utc == sample_flight.target_time_utc
        assert loaded.cruise_altitude_ft == sample_flight.cruise_altitude_ft
        assert loaded.flight_duration_hours == sample_flight.flight_duration_hours

    def test_load_nonexistent_raises(self, db_session):
        with pytest.raises(KeyError):
            load_flight(db_session, "nonexistent")

    def test_list_empty(self, db_session, dev_user):
        assert list_flights(db_session, dev_user) == []

    def test_list_multiple(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)

        flight2 = Flight(
            id="egtk_lfat-2026-03-01",
            user_id=dev_user,
            route_name="egtk_lfat",
            target_date="2026-03-01",
            target_time_utc=10,
            created_at=datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        save_flight(db_session, flight2, dev_user)

        flights = list_flights(db_session, dev_user)
        assert len(flights) == 2
        # Newest first
        assert flights[0].id == flight2.id
        assert flights[1].id == sample_flight.id

    def test_save_overwrites(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)

        updated = sample_flight.model_copy(update={"target_time_utc": 10})
        save_flight(db_session, updated, dev_user)

        loaded = load_flight(db_session, sample_flight.id)
        assert loaded.target_time_utc == 10

    def test_delete(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)
        assert load_flight(db_session, sample_flight.id).id == sample_flight.id

        delete_flight(db_session, sample_flight.id)

        with pytest.raises(KeyError):
            load_flight(db_session, sample_flight.id)
        assert list_flights(db_session, dev_user) == []

    def test_delete_nonexistent_raises(self, db_session):
        with pytest.raises(KeyError):
            delete_flight(db_session, "nonexistent")


# --- BriefingPack tests ---


class TestBriefingPacks:
    def test_save_and_load_meta(self, db_session, dev_user, sample_flight, sample_pack_meta):
        save_flight(db_session, sample_flight, dev_user)
        save_pack_meta(db_session, sample_pack_meta)

        loaded = load_pack_meta(
            db_session,
            sample_pack_meta.flight_id,
            sample_pack_meta.fetch_timestamp,
        )
        assert loaded.flight_id == sample_pack_meta.flight_id
        assert loaded.days_out == 2
        assert loaded.assessment == "GREEN"
        assert loaded.has_gramet is True

    def test_list_packs_empty(self, db_session, dev_user, sample_flight):
        save_flight(db_session, sample_flight, dev_user)
        assert list_packs(db_session, sample_flight.id) == []

    def test_list_packs_multiple(self, db_session, dev_user, sample_flight, sample_pack_meta):
        save_flight(db_session, sample_flight, dev_user)
        save_pack_meta(db_session, sample_pack_meta)

        pack2 = BriefingPackMeta(
            flight_id=sample_flight.id,
            fetch_timestamp="2026-02-18T08:00:00Z",
            days_out=3,
            has_digest=True,
            assessment="AMBER",
        )
        save_pack_meta(db_session, pack2)

        packs = list_packs(db_session, sample_flight.id)
        assert len(packs) == 2
        # Newest first (descending timestamp sort)
        assert packs[0].fetch_timestamp == sample_pack_meta.fetch_timestamp
        assert packs[1].fetch_timestamp == pack2.fetch_timestamp

    def test_pack_dir_for_sanitizes_timestamp(self):
        pack_dir = pack_dir_for(DEV_USER_ID, "some-flight", "2026-02-19T18:00:00Z")
        assert ":" not in pack_dir.name
        assert "2026-02-19T18-00-00Z" in str(pack_dir)

    def test_pack_dir_includes_user_id(self):
        pack_dir = pack_dir_for("user-123", "flight-abc", "2026-02-19T18:00:00Z")
        assert "user-123" in str(pack_dir)
        assert "flight-abc" in str(pack_dir)


# --- Model tests ---


class TestFlightModel:
    def test_defaults(self):
        f = Flight(
            id="test-2026-01-01",
            route_name="test",
            target_date="2026-01-01",
            created_at=datetime.now(tz=timezone.utc),
        )
        assert f.target_time_utc == 9
        assert f.cruise_altitude_ft == 8000
        assert f.flight_duration_hours == 0.0
        assert f.user_id == ""

    def test_json_round_trip(self, sample_flight):
        json_str = sample_flight.model_dump_json()
        loaded = Flight.model_validate_json(json_str)
        assert loaded == sample_flight


class TestBriefingPackMetaModel:
    def test_defaults(self):
        meta = BriefingPackMeta(
            flight_id="test-2026-01-01",
            fetch_timestamp="2026-01-01T00:00:00Z",
            days_out=7,
        )
        assert meta.has_gramet is False
        assert meta.has_skewt is False
        assert meta.has_digest is False
        assert meta.assessment is None
        assert meta.id is None
        assert meta.artifact_path == ""

    def test_json_round_trip(self, sample_pack_meta):
        json_str = sample_pack_meta.model_dump_json()
        loaded = BriefingPackMeta.model_validate_json(json_str)
        assert loaded == sample_pack_meta
