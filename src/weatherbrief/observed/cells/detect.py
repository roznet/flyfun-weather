"""Cell detection: connected components on one reflectivity frame.

Works on any :class:`~weatherbrief.observed.frames.GridFrame` window — the
whole OPERA composite on the archive loop, a route crop if a droplet fallback
is ever built — and reports every position in *absolute* grid pixels so two
windows of the same grid agree.

Three-state coverage carries through: ``nodata`` is never "no echo".  A cell
that touches a nodata pixel or the edge of the window is flagged
``truncated`` — its extent, peak and centroid describe only the part the
radar network saw.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from ..frames import GridFrame
from ..grid import haversine_km
from .policy import TierPolicy

# 8-connectivity: diagonal neighbours belong to the same cell, so a cell whose
# outline runs diagonally across the grid is not split into a staircase.
_STRUCTURE = np.ones((3, 3), dtype=bool)


@dataclass
class Cell:
    """One detected object.  Pixel coordinates are absolute grid indices."""

    label: int
    area_px: int
    area_km2: float
    peak_dbz: float
    centroid_row: float
    centroid_col: float
    lat: float
    lon: float
    truncated: bool
    major_km: float
    minor_km: float
    orientation_deg: float | None
    bbox: tuple[float, float, float, float]  # south, west, north, east
    slice_rows: slice  # within the frame window
    slice_cols: slice


@dataclass
class TierDetection:
    tier: TierPolicy
    labels: np.ndarray  # int32, window-shaped, 0 = no cell
    cells: list[Cell]
    row0: int  # absolute offset of the window
    col0: int

    def cell_by_label(self) -> dict[int, Cell]:
        return {c.label: c for c in self.cells}


def detect(frame: GridFrame, tier: TierPolicy) -> TierDetection:
    values = np.asarray(frame.values)
    # NaN (nodata and undetect) compares False, so neither can join a cell.
    with np.errstate(invalid="ignore"):
        mask = values >= tier.threshold_dbz
    raw, count = ndimage.label(mask, structure=_STRUCTURE)

    grid = frame.grid
    pixel_km2 = abs(grid.dx * grid.dy) / 1e6  # OPERA is equal-area (LAEA)
    min_px = max(1, int(math.ceil(tier.min_area_km2 / pixel_km2)))

    sizes = np.bincount(raw.ravel(), minlength=count + 1)
    keep = sizes >= min_px
    keep[0] = False
    # Relabel 1..n in raster order of first appearance (ndimage.label's own
    # order), so label numbers are deterministic for a given frame + policy —
    # the lineage step relies on that to re-identify last frame's cells.
    mapping = np.zeros(count + 1, dtype=np.int32)
    mapping[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    labels = mapping[raw]
    n = int(keep.sum())

    row0, col0 = frame.window.row0, frame.window.col0
    if n == 0:
        return TierDetection(tier, labels, [], row0, col0)

    rows, cols = np.nonzero(labels)
    lab = labels[rows, cols]
    area = np.bincount(lab, minlength=n + 1).astype(np.float64)
    sum_r = np.bincount(lab, weights=rows, minlength=n + 1)
    sum_c = np.bincount(lab, weights=cols, minlength=n + 1)
    cr = sum_r / np.maximum(area, 1)
    cc = sum_c / np.maximum(area, 1)
    # Second moments about the centroid → the equivalent ellipse.
    dr = rows - cr[lab]
    dc = cols - cc[lab]
    m_rr = np.bincount(lab, weights=dr * dr, minlength=n + 1) / np.maximum(area, 1)
    m_cc = np.bincount(lab, weights=dc * dc, minlength=n + 1) / np.maximum(area, 1)
    m_rc = np.bincount(lab, weights=dr * dc, minlength=n + 1) / np.maximum(area, 1)

    filled = np.where(labels > 0, values, np.float32(-np.inf)).astype(np.float32, copy=False)
    peaks = ndimage.maximum(filled, labels, index=np.arange(1, n + 1))
    peaks = np.atleast_1d(np.asarray(peaks, dtype=np.float64))

    # Truncation: touches nodata (8-neighbourhood) or the window edge.
    near_nodata = ndimage.binary_dilation(np.asarray(frame.nodata), structure=_STRUCTURE)
    truncated = np.zeros(n + 1, dtype=bool)
    truncated[np.unique(labels[near_nodata & (labels > 0)])] = True
    for edge in (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]):
        truncated[np.unique(edge[edge > 0])] = True

    abs_r = cr + row0
    abs_c = cc + col0
    lons, lats = grid.colrow_to_lonlat(abs_c[1:], abs_r[1:])
    lons = np.atleast_1d(np.asarray(lons, dtype=np.float64))
    lats = np.atleast_1d(np.asarray(lats, dtype=np.float64))

    slices = ndimage.find_objects(labels, max_label=n)
    cells: list[Cell] = []
    for i in range(1, n + 1):
        sl = slices[i - 1]
        major_km, minor_km, orient = _ellipse(
            grid, abs_r[i], abs_c[i], m_rr[i], m_cc[i], m_rc[i], lats[i - 1], lons[i - 1]
        )
        cells.append(
            Cell(
                label=i,
                area_px=int(area[i]),
                area_km2=float(area[i] * pixel_km2),
                peak_dbz=float(peaks[i - 1]),
                centroid_row=float(abs_r[i]),
                centroid_col=float(abs_c[i]),
                lat=float(lats[i - 1]),
                lon=float(lons[i - 1]),
                truncated=bool(truncated[i]),
                major_km=major_km,
                minor_km=minor_km,
                orientation_deg=orient,
                bbox=_bbox(grid, sl, row0, col0),
                slice_rows=sl[0],
                slice_cols=sl[1],
            )
        )
    return TierDetection(tier, labels, cells, row0, col0)


def _ellipse(grid, r, c, m_rr, m_cc, m_rc, lat, lon):
    """Equivalent-ellipse axes (km) and major-axis bearing (0–180°, true).

    For a uniform ellipse the variance along an axis is a²/4, so the full axis
    length is 4σ.  The bearing is measured by projecting the axis end point
    back to lat/lon rather than assuming grid north is true north — on the
    OPERA Lambert grid they diverge by tens of degrees at the domain edges.
    """
    cov = np.array([[m_rr, m_rc], [m_rc, m_cc]])
    evals, evecs = np.linalg.eigh(cov)
    evals = np.clip(evals, 0.0, None)
    px_km = grid.pixel_km
    major_km = float(4.0 * math.sqrt(evals[1]) * px_km)
    minor_km = float(4.0 * math.sqrt(evals[0]) * px_km)
    if evals[1] <= 0 or major_km - minor_km < 0.25 * max(major_km, 1e-9):
        # Near-round: an orientation would be noise.
        return major_km, minor_km, None
    vr, vc = evecs[0, 1], evecs[1, 1]
    lon2, lat2 = grid.colrow_to_lonlat(c + vc * 10.0, r + vr * 10.0)
    bearing = initial_bearing_deg(lat, lon, float(lat2), float(lon2)) % 180.0
    return major_km, minor_km, float(bearing)


def _bbox(grid, sl, row0, col0) -> tuple[float, float, float, float]:
    r = np.array([sl[0].start, sl[0].start, sl[0].stop - 1, sl[0].stop - 1]) + row0
    c = np.array([sl[1].start, sl[1].stop - 1, sl[1].start, sl[1].stop - 1]) + col0
    lons, lats = grid.colrow_to_lonlat(c, r)
    lons = np.asarray(lons)
    lats = np.asarray(lats)
    return float(lats.min()), float(lons.min()), float(lats.max()), float(lons.max())


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees true."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(haversine_km(lat1, lon1, np.array([lat2]), np.array([lon2]))[0])


def footprint_runs(det: TierDetection, cell: Cell, block_px: int) -> list[list[int]]:
    """Run-length footprint at ``block_px`` resolution, in absolute block indices.

    Each run is ``[block_row, block_col_start, block_col_end)``.  A block is
    occupied if any of its pixels belongs to the cell.  Decoding: pixel
    rows ``block_row*b .. block_row*b + b - 1`` and the matching columns.
    """
    sub = det.labels[cell.slice_rows, cell.slice_cols] == cell.label
    abs_r0 = cell.slice_rows.start + det.row0
    abs_c0 = cell.slice_cols.start + det.col0
    b = block_px
    if b > 1:
        # Pad so block boundaries align with absolute multiples of b.
        pad_top = abs_r0 % b
        pad_left = abs_c0 % b
        h = sub.shape[0] + pad_top
        w = sub.shape[1] + pad_left
        H = -(-h // b) * b
        W = -(-w // b) * b
        padded = np.zeros((H, W), dtype=bool)
        padded[pad_top:pad_top + sub.shape[0], pad_left:pad_left + sub.shape[1]] = sub
        sub = padded.reshape(H // b, b, W // b, b).any(axis=(1, 3))
        base_r = (abs_r0 - pad_top) // b
        base_c = (abs_c0 - pad_left) // b
    else:
        base_r, base_c = abs_r0, abs_c0
    runs: list[list[int]] = []
    for i in range(sub.shape[0]):
        row = sub[i]
        if not row.any():
            continue
        padded_row = np.concatenate(([False], row, [False]))
        edges = np.flatnonzero(padded_row[1:] != padded_row[:-1])
        for start, stop in zip(edges[0::2], edges[1::2]):
            runs.append([int(base_r + i), int(base_c + start), int(base_c + stop)])
    return runs


def runs_to_pixels(runs: list[list[int]], block_px: int) -> tuple[np.ndarray, np.ndarray]:
    """Absolute (rows, cols) of every pixel a footprint covers."""
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    b = block_px
    for br, bc0, bc1 in runs:
        c = np.arange(bc0 * b, bc1 * b)
        for dr in range(b):
            rows.append(np.full(c.size, br * b + dr))
            cols.append(c)
    if not rows:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    return np.concatenate(rows), np.concatenate(cols)
