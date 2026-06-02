"""Tests for the FlightExchange export feature (issue #197).

Covers the ``Flight`` → ``FlightExchange`` emit adapter
(:func:`weatherbrief.api.flights.flight_to_exchange`) and the
``GET /api/flights/{flight_id}/export`` endpoint that serves the shared
cross-app interchange payload other flyfun apps (forms, brief) import.

The adapter splits weather's single ``waypoints`` list into the explicit
endpoints the shared ``Route`` uses, so we test both with a real airport DB
(end-to-end coordinate resolution) and with a stubbed resolver (deterministic
envelope logic: derived arrival, aircraft registration owner-gating, source).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api import flights as flights_api
from weatherbrief.api.app import create_app
from weatherbrief.db.models import UserAircraftRow
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight


def _find_nav_db() -> str | None:
    """Locate the euro_aip airport DB, or return None so DB tests can skip.

    Worktrees share ``nav.db`` from the ``main`` worktree's data dir (see
    project CLAUDE.md), so prefer an explicit ``AIRPORTS_DB`` then fall back
    to the conventional ``<repo>/main/data/nav.db``.
    """
    env = os.environ.get("AIRPORTS_DB")
    candidates = [os.path.expandvars(env)] if env else []
    here = Path(__file__).resolve()
    candidates.append(str(here.parents[2] / "main" / "data" / "nav.db"))
    candidates.append(str(here.parents[1] / "data" / "nav.db"))
    for c in candidates:
        if c and "$" not in c and Path(c).exists():
            return c
    return None


NAV_DB = _find_nav_db()
needs_nav_db = pytest.mark.skipif(NAV_DB is None, reason="nav.db not available")

DEP = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _make_flight(
    idx: int,
    *,
    waypoints: list[str] | None = None,
    aircraft_id: int | None = None,
    private: bool = False,
    share_code: str | None = None,
) -> Flight:
    return Flight(
        id=f"egtk-lfat-2026-06-01-{idx:04d}",
        user_id=DEV_USER_ID,
        route_name="egtk_lfat",
        waypoints=waypoints if waypoints is not None else ["EGTK", "LFAT"],
        departure_time=DEP,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.5,
        aircraft_id=aircraft_id,
        private=private,
        share_code=share_code,
        created_at=DEP,
    )


# --- Adapter: deterministic envelope logic (stubbed resolver) ---


class TestExchangeEnvelope:
    """Envelope mapping that does not depend on airport geography.

    Stubs :func:`resolve_route` so the route is fixed and we can assert the
    timing / aircraft / provenance logic the adapter layers on top.
    """

    @pytest.fixture
    def stub_route(self, monkeypatch):
        from euro_aip.briefing.models.route import Route

        def _fake_resolve_route(codes, db_path):
            return Route(
                departure=codes[0],
                destination=codes[-1],
                waypoints=list(codes[1:-1]),
                departure_coords=(51.83, -1.32),
                destination_coords=(43.18, 0.0),
            )

        monkeypatch.setattr(flights_api, "resolve_route", _fake_resolve_route, raising=False)
        # The adapter imports resolve_route from weatherbrief.airports at call
        # time, so patch it at the source module too.
        import weatherbrief.airports as airports_mod

        monkeypatch.setattr(airports_mod, "resolve_route", _fake_resolve_route)

    def test_basic_envelope_and_source(self, stub_route, db_session, dev_user):
        flight = _make_flight(1, share_code="aB3xy7Q9")
        fx = flights_api.flight_to_exchange(
            flight, db_session, "unused-db", viewer_id=DEV_USER_ID
        )
        d = fx.to_dict()
        assert d["schema_version"] == 1
        assert d["route"]["departure"] == "EGTK"
        assert d["route"]["destination"] == "LFAT"
        assert d["name"] == "egtk_lfat"
        assert d["source"] == {
            "app": "weather",
            "flight_id": flight.id,
            "share_code": "aB3xy7Q9",
        }

    def test_arrival_derived_from_duration(self, stub_route, db_session, dev_user):
        flight = _make_flight(2)  # duration 1.5h
        fx = flights_api.flight_to_exchange(
            flight, db_session, "unused-db", viewer_id=DEV_USER_ID
        )
        route = fx.route
        assert route.departure_time == DEP
        assert route.arrival_time == DEP + timedelta(hours=1.5)

    def test_no_arrival_when_duration_zero(self, stub_route, db_session, dev_user):
        flight = _make_flight(3)
        flight.flight_duration_hours = 0.0
        fx = flights_api.flight_to_exchange(
            flight, db_session, "unused-db", viewer_id=DEV_USER_ID
        )
        assert fx.route.arrival_time is None

    def test_intermediate_waypoints_kept(self, stub_route, db_session, dev_user):
        flight = _make_flight(4, waypoints=["EGTK", "BILGO", "LFAT"])
        fx = flights_api.flight_to_exchange(
            flight, db_session, "unused-db", viewer_id=DEV_USER_ID
        )
        # waypoints carries the middle tokens only; endpoints are split out.
        assert fx.route.waypoints == ["BILGO"]
        assert fx.route.departure == "EGTK"
        assert fx.route.destination == "LFAT"

    def test_aircraft_registration_for_owner(self, stub_route, db_session, dev_user):
        ac = UserAircraftRow(
            user_id=DEV_USER_ID, icao_type="P28A", tail_number="G-ABCD",
        )
        db_session.add(ac)
        db_session.flush()
        flight = _make_flight(5, aircraft_id=ac.id)
        fx = flights_api.flight_to_exchange(
            flight, db_session, "unused-db", viewer_id=DEV_USER_ID
        )
        d = fx.to_dict()
        assert d["aircraft"]["registration"] == "G-ABCD"
        assert d["aircraft"]["type"] == "P28A"
        assert d["route"]["aircraft_type"] == "P28A"

    def test_aircraft_registration_hidden_from_non_owner(
        self, stub_route, db_session, dev_user
    ):
        ac = UserAircraftRow(
            user_id=DEV_USER_ID, icao_type="P28A", tail_number="G-ABCD",
        )
        db_session.add(ac)
        db_session.flush()
        flight = _make_flight(6, aircraft_id=ac.id)
        fx = flights_api.flight_to_exchange(
            flight, db_session, "unused-db", viewer_id="someone-else"
        )
        d = fx.to_dict()
        # Type still travels (it's not personal); the registration does not.
        assert d["route"]["aircraft_type"] == "P28A"
        assert "registration" not in d.get("aircraft", {})


# --- Adapter: real airport-DB resolution ---


@needs_nav_db
class TestExchangeRealResolution:
    def test_resolves_endpoints_and_coords(self, db_session, dev_user):
        flight = _make_flight(20)
        fx = flights_api.flight_to_exchange(
            flight, db_session, NAV_DB, viewer_id=DEV_USER_ID
        )
        route = fx.route
        assert route.departure == "EGTK"
        assert route.destination == "LFAT"
        assert route.departure_coords is not None
        assert route.destination_coords is not None
        # EGTK (Oxford) sits around 51.8N / -1.3E — sanity, not exactness.
        assert 50 < route.departure_coords[0] < 53


# --- Endpoint: GET /api/flights/{id}/export ---


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
    if NAV_DB:
        monkeypatch.setenv("AIRPORTS_DB", NAV_DB)
    app = create_app()
    if NAV_DB:
        app.state.db_path = NAV_DB

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


def _seed(app_db, flight: Flight) -> Flight:
    s = app_db()
    save_flight(s, flight, flight.user_id)
    s.commit()
    s.close()
    return flight


@needs_nav_db
class TestExportEndpoint:
    def test_export_returns_flightexchange(self, client, app_db):
        f = _seed(app_db, _make_flight(30, share_code="Exp0rtAA"))
        resp = client.get(f"/api/flights/{f.id}/export")
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["schema_version"] == 1
        assert d["route"]["departure"] == "EGTK"
        assert d["route"]["destination"] == "LFAT"
        assert d["route"]["departure_coords"] is not None
        assert d["source"] == {
            "app": "weather",
            "flight_id": f.id,
            "share_code": "Exp0rtAA",
        }

    def test_export_includes_owned_aircraft_registration(self, client, app_db):
        s = app_db()
        ac = UserAircraftRow(
            user_id=DEV_USER_ID, icao_type="C172", tail_number="G-TEST",
        )
        s.add(ac)
        s.commit()
        ac_id = ac.id
        s.close()
        f = _seed(app_db, _make_flight(31, aircraft_id=ac_id))
        resp = client.get(f"/api/flights/{f.id}/export")
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["aircraft"]["registration"] == "G-TEST"
        assert d["aircraft"]["type"] == "C172"

    def test_export_unknown_flight_404(self, client):
        resp = client.get("/api/flights/does-not-exist/export")
        assert resp.status_code == 404

    def test_export_flight_without_route_422(self, client, app_db):
        f = _seed(app_db, _make_flight(32, waypoints=["EGTK"]))
        resp = client.get(f"/api/flights/{f.id}/export")
        assert resp.status_code == 422

    def test_export_private_flight_hidden_from_non_owner(self, client, app_db):
        f = _seed(app_db, _make_flight(33, private=True))
        # Re-point auth at a different user: a private flight they don't own
        # must 404, exactly like GET /{flight_id}.
        client.app.dependency_overrides[current_user_id] = lambda: "intruder"
        resp = client.get(f"/api/flights/{f.id}/export")
        assert resp.status_code == 404
