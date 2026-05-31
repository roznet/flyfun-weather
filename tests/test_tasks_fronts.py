"""Tests for the front-detection pipeline stage (weatherbrief.tasks.fronts)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.models import RoutePointAnalysis
from weatherbrief.tasks.artifacts import load_route_fronts
from weatherbrief.tasks.fronts import (
    compute_route_fronts,
    nearest_cruise_level,
    run_fronts,
)


_INIT_DT = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
_INIT_UNIX = int(_INIT_DT.timestamp())
_LEVELS = (925, 850, 700)
_STRIDE = 3
_N_TIME = 9  # 0..24 h


def _write_front_snapshot(out_dir: Path, model: str = "ecmwf") -> Path:
    """A snapshot with a meridional θe front (cold front, eastward flow)."""
    from weatherbrief.frontal.detect import compute_hewson_diagnostics
    from weatherbrief.hewson.precompute import (
        snapshot_path,
        tendency_k_per_hour,
        write_snapshot,
    )

    lat = np.linspace(45.0, 52.0, 29)
    lon = np.linspace(-2.0, 6.0, 33)
    _, lon_grid = np.meshgrid(lat, lon, indexing="ij")

    per_level: dict[int, dict] = {}
    for L in _LEVELS:
        m = {
            k: np.full((_N_TIME, lat.size, lon.size), np.nan, dtype=np.float32)
            for k in ("theta_e", "gradient", "neg_laplacian", "tfp", "advection")
        }
        for h in range(_N_TIME):
            axis = 2.0 + 0.03 * h * _STRIDE
            theta = 290.0 + 6.0 * np.tanh((lon_grid - axis) / 0.3)
            u = np.full_like(theta, 30.0)
            v = np.zeros_like(theta)
            d = compute_hewson_diagnostics(theta, lat, lon, u, v)
            m["theta_e"][h] = theta
            m["gradient"][h] = d["gradient"]
            m["neg_laplacian"][h] = d["neg_laplacian"]
            m["tfp"][h] = d["tfp"]
            m["advection"][h] = d["advection"]
        m["tendency"] = tendency_k_per_hour(m["theta_e"], step_hours=_STRIDE)
        per_level[L] = m

    valid_times = np.array(
        [np.datetime64(_INIT_DT.replace(tzinfo=None) + timedelta(hours=h * _STRIDE))
         for h in range(_N_TIME)],
        dtype="datetime64[ns]",
    )
    path = snapshot_path(model, _INIT_UNIX, output_dir=out_dir)
    write_snapshot(
        path, init_time_unix=_INIT_UNIX, valid_times=valid_times,
        lat=lat, lon=lon, levels=list(_LEVELS), stride_hours=_STRIDE,
        per_level=per_level,
    )
    return path


def _route_analyses():
    """West→east route across the front axis, departing at init + 6 h."""
    dep = _INIT_DT + timedelta(hours=6)
    pts = [(47.0, -1.5), (47.0, 1.5), (47.0, 4.5)]
    out = []
    for i, (la, lo) in enumerate(pts):
        out.append(RoutePointAnalysis(
            point_index=i, lat=la, lon=lo,
            distance_from_origin_nm=float(i * 80),
            interpolated_time=dep + timedelta(minutes=40 * i),
            forecast_hour=6 + i, track_deg=90.0,
        ))
    return out


class TestNearestCruiseLevel:
    def test_picks_closest_altitude(self):
        assert nearest_cruise_level(2500, [925, 850, 700]) == 925
        assert nearest_cruise_level(5500, [925, 850, 700]) == 850
        assert nearest_cruise_level(11000, [925, 850, 700]) == 700


class TestComputeRouteFronts:
    def test_detects_front_all_levels(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        analyses = _route_analyses()
        wps = [(a.lat, a.lon) for a in analyses]
        etas = [a.interpolated_time for a in analyses]

        manifest = compute_route_fronts(
            wps, etas, route_name="EGKB-LFAT", cruise_altitude_ft=5000,
            advisory_models=["ecmwf", "gfs"], output_dir=out_dir,
        )
        assert manifest.models == ["ecmwf"]            # only ecmwf has a snapshot
        assert manifest.models_without_snapshot == ["gfs"]
        assert manifest.primary_level_hPa == 850       # cruise 5000 ft
        assert manifest.levels == [700, 850, 925]
        assert "ecmwf" in manifest.snapshot_inits

        analyses_850 = [
            a for a in manifest.per_model["ecmwf"] if a.level_hPa == 850
        ]
        assert len(analyses_850) == 1
        a = analyses_850[0]
        assert len(a.crossings) >= 1
        assert a.crossings[0].kind == "cold"
        assert a.decisions  # candidate/decision trace stamped in

    def test_gate_config_stamped(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        analyses = _route_analyses()
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=2500,
            advisory_models=["ecmwf"], output_dir=out_dir,
        )
        assert manifest.gate_config["name"] == "default"
        assert manifest.gate_config["level_hPa"] == 925  # cruise 2500 → primary 925

    def test_no_models_when_no_snapshots(self, tmp_path):
        manifest = compute_route_fronts(
            [(47.0, -1.5), (47.0, 4.5)],
            [_INIT_DT + timedelta(hours=6), _INIT_DT + timedelta(hours=7)],
            route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], output_dir=tmp_path / "empty",
        )
        assert manifest.models == []
        assert manifest.models_without_snapshot == ["ecmwf"]
        assert manifest.per_model == {}


class TestRunFronts:
    def test_writes_artifact_and_roundtrips(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()

        manifest = run_fronts(
            _route_analyses(), route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], pack_dir=pack_dir, output_dir=out_dir,
        )
        assert manifest is not None
        assert (pack_dir / "route_fronts.json").exists()
        loaded = load_route_fronts(pack_dir)
        assert loaded is not None
        assert loaded.models == ["ecmwf"]
        assert loaded.per_model["ecmwf"]

    def test_skips_route_without_etas(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        # Single point → fewer than 2 ETA-stamped waypoints.
        one = _route_analyses()[:1]
        assert run_fronts(
            one, route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], pack_dir=pack_dir, output_dir=out_dir,
        ) is None
        assert not (pack_dir / "route_fronts.json").exists()
