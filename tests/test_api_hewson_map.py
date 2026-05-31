"""Tests for /api/hewson-map endpoints (Phase D.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.hewson.precompute import snapshot_path


# ---------------------------------------------------------------------------
# Synthetic snapshot fixture (mirrors weatherbrief.hewson.precompute schema)
# ---------------------------------------------------------------------------


_INIT_DT = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
_INIT_UNIX = int(_INIT_DT.timestamp())
_INIT_ISO = "2026-04-24T12:00:00Z"
_LEVELS = (925, 850, 700)
_STRIDE_HOURS = 3
_N_TIME = 4  # forecast hours 0, 3, 6, 9
_LAT = np.linspace(40.0, 60.0, 5)
_LON = np.linspace(-10.0, 20.0, 7)
_METRICS = ("theta_e", "gradient", "neg_laplacian", "tfp", "advection", "tendency")


def _write_synthetic_snapshot(out_dir: Path, model: str = "ecmwf") -> Path:
    """Write a small but schema-correct NPZ a test can read."""
    n_time = _N_TIME
    n_lat = _LAT.size
    n_lon = _LON.size
    init_naive = _INIT_DT.replace(tzinfo=None)
    valid_times = np.array(
        [
            np.datetime64(init_naive + timedelta(hours=h * _STRIDE_HOURS))
            for h in range(n_time)
        ],
        dtype="datetime64[ns]",
    )
    # Each cell carries a known value: metric-tag * 100 + level offset + h*10 + lat_idx + lon_idx*0.01
    data: dict = {
        "valid_times": valid_times,
        "lat": _LAT.astype(np.float64),
        "lon": _LON.astype(np.float64),
        "init_time_unix": np.array(_INIT_UNIX, dtype=np.int64),
        "levels": np.array(_LEVELS, dtype=np.int32),
        "stride_hours": np.array(_STRIDE_HOURS, dtype=np.int32),
    }
    metric_tag = {m: i for i, m in enumerate(_METRICS)}
    level_offset = {925: 0, 850: 1, 700: 2}
    for metric in _METRICS:
        for L in _LEVELS:
            arr = np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32)
            for h in range(n_time):
                for i in range(n_lat):
                    for j in range(n_lon):
                        arr[h, i, j] = (
                            metric_tag[metric] * 100
                            + level_offset[L]
                            + h * 10
                            + i
                            + j * 0.01
                        )
            # Plant a NaN in (0,0) of one variant so we can verify NaN→null.
            if metric == "tendency" and L == 850:
                arr[0, 0, 0] = np.nan
            data[f"{metric}_{L}"] = arr

    path = snapshot_path(model, _INIT_UNIX, output_dir=out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **data)
    return path


# ---------------------------------------------------------------------------
# Test client (mirrors tests/test_api.py pattern)
# ---------------------------------------------------------------------------


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
def hewson_env(tmp_path, monkeypatch):
    """Common env-var setup shared by client/client_anon. Adding a new
    DISABLE_* flag should only require touching this one place."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-hewson-map")
    for flag in (
        "DISABLE_SCHEDULER", "DISABLE_RETENTION", "DISABLE_VERIFICATION",
        "DISABLE_DIGEST", "DISABLE_STANDALONE_VERIFICATION",
        "DISABLE_ECMWF_WATCHER", "DISABLE_HEWSON_PRECOMPUTE",
    ):
        monkeypatch.setenv(flag, "1")


def _build_test_app(app_db):
    """Construct an app + dep overrides for the DB; auth override is
    applied by the caller (only ``client`` wants it)."""
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
def client(app_db, hewson_env):
    """Authenticated client. The synoptic endpoints require an authenticated
    user (no admin gate — visibility is controlled client-side by the
    per-user "Synoptic Forecast Map" preference)."""
    app = _build_test_app(app_db)
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_anon(app_db, hewson_env):
    """Same as ``client`` but without an auth override — for 401 checks."""
    app = _build_test_app(app_db)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def hewson_data_dir(tmp_path):
    """Where ``DATA_DIR`` points; precompute snapshots land at ``{DATA_DIR}/hewson``."""
    return tmp_path / "data"


# ---------------------------------------------------------------------------
# Slice endpoint
# ---------------------------------------------------------------------------


