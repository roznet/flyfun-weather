"""Tests for weatherbrief.frontal.contour_fronts — 2-D TFP=0 gating."""

from __future__ import annotations

import numpy as np
import pytest

from weatherbrief.frontal.contour_fronts import extract_front_lines
from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.sources import HewsonGrids


def _front_grids(amp: float = 6.0, width: float = 0.3, u_kmh: float = 30.0):
    """A meridional tanh θe front at lon=2.5 → a N-S TFP=0 axis."""
    lat = np.linspace(45.0, 49.0, 17)
    lon = np.linspace(0.0, 5.0, 21)
    _, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    theta = 290.0 + amp * np.tanh((lon_grid - 2.5) / width)
    u = np.full_like(theta, u_kmh)
    v = np.zeros_like(theta)
    d = compute_hewson_diagnostics(theta, lat, lon, u, v)
    grids = HewsonGrids(
        theta_e=theta, gradient=d["gradient"], tfp=d["tfp"],
        neg_laplacian=d["neg_laplacian"], advection=d["advection"],
        tendency=np.zeros_like(theta), dT_dx=d["dT_dx"], dT_dy=d["dT_dy"],
    )
    return grids, lat, lon


class TestExtractFrontLines:
    def test_detects_meridional_front(self):
        grids, lat, lon = _front_grids()
        lines = extract_front_lines(grids, lat, lon, FrontGateConfig(), min_length_km=200.0)
        assert len(lines) == 1
        ln = lines[0]
        assert ln.kind == "cold"          # eastward wind into colder air east
        assert ln.length_km > 300.0
        # The axis sits on the front longitude.
        assert np.mean([p[1] for p in ln.points]) == pytest.approx(2.5, abs=0.2)

    def test_min_length_drops_short_axes(self):
        grids, lat, lon = _front_grids()
        # An absurd minimum length removes the (≈445 km) axis.
        lines = extract_front_lines(grids, lat, lon, FrontGateConfig(), min_length_km=10_000.0)
        assert lines == []

    def test_strict_gate_can_reject(self):
        # Weak front: amplitude 1.5 K → small gradient & Δθe, strict rejects.
        grids, lat, lon = _front_grids(amp=1.5, width=0.6, u_kmh=5.0)
        strict = FrontGateConfig(gradient_min=8.0, delta_theta_e_min=7.0)
        assert extract_front_lines(grids, lat, lon, strict, min_length_km=200.0) == []

    def test_no_front_no_lines(self):
        lat = np.linspace(45.0, 49.0, 17)
        lon = np.linspace(0.0, 5.0, 21)
        lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")
        theta = 290.0 + 0.01 * lat_grid  # nearly uniform, no front
        d = compute_hewson_diagnostics(
            theta, lat, lon, np.zeros_like(theta), np.zeros_like(theta),
        )
        grids = HewsonGrids(
            theta_e=theta, gradient=d["gradient"], tfp=d["tfp"],
            neg_laplacian=d["neg_laplacian"], advection=d["advection"],
            tendency=np.zeros_like(theta), dT_dx=d["dT_dx"], dT_dy=d["dT_dy"],
        )
        assert extract_front_lines(grids, lat, lon, FrontGateConfig()) == []
