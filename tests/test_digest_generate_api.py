"""API tests for POST /api/flights/{id}/packs/{ts}/digest/generate.

On-demand AI summary generation for a pack whose profile had the AI summary
toggled off (llm_digest_requested=False). Runs only the LLM digest against
existing pack data — no re-fetch.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import (
    load_pack_meta,
    pack_dir_for,
    save_flight,
    save_pack_meta,
)
from weatherbrief.tasks.outputs import DigestResult

_NOW = datetime.now(timezone.utc)
_DEP = (_NOW + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)


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


def _seed(
    app_db, *, has_digest: bool, llm_digest_requested: bool, flexibility: str = "none"
) -> tuple[Flight, BriefingPackMeta]:
    fid = "egtk_lsgs-" + hashlib.sha256(b"digest").hexdigest()[:4]
    session = app_db()
    flight = Flight(
        id=fid, user_id=DEV_USER_ID, route_name="egtk_lsgs",
        waypoints=["EGTK", "LSGS"], departure_time=_DEP,
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        flight_duration_hours=4.5, created_at=_NOW - timedelta(days=1),
        flexibility=flexibility,
    )
    save_flight(session, flight, DEV_USER_ID)
    meta = BriefingPackMeta(
        flight_id=flight.id, fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2, has_gramet=False, has_skewt=False,
        has_digest=has_digest, llm_digest_requested=llm_digest_requested,
        assessment="AMBER", assessment_reason="advisory-derived",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()
    return flight, meta


def test_pack_meta_response_exposes_llm_digest_requested(client, app_db):
    """The flag round-trips to the API so the UI can branch on it."""
    flight, meta = _seed(app_db, has_digest=False, llm_digest_requested=False)
    ts = meta.fetch_timestamp.isoformat()
    resp = client.get(f"/api/flights/{flight.id}/packs/latest")
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_digest_requested"] is False


def test_pack_meta_response_surfaces_live_flexibility(client, app_db):
    """`/packs/latest` injects the flight's CURRENT flexibility (not a value
    baked into the stored pack) so clients gate the timing-scenario panel on the
    live setting even for an old pack. Regression for the iOS panel staying
    hidden after flexibility was set on another device (stale seeded flight)."""
    flight, meta = _seed(
        app_db, has_digest=False, llm_digest_requested=False, flexibility="same_day"
    )
    resp = client.get(f"/api/flights/{flight.id}/packs/latest")
    assert resp.status_code == 200, resp.text
    assert resp.json()["flexibility"] == "same_day"


def test_generate_404_when_pack_data_missing(client, app_db):
    """No briefing.json on disk → nothing to summarise."""
    flight, meta = _seed(app_db, has_digest=False, llm_digest_requested=False)
    ts = meta.fetch_timestamp.isoformat()
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)  # pack dir exists, but empty

    resp = client.post(f"/api/flights/{flight.id}/packs/{ts}/digest/generate")
    assert resp.status_code == 404, resp.text


def test_generate_idempotent_when_already_has_digest(client, app_db, monkeypatch):
    """A pack that already has a summary returns its meta without an LLM call."""
    called = {"n": 0}
    monkeypatch.setattr(
        "weatherbrief.tasks.outputs.run_llm_digest",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or DigestResult(),
    )
    flight, meta = _seed(app_db, has_digest=True, llm_digest_requested=True)
    ts = meta.fetch_timestamp.isoformat()
    Path(pack_dir_for(DEV_USER_ID, flight.id, ts)).mkdir(parents=True, exist_ok=True)

    resp = client.post(f"/api/flights/{flight.id}/packs/{ts}/digest/generate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_digest"] is True
    assert called["n"] == 0  # short-circuited before generating


def test_generate_happy_path(client, app_db, monkeypatch):
    """Generating flips has_digest True and adopts the digest's assessment."""
    flight, meta = _seed(app_db, has_digest=False, llm_digest_requested=False)
    ts = meta.fetch_timestamp.isoformat()
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "briefing.json").write_text(
        json.dumps({"days_out": 2, "departure_time": _DEP.isoformat()})
    )

    # Stub the heavy bits: snapshot reconstruction, route/region resolution,
    # and the actual LLM call. We're testing the endpoint's orchestration.
    from weatherbrief.fetch.variables import ModelRegion

    monkeypatch.setattr(
        "weatherbrief.api.packs._build_snapshot_from_pack",
        lambda pack_dir, briefing: SimpleNamespace(days_out=2),
    )
    monkeypatch.setattr(
        "weatherbrief.api.packs._build_route_config", lambda flight, db_path: None,
    )
    monkeypatch.setattr(
        "weatherbrief.fetch.variables.detect_model_region",
        lambda route: ModelRegion.EUROPE,
    )
    monkeypatch.setattr(
        "weatherbrief.tasks.outputs.run_llm_digest",
        lambda **k: DigestResult(
            path=pack_dir / "digest.json",
            digest=SimpleNamespace(assessment="RED", assessment_reason="storms en route"),
            llm_model="anthropic:claude", llm_input_tokens=100, llm_output_tokens=50,
        ),
    )

    resp = client.post(f"/api/flights/{flight.id}/packs/{ts}/digest/generate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_digest"] is True
    assert body["assessment"] == "RED"
    assert body["assessment_reason"] == "storms en route"

    # Persisted, not just echoed.
    session = app_db()
    reloaded = load_pack_meta(session, flight.id, meta.fetch_timestamp)
    session.close()
    assert reloaded.has_digest is True
    assert reloaded.assessment == "RED"


def test_generate_422_when_nothing_graded(client, app_db, monkeypatch):
    """#392: refuse to narrate a pack whose advisories all came back ungradeable.

    The pipeline already skips the digest for these and the web hides the button,
    but the endpoint is reachable directly — and a digest here would overwrite the
    honest UNAVAILABLE with an LLM GREEN derived from an empty manifest.
    """
    from weatherbrief.models import (
        AdvisoryStatus,
        RouteAdvisoriesManifest,
        RouteAdvisoryResult,
    )

    flight, meta = _seed(app_db, has_digest=False, llm_digest_requested=False)
    ts = meta.fetch_timestamp.isoformat()
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "briefing.json").write_text(
        json.dumps({"days_out": 2, "departure_time": _DEP.isoformat()})
    )
    (pack_dir / "route_advisories.json").write_text(
        RouteAdvisoriesManifest(
            advisories=[
                RouteAdvisoryResult(
                    advisory_id="icing.fiki",
                    aggregate_status=AdvisoryStatus.UNAVAILABLE,
                    per_model=[],
                ),
            ],
        ).model_dump_json()
    )

    from weatherbrief.fetch.variables import ModelRegion

    monkeypatch.setattr(
        "weatherbrief.api.packs._build_snapshot_from_pack",
        lambda pack_dir, briefing: SimpleNamespace(days_out=2),
    )
    monkeypatch.setattr(
        "weatherbrief.api.packs._build_route_config", lambda flight, db_path: None,
    )
    monkeypatch.setattr(
        "weatherbrief.fetch.variables.detect_model_region",
        lambda route: ModelRegion.EUROPE,
    )

    called: list[int] = []

    def _must_not_run(**_k):
        called.append(1)
        raise AssertionError("the LLM must not be called for an ungradeable pack")

    monkeypatch.setattr("weatherbrief.tasks.outputs.run_llm_digest", _must_not_run)

    resp = client.post(f"/api/flights/{flight.id}/packs/{ts}/digest/generate")

    assert resp.status_code == 422, resp.text
    assert called == []
