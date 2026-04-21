"""Tests for the FastAPI API endpoints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Dynamic future dates so tests don't break as time passes
_NOW = datetime.now(timezone.utc)
_FUTURE_DEPARTURE = _NOW + timedelta(days=3)
_FUTURE_DEPARTURE_DATE = _FUTURE_DEPARTURE.strftime("%Y-%m-%d")
_FUTURE_DEPARTURE_ISO = _FUTURE_DEPARTURE.replace(
    hour=9, minute=0, second=0, microsecond=0,
).isoformat()
_FUTURE_DEPARTURE_DT = _FUTURE_DEPARTURE.replace(
    hour=9, minute=0, second=0, microsecond=0,
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory for the test app."""
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    """Create a test client with isolated DB and config directories."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")  # skip lifespan init_db
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-api-tests")

    app = create_app()

    # Override the DB dependency to use our test session
    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID

    return TestClient(app, raise_server_exceptions=False)


def _make_flight_id(route_name, date_str, *, time="09:00", alt=8000, ceil=18000, dur=4.5, user=DEV_USER_ID):
    """Compute flight ID the same way as the API endpoint."""
    h = hashlib.sha256(json.dumps(
        {"alt": alt, "ceil": ceil, "dur": dur, "time": time, "user": user},
        sort_keys=True,
    ).encode()).hexdigest()[:4]
    return f"{route_name}-{date_str}-{h}"


@pytest.fixture
def sample_flight(app_db):
    """Create and save a sample flight."""
    session = app_db()
    flight = Flight(
        id=_make_flight_id("egtk_lsgs", _FUTURE_DEPARTURE_DATE),
        user_id=DEV_USER_ID,
        route_name="egtk_lsgs",
        waypoints=["EGTK", "LFPB", "LSGS"],
        departure_time=_FUTURE_DEPARTURE_DT,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=4.5,
        created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, DEV_USER_ID)
    session.commit()
    session.close()
    return flight


@pytest.fixture
def sample_pack(app_db, sample_flight):
    """Create and save a sample pack for the sample flight."""
    session = app_db()
    meta = BriefingPackMeta(
        flight_id=sample_flight.id,
        fetch_timestamp=_NOW - timedelta(hours=6),
        days_out=3,
        has_gramet=True,
        has_skewt=True,
        has_digest=False,
        assessment="GREEN",
        assessment_reason="Conditions favorable",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()
    return meta


# --- Health ---


class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}



# --- Flights ---


class TestFlightsAPI:
    def test_list_flights_empty(self, client):
        resp = client.get("/api/flights")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_flight(self, client):
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "departure_time": _FUTURE_DEPARTURE_ISO,
            "cruise_altitude_ft": 8000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith(f"egtk_lsgs-{_FUTURE_DEPARTURE_DATE}-")
        assert data["route_name"] == "egtk_lsgs"
        assert data["waypoints"] == ["EGTK", "LFPB", "LSGS"]
        assert data["target_date"] == _FUTURE_DEPARTURE_DATE
        assert data["departure_time"].startswith(f"{_FUTURE_DEPARTURE_DATE}T09:00:00")

    def test_create_flight_defaults(self, client):
        _alt_future = (_NOW + timedelta(days=5)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LSGS"],
            "departure_time": _alt_future.isoformat(),
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["route_name"] == "egtk_lsgs"
        assert data["target_time_utc"] == 9
        assert data["cruise_altitude_ft"] == 8000
        assert data["flight_duration_hours"] == 0.0

    def test_create_flight_duplicate(self, client, sample_flight):
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "departure_time": _FUTURE_DEPARTURE_ISO,
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 18000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 409

    def test_list_flights_with_data(self, client, sample_flight):
        resp = client.get("/api/flights")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == sample_flight.id

    def test_get_flight(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["route_name"] == "egtk_lsgs"

    def test_get_flight_not_found(self, client):
        resp = client.get("/api/flights/nonexistent")
        assert resp.status_code == 404

    def test_delete_flight(self, client, sample_flight):
        resp = client.delete(f"/api/flights/{sample_flight.id}")
        assert resp.status_code == 204
        # Verify it's gone
        resp = client.get(f"/api/flights/{sample_flight.id}")
        assert resp.status_code == 404

    def test_same_route_date_different_params(self, client, sample_flight):
        """Same route+date with different time/altitude creates a new flight."""
        _afternoon = _FUTURE_DEPARTURE_DT.replace(hour=14)
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "departure_time": _afternoon.isoformat(),
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 18000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 201
        assert resp.json()["id"] != sample_flight.id
        assert resp.json()["id"].startswith(f"egtk_lsgs-{_FUTURE_DEPARTURE_DATE}-")

    def test_delete_flight_not_found(self, client):
        resp = client.delete("/api/flights/nonexistent")
        assert resp.status_code == 404

    def test_bulk_delete_flights(self, client, app_db):
        """Bulk-delete removes owned flights and reports unknown/unowned ids."""
        session = app_db()
        # Create a second user whose flight must not be touched
        session.add(UserRow(
            id="other-user", provider="local", provider_sub="other",
            email="other@localhost", display_name="Other User", approved=True,
        ))
        session.flush()
        # Owned flight A
        flight_a = Flight(
            id=_make_flight_id("egtk_lsgs", _FUTURE_DEPARTURE_DATE, time="09:00"),
            user_id=DEV_USER_ID, route_name="egtk_lsgs", waypoints=["EGTK", "LSGS"],
            departure_time=_FUTURE_DEPARTURE_DT,
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=4.5,
            created_at=_NOW - timedelta(days=1),
        )
        # Owned flight B
        flight_b = Flight(
            id=_make_flight_id("egtk_lsgs", _FUTURE_DEPARTURE_DATE, time="14:00"),
            user_id=DEV_USER_ID, route_name="egtk_lsgs", waypoints=["EGTK", "LSGS"],
            departure_time=_FUTURE_DEPARTURE_DT.replace(hour=14),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=4.5,
            created_at=_NOW - timedelta(days=1),
        )
        # Flight owned by someone else — must not be deleted
        flight_other = Flight(
            id=_make_flight_id("egtf_eglf", _FUTURE_DEPARTURE_DATE, user="other-user"),
            user_id="other-user", route_name="egtf_eglf", waypoints=["EGTF", "EGLF"],
            departure_time=_FUTURE_DEPARTURE_DT,
            cruise_altitude_ft=7000, flight_ceiling_ft=15000, flight_duration_hours=0.5,
            created_at=_NOW - timedelta(days=1),
        )
        save_flight(session, flight_a, DEV_USER_ID)
        save_flight(session, flight_b, DEV_USER_ID)
        save_flight(session, flight_other, "other-user")
        session.commit()
        session.close()

        resp = client.post("/api/flights/bulk-delete", json={
            "ids": [flight_a.id, flight_b.id, flight_other.id, "does-not-exist"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["deleted"]) == sorted([flight_a.id, flight_b.id])
        assert sorted(data["not_found"]) == sorted([flight_other.id, "does-not-exist"])

        # The other user's flight must still exist
        session = app_db()
        from weatherbrief.storage.flights import load_flight
        assert load_flight(session, flight_other.id).id == flight_other.id
        session.close()

    def test_bulk_delete_empty(self, client):
        resp = client.post("/api/flights/bulk-delete", json={"ids": []})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": [], "not_found": []}

    # --- Move flight (atomic re-create with new structural fields) ---

    def test_move_flight_changes_date(self, client, sample_flight):
        """Move with a new departure date returns a new flight ID and the old one is gone."""
        new_dt = (_FUTURE_DEPARTURE_DT + timedelta(days=2)).isoformat()
        resp = client.post(
            f"/api/flights/{sample_flight.id}/move",
            json={"departure_time": new_dt},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] != sample_flight.id
        assert data["departure_time"].startswith(new_dt[:10])
        assert data["cruise_altitude_ft"] == sample_flight.cruise_altitude_ft
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 404

    def test_move_flight_changes_route(self, client, sample_flight):
        """Move with new origin/dest creates a flight with a different route."""
        resp = client.post(
            f"/api/flights/{sample_flight.id}/move",
            json={"waypoints": ["EGTF", "EGLF"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["waypoints"] == ["EGTF", "EGLF"]
        assert data["route_name"] == "egtf_eglf"
        assert data["id"] != sample_flight.id
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 404

    def test_move_flight_no_change_rejected(self, client, sample_flight):
        """Move with no structural change returns 422 (would generate the same ID)."""
        resp = client.post(f"/api/flights/{sample_flight.id}/move", json={})
        assert resp.status_code == 422
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 200

    def test_move_flight_collision_rolls_back(self, client, sample_flight, app_db):
        """If the new ID would collide with another existing flight, abort with 409 and don't touch the source."""
        session = app_db()
        other_dt = (_FUTURE_DEPARTURE_DT + timedelta(days=2))
        other = Flight(
            id=_make_flight_id("egtk_lsgs", other_dt.strftime("%Y-%m-%d"), time="09:00"),
            user_id=DEV_USER_ID, route_name="egtk_lsgs",
            waypoints=["EGTK", "LFPB", "LSGS"],
            departure_time=other_dt,
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=4.5,
            created_at=_NOW - timedelta(days=1),
        )
        save_flight(session, other, DEV_USER_ID)
        session.commit()
        session.close()

        resp = client.post(
            f"/api/flights/{sample_flight.id}/move",
            json={"departure_time": other_dt.isoformat()},
        )
        assert resp.status_code == 409
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 200
        assert client.get(f"/api/flights/{other.id}").status_code == 200

    def test_move_flight_not_owner(self, client, app_db):
        """Cannot move someone else's flight."""
        session = app_db()
        session.add(UserRow(
            id="other-user", provider="local", provider_sub="other",
            email="other@localhost", display_name="Other", approved=True,
        ))
        session.flush()
        flight = Flight(
            id=_make_flight_id("egtf_eglf", _FUTURE_DEPARTURE_DATE, user="other-user"),
            user_id="other-user", route_name="egtf_eglf", waypoints=["EGTF", "EGLF"],
            departure_time=_FUTURE_DEPARTURE_DT,
            cruise_altitude_ft=7000, flight_ceiling_ft=15000, flight_duration_hours=0.5,
            created_at=_NOW - timedelta(days=1),
        )
        save_flight(session, flight, "other-user")
        session.commit()
        session.close()

        resp = client.post(
            f"/api/flights/{flight.id}/move",
            json={"departure_time": (_FUTURE_DEPARTURE_DT + timedelta(days=1)).isoformat()},
        )
        assert resp.status_code == 404


