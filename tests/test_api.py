"""Tests for the FastAPI API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from weatherbrief.api.app import create_app
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.db.models import Base, UserPreferencesRow, UserRow
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory for the test app."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Seed dev user
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    session.commit()
    session.close()

    yield TestSession
    engine.dispose()


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Temporary config directory with sample routes.yaml."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "routes.yaml").write_text(
        "routes:\n"
        "  egtk_lsgs:\n"
        '    name: "Oxford to Sion"\n'
        "    waypoints: [EGTK, LFPB, LSGS]\n"
        "    cruise_altitude_ft: 8000\n"
        "    flight_duration_hours: 4.5\n"
        "  egtk_lfmt:\n"
        '    name: "Oxford to Montpellier"\n'
        "    waypoints: [EGTK, LFMT]\n"
        "    cruise_altitude_ft: 10000\n"
    )
    return config_dir


@pytest.fixture
def client(app_db, tmp_config_dir, tmp_path, monkeypatch):
    """Create a test client with isolated DB and config directories."""
    import weatherbrief.api.routes as routes_mod
    import weatherbrief.db.engine as engine_mod

    monkeypatch.setattr(routes_mod, "CONFIG_DIR", tmp_config_dir)
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


@pytest.fixture
def sample_flight(app_db):
    """Create and save a sample flight."""
    session = app_db()
    flight = Flight(
        id="egtk_lsgs-2026-02-21-8823",
        user_id=DEV_USER_ID,
        route_name="egtk_lsgs",
        waypoints=["EGTK", "LFPB", "LSGS"],
        target_date="2026-02-21",
        target_time_utc=9,
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
        created_at=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
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
        fetch_timestamp="2026-02-19T18:00:00+00:00",
        days_out=2,
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


# --- Routes ---


class TestRoutesAPI:
    def test_list_routes(self, client):
        resp = client.get("/api/routes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {r["name"] for r in data}
        assert "egtk_lsgs" in names
        assert "egtk_lfmt" in names

    def test_list_routes_contains_details(self, client):
        resp = client.get("/api/routes")
        data = resp.json()
        route = next(r for r in data if r["name"] == "egtk_lsgs")
        assert route["display_name"] == "Oxford to Sion"
        assert route["waypoints"] == ["EGTK", "LFPB", "LSGS"]
        assert route["cruise_altitude_ft"] == 8000
        assert route["flight_duration_hours"] == 4.5

    def test_get_route(self, client):
        resp = client.get("/api/routes/egtk_lsgs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "egtk_lsgs"
        assert data["display_name"] == "Oxford to Sion"

    def test_get_route_not_found(self, client):
        resp = client.get("/api/routes/nonexistent")
        assert resp.status_code == 404


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
            "target_date": "2026-02-21",
            "target_time_utc": 9,
            "cruise_altitude_ft": 8000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "egtk_lsgs-2026-02-21-8823"
        assert data["route_name"] == "egtk_lsgs"
        assert data["waypoints"] == ["EGTK", "LFPB", "LSGS"]
        assert data["target_date"] == "2026-02-21"

    def test_create_flight_defaults(self, client):
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LSGS"],
            "target_date": "2026-03-01",
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
            "target_date": "2026-02-21",
            "target_time_utc": 9,
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
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "target_date": "2026-02-21",
            "target_time_utc": 14,  # afternoon instead of morning
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 18000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 201
        assert resp.json()["id"] != sample_flight.id
        assert resp.json()["id"].startswith("egtk_lsgs-2026-02-21-")

    def test_delete_flight_not_found(self, client):
        resp = client.delete("/api/flights/nonexistent")
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
        assert data[0]["fetch_timestamp"] == "2026-02-19T18:00:00+00:00"
        assert data[0]["has_gramet"] is True

    def test_list_packs_flight_not_found(self, client):
        resp = client.get("/api/flights/nonexistent/packs")
        assert resp.status_code == 404

    def test_get_latest_pack(self, client, sample_pack):
        resp = client.get(f"/api/flights/{sample_pack.flight_id}/packs/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetch_timestamp"] == "2026-02-19T18:00:00+00:00"

    def test_get_latest_pack_none(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}/packs/latest")
        assert resp.status_code == 404

    def test_get_specific_pack(self, client, sample_pack):
        resp = client.get(
            f"/api/flights/{sample_pack.flight_id}/packs/2026-02-19T18:00:00+00:00"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["days_out"] == 2
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

        # Snapshot JSON
        (pack_dir / "snapshot.json").write_text('{"route": {}}')

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
        ts = pack_with_artifacts.fetch_timestamp
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/snapshot")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"

    def test_get_gramet(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/gramet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_skewt(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/skewt/EGTK/gfs")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_skewt_not_found(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/skewt/XXXX/gfs")
        assert resp.status_code == 404

    def test_get_digest(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/digest")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]

    def test_snapshot_not_found(self, client, sample_pack):
        ts = sample_pack.fetch_timestamp
        fid = sample_pack.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/snapshot")
        assert resp.status_code == 404

    def test_gramet_not_found(self, client, sample_pack):
        ts = sample_pack.fetch_timestamp
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
