"""API test for GET /flights/{id}/packs/{ts}/alternates (#210/#249).

This is the REST endpoint the MCP client (and iOS) call to pull the weather-
alternates block out of a pack's snapshot. The ChatGPT connector reads the file
directly (covered by test_agent_endpoints); this exercises the packs.py route
itself — extraction from briefing.json, the 404 paths, and the ownership gate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta

_NOW = datetime.now(timezone.utc)
_DEP = (_NOW + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)

_ALTERNATES = {
    "destination_icao": "LSGS",
    "destination_category": "MVFR",
    "candidates_evaluated": 12,
    "alternates": [
        {"icao": "LFLP", "name": "Annecy", "lat": 45.9, "lon": 6.1,
         "distance_from_dest_nm": 18.0, "position": "before", "flight_category": "VFR"},
    ],
    "alternate_requirement": {
        "destination_icao": "LSGS",
        "faa": {"regime": "faa", "status": "not_required", "reason": "ok", "source": "nwp",
                "ceiling": None, "visibility": None},
        "easa": {"regime": "easa", "status": "required", "reason": "low", "source": "nwp",
                 "ceiling": None, "visibility": None},
    },
}


@pytest.fixture
def app_db():
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    app = create_app()

    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


def _seed(app_db, *, owner: str = DEV_USER_ID, suffix: str = "ok") -> tuple[Flight, str]:
    fid = "egtk_lsgs-" + hashlib.sha256(suffix.encode()).hexdigest()[:6]
    session = app_db()
    flight = Flight(
        id=fid, user_id=owner, route_name="egtk_lsgs",
        waypoints=["EGTK", "LSGS"], departure_time=_DEP,
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        flight_duration_hours=4.5, created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, owner)
    meta = BriefingPackMeta(
        flight_id=flight.id, fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2, has_gramet=False, has_skewt=False, has_digest=True,
        assessment="RED", assessment_reason="convective",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()
    return flight, meta.fetch_timestamp.isoformat()


def test_alternates_endpoint_extracts_block(client, app_db):
    flight, ts = _seed(app_db, suffix="extract")
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "briefing.json").write_text(json.dumps({"alternates": _ALTERNATES}))

    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/alternates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The raw RouteAlternates block is returned verbatim (shaping happens in
    # the connector layer, not the endpoint).
    assert body["destination_icao"] == "LSGS"
    assert body["alternates"][0]["icao"] == "LFLP"
    assert body["alternate_requirement"]["easa"]["status"] == "required"


def test_alternates_endpoint_404_when_no_alternates(client, app_db):
    flight, ts = _seed(app_db, suffix="noalt")
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "briefing.json").write_text(json.dumps({"route": {"waypoints": []}}))

    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/alternates")
    assert resp.status_code == 404, resp.text


def test_alternates_endpoint_404_when_no_snapshot(client, app_db):
    flight, ts = _seed(app_db, suffix="nosnap")
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)  # pack exists, no briefing.json

    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/alternates")
    assert resp.status_code == 404, resp.text