def test_get_slice_happy_path(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")

    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf",
            "init": _INIT_ISO,
            "level": 850,
            "metric": "advection",
            "hour": 6,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["model"] == "ecmwf"
    assert body["init_time"] == _INIT_ISO
    assert body["level"] == 850
    assert body["metric"] == "advection"
    assert body["hour"] == 6
    assert body["stride_hours"] == _STRIDE_HOURS
    assert body["lat"] == _LAT.tolist()
    assert body["lon"] == _LON.tolist()

    values = body["values"]
    assert len(values) == _LAT.size
    assert len(values[0]) == _LON.size

    # advection tag = 4, level 850 offset = 1, hour 6 → idx 2 → h_factor 20
    # Expected (0,0): 4*100 + 1 + 20 + 0 + 0 = 421
    assert values[0][0] == pytest.approx(421.0, abs=1e-3)
    # (lat_idx=2, lon_idx=3): 421 + 2 + 0.03 = 423.03
    assert values[2][3] == pytest.approx(423.03, abs=1e-3)

    # Cache-Control header
    assert "max-age=" in resp.headers.get("cache-control", "")
    assert "immutable" in resp.headers.get("cache-control", "")


def test_get_slice_nan_becomes_null(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")

    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "tendency", "hour": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # The fixture plants NaN at [0][0] for tendency_850 hour=0
    assert body["values"][0][0] is None
    # Other cells are still finite numbers
    assert body["values"][0][1] is not None


def test_get_slice_valid_time_matches_init_plus_offset(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")

    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 700, "metric": "gradient", "hour": 9,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # init = 2026-04-24T12:00:00Z + 9h = 2026-04-24T21:00:00Z
    assert body["valid_time"] == "2026-04-24T21:00:00Z"


def test_get_slice_404_when_snapshot_missing(client):
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 404
    assert "no snapshot" in resp.json()["detail"].lower()


def test_get_slice_404_unknown_model(client):
    """Unknown models return 404 (no snapshot on disk), not 400. The frontend
    discovers valid models via /manifest, which scans subdirs dynamically."""
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "wrf", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 404


def test_get_slice_400_unsafe_model(client):
    """Path-like model names are rejected at the syntactic check."""
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "../etc", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"].lower()


def test_get_slice_400_invalid_level(client):
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 500, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 400
    assert "level" in resp.json()["detail"].lower()


def test_get_slice_400_invalid_metric(client):
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "humidity", "hour": 0,
        },
    )
    assert resp.status_code == 400
    assert "metric" in resp.json()["detail"].lower()


def test_get_slice_400_misaligned_hour(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")
    # stride is 3 h, so hour=4 is invalid
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 4,
        },
    )
    assert resp.status_code == 400
    assert "stride" in resp.json()["detail"].lower()


def test_get_slice_400_hour_past_horizon(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")
    # snapshot has 4 timesteps × 3 h = max hour 9; ask for 12
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 12,
        },
    )
    assert resp.status_code == 400
    assert "horizon" in resp.json()["detail"].lower()


def test_get_slice_400_bad_init_format(client):
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": "yesterday",
            "level": 850, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 400


def test_get_slice_requires_auth(client_anon, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")
    resp = client_anon.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 401


def test_corrupt_snapshot_returns_404(client, hewson_data_dir):
    """A truncated/corrupt NPZ (e.g. from a precompute crash mid-write)
    must surface as 404, not a 500. Manifest must skip it silently."""
    target = snapshot_path("ecmwf", _INIT_UNIX, output_dir=hewson_data_dir / "hewson")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not a valid zip")

    # Slice endpoint
    resp = client.get(
        "/api/hewson-map",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "metric": "advection", "hour": 0,
        },
    )
    assert resp.status_code == 404

    # All-metrics endpoint
    resp = client.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "hour": 0,
        },
    )
    assert resp.status_code == 404

    # Manifest must not 500 — corrupt files are skipped, others (none here)
    # would still be listed. Empty result for ecmwf is fine.
    resp = client.get("/api/hewson-map/manifest")
    assert resp.status_code == 200
    assert "ecmwf" not in resp.json()["models"]


# ---------------------------------------------------------------------------
# All-metrics endpoint (cursor-tooltip backend)
# ---------------------------------------------------------------------------


