"""Tests for /api/flights/{id}/debrief and /api/debriefs/stats."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight


_NOW = datetime.now(timezone.utc)


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


@pytest.fixture
def past_flight(app_db):
    """A flight in the past — admin/dev rules don't bite this fixture path."""
    s = app_db()
    dep = _NOW - timedelta(days=3)
    h = hashlib.sha256(json.dumps(
        {"alt": 8000, "ceil": 18000, "dur": 1.0, "time": dep.strftime("%H:%M"), "user": DEV_USER_ID},
        sort_keys=True,
    ).encode()).hexdigest()[:4]
    flight = Flight(
        id=f"egtk_lfat-{dep.strftime('%Y-%m-%d')}-{h}",
        user_id=DEV_USER_ID,
        route_name="egtk_lfat",
        waypoints=["EGTK", "LFAT"],
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        created_at=_NOW - timedelta(days=10),
    )
    save_flight(s, flight, DEV_USER_ID)
    s.commit()
    s.close()
    return flight


class TestDebriefCRUDApi:
    def test_get_404_when_absent(self, client, past_flight):
        r = client.get(f"/api/flights/{past_flight.id}/debrief")
        assert r.status_code == 404

    def test_put_creates_cancelled(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "reasons": ["IMC", "WIND"], "note": "too windy"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["decision"] == "cancelled"
        assert set(body["reasons"]) == {"IMC", "WIND"}
        assert body["outcomes"] == {}
        assert body["note"] == "too windy"
        assert body["created_at"]
        assert body["updated_at"]

    def test_put_creates_flown(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={
                "decision": "flown",
                "outcomes": {"ICE": "worse", "IMC": "consistent"},
                "note": "more icing than forecast",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcomes"] == {"ICE": "worse", "IMC": "consistent"}
        assert body["reasons"] == []

    def test_put_creates_monitoring(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "monitoring", "note": "weather watch only"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["decision"] == "monitoring"
        assert body["reasons"] == []
        assert body["outcomes"] == {}
        assert body["note"] == "weather watch only"

    def test_put_monitoring_with_reasons_rejected(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "monitoring", "reasons": ["IMC"]},
        )
        assert r.status_code == 422

    def test_put_monitoring_with_outcomes_rejected(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "monitoring", "outcomes": {"ICE": "worse"}},
        )
        assert r.status_code == 422

    def test_put_then_get(self, client, past_flight):
        client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "reasons": ["IMC"]},
        )
        r = client.get(f"/api/flights/{past_flight.id}/debrief")
        assert r.status_code == 200
        assert r.json()["decision"] == "cancelled"

    def test_put_replaces_decision(self, client, past_flight):
        client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "reasons": ["IMC"]},
        )
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "flown", "outcomes": {"ICE": "consistent"}},
        )
        assert r.status_code == 200
        assert r.json()["decision"] == "flown"
        assert r.json()["reasons"] == []

    def test_delete(self, client, past_flight):
        client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "reasons": ["IMC"]},
        )
        r = client.delete(f"/api/flights/{past_flight.id}/debrief")
        assert r.status_code == 204
        assert client.get(f"/api/flights/{past_flight.id}/debrief").status_code == 404

    def test_delete_idempotent(self, client, past_flight):
        # Never created — DELETE still returns 204.
        r = client.delete(f"/api/flights/{past_flight.id}/debrief")
        assert r.status_code == 204


class TestValidationErrors:
    def test_reasons_with_flown_rejected(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "flown", "reasons": ["IMC"]},
        )
        assert r.status_code == 422

    def test_outcomes_with_cancelled_rejected(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "outcomes": {"ICE": "worse"}},
        )
        assert r.status_code == 422

    def test_unknown_tag_rejected(self, client, past_flight):
        r = client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "reasons": ["BANANA"]},
        )
        assert r.status_code == 422


class TestOwnership:
    def test_unknown_flight_404(self, client):
        r = client.get("/api/flights/nope-2026-01-01-aaaa/debrief")
        assert r.status_code == 404

    def test_put_unknown_flight_404(self, client):
        r = client.put(
            "/api/flights/nope-2026-01-01-aaaa/debrief",
            json={"decision": "cancelled", "reasons": ["IMC"]},
        )
        assert r.status_code == 404


class TestStatsEndpoint:
    def test_empty_stats(self, client):
        r = client.get("/api/debriefs/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["window_days"] == 90
        assert body["flown_count"] == 0
        assert body["cancelled_count"] == 0

    def test_stats_after_debriefs(self, client, past_flight):
        client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "cancelled", "reasons": ["IMC", "WIND"]},
        )
        r = client.get("/api/debriefs/stats")
        body = r.json()
        assert body["cancelled_count"] == 1
        assert body["cancellation_reasons"]["IMC"] == 1
        assert body["cancellation_reasons"]["WIND"] == 1

    def test_window_days_query(self, client, past_flight):
        # 30d window: the past_flight (3d ago) is in window.
        r = client.get("/api/debriefs/stats?window_days=30")
        assert r.status_code == 200
        assert r.json()["window_days"] == 30
        assert r.json()["pending_debrief_count"] == 1

    def test_window_days_bounds(self, client):
        assert client.get("/api/debriefs/stats?window_days=0").status_code == 422
        assert client.get("/api/debriefs/stats?window_days=99999").status_code == 422

    def test_monitoring_in_stats(self, client, past_flight):
        client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "monitoring", "note": "weather watch"},
        )
        body = client.get("/api/debriefs/stats").json()
        assert body["monitoring_count"] == 1
        assert body["flown_count"] == 0
        assert body["cancelled_count"] == 0
        assert body["pending_debrief_count"] == 0


class TestSectionMonitoring:
    def test_monitoring_drops_out_of_recent(self, client, past_flight):
        # Before debrief: flight is in recent (it's a past undebriefed flight).
        body = client.get("/api/flights").json()
        rec = next(f for f in body if f["id"] == past_flight.id)
        assert rec["section"] == "recent"

        # After monitoring debrief: flight moves to past.
        client.put(
            f"/api/flights/{past_flight.id}/debrief",
            json={"decision": "monitoring"},
        )
        body = client.get("/api/flights").json()
        rec = next(f for f in body if f["id"] == past_flight.id)
        assert rec["section"] == "past"
