"""Tests for the preferences API endpoints."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory."""
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    """Create a test client with isolated DB."""
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
        assert data["autorouter_mode"] == "password"  # dev environment
        assert data["defaults"]["cruise_altitude_ft"] is None
        assert data["defaults"]["models"] is None
        assert data["units_region"] == "auto"  # default

    def test_units_region_round_trip(self, client):
        resp = client.put("/api/user/preferences", json={"units_region": "us"})
        assert resp.status_code == 200
        assert resp.json()["units_region"] == "us"
        # persists and is surfaced on the current-user payload
        assert client.get("/api/user/preferences").json()["units_region"] == "us"
        assert client.get("/auth/me").json()["units_region"] == "us"

    def test_units_region_rejects_invalid(self, client):
        resp = client.put("/api/user/preferences", json={"units_region": "metric"})
        assert resp.status_code == 422

    def test_flight_order_defaults_to_furthest_first(self, client, app_db):
        # Absent from app_prefs_json → today's ordering, for every existing user.
        assert client.get("/api/user/preferences").json()["flight_order"] == "furthest_first"

        from weatherbrief.api.preferences import load_flight_order

        s = app_db()
        assert load_flight_order(s, DEV_USER_ID) == "furthest_first"
        s.close()

    def test_flight_order_round_trip(self, client, app_db):
        resp = client.put("/api/user/preferences", json={"flight_order": "soonest_first"})
        assert resp.status_code == 200
        assert resp.json()["flight_order"] == "soonest_first"
        assert client.get("/api/user/preferences").json()["flight_order"] == "soonest_first"

        from weatherbrief.api.preferences import load_flight_order

        s = app_db()
        assert load_flight_order(s, DEV_USER_ID) == "soonest_first"
        s.close()

    def test_flight_order_rejects_invalid(self, client):
        resp = client.put("/api/user/preferences", json={"flight_order": "chronological"})
        assert resp.status_code == 422

    def test_flight_order_does_not_disturb_other_prefs(self, client):
        client.put("/api/user/preferences", json={"units_region": "us"})
        client.put("/api/user/preferences", json={"flight_order": "soonest_first"})
        data = client.get("/api/user/preferences").json()
        assert data["units_region"] == "us"
        assert data["flight_order"] == "soonest_first"

    def test_load_flight_order_ignores_unknown_stored_value(self, client, app_db):
        """A hand-edited blob can't produce an order no client knows how to render."""
        from weatherbrief.api.preferences import load_flight_order

        client.get("/api/user/preferences")  # ensure the row exists
        s = app_db()
        row = s.get(UserPreferencesRow, DEV_USER_ID)
        data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
        data["flight_order"] = "sideways"
        row.app_prefs_json = json.dumps(data)
        s.commit()
        assert load_flight_order(s, DEV_USER_ID) == "furthest_first"
        s.close()

    def test_defer_email_for_model_update_round_trip(self, client, app_db):
        # Default off (current behaviour).
        assert client.get("/api/user/preferences").json()["defer_email_for_model_update"] is False

        resp = client.put(
            "/api/user/preferences",
            json={"defer_email_for_model_update": True},
        )
        assert resp.status_code == 200
        assert resp.json()["defer_email_for_model_update"] is True
        assert client.get("/api/user/preferences").json()["defer_email_for_model_update"] is True

        # The scheduler's loader reads the same stored value.
        from weatherbrief.api.preferences import load_defer_email_for_model_update

        session = app_db()
        try:
            assert load_defer_email_for_model_update(session, DEV_USER_ID) is True
        finally:
            session.close()

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
    """Test that profile settings are applied when creating flights."""

    def _get_default_profile_id(self, client) -> int:
        """Get the user's default profile id (auto-created on first list)."""
        resp = client.get("/api/user/profiles")
        assert resp.status_code == 200
        profiles = resp.json()
        default = next(p for p in profiles if p["is_default"])
        return default["id"]

    def test_flight_uses_profile_defaults(self, client):
        """Flight created without altitude uses profile's settings."""
        pid = self._get_default_profile_id(client)
        client.put(f"/api/user/profiles/{pid}", json={
            "settings": {"cruise_altitude_ft": 6000, "flight_ceiling_ft": 14000},
        })
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB"],
            "departure_time": "2026-06-01T09:00:00Z",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["cruise_altitude_ft"] == 6000
        assert data["flight_ceiling_ft"] == 14000

    def test_flight_explicit_overrides_defaults(self, client):
        """Explicit values in the request override profile defaults."""
        pid = self._get_default_profile_id(client)
        client.put(f"/api/user/profiles/{pid}", json={
            "settings": {"cruise_altitude_ft": 6000},
        })
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB"],
            "departure_time": "2026-06-02T09:00:00Z",
            "cruise_altitude_ft": 10000,
        })
        assert resp.status_code == 201
        assert resp.json()["cruise_altitude_ft"] == 10000

    def test_flight_system_defaults_without_profile_settings(self, client):
        """Without profile settings, system defaults (8000/18000) are used."""
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB"],
            "departure_time": "2026-06-03T09:00:00Z",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["cruise_altitude_ft"] == 8000
        assert data["flight_ceiling_ft"] == 18000