# --- Packs ---


class TestPacksAPI:
    def test_list_packs_empty(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}/packs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_packs_with_data(self, client, sample_pack):
        resp = client.get(f"/api/flights/{sample_pack.flight_id}/packs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["fetch_timestamp"] == sample_pack.fetch_timestamp.isoformat()
        assert data[0]["has_gramet"] is True

    def test_list_packs_flight_not_found(self, client):
        resp = client.get("/api/flights/nonexistent/packs")
        assert resp.status_code == 404

    def test_get_latest_pack(self, client, sample_pack):
        resp = client.get(f"/api/flights/{sample_pack.flight_id}/packs/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetch_timestamp"] == sample_pack.fetch_timestamp.isoformat()

    def test_get_latest_pack_none(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}/packs/latest")
        assert resp.status_code == 404

    def test_get_specific_pack(self, client, sample_pack):
        resp = client.get(
            f"/api/flights/{sample_pack.flight_id}/packs/{sample_pack.fetch_timestamp.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["days_out"] == sample_pack.days_out
        assert data["assessment"] == "GREEN"

    def test_get_pack_not_found(self, client, sample_flight):
        resp = client.get(
            f"/api/flights/{sample_flight.id}/packs/1999-01-01T00:00:00+00:00"
        )
        assert resp.status_code == 404


class TestPackArtifacts:
    """Test artifact serving (snapshot, gramet, skewt, digest)."""

    @pytest.fixture
    def pack_with_artifacts(self, tmp_path, sample_pack, monkeypatch):
        """Create a pack with actual artifact files on disk."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        pack_dir = pack_dir_for(
            DEV_USER_ID, sample_pack.flight_id, sample_pack.fetch_timestamp,
        )
        pack_dir.mkdir(parents=True, exist_ok=True)

        # Briefing JSON (snapshot split: briefing.json has route/analyses/observations)
        (pack_dir / "briefing.json").write_text('{"route": {}}')

        # GRAMET PNG (fake 1-pixel PNG header)
        (pack_dir / "gramet.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

        # Skew-T
        skewt_dir = pack_dir / "skewt"
        skewt_dir.mkdir()
        (skewt_dir / "EGTK_gfs.png").write_bytes(b"\x89PNG\r\n\x1a\nskewt")

        # Digest
        (pack_dir / "digest.md").write_text("# Weather Digest\nAll clear.")

        return sample_pack

    def test_get_snapshot(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/snapshot")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"

    def test_get_gramet(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/gramet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_skewt(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/skewt/EGTK/gfs")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_skewt_not_found(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/skewt/XXXX/gfs")
        assert resp.status_code == 404

    def test_get_digest(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/digest")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]

    def test_snapshot_not_found(self, client, sample_pack):
        ts = sample_pack.fetch_timestamp.isoformat()
        fid = sample_pack.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/snapshot")
        assert resp.status_code == 404

    def test_gramet_not_found(self, client, sample_pack):
        ts = sample_pack.fetch_timestamp.isoformat()
        fid = sample_pack.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/gramet")
        assert resp.status_code == 404


class TestRefreshEndpoint:
    """Test the POST /refresh endpoint (mocked pipeline)."""

    def test_refresh_flight_not_found(self, client):
        resp = client.post("/api/flights/nonexistent/packs/refresh")
        assert resp.status_code == 404

    def test_refresh_no_db_configured(self, client, sample_flight):
        """When AIRPORTS_DB is empty, refresh returns 503."""
        client.app.state.db_path = ""
        resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_refresh_uses_app_state_db_path(self, client, sample_flight):
        """Verify app.state.db_path is used when set."""
        client.app.state.db_path = "/fake/db/path"
        resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
        # Will fail because /fake/db/path doesn't exist or load_route fails,
        # but importantly it should NOT be a 503 "not configured"
        assert resp.status_code != 503 or "not configured" not in resp.json().get("detail", "")
        client.app.state.db_path = ""

    @patch("weatherbrief.airports._load_airport_model")
    def test_refresh_queued(self, mock_load, client, sample_flight):
        """Refresh returns 202 with queued status (pipeline runs in background)."""
        from airport_mocks import TEST_AIRPORTS, mock_model
        from weatherbrief.api.packs import refresh_registry

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
        assert resp.status_code == 202

        data = resp.json()
        assert data["status"] == "queued"
        assert data["flight_id"] == sample_flight.id

        # Clean up: wait briefly for executor to pick up and unregister
        import time
        for _ in range(20):
            if not refresh_registry._entries.get(sample_flight.id):
                break
            time.sleep(0.1)
        else:
            # Force cleanup if pipeline failed (expected — /fake/db doesn't exist)
            refresh_registry.unregister(sample_flight.id)

    def test_refresh_duplicate_409(self, client, sample_flight):
        """Refresh returns 409 when one is already in progress."""
        from weatherbrief.api.packs import refresh_registry

        client.app.state.db_path = "/fake/db"
        refresh_registry.try_register(
            sample_flight.id, triggered_by="user", user_id=DEV_USER_ID,
        )
        try:
            resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
            assert resp.status_code == 409
            assert "already in progress" in resp.json()["detail"]
        finally:
            refresh_registry.unregister(sample_flight.id)


# --- Interpret Route ---

# Mock helpers imported from conftest — mock only the DB layer so the real
# RouteResolver runs (catches contract violations like the single-token bug).
from airport_mocks import TEST_AIRPORTS, mock_model


class TestInterpretRoute:
    """Tests for POST /api/flights/interpret-route.

    Mock at _load_airport_model level so RouteResolver runs for real.
    """

    def _post(self, client, raw_route: str):
        return client.post(
            "/api/flights/interpret-route",
            json={"raw_route": raw_route},
        )

    @patch("weatherbrief.airports._load_airport_model")
    def test_two_valid_airports(self, mock_load, client):
        """Basic happy path — two ICAO codes resolve successfully."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        assert data["skipped"] == []
        assert len(data["waypoints"]) == 2

    @patch("weatherbrief.airports._load_airport_model")
    def test_single_valid_airport(self, mock_load, client):
        """Single code is recognized but no full route resolution."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ"]
        assert data["skipped"] == []
        # No waypoint details — need >= 2 for full resolution
        assert data["waypoints"] == []

    @patch("weatherbrief.airports._load_airport_model")
    def test_unknown_token_skipped(self, mock_load, client):
        """Unknown codes go to skipped, valid ones to interpreted."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ ZZZZ LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        assert data["skipped"] == ["ZZZZ"]

    @patch("weatherbrief.airports._load_airport_model")
    def test_filters_route_notation(self, mock_load, client):
        """Non-waypoint tokens (separators, short words) are filtered out."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ - LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]

    @patch("weatherbrief.airports._load_airport_model")
    def test_multi_waypoint_route(self, mock_load, client):
        """Three-leg route resolves all waypoints."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGTK EGBJ LSGS")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGTK", "EGBJ", "LSGS"]
        assert len(data["waypoints"]) == 3

    @patch("weatherbrief.airports._load_airport_model")
    def test_duplicate_consecutive_tokens(self, mock_load, client):
        """Consecutive duplicates are collapsed."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ EGBJ LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]

    def test_no_db_configured(self, client):
        """Returns 500 when airport database is not configured."""
        client.app.state.db_path = ""
        resp = self._post(client, "EGBJ LFOV")
        assert resp.status_code == 500

    @patch("weatherbrief.airports._load_airport_model")
    def test_all_unknown(self, mock_load, client):
        """All tokens unknown — interpreted is empty."""
        mock_load.return_value = mock_model({})
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "ZZZZ YYYY")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == []
        assert set(data["skipped"]) == {"ZZZZ", "YYYY"}
