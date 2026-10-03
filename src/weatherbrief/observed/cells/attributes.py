"""Per-cell attributes from the other observed sources.

Each attribute is read at its *own* frame time — the catalogue records which
frame that was — and never advected:

* **Rain rate** (OPERA RATE): peak over the cell's footprint.  The only
  intensity we claim; we never convert dBZ to a rate ourselves (D8).
* **Lightning** (MTG LI): flashes within ``flash_buffer_km`` of the cell.
  Evidence, counted where it happened; nothing projects it forward.
* **Cloud top** (MTG CTTH): highest top whose *parallax-corrected* position
  falls inside the footprint.  Parallax is applied before membership, exactly
  as the corridor sampler does — the uncorrected pixel is tens of km away.

Every function returns ``None`` per cell when its source could not answer
(no frame, different grid, no detection over the cell), never a zero standing
in for "unknown".
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from ..ctth import metres_to_fl
from ..frames import FlashFrame, GridFrame
from .detect import TierDetection


def same_grid(a: GridFrame, b: GridFrame) -> bool:
    return a.grid == b.grid and a.window == b.window


def rate_peaks(det: TierDetection, frame_grid, rate: GridFrame) -> list[float | None]:
    """Peak rain rate (mm/h) per cell, ``None`` where RATE detected nothing.

    RATE is a 2 km composite and DBZH a 1 km one on the same projection, so
    each cell pixel maps to its RATE pixel through the two affines — no
    reprojection, and a 2 km rate pixel serves the four DBZH pixels inside it.
    """
    n = len(det.cells)
    if n == 0:
        return []
    values = np.asarray(rate.values, dtype=np.float32)
    if rate.grid == frame_grid and rate.window.row0 == det.row0 and rate.window.col0 == det.col0 \
            and values.shape == det.labels.shape:
        filled = np.where(np.isfinite(values), values, -np.inf)
        peaks = np.atleast_1d(ndimage.maximum(filled, det.labels, index=np.arange(1, n + 1)))
        return [float(p) if np.isfinite(p) else None for p in peaks]
    if rate.grid.proj4 != frame_grid.proj4:
        raise ValueError("RATE and DBZH projections differ")
    rows, cols = np.nonzero(det.labels)
    lab = det.labels[rows, cols]
    x = frame_grid.x0 + (cols + det.col0) * frame_grid.dx
    y = frame_grid.y0 + (rows + det.row0) * frame_grid.dy
    rc = np.rint((x - rate.grid.x0) / rate.grid.dx).astype(np.int64) - rate.window.col0
    rr = np.rint((y - rate.grid.y0) / rate.grid.dy).astype(np.int64) - rate.window.row0
    ny, nx = values.shape
    ok = (rr >= 0) & (rr < ny) & (rc >= 0) & (rc < nx)
    sampled = np.full(lab.size, np.nan, dtype=np.float32)
    sampled[ok] = values[rr[ok], rc[ok]]
    hit = np.isfinite(sampled)
    peak = np.full(n + 1, -np.inf, dtype=np.float64)
    np.maximum.at(peak, lab[hit], sampled[hit])
    return [float(peak[i]) if np.isfinite(peak[i]) else None for i in range(1, n + 1)]


def flash_counts(det: TierDetection, frame_grid, flashes: FlashFrame, buffer_km: float) -> list[int]:
    """Flashes per cell: each flash goes to the nearest cell pixel within the buffer.

    A flash between two cells counts once, for the nearer one.  Checked over a
    small square of offsets around each flash, which stays cheap however many
    thousand flashes a convective afternoon produces.
    """
    n = len(det.cells)
    counts = np.zeros(n + 1, dtype=np.int64)
    if n == 0 or flashes.lats.size == 0:
        return [0] * n
    cols, rows = frame_grid.lonlat_to_colrow(flashes.lons, flashes.lats)
    rows = np.rint(np.asarray(rows, dtype=np.float64)) - det.row0
    cols = np.rint(np.asarray(cols, dtype=np.float64)) - det.col0
    ok = np.isfinite(rows) & np.isfinite(cols)
    rows = rows[ok].astype(np.int64)
    cols = cols[ok].astype(np.int64)
    ny, nx = det.labels.shape
    reach = int(math.ceil(buffer_km / frame_grid.pixel_km))
    best_label = np.zeros(rows.size, dtype=np.int64)
    best_d2 = np.full(rows.size, np.inf)
    for dr in range(-reach, reach + 1):
        for dc in range(-reach, reach + 1):
            d2 = float(dr * dr + dc * dc)
            if d2 > reach * reach:
                continue
            r = rows + dr
            c = cols + dc
            inside = (r >= 0) & (r < ny) & (c >= 0) & (c < nx)
            lab = np.zeros(rows.size, dtype=np.int64)
            lab[inside] = det.labels[r[inside], c[inside]]
            better = (lab > 0) & (d2 < best_d2)
            best_label[better] = lab[better]
            best_d2[better] = d2
    hit = best_label > 0
    np.add.at(counts, best_label[hit], 1)
    return [int(x) for x in counts[1:]]


def cloud_tops(det: TierDetection, frame_grid, ctth: GridFrame) -> list[tuple[float | None, int]]:
    """(highest top FL, cloudy pixel count) per cell from parallax-corrected CTTH."""
    n = len(det.cells)
    if n == 0:
        return []
    values = np.asarray(ctth.values, dtype=np.float64)
    cloudy = np.isfinite(values)
    if not cloudy.any():
        return [(None, 0)] * n
    rr, cc = np.nonzero(cloudy)
    lon, lat = ctth.grid.colrow_to_lonlat(cc + ctth.window.col0, rr + ctth.window.row0)
    lat = np.asarray(lat, dtype=np.float64) + ctth.aux["delta_latitude"][rr, cc]
    lon = np.asarray(lon, dtype=np.float64) + ctth.aux["delta_longitude"][rr, cc]
    heights = values[rr, cc]

    cols, rows = frame_grid.lonlat_to_colrow(lon, lat)
    rows = np.rint(np.asarray(rows, dtype=np.float64)) - det.row0
    cols = np.rint(np.asarray(cols, dtype=np.float64)) - det.col0
    ny, nx = det.labels.shape
    ok = np.isfinite(rows) & np.isfinite(cols) & (rows >= 0) & (rows < ny) & (cols >= 0) & (cols < nx)
    lab = np.zeros(rows.size, dtype=np.int64)
    lab[ok] = det.labels[rows[ok].astype(np.int64), cols[ok].astype(np.int64)]
    hit = lab > 0
    top = np.full(n + 1, -np.inf)
    np.maximum.at(top, lab[hit], heights[hit])
    count = np.bincount(lab[hit], minlength=n + 1)
    fl = metres_to_fl(np.where(np.isfinite(top), top, 0.0))
    return [
        (float(fl[i]) if np.isfinite(top[i]) else None, int(count[i]))
        for i in range(1, n + 1)
    ]
