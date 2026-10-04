"""Projecting a cell forward: straight lines and trajectories through the field (#662).

The raw tile vectors (``motion.FlowField``) are stored in each catalogue
(``flow_to_dict``) and turned into a smooth **motion field** here
(:class:`MotionField`): normalised convolution on the tile lattice fills the
gaps between matched tiles, reaching at most ``field_fill_tiles`` lattice
steps past one — never far beyond what was measured — and bilinear
interpolation gives a vector anywhere.  Where the field is unsupported the
cell's own vector takes over.

Five ways to project a position ``minutes`` ahead (``policy.MOTION_VARIANTS``):

* ``raw`` / ``smoothed`` / ``track`` — straight line at the cell's raw,
  lineage-smoothed or centroid-track velocity (``velocity.py``);
* ``field`` — semi-Lagrangian: follow the field from the point, integrated in
  ``advect_step_minutes`` steps (midpoint rule), the field held constant in
  time as pysteps/STEPS/INCA do;
* ``field_anchored`` — the field supplies only the *spatial variation*: the
  velocity at x is ``field(x) + (anchor − field(cell centroid))`` with the
  anchor the cell's smoothed vector.  At the cell itself it moves at its own
  velocity; along the path it turns where the flow turns.

Every variant needs the cell's raw motion to be ``available`` (the gates in
``runner._finalize_motion``), so they all forecast the same cells.

The field and the template tile are taken at the *earlier* frame of the pair
(the match places the earlier tile in the later frame) — half a pair spacing,
5 min, of position offset that the straight-line raw vector has too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .catalogue import r
from .motion import FlowField
from .policy import CellPolicy

STRAIGHT = ("raw", "smoothed", "track")


def flow_to_dict(flow: FlowField | None, row0: int, col0: int) -> dict | None:
    """The tile vectors for the catalogue (px over the pair, ``None`` where unmatched)."""
    if flow is None:
        return None

    def grid(a: np.ndarray) -> list:
        return [[r(v, 2) for v in row] for row in a.tolist()]

    return {
        "pair_minutes": flow.pair_minutes,
        "tile_px": flow.tile_px,
        "stride_px": flow.stride_px,
        "origin": [int(row0), int(col0)],
        "dr": grid(flow.dr),
        "dc": grid(flow.dc),
    }


@dataclass
class MotionField:
    """Smoothed per-minute velocity on the tile lattice, NaN where unsupported."""

    vr: np.ndarray
    vc: np.ndarray
    tile_px: int
    stride_px: int
    row0: int
    col0: int

    @classmethod
    def from_dict(cls, flow: dict | None, policy: CellPolicy) -> MotionField | None:
        if not flow:
            return None
        dr = np.array([[np.nan if v is None else v for v in row] for row in flow["dr"]], dtype=float)
        dc = np.array([[np.nan if v is None else v for v in row] for row in flow["dc"]], dtype=float)
        if dr.ndim != 2:
            dr = dc = np.zeros((0, 0))
        vr, vc = smooth_lattice(dr, dc, policy)
        minutes = float(flow["pair_minutes"])
        return cls(vr / minutes, vc / minutes, int(flow["tile_px"]), int(flow["stride_px"]),
                   int(flow["origin"][0]), int(flow["origin"][1]))

    def velocity(self, rows, cols) -> tuple[np.ndarray, np.ndarray]:
        """Bilinear per-minute velocity at absolute pixel positions.

        Corners without a value drop out and the rest are renormalised; NaN
        only where all four are missing.  Positions past the outer tile
        centres are clamped to them (at most half a tile of reach).
        """
        rows = np.asarray(rows, dtype=float)
        cols = np.asarray(cols, dtype=float)
        n_i, n_j = self.vr.shape
        if n_i == 0 or n_j == 0:
            nan = np.full(rows.shape, np.nan)
            return nan, nan.copy()
        half = (self.tile_px - 1) / 2.0
        u = np.clip((rows - self.row0 - half) / self.stride_px, 0, n_i - 1)
        v = np.clip((cols - self.col0 - half) / self.stride_px, 0, n_j - 1)
        i0 = np.minimum(np.floor(u).astype(int), max(n_i - 2, 0))
        j0 = np.minimum(np.floor(v).astype(int), max(n_j - 2, 0))
        i1 = np.minimum(i0 + 1, n_i - 1)
        j1 = np.minimum(j0 + 1, n_j - 1)
        fu = u - i0
        fv = v - j0
        out_r = np.zeros(rows.shape)
        out_c = np.zeros(rows.shape)
        weight = np.zeros(rows.shape)
        for ii, jj, w in ((i0, j0, (1 - fu) * (1 - fv)), (i1, j0, fu * (1 - fv)),
                          (i0, j1, (1 - fu) * fv), (i1, j1, fu * fv)):
            a = self.vr[ii, jj]
            b = self.vc[ii, jj]
            ok = np.isfinite(a) & np.isfinite(b) & (w > 0)
            out_r += np.where(ok, w * np.nan_to_num(a), 0.0)
            out_c += np.where(ok, w * np.nan_to_num(b), 0.0)
            weight += np.where(ok, w, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            has = weight > 1e-9
            return (np.where(has, out_r / np.where(has, weight, 1.0), np.nan),
                    np.where(has, out_c / np.where(has, weight, 1.0), np.nan))


def smooth_lattice(dr: np.ndarray, dc: np.ndarray, policy: CellPolicy) -> tuple[np.ndarray, np.ndarray]:
    """Normalised convolution of the matched tiles, limited to their reach."""
    valid = np.isfinite(dr) & np.isfinite(dc)
    if dr.size == 0 or not valid.any():
        nan = np.full(dr.shape, np.nan)
        return nan, nan.copy()
    sigma = policy.field_sigma_tiles
    weight = valid.astype(float)
    if sigma > 0:
        def blur(a):
            return ndimage.gaussian_filter(a, sigma, mode="constant", truncate=2.0)
    else:
        def blur(a):
            return a
    den = blur(weight)
    num_r = blur(np.where(valid, dr, 0.0))
    num_c = blur(np.where(valid, dc, 0.0))
    reach = valid
    if policy.field_fill_tiles > 0:
        reach = ndimage.binary_dilation(valid, structure=np.ones((3, 3), dtype=bool),
                                        iterations=policy.field_fill_tiles)
    ok = reach & (den > 1e-6)
    with np.errstate(invalid="ignore", divide="ignore"):
        safe = np.where(ok, den, 1.0)
        return np.where(ok, num_r / safe, np.nan), np.where(ok, num_c / safe, np.nan)


def cell_vector(motion: dict, variant: str) -> tuple[float, float] | None:
    """The straight-line per-minute vector a variant uses (anchor for the field ones).

    ``None`` unless the raw motion is available.  A catalogue written before
    #662 has no ``smoothed``/``track``: they read as the raw vector.
    """
    if motion.get("status") != "available" or motion.get("drow_per_min") is None \
            or motion.get("dcol_per_min") is None:
        return None
    raw = (float(motion["drow_per_min"]), float(motion["dcol_per_min"]))
    key = "track" if variant == "track" else "smoothed" if variant != "raw" else None
    est = motion.get(key) if key else None
    if est and est.get("drow_per_min") is not None and est.get("dcol_per_min") is not None:
        return float(est["drow_per_min"]), float(est["dcol_per_min"])
    return raw


def displacement(cell: dict, variant: str, minutes: float, rows, cols,
                 field: MotionField | None, policy: CellPolicy) -> tuple[np.ndarray, np.ndarray] | None:
    """Pixel displacement after ``minutes`` of the points ``rows, cols`` of ``cell``.

    ``None`` when the cell has no available raw motion.  Straight variants give
    the same displacement everywhere; the field ones follow a trajectory from
    each point.
    """
    return displacements([cell], variant, minutes, [(rows, cols)], field, policy)[0]


def displacements(cells: list[dict], variant: str, minutes: float, points: list,
                  field: MotionField | None, policy: CellPolicy) -> list:
    """:func:`displacement` for many cells at once — one integration for all.

    ``points[k]`` is ``(rows, cols)`` for ``cells[k]``.  Batched because
    scoring projects every footprint block of every cell of a frame, and
    per-cell calls would multiply the numpy overhead by the cell count.
    """
    out: list = [None] * len(cells)
    batch = []  # (k, rows, cols, vec)
    for k, (cell, (rows, cols)) in enumerate(zip(cells, points)):
        rows = np.atleast_1d(np.asarray(rows, dtype=float))
        cols = np.atleast_1d(np.asarray(cols, dtype=float))
        vec = cell_vector(cell["motion"], variant)
        if vec is None:
            continue
        if variant in STRAIGHT or field is None:
            out[k] = (np.full(rows.shape, vec[0] * minutes), np.full(rows.shape, vec[1] * minutes))
        else:
            batch.append((k, rows, cols, vec))
    if not batch:
        return out
    offsets = np.zeros((len(batch), 2))
    straight = np.zeros(len(batch), dtype=bool)
    if variant == "field_anchored":
        f0r, f0c = field.velocity([cells[k]["row"] for k, *_ in batch], [cells[k]["col"] for k, *_ in batch])
        for n, (_, _, _, vec) in enumerate(batch):
            if np.isfinite(f0r[n]) and np.isfinite(f0c[n]):
                offsets[n] = (vec[0] - f0r[n], vec[1] - f0c[n])
            else:
                # No field at the cell: nothing to anchor the variation to.
                straight[n] = True
    todo = [n for n in range(len(batch)) if not straight[n]]
    for n in range(len(batch)):
        if straight[n]:
            k, rows, _, vec = batch[n]
            out[k] = (np.full(rows.shape, vec[0] * minutes), np.full(rows.shape, vec[1] * minutes))
    if not todo:
        return out
    sizes = [batch[n][1].size for n in todo]
    rows = np.concatenate([batch[n][1] for n in todo])
    cols = np.concatenate([batch[n][2] for n in todo])
    off = np.repeat(offsets[todo], sizes, axis=0)
    fallback = np.repeat(np.array([batch[n][3] for n in todo], dtype=float), sizes, axis=0)
    end_r, end_c = integrate(field, rows, cols, minutes, policy.advect_step_minutes, off, fallback)
    split = np.cumsum(sizes)[:-1]
    for n, dr, dc in zip(todo, np.split(end_r - rows, split), np.split(end_c - cols, split)):
        out[batch[n][0]] = (dr, dc)
    return out


def integrate(field: MotionField, rows: np.ndarray, cols: np.ndarray, minutes: float,
              step: float, off, fallback):
    """Midpoint-rule trajectories: velocity ``field(x) + off``, ``fallback`` where unsupported.

    ``off`` and ``fallback`` are ``(row, col)`` pairs, or ``(n, 2)`` arrays
    with one pair per point.
    """
    off = np.broadcast_to(np.asarray(off, dtype=float), (rows.size, 2))
    fallback = np.broadcast_to(np.asarray(fallback, dtype=float), (rows.size, 2))

    def vel(rr, cc):
        vr, vc = field.velocity(rr, cc)
        ok = np.isfinite(vr) & np.isfinite(vc)
        return (np.where(ok, vr + off[:, 0], fallback[:, 0]),
                np.where(ok, vc + off[:, 1], fallback[:, 1]))

    n = max(1, int(math.ceil(minutes / step))) if step > 0 else 1
    dt = minutes / n
    rr, cc = rows.copy(), cols.copy()
    for _ in range(n):
        v1r, v1c = vel(rr, cc)
        v2r, v2c = vel(rr + v1r * dt / 2.0, cc + v1c * dt / 2.0)
        rr = rr + v2r * dt
        cc = cc + v2c * dt
    return rr, cc


def project_centroid(cell: dict, variant: str, minutes: float, field: MotionField | None,
                     policy: CellPolicy) -> tuple[float, float] | None:
    """``(row, col)`` of the cell's centroid after ``minutes``, or ``None``."""
    d = displacement(cell, variant, minutes, [cell["row"]], [cell["col"]], field, policy)
    if d is None:
        return None
    return float(cell["row"] + d[0][0]), float(cell["col"] + d[1][0])


