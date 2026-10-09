"""``current_user_id_short``: same auth as ``current_user_id``, no held connection (#719).

flyfun-common's ``current_user_id`` resolves through ``Depends(get_db)``, whose
teardown FastAPI runs only after the response is sent. On the observed tile
endpoints that kept one pooled connection per in-flight request while the tile
waited on EUMETView or rendered, and one map pan exhausted the pool (5 + 10)
and 500'd the whole app for three minutes.

These tests run against a file SQLite engine with SQLAlchemy's default
QueuePool, so ``pool.checkedout()`` counts real checkouts, and with real
cookie auth (production mode), so the user lookup actually touches the DB.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import weatherbrief.api.deps as api_deps
from flyfun_common.auth import COOKIE_NAME, create_token
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import ApiTokenRow, Base, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.api.deps import current_user_id_short
from weatherbrief.observed import satellite_ir, tiles
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH

SECRET = "test-secret-short-auth-0123456789abcdef"
APPROVED = "zz-user-approved"
SUSPENDED = "zz-user-suspended"
REVOKED = "zz-user-revoked"

CYCLE = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
STAMP = CYCLE.strftime("%Y%m%dT%H%M")
SAT_TILE = f"/api/observed/tiles/{satellite_ir.SOURCE_ID}/{STAMP}/5/16/10.png"
RADAR_TILE = f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/{STAMP}/5/16/10.png"
PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def engine(tmp_path):
    import weatherbrief.db.models  # noqa: F401  (registers app tables on Base)

    # Default pool for a file DB is QueuePool: checkouts are counted.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        s.add_all([
            UserRow(id=APPROVED, provider="google", provider_sub="zz-a",
                    email="a@example.invalid", display_name="Pilot A", approved=True),
            UserRow(id=SUSPENDED, provider="google", provider_sub="zz-s",
                    email="s@example.invalid", display_name="Pilot S", approved=False),
            UserRow(id=REVOKED, provider="google", provider_sub="zz-r",
                    email="r@example.invalid", display_name="Pilot R", approved=True,
                    tokens_valid_after=datetime.now(timezone.utc) + timedelta(hours=1)),
        ])
        s.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def app(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_SATELLITE_IR", "1")
    for flag in (
        "DISABLE_SCHEDULER", "DISABLE_RETENTION", "DISABLE_VERIFICATION",
        "DISABLE_DIGEST", "DISABLE_STANDALONE_VERIFICATION",
        "DISABLE_ECMWF_WATCHER", "DISABLE_HEWSON_PRECOMPUTE",
        "DISABLE_METAR_INGEST", "DISABLE_FORECAST_FETCH",
        "DISABLE_FRESHNESS_LOOP", "DISABLE_ANALYTICS_ROLLUP",
    ):
        monkeypatch.setenv(flag, "1")
    app = create_app()
    # The dependency owns its session: point that session at the test engine.
    monkeypatch.setattr(api_deps, "SessionLocal", sessionmaker(bind=engine))
    return app


def _client(app, user_id: str | None = None) -> TestClient:
    client = TestClient(app, raise_server_exceptions=False)
    if user_id is not None:
        client.cookies.set(
            COOKIE_NAME, create_token(user_id, "x@example.invalid", "x", SECRET),
        )
    return client


# --- Auth parity with current_user_id ----------------------------------------


def test_no_credentials_is_401(app):
    assert _client(app).get("/api/observed/status").status_code == 401


def test_invalid_cookie_is_401(app):
    client = _client(app)
    client.cookies.set(COOKIE_NAME, "not-a-jwt")
    assert client.get("/api/observed/status").status_code == 401


def test_unknown_user_is_401(app):
    assert _client(app, "zz-nobody").get("/api/observed/status").status_code == 401


def test_suspended_user_is_403(app):
    assert _client(app, SUSPENDED).get("/api/observed/status").status_code == 403


def test_revoked_session_is_401(app):
    response = _client(app, REVOKED).get("/api/observed/status")
    assert response.status_code == 401
    assert "revoked" in response.json()["detail"].lower()


def test_approved_user_gets_through(app):
    assert _client(app, APPROVED).get("/api/observed/status").status_code == 200


def _add_api_token(engine, raw: str, *, revoked: bool) -> None:
    with sessionmaker(bind=engine)() as s:
        s.add(ApiTokenRow(
            user_id=APPROVED, name="zz-test",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(), revoked=revoked,
        ))
        s.commit()


def test_api_token_use_is_committed(app, engine):
    """``get_db`` committed the token's last_used_at; the owned session must too."""
    _add_api_token(engine, "ff_zz-live", revoked=False)
    response = _client(app).get(
        "/api/observed/status", headers={"Authorization": "Bearer ff_zz-live"},
    )
    assert response.status_code == 200
    with sessionmaker(bind=engine)() as s:
        assert s.query(ApiTokenRow).one().last_used_at is not None


