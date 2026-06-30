"""API test for GET /api/flights/{id}/packs/{ts}/advisories/{advisory_id}/detail.

The one backend touch of the iOS modernisation epic (#291 / #285): the advisory
"why it's RED" drill-down, reusing connectors/views.py shaping over REST.
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


_ADVISORIES = {
    "advisories": [
        {
            "advisory_id": "icing",
            "aggregate_status": "amber",
            "aggregate_detail": "Light icing FL060-080",
            "per_model": [
                {
                    "model": "gfs",
                    "status": "amber",
                    "detail": "20% affected",
                    "affected_pct": 20.0,
                    "affected_nm": 10.0,
                    "total_nm": 50.0,
                    "cross_check": None,
                }
            ],
            "parameters_used": {"affected_pct_amber": 20.0},
        },
        {
            "advisory_id": "convective",
            "aggregate_status": "red",
            "aggregate_detail": "RED on high CAPE",
            "per_model": [
                {
                    "model": "gfs",
                    "status": "red",
                    "detail": "CAPE 1200 J/kg",
                    "affected_pct": 40.0,
                    "affected_nm": 30.0,
                    "total_nm": 80.0,
                    "cross_check": "High CAPE; NWP scheme quiet — expected pattern.",
                }
            ],
            "parameters_used": {"cape_red_jkg": 1000.0},
        },
        {
            "advisory_id": "vfr_feasibility",
            "aggregate_status": "red",
            "aggregate_detail": "VFR not feasible — departure deck",
            "per_model": [
                {
                    "model": "gfs",
                    "status": "red",
                    "detail": "OVC deck below cruise",
                    "affected_pct": 25.0,
                    "affected_nm": 22.0,
                    "total_nm": 90.0,
                    "cross_check": None,
                    "mitigations": [
                        {
                            "kind": "route_position",
                            "addresses": "climb_deck",
                            "detail": "Climb to cruise after ~40 nm to clear the deck.",
                            "mitigated_status": "amber",
                            "distance_nm": 40.0,
                            "reference": "departure",
                        }
                    ],
                }
            ],
            "parameters_used": {},
            "aggregate_mitigations": [
                {
                    "kind": "altitude",
                    "addresses": "cruise_imc",
                    "detail": "Fly 6,000 ft to stay below the cloud base.",
                    "mitigated_status": "green",
                    "altitude_ft": 6000,
                }
            ],
        },
    ],
    "catalog": [
        {"id": "icing", "name": "Icing", "category": "hazard", "description": "Airframe icing risk."},
        {"id": "convective", "name": "Convective", "category": "hazard", "description": "Thunderstorm risk."},
        {"id": "vfr_feasibility", "name": "VFR Feasibility", "category": "feasibility",
         "description": "Composite VFR go/no-go."},
    ],
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


def _seed(app_db, *, write_advisories=True, write_route_analyses=False) -> tuple[Flight, str]:
    fid = "egtk_lsgs-" + hashlib.sha256(b"detail").hexdigest()[:4]
    session = app_db()
    flight = Flight(
        id=fid, user_id=DEV_USER_ID, route_name="egtk_lsgs",
        waypoints=["EGTK", "LSGS"], departure_time=_DEP,
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        flight_duration_hours=4.5, created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, DEV_USER_ID)
    meta = BriefingPackMeta(
        flight_id=flight.id, fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2, has_gramet=False, has_skewt=False, has_digest=False,
        assessment="RED", assessment_reason="convective",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()

    ts = meta.fetch_timestamp.isoformat()
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    if write_advisories:
        (pack_dir / "route_advisories.json").write_text(json.dumps(_ADVISORIES))
    if write_route_analyses:
        (pack_dir / "route_analyses.json").write_text(json.dumps({"analyses": []}))
    return flight, ts


def test_generic_advisory_detail(client, app_db):
    flight, ts = _seed(app_db)
    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/advisories/icing/detail")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["advisory_id"] == "icing"
    assert body["aggregate_status"] == "amber"
    assert body["name"] == "Icing"  # catalog enrichment
    assert "cross_check_note" in body  # always present (the explainer, never an alert)
    assert len(body["per_model"]) == 1
    assert body["parameters_used"]["affected_pct_amber"] == 20.0
    # Non-convective → no convective reconciliation block.
    assert "convective" not in body


def test_convective_advisory_detail(client, app_db):
    flight, ts = _seed(app_db, write_route_analyses=True)
    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/advisories/convective/detail")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["advisory_id"] == "convective"
    # Convective carries the richest fill: the CAPE-vs-cover reconciliation block
    # plus its provenance note (parcel-derived tops vs the model's cloud field).
    assert "convective" in body
    assert isinstance(body["convective"], dict)
    assert "convective_note" in body


def test_advisory_detail_surfaces_mitigations(client, app_db):
    # The shaper plumbs mitigations through to the REST detail endpoint (#330):
    # aggregate + per-model objects plus the advice-only guardrail note.
    flight, ts = _seed(app_db)
    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/advisories/vfr_feasibility/detail")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aggregate_status"] == "red"  # mitigation never changes the grade
    assert body["aggregate_mitigations"][0]["addresses"] == "cruise_imc"
    assert body["aggregate_mitigations"][0]["mitigated_status"] == "green"
    assert body["per_model"][0]["mitigations"][0]["addresses"] == "climb_deck"
    assert "mitigation_note" in body


def test_advisory_detail_no_mitigation_note_when_absent(client, app_db):
    # Mitigation-free advisory → no guardrail note, no aggregate_mitigations key.
    flight, ts = _seed(app_db)
    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/advisories/icing/detail")
    body = resp.json()
    assert "mitigation_note" not in body
    assert "aggregate_mitigations" not in body


def test_unknown_advisory_returns_404(client, app_db):
    flight, ts = _seed(app_db)
    resp = client.get(f"/api/flights/{flight.id}/packs/{ts}/advisories/bogus/detail")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
