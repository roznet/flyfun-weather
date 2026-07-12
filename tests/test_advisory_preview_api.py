"""API test for POST .../advisories/preview — the non-persisting draft preview (#387, slice 4).

The preview endpoint must (a) return a fresh manifest computed under explicit
draft overrides and (b) never write anything into the pack dir (unlike
recalculate, which persists route_advisories.json + route_fronts.json).
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

_NOW = datetime(2026, 5, 31, 6, 0, tzinfo=timezone.utc)
_DEP = _NOW + timedelta(days=2)


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


def _route_analyses() -> dict:
    """A minimal but loadable route_analyses manifest (one point)."""
    return {
        "route_name": "EGKB-LFAT",
        "target_date": "2026-05-31",
        "departure_time": "2026-05-31T06:00:00+00:00",
        "flight_duration_hours": 2.0,
        "total_distance_nm": 160.0,
        "cruise_altitude_ft": 5000,
        "models": ["ecmwf", "gfs", "icon"],
        "analyses": [
            {
                "point_index": 0,
                "lat": 47.0,
                "lon": -1.5,
                "distance_from_origin_nm": 0.0,
                "interpolated_time": "2026-05-31T06:00:00+00:00",
                "forecast_hour": "2026-05-31T06:00:00+00:00",
                "track_deg": 90.0,
                "model_divergence": [],
            }
        ],
    }


def _seed(app_db) -> tuple[str, str, Path]:
    fid = "egkb_lfat-" + hashlib.sha256(b"preview").hexdigest()[:4]
    session = app_db()
    flight = Flight(
        id=fid, user_id=DEV_USER_ID, route_name="egkb_lfat",
        waypoints=["EGKB", "LFAT"], departure_time=_DEP,
        cruise_altitude_ft=5000, flight_ceiling_ft=18000,
        flight_duration_hours=2.0, created_at=_NOW - timedelta(days=1),
    )
    save_flight(session, flight, DEV_USER_ID)
    meta = BriefingPackMeta(
        flight_id=flight.id, fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2, has_gramet=False, has_skewt=False, has_digest=False,
        assessment="AMBER", assessment_reason="test",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()

    ts = meta.fetch_timestamp.isoformat()
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "route_analyses.json").write_text(json.dumps(_route_analyses()))
    return flight.id, ts, pack_dir


def test_preview_returns_manifest_without_persisting(client, app_db):
    """Preview returns a manifest and writes nothing into the pack dir."""
    flight_id, ts, pack_dir = _seed(app_db)
    assert not (pack_dir / "route_advisories.json").exists()

    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={"enabled": {"airport_wind": True}, "params": {}, "aggregation": "majority"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "manifest" in body
    assert isinstance(body["manifest"]["advisories"], list)

    # The core invariant: no artifact was written.
    assert not (pack_dir / "route_advisories.json").exists()
    assert not (pack_dir / "route_fronts.json").exists()


def test_preview_applies_param_override(client, app_db):
    """A draft param override shows up in the previewed advisory's parameters_used."""
    flight_id, ts, pack_dir = _seed(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={
            "enabled": {"airport_wind": True},
            "params": {"airport_wind": {"crosswind_red_kt": 33}},
        },
    )
    assert resp.status_code == 200, resp.text
    advisories = {a["advisory_id"]: a for a in resp.json()["manifest"]["advisories"]}
    assert "airport_wind" in advisories
    assert advisories["airport_wind"]["parameters_used"]["crosswind_red_kt"] == 33
    assert not (pack_dir / "route_advisories.json").exists()


def test_preview_empty_body_uses_saved_settings(client, app_db):
    """A bare body previews saved settings (baseline for the diff) and still persists nothing."""
    flight_id, ts, pack_dir = _seed(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={},
    )
    assert resp.status_code == 200, resp.text
    assert not (pack_dir / "route_advisories.json").exists()
