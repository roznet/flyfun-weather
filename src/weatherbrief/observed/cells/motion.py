"""Field motion from a pair of reflectivity frames.

Field-based, not object matching (brainstorm §4, family B): the composite is
cut into half-overlapping tiles, each tile of the earlier frame is matched in
the later one by **masked normalised cross-correlation**, and a cell's velocity
is the mean displacement of the tiles under it.  Splits and merges cannot
break a field estimate the way they break object-to-object matching.

Masking is the point of the formulation (Padfield, "Masked object
registration in the Fourier domain", 2012).  Pixels the radar network did not
cover take no part in the match under any shift — so a coverage edge, which
does not move, cannot pull the estimate towards zero, and the half of the
OPERA grid that is nodata never enters a correlation.

Each tile's estimate must pass three gates or it is dropped, never guessed:
enough coverage and echo to match on, a correlation peak above ``min_ncc``,
and forward/reverse agreement (matching later→earlier must give the opposite
shift to within ``max_reciprocity_px``).

Pure numpy/scipy (``scipy.signal.fftconvolve``), no OpenCV, no process pool —
identical on macOS and Linux.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import fftconvolve

from ..frames import GridFrame
from .policy import CellPolicy, px

KT_TO_MS = 0.514444


@dataclass
class FlowField:
    """Per-tile displacement over ``pair_minutes``, in grid pixels.

    Tile ``(i, j)`` covers window rows ``i*stride .. i*stride+tile`` and the
    matching columns.  ``dr``/``dc`` are NaN where the tile was not matched.
    """

    pair_minutes: float
    tile_px: int
    stride_px: int
    dr: np.ndarray  # (n_tile_rows, n_tile_cols)
    dc: np.ndarray
    ncc: np.ndarray
    tried: int
    matched: int

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.dr) & np.isfinite(self.dc)


def correlation_field(frame: GridFrame, policy: CellPolicy) -> tuple[np.ndarray, np.ndarray]:
    """(field, covered) for matching: dBZ above the floor, 0 where empty.

    float32 over the full grid (67 MB at 3800 × 4400 rather than 134); each
    tile is promoted to float64 inside :func:`masked_ncc`.
    """
    values = np.asarray(frame.values, dtype=np.float32)
    covered = ~np.asarray(frame.nodata)
    with np.errstate(invalid="ignore"):
        field = np.where(np.isfinite(values), np.clip(values - policy.flow_floor_dbz, 0.0, 50.0), 0.0)
    field = field.astype(np.float32, copy=False)
    field[~covered] = 0.0
    return field, covered


def search_radius_px(policy: CellPolicy, pair_minutes: float, pixel_km: float) -> int:
    reach_km = policy.max_speed_kt * KT_TO_MS * pair_minutes * 60.0 / 1000.0
    return int(math.ceil(reach_km / pixel_km)) + 1


def _xcorr(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    return fftconvolve(image, template[::-1, ::-1], mode="valid")


def masked_ncc(
    image: np.ndarray,
    image_mask: np.ndarray,
    template: np.ndarray,
    template_mask: np.ndarray,
    *,
    min_overlap_fraction: float = 0.5,
) -> np.ndarray:
    """Masked NCC of ``template`` at every fully-inside offset of ``image``.

    Output shape is ``image.shape - template.shape + 1``; entry ``(i, j)`` is
    the correlation with the template's top-left corner at ``(i, j)``.  Only
    pixels valid in *both* masks contribute.  NaN where the overlap is too
    small or either side has no variance.
    """
    image = np.asarray(image, dtype=np.float64)
    template = np.asarray(template, dtype=np.float64)
    f_m = image_mask.astype(np.float64)
    t_m = template_mask.astype(np.float64)
    f1 = image * f_m
    t1 = template * t_m
    overlap = _xcorr(f_m, t_m)
    sum_f = _xcorr(f1, t_m)
    sum_f2 = _xcorr(f1 * image, t_m)
    sum_t = _xcorr(f_m, t1)
    sum_t2 = _xcorr(f_m, t1 * template)
    sum_ft = _xcorr(f1, t1)

    min_overlap = min_overlap_fraction * template_mask.sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = overlap >= max(min_overlap, 1.0)
        n = np.where(ok, overlap, 1.0)
        num = sum_ft - sum_f * sum_t / n
        var_f = sum_f2 - sum_f * sum_f / n
        var_t = sum_t2 - sum_t * sum_t / n
        denom = np.sqrt(np.clip(var_f, 0, None) * np.clip(var_t, 0, None))
        # Variance below this is FFT round-off on a flat patch, not texture.
        eps = 1e-6 * max(float(np.abs(sum_f2).max(initial=0.0)), 1.0)
        ok &= (var_f > eps) & (var_t > eps)
        ncc = np.where(ok, num / np.where(denom > 0, denom, 1.0), np.nan)
    return np.clip(ncc, -1.0, 1.0)


def _peak(ncc: np.ndarray) -> tuple[float, float, float] | None:
    """Sub-pixel (row, col, value) of the correlation maximum."""
    if not np.isfinite(ncc).any():
        return None
    filled = np.where(np.isfinite(ncc), ncc, -np.inf)
    i, j = np.unravel_index(int(np.argmax(filled)), filled.shape)
    best = float(filled[i, j])

    def refine(a, b, c):
        if not (np.isfinite(a) and np.isfinite(c)):
            return 0.0
        d = a - 2.0 * b + c
        return 0.0 if d >= 0 else float(np.clip(0.5 * (a - c) / d, -0.5, 0.5))

    di = refine(filled[i - 1, j], best, filled[i + 1, j]) if 0 < i < filled.shape[0] - 1 else 0.0
    dj = refine(filled[i, j - 1], best, filled[i, j + 1]) if 0 < j < filled.shape[1] - 1 else 0.0
    return i + di, j + dj, best


def _match(
    src_field, src_cov, dst_field, dst_cov, r0, c0, tile, radius
) -> tuple[float, float, float] | None:
    """Displacement of the ``src`` tile at (r0, c0) inside ``dst``.

    ``dst_*`` are padded by ``radius`` on every side, so the search block for
    the tile is ``[r0 : r0 + tile + 2*radius]`` in padded coordinates.
    """
    template = src_field[r0:r0 + tile, c0:c0 + tile]
    t_mask = src_cov[r0:r0 + tile, c0:c0 + tile]
    block = dst_field[r0:r0 + tile + 2 * radius, c0:c0 + tile + 2 * radius]
    b_mask = dst_cov[r0:r0 + tile + 2 * radius, c0:c0 + tile + 2 * radius]
    ncc = masked_ncc(block, b_mask, template, t_mask)
    peak = _peak(ncc)
    if peak is None:
        return None
    pi, pj, value = peak
    return pi - radius, pj - radius, value


def estimate_flow(
    earlier: GridFrame, later: GridFrame, policy: CellPolicy, pair_minutes: float
) -> FlowField:
    if earlier.values.shape != later.values.shape or earlier.window != later.window:
        raise ValueError("flow needs two windows of the same grid")
    pixel_km = later.grid.pixel_km
    tile = px(policy.tile_km, pixel_km)
    stride = px(policy.tile_stride_km, pixel_km)
    radius = search_radius_px(policy, pair_minutes, pixel_km)

    f0, cov0 = correlation_field(earlier, policy)
    f1, cov1 = correlation_field(later, policy)
    echo_level = policy.tile_echo_dbz - policy.flow_floor_dbz

    pad = ((radius, radius), (radius, radius))
    f0p = np.pad(f0, pad)
    f1p = np.pad(f1, pad)
    cov0p = np.pad(cov0, pad, constant_values=False)
    cov1p = np.pad(cov1, pad, constant_values=False)

    ny, nx = f0.shape
    n_i = max(0, (ny - tile) // stride + 1)
    n_j = max(0, (nx - tile) // stride + 1)
    dr = np.full((n_i, n_j), np.nan)
    dc = np.full((n_i, n_j), np.nan)
    score = np.full((n_i, n_j), np.nan)
    tried = matched = 0

    for i in range(n_i):
        r0 = i * stride
        for j in range(n_j):
            c0 = j * stride
            t_cov = cov0[r0:r0 + tile, c0:c0 + tile]
            if t_cov.mean() < policy.min_tile_valid_fraction:
                continue
            if (f0[r0:r0 + tile, c0:c0 + tile] >= echo_level).mean() < policy.min_tile_echo_fraction:
                continue
            tried += 1
            fwd = _match(f0, cov0, f1p, cov1p, r0, c0, tile, radius)
            if fwd is None or fwd[2] < policy.min_ncc:
                continue
            # Reverse: the later frame's tile near the matched position,
            # searched for in the earlier frame, must come back by the opposite
            # shift.  Clamped into the grid so an edge tile moving outward is
            # still checked (motion is locally uniform, so where the reverse
            # tile sits does not change the expected answer: fwd + rev ≈ 0).
            rr = min(max(int(round(r0 + fwd[0])), 0), ny - tile)
            rc = min(max(int(round(c0 + fwd[1])), 0), nx - tile)
            rev = _match(f1, cov1, f0p, cov0p, rr, rc, tile, radius)
            if rev is None or rev[2] < policy.min_ncc:
                continue
            if math.hypot(fwd[0] + rev[0], fwd[1] + rev[1]) > policy.max_reciprocity_px:
                continue
            dr[i, j] = fwd[0]
            dc[i, j] = fwd[1]
            score[i, j] = fwd[2]
            matched += 1

    return FlowField(
        pair_minutes=float(pair_minutes),
        tile_px=tile,
        stride_px=stride,
        dr=dr,
        dc=dc,
        ncc=score,
        tried=tried,
        matched=matched,
    )


@dataclass
class CellMotion:
    status: str  # "available" | "unsupported" | "no_pair" | "withheld"
    reason: str | None
    support: float
    drow_per_min: float | None = None
    dcol_per_min: float | None = None


def cell_motion(flow: FlowField | None, labels: np.ndarray, label: int, rows: slice, cols: slice) -> CellMotion:
    """Mean tile displacement under a cell, weighted by the cell's pixels per tile.

    ``support`` is the share of that weight carried by matched tiles.  Below
    ``min_motion_support`` the caller withholds the velocity: a cell mostly
    sitting in tiles that failed to match has no measured motion, and an
    average of the one tile that did match would be a guess.
    """
    if flow is None:
        return CellMotion("no_pair", "no earlier frame to pair with", 0.0)
    tile, stride = flow.tile_px, flow.stride_px
    n_i, n_j = flow.dr.shape
    if n_i == 0 or n_j == 0:
        return CellMotion("unsupported", "grid smaller than one tile", 0.0)
    i_lo = max(0, -(-(rows.start - tile + 1) // stride))
    i_hi = min(n_i - 1, (rows.stop - 1) // stride)
    j_lo = max(0, -(-(cols.start - tile + 1) // stride))
    j_hi = min(n_j - 1, (cols.stop - 1) // stride)

    total = 0.0
    supported = 0.0
    sum_r = sum_c = 0.0
    for i in range(i_lo, i_hi + 1):
        r0 = i * stride
        rs = slice(max(rows.start, r0), min(rows.stop, r0 + tile))
        if rs.start >= rs.stop:
            continue
        for j in range(j_lo, j_hi + 1):
            c0 = j * stride
            cs = slice(max(cols.start, c0), min(cols.stop, c0 + tile))
            if cs.start >= cs.stop:
                continue
            w = float(np.count_nonzero(labels[rs, cs] == label))
            if w == 0:
                continue
            total += w
            if np.isfinite(flow.dr[i, j]):
                supported += w
                sum_r += w * flow.dr[i, j]
                sum_c += w * flow.dc[i, j]
    if total == 0:
        # The cell lies in the strip past the last whole tile.
        return CellMotion("unsupported", "outside the tiled area", 0.0)
    support = supported / total
    return CellMotion(
        "pending",
        None,
        support,
        (sum_r / supported) / flow.pair_minutes if supported else None,
        (sum_c / supported) / flow.pair_minutes if supported else None,
    )
