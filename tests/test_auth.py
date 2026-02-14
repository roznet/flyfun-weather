"""Tests for authentication endpoints and authorization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from weatherbrief.api.app import create_app
from weatherbrief.api.auth_config import COOKIE_NAME
from weatherbrief.api.jwt_utils import create_token
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.db.models import Base, UserPreferencesRow, UserRow
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight

TEST_SECRET = "test-jwt-secret"
USER_A_ID = "user-aaa-111"
USER_B_ID = "user-bbb-222"


@pytest.fixture
def auth_db():
    """In-memory SQLite with two test users."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    session = TestSession()
    # User A — approved
    session.add(UserRow(
        id=USER_A_ID, provider="google", provider_sub="goog-a",
        email="alice@test.com", display_name="Alice", approved=True,
    ))
    session.add(UserPreferencesRow(user_id=USER_A_ID))
    # User B — approved
    session.add(UserRow(
        id=USER_B_ID, provider="google", provider_sub="goog-b",
        email="bob@test.com", display_name="Bob", approved=True,
    ))
    session.add(UserPreferencesRow(user_id=USER_B_ID))
    session.commit()
    session.close()

    yield TestSession
    engine.dispose()


def _make_client(auth_db, tmp_path, monkeypatch, user_id: str | None = None):
    """Create a test client, optionally injecting a specific user."""
    import weatherbrief.api.routes as routes_mod

    # Minimal routes config
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "routes.yaml").write_text("routes: {}\n")
    monkeypatch.setattr(routes_mod, "CONFIG_DIR", config_dir)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)

    app = create_app()

    def _override_get_db():
        session = auth_db()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db

    if user_id is not None:
        app.dependency_overrides[current_user_id] = lambda: user_id

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_a(auth_db, tmp_path, monkeypatch):
    """Client authenticated as User A."""
    return _make_client(auth_db, tmp_path, monkeypatch, USER_A_ID)


@pytest.fixture
def client_b(auth_db, tmp_path, monkeypatch):
    """Client authenticated as User B."""
    return _make_client(auth_db, tmp_path, monkeypatch, USER_B_ID)


@pytest.fixture
def client_unauth(auth_db, tmp_path, monkeypatch):
    """Client with no auth (production mode, no cookie override)."""
    return _make_client(auth_db, tmp_path, monkeypatch, user_id=None)


class TestAuthMe:
    def test_me_authenticated(self, client_a):
        resp = client_a.get("/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == USER_A_ID
        assert data["email"] == "alice@test.com"
        assert data["name"] == "Alice"
        assert data["approved"] is True

    def test_me_unauthenticated(self, client_unauth):
        resp = client_unauth.get("/auth/me")
        assert resp.status_code == 401


class TestAuthLogout:
    def test_logout_clears_cookie(self, client_a):
        resp = client_a.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login.html" in resp.headers["location"]


class TestJWTCookie:
    def test_valid_jwt_cookie(self, auth_db, tmp_path, monkeypatch):
        """A real JWT cookie (no dependency override) should authenticate."""
        client = _make_client(auth_db, tmp_path, monkeypatch, user_id=None)
        token = create_token(USER_A_ID, "alice@test.com", "Alice", TEST_SECRET)
        resp = client.get("/auth/me", cookies={COOKIE_NAME: token})
        assert resp.status_code == 200
        assert resp.json()["id"] == USER_A_ID

    def test_expired_jwt_returns_401(self, auth_db, tmp_path, monkeypatch):
        """An expired JWT should return 401."""
        import time
        import jwt as pyjwt
        client = _make_client(auth_db, tmp_path, monkeypatch, user_id=None)
        payload = {
            "sub": USER_A_ID,
            "email": "alice@test.com",
            "name": "Alice",
            "iat": time.time() - 3600,
            "exp": time.time() - 1,
        }
        token = pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")
        resp = client.get("/auth/me", cookies={COOKIE_NAME: token})
        assert resp.status_code == 401

    def test_invalid_jwt_returns_401(self, auth_db, tmp_path, monkeypatch):
        """A bogus JWT should return 401."""
        client = _make_client(auth_db, tmp_path, monkeypatch, user_id=None)
        resp = client.get("/auth/me", cookies={COOKIE_NAME: "garbage"})
        assert resp.status_code == 401


class TestLoginRedirect:
    def test_google_login_redirects(self, client_a):
        resp = client_a.get("/auth/login/google", follow_redirects=False)
        # Should redirect to Google OAuth (302)
        assert resp.status_code in (302, 307)
        location = resp.headers.get("location", "")
        assert "accounts.google.com" in location


class TestFlightOwnership:
    """Verify that users can only see their own flights."""

    @pytest.fixture
    def flights_seeded(self, auth_db):
        session = auth_db()
        flight_a = Flight(
            id="flight-a-2026-03-01",
            user_id=USER_A_ID,
            route_name="alice_route",
            waypoints=["EGTK", "LSGS"],
            target_date="2026-03-01",
            target_time_utc=9,
            cruise_altitude_ft=8000,
            created_at=datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        flight_b = Flight(
            id="flight-b-2026-03-01",
            user_id=USER_B_ID,
            route_name="bob_route",
            waypoints=["LFPB", "LFMT"],
            target_date="2026-03-01",
            target_time_utc=10,
            cruise_altitude_ft=10000,
            created_at=datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        save_flight(session, flight_a, USER_A_ID)
        save_flight(session, flight_b, USER_B_ID)
        session.commit()
        session.close()

    def test_list_only_own_flights(self, client_a, client_b, flights_seeded):
        resp_a = client_a.get("/api/flights")
        assert resp_a.status_code == 200
        ids_a = [f["id"] for f in resp_a.json()]
        assert "flight-a-2026-03-01" in ids_a
        assert "flight-b-2026-03-01" not in ids_a

        resp_b = client_b.get("/api/flights")
        ids_b = [f["id"] for f in resp_b.json()]
        assert "flight-b-2026-03-01" in ids_b
        assert "flight-a-2026-03-01" not in ids_b

    def test_can_view_other_users_flight(self, client_b, flights_seeded):
        """Any authenticated user can view any flight (shareable links)."""
        resp = client_b.get("/api/flights/flight-a-2026-03-01")
        assert resp.status_code == 200
        assert resp.json()["id"] == "flight-a-2026-03-01"

    def test_cannot_delete_other_users_flight(self, client_b, flights_seeded):
        resp = client_b.delete("/api/flights/flight-a-2026-03-01")
        assert resp.status_code == 404

    def test_unauthenticated_api_returns_401(self, client_unauth):
        resp = client_unauth.get("/api/flights")
        assert resp.status_code == 401
