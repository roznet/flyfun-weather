"""Tests for the FastAPI API endpoints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Dynamic future dates so tests don't break as time passes
_NOW = datetime.now(timezone.utc)
_FUTURE_DEPARTURE = _NOW + timedelta(days=3)
_FUTURE_DEPARTURE_DATE = _FUTURE_DEPARTURE.strftime("%Y-%m-%d")
_FUTURE_DEPARTURE_ISO = _FUTURE_DEPARTURE.replace(
    hour=9, minute=0, second=0, microsecond=0,
).isoformat()
_FUTURE_DEPARTURE_DT = _FUTURE_DEPARTURE.replace(
    hour=9, minute=0, second=0, microsecond=0,
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory for the test app."""
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
    """Create a test client with isolated DB and config directories."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")  # skip lifespan init_db
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-api-tests")

    app = create_app()

    # Override the DB dependency to use our test session
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


def _make_flight_id(route_name, date_str, *, time="09:00", alt=8000, ceil=18000, dur=4.5, user=DEV_USER_ID):
    """Compute flight ID the same way as the API endpoint."""
    h = hashlib.sha256(json.dumps(
        {"alt": alt, "ceil": ceil, "dur": dur, "time": time, "user": user},
        sort_keys=True,
    ).encode()).hexdigest()[:4]
    return f"{route_name}-{date_str}-{h}"


@pytest.fixture
def sample_flight(app_db):
    """Create and save a sample flight."""
    session = app_db()
    flight = Flight(
        id=_make_flight_id("egtk_lsgs", _FUTURE_DEPARTURE_DATE),
        user_id=DEV_USER_ID,
        route_name="egtk_lsgs",
        waypoints=["EGTK", "LFPB", "LSGS"],
        departure_time=_FUTURE_DEPARTURE_DT,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=4.5,
        created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, DEV_USER_ID)
    session.commit()
    session.close()
    return flight


@pytest.fixture
def sample_pack(app_db, sample_flight):
    """Create and save a sample pack for the sample flight."""
    session = app_db()
    meta = BriefingPackMeta(
        flight_id=sample_flight.id,
        fetch_timestamp=_NOW - timedelta(hours=6),
        days_out=3,
        has_gramet=True,
        has_skewt=True,
        has_digest=False,
        assessment="GREEN",
        assessment_reason="Conditions favorable",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()
    return meta


# --- Health ---


class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}



# --- Flights ---


class TestFlightsAPI:
    def test_list_flights_empty(self, client):
        resp = client.get("/api/flights")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_flight(self, client):
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "departure_time": _FUTURE_DEPARTURE_ISO,
            "cruise_altitude_ft": 8000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith(f"egtk_lsgs-{_FUTURE_DEPARTURE_DATE}-")
        assert data["route_name"] == "egtk_lsgs"
        assert data["waypoints"] == ["EGTK", "LFPB", "LSGS"]
        assert data["target_date"] == _FUTURE_DEPARTURE_DATE
        assert data["departure_time"].startswith(f"{_FUTURE_DEPARTURE_DATE}T09:00:00")

    def test_create_flight_with_raw_route(self, client):
        """raw_route flows through and gets stamped with parser_version."""
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "raw_route": "EGTK DCT LFPB DCT LSGS",
            "departure_time": _FUTURE_DEPARTURE_ISO,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["raw_route"] == "EGTK DCT LFPB DCT LSGS"
        assert data["parser_version"] is not None
        assert data["parser_version"].startswith("euro_aip/")

    def test_create_flight_without_raw_route_stays_null(self, client):
        """iOS/MCP-style create (waypoints only) — both columns NULL."""
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "departure_time": _FUTURE_DEPARTURE_ISO,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["raw_route"] is None
        assert data["parser_version"] is None

    def test_create_flight_with_inline_coord_waypoint(self, client):
        """Validator accepts ICAO inline coords alongside named codes."""
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "5000N00200W", "LSGS"],
            "departure_time": _FUTURE_DEPARTURE_ISO,
        })
        # Real DB lookup is mocked away in TestFlightsAPI (no app.state.db_path)
        # so the resolver guard is skipped — we're only asserting the validator
        # accepts the coord shape, which is the change under test.
        assert resp.status_code == 201
        data = resp.json()
        assert data["waypoints"] == ["EGTK", "5000N00200W", "LSGS"]

    def test_create_flight_rejects_unparseable_token(self, client):
        """Tokens that are neither named nor a valid coord shape get rejected."""
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "ZZZZZZZ", "LSGS"],
            "departure_time": _FUTURE_DEPARTURE_ISO,
        })
        assert resp.status_code == 422

    def test_create_flight_defaults(self, client):
        _alt_future = (_NOW + timedelta(days=5)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LSGS"],
            "departure_time": _alt_future.isoformat(),
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["route_name"] == "egtk_lsgs"
        assert data["target_time_utc"] == 9
        assert data["cruise_altitude_ft"] == 8000
        assert data["flight_duration_hours"] == 0.0

    def test_create_flight_duplicate(self, client, sample_flight):
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "departure_time": _FUTURE_DEPARTURE_ISO,
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 18000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 409

    def test_list_flights_with_data(self, client, sample_flight):
        resp = client.get("/api/flights")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == sample_flight.id

    def test_get_flight(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["route_name"] == "egtk_lsgs"

    def test_get_flight_not_found(self, client):
        resp = client.get("/api/flights/nonexistent")
        assert resp.status_code == 404

    def test_delete_flight(self, client, sample_flight):
        resp = client.delete(f"/api/flights/{sample_flight.id}")
        assert resp.status_code == 204
        # Verify it's gone
        resp = client.get(f"/api/flights/{sample_flight.id}")
        assert resp.status_code == 404

    def test_same_route_date_different_params(self, client, sample_flight):
        """Same route+date with different time/altitude creates a new flight."""
        _afternoon = _FUTURE_DEPARTURE_DT.replace(hour=14)
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "route_name": "egtk_lsgs",
            "departure_time": _afternoon.isoformat(),
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 18000,
            "flight_duration_hours": 4.5,
        })
        assert resp.status_code == 201
        assert resp.json()["id"] != sample_flight.id
        assert resp.json()["id"].startswith(f"egtk_lsgs-{_FUTURE_DEPARTURE_DATE}-")

    def test_delete_flight_not_found(self, client):
        resp = client.delete("/api/flights/nonexistent")
        assert resp.status_code == 404

    def test_bulk_delete_flights(self, client, app_db):
        """Bulk-delete removes owned flights and reports unknown/unowned ids."""
        session = app_db()
        # Create a second user whose flight must not be touched
        session.add(UserRow(
            id="other-user", provider="local", provider_sub="other",
            email="other@localhost", display_name="Other User", approved=True,
        ))
        session.flush()
        # Owned flight A
        flight_a = Flight(
            id=_make_flight_id("egtk_lsgs", _FUTURE_DEPARTURE_DATE, time="09:00"),
            user_id=DEV_USER_ID, route_name="egtk_lsgs", waypoints=["EGTK", "LSGS"],
            departure_time=_FUTURE_DEPARTURE_DT,
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=4.5,
            created_at=_NOW - timedelta(days=1),
        )
        # Owned flight B
        flight_b = Flight(
            id=_make_flight_id("egtk_lsgs", _FUTURE_DEPARTURE_DATE, time="14:00"),
            user_id=DEV_USER_ID, route_name="egtk_lsgs", waypoints=["EGTK", "LSGS"],
            departure_time=_FUTURE_DEPARTURE_DT.replace(hour=14),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=4.5,
            created_at=_NOW - timedelta(days=1),
        )
        # Flight owned by someone else — must not be deleted
        flight_other = Flight(
            id=_make_flight_id("egtf_eglf", _FUTURE_DEPARTURE_DATE, user="other-user"),
            user_id="other-user", route_name="egtf_eglf", waypoints=["EGTF", "EGLF"],
            departure_time=_FUTURE_DEPARTURE_DT,
            cruise_altitude_ft=7000, flight_ceiling_ft=15000, flight_duration_hours=0.5,
            created_at=_NOW - timedelta(days=1),
        )
        save_flight(session, flight_a, DEV_USER_ID)
        save_flight(session, flight_b, DEV_USER_ID)
        save_flight(session, flight_other, "other-user")
        session.commit()
        session.close()

        resp = client.post("/api/flights/bulk-delete", json={
            "ids": [flight_a.id, flight_b.id, flight_other.id, "does-not-exist"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["deleted"]) == sorted([flight_a.id, flight_b.id])
        assert sorted(data["not_found"]) == sorted([flight_other.id, "does-not-exist"])

        # The other user's flight must still exist
        session = app_db()
        from weatherbrief.storage.flights import load_flight
        assert load_flight(session, flight_other.id).id == flight_other.id
        session.close()

    def test_bulk_delete_empty(self, client):
        resp = client.post("/api/flights/bulk-delete", json={"ids": []})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": [], "not_found": []}

    # --- Move flight (atomic re-create with new structural fields) ---

    def test_move_flight_changes_date(self, client, sample_flight):
        """Move with a new departure date returns a new flight ID and the old one is gone."""
        new_dt = (_FUTURE_DEPARTURE_DT + timedelta(days=2)).isoformat()
        resp = client.post(
            f"/api/flights/{sample_flight.id}/move",
            json={"departure_time": new_dt},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] != sample_flight.id
        assert data["departure_time"].startswith(new_dt[:10])
        assert data["cruise_altitude_ft"] == sample_flight.cruise_altitude_ft
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 404

    def test_move_flight_changes_route(self, client, sample_flight):
        """Move with new origin/dest creates a flight with a different route."""
        resp = client.post(
            f"/api/flights/{sample_flight.id}/move",
            json={"waypoints": ["EGTF", "EGLF"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["waypoints"] == ["EGTF", "EGLF"]
        assert data["route_name"] == "egtf_eglf"
        assert data["id"] != sample_flight.id
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 404

    def test_move_flight_no_change_rejected(self, client, sample_flight):
        """Move with no structural change returns 422 (would generate the same ID)."""
        resp = client.post(f"/api/flights/{sample_flight.id}/move", json={})
        assert resp.status_code == 422
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 200

    def test_move_flight_collision_rolls_back(self, client, sample_flight, app_db):
        """If the new ID would collide with another existing flight, abort with 409 and don't touch the source."""
        session = app_db()
        other_dt = (_FUTURE_DEPARTURE_DT + timedelta(days=2))
        other = Flight(
            id=_make_flight_id("egtk_lsgs", other_dt.strftime("%Y-%m-%d"), time="09:00"),
            user_id=DEV_USER_ID, route_name="egtk_lsgs",
            waypoints=["EGTK", "LFPB", "LSGS"],
            departure_time=other_dt,
            cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=4.5,
            created_at=_NOW - timedelta(days=1),
        )
        save_flight(session, other, DEV_USER_ID)
        session.commit()
        session.close()

        resp = client.post(
            f"/api/flights/{sample_flight.id}/move",
            json={"departure_time": other_dt.isoformat()},
        )
        assert resp.status_code == 409
        assert client.get(f"/api/flights/{sample_flight.id}").status_code == 200
        assert client.get(f"/api/flights/{other.id}").status_code == 200

    def test_move_flight_not_owner(self, client, app_db):
        """Cannot move someone else's flight."""
        session = app_db()
        session.add(UserRow(
            id="other-user", provider="local", provider_sub="other",
            email="other@localhost", display_name="Other", approved=True,
        ))
        session.flush()
        flight = Flight(
            id=_make_flight_id("egtf_eglf", _FUTURE_DEPARTURE_DATE, user="other-user"),
            user_id="other-user", route_name="egtf_eglf", waypoints=["EGTF", "EGLF"],
            departure_time=_FUTURE_DEPARTURE_DT,
            cruise_altitude_ft=7000, flight_ceiling_ft=15000, flight_duration_hours=0.5,
            created_at=_NOW - timedelta(days=1),
        )
        save_flight(session, flight, "other-user")
        session.commit()
        session.close()

        resp = client.post(
            f"/api/flights/{flight.id}/move",
            json={"departure_time": (_FUTURE_DEPARTURE_DT + timedelta(days=1)).isoformat()},
        )
        assert resp.status_code == 404


