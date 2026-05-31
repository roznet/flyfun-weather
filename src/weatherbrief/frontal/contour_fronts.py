"""2-D front-line extraction — the continental sibling of the route locator.

``detect_front_crossings`` answers "does a front cross *my track*?" by walking a
1-D route and finding gated TFP zero-crossings. This module generalises the same
gate philosophy to the whole grid: extract the **TFP = 0 contours** (the Hewson
front axes) from a snapshot's ``tfp`` field, gate each contour vertex by the
*same* :class:`~weatherbrief.frontal.gates.FrontGateConfig`
(``gradient_min`` + cross-contour ``delta_theta_e_min`` + optional anomaly),
classify cold/warm by advection sign, and drop axes shorter than a minimum
length. Output: a set of coloured front polylines for one
``(model, init, level, hour, gate config)``.

This is the primary continental-scale **calibration surface** — drawing
gate-detected front lines on top of the official DWD / Met Office analysis
(issue #195 §C2) gives a direct visual POD/FAR read of "which gate reproduces
the drawn fronts?". It composes with the synoptic Hewson map overlay.

Contours come from ``contourpy`` (matplotlib's engine, already a dependency) —
no new package, and the same routine matplotlib uses for ``contour()``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from contourpy import contour_generator

from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.route_sampling import bilinear_sample, haversine_km
from weatherbrief.frontal.sources import HewsonGrids


@dataclass(frozen=True)
class FrontVertex:
    """One gated point on a TFP=0 contour."""
    lat: float
    lon: float
    gradient: float        # |∇θe|  K/100km
    delta_theta_e: float   # signed θe jump (warm − cold) across ±window, K
    advection: float       # −V·∇θe  K/h


@dataclass(frozen=True)
class FrontPolyline:
    """A contiguous run of gated front-axis vertices, with a single label.

    ``kind`` is the majority advection class along the run; ``length_km`` is the
    great-circle path length. Mean gradient / Δθe summarise the run's intensity.
    """
    kind: str              # "cold" | "warm" | "quasi-stationary"
    vertices: list[FrontVertex]
    length_km: float
    mean_gradient: float
    mean_delta_theta_e: float

    @property
    def points(self) -> list[tuple[float, float]]:
        """``[(lat, lon), ...]`` — the drawable polyline."""
        return [(v.lat, v.lon) for v in self.vertices]


def _index_to_coord(idx: float, axis: np.ndarray) -> float:
    """Map a fractional grid index along an ascending axis to its coordinate."""
    n = len(axis)
    if idx <= 0:
        return float(axis[0])
    if idx >= n - 1:
        return float(axis[-1])
    lo = int(np.floor(idx))
    frac = idx - lo
    return float(axis[lo] + frac * (axis[lo + 1] - axis[lo]))


def _gate_vertex(
    lat: float,
    lon: float,
    grids: HewsonGrids,
    lat_axis: np.ndarray,
    lon_axis: np.ndarray,
    config: FrontGateConfig,
    background: np.ndarray | None,
    terrain_mask: np.ndarray | None,
) -> FrontVertex | None:
    """Evaluate the gate config at one contour vertex; ``None`` if it fails.

    Mirrors :func:`extract_gated_fronts`' per-cell test: magnitude gate, optional
    gradient-anomaly gate (rejects persistent orographic / sea-land gradients),
    and the cross-axis θe-jump gate measured ±``airmass_window_km`` along the
    unit gradient.
    """
    grad = bilinear_sample(grids.gradient, lat_axis, lon_axis, lat, lon)
    if not np.isfinite(grad) or grad < config.gradient_min:
        return None

    if config.use_anomaly_filter and background is not None:
        bg = bilinear_sample(background, lat_axis, lon_axis, lat, lon)
        if np.isfinite(bg) and (grad - bg) < config.anomaly_min:
            return None

    if terrain_mask is not None:
        # Reject vertices sitting on high terrain (orographic θe, not synoptic).
        valid = bilinear_sample(
            terrain_mask.astype(np.float64), lat_axis, lon_axis, lat, lon,
        )
        if np.isfinite(valid) and valid < 0.5:
            return None

    dtdx = bilinear_sample(grids.dT_dx, lat_axis, lon_axis, lat, lon)
    dtdy = bilinear_sample(grids.dT_dy, lat_axis, lon_axis, lat, lon)
    gmag = float(np.hypot(dtdx, dtdy))
    if not np.isfinite(gmag) or gmag < 1e-9:
        return None
    ux, uy = dtdx / gmag, dtdy / gmag  # unit gradient, cold→warm

    dlat = (uy * config.airmass_window_km) / 111.0
    dlon = (ux * config.airmass_window_km) / (
        111.0 * float(np.cos(np.radians(lat)))
    )
    warm = bilinear_sample(grids.theta_e, lat_axis, lon_axis, lat + dlat, lon + dlon)
    cold = bilinear_sample(grids.theta_e, lat_axis, lon_axis, lat - dlat, lon - dlon)
    dthe = warm - cold
    if not np.isfinite(dthe) or abs(dthe) < config.delta_theta_e_min:
        return None

    adv = bilinear_sample(grids.advection, lat_axis, lon_axis, lat, lon)
    return FrontVertex(
        lat=lat, lon=lon, gradient=float(grad),
        delta_theta_e=float(dthe), advection=float(adv) if np.isfinite(adv) else 0.0,
    )


def _polyline_length_km(vertices: list[FrontVertex]) -> float:
    return sum(
        haversine_km(a.lat, a.lon, b.lat, b.lon)
        for a, b in zip(vertices[:-1], vertices[1:])
    )


def _classify_run(vertices: list[FrontVertex], advection_min: float) -> str:
    """Majority advection class along a gated run (warm / cold / stationary)."""
    warm = sum(1 for v in vertices if v.advection > advection_min)
    cold = sum(1 for v in vertices if v.advection < -advection_min)
    if warm == 0 and cold == 0:
        return "quasi-stationary"
    return "warm" if warm >= cold else "cold"


def extract_front_lines(
    grids: HewsonGrids,
    lat_axis: np.ndarray,
    lon_axis: np.ndarray,
    config: FrontGateConfig,
    *,
    min_length_km: float = 200.0,
    background: np.ndarray | None = None,
    terrain_mask: np.ndarray | None = None,
) -> list[FrontPolyline]:
    """Gated TFP=0 front polylines for one snapshot hour.

    Steps: (1) ``contourpy`` extracts the TFP=0 contours, (2) each vertex is
    mapped to lat/lon and gated by ``config``, (3) contiguous runs of surviving
    vertices become polylines, (4) runs shorter than ``min_length_km`` are
    dropped. ``background`` (a time-mean |∇θe| grid) enables the anomaly filter
    when ``config.use_anomaly_filter`` is set.
    """
    tfp = grids.tfp
    if tfp is None or not np.isfinite(tfp).any():
        return []

    # contourpy works in grid-index space; x = column (lon), y = row (lat).
    ny, nx = tfp.shape
    z = np.where(np.isfinite(tfp), tfp, np.nan)
    gen = contour_generator(
        x=np.arange(nx), y=np.arange(ny), z=z,
        # NaN handling: corner_mask keeps cells with NaN corners out of the
        # contour, so terrain holes / edges don't spawn spurious axes.
        corner_mask=True,
    )

    polylines: list[FrontPolyline] = []
    for line in gen.lines(0.0):
        # ``line`` is an (n, 2) array of (x=col, y=row) fractional indices.
        run: list[FrontVertex] = []
        for col, row in line:
            lat = _index_to_coord(float(row), lat_axis)
            lon = _index_to_coord(float(col), lon_axis)
            vtx = _gate_vertex(
                lat, lon, grids, lat_axis, lon_axis, config,
                background, terrain_mask,
            )
            if vtx is not None:
                run.append(vtx)
            elif run:
                # Gap in the gated axis — close the current run.
                polylines.extend(
                    _finish_run(run, config, min_length_km)
                )
                run = []
        polylines.extend(_finish_run(run, config, min_length_km))

    return polylines


def _finish_run(
    run: list[FrontVertex],
    config: FrontGateConfig,
    min_length_km: float,
) -> list[FrontPolyline]:
    if len(run) < 2:
        return []
    length = _polyline_length_km(run)
    if length < min_length_km:
        return []
    return [
        FrontPolyline(
            kind=_classify_run(run, config.advection_min),
            vertices=run,
            length_km=length,
            mean_gradient=float(np.mean([v.gradient for v in run])),
            mean_delta_theta_e=float(np.mean([v.delta_theta_e for v in run])),
        )
    ]
