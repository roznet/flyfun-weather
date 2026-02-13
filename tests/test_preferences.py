"""Tests for the preferences API endpoints."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from weatherbrief.api.app import create_app
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.db.models import Base, UserPreferencesRow, UserRow


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory."""
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
def client(app_db, tmp_path, monkeypatch):
    """Create a test client with isolated DB."""
    import weatherbrief.api.routes as routes_mod

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "routes.yaml").write_text("routes: {}\n")
    monkeypatch.setattr(routes_mod, "CONFIG_DIR", config_dir)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    app = create_app()

    # Clear after create_app() since load_dotenv() may re-inject from .env
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

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


class TestPreferencesAPI:
    """Test GET/PUT preferences and DELETE autorouter credentials."""

    def test_get_default_preferences(self, client):
        resp = client.get("/api/user/preferences")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_autorouter_creds"] is False
        assert data["defaults"]["cruise_altitude_ft"] is None
        assert data["defaults"]["models"] is None

    def test_save_flight_defaults(self, client):
        resp = client.put("/api/user/preferences", json={
            "defaults": {
                "cruise_altitude_ft": 6000,
                "flight_ceiling_ft": 14000,
                "models": ["gfs", "ecmwf"],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["defaults"]["cruise_altitude_ft"] == 6000
        assert data["defaults"]["flight_ceiling_ft"] == 14000
        assert data["defaults"]["models"] == ["gfs", "ecmwf"]

    def test_save_and_reload(self, client):
        """Preferences persist across requests."""
        client.put("/api/user/preferences", json={
            "defaults": {"cruise_altitude_ft": 10000},
        })
        resp = client.get("/api/user/preferences")
        assert resp.json()["defaults"]["cruise_altitude_ft"] == 10000

    def test_save_autorouter_credentials(self, client):
        resp = client.put("/api/user/preferences", json={
            "autorouter_username": "myuser",
            "autorouter_password": "mypass",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_autorouter_creds"] is True
        # Credentials must NEVER appear in the response
        assert "myuser" not in json.dumps(data)
        assert "mypass" not in json.dumps(data)

    def test_credentials_never_in_get_response(self, client):
        """After saving credentials, GET never returns them."""
        client.put("/api/user/preferences", json={
            "autorouter_username": "secret_user",
            "autorouter_password": "secret_pass",
        })
        resp = client.get("/api/user/preferences")
        data = resp.json()
        assert data["has_autorouter_creds"] is True
        raw = json.dumps(data)
        assert "secret_user" not in raw
        assert "secret_pass" not in raw

    def test_clear_autorouter_credentials(self, client):
        client.put("/api/user/preferences", json={
            "autorouter_username": "user",
            "autorouter_password": "pass",
        })
        resp = client.delete("/api/user/preferences/autorouter")
        assert resp.status_code == 204

        resp = client.get("/api/user/preferences")
        assert resp.json()["has_autorouter_creds"] is False

    def test_partial_update_preserves_other_fields(self, client):
        """Updating defaults doesn't clear autorouter creds."""
        client.put("/api/user/preferences", json={
            "defaults": {"cruise_altitude_ft": 8000},
            "autorouter_username": "u",
            "autorouter_password": "p",
        })
        # Now update just defaults
        client.put("/api/user/preferences", json={
            "defaults": {"cruise_altitude_ft": 6000},
        })
        resp = client.get("/api/user/preferences")
        data = resp.json()
        assert data["defaults"]["cruise_altitude_ft"] == 6000
        assert data["has_autorouter_creds"] is True


class TestPreferencesAppliedToFlights:
    """Test that user preferences are applied when creating flights."""

    def test_flight_uses_user_defaults(self, client):
        """Flight created without altitude uses user's preferred altitude."""
        client.put("/api/user/preferences", json={
            "defaults": {"cruise_altitude_ft": 6000, "flight_ceiling_ft": 14000},
        })
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB"],
            "target_date": "2026-06-01",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["cruise_altitude_ft"] == 6000
        assert data["flight_ceiling_ft"] == 14000

    def test_flight_explicit_overrides_defaults(self, client):
        """Explicit values in the request override user defaults."""
        client.put("/api/user/preferences", json={
            "defaults": {"cruise_altitude_ft": 6000},
        })
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB"],
            "target_date": "2026-06-02",
            "cruise_altitude_ft": 10000,
        })
        assert resp.status_code == 201
        assert resp.json()["cruise_altitude_ft"] == 10000

    def test_flight_system_defaults_without_preferences(self, client):
        """Without user preferences, system defaults (8000/18000) are used."""
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB"],
            "target_date": "2026-06-03",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["cruise_altitude_ft"] == 8000
        assert data["flight_ceiling_ft"] == 18000
