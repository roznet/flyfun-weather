"""Tests for API token authentication and agent management."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from flyfun_common.auth import COOKIE_NAME, create_token
from flyfun_common.admin import TOKEN_PREFIX
from flyfun_common.db import get_db, DEV_USER_ID
from flyfun_common.db.models import ApiTokenRow, UserPreferencesRow, UserRow

TEST_SECRET = "test-jwt-secret"
ADMIN_EMAIL = "admin@test.com"
ADMIN_ID = "admin-user-001"
REGULAR_ID = "regular-user-001"


@pytest.fixture
def db_session():
    """In-memory SQLite database with admin + regular user."""
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=ADMIN_ID, provider="google", provider_sub="goog-admin",
        email=ADMIN_EMAIL, display_name="Admin", approved=True,
    ))
    session.add(UserRow(
        id=REGULAR_ID, provider="google", provider_sub="goog-reg",
        email="user@test.com", display_name="Regular User", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=ADMIN_ID))
    session.add(UserPreferencesRow(user_id=REGULAR_ID))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


def _make_client(db_session, tmp_path, monkeypatch) -> TestClient:
    """Create a test client with production auth (no user override)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN_EMAIL)

    app = create_app()

    def _override_get_db():
        session = db_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    return _make_client(db_session, tmp_path, monkeypatch)


def _admin_cookie(secret: str = TEST_SECRET) -> dict:
    token = create_token(ADMIN_ID, ADMIN_EMAIL, "Admin", secret)
    return {COOKIE_NAME: token}


def _user_cookie(secret: str = TEST_SECRET) -> dict:
    token = create_token(REGULAR_ID, "user@test.com", "Regular User", secret)
    return {COOKIE_NAME: token}


class TestCreateAgent:
    def test_create_agent_returns_token(self, client):
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Claude Desktop"},
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Claude Desktop"
        assert data["token"].startswith(TOKEN_PREFIX)
        assert data["user_id"].startswith("agent-")
        assert len(data["token"]) >= 40

    def test_non_admin_cannot_create_agent(self, client):
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Hacker Bot"},
            cookies=_user_cookie(),
        )
        assert resp.status_code == 403


class TestBearerAuth:
    def test_bearer_token_accesses_api(self, client):
        # Create agent
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Test Agent"},
            cookies=_admin_cookie(),
        )
        token = resp.json()["token"]

        # Use Bearer token to access flights API
        resp = client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, client):
        resp = client.get(
            "/api/flights",
            headers={"Authorization": "Bearer ff_bogustoken123456"},
        )
        assert resp.status_code == 401

    def test_no_auth_returns_401(self, client):
        resp = client.get("/api/flights")
        assert resp.status_code == 401

    def test_wrong_prefix_returns_401(self, client):
        resp = client.get(
            "/api/flights",
            headers={"Authorization": "Bearer sk_notavalidtoken"},
        )
        assert resp.status_code == 401