def test_all_metrics_happy_path(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")
    resp = client.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "hour": 6,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["model"] == "ecmwf"
    assert body["init_time"] == _INIT_ISO
    assert body["level"] == 850
    assert body["hour"] == 6
    assert body["stride_hours"] == _STRIDE_HOURS
    assert body["lat"] == _LAT.tolist()
    assert body["lon"] == _LON.tolist()

    # All 6 metrics present, none missing.
    assert set(body["metrics"].keys()) == set(_METRICS)
    assert body["missing_metrics"] == []

    # Spot-check shape and a known cell. advection tag=4, level 850 offset=1,
    # hour 6 → idx=2 → h_factor=20. Expected (0,0): 4*100 + 1 + 20 + 0 = 421.
    adv = body["metrics"]["advection"]
    assert len(adv) == _LAT.size
    assert len(adv[0]) == _LON.size
    assert adv[0][0] == pytest.approx(421.0, abs=1e-3)

    # NaN cell from the fixture (tendency at 850, hour 0, [0][0]) is null in
    # this hour=6 response slot — at hour=6 the value is finite (the fixture
    # only plants NaN at hour 0).
    assert body["metrics"]["tendency"][0][0] is not None

    # Auth-gated → private cache directive.
    cache = resp.headers.get("cache-control", "")
    assert "private" in cache and "immutable" in cache


def test_all_metrics_partial_when_metric_key_missing(client, hewson_data_dir, tmp_path):
    """A snapshot missing one metric for the level still returns 200; the
    missing metric appears in `missing_metrics` but other metrics render."""
    # Build a normal snapshot and delete one metric stack from it before
    # the endpoint reads it. Easiest: rewrite the NPZ without the dropped key.
    path = _write_synthetic_snapshot(hewson_data_dir / "hewson")
    with np.load(path) as npz:
        keep = {k: npz[k] for k in npz.files if k != "tendency_850"}
    np.savez_compressed(path, **keep)

    resp = client.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "hour": 0,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tendency" not in body["metrics"]
    assert "tendency" in body["missing_metrics"]
    # Other metrics still present.
    assert "gradient" in body["metrics"]
    assert "advection" in body["metrics"]


def test_all_metrics_404_when_snapshot_missing(client):
    resp = client.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "hour": 0,
        },
    )
    assert resp.status_code == 404


def test_all_metrics_400_invalid_level(client):
    resp = client.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 500, "hour": 0,
        },
    )
    assert resp.status_code == 400


def test_all_metrics_requires_auth(client_anon, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson")
    resp = client_anon.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "ecmwf", "init": _INIT_ISO,
            "level": 850, "hour": 0,
        },
    )
    assert resp.status_code == 401


def test_all_metrics_400_unsafe_model(client):
    """Path-like model names are rejected at the syntactic check —
    same guard as the slice endpoint, separately exercised so a
    regression to _validate_common_params is caught on both routes."""
    resp = client.get(
        "/api/hewson-map/all-metrics",
        params={
            "model": "../etc", "init": _INIT_ISO,
            "level": 850, "hour": 0,
        },
    )
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Manifest endpoint
# ---------------------------------------------------------------------------


def test_manifest_lists_snapshots(client, hewson_data_dir):
    _write_synthetic_snapshot(hewson_data_dir / "hewson", model="ecmwf")
    _write_synthetic_snapshot(hewson_data_dir / "hewson", model="gfs")

    resp = client.get("/api/hewson-map/manifest")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["models"].keys()) == {"ecmwf", "gfs"}

    ecmwf = body["models"]["ecmwf"]
    assert len(ecmwf) == 1
    snap = ecmwf[0]
    assert snap["init_time"] == _INIT_ISO
    assert snap["levels"] == list(_LEVELS)
    assert snap["stride_hours"] == _STRIDE_HOURS
    assert snap["n_hours"] == _N_TIME
    assert snap["n_lat"] == _LAT.size
    assert snap["n_lon"] == _LON.size
    assert snap["lat_min"] == pytest.approx(_LAT.min())
    assert snap["lat_max"] == pytest.approx(_LAT.max())
    assert len(snap["valid_times"]) == _N_TIME
    assert snap["valid_times"][0].startswith("2026-04-24T12:00:00")


def test_manifest_empty_when_no_data_dir(client):
    resp = client.get("/api/hewson-map/manifest")
    assert resp.status_code == 200
    assert resp.json() == {"models": {}}