class TestProfilesRouteOrdering:
    """Literal profile sub-routes must not be shadowed by /{profile_id:int}."""

    def test_system_templates_resolves(self, client):
        # Before the int-converter fix this hit GET /{profile_id} and 422'd
        # trying to parse "system-templates" as an int.
        resp = client.get("/api/user/profiles/system-templates")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_digest_guidance_presets_resolves(self, client):
        resp = client.get("/api/user/profiles/digest-guidance-presets")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestDeclaredApproaches:
    """User-declared unpublished approaches (issue #510).

    Storage is a plain ``app_prefs_json`` key rather than an advisory param
    because it is a fact about the pilot and the airport ("I'm current on the
    Fairoaks cloud break"), not about a flight profile — entered once, applied
    everywhere.
    """

    def test_default_is_empty(self, client):
        assert client.get("/api/user/preferences").json()["declared_approach_icaos"] == []

    def test_free_form_text_is_canonicalized(self, client):
        """The field is free-form; the canonical shape is the server's job.

        Normalizing here rather than in the web client means every writer — a
        direct PUT, a future iOS screen — stores the same thing.
        """
        resp = client.put(
            "/api/user/preferences",
            json={"declared_approach_icaos": " egtf, EGSX  egtf;eglk "},
        )
        assert resp.status_code == 200
        assert resp.json()["declared_approach_icaos"] == ["EGTF", "EGSX", "EGLK"]
        # …and survives the round trip
        assert client.get("/api/user/preferences").json()["declared_approach_icaos"] == [
            "EGTF", "EGSX", "EGLK",
        ]

    def test_accepts_a_json_array_too(self, client):
        resp = client.put(
            "/api/user/preferences", json={"declared_approach_icaos": ["egtf", "EGSX"]},
        )
        assert resp.json()["declared_approach_icaos"] == ["EGTF", "EGSX"]

    def test_can_be_cleared(self, client):
        client.put("/api/user/preferences", json={"declared_approach_icaos": "EGTF"})
        resp = client.put("/api/user/preferences", json={"declared_approach_icaos": ""})
        assert resp.status_code == 200
        assert resp.json()["declared_approach_icaos"] == []

    def test_preserves_sibling_keys(self, client):
        """The merge-preserving write — a declaration must not drop settings."""
        client.put("/api/user/preferences", json={"units_region": "us"})
        client.put("/api/user/preferences", json={"declared_approach_icaos": "EGTF"})
        data = client.get("/api/user/preferences").json()
        assert data["units_region"] == "us"
        assert data["declared_approach_icaos"] == ["EGTF"]

    def test_unknown_code_is_rejected_and_nothing_is_stored(self, client, monkeypatch):
        """A typo'd EGFT must come back, never be silently dropped.

        Dropping it would leave the pilot believing their home field is
        declared when it is not — silently reinstating the very false REDs the
        declaration removes.
        """
        client.put("/api/user/preferences", json={"declared_approach_icaos": "EGTF"})
        monkeypatch.setattr(
            "weatherbrief.airports.unknown_icaos", lambda codes, db_path: ["EGFT"],
        )
        resp = client.put(
            "/api/user/preferences", json={"declared_approach_icaos": "EGTF EGFT"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "unknown_airports"
        assert detail["codes"] == ["EGFT"]
        assert "EGFT" in detail["message"]
        # The whole write is rejected — the previous list is untouched.
        assert client.get("/api/user/preferences").json()["declared_approach_icaos"] == ["EGTF"]

    def test_validation_is_skipped_when_the_airport_db_is_unavailable(self):
        """A missing AIRPORTS_DB is a server-config gap, not the user's typo."""
        from weatherbrief.airports import unknown_icaos

        assert unknown_icaos(["EGTF"], "/nonexistent/nav.db") == []
        assert unknown_icaos([], "") == []

    def test_load_helper_reads_the_stored_list(self, client, app_db):
        """The single read path used by every advisory entry point."""
        from weatherbrief.api.preferences import load_declared_approaches

        client.put("/api/user/preferences", json={"declared_approach_icaos": "egtf EGSX"})
        session = app_db()
        try:
            assert load_declared_approaches(session, DEV_USER_ID) == ["EGTF", "EGSX"]
            assert load_declared_approaches(session, "nobody") == []
        finally:
            session.close()