def project_centroids(cells: list[dict], variant: str, minutes: float, field: MotionField | None,
                      policy: CellPolicy) -> list:
    """:func:`project_centroid` for many cells, one integration."""
    moved = displacements(cells, variant, minutes, [([c["row"]], [c["col"]]) for c in cells], field, policy)
    return [None if d is None else (float(c["row"] + d[0][0]), float(c["col"] + d[1][0]))
            for c, d in zip(cells, moved)]


def project_footprint(cell: dict, variant: str, minutes: float, rows: np.ndarray, cols: np.ndarray,
                      block_px: int, field: MotionField | None, policy: CellPolicy):
    """Footprint pixels moved by the variant, or ``None`` (one cell; see :func:`project_footprints`)."""
    return project_footprints([cell], variant, minutes, [(rows, cols, block_px)], field, policy)[0]


def project_footprints(cells: list[dict], variant: str, minutes: float, footprints: list,
                       field: MotionField | None, policy: CellPolicy) -> list:
    """Each cell's footprint pixels moved by the variant (``None`` without motion).

    ``footprints[k]`` is ``(rows, cols, block_px)``.  The displacement is
    taken at each footprint block's centre and applied, rounded, to all of
    its pixels — the field is smooth over tens of km, so a block (≤ 8 km)
    moves as one, and a straight variant gives exactly the whole-footprint
    integer shift the raw score always used.
    """
    points = []
    inverses = []
    for cell, (rows, cols, block_px) in zip(cells, footprints):
        b = max(1, int(block_px))
        if variant in STRAIGHT or field is None or rows.size == 0:
            points.append(([cell["row"]], [cell["col"]]))
            inverses.append(None)
            continue
        keys = np.stack([rows // b, cols // b], axis=1)
        blocks, inverse = np.unique(keys, axis=0, return_inverse=True)
        points.append((blocks[:, 0] * b + (b - 1) / 2.0, blocks[:, 1] * b + (b - 1) / 2.0))
        inverses.append(np.asarray(inverse).reshape(-1))
    moved = displacements(cells, variant, minutes, points, field, policy)
    out = []
    for (rows, cols, _), inverse, d in zip(footprints, inverses, moved):
        if d is None:
            out.append(None)
        elif inverse is None:
            out.append((rows + int(round(float(d[0][0]))), cols + int(round(float(d[1][0])))))
        else:
            out.append((rows + np.rint(d[0]).astype(np.int64)[inverse],
                        cols + np.rint(d[1]).astype(np.int64)[inverse]))
    return out