# --- Flight subscriptions ---


def _seed_other_user_flight(
    app_db,
    *,
    owner_id: str = "owner-user",
    owner_name: str = "Flight Owner",
    owner_email: str = "owner@example.com",
    private: bool = False,
    route_name: str = "egtf_eglf",
    waypoints: tuple[str, ...] = ("EGTF", "EGLF"),
) -> Flight:
    """Create a second user and a flight they own, returning the Flight.

    The viewer (DEV_USER_ID) is *not* the owner — they can subscribe.
    """
    session = app_db()
    if session.get(UserRow, owner_id) is None:
        session.add(UserRow(
            id=owner_id, provider="local", provider_sub=owner_id,
            email=owner_email, display_name=owner_name, approved=True,
        ))
        session.flush()
    flight = Flight(
        id=_make_flight_id(route_name, _FUTURE_DEPARTURE_DATE, user=owner_id),
        user_id=owner_id, route_name=route_name, waypoints=list(waypoints),
        departure_time=_FUTURE_DEPARTURE_DT,
        cruise_altitude_ft=7000, flight_ceiling_ft=15000, flight_duration_hours=1.0,
        private=private,
        created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, owner_id)
    session.commit()
    session.close()
    return flight


class TestFlightSubscriptions:
    def test_subscribe_public_flight(self, client, app_db):
        """Subscribing to a public flight owned by someone else returns 200 and flags the flight as shared."""
        other = _seed_other_user_flight(app_db)

        resp = client.post(f"/api/flights/{other.id}/subscribe")
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] is True
        assert body["flight_id"] == other.id

        # Now appears in list with subscriber role + owner display name
        flights = client.get("/api/flights").json()
        shared = [f for f in flights if f["id"] == other.id]
        assert len(shared) == 1
        assert shared[0]["role"] == "subscriber"
        assert shared[0]["owner_display_name"] == "Flight Owner"
        assert shared[0]["is_subscribed"] is True

    def test_get_flight_subscriber_fields(self, client, app_db):
        """GET /api/flights/{id} returns subscriber role + owner name + is_subscribed.

        Exercises the single-flight code path (_resolve_owner_display_name +
        is_subscribed DB lookup) which the list endpoint short-circuits via
        its outer join.
        """
        other = _seed_other_user_flight(app_db)
        client.post(f"/api/flights/{other.id}/subscribe")

        resp = client.get(f"/api/flights/{other.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "subscriber"
        assert body["is_subscribed"] is True
        assert body["owner_display_name"] == "Flight Owner"

    def test_get_flight_subscriber_owner_without_display_name(self, client, app_db):
        """Owner with empty display_name surfaces as None — email is NOT leaked."""
        other = _seed_other_user_flight(
            app_db, owner_id="anon-owner", owner_name="", owner_email="anon@example.com",
        )
        client.post(f"/api/flights/{other.id}/subscribe")

        resp = client.get(f"/api/flights/{other.id}")
        assert resp.status_code == 200
        assert resp.json()["owner_display_name"] is None

    def test_subscribe_idempotent(self, client, app_db):
        """Subscribing twice is a no-op; second call reports created=False."""
        other = _seed_other_user_flight(app_db)
        first = client.post(f"/api/flights/{other.id}/subscribe")
        assert first.status_code == 200
        assert first.json()["created"] is True

        second = client.post(f"/api/flights/{other.id}/subscribe")
        assert second.status_code == 200
        assert second.json()["created"] is False

    def test_subscribe_own_flight_rejected(self, client, sample_flight):
        """Owners cannot subscribe to their own flights — 409."""
        resp = client.post(f"/api/flights/{sample_flight.id}/subscribe")
        assert resp.status_code == 409

    def test_subscribe_private_flight_not_found(self, client, app_db):
        """Private flights return 404 to non-owners even at the subscribe endpoint."""
        other = _seed_other_user_flight(app_db, private=True)
        resp = client.post(f"/api/flights/{other.id}/subscribe")
        assert resp.status_code == 404

    def test_subscribe_unknown_flight(self, client):
        resp = client.post("/api/flights/does-not-exist/subscribe")
        assert resp.status_code == 404

    def test_unsubscribe_removes_from_list(self, client, app_db):
        other = _seed_other_user_flight(app_db)
        assert client.post(f"/api/flights/{other.id}/subscribe").status_code == 200
        assert any(f["id"] == other.id for f in client.get("/api/flights").json())

        resp = client.delete(f"/api/flights/{other.id}/subscribe")
        assert resp.status_code == 204

        flights = client.get("/api/flights").json()
        assert not any(f["id"] == other.id for f in flights)

    def test_unsubscribe_idempotent(self, client, app_db):
        other = _seed_other_user_flight(app_db)
        # Unsubscribing when not subscribed is still 204.
        resp = client.delete(f"/api/flights/{other.id}/subscribe")
        assert resp.status_code == 204

    def test_privacy_flip_hides_subscribed_flight(self, client, app_db):
        """When the owner flips a subscribed flight to private it disappears from the subscriber list."""
        other = _seed_other_user_flight(app_db)
        assert client.post(f"/api/flights/{other.id}/subscribe").status_code == 200
        assert any(f["id"] == other.id for f in client.get("/api/flights").json())

        # Owner flips private directly in the DB to avoid needing a second auth session.
        session = app_db()
        from weatherbrief.db.models import FlightRow
        row = session.get(FlightRow, other.id)
        row.private = True
        session.commit()
        session.close()

        flights = client.get("/api/flights").json()
        assert not any(f["id"] == other.id for f in flights), (
            "Private shared flights must disappear from subscriber lists"
        )
        # Detail endpoint now 404s for the subscriber too.
        assert client.get(f"/api/flights/{other.id}").status_code == 404

    def test_subscription_survives_owner_unrelated_edits(self, client, app_db):
        """Subscription is not disturbed by owner edits that don't flip privacy."""
        other = _seed_other_user_flight(app_db)
        assert client.post(f"/api/flights/{other.id}/subscribe").status_code == 200

        # Flip auto_refresh directly to simulate an owner-only field change.
        session = app_db()
        from weatherbrief.db.models import FlightRow
        row = session.get(FlightRow, other.id)
        row.auto_refresh = True
        session.commit()
        session.close()

        flights = client.get("/api/flights").json()
        shared = [f for f in flights if f["id"] == other.id]
        assert len(shared) == 1
        assert shared[0]["role"] == "subscriber"

    def test_owned_flight_list_role(self, client, sample_flight):
        """Own flights show role=owner and is_subscribed=False."""
        flights = client.get("/api/flights").json()
        mine = [f for f in flights if f["id"] == sample_flight.id]
        assert len(mine) == 1
        assert mine[0]["role"] == "owner"
        assert mine[0]["is_subscribed"] is False
        assert mine[0]["owner_display_name"] is None

    def test_subscriber_cannot_delete_or_refresh(self, client, app_db):
        """Subscribers get 404 from owner-only endpoints (delete, move)."""
        other = _seed_other_user_flight(app_db)
        assert client.post(f"/api/flights/{other.id}/subscribe").status_code == 200

        assert client.delete(f"/api/flights/{other.id}").status_code == 404
        assert client.post(
            f"/api/flights/{other.id}/move",
            json={"departure_time": (_FUTURE_DEPARTURE_DT + timedelta(days=2)).isoformat()},
        ).status_code == 404


# --- Packs ---


class TestPacksAPI:
    def test_list_packs_empty(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}/packs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_packs_with_data(self, client, sample_pack):
        resp = client.get(f"/api/flights/{sample_pack.flight_id}/packs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["fetch_timestamp"] == sample_pack.fetch_timestamp.isoformat()
        assert data[0]["has_gramet"] is True

    def test_list_packs_flight_not_found(self, client):
        resp = client.get("/api/flights/nonexistent/packs")
        assert resp.status_code == 404

    def test_get_latest_pack(self, client, sample_pack):
        resp = client.get(f"/api/flights/{sample_pack.flight_id}/packs/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetch_timestamp"] == sample_pack.fetch_timestamp.isoformat()

    def test_get_latest_pack_none(self, client, sample_flight):
        resp = client.get(f"/api/flights/{sample_flight.id}/packs/latest")
        assert resp.status_code == 404

    def test_get_specific_pack(self, client, sample_pack):
        resp = client.get(
            f"/api/flights/{sample_pack.flight_id}/packs/{sample_pack.fetch_timestamp.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["days_out"] == sample_pack.days_out
        assert data["assessment"] == "GREEN"

    def test_get_pack_not_found(self, client, sample_flight):
        resp = client.get(
            f"/api/flights/{sample_flight.id}/packs/1999-01-01T00:00:00+00:00"
        )
        assert resp.status_code == 404


class TestPackArtifacts:
    """Test artifact serving (snapshot, gramet, skewt, digest)."""

    @pytest.fixture
    def pack_with_artifacts(self, tmp_path, sample_pack, monkeypatch):
        """Create a pack with actual artifact files on disk."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        pack_dir = pack_dir_for(
            DEV_USER_ID, sample_pack.flight_id, sample_pack.fetch_timestamp,
        )
        pack_dir.mkdir(parents=True, exist_ok=True)

        # Briefing JSON (snapshot split: briefing.json has route/analyses/observations)
        (pack_dir / "briefing.json").write_text('{"route": {}}')

        # GRAMET PNG (fake 1-pixel PNG header)
        (pack_dir / "gramet.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

        # Skew-T
        skewt_dir = pack_dir / "skewt"
        skewt_dir.mkdir()
        (skewt_dir / "EGTK_gfs.png").write_bytes(b"\x89PNG\r\n\x1a\nskewt")

        # Digest
        (pack_dir / "digest.md").write_text("# Weather Digest\nAll clear.")

        return sample_pack

    def test_get_snapshot(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/snapshot")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"

    def test_get_gramet(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/gramet")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_skewt(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/skewt/EGTK/gfs")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_skewt_not_found(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/skewt/XXXX/gfs")
        assert resp.status_code == 404

    def test_get_digest(self, client, pack_with_artifacts):
        ts = pack_with_artifacts.fetch_timestamp.isoformat()
        fid = pack_with_artifacts.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/digest")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]

    def test_snapshot_not_found(self, client, sample_pack):
        ts = sample_pack.fetch_timestamp.isoformat()
        fid = sample_pack.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/snapshot")
        assert resp.status_code == 404

    def test_gramet_not_found(self, client, sample_pack):
        ts = sample_pack.fetch_timestamp.isoformat()
        fid = sample_pack.flight_id
        resp = client.get(f"/api/flights/{fid}/packs/{ts}/gramet")
        assert resp.status_code == 404


class TestRefreshEndpoint:
    """Test the POST /refresh endpoint (mocked pipeline)."""

    def test_refresh_flight_not_found(self, client):
        resp = client.post("/api/flights/nonexistent/packs/refresh")
        assert resp.status_code == 404

    def test_refresh_no_db_configured(self, client, sample_flight):
        """When AIRPORTS_DB is empty, refresh returns 503."""
        client.app.state.db_path = ""
        resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_refresh_uses_app_state_db_path(self, client, sample_flight):
        """Verify app.state.db_path is used when set."""
        client.app.state.db_path = "/fake/db/path"
        resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
        # Will fail because /fake/db/path doesn't exist or load_route fails,
        # but importantly it should NOT be a 503 "not configured"
        assert resp.status_code != 503 or "not configured" not in resp.json().get("detail", "")
        client.app.state.db_path = ""

    @patch("weatherbrief.airports._load_airport_model")
    def test_refresh_queued(self, mock_load, client, sample_flight):
        """Refresh returns 202 with queued status (pipeline runs in background)."""
        from airport_mocks import TEST_AIRPORTS, mock_model
        from weatherbrief.api.packs import refresh_registry

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
        assert resp.status_code == 202

        data = resp.json()
        assert data["status"] == "queued"
        assert data["flight_id"] == sample_flight.id

        # Clean up: wait briefly for executor to pick up and unregister
        import time
        for _ in range(20):
            if not refresh_registry._entries.get(sample_flight.id):
                break
            time.sleep(0.1)
        else:
            # Force cleanup if pipeline failed (expected — /fake/db doesn't exist)
            refresh_registry.unregister(sample_flight.id)

    def test_refresh_duplicate_409(self, client, sample_flight):
        """Refresh returns 409 when one is already in progress."""
        from weatherbrief.api.packs import refresh_registry

        client.app.state.db_path = "/fake/db"
        refresh_registry.try_register(
            sample_flight.id, triggered_by="user", user_id=DEV_USER_ID,
        )
        try:
            resp = client.post(f"/api/flights/{sample_flight.id}/packs/refresh")
            assert resp.status_code == 409
            assert "already in progress" in resp.json()["detail"]
        finally:
            refresh_registry.unregister(sample_flight.id)


# --- Raw route persistence on update ---


class TestUpdateFlightRawRoute:
    """raw_route sync semantics on PATCH /api/flights/{id}.

    Three cases:
      1. waypoints + raw_route → both stored, parser_version stamped
      2. waypoints only        → raw_route cleared (stale string better off NULL)
      3. waypoints unchanged   → raw_route untouched
    """

    def _create_with_raw(self, client, raw="EGTK DCT LFPB DCT LSGS"):
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "raw_route": raw,
            "departure_time": _FUTURE_DEPARTURE_ISO,
        })
        assert resp.status_code == 201
        return resp.json()

    def test_update_with_new_raw_route_overwrites(self, client):
        flight = self._create_with_raw(client)
        resp = client.patch(f"/api/flights/{flight['id']}", json={
            "waypoints": ["EGTK", "LSGS"],  # origin/dest unchanged, middle removed
            "raw_route": "EGTK DCT LSGS",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["waypoints"] == ["EGTK", "LSGS"]
        assert data["raw_route"] == "EGTK DCT LSGS"
        assert data["parser_version"] is not None

    def test_update_waypoints_only_clears_raw_route(self, client):
        """Direct waypoint edit (no raw_route in body) — stored string would
        no longer match, so we clear it rather than lie about its provenance."""
        flight = self._create_with_raw(client)
        resp = client.patch(f"/api/flights/{flight['id']}", json={
            "waypoints": ["EGTK", "LSGS"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["waypoints"] == ["EGTK", "LSGS"]
        assert data["raw_route"] is None
        assert data["parser_version"] is None

    def test_update_unrelated_field_preserves_raw_route(self, client):
        """Touching only altitude/duration must not disturb raw_route."""
        flight = self._create_with_raw(client)
        resp = client.patch(f"/api/flights/{flight['id']}", json={
            "cruise_altitude_ft": 9000,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["raw_route"] == "EGTK DCT LFPB DCT LSGS"
        assert data["parser_version"] is not None

    def test_update_same_waypoints_with_raw_route_stores_it(self, client):
        """iOS-then-annotate scenario: a flight created without a raw_route
        (iOS/MCP path) gets edited from the web with a Field-15 string whose
        resolved waypoints equal the stored list. The raw_route must still
        land — the input string is the new piece of information, even though
        the waypoint list didn't change."""
        # Create without raw_route (mimics iOS/MCP)
        resp = client.post("/api/flights", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "departure_time": _FUTURE_DEPARTURE_ISO,
        })
        assert resp.status_code == 201
        flight = resp.json()
        assert flight["raw_route"] is None

        # Annotate with an equivalent Field-15 string from the web
        resp = client.patch(f"/api/flights/{flight['id']}", json={
            "waypoints": ["EGTK", "LFPB", "LSGS"],
            "raw_route": "EGTK DCT LFPB DCT LSGS",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["raw_route"] == "EGTK DCT LFPB DCT LSGS"
        assert data["parser_version"] is not None


# --- Interpret Route ---

# Mock helpers imported from conftest — mock only the DB layer so the real
# RouteResolver runs (catches contract violations like the single-token bug).
from airport_mocks import TEST_AIRPORTS, mock_model


class TestInterpretRoute:
    """Tests for POST /api/flights/interpret-route.

    Mock at _load_airport_model level so RouteResolver runs for real.
    """

    def _post(self, client, raw_route: str):
        return client.post(
            "/api/flights/interpret-route",
            json={"raw_route": raw_route},
        )

    @patch("weatherbrief.airports._load_airport_model")
    def test_two_valid_airports(self, mock_load, client):
        """Basic happy path — two ICAO codes resolve successfully."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        assert data["skipped"] == []
        assert len(data["waypoints"]) == 2

    @patch("weatherbrief.airports._load_airport_model")
    def test_single_valid_airport(self, mock_load, client):
        """Single code is recognized but no full route resolution."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ"]
        assert data["skipped"] == []
        # No waypoint details — need >= 2 for full resolution
        assert data["waypoints"] == []

    @patch("weatherbrief.airports._load_airport_model")
    def test_unknown_token_skipped(self, mock_load, client):
        """Unknown codes go to skipped, valid ones to interpreted."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ ZZZZ LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        assert data["skipped"] == ["ZZZZ"]

    @patch("weatherbrief.airports._load_airport_model")
    def test_filters_route_notation(self, mock_load, client):
        """Non-waypoint tokens (separators, short words) are filtered out."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ - LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]

    @patch("weatherbrief.airports._load_airport_model")
    def test_multi_waypoint_route(self, mock_load, client):
        """Three-leg route resolves all waypoints."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGTK EGBJ LSGS")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGTK", "EGBJ", "LSGS"]
        assert len(data["waypoints"]) == 3

    @patch("weatherbrief.airports._load_airport_model")
    def test_duplicate_consecutive_tokens(self, mock_load, client):
        """Consecutive duplicates are collapsed."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "EGBJ EGBJ LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]

    def test_no_db_configured(self, client):
        """Returns 500 when airport database is not configured."""
        client.app.state.db_path = ""
        resp = self._post(client, "EGBJ LFOV")
        assert resp.status_code == 500

    @patch("weatherbrief.airports._load_airport_model")
    def test_all_unknown(self, mock_load, client):
        """All tokens unknown — interpreted is empty."""
        mock_load.return_value = mock_model({})
        client.app.state.db_path = "/fake/db"

        resp = self._post(client, "ZZZZ YYYY")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == []
        assert set(data["skipped"]) == {"ZZZZ", "YYYY"}

    @patch("weatherbrief.airports._load_airport_model")
    def test_field15_keywords_silently_dropped(self, mock_load, client):
        """ICAO Field-15 syntax (IFR/DCT/airways/speed-level) is silently
        dropped from `skipped` — it's expected noise, not a pilot mistake.
        Regression test for the IFR-vs-navaid-collision bug where `IFR`
        coincidentally matched a global navaid code (it must still NOT be
        sent to the resolver), and for the UX bug where the popup showed
        "DCT, DCT, DCT not recognized" which confused pilots."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        resp = self._post(
            client,
            "EGBJ N0152F100 IFR DCT M150 LFOV",
        )
        assert resp.status_code == 200
        data = resp.json()
        # Endpoints survive
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        # Pure Field-15 syntax never reaches the user-visible skip list.
        assert data["skipped"] == []
        # But original_tokens still records everything the parser saw.
        assert set(data["original_tokens"]) == {
            "EGBJ", "N0152F100", "IFR", "DCT", "M150", "LFOV",
        }

    @patch("weatherbrief.airports._load_airport_model")
    def test_unknown_grammar_token_skipped(self, mock_load, client):
        """Tokens that don't match any Field-15 grammar (typos, mixed alnum)
        surface in `skipped` so the pilot sees them. The 5-letter typo path
        is covered by `test_unknown_token_skipped` — this one exercises the
        parser's UNKNOWN bucket directly."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        # 6+ char strings that are neither valid waypoint shape nor a coord.
        resp = self._post(client, "EGBJ ZZZTOPP LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        assert data["skipped"] == ["ZZZTOPP"]

    @patch("weatherbrief.airports._load_airport_model")
    def test_inline_coordinate_resolved(self, mock_load, client):
        """ICAO inline coords (DDMM[NS]DDDMM[EW]) resolve geometrically and
        flow through interpret-route as a real route point — name = the
        original token so it round-trips back into the route string."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        # 5000N00200W (~50°N, 2°W) sits between EGBJ (~52°N -2°W) and
        # LFOV (~48°N -0.7°W) — comfortably on-route, well within the
        # detour gate.
        resp = self._post(client, "EGBJ DCT 5000N00200W DCT LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "5000N00200W", "LFOV"]
        assert data["skipped"] == []
        # Coord waypoint comes back with its parsed lat/lon
        coord_wp = next(w for w in data["waypoints"] if w["icao"] == "5000N00200W")
        assert abs(coord_wp["lat"] - 50.0) < 0.01
        assert abs(coord_wp["lon"] - (-2.0)) < 0.01

    @patch("weatherbrief.airports._load_airport_model")
    def test_off_route_coordinate_rejected_by_detour_gate(self, mock_load, client):
        """A coord too far off the direct route gets rejected by the same
        detour gate that catches misresolved navaids. Surfaces in `skipped`
        like any other late-stage rejection."""
        mock_load.return_value = mock_model(TEST_AIRPORTS)
        client.app.state.db_path = "/fake/db"

        # 4629N01541E (Slovenia) on a EGBJ→LFOV (UK→NW France) route is
        # ~700+ nm off — detour gate rejects.
        resp = self._post(client, "EGBJ 4629N01541E LFOV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interpreted"] == ["EGBJ", "LFOV"]
        assert data["skipped"] == ["4629N01541E"]
