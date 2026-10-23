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

    def test_usage_follows_period_and_has_no_disk(self, client, db_session):
        from weatherbrief.db.models import BriefingUsageRow, FlightRow

        now = datetime.now(timezone.utc)
        session = db_session()
        session.add(FlightRow(id="flt-recent", user_id=ADMIN_ID, departure_time=now))
        session.add(FlightRow(id="flt-old", user_id=REGULAR_ID, departure_time=now))
        session.flush()
        session.add(BriefingUsageRow(
            user_id=ADMIN_ID, flight_id="flt-recent", timestamp=now - timedelta(days=2),
            llm_input_tokens=100, llm_output_tokens=20,
        ))
        session.add(BriefingUsageRow(
            user_id=REGULAR_ID, flight_id="flt-old", timestamp=now - timedelta(days=90),
        ))
        session.commit()
        session.close()

        def usage(period: str) -> dict:
            users = client.get(
                f"/api/admin/users?period={period}", cookies=_admin_cookie(),
            ).json()["users"]
            return {u["id"]: u for u in users}

        recent = usage("30d")
        assert recent[ADMIN_ID]["usage"] == {"briefings": 1, "total_tokens": 120}
        assert recent[REGULAR_ID]["usage"]["briefings"] == 0
        assert "disk_usage_bytes" not in recent[ADMIN_ID]
        assert usage("all")[REGULAR_ID]["usage"]["briefings"] == 1


class TestAdminActivity:
    def test_windows_and_metrics(self, client, db_session):
        from weatherbrief.db.models import BriefingUsageRow, FlightRow, McpToolCallRow

        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Mid-way through yesterday / 3 days / 20 days / 90 days ago.
        yday = today - timedelta(hours=12)
        d3, d20, d90 = (now - timedelta(days=n) for n in (3, 20, 90))
        session = db_session()
        session.query(UserRow).filter(UserRow.id == REGULAR_ID).one().created_at = d3
        admin = session.query(UserRow).filter(UserRow.id == ADMIN_ID).one()
        admin.created_at = d90
        admin.last_seen_at = now  # read in the app today, no briefing requested
        # Admin: flight created 20 days ago, first briefing then, refreshed
        # yesterday by hand and today by the scheduler (not a user refresh).
        session.add(FlightRow(id="f-a", user_id=ADMIN_ID, departure_time=now, created_at=d20))
        # Regular: flight created yesterday, first briefing via MCP, refreshed today via MCP.
        session.add(FlightRow(id="f-r", user_id=REGULAR_ID, departure_time=now, created_at=yday))
        session.flush()
        for user, flight, ts, trig, tokens in (
            (ADMIN_ID, "f-a", d20, "user", None),
            (ADMIN_ID, "f-a", yday, "user", (100, 20)),
            (ADMIN_ID, "f-a", now, "scheduler", (50, 0)),
            (REGULAR_ID, "f-r", yday, "mcp", None),
            (REGULAR_ID, "f-r", now, "mcp", None),
        ):
            session.add(BriefingUsageRow(
                user_id=user, flight_id=flight, timestamp=ts, triggered_by=trig,
                llm_digest=tokens is not None,
                llm_input_tokens=tokens[0] if tokens else None,
                llm_output_tokens=tokens[1] if tokens else None,
            ))
        session.add(McpToolCallRow(user_id=REGULAR_ID, ts=now, tool="get_briefing"))
        # MCP connector pings (token last_used_at) are not activity.
        session.add(ApiTokenRow(
            user_id=REGULAR_ID, token_hash="h-mcp", scope="mcp", last_used_at=now,
        ))
        session.add(ApiTokenRow(
            user_id=ADMIN_ID, token_hash="h-other", scope=None, last_used_at=now,
        ))
        session.commit()
        session.close()

        resp = client.get("/api/admin/activity", cookies=_admin_cookie())
        assert resp.status_code == 200
        body = resp.json()
        assert body["windows"] == ["today", "yesterday", "week", "month", "lifetime"]
        m = body["metrics"]

        def row(key: str, part: str = "users") -> list[int]:
            return [m[key][part][w] for w in body["windows"]]

        assert row("new_users") == [0, 0, 1, 1, 2]
        assert m["new_users"]["count"] is None
        assert row("new_briefings") == [0, 1, 1, 2, 2]
        assert row("new_briefings", "count") == [0, 1, 1, 2, 2]
        # Admin's yesterday refresh + regular's today MCP refresh; the
        # scheduler run and each flight's first briefing are not refreshes.
        assert row("refreshes") == [1, 1, 2, 2, 2]
        assert row("refreshes", "count") == [1, 1, 2, 2, 2]
        # Users: tool calls or MCP briefings; count: logged tool calls only.
        assert row("mcp") == [1, 1, 1, 1, 1]
        assert row("mcp", "count") == [1, 0, 1, 1, 1]
        assert row("auto_refreshes") == [1, 0, 1, 1, 1]
        assert row("auto_refreshes", "count") == [1, 0, 1, 1, 1]
        assert row("llm_tokens") == [1, 1, 1, 1, 1]
        assert row("llm_tokens", "count") == [50, 120, 170, 170, 170]
        # Today: admin was seen (last_seen_at) and regular called MCP; the
        # scheduler run and the token pings don't count as activity.
        assert row("active_users") == [2, 2, 2, 2, 2]

    def test_requires_admin(self, client):
        token = create_token(REGULAR_ID, "user@test.com", "Regular User", TEST_SECRET)
        resp = client.get("/api/admin/activity", cookies={COOKIE_NAME: token})
        assert resp.status_code == 403


class TestAdminDigestSend:
    def test_send_reaches_admin_emails(self, client, monkeypatch):
        """Regression: the endpoint called an un-imported get_admin_emails (500)."""
        sent = {}
        monkeypatch.setattr(
            "weatherbrief.tasks.admin_digest_stats.get_admin_digest_data",
            lambda *a, **k: "data",
        )
        monkeypatch.setattr(
            "weatherbrief.notify.admin_digest_email.send_admin_digest",
            lambda emails, data: sent.update(emails=emails, data=data),
        )
        resp = client.post("/api/admin/digest/send", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert resp.json()["recipients"] == [ADMIN_EMAIL]
        assert sent == {"emails": [ADMIN_EMAIL], "data": "data"}


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
