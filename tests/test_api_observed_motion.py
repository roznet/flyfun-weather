"""Observed-motion publication through the server snapshot surface."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from conftest import make_app_engine
from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.models.analysis import ForecastSnapshot, RouteConfig, Waypoint
from weatherbrief.models.observations import RealtimeRefreshResult, RouteObservations
from weatherbrief.models.observed_motion import empty_motion
from weatherbrief.observed.motion.route import route_identities
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta
from weatherbrief.storage.observed_motion import (
    MotionPublicationError,
    publish_motion_snapshot,
    reserve_motion_revision,
)
from weatherbrief.storage.snapshots import save_snapshot
from weatherbrief.tasks.artifacts import save_analysis_artifacts


def _route() -> RouteConfig:
    return RouteConfig(
        name="Motion test route",
        waypoints=[
            Waypoint(icao="EGTF", name="Fairoaks", lat=51.348, lon=-0.559),
            Waypoint(icao="LFQA", name="Reims", lat=49.310, lon=3.620),
        ],
        flight_duration_hours=2.0,
    )


def _disabled_motion(route: RouteConfig):
    departure = datetime(2026, 9, 5, 9, tzinfo=timezone.utc)
    geometry_id, timing_id = route_identities(route, departure)
    return empty_motion(
        route_geometry_id=geometry_id,
        planned_timing_id=timing_id,
        cutoff_at=datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc),
        revision=1,
        status="disabled",
        reason_codes=["feature_disabled"],
    )


@pytest.fixture
def motion_client(tmp_path, monkeypatch):
    """A real isolated API/database pair, with no provider configuration."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "motion-test-secret")
    engine = make_app_engine()
    sessions = sessionmaker(bind=engine)
    setup = sessions()
    setup.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="motion-test",
        email="motion@example.test", display_name="Motion Test", approved=True,
    ))
    setup.flush()
    setup.add(UserPreferencesRow(user_id=DEV_USER_ID))
    setup.commit()
    setup.close()
    app = create_app()

    def _database():
        session = sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _database
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    try:
        yield TestClient(app, raise_server_exceptions=False), sessions
    finally:
        engine.dispose()


def _stored_pack(sessions, route: RouteConfig):
    now = datetime.now(timezone.utc)
    flight = Flight(
        id="motion-flight",
        user_id=DEV_USER_ID,
        route_name=route.name,
        waypoints=[waypoint.icao for waypoint in route.waypoints],
        departure_time=now + timedelta(days=1),
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=route.flight_duration_hours,
        created_at=now,
    )
    session = sessions()
    save_flight(session, flight, DEV_USER_ID)
    pack = BriefingPackMeta(
        flight_id=flight.id,
        fetch_timestamp=now,
        days_out=0,
        has_gramet=False,
        has_skewt=False,
        has_digest=False,
    )
    save_pack_meta(session, pack)
    session.commit()
    session.close()
    return pack


def test_full_snapshot_persists_disabled_motion_envelope(tmp_path):
    """Removing the server snapshot field would drop a completed disabled run."""
    route = _route()
    snapshot = ForecastSnapshot(
        route=route,
        target_date="2026-09-05",
        fetch_date="2026-09-05",
        days_out=0,
        departure_time=datetime(2026, 9, 5, 9, tzinfo=timezone.utc),
        observed_motion=_disabled_motion(route),
    )

    path = save_snapshot(snapshot, data_dir=tmp_path)

    stored = json.loads(path.read_text())
    assert stored["observed_motion"]["status"] == "disabled"
    assert stored["observed_motion"]["revision"] == 1


