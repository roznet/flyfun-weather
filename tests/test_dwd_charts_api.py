"""API tests for the /dwd-chart/{chart_id} endpoint."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.fetch.dwd_charts import cycle_dir
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import save_flight, save_pack_meta


_NOW = datetime.now(timezone.utc)
_FUTURE_DEPARTURE = _NOW + timedelta(days=2)
_FUTURE_DEPARTURE_DT = _FUTURE_DEPARTURE.replace(
    hour=9, minute=0, second=0, microsecond=0,
)
_FUTURE_DEPARTURE_DATE = _FUTURE_DEPARTURE.strftime("%Y-%m-%d")
_RUN_CYCLE = "2026-05-08T06Z"
_PNG = b"\x89PNG\r\n\x1a\n" + b"y" * 64


def _make_flight_id(route_name: str, date_str: str, *, user: str = DEV_USER_ID) -> str:
    h = hashlib.sha256(
        json.dumps(
            {"alt": 8000, "ceil": 18000, "dur": 4.5, "time": "09:00", "user": user},
            sort_keys=True,
        ).encode()
    ).hexdigest()[:4]
    return f"{route_name}-{date_str}-{h}"


@pytest.fixture
def app_db():
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
def client(app_db, tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-api-tests")

    app = create_app()

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


def _seed_flight_and_pack(
    app_db,
    *,
    run_cycle: str | None = _RUN_CYCLE,
    in_coverage: bool = True,
    within_horizon: bool = True,
    default_id: str | None = "048",
) -> tuple[Flight, BriefingPackMeta]:
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

    meta = BriefingPackMeta(
        flight_id=flight.id,
        fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2,
        has_gramet=False,
        has_skewt=False,
        has_digest=False,
        assessment="GREEN",
        assessment_reason="ok",
        dwd_charts_run_cycle=run_cycle,
        dwd_charts_default_id=default_id,
        dwd_charts_in_coverage=in_coverage,
        dwd_charts_within_horizon=within_horizon,
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()
    return flight, meta


def _write_chart_bytes(data_dir: Path, run_cycle: str, chart_id: str) -> Path:
    cdir = cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / f"{chart_id}.png"
    path.write_bytes(_PNG)
    return path


def test_dwd_chart_returns_png_bytes(client, app_db, tmp_path):
    flight, meta = _seed_flight_and_pack(app_db)
    _write_chart_bytes(tmp_path / "data", _RUN_CYCLE, "ana")

    ts = meta.fetch_timestamp.isoformat()
    resp = client.get(
        f"/api/flights/{flight.id}/packs/{ts}/dwd-chart/ana",
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == _PNG


def test_dwd_chart_410_when_evicted(client, app_db, tmp_path):
    flight, meta = _seed_flight_and_pack(app_db)
    # No bytes written → cache miss
    ts = meta.fetch_timestamp.isoformat()
    resp = client.get(
        f"/api/flights/{flight.id}/packs/{ts}/dwd-chart/048",
    )
    assert resp.status_code == 410


def test_dwd_chart_404_when_no_run_cycle(client, app_db, tmp_path):
    flight, meta = _seed_flight_and_pack(app_db, run_cycle=None, in_coverage=False)
    ts = meta.fetch_timestamp.isoformat()
    resp = client.get(
        f"/api/flights/{flight.id}/packs/{ts}/dwd-chart/ana",
    )
    assert resp.status_code == 404


def test_dwd_chart_400_on_bogus_chart_id(client, app_db, tmp_path):
    flight, meta = _seed_flight_and_pack(app_db)
    ts = meta.fetch_timestamp.isoformat()
    resp = client.get(
        f"/api/flights/{flight.id}/packs/{ts}/dwd-chart/999",
    )
    assert resp.status_code == 400


def test_pack_meta_response_includes_dwd_chart_fields(client, app_db, tmp_path):
    """The four DWD chart meta fields flow through to the API response."""
    flight, meta = _seed_flight_and_pack(app_db)
    resp = client.get(f"/api/flights/{flight.id}/packs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    pack = body[0]
    assert pack["dwd_charts_run_cycle"] == _RUN_CYCLE
    assert pack["dwd_charts_default_id"] == "048"
    assert pack["dwd_charts_in_coverage"] is True
    assert pack["dwd_charts_within_horizon"] is True
