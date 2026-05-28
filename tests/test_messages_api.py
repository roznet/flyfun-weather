"""Tests for the system-messages (What's New) API, focused on the highlight
flag that decouples "appears in the stream" from "lights the notification dot".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.db.models import SystemMessageRow


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
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
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
    return TestClient(app, raise_server_exceptions=False)


def _add(app_db, *, title, highlight, date="2026-05-20", category="feature"):
    s = app_db()
    row = SystemMessageRow(date=date, title=title, body="b", category=category, highlight=highlight)
    s.add(row)
    s.commit()
    rid = row.id
    s.close()
    return rid


def test_stream_includes_highlight_field(client, app_db):
    _add(app_db, title="lit", highlight=True)
    _add(app_db, title="quiet", highlight=False)

    r = client.get("/api/messages")
    assert r.status_code == 200
    by_title = {m["title"]: m for m in r.json()}
    assert by_title["lit"]["highlight"] is True
    assert by_title["quiet"]["highlight"] is False


def test_status_counts_only_highlighted(client, app_db):
    # Two highlighted + one quiet; only the highlighted ones light the dot.
    _add(app_db, title="lit1", highlight=True)
    _add(app_db, title="lit2", highlight=True)
    _add(app_db, title="quiet", highlight=False)

    r = client.get("/api/messages/status")
    assert r.status_code == 200
    assert r.json()["unseen_count"] == 2


def test_seen_clears_then_new_highlight_relights(client, app_db):
    _add(app_db, title="lit1", highlight=True)
    _add(app_db, title="quiet", highlight=False)
    assert client.get("/api/messages/status").json()["unseen_count"] == 1

    # Marking seen moves the watermark past all current messages.
    assert client.post("/api/messages/seen").status_code == 204
    assert client.get("/api/messages/status").json()["unseen_count"] == 0

    # A later quiet release does NOT relight the dot...
    _add(app_db, title="quiet2", highlight=False)
    assert client.get("/api/messages/status").json()["unseen_count"] == 0

    # ...but a later highlighted release does.
    _add(app_db, title="lit2", highlight=True)
    assert client.get("/api/messages/status").json()["unseen_count"] == 1
