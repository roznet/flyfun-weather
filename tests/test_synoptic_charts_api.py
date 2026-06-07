"""API tests for the flight-independent /api/synoptic-charts/* endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.fetch import dwd_charts, metoffice_charts

_PNG = b"\x89PNG\r\n\x1a\n" + b"y" * 64
_GIF = b"GIF89a" + b"z" * 64
_DWD_CYCLE = "2026-05-08T06Z"
_MO_CYCLE = "2026-05-29T00Z"


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
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(app_db, data_dir: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-api-tests")
    # Default: Met Office not public (admin-gated).
    monkeypatch.delenv("METOFFICE_CHARTS_PUBLIC", raising=False)

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


def _write(module, data_dir: Path, run_cycle: str, chart_id: str, body: bytes) -> None:
    cdir = module.cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    ext = "png" if module is dwd_charts else "gif"
    (cdir / f"{chart_id}.{ext}").write_bytes(body)
    # Minimal meta so the manifest can surface an issuance time for ana.
    meta_path = cdir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta[chart_id] = {"last_modified": datetime.now(timezone.utc).isoformat()}
    meta_path.write_text(json.dumps(meta))


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def test_manifest_lists_dwd_only_for_non_admin(client, data_dir):
    _write(dwd_charts, data_dir, _DWD_CYCLE, "ana", _PNG)
    _write(dwd_charts, data_dir, _DWD_CYCLE, "048", _PNG)
    _write(metoffice_charts, data_dir, _MO_CYCLE, "ana", _GIF)

    resp = client.get("/api/synoptic-charts/manifest")
    assert resp.status_code == 200
    slugs = [s["slug"] for s in resp.json()["sources"]]
    assert slugs == ["dwd"]  # Met Office hidden for non-admin

    dwd = resp.json()["sources"][0]
    assert dwd["run_cycle"] == _DWD_CYCLE
    ids = [c["id"] for c in dwd["charts"]]
    assert ids == ["ana", "048"]
    ana = next(c for c in dwd["charts"] if c["id"] == "ana")
    assert ana["chart_type"] == "analysis"
    assert ana["native_size"] == [4389, 3114]
    assert ana["valid_time"] == "2026-05-08T06:00:00Z"


def test_manifest_includes_metoffice_when_public(client, data_dir, monkeypatch):
    monkeypatch.setenv("METOFFICE_CHARTS_PUBLIC", "1")
    _write(dwd_charts, data_dir, _DWD_CYCLE, "ana", _PNG)
    _write(metoffice_charts, data_dir, _MO_CYCLE, "ana", _GIF)
    _write(metoffice_charts, data_dir, _MO_CYCLE, "024", _GIF)

    resp = client.get("/api/synoptic-charts/manifest")
    assert resp.status_code == 200
    slugs = {s["slug"] for s in resp.json()["sources"]}
    assert slugs == {"dwd", "metoffice"}
    mo = next(s for s in resp.json()["sources"] if s["slug"] == "metoffice")
    assert mo["run_cycle"] == _MO_CYCLE
    assert [c["id"] for c in mo["charts"]] == ["ana", "024"]
    assert all(c["chart_type"] == "colour" for c in mo["charts"])


def test_manifest_empty_when_nothing_cached(client):
    resp = client.get("/api/synoptic-charts/manifest")
    assert resp.status_code == 200
    assert resp.json() == {"sources": []}


# ---------------------------------------------------------------------------
# serve bytes
# ---------------------------------------------------------------------------


def test_serve_dwd_png(client, data_dir):
    _write(dwd_charts, data_dir, _DWD_CYCLE, "ana", _PNG)
    resp = client.get(f"/api/synoptic-charts/dwd/{_DWD_CYCLE}/ana")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == _PNG
    assert "immutable" in resp.headers["cache-control"]


def test_serve_410_when_missing(client):
    resp = client.get(f"/api/synoptic-charts/dwd/{_DWD_CYCLE}/048")
    assert resp.status_code == 410


def test_serve_400_bad_chart_id(client):
    resp = client.get(f"/api/synoptic-charts/dwd/{_DWD_CYCLE}/999")
    assert resp.status_code == 400


def test_serve_400_bad_run_cycle(client):
    resp = client.get("/api/synoptic-charts/dwd/not-a-cycle/ana")
    assert resp.status_code == 400


def test_serve_404_unknown_source(client):
    resp = client.get(f"/api/synoptic-charts/bogus/{_DWD_CYCLE}/ana")
    assert resp.status_code == 404


def test_serve_metoffice_404_for_non_admin(client, data_dir):
    _write(metoffice_charts, data_dir, _MO_CYCLE, "ana", _GIF)
    resp = client.get(f"/api/synoptic-charts/metoffice/{_MO_CYCLE}/ana")
    assert resp.status_code == 404  # gated sources are 404, not 403, to non-admins


def test_serve_metoffice_when_public(client, data_dir, monkeypatch):
    monkeypatch.setenv("METOFFICE_CHARTS_PUBLIC", "1")
    _write(metoffice_charts, data_dir, _MO_CYCLE, "ana", _GIF)
    resp = client.get(f"/api/synoptic-charts/metoffice/{_MO_CYCLE}/ana")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/gif"
    assert resp.content == _GIF