class TestTokenRevocation:
    def _create_agent(self, client) -> tuple[str, str]:
        """Helper: create an agent, return (user_id, token)."""
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Revoke Test"},
            cookies=_admin_cookie(),
        )
        data = resp.json()
        return data["user_id"], data["token"]

    def test_revoked_token_returns_401(self, client, db_session):
        user_id, token = self._create_agent(client)

        # Revoke the token directly in DB
        session = db_session()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = session.query(ApiTokenRow).filter(ApiTokenRow.token_hash == token_hash).one()
        row.revoked = True
        session.commit()
        session.close()

        resp = client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_revoke_agent_disables_all_access(self, client):
        user_id, token = self._create_agent(client)

        # Revoke agent via admin API
        resp = client.delete(
            f"/api/admin/agents/{user_id}",
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 200

        # Token should fail (revoked + account suspended)
        resp = client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (401, 403)

    def test_revoke_single_token(self, client):
        user_id, token1 = self._create_agent(client)

        # Create second token
        resp = client.post(
            f"/api/admin/agents/{user_id}/tokens",
            json={"name": "token-2"},
            cookies=_admin_cookie(),
        )
        token2 = resp.json()["token"]
        token2_id = resp.json()["token_id"]

        # Revoke second token
        resp = client.delete(
            f"/api/admin/agents/{user_id}/tokens/{token2_id}",
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 200

        # First token still works
        resp = client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert resp.status_code == 200

        # Second token fails
        resp = client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 401


class TestExpiredToken:
    def test_expired_token_returns_401(self, client, db_session):
        # Create agent
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Expire Test"},
            cookies=_admin_cookie(),
        )
        data = resp.json()
        token = data["token"]

        # Set token expiry to the past
        session = db_session()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = session.query(ApiTokenRow).filter(ApiTokenRow.token_hash == token_hash).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()
        session.close()

        resp = client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


class TestUsageTracking:
    def test_last_used_at_updated(self, client, db_session):
        # Create agent
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Usage Test"},
            cookies=_admin_cookie(),
        )
        token = resp.json()["token"]

        # Use the token
        client.get(
            "/api/flights",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Check last_used_at was set
        session = db_session()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = session.query(ApiTokenRow).filter(ApiTokenRow.token_hash == token_hash).one()
        assert row.last_used_at is not None
        session.close()


class TestAdminUsersList:
    def test_agents_appear_with_type_agent(self, client):
        # Create an agent
        client.post(
            "/api/admin/agents",
            json={"name": "List Test Agent"},
            cookies=_admin_cookie(),
        )

        # Fetch user list
        resp = client.get("/api/admin/users", cookies=_admin_cookie())
        assert resp.status_code == 200
        users = resp.json()["users"]

        agent_users = [u for u in users if u["type"] == "agent"]
        human_users = [u for u in users if u["type"] == "human"]

        assert len(agent_users) == 1
        assert agent_users[0]["display_name"] == "List Test Agent"
        assert agent_users[0]["token_count"] == 1
        assert agent_users[0]["active_tokens"] == 1
        assert len(human_users) == 2  # admin + regular

    def test_active_users_follows_period(self, client, db_session):
        """``active_users`` is windowed; ``total_users`` is every account."""
        from weatherbrief.db.models import BriefingUsageRow, FlightRow

        now = datetime.now(timezone.utc)
        session = db_session()
        session.add(FlightRow(id="flt-recent", user_id=ADMIN_ID, departure_time=now))
        session.add(FlightRow(id="flt-old", user_id=REGULAR_ID, departure_time=now))
        session.flush()
        session.add(BriefingUsageRow(
            user_id=ADMIN_ID, flight_id="flt-recent", timestamp=now - timedelta(days=2),
        ))
        session.add(BriefingUsageRow(
            user_id=REGULAR_ID, flight_id="flt-old", timestamp=now - timedelta(days=90),
        ))
        session.commit()
        session.close()

        recent = client.get("/api/admin/users", cookies=_admin_cookie()).json()["summary"]
        assert recent["active_users"] == 1  # only the 2-day-old briefing counts
        assert recent["total_briefings"] == 1
        assert recent["total_users"] == 2  # both accounts, unwindowed

        all_time = client.get(
            "/api/admin/users?period=all", cookies=_admin_cookie(),
        ).json()["summary"]
        assert all_time["active_users"] == 2
        assert all_time["total_briefings"] == 2
        assert all_time["total_users"] == 2


class TestCreateAdditionalToken:
    def test_create_additional_token(self, client):
        # Create agent
        resp = client.post(
            "/api/admin/agents",
            json={"name": "Multi Token"},
            cookies=_admin_cookie(),
        )
        user_id = resp.json()["user_id"]

        # Create second token
        resp = client.post(
            f"/api/admin/agents/{user_id}/tokens",
            json={"name": "secondary"},
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token"].startswith(TOKEN_PREFIX)
        assert "token_id" in data

    def test_create_token_for_nonexistent_agent(self, client):
        resp = client.post(
            "/api/admin/agents/nonexistent/tokens",
            json={"name": "nope"},
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 404
