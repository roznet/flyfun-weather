"""Tests for /api/observed — status, overlay imagery, lightning points (#574).

Imagery is served here rather than embedded in ``briefing.json``: a corridor
of 2 km composite is hundreds of kilobytes, and every pack load would pay for
a layer most of them never draw.  These tests pin that separation, the
enable-gate, and the fact that an overlay carries its own age.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    FrameStore,
)

FIXTURES = Path(__file__).parent / "observed" / "data"


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
def observed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-observed")
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    for flag in (
        "DISABLE_SCHEDULER", "DISABLE_RETENTION", "DISABLE_VERIFICATION",
        "DISABLE_DIGEST", "DISABLE_STANDALONE_VERIFICATION",
        "DISABLE_ECMWF_WATCHER", "DISABLE_HEWSON_PRECOMPUTE",
        "DISABLE_METAR_INGEST", "DISABLE_FORECAST_FETCH",
        "DISABLE_FRESHNESS_LOOP", "DISABLE_ANALYTICS_ROLLUP",
    ):
        monkeypatch.setenv(flag, "1")
    return tmp_path / "data"


@pytest.fixture
def stocked(observed_env):
    """A frame store under DATA_DIR holding one recent frame per source.

    Sidecars are built the way the collector builds them — by reading the
    frame that was just written — so the attribution under test is the one
    the payload actually carries, not a literal invented here.
    """
    from weatherbrief.observed import ctth, lightning, opera

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    store = FrameStore(observed_env / "observed")
    for source, filename, describe in (
        (SOURCE_OPERA_DBZH, "opera_dbzh.h5", lambda p: opera.read_metadata(p, "DBZH")),
        (SOURCE_EUMETSAT_CTTH, "ctth.nc", ctth.read_metadata),
        (SOURCE_EUMETSAT_LI, "li_flashes.nc", lightning.read_metadata),
    ):
        path = store.write_payload(source, now, (FIXTURES / filename).read_bytes())
        store.write_sidecar(source, now, describe(path))
    return store


def _build_app(app_db):
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
    return app


@pytest.fixture
def client(app_db, observed_env):
    app = _build_app(app_db)
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_anon(app_db, observed_env):
    return TestClient(_build_app(app_db), raise_server_exceptions=False)


BBOX = {"south": 49.8, "west": 0.4, "north": 51.2, "east": 2.9}


# --- Gating ----------------------------------------------------------------


def test_endpoints_require_authentication(client_anon):
    assert client_anon.get("/api/observed/status").status_code in (401, 403)


def test_endpoints_are_absent_unless_the_collector_is_enabled(
    app_db, observed_env, monkeypatch
):
    """A deployment without the collector must not advertise the feature."""
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    app = _build_app(app_db)
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/observed/status").status_code == 404


# --- Status ----------------------------------------------------------------


def test_status_lists_every_source_with_its_own_age(client, stocked):
    payload = client.get("/api/observed/status").json()
    by_source = {s["source"]: s for s in payload["sources"]}
    assert set(by_source) == {
        "opera_dbzh", "opera_rate", "eumetsat_li", "eumetsat_ctth",
    }
    assert by_source["opera_dbzh"]["available"] is True
    assert by_source["opera_dbzh"]["age_minutes"] < 5
    # No payload-level "as of": the four streams do not share an instant.
    assert "as_of" not in payload
    # A source with nothing collected says so rather than being omitted.
    assert by_source["opera_rate"]["available"] is False


def test_status_reports_the_rolling_window_separately_from_the_age(client, stocked):
    """A 10-minute rolling maximum is not a snapshot, and says so."""
    payload = client.get("/api/observed/status").json()
    dbzh = next(s for s in payload["sources"] if s["source"] == "opera_dbzh")
    assert dbzh["window_minutes"] == 10.0
    assert dbzh["interval_minutes"] == 5.0


def test_status_carries_attribution_read_from_the_frame(client, stocked):
    payload = client.get("/api/observed/status").json()
    dbzh = next(s for s in payload["sources"] if s["source"] == "opera_dbzh")
    assert "MeteoFrance" in dbzh["attribution"]["producer"]
    assert dbzh["attribution"]["text"]


def test_status_ships_the_legend_so_the_client_cannot_drift(client, stocked):
    payload = client.get("/api/observed/status").json()
    dbzh = next(s for s in payload["sources"] if s["source"] == "opera_dbzh")
    assert dbzh["legend"]
    lightning = next(s for s in payload["sources"] if s["source"] == "eumetsat_li")
    # Lightning is points, not a raster — nothing to ramp.
    assert lightning["legend"] == []
    assert lightning["renders_imagery"] is False


# --- Overlay ---------------------------------------------------------------


def test_overlay_returns_a_png_with_its_own_valid_time(client, stocked):
    response = client.get("/api/observed/overlay/opera_dbzh.png", params=BBOX)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    # The badge on the map is fed by the same response that carries the image,
    # so the two cannot disagree.
    assert response.headers["X-Observed-Valid-Time"]
    assert response.headers["X-Observed-Attribution"]


def test_overlay_rejects_an_unknown_source(client, stocked):
    assert client.get("/api/observed/overlay/eumetsat_li.png", params=BBOX).status_code == 404
    assert client.get("/api/observed/overlay/nope.png", params=BBOX).status_code == 404


def test_overlay_rejects_an_empty_or_oversized_box(client, stocked):
    empty = dict(BBOX, north=BBOX["south"])
    assert client.get("/api/observed/overlay/opera_dbzh.png", params=empty).status_code == 400
    huge = {"south": 0.0, "west": 0.0, "north": 60.0, "east": 60.0}
    assert client.get("/api/observed/overlay/opera_dbzh.png", params=huge).status_code == 400


def test_overlay_says_410_when_nothing_current_is_held(client, observed_env):
    """Configured but empty is a different answer from "no such source"."""
    response = client.get("/api/observed/overlay/opera_dbzh.png", params=BBOX)
    assert response.status_code == 410


def test_a_stale_frame_is_not_served_as_current(client, observed_env):
    store = FrameStore(observed_env / "observed")
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    store.write(
        SOURCE_OPERA_DBZH, old.replace(second=0, microsecond=0),
        (FIXTURES / "opera_dbzh.h5").read_bytes(), {},
    )
    assert client.get(
        "/api/observed/overlay/opera_dbzh.png", params=BBOX
    ).status_code == 410


def test_cloud_top_overlay_renders(client, stocked):
    response = client.get("/api/observed/overlay/eumetsat_ctth.png", params=BBOX)
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


# --- Flashes ---------------------------------------------------------------


def test_flashes_come_back_as_points_with_individual_times(client, stocked):
    payload = client.get("/api/observed/flashes", params=BBOX).json()
    assert payload["count"] > 0
    first = payload["flashes"][0]
    assert {"lat", "lon", "time"} <= set(first)
    # Per-flash times are what lets the map fade by age instead of drawing a
    # ten-minute accumulation as one instant.
    times = {f["time"] for f in payload["flashes"]}
    assert len(times) > 1
    assert payload["window_minutes"] == 10.0


def test_flashes_outside_the_box_are_excluded(client, stocked):
    elsewhere = {"south": 40.0, "west": -8.0, "north": 42.0, "east": -6.0}
    payload = client.get("/api/observed/flashes", params=elsewhere).json()
    assert payload["count"] == 0
    # Absence of flashes is an observation, so the request still succeeds.
    assert payload["attribution"] or payload["newest_valid_time"]