def test_snapshot_and_bundle_advertise_live_motion_capability(
    motion_client, tmp_path, monkeypatch,
):
    """Dropping no-store/current-capability headers would authorize stale motion."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "1")
    route = _route()
    client, sessions = motion_client
    sample_pack = _stored_pack(sessions, route)
    pack_dir = pack_dir_for(
        DEV_USER_ID, sample_pack.flight_id, sample_pack.fetch_timestamp,
    )
    pack_dir.mkdir(parents=True)
    motion = _disabled_motion(route).model_dump(mode="json")
    (pack_dir / "briefing.json").write_text(json.dumps({
        "route": route.model_dump(mode="json"),
        "target_date": "2026-09-05",
        "fetch_date": "2026-09-05",
        "days_out": 0,
        "departure_time": "2026-09-05T09:00:00Z",
        "observed_motion": motion,
    }))

    timestamp = sample_pack.fetch_timestamp.isoformat()
    snapshot = client.get(
        f"/api/flights/{sample_pack.flight_id}/packs/{timestamp}/snapshot",
    )
    bundle = client.get(
        f"/api/flights/{sample_pack.flight_id}/packs/{timestamp}/bundle",
    )

    for response in (snapshot, bundle):
        assert response.headers["X-Observed-Motion-Enabled"] == "1"
        assert response.headers["Cache-Control"] == "no-store"
    assert snapshot.json()["observed_motion"]["revision"] == 1
    assert bundle.json()["snapshot"]["observed_motion"]["revision"] == 1


def test_direct_observations_refresh_returns_published_disabled_motion(
    motion_client, tmp_path, monkeypatch,
):
    """Removing the real motion stage would leave direct refresh without its replacement."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    monkeypatch.delenv("WB_OBSERVED_MOTION_ENABLED", raising=False)
    route = _route()
    client, sessions = motion_client
    pack = _stored_pack(sessions, route)
    pack_dir = pack_dir_for(DEV_USER_ID, pack.flight_id, pack.fetch_timestamp)
    pack_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (pack_dir / "briefing.json").write_text(json.dumps({
        "route": route.model_dump(mode="json"),
        "target_date": now.date().isoformat(),
        "fetch_date": now.date().isoformat(),
        "days_out": 0,
        "departure_time": now.isoformat(),
    }))
    (pack_dir / "forecasts.json").write_text('{"forecasts": []}')
    client.app.state.db_path = "/motion-test-nav.db"
    fresh = RouteObservations(
        corridor_nm=30.0,
        fetch_time=now,
        airports_found=0,
        airports_with_metar=0,
        airports_with_taf=0,
    )

    with patch(
        "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh,
    ), patch(
        "weatherbrief.tasks.route_weather.run_route_sigmets", return_value=None,
    ), patch("weatherbrief.airports.get_runway_ends", return_value={}):
        response = client.post(
            f"/api/flights/{pack.flight_id}/packs/"
            f"{pack.fetch_timestamp.isoformat()}/observations/refresh",
        )

    assert response.status_code == 200
    assert response.headers["X-Observed-Motion-Enabled"] == "0"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["observed_motion"]["status"] == "disabled"
    stored = json.loads((pack_dir / "briefing.json").read_text())
    assert stored["observed_motion"] == response.json()["observed_motion"]


