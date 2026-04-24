"""Tests for Hewson snapshot precompute.

Unit coverage for the pure helpers (path resolution, tendency math, purge)
plus one end-to-end `run_once` run with a fake Open-Meteo client, verifying
the NPZ schema defined in designs/future/hewson-fields-aviation-advisories.md
§ 6.1 is what actually lands on disk.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from weatherbrief.hewson.precompute import (
    DEFAULT_LEVELS,
    _init_to_iso_z,
    _tendency_k_per_hour,
    load_snapshot,
    purge_old_snapshots,
    resolve_output_dir,
    run_once,
    snapshot_path,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_resolve_output_dir_explicit(tmp_path):
    assert resolve_output_dir(tmp_path / "foo") == tmp_path / "foo"


def test_resolve_output_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert resolve_output_dir() == tmp_path / "hewson"


def test_resolve_output_dir_fallback(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert resolve_output_dir() == Path("data") / "hewson"


def test_init_to_iso_z_format():
    # 2026-04-24 12:00:00 UTC
    dt = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    unix = int(dt.timestamp())
    assert _init_to_iso_z(unix) == "2026-04-24T12:00:00Z"


def test_snapshot_path_structure(tmp_path):
    unix = int(datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    p = snapshot_path("ecmwf", unix, output_dir=tmp_path)
    assert p == tmp_path / "ecmwf" / "2026-04-24T00:00:00Z.npz"


# ---------------------------------------------------------------------------
# Tendency
# ---------------------------------------------------------------------------


def test_tendency_centered_difference():
    # θe ramping linearly at 2 K/h → tendency = 2 K/h everywhere (including
    # edges via one-sided diff, since the slope is constant).
    stack = np.stack([np.full((3, 3), 10.0 + 2.0 * h) for h in range(5)]).astype(
        np.float32
    )
    tend = _tendency_k_per_hour(stack)
    assert tend.shape == stack.shape
    assert np.allclose(tend, 2.0)


def test_tendency_edges_one_sided():
    # θe piecewise: 0, 0, 10, 20, 30 — tendency at hour 0 is 0, hour 1 is
    # (10-0)/2 = 5, hour 4 edge is 30-20 = 10.
    vals = [0.0, 0.0, 10.0, 20.0, 30.0]
    stack = np.stack([np.full((2, 2), v) for v in vals]).astype(np.float32)
    tend = _tendency_k_per_hour(stack)
    assert tend[0, 0, 0] == pytest.approx(0.0)
    assert tend[1, 0, 0] == pytest.approx(5.0)
    assert tend[-1, 0, 0] == pytest.approx(10.0)


def test_tendency_single_hour_is_nan():
    stack = np.full((1, 2, 2), 5.0, dtype=np.float32)
    tend = _tendency_k_per_hour(stack)
    assert np.all(np.isnan(tend))


def test_tendency_scales_with_step_hours():
    # θe ramping at 6 K/h, sampled every 3 h → stack values [0, 18, 36, 54].
    # With step_hours=3 the computed tendency must still be 6 K/h.
    stack = np.stack(
        [np.full((2, 2), 6.0 * t) for t in (0, 3, 6, 9)]
    ).astype(np.float32)
    tend = _tendency_k_per_hour(stack, step_hours=3)
    assert np.allclose(tend, 6.0)


def test_tendency_rejects_nonpositive_step():
    stack = np.zeros((3, 2, 2), dtype=np.float32)
    with pytest.raises(ValueError):
        _tendency_k_per_hour(stack, step_hours=0)


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def _touch_snapshot(root: Path, model: str, dt: datetime) -> Path:
    """Create an empty NPZ-named file for retention tests."""
    iso_z = dt.isoformat().replace("+00:00", "Z")
    path = root / model / f"{iso_z}.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_purge_removes_old_keeps_fresh(tmp_path):
    now = datetime(2026, 4, 24, 18, 0, 0, tzinfo=timezone.utc)
    root = tmp_path / "hewson"
    # Fresh: within 48 h cutoff
    fresh = _touch_snapshot(root, "ecmwf", now - timedelta(hours=12))
    # Stale: outside 48 h cutoff
    stale = _touch_snapshot(root, "ecmwf", now - timedelta(hours=72))
    # Another model, fresh
    fresh2 = _touch_snapshot(root, "gfs", now - timedelta(hours=6))

    removed = purge_old_snapshots(
        output_dir=root, retention_hours=48, now=now,
    )
    assert removed == 1
    assert fresh.exists()
    assert fresh2.exists()
    assert not stale.exists()


def test_purge_scoped_to_model(tmp_path):
    now = datetime(2026, 4, 24, 18, 0, 0, tzinfo=timezone.utc)
    root = tmp_path / "hewson"
    old_ecmwf = _touch_snapshot(root, "ecmwf", now - timedelta(hours=72))
    old_gfs = _touch_snapshot(root, "gfs", now - timedelta(hours=72))

    removed = purge_old_snapshots(
        model="ecmwf", output_dir=root, retention_hours=48, now=now,
    )
    assert removed == 1
    assert not old_ecmwf.exists()
    assert old_gfs.exists()  # gfs scope untouched


def test_purge_skips_unrecognised_filename(tmp_path, caplog):
    now = datetime(2026, 4, 24, 18, 0, 0, tzinfo=timezone.utc)
    root = tmp_path / "hewson"
    (root / "ecmwf").mkdir(parents=True)
    bogus = root / "ecmwf" / "garbage.npz"
    bogus.write_bytes(b"")

    removed = purge_old_snapshots(
        output_dir=root, retention_hours=48, now=now,
    )
    assert removed == 0
    assert bogus.exists()  # left in place


def test_purge_no_root(tmp_path):
    assert purge_old_snapshots(output_dir=tmp_path / "missing") == 0


# ---------------------------------------------------------------------------
# End-to-end run_once with a fake client
# ---------------------------------------------------------------------------


class _FakeOpenMeteoClient:
    """Minimal stand-in for OpenMeteoClient returning synthetic grid data.

    Returns a list of per-point ``{"hourly": {...}}`` dicts, one per
    (latitude, longitude) pair in the request params. The values are a
    smooth function of (lat, lon, hour) so the downstream gradient math
    produces a non-trivial but reproducible result.
    """

    def __init__(self, n_hours: int = 3):
        self.n_hours = n_hours
        self.calls = 0

    def get_json(self, url: str, params: dict) -> list[dict]:
        self.calls += 1
        lats = [float(x) for x in params["latitude"].split(",")]
        lons = [float(x) for x in params["longitude"].split(",")]
        # Each level has 4 variables
        variables = [v.strip() for v in params["hourly"].split(",")]

        # Fake time axis (ISO local strings — the client doesn't care about tz)
        time_axis = [
            f"2026-04-24T{h:02d}:00" for h in range(self.n_hours)
        ]

        out: list[dict] = []
        for la, lo in zip(lats, lons):
            hourly: dict[str, list] = {"time": time_axis}
            for var in variables:
                if var.startswith("temperature_"):
                    # smooth N-S gradient: colder north
                    level = int(var.split("_")[1].replace("hPa", ""))
                    base = 15.0 if level == 925 else (10.0 if level == 850 else 0.0)
                    hourly[var] = [base - (la - 45.0) * 0.2 for _ in range(self.n_hours)]
                elif var.startswith("dewpoint_"):
                    hourly[var] = [2.0 for _ in range(self.n_hours)]
                elif var.startswith("wind_speed_"):
                    hourly[var] = [20.0 for _ in range(self.n_hours)]
                elif var.startswith("wind_direction_"):
                    hourly[var] = [270.0 for _ in range(self.n_hours)]
                else:
                    hourly[var] = [0.0 for _ in range(self.n_hours)]
            out.append({"hourly": hourly})
        return out


def _fake_model_metadata(models):
    """Fake fetch_model_metadata return value — init at 2026-04-24 00:00Z."""

    class _Meta:
        last_init_time = int(
            datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        )

    return {m: _Meta() for m in models}


@pytest.fixture
def patched_metadata():
    """Patch fetch_model_metadata both in hewson and frontal call sites."""
    with patch(
        "weatherbrief.hewson.precompute.fetch_model_metadata",
        side_effect=_fake_model_metadata,
        create=True,
    ):
        with patch(
            "weatherbrief.fetch.model_status.fetch_model_metadata",
            side_effect=_fake_model_metadata,
        ):
            yield


@pytest.fixture
def patched_terrain_mask():
    """Replace build_terrain_mask with all-True to avoid SRTM lookups."""
    def _all_true(lat, lon):
        return np.ones((len(lat), len(lon)), dtype=bool)

    with patch(
        "weatherbrief.hewson.precompute.build_terrain_mask",
        side_effect=_all_true,
    ):
        yield


def test_run_once_writes_snapshot_with_expected_schema(
    tmp_path, patched_metadata, patched_terrain_mask,
):
    client = _FakeOpenMeteoClient(n_hours=4)
    result = run_once(
        models=["gfs"],  # single model keeps fetch under 10 s
        output_dir=tmp_path,
        forecast_days=1,
        stride_hours=1,  # keep every hour so all 4 timestamps land in NPZ
        skip_existing=False,
        client=client,
    )

    assert "gfs" in result.snapshots
    path = result.snapshots["gfs"]
    assert path is not None
    assert path.exists()
    assert path.name == "2026-04-24T00:00:00Z.npz"

    snap = load_snapshot(path)
    # Per-level metric keys (§ 6.1)
    for L in DEFAULT_LEVELS:
        for metric in (
            "theta_e", "gradient", "neg_laplacian",
            "tfp", "advection", "tendency",
        ):
            key = f"{metric}_{L}"
            assert key in snap, f"missing key {key}"
            # 4 hours × 101 lat × 193 lon per § 6.1 grid
            assert snap[key].shape == (4, 101, 193), (
                f"{key} shape {snap[key].shape} — expected (4, 101, 193)"
            )
    # Coordinate arrays and metadata
    assert snap["lat"].shape == (101,)
    assert snap["lon"].shape == (193,)
    assert snap["valid_times"].shape == (4,)
    assert snap["levels"].tolist() == sorted(DEFAULT_LEVELS)
    assert int(snap["stride_hours"]) == 1
    assert int(snap["init_time_unix"]) == int(
        datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    )


def test_run_once_stride_hours_decimates(
    tmp_path, patched_metadata, patched_terrain_mask,
):
    # 7 hourly inputs × stride=3 → keep indices 0, 3, 6 → 3 timesteps.
    client = _FakeOpenMeteoClient(n_hours=7)
    result = run_once(
        models=["gfs"],
        output_dir=tmp_path,
        forecast_days=1,
        stride_hours=3,
        skip_existing=False,
        client=client,
    )
    snap = load_snapshot(result.snapshots["gfs"])
    assert int(snap["stride_hours"]) == 3
    # Every metric stack should have 3 timesteps now
    for L in DEFAULT_LEVELS:
        assert snap[f"theta_e_{L}"].shape == (3, 101, 193)
    assert snap["valid_times"].shape == (3,)


def test_run_once_dry_run_writes_nothing(
    tmp_path, patched_metadata, patched_terrain_mask,
):
    client = _FakeOpenMeteoClient(n_hours=2)
    result = run_once(
        models=["gfs"],
        output_dir=tmp_path,
        forecast_days=1,
        dry_run=True,
        skip_existing=False,
        client=client,
    )
    assert result.snapshots["gfs"] is None
    # No NPZ on disk, but terrain_mask cache may be created
    assert not any(
        p for p in tmp_path.rglob("*.npz") if p.name != "terrain_mask.npz"
    )


def test_run_once_skips_when_snapshot_exists(
    tmp_path, patched_metadata, patched_terrain_mask,
):
    # Pre-create the expected snapshot so the skip path fires
    expected_init = int(
        datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    )
    existing = snapshot_path("gfs", expected_init, output_dir=tmp_path)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"")

    client = _FakeOpenMeteoClient(n_hours=2)
    result = run_once(
        models=["gfs"],
        output_dir=tmp_path,
        forecast_days=1,
        client=client,
    )
    assert result.skipped.get("gfs") == "fresh"
    assert client.calls == 0, "fetch should be short-circuited when fresh"
