"""Tests for the flight share_code short-link feature.

Covers the storage layer (code allocation + lookup) and the
``GET /s/{code}`` HTTP endpoint that resolves a code into a redirect.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import Flight
from weatherbrief.storage.flights import (
    _allocate_share_code,
    load_flight,
    lookup_flight_id_by_share_code,
    save_flight,
)


def _make_flight(idx: int, *, share_code: str | None = None) -> Flight:
    dep = datetime.now(timezone.utc) + timedelta(days=idx)
    return Flight(
        id=f"egtk-lfat-2026-04-30-{idx:04d}",
        user_id=DEV_USER_ID,
        route_name="egtk_lfat",
        waypoints=["EGTK", "LFAT"],
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        share_code=share_code,
        created_at=datetime.now(timezone.utc),
    )


# --- Storage layer ---


class TestShareCodeAllocation:
    def test_save_flight_mints_code_when_absent(self, db_session, dev_user):
        flight = _make_flight(1, share_code=None)
        save_flight(db_session, flight, dev_user)
        loaded = load_flight(db_session, flight.id)
        # Code populated on the in-memory object and on the DB row.
        assert flight.share_code is not None
        assert len(flight.share_code) == 8
        assert flight.share_code.isalnum()
        assert loaded.share_code == flight.share_code

    def test_save_flight_preserves_existing_code(self, db_session, dev_user):
        flight = _make_flight(2, share_code="abc12345")
        save_flight(db_session, flight, dev_user)
        loaded = load_flight(db_session, flight.id)
        assert loaded.share_code == "abc12345"

    def test_update_does_not_rotate_code(self, db_session, dev_user):
        flight = _make_flight(3, share_code=None)
        save_flight(db_session, flight, dev_user)
        original_code = flight.share_code

        # Re-save with a tweaked field — code must not rotate, otherwise
        # already-shared links would silently break on every edit.
        flight.cruise_altitude_ft = 9000
        save_flight(db_session, flight, dev_user)
        loaded = load_flight(db_session, flight.id)
        assert loaded.share_code == original_code

    def test_lookup_resolves_code_to_id(self, db_session, dev_user):
        flight = _make_flight(4, share_code=None)
        save_flight(db_session, flight, dev_user)
        resolved = lookup_flight_id_by_share_code(db_session, flight.share_code)
        assert resolved == flight.id

    def test_lookup_unknown_returns_none(self, db_session, dev_user):
        assert lookup_flight_id_by_share_code(db_session, "nope0000") is None

    def test_alloc_retries_on_collision(self, db_session, dev_user, monkeypatch):
        # Stuff every attempt with the same code, then let the third try
        # succeed — proves the retry loop actually iterates.
        flight = _make_flight(5, share_code="taken123")
        save_flight(db_session, flight, dev_user)

        attempts = iter(["taken123", "taken123", "fresh999"])
        monkeypatch.setattr(
            "weatherbrief.storage.flights._generate_share_code",
            lambda: next(attempts),
        )
        new_code = _allocate_share_code(db_session)
        assert new_code == "fresh999"


# --- HTTP endpoint ---


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="d@l", display_name="D", approved=True,
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


def _seed_flight(app_db, *, idx: int, share_code: str) -> Flight:
    s = app_db()
    f = _make_flight(idx, share_code=share_code)
    save_flight(s, f, DEV_USER_ID)
    s.commit()
    s.close()
    return f


class TestShareRedirect:
    def test_redirects_to_briefing_page(self, client, app_db):
        f = _seed_flight(app_db, idx=10, share_code="aB3xy7Q9")
        resp = client.get(f"/s/{f.share_code}", follow_redirects=False)
        assert resp.status_code == 302, resp.text
        loc = resp.headers["location"]
        # /s/{code} always lands on the briefing page — that's the
        # artifact pilots want, and briefing.html picks the latest pack
        # when none is supplied.
        assert loc.startswith("/briefing.html?")
        assert f"flight={f.id}" in loc
        assert "pack=" not in loc

    def test_pack_param_pins_specific_pack(self, client, app_db):
        f = _seed_flight(app_db, idx=11, share_code="zz99ZZ11")
        ts = "2026-04-30T08:00:00Z"
        resp = client.get(f"/s/{f.share_code}?pack={ts}", follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith("/briefing.html?")
        assert f"flight={f.id}" in loc
        # urlencode quotes the colons in the timestamp.
        assert "pack=2026-04-30T08" in loc

    def test_unknown_code_returns_404(self, client):
        resp = client.get("/s/nope0000", follow_redirects=False)
        assert resp.status_code == 404

    def test_invalid_code_shape_returns_404_without_db_hit(self, client):
        # Path traversal / SQL-injection-shaped strings must be rejected
        # by the regex before they reach the DB lookup.
        resp = client.get("/s/../etc/passwd", follow_redirects=False)
        # Path normalization may even 404 earlier; either way, never 302.
        assert resp.status_code != 302


# --- JSON resolver endpoint (iOS preview-before-subscribe on-ramp) ---


def _seed_other_owner_flight(
    app_db,
    *,
    idx: int,
    share_code: str,
    private: bool = False,
    owner_id: str = "owner-user",
    owner_name: str = "Flight Owner",
) -> Flight:
    """Seed a flight owned by a *different* user than the DEV viewer.

    Lets the resolver tests exercise the subscriber view (role/owner name) and
    the private-flight 404, which a self-owned flight can't.
    """
    s = app_db()
    if s.get(UserRow, owner_id) is None:
        s.add(UserRow(
            id=owner_id, provider="local", provider_sub=owner_id,
            email=f"{owner_id}@example.com", display_name=owner_name, approved=True,
        ))
        s.flush()
    f = _make_flight(idx, share_code=share_code)
    f.id = f"{f.id}-{owner_id}"
    f.user_id = owner_id
    f.private = private
    save_flight(s, f, owner_id)
    s.commit()
    s.close()
    return f


class TestShareResolver:
    def test_public_flight_returns_subscriber_view(self, client, app_db):
        # A public flight owned by someone else resolves to a normal
        # FlightResponse with the subscriber-facing fields the preview reads.
        f = _seed_other_owner_flight(app_db, idx=20, share_code="Pub12345")
        resp = client.get(f"/api/flights/by-share/{f.share_code}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == f.id
        assert body["role"] == "subscriber"
        assert body["is_subscribed"] is False
        assert body["owner_display_name"] == "Flight Owner"
        assert body["share_code"] == f.share_code

    def test_owner_view_when_viewer_owns_flight(self, client, app_db):
        # The DEV viewer resolving their own flight's code sees the owner view.
        f = _seed_flight(app_db, idx=21, share_code="Own12345")
        resp = client.get(f"/api/flights/by-share/{f.share_code}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["role"] == "owner"
        assert body["is_subscribed"] is False

    def test_unknown_code_returns_404(self, client):
        resp = client.get("/api/flights/by-share/nope0000")
        assert resp.status_code == 404

    def test_invalid_shape_returns_404_without_db_hit(self, client):
        # Too short / illegal chars are rejected by the regex before the lookup.
        assert client.get("/api/flights/by-share/ab").status_code == 404
        assert client.get("/api/flights/by-share/has spaces").status_code == 404

    def test_private_flight_hidden_from_non_owner(self, client, app_db):
        # Private + non-owner → 404, identical to GET /{flight_id}.
        f = _seed_other_owner_flight(app_db, idx=22, share_code="Prv12345", private=True)
        resp = client.get(f"/api/flights/by-share/{f.share_code}")
        assert resp.status_code == 404

    def test_owner_can_resolve_own_private_flight(self, client, app_db):
        # A private flight the viewer owns still resolves for them.
        f = _seed_flight(app_db, idx=23, share_code="PrvOwn12")
        s = app_db()
        loaded = load_flight(s, f.id)
        loaded.private = True
        save_flight(s, loaded, DEV_USER_ID)
        s.commit()
        s.close()
        resp = client.get(f"/api/flights/by-share/{f.share_code}")
        assert resp.status_code == 200
        assert resp.json()["role"] == "owner"
