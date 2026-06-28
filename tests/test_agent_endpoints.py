"""Smoke tests for the ChatGPT / OpenAPI front-door (``/agent/v1``).

The shared response *shaping* (``connectors.views``) is covered by
``test_connectors_views`` and the MCP tool path by ``test_mcp_advisory_detail``.
This file covers the part unique to ``api.agent``: the in-process route wiring —
the ownership gate ordering, the status envelopes, and the two error paths the
PR #282 review flagged and fixed (the private-flight info leak and the
``", ".join(None)`` 404→500 TypeError). Modelled on ``test_digest_context_api``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta

_NOW = datetime.now(timezone.utc)
_DEP = (_NOW + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
_OTHER_USER = "someone-else"


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
    # raise_server_exceptions=False so an unhandled 500 surfaces as a response
    # to assert on, rather than bubbling out of the test as an exception.
    return TestClient(app, raise_server_exceptions=False)


def _seed_flight(app_db, *, owner: str = DEV_USER_ID, private: bool = False,
                 suffix: str = "ok") -> Flight:
    fid = "egtk_lsgs-" + hashlib.sha256(suffix.encode()).hexdigest()[:6]
    session = app_db()
    if owner != DEV_USER_ID and session.get(UserRow, owner) is None:
        # flights.user_id is an FK — the owner row must exist before insert.
        session.add(UserRow(
            id=owner, provider="local", provider_sub=owner,
            email=f"{owner}@localhost", display_name=owner, approved=True,
        ))
        session.flush()
    flight = Flight(
        id=fid, user_id=owner, route_name="egtk_lsgs",
        waypoints=["EGTK", "LSGS"], departure_time=_DEP, private=private,
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        flight_duration_hours=4.5, created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, owner)
    session.commit()
    session.close()
    return flight


def _seed_pack(app_db, flight: Flight, *, owner: str = DEV_USER_ID) -> str:
    """Add a pack meta and return its ISO timestamp."""
    session = app_db()
    meta = BriefingPackMeta(
        flight_id=flight.id, fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2, has_gramet=False, has_skewt=False, has_digest=True,
        assessment="RED", assessment_reason="convective",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()
    return meta.fetch_timestamp.isoformat()


def _pack_dir(flight: Flight, ts: str, *, owner: str = DEV_USER_ID) -> Path:
    d = Path(pack_dir_for(owner, flight.id, ts))
    d.mkdir(parents=True, exist_ok=True)
    return d


# A minimal route_advisories.json the shared shapers understand. The second
# advisory deliberately omits ``advisory_id`` (→ None) to exercise the
# None-filter on the "not found" 404 path.
_ADVISORIES = {
    "advisories": [
        {
            "advisory_id": "convective",
            "aggregate_status": "red",
            "aggregate_detail": "High CAPE along route",
            "per_model": [
                {"model": "gfs", "status": "red", "detail": "CAPE 2500",
                 "affected_pct": 40, "cross_check": {"note": "scheme quiet"}},
                {"model": "ecmwf", "status": "amber", "detail": "CAPE 1200",
                 "affected_pct": 15},
            ],
            "parameters_used": {"cape_threshold_jkg": 1000},
        },
        {"aggregate_status": "amber"},  # no advisory_id -> None in the available list
    ],
    "catalog": [
        {"id": "convective", "name": "Convection", "category": "thunderstorm",
         "description": "Convective activity along the route."},
    ],
}


# A minimal serialized RouteAlternates block, as it sits under the "alternates"
# key of briefing.json. Exercises the snapshot-extraction + shaper wiring.
_ALTERNATES = {
    "destination_icao": "LSGS",
    "destination_category": "MVFR",
    "destination_crosswind_kt": 12.0,
    "corridor_nm": 20.0,
    "radius_nm": 50.0,
    "candidates_evaluated": 40,
    "approach_filter_relaxed": False,
    "nearest_improving": [
        {"axis": "category", "icao": "LFLP", "distance_from_dest_nm": 18.0,
         "position": "before"},
    ],
    "alternates": [
        {"icao": "LFLP", "name": "Annecy", "lat": 45.9, "lon": 6.1,
         "distance_from_dest_nm": 18.0, "position": "before", "flight_category": "VFR",
         "best_approach_type": "RNP", "point_of_entry": True, "is_major": False,
         "dominates_destination": True,
         "faa": {"verdict": "likely", "source": "nwp"},
         "easa": {"verdict": "likely", "source": "nwp"}},
    ],
    "alternate_requirement": {
        "destination_icao": "LSGS",
        "faa": {"regime": "faa", "status": "not_required", "reason": "ok",
                "source": "nwp",
                "ceiling": {"label": "ceiling", "unit": "ft", "forecast": 1500.0,
                            "required_min": 600.0, "required_max": 600.0,
                            "verdict": "not_required"},
                "visibility": {"label": "visibility", "unit": "m", "forecast": 8000.0,
                               "required_min": 1600.0, "required_max": 1600.0,
                               "verdict": "not_required"}},
        "easa": {"regime": "easa", "status": "required", "reason": "low",
                 "source": "nwp",
                 "ceiling": {"label": "ceiling", "unit": "ft", "forecast": 900.0,
                             "required_min": 600.0, "required_max": 800.0,
                             "verdict": "required"},
                 "visibility": {"label": "visibility", "unit": "m", "forecast": 8000.0,
                                "required_min": 1600.0, "required_max": 3200.0,
                                "verdict": "required"}},
        "caveats": ["estimated minima"],
    },
}


def test_get_alternates_happy_path(client, app_db):
    flight = _seed_flight(app_db, suffix="alt")
    ts = _seed_pack(app_db, flight)
    pd = _pack_dir(flight, ts)
    (pd / "briefing.json").write_text(json.dumps({"alternates": _ALTERNATES}))

    resp = client.get(f"/agent/v1/flights/{flight.id}/alternates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["flight_id"] == flight.id
    assert body["destination"]["icao"] == "LSGS"
    assert body["requirement"]["faa"]["status"] == "not_required"
    assert body["requirement"]["easa"]["status"] == "required"
    assert body["candidates"][0]["icao"] == "LFLP"
    assert body["candidates"][0]["point_of_entry"] is True
    # The weather-vs-operational caveat must ship in the payload.
    assert "operational" in body["note"].lower()
    assert body["web_url"].endswith(f"flight={flight.id}")


def test_get_alternates_none_when_not_computed(client, app_db):
    """A pack whose snapshot has no alternates returns a 200 'none' envelope."""
    flight = _seed_flight(app_db, suffix="altnone")
    ts = _seed_pack(app_db, flight)
    pd = _pack_dir(flight, ts)
    (pd / "briefing.json").write_text(json.dumps({"route": {"waypoints": []}}))

    resp = client.get(f"/agent/v1/flights/{flight.id}/alternates")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "none"


def test_get_alternates_404_for_private_other_user(client, app_db):
    """Ownership gate runs first — no leak of a private flight's alternates."""
    flight = _seed_flight(app_db, owner=_OTHER_USER, private=True, suffix="altleak")
    ts = _seed_pack(app_db, flight, owner=_OTHER_USER)
    pd = _pack_dir(flight, ts, owner=_OTHER_USER)
    (pd / "briefing.json").write_text(json.dumps({"alternates": _ALTERNATES}))

    resp = client.get(f"/agent/v1/flights/{flight.id}/alternates")
    assert resp.status_code == 404, resp.text