def test_manifest_requires_auth(client_anon):
    resp = client_anon.get("/api/hewson-map/manifest")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Fronts endpoint (2-D TFP=0 gated polylines — §C2)
# ---------------------------------------------------------------------------


def _write_front_snapshot(out_dir: Path, model: str = "ecmwf") -> Path:
    """Write a snapshot whose 850 hPa field holds a real meridional θe front.

    All three levels are populated (the endpoint validates the level is present)
    using the same front so the gate detects an axis at any level.
    """
    from weatherbrief.frontal.detect import compute_hewson_diagnostics
    from weatherbrief.hewson.precompute import tendency_k_per_hour, write_snapshot

    lat = np.linspace(45.0, 52.0, 29)   # 0.25°
    lon = np.linspace(-2.0, 6.0, 33)
    _, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    n_time = _N_TIME

    per_level: dict[int, dict] = {}
    for L in _LEVELS:
        metrics = {
            k: np.full((n_time, lat.size, lon.size), np.nan, dtype=np.float32)
            for k in ("theta_e", "gradient", "neg_laplacian", "tfp", "advection")
        }
        for h in range(n_time):
            axis = 2.0 + 0.05 * h * _STRIDE_HOURS
            theta = 290.0 + 6.0 * np.tanh((lon_grid - axis) / 0.3)
            u = np.full_like(theta, 30.0)   # eastward → cold advection
            v = np.zeros_like(theta)
            d = compute_hewson_diagnostics(theta, lat, lon, u, v)
            metrics["theta_e"][h] = theta
            metrics["gradient"][h] = d["gradient"]
            metrics["neg_laplacian"][h] = d["neg_laplacian"]
            metrics["tfp"][h] = d["tfp"]
            metrics["advection"][h] = d["advection"]
        metrics["tendency"] = tendency_k_per_hour(
            metrics["theta_e"], step_hours=_STRIDE_HOURS,
        )
        per_level[L] = metrics

    path = snapshot_path(model, _INIT_UNIX, output_dir=out_dir)
    write_snapshot(
        path, init_time_unix=_INIT_UNIX,
        valid_times=np.array(
            [np.datetime64(_INIT_DT.replace(tzinfo=None) + timedelta(hours=h * _STRIDE_HOURS))
             for h in range(n_time)],
            dtype="datetime64[ns]",
        ),
        lat=lat, lon=lon, levels=list(_LEVELS),
        stride_hours=_STRIDE_HOURS, per_level=per_level,
    )
    return path


def test_get_fronts_happy_path(client, hewson_data_dir):
    _write_front_snapshot(hewson_data_dir / "hewson")
    resp = client.get(
        "/api/hewson-map/fronts",
        params={"model": "ecmwf", "init": _INIT_ISO, "level": 850,
                "hour": 6, "gate": "default"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gate"] == "default"
    assert body["gate_config"]["level_hPa"] == 850
    assert len(body["fronts"]) >= 1
    front = body["fronts"][0]
    assert front["kind"] == "cold"
    assert front["length_km"] > 300.0
    # GeoJSON [lon, lat] pairs, axis near the front longitude.
    lons = [pt[0] for pt in front["coordinates"]]
    assert min(lons) < 2.5 < max(lons) or abs(sum(lons) / len(lons) - 2.0) < 1.0


def test_get_fronts_unknown_gate_400(client, hewson_data_dir):
    _write_front_snapshot(hewson_data_dir / "hewson")
    resp = client.get(
        "/api/hewson-map/fronts",
        params={"model": "ecmwf", "init": _INIT_ISO, "level": 850,
                "hour": 0, "gate": "bogus"},
    )
    assert resp.status_code == 400
    assert "unknown gate preset" in resp.json()["detail"]


def test_get_fronts_missing_snapshot_404(client):
    resp = client.get(
        "/api/hewson-map/fronts",
        params={"model": "ecmwf", "init": _INIT_ISO, "level": 850, "hour": 0},
    )
    assert resp.status_code == 404


def test_get_fronts_requires_auth(client_anon):
    resp = client_anon.get(
        "/api/hewson-map/fronts",
        params={"model": "ecmwf", "init": _INIT_ISO, "level": 850, "hour": 0},
    )
    assert resp.status_code == 401
