"""Tests for weatherbrief.frontal.sources — the swappable field sources.

Covers:
  * CaseFieldSource recomputes the same diagnostics the legacy path did.
  * SnapshotFieldSource reads a written precompute NPZ.
  * The two sources agree (the abstraction is sound): a snapshot built from a
    case yields the same grids the case recomputes.
  * End-to-end front detection over a snapshot with an embedded θe front
    (a self-contained regression for the production read path).
"""

from __future__ import annotations

import numpy as np
import pytest

from weatherbrief.frontal.case import Case
from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.route_sampling import (
    analyze_route_fronts,
    grids_at_fractional_hour,
)
from weatherbrief.frontal.sources import CaseFieldSource, SnapshotFieldSource
from weatherbrief.hewson.precompute import (
    tendency_k_per_hour,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Synthetic front: a tanh θe step in longitude (a meridional front).
# ---------------------------------------------------------------------------

LON0 = 2.5      # front axis longitude
AMP = 6.0       # ± amplitude → 12 K total air-mass contrast
WIDTH = 0.3     # deg, front sharpness


def _theta_e(lat_grid, lon_grid, hour: int) -> np.ndarray:
    # Front drifts slowly east with time so tendency is non-trivial.
    axis = LON0 + 0.05 * hour
    return 290.0 + AMP * np.tanh((lon_grid - axis) / WIDTH)


def _build_field_stacks(n_hours: int = 5):
    lat = np.linspace(45.0, 49.0, 17)   # 0.25°
    lon = np.linspace(0.0, 5.0, 21)
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    nlat, nlon = lat.size, lon.size

    theta = np.empty((n_hours, nlat, nlon), dtype=np.float64)
    u = np.full((n_hours, nlat, nlon), 30.0)   # km/h eastward → cold advection
    v = np.zeros((n_hours, nlat, nlon))
    for h in range(n_hours):
        theta[h] = _theta_e(lat_grid, lon_grid, h)
    return lat, lon, theta, u, v


def _make_case(lat, lon, theta, u, v) -> Case:
    n_hours = theta.shape[0]
    fields_by_hour = {
        h: {
            "T850": theta[h] - 40.0,
            "Td850": theta[h] - 50.0,
            "theta_e": theta[h],
            "u850": u[h],
            "v850": v[h],
        }
        for h in range(n_hours)
    }
    valid_times = np.array(
        [np.datetime64("2026-05-31T00", "ns") + np.timedelta64(h, "h")
         for h in range(n_hours)],
        dtype="datetime64[ns]",
    )
    case = Case(
        case_dir=None, case_name="front", source="synthetic",
        resolution_deg=0.25, lat=lat, lon=lon, models=["syn"],
        valid_times={"syn": valid_times}, init_times={"syn": 0},
    )
    case.fields = lambda model, hour, level_hPa=None, _s=fields_by_hour: _s.get(hour)  # type: ignore
    case.available_hours = lambda model, _n=n_hours: list(range(_n))  # type: ignore
    return case


def _write_snapshot_from_case(path, lat, lon, theta, u, v, *, stride: int = 1):
    """Mimic the precompute write (no Open-Meteo fetch) at one level (850)."""
    n_hours = theta.shape[0]
    metrics = {
        k: np.full((n_hours, lat.size, lon.size), np.nan, dtype=np.float32)
        for k in ("theta_e", "gradient", "neg_laplacian", "tfp", "advection")
    }
    for h in range(n_hours):
        diag = compute_hewson_diagnostics(theta[h], lat, lon, u[h], v[h])
        metrics["theta_e"][h] = theta[h]
        metrics["gradient"][h] = diag["gradient"]
        metrics["neg_laplacian"][h] = diag["neg_laplacian"]
        metrics["tfp"][h] = diag["tfp"]
        metrics["advection"][h] = diag["advection"]
    metrics["tendency"] = tendency_k_per_hour(metrics["theta_e"], step_hours=stride)

    valid_times = np.array(
        [np.datetime64("2026-05-31T00", "ns") + np.timedelta64(h * stride, "h")
         for h in range(n_hours)],
        dtype="datetime64[ns]",
    )
    write_snapshot(
        path, init_time_unix=int(np.datetime64("2026-05-31T00").astype("datetime64[s]").astype(int)),
        valid_times=valid_times, lat=lat, lon=lon, levels=[850],
        stride_hours=stride, per_level={850: metrics},
    )


class TestCaseFieldSource:
    def test_grids_match_compute_hewson(self):
        lat, lon, theta, u, v = _build_field_stacks()
        case = _make_case(lat, lon, theta, u, v)
        src = CaseFieldSource(case)
        grids = src.grids_at_hour("syn", 1, 850)
        diag = compute_hewson_diagnostics(theta[1], lat, lon, u[1], v[1])
        np.testing.assert_allclose(grids.gradient, diag["gradient"])
        np.testing.assert_allclose(grids.tfp, diag["tfp"])
        np.testing.assert_allclose(grids.advection, diag["advection"])

    def test_available_hours_and_levels(self):
        lat, lon, theta, u, v = _build_field_stacks()
        src = CaseFieldSource(_make_case(lat, lon, theta, u, v))
        assert src.available_hours("syn") == [0, 1, 2, 3, 4]
        assert src.models == ["syn"]


class TestFractionalHour:
    def test_interpolates_between_integer_hours(self):
        lat, lon, theta, u, v = _build_field_stacks()
        src = CaseFieldSource(_make_case(lat, lon, theta, u, v))
        g0 = src.grids_at_hour("syn", 1, 850)
        g1 = src.grids_at_hour("syn", 2, 850)
        gm = grids_at_fractional_hour(src, "syn", 1.5, level_hPa=850)
        assert gm is not None
        np.testing.assert_allclose(gm.theta_e, 0.5 * (g0.theta_e + g1.theta_e))

    def test_out_of_range_returns_none(self):
        lat, lon, theta, u, v = _build_field_stacks()
        src = CaseFieldSource(_make_case(lat, lon, theta, u, v))
        assert grids_at_fractional_hour(src, "syn", 99.0, level_hPa=850) is None


class TestSnapshotFieldSource:
    def test_reads_written_snapshot(self, tmp_path):
        lat, lon, theta, u, v = _build_field_stacks()
        snap = tmp_path / "snap.npz"
        _write_snapshot_from_case(snap, lat, lon, theta, u, v)
        src = SnapshotFieldSource(snap, model_name="ecmwf")
        assert src.models == ["ecmwf"]
        assert src.available_levels("ecmwf") == [850]
        assert src.available_hours("ecmwf") == [0, 1, 2, 3, 4]
        grids = src.grids_at_hour("ecmwf", 2, 850)
        assert grids is not None
        assert grids.theta_e.shape == (lat.size, lon.size)

    def test_wrong_model_raises(self, tmp_path):
        lat, lon, theta, u, v = _build_field_stacks()
        snap = tmp_path / "snap.npz"
        _write_snapshot_from_case(snap, lat, lon, theta, u, v)
        src = SnapshotFieldSource(snap, model_name="ecmwf")
        with pytest.raises(ValueError, match="snapshot source holds"):
            src.grids_at_hour("gfs", 0, 850)

    def test_out_of_range_hour_returns_none(self, tmp_path):
        lat, lon, theta, u, v = _build_field_stacks()
        snap = tmp_path / "snap.npz"
        _write_snapshot_from_case(snap, lat, lon, theta, u, v)
        src = SnapshotFieldSource(snap, model_name="ecmwf")
        assert src.grids_at_hour("ecmwf", 99, 850) is None


class TestSourceEquivalence:
    """The whole point of the abstraction: snapshot ≡ case on the same fields."""

    def test_grids_agree(self, tmp_path):
        lat, lon, theta, u, v = _build_field_stacks()
        case = _make_case(lat, lon, theta, u, v)
        snap = tmp_path / "snap.npz"
        _write_snapshot_from_case(snap, lat, lon, theta, u, v, stride=1)

        case_src = CaseFieldSource(case)
        snap_src = SnapshotFieldSource(snap, model_name="ecmwf")

        for h in (1, 2, 3):  # interior hours (centred tendency on both sides)
            cg = case_src.grids_at_hour("syn", h, 850)
            sg = snap_src.grids_at_hour("ecmwf", h, 850)
            # float32 storage in the NPZ → loosen tolerance vs float64 recompute
            for fld in ("theta_e", "gradient", "tfp", "neg_laplacian",
                        "advection", "tendency", "dT_dx", "dT_dy"):
                np.testing.assert_allclose(
                    getattr(cg, fld), getattr(sg, fld), atol=2e-3, rtol=1e-3,
                    err_msg=f"hour={h} field={fld}",
                )

    def test_analysis_agrees(self, tmp_path):
        """analyze_route_fronts gives the same crossing via either source."""
        lat, lon, theta, u, v = _build_field_stacks()
        case = _make_case(lat, lon, theta, u, v)
        snap = tmp_path / "snap.npz"
        _write_snapshot_from_case(snap, lat, lon, theta, u, v, stride=1)

        # West→east route crossing the front axis at lon=2.5, 47°N.
        wps = [(47.0, 0.5), (47.0, 4.5)]
        cfg = FrontGateConfig(level_hPa=850)

        res_case = analyze_route_fronts(case, "syn", wps, 2.0, config=cfg)
        res_snap = analyze_route_fronts(
            SnapshotFieldSource(snap, model_name="ecmwf"), "ecmwf", wps, 2.0, config=cfg,
        )
        assert len(res_case.crossings) == 1
        assert len(res_snap.crossings) == 1
        c, s = res_case.crossings[0], res_snap.crossings[0]
        assert c.kind == s.kind == "cold"   # eastward wind into colder air (−AMP east)
        assert c.distance_km == pytest.approx(s.distance_km, abs=20.0)


class TestSnapshotFrontRegression:
    """A front embedded in a snapshot is detected through the production path."""

    def test_detects_embedded_cold_front(self, tmp_path):
        lat, lon, theta, u, v = _build_field_stacks()
        snap = tmp_path / "snap.npz"
        _write_snapshot_from_case(snap, lat, lon, theta, u, v)
        src = SnapshotFieldSource(snap, model_name="ecmwf")

        wps = [(47.0, 0.5), (47.0, 4.5)]
        res = analyze_route_fronts(src, "ecmwf", wps, 2.0, config=FrontGateConfig())

        assert res.level_hPa == 850
        assert res.config.name == "default"
        assert len(res.crossings) == 1
        xc = res.crossings[0]
        assert xc.kind == "cold"
        assert xc.gradient >= 6.0           # cleared the magnitude gate
        assert abs(xc.delta_theta_e) >= 5.0  # cleared the air-mass-jump gate
        # The full candidate/decision trace is stamped in.
        assert res.decisions
        assert any(d.accepted for d in res.decisions)
