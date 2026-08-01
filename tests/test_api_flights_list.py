"""Tests for aircraft resolution in the GET /api/flights list response.

The list endpoint batch-prefetches every referenced user aircraft in ONE
``select(...).where(UserAircraftRow.id.in_(ids))`` query instead of a
per-flight ``db.get`` (mysql-review N+1 fix). These tests pin both the query
count — via an engine cursor spy filtering statements that read the
``user_aircraft`` table — and the unchanged response shape (owner-visible
tail/nickname, ``None`` aircraft block).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.db.models import UserAircraftRow
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight


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


@pytest.fixture
def aircraft_query_count(app_db):
    """Count SELECT statements that read the user_aircraft table.

    Engine-level cursor spy (the ground truth for "queries issued") — a
    per-flight ``db.get`` and the batch IN-select both surface here, while
    identity-map hits emit no SQL at all.
    """
    engine = app_db.kw["bind"]
    count = 0

    @event.listens_for(engine, "before_cursor_execute")
    def _spy(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal count
        if statement.lstrip().upper().startswith("SELECT") and "user_aircraft" in statement:
            count += 1

    yield lambda: count
    event.remove(engine, "before_cursor_execute", _spy)


def _save_aircraft(
    app_db,
    *,
    icao_type: str,
    tail: str | None = None,
    nickname: str | None = None,
) -> int:
    s = app_db()
    ac = UserAircraftRow(
        user_id=DEV_USER_ID, icao_type=icao_type,
        tail_number=tail, nickname=nickname,
    )
    s.add(ac)
    s.commit()
    aircraft_id = ac.id
    s.close()
    return aircraft_id


def _save_flight(
    app_db,
    *,
    route: str,
    days_offset: int,
    idx: int,
    aircraft_id: int | None = None,
) -> Flight:
    s = app_db()
    dep = datetime.now(timezone.utc) + timedelta(days=days_offset)
    h = hashlib.sha256(json.dumps(
        {"alt": 8000, "ceil": 18000, "dur": 1.0, "time": dep.strftime("%H:%M"),
         "user": DEV_USER_ID, "idx": idx},
        sort_keys=True,
    ).encode()).hexdigest()[:4]
    f = Flight(
        id=f"{route}-{dep.strftime('%Y-%m-%d')}-{h}",
        user_id=DEV_USER_ID,
        route_name=route,
        waypoints=["EGTK", "LFAT"],
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        aircraft_id=aircraft_id,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    save_flight(s, f, DEV_USER_ID)
    s.commit()
    s.close()
    return f


class TestAircraftBatchPrefetch:
    def test_distinct_aircraft_fetched_in_one_query(self, client, app_db, aircraft_query_count):
        # Two flights with different aircraft + one with none: the per-flight
        # db.get path issues one SELECT per aircraft-carrying flight (the
        # ORM row is dropped right after the AircraftInfo is built, so the
        # weak-ref identity map never dedups); the batch prefetch issues 1.
        ac1 = _save_aircraft(app_db, icao_type="P28A", tail="G-ABCD", nickname="Archer")
        ac2 = _save_aircraft(app_db, icao_type="SR22", tail="G-EFGH")
        f1 = _save_flight(app_db, route="ac1", days_offset=+5, idx=1, aircraft_id=ac1)
        f2 = _save_flight(app_db, route="ac2", days_offset=+6, idx=2, aircraft_id=ac2)
        f3 = _save_flight(app_db, route="noac", days_offset=+7, idx=3)

        # Baseline after seeding: only request-time queries count.
        baseline = aircraft_query_count()
        resp = client.get("/api/flights")
        assert resp.status_code == 200
        assert aircraft_query_count() - baseline == 1

        by_id = {f["id"]: f for f in resp.json()}
        assert by_id[f1.id]["aircraft"] == {
            "id": ac1,
            "icao_type": "P28A",
            "type_name": "Piper PA-28 Cherokee / Warrior / Archer",
            "tail_number": "G-ABCD",  # owner sees own tail
            "nickname": "Archer",
        }
        assert by_id[f2.id]["aircraft"] == {
            "id": ac2,
            "icao_type": "SR22",
            "type_name": "Cirrus SR22",
            "tail_number": "G-EFGH",
            "nickname": None,
        }
        assert by_id[f3.id]["aircraft"] is None
        assert by_id[f3.id]["aircraft_id"] is None

    def test_shared_aircraft_still_one_query(self, client, app_db, aircraft_query_count):
        # Two flights sharing one aircraft + one with none — the batch map is
        # keyed on distinct ids, so shared references cost nothing extra.
        ac = _save_aircraft(app_db, icao_type="C172", tail="G-IJKL")
        f1 = _save_flight(app_db, route="sh1", days_offset=+5, idx=1, aircraft_id=ac)
        f2 = _save_flight(app_db, route="sh2", days_offset=+6, idx=2, aircraft_id=ac)
        f3 = _save_flight(app_db, route="shn", days_offset=+7, idx=3)

        baseline = aircraft_query_count()
        resp = client.get("/api/flights")
        assert resp.status_code == 200
        assert aircraft_query_count() - baseline == 1

        by_id = {f["id"]: f for f in resp.json()}
        assert by_id[f1.id]["aircraft"]["id"] == ac
        assert by_id[f2.id]["aircraft"]["id"] == ac
        assert by_id[f1.id]["aircraft"]["tail_number"] == "G-IJKL"
        assert by_id[f3.id]["aircraft"] is None

    def test_no_aircraft_referenced_issues_no_query(self, client, app_db, aircraft_query_count):
        _save_flight(app_db, route="bare1", days_offset=+5, idx=1)
        _save_flight(app_db, route="bare2", days_offset=+6, idx=2)

        resp = client.get("/api/flights")
        assert resp.status_code == 200
        assert aircraft_query_count() == 0
        assert all(f["aircraft"] is None for f in resp.json())