def test_get_briefing_includes_alternates_hook(client, app_db):
    """get_briefing carries the compact alternates hook (not the full list)."""
    flight = _seed_flight(app_db, suffix="althook")
    ts = _seed_pack(app_db, flight)
    pd = _pack_dir(flight, ts)
    (pd / "route_advisories.json").write_text(json.dumps(_ADVISORIES))
    (pd / "briefing.json").write_text(json.dumps({"alternates": _ALTERNATES}))

    resp = client.get(f"/agent/v1/flights/{flight.id}/briefing")
    assert resp.status_code == 200, resp.text
    hook = resp.json()["alternates"]
    assert hook["detail_tool"] == "get_alternates"
    assert hook["candidate_count"] == 1
    assert hook["alternate_required"] == {"faa": "not_required", "easa": "required"}
    assert "candidates" not in hook  # hook stays lean


def test_get_briefing_happy_path(client, app_db):
    flight = _seed_flight(app_db, suffix="happy")
    ts = _seed_pack(app_db, flight)
    pd = _pack_dir(flight, ts)
    (pd / "route_advisories.json").write_text(json.dumps(_ADVISORIES))
    (pd / "digest.json").write_text(json.dumps({"summary": "VFR with afternoon build-ups"}))

    resp = client.get(f"/agent/v1/flights/{flight.id}/briefing")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Envelope + assessment passthrough.
    assert body["status"] in ("ready", "stale")
    assert body["assessment"] == "RED"
    assert body["flight_id"] == flight.id
    assert body["web_url"].endswith(f"flight={flight.id}")
    # Advisories went through the shared shaper (neutral cross-check hint flag,
    # not a valenced "disagreement" signal).
    conv = next(a for a in body["advisories"] if a["id"] == "convective")
    assert conv["status"] == "red"
    assert conv["cross_check_present"] is True
    assert conv["name"] == "Convection"
    assert body["digest"] == {"summary": "VFR with afternoon build-ups"}


