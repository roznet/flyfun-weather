"""Tests for flight and briefing pack storage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def sample_flight():
    return Flight(
        id="egtk_lsgs-2026-02-21",
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
    def test_save_and_load(self, data_dir, sample_flight):
        path = save_flight(sample_flight, data_dir)
        assert path.exists()
        assert path.name == "flight.json"

        loaded = load_flight(sample_flight.id, data_dir)
        assert loaded.id == sample_flight.id
        assert loaded.route_name == sample_flight.route_name
        assert loaded.target_date == sample_flight.target_date
        assert loaded.target_time_utc == sample_flight.target_time_utc
        assert loaded.cruise_altitude_ft == sample_flight.cruise_altitude_ft
        assert loaded.flight_duration_hours == sample_flight.flight_duration_hours

    def test_load_nonexistent_raises(self, data_dir):
        with pytest.raises(FileNotFoundError):
            load_flight("nonexistent", data_dir)

    def test_list_empty(self, data_dir):
        assert list_flights(data_dir) == []

    def test_list_multiple(self, data_dir, sample_flight):
        save_flight(sample_flight, data_dir)

        flight2 = Flight(
            id="egtk_lfat-2026-03-01",
            route_name="egtk_lfat",
            target_date="2026-03-01",
            target_time_utc=10,
            created_at=datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        save_flight(flight2, data_dir)

        flights = list_flights(data_dir)
        assert len(flights) == 2
        # Newest first
        assert flights[0].id == flight2.id
        assert flights[1].id == sample_flight.id

    def test_save_overwrites(self, data_dir, sample_flight):
        save_flight(sample_flight, data_dir)

        updated = sample_flight.model_copy(update={"target_time_utc": 10})
        save_flight(updated, data_dir)

        loaded = load_flight(sample_flight.id, data_dir)
        assert loaded.target_time_utc == 10

    def test_delete(self, data_dir, sample_flight):
        save_flight(sample_flight, data_dir)
        assert load_flight(sample_flight.id, data_dir).id == sample_flight.id

        delete_flight(sample_flight.id, data_dir)

        with pytest.raises(FileNotFoundError):
            load_flight(sample_flight.id, data_dir)
        assert list_flights(data_dir) == []

    def test_delete_nonexistent_raises(self, data_dir):
        with pytest.raises(FileNotFoundError):
            delete_flight("nonexistent", data_dir)

    def test_registry_updated_on_save(self, data_dir, sample_flight):
        save_flight(sample_flight, data_dir)
        registry_path = data_dir / "flights.json"
        assert registry_path.exists()

    def test_delete_removes_packs_too(self, data_dir, sample_flight, sample_pack_meta):
        save_flight(sample_flight, data_dir)
        save_pack_meta(sample_pack_meta, data_dir)

        pack_dir = pack_dir_for(
            sample_flight.id, sample_pack_meta.fetch_timestamp, data_dir
        )
        assert pack_dir.exists()

        delete_flight(sample_flight.id, data_dir)
        assert not pack_dir.exists()


# --- BriefingPack tests ---


class TestBriefingPacks:
    def test_save_and_load_meta(self, data_dir, sample_flight, sample_pack_meta):
        save_flight(sample_flight, data_dir)
        path = save_pack_meta(sample_pack_meta, data_dir)
        assert path.exists()
        assert path.name == "pack.json"

        loaded = load_pack_meta(
            sample_pack_meta.flight_id,
            sample_pack_meta.fetch_timestamp,
            data_dir,
        )
        assert loaded.flight_id == sample_pack_meta.flight_id
        assert loaded.days_out == 2
        assert loaded.assessment == "GREEN"
        assert loaded.has_gramet is True

    def test_list_packs_empty(self, data_dir, sample_flight):
        save_flight(sample_flight, data_dir)
        assert list_packs(sample_flight.id, data_dir) == []

    def test_list_packs_multiple(self, data_dir, sample_flight, sample_pack_meta):
        save_flight(sample_flight, data_dir)
        save_pack_meta(sample_pack_meta, data_dir)

        pack2 = BriefingPackMeta(
            flight_id=sample_flight.id,
            fetch_timestamp="2026-02-18T08:00:00Z",
            days_out=3,
            has_digest=True,
            assessment="AMBER",
        )
        save_pack_meta(pack2, data_dir)

        packs = list_packs(sample_flight.id, data_dir)
        assert len(packs) == 2
        # Newest first (directory sort order, reversed)
        assert packs[0].fetch_timestamp == sample_pack_meta.fetch_timestamp
        assert packs[1].fetch_timestamp == pack2.fetch_timestamp

    def test_pack_dir_for_sanitizes_timestamp(self, data_dir, sample_flight):
        pack_dir = pack_dir_for(sample_flight.id, "2026-02-19T18:00:00Z", data_dir)
        assert ":" not in pack_dir.name
        assert "2026-02-19T18-00-00Z" in str(pack_dir)

    def test_pack_dir_usable_for_artifacts(self, data_dir, sample_flight, sample_pack_meta):
        save_flight(sample_flight, data_dir)
        save_pack_meta(sample_pack_meta, data_dir)

        pack_dir = pack_dir_for(
            sample_flight.id, sample_pack_meta.fetch_timestamp, data_dir
        )
        # Can write artifacts alongside pack.json
        (pack_dir / "gramet.png").write_bytes(b"fake png data")
        skewt_dir = pack_dir / "skewt"
        skewt_dir.mkdir()
        (skewt_dir / "EGTK_gfs.png").write_bytes(b"fake skewt")

        assert (pack_dir / "pack.json").exists()
        assert (pack_dir / "gramet.png").exists()
        assert (skewt_dir / "EGTK_gfs.png").exists()


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

    def test_json_round_trip(self, sample_pack_meta):
        json_str = sample_pack_meta.model_dump_json()
        loaded = BriefingPackMeta.model_validate_json(json_str)
        assert loaded == sample_pack_meta
