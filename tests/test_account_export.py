"""Tests for /api/account/export (GDPR data portability) and mask_email."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserRow, UserPreferencesRow
from weatherbrief.api.admin import require_admin
from weatherbrief.api.app import create_app
from weatherbrief.db.models import (
    DeviceTokenRow,
    FeedbackRow,
    FlightRow,
    UserAircraftRow,
)
from weatherbrief.privacy import mask_email


# ---------------------------------------------------------------------------
# mask_email
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("brice.rosenzweig@gmail.com", "b***@gmail.com"),
    ("a@x.com", "a***@x.com"),
    ("@nolocal.com", "***@nolocal.com"),
    ("notanemail", "n***"),
    ("", "<empty>"),
])
def test_mask_email_single(raw, expected):
    assert mask_email(raw) == expected


def test_mask_email_none():
    assert mask_email(None) == "<none>"


def test_mask_email_iterable():
    assert mask_email(["x@a.com", "y@b.com"]) == "x***@a.com, y***@b.com"


def test_mask_email_does_not_leak_local_part():
    masked = mask_email("johnsmith@example.com")
    assert "johnsmith" not in masked
    assert masked == "j***@example.com"


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(
        id=DEV_USER_ID, provider="google", provider_sub="secret-sub-123",
        email="dev@localhost", display_name="Dev", approved=True,
    ))
    s.flush()  # ensure the user row exists before FK-dependent rows
    s.add(UserPreferencesRow(
        user_id=DEV_USER_ID,
        setup_completed=True,
        encrypted_creds_json="SUPER_SECRET_ENCRYPTED_BLOB",
        app_prefs_json='{"locale": "en", "units_region": "europe"}',
    ))
    s.add(UserAircraftRow(user_id=DEV_USER_ID, icao_type="SR22", tail_number="N123AB"))
    s.add(FeedbackRow(
        user_id=DEV_USER_ID, category="general", comment="Great app", contact_ok=True,
        ai_analysis="INTERNAL AI NOTES", triage_prompt="INTERNAL PROMPT",
    ))
    s.add(DeviceTokenRow(
        user_id=DEV_USER_ID, token="SECRET_PUSH_TOKEN", environment="production",
    ))
    s.commit()
    s.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test")
    app = create_app()

    def _override_get_db():
        s = app_db()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    app.dependency_overrides[require_admin] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


def test_export_returns_json_attachment(client):
    resp = client.get("/api/account/export")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd and ".json" in cd


def test_export_contains_all_sections(client):
    data = client.get("/api/account/export").json()
    for key in (
        "account", "preferences", "aircraft", "flight_profiles", "flights",
        "feedback", "usage", "pireps", "device_registrations",
        "format_version", "exported_at", "user_id",
    ):
        assert key in data, f"missing section {key}"
    assert data["account"]["email"] == "dev@localhost"
    assert data["aircraft"][0]["icao_type"] == "SR22"
    assert data["feedback"][0]["comment"] == "Great app"


def test_export_parses_json_columns(client):
    data = client.get("/api/account/export").json()
    # app_prefs_json must be a parsed object, not a JSON-encoded string.
    assert isinstance(data["preferences"]["app_prefs_json"], dict)
    assert data["preferences"]["app_prefs_json"]["units_region"] == "europe"


def test_export_excludes_secrets_and_internals(client):
    body = client.get("/api/account/export").text
    data = client.get("/api/account/export").json()
    # Encrypted credentials must never appear.
    assert "SUPER_SECRET_ENCRYPTED_BLOB" not in body
    assert "encrypted_creds_json" not in data["preferences"]
    # OAuth provider_sub and session-invalidation timestamp are internal.
    assert "provider_sub" not in data["account"]
    assert "tokens_valid_after" not in data["account"]
    # Push token value is a credential.
    assert "SECRET_PUSH_TOKEN" not in body
    assert all("token" not in d for d in data["device_registrations"])
    # AI-triage internals are not the user's personal data.
    assert "INTERNAL AI NOTES" not in body
    assert "ai_analysis" not in data["feedback"][0]
    assert "triage_prompt" not in data["feedback"][0]


def test_export_requires_existing_user(client, app_db):
    # Wipe the user; export should 401 rather than emit a half-empty document.
    s = app_db()
    s.query(UserRow).delete()
    s.commit()
    s.close()
    resp = client.get("/api/account/export")
    assert resp.status_code == 401