def test_get_briefing_none_when_no_pack(client, app_db):
    """A flight with no pack returns a 200 'none' envelope, not an error."""
    flight = _seed_flight(app_db, suffix="nopack")
    resp = client.get(f"/agent/v1/flights/{flight.id}/briefing")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "none"
    assert body["flight_id"] == flight.id


def test_get_briefing_404_for_private_other_user(client, app_db):
    """The ownership gate runs first: a private flight owned by another user
    must 404 before any pack state is touched (the leak PR #282 fixed)."""
    flight = _seed_flight(app_db, owner=_OTHER_USER, private=True, suffix="leak")
    # Pack exists on disk for the real owner — must NOT be observable.
    ts = _seed_pack(app_db, flight, owner=_OTHER_USER)
    _pack_dir(flight, ts, owner=_OTHER_USER)

    resp = client.get(f"/agent/v1/flights/{flight.id}/briefing")
    assert resp.status_code == 404, resp.text


def test_get_digest_context_404_for_private_other_user(client, app_db):
    """Same gate on the specifically-flagged get_digest_context front-door."""
    flight = _seed_flight(app_db, owner=_OTHER_USER, private=True, suffix="leak2")
    resp = client.get(f"/agent/v1/flights/{flight.id}/digest-context")
    assert resp.status_code == 404, resp.text


def test_advisory_detail_unknown_id_is_404_not_500(client, app_db):
    """Requesting a missing advisory returns a clean 404 listing the available
    ids — and does NOT 500 even when an advisory dict has no advisory_id
    (the ", ".join(None) TypeError the review fixed)."""
    flight = _seed_flight(app_db, suffix="notfound")
    ts = _seed_pack(app_db, flight)
    pd = _pack_dir(flight, ts)
    (pd / "route_advisories.json").write_text(json.dumps(_ADVISORIES))

    resp = client.get(f"/agent/v1/flights/{flight.id}/advisories/does_not_exist")
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    # The real id is listed; the None-id advisory was filtered, not crashed on.
    assert "convective" in detail


def test_advisory_detail_injects_cross_check_guardrail(client, app_db):
    """The load-bearing CROSS_CHECK_NOTE guardrail is present at the agent
    endpoint, and a convective drill-down attaches the convective note."""
    flight = _seed_flight(app_db, suffix="detail")
    ts = _seed_pack(app_db, flight)
    pd = _pack_dir(flight, ts)
    (pd / "route_advisories.json").write_text(json.dumps(_ADVISORIES))
    # Empty analyses still exercises the convective enrichment wiring + note.
    (pd / "route_analyses.json").write_text(json.dumps({"analyses": []}))

    resp = client.get(f"/agent/v1/flights/{flight.id}/advisories/convective")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["advisory_id"] == "convective"
    assert body["aggregate_status"] == "red"
    assert body["name"] == "Convection"
    assert "downgrade signal" in body["cross_check_note"]
    assert "per_model" in body and len(body["per_model"]) == 2
    assert "convective_note" in body
    assert body["flight_id"] == flight.id
