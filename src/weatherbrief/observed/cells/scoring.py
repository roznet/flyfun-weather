"""Self-scoring: did the motion we measured predict where the cells went?

At each frame T, for each lead L (30, 60 min), the cells of the catalogue at
T−L that had an available velocity are moved by velocity × L and compared
with what the radar shows at T.  The same cells left where they were form the
**persistence** baseline — extrapolation is only worth showing a pilot where it
beats "it stays put".

Two kinds of numbers per (tier, lead):

* **Footprint skill** — hits / misses / false alarms on the tier's block grid
  (the footprint's own resolution, so a coarse rain-area footprint is not
  punished for its encoding), giving POD, FAR, CSI for both forecasts.
  Verified only where the radar covered the pixel at T, and only within
  ``score_margin_km`` of an issued footprint: a cell born far from anything we
  tracked is not a miss of the tracker.
* **Centroid error** — for cells whose id survives to T, distance from the
  advected (and from the unmoved) centroid to the real one, in km.

Nothing here feeds a pilot.  The rows accumulate in ``scores/<day>.jsonl`` and
are what will set the projection horizon (Tier 3) and its acceptance gates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy import ndimage

from .catalogue import catalogue_path, cells_dir, r, read_catalogue
from .detect import TierDetection, distance_km, runs_to_pixels
from .policy import px

def _block_mask(rows, cols, shape, b, row0, col0):
    mask = np.zeros(shape, dtype=bool)
    br = (rows - row0) // b
    bc = (cols - col0) // b
    ok = (br >= 0) & (br < shape[0]) & (bc >= 0) & (bc < shape[1])
    mask[br[ok], bc[ok]] = True
    return mask


def _blockify(mask: np.ndarray, b: int) -> np.ndarray:
    if b == 1:
        return mask
    h = -(-mask.shape[0] // b) * b
    w = -(-mask.shape[1] // b) * b
    padded = np.zeros((h, w), dtype=bool)
    padded[: mask.shape[0], : mask.shape[1]] = mask
    return padded.reshape(h // b, b, w // b, b).any(axis=(1, 3))


def _contingency(forecast, observed, area):
    hits = int(np.count_nonzero(forecast & observed & area))
    misses = int(np.count_nonzero(~forecast & observed & area))
    false_alarms = int(np.count_nonzero(forecast & ~observed & area))
    pod = hits / (hits + misses) if hits + misses else None
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else None
    csi = hits / (hits + misses + false_alarms) if hits + misses + false_alarms else None
    return {"hits": hits, "misses": misses, "false_alarms": false_alarms,
            "pod": r(pod, 4), "far": r(far, 4), "csi": r(csi, 4)}


def score_frame(
    root: Path,
    valid_time: datetime,
    grid,
    detections: dict[str, TierDetection],
    covered: np.ndarray,
    current_catalogue: dict,
    leads_minutes: tuple[int, ...],
    policy_version: str,
    margin_km: float,
) -> list[dict]:
    rows_out: list[dict] = []
    now_cells = {c["id"]: c for c in current_catalogue["cells"]}
    for lead in leads_minutes:
        issued_time = valid_time - timedelta(minutes=lead)
        issued = read_catalogue(catalogue_path(root, issued_time))
        if issued is None or issued.get("policy_version") != policy_version:
            continue
        for tier_name, det in detections.items():
            b = det.tier.block_px(grid.pixel_km)
            shape_b = _blockify(np.zeros(det.labels.shape, dtype=bool), b).shape
            fc = np.zeros(shape_b, dtype=bool)
            pers = np.zeros(shape_b, dtype=bool)
            errs, pers_errs = [], []
            n_cells = 0
            for cell in issued["cells"]:
                if cell["tier"] != tier_name or cell["motion"]["status"] != "available":
                    continue
                n_cells += 1
                pr, pc = runs_to_pixels(cell["footprint"]["runs"], cell["footprint"]["block_px"])
                pers |= _block_mask(pr, pc, shape_b, b, det.row0, det.col0)
                m = cell["motion"]
                shift_r = int(round(m["drow_per_min"] * lead))
                shift_c = int(round(m["dcol_per_min"] * lead))
                fc |= _block_mask(pr + shift_r, pc + shift_c, shape_b, b, det.row0, det.col0)
                later = now_cells.get(cell["id"])
                if later is not None:
                    lon, lat = _advect_centroid(grid, cell, lead)
                    errs.append(distance_km(lat, lon, later["lat"], later["lon"]))
                    pers_errs.append(distance_km(cell["lat"], cell["lon"], later["lat"], later["lon"]))
            if n_cells == 0:
                continue
            observed = _blockify(det.labels > 0, b)
            cov_b = ~_blockify(~covered, b)  # a block counts only if fully covered
            margin = px(margin_km, grid.pixel_km * b)
            # Square neighbourhood via a separable max filter: one pass, where
            # an iterated binary dilation would cost ``margin`` passes per tier.
            near = ndimage.maximum_filter((fc | pers).astype(np.uint8), size=2 * margin + 1) > 0
            area = near & cov_b
            rows_out.append({
                "verify_time": valid_time.isoformat(),
                "issued_time": issued_time.isoformat(),
                "lead_min": lead,
                "tier": tier_name,
                "policy_version": policy_version,
                "cells_issued": n_cells,
                "cells_tracked": len(errs),
                "extrapolation": _contingency(fc, observed, area),
                "persistence": _contingency(pers, observed, area),
                "centroid_err_km_median": r(np.median(errs), 2) if errs else None,
                "persistence_centroid_err_km_median": r(np.median(pers_errs), 2) if pers_errs else None,
            })
    return rows_out


def _advect_centroid(grid, cell: dict, lead: int) -> tuple[float, float]:
    m = cell["motion"]
    lon, lat = grid.colrow_to_lonlat(
        cell["col"] + m["dcol_per_min"] * lead, cell["row"] + m["drow_per_min"] * lead
    )
    return float(lon), float(lat)


def append_scores(root: Path, valid_time: datetime, rows: list[dict]) -> None:
    if not rows:
        return
    path = cells_dir(root) / "scores" / f"{valid_time:%Y%m%d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
