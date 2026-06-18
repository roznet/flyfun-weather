"""Tests for the admin connected-apps view (dynamically-registered OAuth apps)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from flyfun_common.auth import COOKIE_NAME, create_token
from flyfun_common.db import get_db
from flyfun_common.db.models import ApiTokenRow, UserPreferencesRow, UserRow
from flyfun_common.oauth.models import OAuthClientRow

TEST_SECRET = "test-jwt-secret"
ADMIN_EMAIL = "admin@test.com"
ADMIN_ID = "admin-user-001"
REGULAR_ID = "regular-user-001"


@pytest.fixture
def db_session():
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


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
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


def _admin_cookie() -> dict:
    return {COOKIE_NAME: create_token(ADMIN_ID, ADMIN_EMAIL, "Admin", TEST_SECRET)}


def _user_cookie() -> dict:
    return {COOKIE_NAME: create_token(REGULAR_ID, "user@test.com", "Regular User", TEST_SECRET)}


def _seed_connected_apps(db_session) -> None:
    s = db_session()
    # A connected "MCP" app: registered client + two tokens for two users,
    # one of them revoked.
    s.add(OAuthClientRow(
        id="mcp_client_1", client_secret_hash="x", client_name="Claude (MCP)",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ))
    s.add(ApiTokenRow(
        user_id=ADMIN_ID, token_hash="h1", oauth_client_id="mcp_client_1",
        scope="mcp", revoked=False,
        last_used_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
    ))
    s.add(ApiTokenRow(
        user_id=REGULAR_ID, token_hash="h2", oauth_client_id="mcp_client_1",
        scope="mcp", revoked=True,
        last_used_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    ))
    # A read-only third-party app: one active flights:read token.
    s.add(OAuthClientRow(
        id="sample_client_1", client_secret_hash="x", client_name="flyfun-example",
        created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
    ))
    s.add(ApiTokenRow(
        user_id=REGULAR_ID, token_hash="h3", oauth_client_id="sample_client_1",
        scope="flights:read", revoked=False,
        last_used_at=datetime(2026, 6, 17, 12, tzinfo=timezone.utc),
    ))
    # A manually-created agent token (no oauth_client_id) — must be EXCLUDED.
    s.add(ApiTokenRow(
        user_id=ADMIN_ID, token_hash="h4", oauth_client_id=None,
        scope=None, revoked=False,
    ))
    s.commit()
    s.close()


def test_connected_apps_requires_admin(client):
    assert client.get("/api/admin/connected-apps", cookies=_user_cookie()).status_code == 403


def test_connected_apps_aggregates_and_excludes_non_oauth(client, db_session):
    _seed_connected_apps(db_session)

    resp = client.get("/api/admin/connected-apps", cookies=_admin_cookie())
    assert resp.status_code == 200
    apps = {a["name"]: a for a in resp.json()["apps"]}

    # Only the two OAuth clients show up — the manual agent token is excluded.
    assert set(apps) == {"Claude (MCP)", "flyfun-example"}

    mcp = apps["Claude (MCP)"]
    assert mcp["scopes"] == ["mcp"]
    assert mcp["tokens_total"] == 2
    assert mcp["tokens_active"] == 1
    assert mcp["users"] == 1        # only the non-revoked token's user counts as active
    assert mcp["users_total"] == 2
    assert mcp["last_used"].startswith("2026-06-18")
    assert mcp["registered"].startswith("2026-06-01")

    sample = apps["flyfun-example"]
    assert sample["scopes"] == ["flights:read"]
    assert sample["tokens_active"] == 1 and sample["tokens_total"] == 1
    assert sample["users"] == 1

    # Sorted most-recently-used first.
    names = [a["name"] for a in resp.json()["apps"]]
    assert names == ["Claude (MCP)", "flyfun-example"]


def test_connected_apps_empty(client):
    resp = client.get("/api/admin/connected-apps", cookies=_admin_cookie())
    assert resp.status_code == 200
    assert resp.json() == {"apps": []}
