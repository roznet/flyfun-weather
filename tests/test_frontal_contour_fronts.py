"""Tests for weatherbrief.frontal.contour_fronts — 2-D TFP=0 gating."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from contourpy import contour_generator

from weatherbrief.frontal.contour_fronts import (
    _finish_run,
    _gate_vertex,
    _index_to_coord,
    extract_front_lines,
)
from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.frontal.gates import FrontGateConfig, get_preset, preset_names
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


def _weaken_rows(grids: HewsonGrids, rows: slice) -> HewsonGrids:
    """Zero the gate's |∇θe| on some grid rows (0.25° each), leaving TFP alone.

    The TFP=0 contour keeps its shape, but the vertices in that stretch fail
    the gradient gate, as terrain / θe noise does on a real front.
    """
    grad = grids.gradient.copy()
    grad[rows, :] = 0.0
    return dataclasses.replace(grids, gradient=grad)


def _legacy_extract(grids, lat_axis, lon_axis, config, min_length_km):
    """The pre-#1028 loop: a line closes at the first failing point."""
    ny, nx = grids.tfp.shape
    gen = contour_generator(
        x=np.arange(nx), y=np.arange(ny),
        z=np.where(np.isfinite(grids.tfp), grids.tfp, np.nan), corner_mask=True,
    )
    out = []
    for line in gen.lines(0.0):
        run = []
        for col, row in line:
            vtx = _gate_vertex(
                _index_to_coord(float(row), lat_axis),
                _index_to_coord(float(col), lon_axis),
                grids, lat_axis, lon_axis, config, None, None,
            )
            if vtx is not None:
                run.append(vtx)
            elif run:
                out.extend(_finish_run(run, config, min_length_km))
                run = []
        out.extend(_finish_run(run, config, min_length_km))
    return out


class TestGapBridging:
    """``max_gap_km`` (the ``default-gap75`` map preset, #1028)."""

    def test_short_gap_is_bridged(self):
        grids, lat, lon = _front_grids()
        # Rows 8-9 (47.0, 47.25°N): two failing points, ~56 km of contour.
        weak = _weaken_rows(grids, slice(8, 10))
        # Default: two pieces (~167 and ~195 km), both under 200 km, so nothing.
        assert extract_front_lines(weak, lat, lon, get_preset("default"), min_length_km=200.0) == []
        lines = extract_front_lines(weak, lat, lon, get_preset("default-gap75"), min_length_km=200.0)
        assert len(lines) == 1
        ln = lines[0]
        assert ln.length_km > 400.0
        assert ln.kind == "cold"
        # Failing points are never appended: the line jumps the gap.
        assert not any(47.0 - 0.01 < p[0] < 47.25 + 0.01 for p in ln.points)

    def test_long_gap_still_splits(self):
        grids, lat, lon = _front_grids()
        # Rows 8-10: three failing points, ~83 km > 75 km.
        weak = _weaken_rows(grids, slice(8, 11))
        lines = extract_front_lines(weak, lat, lon, get_preset("default-gap75"), min_length_km=0.0)
        assert len(lines) == 2
        assert all(ln.length_km < 200.0 for ln in lines)
        assert extract_front_lines(weak, lat, lon, get_preset("default-gap75"), min_length_km=200.0) == []

    @pytest.mark.parametrize("name", [n for n in preset_names() if get_preset(n).max_gap_km == 0.0])
    @pytest.mark.parametrize("weak_rows", [None, slice(8, 10), slice(5, 7)])
    def test_unbridged_presets_match_legacy(self, name, weak_rows):
        grids, lat, lon = _front_grids()
        if weak_rows is not None:
            grids = _weaken_rows(grids, weak_rows)
        cfg = get_preset(name)
        assert extract_front_lines(grids, lat, lon, cfg, min_length_km=0.0) == \
            _legacy_extract(grids, lat, lon, cfg, 0.0)
