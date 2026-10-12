"""Aircraft API + the default-aircraft rules (#777).

Every user always has exactly one default aircraft: ``GET /aircraft`` and
``create_flight`` synthesise a placeholder for a user who never set one up,
the last aircraft can't be deleted, deleting the default promotes another,
any PUT clears the placeholder flag, and a POST while the placeholder is the
only aircraft replaces it (flights re-pointed).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.db.models import FlightProfileRow, FlightRow, UserAircraftRow
from weatherbrief.models import Flight
from weatherbrief.storage.aircraft import (
    PLACEHOLDER_ICAO_TYPE,
    ensure_default_aircraft,
    resolve_flight_aircraft,
)
from weatherbrief.storage.flights import save_flight

OTHER_USER = "other-user"


@pytest.fixture
def app_db():
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    for uid in (DEV_USER_ID, OTHER_USER):
        s.add(UserRow(
            id=uid, provider="local", provider_sub=uid,
            email=f"{uid}@l", display_name="D", approved=True,
        ))
        s.flush()
        s.add(UserPreferencesRow(user_id=uid))
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
    app.state.db_path = ""  # skip airport-DB waypoint validation

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


def _rows(app_db, user_id=DEV_USER_ID) -> list[UserAircraftRow]:
    s = app_db()
    rows = s.query(UserAircraftRow).filter_by(user_id=user_id).order_by(UserAircraftRow.id).all()
    s.expunge_all()
    s.close()
    return rows


def _add_profile(app_db, settings: dict, *, user_id=DEV_USER_ID, is_default=True) -> int:
    s = app_db()
    row = FlightProfileRow(
        user_id=user_id, name="P", is_default=is_default, settings_json=json.dumps(settings),
    )
    s.add(row)
    s.commit()
    pid = row.id
    s.close()
    return pid


def _add_aircraft(app_db, **kw) -> int:
    s = app_db()
    row = UserAircraftRow(user_id=kw.pop("user_id", DEV_USER_ID), **kw)
    s.add(row)
    s.commit()
    aid = row.id
    s.close()
    return aid


def _save_flight(app_db, fid: str, *, aircraft_id=None, user_id=DEV_USER_ID) -> None:
    s = app_db()
    dep = datetime.now(timezone.utc) + timedelta(days=2)
    save_flight(s, Flight(
        id=fid, user_id=user_id, route_name="egtk_lfat", waypoints=["EGTK", "LFAT"],
        departure_time=dep, cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        flight_duration_hours=1.0, aircraft_id=aircraft_id,
        created_at=datetime.now(timezone.utc),
    ), user_id)
    s.commit()
    s.close()


def _flight_aircraft_id(app_db, fid: str):
    s = app_db()
    aid = s.get(FlightRow, fid).aircraft_id
    s.close()
    return aid


# --- ensure_default_aircraft ---------------------------------------------


class TestEnsureDefaultAircraft:
    def test_none_creates_placeholder_from_default_profile(self, app_db):
        _add_profile(app_db, {"speed_kt": 125, "flight_rules": "vfr_only"})
        s = app_db()
        row = ensure_default_aircraft(s, DEV_USER_ID)
        s.commit()
        assert row.is_default and row.is_placeholder
        assert row.icao_type == PLACEHOLDER_ICAO_TYPE
        assert row.nickname == "My aircraft"
        assert row.cruise_speed_kt == 125
        assert row.ceiling_ft is None
        assert row.is_ifr is False and row.is_fiki is False
        s.close()

    def test_none_without_profile_is_ifr_and_speedless(self, app_db):
        s = app_db()
        row = ensure_default_aircraft(s, DEV_USER_ID)
        assert row.cruise_speed_kt is None
        assert row.is_ifr is True
        s.close()

    def test_some_but_no_default_promotes_oldest(self, app_db):
        first = _add_aircraft(app_db, icao_type="C172", is_default=False)
        _add_aircraft(app_db, icao_type="SR22", is_default=False)
        s = app_db()
        promoted = ensure_default_aircraft(s, DEV_USER_ID).id
        s.commit()
        s.close()
        assert promoted == first
        rows = _rows(app_db)
        assert [r.is_default for r in rows] == [True, False]
        assert not any(r.is_placeholder for r in rows)

    def test_has_default_is_noop(self, app_db):
        _add_aircraft(app_db, icao_type="C172", is_default=False)
        second = _add_aircraft(app_db, icao_type="SR22", is_default=True)
        s = app_db()
        assert ensure_default_aircraft(s, DEV_USER_ID).id == second
        s.close()
        assert len(_rows(app_db)) == 2


# --- GET / POST / PUT / DELETE -------------------------------------------


class TestAircraftApi:
    def test_list_synthesises_placeholder_once(self, client, app_db):
        body = client.get("/api/aircraft").json()
        assert len(body) == 1
        assert body[0]["is_default"] and body[0]["is_placeholder"]
        assert body[0]["icao_type"] == "ZZZZ"
        assert len(client.get("/api/aircraft").json()) == 1
        # Another user is untouched.
        assert _rows(app_db, OTHER_USER) == []

    def test_create_first_aircraft_becomes_default(self, client, app_db):
        resp = client.post("/api/aircraft", json={"icao_type": "c172"})
        assert resp.status_code == 201
        assert resp.json()["is_default"] is True
        assert resp.json()["is_placeholder"] is False
        assert resp.json()["icao_type"] == "C172"

    def test_default_is_unique(self, client, app_db):
        _add_aircraft(app_db, icao_type="C172", is_default=True)
        client.post("/api/aircraft", json={"icao_type": "SR22", "is_default": True})
        rows = _rows(app_db)
        assert [r.icao_type for r in rows if r.is_default] == ["SR22"]

    def test_update_fields(self, client, app_db):
        aid = _add_aircraft(app_db, icao_type="C172", is_default=True, cruise_speed_kt=110)
        resp = client.put(f"/api/aircraft/{aid}", json={"cruise_speed_kt": 120, "ceiling_ft": 14000})
        assert resp.status_code == 200
        assert resp.json()["cruise_speed_kt"] == 120
        assert resp.json()["ceiling_ft"] == 14000

    def test_cannot_unset_the_only_default(self, client, app_db):
        aid = _add_aircraft(app_db, icao_type="C172", is_default=True)
        resp = client.put(f"/api/aircraft/{aid}", json={"is_default": False})
        assert resp.status_code == 200
        assert resp.json()["is_default"] is True

    def test_other_users_aircraft_is_404(self, client, app_db):
        aid = _add_aircraft(app_db, user_id=OTHER_USER, icao_type="C172", is_default=True)
        assert client.put(f"/api/aircraft/{aid}", json={"nickname": "x"}).status_code == 404
        assert client.delete(f"/api/aircraft/{aid}").status_code == 404

    def test_delete_last_aircraft_is_409(self, client, app_db):
        aid = _add_aircraft(app_db, icao_type="C172", is_default=True)
        resp = client.delete(f"/api/aircraft/{aid}")
        assert resp.status_code == 409
        assert "only aircraft" in resp.json()["detail"]
        assert len(_rows(app_db)) == 1

    def test_delete_default_promotes_another(self, client, app_db):
        default = _add_aircraft(app_db, icao_type="C172", is_default=True)
        other = _add_aircraft(app_db, icao_type="SR22", is_default=False)
        assert client.delete(f"/api/aircraft/{default}").status_code == 204
        rows = _rows(app_db)
        assert [(r.id, r.is_default) for r in rows] == [(other, True)]

    def test_delete_non_default_keeps_default(self, client, app_db):
        default = _add_aircraft(app_db, icao_type="C172", is_default=True)
        other = _add_aircraft(app_db, icao_type="SR22", is_default=False)
        assert client.delete(f"/api/aircraft/{other}").status_code == 204
        assert [(r.id, r.is_default) for r in _rows(app_db)] == [(default, True)]


# --- placeholder lifecycle ------------------------------------------------


class TestPlaceholderLifecycle:
    def _placeholder(self, client) -> int:
        return client.get("/api/aircraft").json()[0]["id"]

    def test_put_clears_placeholder(self, client, app_db):
        pid = self._placeholder(client)
        resp = client.put(f"/api/aircraft/{pid}", json={"cruise_speed_kt": 140})
        assert resp.json()["is_placeholder"] is False

    def test_put_keeping_zzzz_is_no_longer_placeholder(self, client, app_db):
        pid = self._placeholder(client)
        resp = client.put(f"/api/aircraft/{pid}", json={"icao_type": "ZZZZ"})
        assert resp.json()["icao_type"] == "ZZZZ"
        assert resp.json()["is_placeholder"] is False
        # ...and so a later POST no longer replaces it.
        client.post("/api/aircraft", json={"icao_type": "C172"})
        assert len(_rows(app_db)) == 2

    def test_post_replaces_sole_placeholder_and_moves_flights(self, client, app_db):
        pid = self._placeholder(client)
        _save_flight(app_db, "f-1", aircraft_id=pid)
        resp = client.post("/api/aircraft", json={"icao_type": "SR22", "cruise_speed_kt": 170})
        assert resp.status_code == 201
        new = resp.json()
        assert new["is_default"] is True and new["is_placeholder"] is False
        rows = _rows(app_db)
        assert [r.id for r in rows] == [new["id"]]
        assert _flight_aircraft_id(app_db, "f-1") == new["id"]

    def test_post_alongside_real_aircraft_leaves_placeholder(self, client, app_db):
        pid = self._placeholder(client)
        real = _add_aircraft(app_db, icao_type="C172", is_default=False)
        resp = client.post("/api/aircraft", json={"icao_type": "SR22"})
        assert resp.status_code == 201
        ids = {r.id for r in _rows(app_db)}
        assert ids == {pid, real, resp.json()["id"]}
        # The placeholder stays the default: the POST didn't ask to change it.
        assert next(r for r in _rows(app_db) if r.id == pid).is_default


# --- flights ---------------------------------------------------------------


def _flight_payload(**kw) -> dict:
    dep = (datetime.now(timezone.utc) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    return {
        "waypoints": ["EGTK", "LFAT"],
        "departure_time": dep.isoformat(),
        "cruise_altitude_ft": 8000,
        "flight_duration_hours": 1.0,
        **kw,
    }


class TestFlightsAttachDefault:
    def test_create_flight_without_aircraft_attaches_default(self, client, app_db):
        resp = client.post("/api/flights", json=_flight_payload())
        assert resp.status_code == 201, resp.text
        default = _rows(app_db)[0]
        assert default.is_default and default.is_placeholder
        assert resp.json()["aircraft_id"] == default.id
        assert _flight_aircraft_id(app_db, resp.json()["id"]) == default.id

    def test_create_flight_with_aircraft_keeps_it(self, client, app_db):
        _add_aircraft(app_db, icao_type="C172", is_default=True)
        other = _add_aircraft(app_db, icao_type="SR22", is_default=False)
        resp = client.post("/api/flights", json=_flight_payload(aircraft_id=other))
        assert resp.status_code == 201
        assert _flight_aircraft_id(app_db, resp.json()["id"]) == other

    def test_null_aircraft_flight_reads_as_default(self, client, app_db):
        default = _add_aircraft(app_db, icao_type="C172", is_default=True)
        _save_flight(app_db, "legacy-1", aircraft_id=None)
        body = client.get("/api/flights/legacy-1").json()
        assert body["aircraft_id"] == default
        assert body["aircraft"]["icao_type"] == "C172"
        assert _flight_aircraft_id(app_db, "legacy-1") is None  # read-only

    def test_clear_sentinel_resolves_to_default(self, client, app_db):
        default = _add_aircraft(app_db, icao_type="C172", is_default=True)
        other = _add_aircraft(app_db, icao_type="SR22", is_default=False)
        _save_flight(app_db, "f-2", aircraft_id=other)
        resp = client.patch("/api/flights/f-2", json={"aircraft_id": 0})
        assert resp.status_code == 200, resp.text
        assert resp.json()["aircraft_id"] == default


class TestResolveFlightAircraft:
    def test_explicit_then_default(self, app_db):
        default = _add_aircraft(app_db, icao_type="C172", is_default=True)
        other = _add_aircraft(app_db, icao_type="SR22", is_default=False)
        s = app_db()
        f = Flight(id="x", user_id=DEV_USER_ID, route_name="r",
                   departure_time=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
                   aircraft_id=other)
        assert resolve_flight_aircraft(s, f).id == other
        f.aircraft_id = None
        assert resolve_flight_aircraft(s, f).id == default
        s.close()

    def test_none_without_any_aircraft(self, app_db):
        s = app_db()
        f = Flight(id="x", user_id=DEV_USER_ID, route_name="r",
                   departure_time=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
        assert resolve_flight_aircraft(s, f) is None
        s.close()


class TestCruiseIasResolution:
    """``_resolve_cruise_ias_kt``: aircraft only, NULL → the default aircraft."""

    def test_aircraft_speed_ignores_profile(self, app_db):
        from weatherbrief.api.packs import _resolve_cruise_ias_kt

        pid = _add_profile(app_db, {"speed_kt": 100})
        aid = _add_aircraft(app_db, icao_type="SR22", is_default=True, cruise_speed_kt=170)
        s = app_db()
        f = Flight(id="x", user_id=DEV_USER_ID, route_name="r", profile_id=pid,
                   departure_time=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
                   aircraft_id=aid)
        assert _resolve_cruise_ias_kt(s, f) == 170.0
        s.close()

    def test_null_aircraft_uses_default_aircraft(self, app_db):
        from weatherbrief.api.packs import _resolve_cruise_ias_kt

        _add_profile(app_db, {"speed_kt": 100})
        _add_aircraft(app_db, icao_type="SR22", is_default=True, cruise_speed_kt=170)
        s = app_db()
        f = Flight(id="x", user_id=DEV_USER_ID, route_name="r",
                   departure_time=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
        assert _resolve_cruise_ias_kt(s, f) == 170.0
        s.close()

    def test_speedless_aircraft_has_no_profile_fallback(self, app_db):
        from weatherbrief.api.packs import _resolve_cruise_ias_kt

        _add_profile(app_db, {"speed_kt": 100})
        aid = _add_aircraft(app_db, icao_type="SR22", is_default=True)
        s = app_db()
        f = Flight(id="x", user_id=DEV_USER_ID, route_name="r",
                   departure_time=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
                   aircraft_id=aid)
        assert _resolve_cruise_ias_kt(s, f) is None
        s.close()


class TestReviewFollowUps:
    def test_extra_defaults_collapse_to_oldest(self, app_db):
        first = _add_aircraft(app_db, icao_type="C172", is_default=True)
        _add_aircraft(app_db, icao_type="SR22", is_default=True)
        s = app_db()
        assert ensure_default_aircraft(s, DEV_USER_ID).id == first
        s.commit()
        s.close()
        assert [r.is_default for r in _rows(app_db)] == [True, False]

    def test_default_memo_follows_default_changes(self, app_db):
        from weatherbrief.storage.aircraft import create_aircraft, get_default_aircraft

        first = _add_aircraft(app_db, icao_type="C172", is_default=True)
        s = app_db()
        assert get_default_aircraft(s, DEV_USER_ID).id == first
        new = create_aircraft(s, DEV_USER_ID, icao_type="SR22", is_default=True)
        assert get_default_aircraft(s, DEV_USER_ID).id == new.id
        s.close()
