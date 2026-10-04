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

Since #662 every projection in ``policy.MOTION_VARIANTS`` (raw, smoothed and
track straight lines, field and field-anchored trajectories — ``advect.py``)
is scored on the same cells and the same verification area, under
``variants``; ``extrapolation`` stays the raw straight line.  ``summarise``
gives the side-by-side medians (CLI: ``scores``).

Nothing here feeds a pilot.  The rows accumulate in ``scores/<day>.jsonl`` and
are what will set the projection horizon (Tier 3) and its acceptance gates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy import ndimage

from .advect import MotionField, project_centroids, project_footprints
from .catalogue import catalogue_path, cells_dir, r, read_catalogue
from .detect import TierDetection, distance_km, runs_to_pixels
from .policy import MOTION_VARIANTS, CellPolicy, px


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
    policy: CellPolicy,
) -> list[dict]:
    rows_out: list[dict] = []
    now_cells = {c["id"]: c for c in current_catalogue["cells"]}
    for lead in policy.score_leads_minutes:
        issued_time = valid_time - timedelta(minutes=lead)
        issued = read_catalogue(catalogue_path(root, issued_time))
        if issued is None or issued.get("policy_version") != policy.policy_version:
            continue
        field = MotionField.from_dict(issued.get("flow"), policy)
        for tier_name, det in detections.items():
            b = det.tier.block_px(grid.pixel_km)
            shape_b = _blockify(np.zeros(det.labels.shape, dtype=bool), b).shape
            pers = np.zeros(shape_b, dtype=bool)
            pers_errs: list[float] = []
            cells = [c for c in issued["cells"]
                     if c["tier"] == tier_name and c["motion"]["status"] == "available"]
            n_cells = len(cells)
            if n_cells == 0:
                continue
            n_smoothed = sum(int(((c["motion"].get("smoothed") or {}).get("n") or 1) > 1) for c in cells)
            n_track = sum(int(((c["motion"].get("track") or {}).get("n") or 1) > 1) for c in cells)
            footprints = []
            for cell in cells:
                fb = cell["footprint"]["block_px"]
                pr, pc = runs_to_pixels(cell["footprint"]["runs"], fb)
                footprints.append((pr, pc, fb))
                pers |= _block_mask(pr, pc, shape_b, b, det.row0, det.col0)
            later = [now_cells.get(c["id"]) for c in cells]
            for cell, now in zip(cells, later):
                if now is not None:
                    pers_errs.append(distance_km(cell["lat"], cell["lon"], now["lat"], now["lon"]))
            fc = {v: np.zeros(shape_b, dtype=bool) for v in MOTION_VARIANTS}
            errs: dict[str, list[float]] = {v: [] for v in MOTION_VARIANTS}
            for variant in MOTION_VARIANTS:
                for moved in project_footprints(cells, variant, lead, footprints, field, policy):
                    fc[variant] |= _block_mask(moved[0], moved[1], shape_b, b, det.row0, det.col0)
                for end, now in zip(project_centroids(cells, variant, lead, field, policy), later):
                    if now is not None:
                        lon, lat = grid.colrow_to_lonlat(end[1], end[0])
                        errs[variant].append(distance_km(float(lat), float(lon), now["lat"], now["lon"]))
            observed = _blockify(det.labels > 0, b)
            cov_b = ~_blockify(~covered, b)  # a block counts only if fully covered
            margin = px(policy.score_margin_km, grid.pixel_km * b)
            # One verification area for every forecast — near any variant's
            # footprint or the persisted one — so the variants are scored on
            # the same blocks.  Square neighbourhood via a separable max
            # filter: one pass, where an iterated binary dilation would cost
            # ``margin`` passes per tier.
            union = pers.copy()
            for mask in fc.values():
                union |= mask
            near = ndimage.maximum_filter(union.astype(np.uint8), size=2 * margin + 1) > 0
            area = near & cov_b
            variants = {
                v: {**_contingency(fc[v], observed, area),
                    "centroid_err_km_median": r(np.median(errs[v]), 2) if errs[v] else None}
                for v in MOTION_VARIANTS
            }
            rows_out.append({
                "verify_time": valid_time.isoformat(),
                "issued_time": issued_time.isoformat(),
                "lead_min": lead,
                "tier": tier_name,
                "policy_version": policy.policy_version,
                "cells_issued": n_cells,
                "cells_tracked": len(pers_errs),
                # Cells whose smoothed / track estimate used more than this
                # frame (the rest fell back to the raw vector).
                "cells_smoothed": n_smoothed,
                "cells_track": n_track,
                "field_available": field is not None,
                # ``extrapolation`` and ``centroid_err_km_median`` are the raw
                # straight line, as before #662; ``variants.raw`` repeats them.
                "extrapolation": {k: v for k, v in variants["raw"].items() if k != "centroid_err_km_median"},
                "persistence": _contingency(pers, observed, area),
                "centroid_err_km_median": variants["raw"]["centroid_err_km_median"],
                "persistence_centroid_err_km_median": r(np.median(pers_errs), 2) if pers_errs else None,
                "variants": variants,
            })
    return rows_out


def summarise(rows: list[dict]) -> list[dict]:
    """Median skill per (tier, lead, forecast) over score rows — the side-by-side #662 asks for.

    Medians over frames of each frame's CSI / POD / FAR and of each frame's
    median centroid error; ``frames`` counts the rows that had a value.
    Persistence is listed as one more forecast.
    """
    groups: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["tier"], row["lead_min"]), []).append(row)
    out = []
    for (tier, lead), group in sorted(groups.items()):
        forecasts: dict[str, list[dict]] = {}
        for row in group:
            variants = row.get("variants") or {
                "raw": {**row["extrapolation"], "centroid_err_km_median": row.get("centroid_err_km_median")}}
            for name, sc in variants.items():
                forecasts.setdefault(name, []).append(sc)
            forecasts.setdefault("persistence", []).append(
                {**row["persistence"], "centroid_err_km_median": row.get("persistence_centroid_err_km_median")})
        for name, scores in forecasts.items():
            entry = {"tier": tier, "lead_min": lead, "forecast": name, "frames": len(scores)}
            for key in ("csi", "pod", "far", "centroid_err_km_median"):
                vals = [s[key] for s in scores if s.get(key) is not None]
                entry[key] = r(np.median(vals), 3) if vals else None
            out.append(entry)
    return out


def read_scores(root: Path, start: datetime, end: datetime) -> list[dict]:
    """Score rows with ``verify_time`` in ``[start, end]``."""
    rows = []
    day = start.date()
    while day <= end.date():
        path = cells_dir(root) / "scores" / f"{day:%Y%m%d}.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if start <= datetime.fromisoformat(row["verify_time"]) <= end:
                    rows.append(row)
        day += timedelta(days=1)
    return rows


def append_scores(root: Path, valid_time: datetime, rows: list[dict]) -> None:
    if not rows:
        return
    path = cells_dir(root) / "scores" / f"{valid_time:%Y%m%d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