def test_direct_refresh_replaces_stored_motion_outside_current_d0(
    motion_client, tmp_path, monkeypatch,
):
    """Using saved days_out alone would run motion for an old pack as if it were D-0."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "1")
    route = _route()
    client, sessions = motion_client
    pack = _stored_pack(sessions, route)
    pack_dir = pack_dir_for(DEV_USER_ID, pack.flight_id, pack.fetch_timestamp)
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.json").write_text(json.dumps({
        "route": route.model_dump(mode="json"),
        "target_date": "2026-09-04",
        "fetch_date": "2026-09-04",
        "days_out": 0,
        "departure_time": "2026-09-04T09:00:00Z",
    }))
    (pack_dir / "forecasts.json").write_text('{"forecasts": []}')
    client.app.state.db_path = "/motion-test-nav.db"
    fresh = RouteObservations(
        corridor_nm=30.0,
        fetch_time=datetime.now(timezone.utc),
        airports_found=0,
        airports_with_metar=0,
        airports_with_taf=0,
    )

    with patch(
        "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh,
    ), patch(
        "weatherbrief.tasks.route_weather.run_route_sigmets", return_value=None,
    ), patch("weatherbrief.airports.get_runway_ends", return_value={}):
        response = client.post(
            f"/api/flights/{pack.flight_id}/packs/"
            f"{pack.fetch_timestamp.isoformat()}/observations/refresh",
        )

    motion = response.json()["observed_motion"]
    assert response.status_code == 200
    assert motion["status"] == "unavailable"
    assert motion["reason_codes"] == ["outside_d0"]


def test_reused_full_artifact_writer_keeps_current_motion(tmp_path):
    """Changing the shared full writer to direct JSON output would erase a newer run."""
    route = _route()
    snapshot = ForecastSnapshot(
        route=route,
        target_date="2026-09-05",
        fetch_date="2026-09-05",
        days_out=0,
        departure_time=datetime(2026, 9, 5, 9, tzinfo=timezone.utc),
        observed_motion=_disabled_motion(route),
    )
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    token = reserve_motion_revision(pack_dir, allow_create=True)
    publish_motion_snapshot(
        pack_dir,
        token,
        snapshot.observed_motion,
        refreshed_fields={},
        initial_snapshot=snapshot.model_dump(mode="json"),
    )

    save_analysis_artifacts(
        pack_dir,
        snapshot.model_copy(update={"observed_motion": None}),
        route_analyses_manifest=None,
    )

    stored = json.loads((pack_dir / "briefing.json").read_text())
    assert stored["observed_motion"]["revision"] == token.revision


def test_newer_disabled_realtime_publication_fences_delayed_older_result(
    tmp_path, monkeypatch,
):
    """Returning the local older envelope after a race would regress the client."""
    from weatherbrief.tasks import route_weather
    from weatherbrief.tasks.route_weather import run_realtime_refresh
    import weatherbrief.observed.motion.payload as motion_payload

    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    monkeypatch.delenv("WB_OBSERVED_MOTION_ENABLED", raising=False)
    route = _route()
    now = datetime.now(timezone.utc)
    (tmp_path / "briefing.json").write_text(json.dumps({
        "route": route.model_dump(mode="json"),
        "target_date": now.date().isoformat(),
        "fetch_date": now.date().isoformat(),
        "days_out": 0,
        "departure_time": now.isoformat(),
    }))
    (tmp_path / "forecasts.json").write_text('{"forecasts": []}')
    observation_calls = 0
    observation_lock = threading.Lock()

    def distinct_observations(**_kwargs):
        nonlocal observation_calls
        with observation_lock:
            observation_calls += 1
            airports_found = observation_calls
        return RouteObservations(
            corridor_nm=30.0, fetch_time=now, airports_found=airports_found,
            airports_with_metar=0, airports_with_taf=0,
        )

    monkeypatch.setattr(route_weather, "run_route_weather", distinct_observations)
    monkeypatch.setattr(route_weather, "run_route_sigmets", lambda **_kwargs: None)

    actual_build = motion_payload.build_observed_motion
    first_started = threading.Event()
    release_first = threading.Event()
    invocation = 0
    invocation_lock = threading.Lock()

    def delayed_first_build(*args, **kwargs):
        nonlocal invocation
        with invocation_lock:
            invocation += 1
            is_first = invocation == 1
        if is_first:
            first_started.set()
            assert release_first.wait(timeout=5)
        return actual_build(*args, **kwargs)

    monkeypatch.setattr(motion_payload, "build_observed_motion", delayed_first_build)
    old_result: list[object] = []

    def older_refresh():
        old_result.append(run_realtime_refresh(tmp_path, "/fake/db"))

    older = threading.Thread(target=older_refresh)
    older.start()
    assert first_started.wait(timeout=5)
    newer = run_realtime_refresh(tmp_path, "/fake/db")
    release_first.set()
    older.join(timeout=5)
    assert not older.is_alive()

    older_motion = old_result[0].observed_motion
    assert older_motion is not None
    assert newer.observed_motion is not None
    assert older_motion.revision == newer.observed_motion.revision
    assert older_motion.revision == 2
    assert old_result[0].observations.airports_found == 2
    assert newer.observations.airports_found == 2
    assert json.loads((tmp_path / "briefing.json").read_text())["observed_motion"]["revision"] == 2


def test_gated_refresh_and_sse_return_the_motion_transport(
    motion_client, tmp_path, monkeypatch,
):
    """Dropping the gated/SSE sibling would leave clients with divergent refresh state."""
    from weatherbrief.api.packs import DataStatus, RefreshDecision

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    monkeypatch.delenv("WB_OBSERVED_MOTION_ENABLED", raising=False)
    route = _route()
    client, sessions = motion_client
    pack = _stored_pack(sessions, route)
    client.app.state.db_path = "/motion-test-nav.db"
    decision = RefreshDecision(
        mode="realtime", reason="D-0 motion refresh", needed=1,
        n_eligible=1, n_updated=0, days_out=0,
    )
    status = DataStatus(fresh=True, refresh_decision=decision)
    motion = _disabled_motion(route)
    result = RealtimeRefreshResult(
        observations=RouteObservations(
            corridor_nm=30.0, fetch_time=datetime.now(timezone.utc),
            airports_found=0, airports_with_metar=0, airports_with_taf=0,
        ),
        observed_motion=motion,
    )
    latest = BriefingPackMeta(
        flight_id=pack.flight_id,
        fetch_timestamp=pack.fetch_timestamp,
        days_out=0,
        artifact_path=str(tmp_path / "pack"),
    )

    with patch("weatherbrief.api.packs.list_packs", return_value=[latest]), patch(
        "weatherbrief.api.packs.gated_data_status", return_value=status,
    ), patch("weatherbrief.api.packs.run_realtime_refresh", return_value=result), patch(
        "weatherbrief.api.packs.SessionLocal", side_effect=sessions,
    ):
        accepted = client.post(f"/api/flights/{pack.flight_id}/packs/refresh")
        stream = client.post(f"/api/flights/{pack.flight_id}/packs/refresh/stream")

    assert accepted.headers["X-Observed-Motion-Enabled"] == "0"
    assert accepted.headers["Cache-Control"] == "no-store"
    assert accepted.json()["observed_motion"]["revision"] == 1
    assert stream.headers["X-Observed-Motion-Enabled"] == "0"
    assert stream.headers["Cache-Control"] == "no-store"
    event = json.loads([line[6:] for line in stream.text.splitlines() if line.startswith("data: ")][-1])
    assert event["observed_motion"]["revision"] == 1


def test_gated_refresh_surfaces_motion_publication_errors(
    motion_client, tmp_path, monkeypatch,
):
    """A lifecycle failure must not be presented as a successful gated no-op."""
    from weatherbrief.api.packs import DataStatus, RefreshDecision

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    monkeypatch.delenv("WB_OBSERVED_MOTION_ENABLED", raising=False)
    route = _route()
    client, sessions = motion_client
    pack = _stored_pack(sessions, route)
    client.app.state.db_path = "/motion-test-nav.db"
    decision = RefreshDecision(
        mode="realtime", reason="D-0 motion refresh", needed=1,
        n_eligible=1, n_updated=0, days_out=0,
    )
    status = DataStatus(fresh=True, refresh_decision=decision)
    latest = BriefingPackMeta(
        flight_id=pack.flight_id,
        fetch_timestamp=pack.fetch_timestamp,
        days_out=0,
        artifact_path=str(tmp_path / "pack"),
    )
    failure = MotionPublicationError("Pack generation was deleted")

    with patch("weatherbrief.api.packs.list_packs", return_value=[latest]), patch(
        "weatherbrief.api.packs.gated_data_status", return_value=status,
    ), patch(
        "weatherbrief.api.packs.run_realtime_refresh", side_effect=failure,
    ), patch("weatherbrief.api.packs.SessionLocal", side_effect=sessions):
        accepted = client.post(f"/api/flights/{pack.flight_id}/packs/refresh")
        stream = client.post(f"/api/flights/{pack.flight_id}/packs/refresh/stream")

    assert accepted.status_code == 409
    assert "publication failed" in accepted.json()["detail"].lower()
    assert accepted.headers["X-Observed-Motion-Enabled"] == "0"
    assert accepted.headers["Cache-Control"] == "no-store"
    assert stream.status_code == 200
    assert stream.headers["X-Observed-Motion-Enabled"] == "0"
    assert stream.headers["Cache-Control"] == "no-store"
    assert "event: error" in stream.text
    assert "event: complete" not in stream.text
    event = json.loads([line[6:] for line in stream.text.splitlines() if line.startswith("data: ")][-1])
    assert event["type"] == "error"
    assert event["status"] == 409
    assert "publication failed" in event["detail"].lower()
