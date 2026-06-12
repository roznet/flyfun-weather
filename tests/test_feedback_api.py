"""Tests for /api/feedback: thumb ratings, consent gating, traditional form."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserRow
from weatherbrief.api.admin import require_admin
from weatherbrief.api.app import create_app
from weatherbrief.api import throttle
from weatherbrief.db.models import FeedbackRow


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev", approved=True,
    ))
    s.commit()
    s.close()
    yield TestSession
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """The burst limiter allows 1 req/min — neutralize it for tests."""
    monkeypatch.setattr(throttle.feedback_burst_limiter, "max_requests", 1000)
    monkeypatch.setattr(throttle.feedback_daily_limiter, "max_requests", 1000)
    throttle.feedback_burst_limiter._hits.clear()
    throttle.feedback_daily_limiter._hits.clear()


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    """Don't send real emails from tests."""
    from weatherbrief.notify import admin_email
    monkeypatch.setattr(admin_email, "send_feedback_notification", lambda **kw: None)


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


def _get_row(app_db, feedback_id: int) -> FeedbackRow:
    s = app_db()
    try:
        return s.query(FeedbackRow).filter(FeedbackRow.id == feedback_id).one()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Submit endpoint
# ---------------------------------------------------------------------------

def test_thumb_up_empty_comment_succeeds(client, app_db):
    resp = client.post("/api/feedback", json={
        "flight_id": "egtk_lfat-2026-06-12-abcd",
        "pack_timestamp": "2026-06-12T06:00:00+00:00",
        "category": "digest_rating",
        "comment": "",
        "sentiment": "up",
        "target": "digest",
        "contact_ok": False,
    })
    assert resp.status_code == 200, resp.text
    row = _get_row(app_db, resp.json()["id"])
    assert row.sentiment == "up"
    assert row.target == "digest"
    assert row.category == "digest_rating"
    assert row.comment == ""
    assert row.contact_ok is False


def test_thumb_down_with_comment_and_consent(client, app_db):
    resp = client.post("/api/feedback", json={
        "flight_id": "egtk_lfat-2026-06-12-abcd",
        "pack_timestamp": "2026-06-12T06:00:00+00:00",
        "category": "digest_rating",
        "comment": "The icing advisory did not match the conditions I saw.",
        "sentiment": "down",
        "target": "digest",
        "contact_ok": True,
    })
    assert resp.status_code == 200, resp.text
    row = _get_row(app_db, resp.json()["id"])
    assert row.sentiment == "down"
    assert row.target == "digest"
    assert row.comment == "The icing advisory did not match the conditions I saw."
    assert row.contact_ok is True


def test_thumb_down_bare_is_recorded(client, app_db):
    resp = client.post("/api/feedback", json={
        "category": "digest_rating",
        "comment": "",
        "sentiment": "down",
        "target": "digest",
    })
    assert resp.status_code == 200, resp.text
    row = _get_row(app_db, resp.json()["id"])
    assert row.sentiment == "down"
    assert row.comment == ""


def test_traditional_form_still_requires_comment(client):
    resp = client.post("/api/feedback", json={
        "category": "other",
        "comment": "   ",
    })
    assert resp.status_code == 422


def test_traditional_form_defaults(client, app_db):
    resp = client.post("/api/feedback", json={
        "category": "data_issue",
        "comment": "Wind at EGTK looked wrong.",
    })
    assert resp.status_code == 200, resp.text
    row = _get_row(app_db, resp.json()["id"])
    assert row.sentiment is None
    assert row.target is None
    assert row.contact_ok is False


def test_invalid_sentiment_rejected(client):
    resp = client.post("/api/feedback", json={
        "category": "digest_rating",
        "comment": "",
        "sentiment": "sideways",
        "target": "digest",
    })
    assert resp.status_code == 422


def test_invalid_target_rejected(client):
    resp = client.post("/api/feedback", json={
        "category": "digest_rating",
        "comment": "",
        "sentiment": "up",
        "target": "advisory",
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Admin: serialization + consent gate on send-reply
# ---------------------------------------------------------------------------

def test_admin_list_includes_new_fields(client):
    resp = client.post("/api/feedback", json={
        "category": "digest_rating",
        "comment": "",
        "sentiment": "up",
        "target": "digest",
    })
    assert resp.status_code == 200

    entries = client.get("/api/feedback/admin").json()
    entry = next(e for e in entries if e["id"] == resp.json()["id"])
    assert entry["sentiment"] == "up"
    assert entry["target"] == "digest"
    assert entry["contact_ok"] is False


def test_send_reply_blocked_without_consent(client, monkeypatch):
    from weatherbrief.notify import admin_email
    sent = []
    monkeypatch.setattr(admin_email, "send_feedback_reply", lambda **kw: sent.append(kw))

    resp = client.post("/api/feedback", json={
        "category": "other",
        "comment": "Something broke.",
        "contact_ok": False,
    })
    fb_id = resp.json()["id"]

    resp = client.post(f"/api/feedback/admin/{fb_id}/send", json={"reply": "We fixed it."})
    assert resp.status_code == 403
    assert "consent" in resp.json()["detail"].lower()
    assert sent == []


def test_send_reply_allowed_with_consent(client, monkeypatch):
    from weatherbrief.notify import admin_email
    sent = []
    monkeypatch.setattr(admin_email, "send_feedback_reply", lambda **kw: sent.append(kw))

    resp = client.post("/api/feedback", json={
        "category": "digest_rating",
        "comment": "Digest missed the front entirely.",
        "sentiment": "down",
        "target": "digest",
        "contact_ok": True,
    })
    fb_id = resp.json()["id"]

    resp = client.post(f"/api/feedback/admin/{fb_id}/send", json={"reply": "Thanks, looking into it."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "replied"
    assert len(sent) == 1
    assert sent[0]["to_email"] == "dev@localhost"
