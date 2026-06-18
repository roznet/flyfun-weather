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
    # "Claude" connected via Dynamic Client Registration TWICE — two distinct
    # client_ids reporting the same name, registered on different dates. These
    # must collapse into ONE row when grouped by name.
    s.add(OAuthClientRow(
        id="claude_reg_1", client_secret_hash="x", client_name="Claude",
        created_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
    ))
    s.add(OAuthClientRow(
        id="claude_reg_2", client_secret_hash="x", client_name="Claude",
        created_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
    ))
    # First registration: admin's token (active) + a rotated-out revoked one.
    s.add(ApiTokenRow(
        user_id=ADMIN_ID, token_hash="h1", oauth_client_id="claude_reg_1",
        scope="mcp", revoked=False,
        last_used_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
    ))
    s.add(ApiTokenRow(
        user_id=ADMIN_ID, token_hash="h2", oauth_client_id="claude_reg_1",
        scope="mcp", revoked=True,
        last_used_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    ))
    # Second registration: a different user, active token (so 2 distinct users).
    s.add(ApiTokenRow(
        user_id=REGULAR_ID, token_hash="h3", oauth_client_id="claude_reg_2",
        scope="mcp", revoked=False,
        last_used_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    ))
    # A read-only third-party app: one active flights:read token.
    s.add(OAuthClientRow(
        id="sample_client_1", client_secret_hash="x", client_name="flyfun-example",
        created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
    ))
    s.add(ApiTokenRow(
        user_id=REGULAR_ID, token_hash="h4", oauth_client_id="sample_client_1",
        scope="flights:read", revoked=False,
        last_used_at=datetime(2026, 6, 17, 12, tzinfo=timezone.utc),
    ))
    # A manually-created agent token (no oauth_client_id) — must be EXCLUDED.
    s.add(ApiTokenRow(
        user_id=ADMIN_ID, token_hash="h5", oauth_client_id=None,
        scope=None, revoked=False,
    ))
    s.commit()
    s.close()


def test_connected_apps_requires_admin(client):
    assert client.get("/api/admin/connected-apps", cookies=_user_cookie()).status_code == 403


def test_connected_apps_groups_by_name_and_excludes_non_oauth(client, db_session):
    _seed_connected_apps(db_session)

    resp = client.get("/api/admin/connected-apps", cookies=_admin_cookie())
    assert resp.status_code == 200
    apps = {a["name"]: a for a in resp.json()["apps"]}

    # Two app names — the manual agent token is excluded, and the two "Claude"
    # registrations collapse into a single row.
    assert set(apps) == {"Claude", "flyfun-example"}

    claude = apps["Claude"]
    assert claude["registrations"] == 2          # both DCR registrations merged
    assert claude["scopes"] == ["mcp"]
    assert claude["tokens_total"] == 3           # 2 under reg_1 + 1 under reg_2
    assert claude["tokens_active"] == 2          # one revoked under reg_1
    assert claude["users"] == 2                  # admin + regular, distinct active users
    assert claude["users_total"] == 2
    assert claude["last_used"].startswith("2026-06-18")
    assert claude["registered"].startswith("2026-04-09")  # earliest registration

    sample = apps["flyfun-example"]
    assert sample["registrations"] == 1
    assert sample["scopes"] == ["flights:read"]
    assert sample["tokens_active"] == 1 and sample["tokens_total"] == 1
    assert sample["users"] == 1

    # Sorted most-recently-used first.
    names = [a["name"] for a in resp.json()["apps"]]
    assert names == ["Claude", "flyfun-example"]


def test_connected_apps_empty(client):
    resp = client.get("/api/admin/connected-apps", cookies=_admin_cookie())
    assert resp.status_code == 200
    assert resp.json() == {"apps": []}