def test_revoked_api_token_is_401(app, engine):
    _add_api_token(engine, "ff_zz-dead", revoked=True)
    response = _client(app).get(
        "/api/observed/status", headers={"Authorization": "Bearer ff_zz-dead"},
    )
    assert response.status_code == 401


def test_session_is_closed_after_auth(app, engine):
    _client(app, APPROVED).get("/api/observed/status")
    assert engine.pool.checkedout() == 0


# --- No connection held while a tile waits -----------------------------------


@pytest.fixture
def slow_satellite(monkeypatch):
    """Satellite tile fetch that blocks until released, recording pool usage."""
    release = threading.Event()
    entered = threading.Semaphore(0)

    def _blocking_fetch(cycle, z, x, y):
        entered.release()
        assert release.wait(timeout=30), "test never released the fetch"
        return PNG

    monkeypatch.setattr(satellite_ir, "available_times", lambda: [CYCLE])
    monkeypatch.setattr(satellite_ir, "fetch_tile", _blocking_fetch)
    return entered, release


def _fire(client: TestClient, path: str, n: int) -> tuple[list[threading.Thread], list[int]]:
    statuses: list[int] = []
    lock = threading.Lock()

    def _get():
        status = client.get(path).status_code
        with lock:
            statuses.append(status)

    threads = [threading.Thread(target=_get) for _ in range(n)]
    for t in threads:
        t.start()
    return threads, statuses


def test_concurrent_slow_satellite_tiles_hold_no_connection(app, engine, slow_satellite):
    """More requests in flight than the pool holds (5 + 10), none checked out."""
    entered, release = slow_satellite
    client = _client(app, APPROVED)
    n = 20
    threads, statuses = _fire(client, SAT_TILE, n)
    try:
        for _ in range(n):
            assert entered.acquire(timeout=30), "tile requests never reached the fetch"
        assert engine.pool.checkedout() == 0
        # A DB-backed request still gets a connection while they all wait.
        assert client.get("/api/observed/status").status_code == 200
    finally:
        release.set()
        for t in threads:
            t.join(timeout=30)
    assert statuses == [200] * n


def test_radar_tile_render_holds_no_connection(app, engine, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    seen: list[int] = []

    def _blocking_render(store, source, valid_time, z, x, y):
        seen.append(engine.pool.checkedout())
        entered.set()
        assert release.wait(timeout=30)
        return PNG

    monkeypatch.setattr(tiles, "tile_png", _blocking_render)
    client = _client(app, APPROVED)
    threads, statuses = _fire(client, RADAR_TILE, 1)
    try:
        assert entered.wait(timeout=30)
        assert engine.pool.checkedout() == 0
    finally:
        release.set()
        threads[0].join(timeout=30)
    assert seen == [0]
    assert statuses == [200]


def test_control_get_db_backed_auth_does_hold_a_connection(app, engine, slow_satellite):
    """The premise of #719 on this FastAPI version: with ``current_user_id``
    (``Depends(get_db)``) the session is still checked out during the fetch.

    If this ever fails, FastAPI changed when generator dependencies tear down;
    the short dependency is then harmless but no longer needed.
    """
    entered, release = slow_satellite
    Session = sessionmaker(bind=engine)

    def _get_db():
        session = Session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[current_user_id_short] = current_user_id
    app.dependency_overrides[get_db] = _get_db
    client = _client(app, APPROVED)
    threads, statuses = _fire(client, SAT_TILE, 1)
    try:
        assert entered.acquire(timeout=30)
        assert engine.pool.checkedout() == 1
    finally:
        release.set()
        threads[0].join(timeout=30)
    assert statuses == [200]
