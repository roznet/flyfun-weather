"""The display file: one frame's cells, ready for a web map (#656).

Built on the home node right after the frame's catalogue, from the same
detections, and pushed to the droplet (``push.py``).  It is the *only* thing
that leaves the home node: the browser never sees run-length footprints, grid
pixels, histories or scores — those stay in the catalogue.

Contents (``DISPLAY_SCHEMA``):

* **outlines per tier** — the detection masks traced with ``contourpy`` into
  ``[[lat, lon], ...]`` polylines, coarser for rain20 (and coarser again on a
  Europe-sized grid) so the whole continent stays ~100 KB gzipped;
* **cells** — every core, plus rain areas of at least ``RAIN_MIN_AREA_KM2``,
  each with its attributes, trend, motion and the point where it would be in
  ``ARROW_MINUTES`` if its motion continued (only when motion is
  ``available``: withheld or unsupported motion has no arrow);
* **the frame's own times** — radar, rain rate, lightning, cloud top — plus
  ``rate_age_min`` (how much older the rain rate is than the radar: the
  newest RATE on disk is used rather than waiting for its slot, #666),
  ``unavailable``, ``policy_version`` and ``code_revision``;
* **pending** — inputs still expected (``["lightning"]`` until the frame's
  LI slot lands): *not* listed under ``unavailable``, and each cell carries
  ``flashes_pending: true``, so no client can word it as "no lightning"
  (#666 review).  Cells whose rain rate is from an older frame than the radar
  carry ``rate_as_of``;
* **revision** — 0 when first published; a frame whose lightning landed after
  it was published is re-issued as revision 1 (#666), each revision its own
  immutable file (``catalogue.display_path``).

Deterministic like the catalogue (sorted keys, rounded floats, gzip
``mtime=0``), so a replay reproduces it byte for byte.  Lightning flashes are
*not* in it: the droplet collects the same LI frames itself and the map draws
them from ``/api/observed/flashes``.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..frames import SOURCE_EUMETSAT_CTTH, SOURCE_EUMETSAT_LI, SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE
from ..grid import GridSpec
from .advect import MotionField, project_centroid
from .catalogue import encode, r
from .detect import TierDetection, distance_km, initial_bearing_deg
from .policy import CellPolicy

DISPLAY_SCHEMA = "observed-cells-display/1"
# Rain areas below this are outlined but get no marker: hundreds of small
# showers would bury the cores.  The prototype's floor (webmap.py).
RAIN_MIN_AREA_KM2 = 2000.0
# The `unavailable` reason of a frame whose lightning slot has not landed yet.
# The runner amends it within its window (a frame past that is stale on every
# map before it could matter), so the display calls it *pending*, never "none".
LIGHTNING_PENDING_REASON = "no lightning frame for this slot"
# The whole `unavailable` entry, shared with the runner's amend check so the two
# cannot drift (a mismatch would stop amends or stop showing "pending").
LIGHTNING_PENDING = {"what": "lightning", "reason": LIGHTNING_PENDING_REASON}
ARROW_MINUTES = 30
# Above this many pixels the grid is "Europe" and outlines are traced coarser.
_BIG_GRID_PX = 4_000_000


def outline_step(tier: str, big: bool) -> int:
    """Mask decimation for tracing: rain20 coarser, everything coarser on a big grid."""
    if big:
        return 4 if tier == "rain20" else 2
    return 2 if tier == "rain20" else 1


def outlines(det: TierDetection, grid: GridSpec, r0: int, r1: int, c0: int, c1: int,
             step: int, digits: int = 3) -> list:
    """Cell boundaries in the crop as ``[[lat, lon], ...]`` polylines."""
    import contourpy

    mask = det.labels[r0:r1, c0:c1] > 0
    if step > 1:
        h = (mask.shape[0] // step) * step
        w = (mask.shape[1] // step) * step
        mask = mask[:h, :w].reshape(h // step, step, w // step, step).any(axis=(1, 3))
    if not mask.any():
        return []
    padded = np.pad(mask.astype(np.float32), 1)
    lines = contourpy.contour_generator(z=padded).lines(0.5)
    out = []
    for line in lines:
        if len(line) < 4:
            continue
        cc = (line[:, 0] - 1) * step + (step - 1) / 2 + c0 + det.col0
        rr = (line[:, 1] - 1) * step + (step - 1) / 2 + r0 + det.row0
        lons, lats = grid.colrow_to_lonlat(cc, rr)
        out.append([[r(a, digits), r(b, digits)] for a, b in zip(lats, lons)])
    return out


def arrow_end(cell: dict, grid: GridSpec, minutes: float = ARROW_MINUTES, *,
              variant: str = "raw", field: MotionField | None = None,
              policy: CellPolicy | None = None) -> list | None:
    """Where the cell would be after ``minutes`` at its current motion, or ``None``.

    Only for ``available`` motion — never extrapolate a withheld (split/merge)
    or unsupported velocity.  ``variant`` is ``policy.display_motion`` (#662):
    a straight line at the raw, smoothed or track vector, or the end of a
    trajectory through the motion field.
    """
    m = cell["motion"]
    if m.get("status") != "available" or m.get("dcol_per_min") is None or m.get("drow_per_min") is None:
        return None
    end = project_centroid(cell, variant, minutes, field, policy or CellPolicy())
    if end is None:
        return None
    lon2, lat2 = grid.colrow_to_lonlat(end[1], end[0])
    return [r(lat2, 4), r(lon2, 4)]


def display_cell(cell: dict, grid: GridSpec, *, variant: str = "raw", field: MotionField | None = None,
                 policy: CellPolicy | None = None, flashes_pending: bool = False,
                 rate_as_of: str | None = None) -> dict:
    """One catalogue cell reduced to what the map shows.

    ``flashes_pending`` / ``rate_as_of`` are added only when set, so a frame
    with all its inputs keeps the pre-#666 cell shape."""
    m = cell["motion"]
    t = cell["trend"]
    arrow = arrow_end(cell, grid, variant=variant, field=field, policy=policy)
    motion = {k: m.get(k) for k in ("status", "reason", "speed_kt", "toward_deg")}
    if variant != "raw" and arrow is not None:
        # Speed and direction follow the arrow the map draws, not the raw vector.
        km = distance_km(cell["lat"], cell["lon"], arrow[0], arrow[1])
        speed_kt = km / (ARROW_MINUTES / 60.0) / 1.852
        motion["speed_kt"] = r(speed_kt, 1)
        motion["toward_deg"] = (r(initial_bearing_deg(cell["lat"], cell["lon"], arrow[0], arrow[1]), 0)
                                if speed_kt >= 1.0 else None)
    out = {
        "id": cell["id"],
        "tier": cell["tier"],
        "lat": cell["lat"],
        "lon": cell["lon"],
        "area_km2": cell["area_km2"],
        "peak_dbz": cell["peak_dbz"],
        "rate_peak_mm_h": cell["rate_peak_mm_h"],
        "flashes": cell["flashes"],
        "top_fl": cell["top_fl"],
        "truncated": cell["truncated"],
        "age_min": cell["lineage"]["age_min"],
        "event": cell["lineage"]["event"],
        "trend": {k: t.get(k) for k in ("state", "window_min", "d_peak_db", "area_ratio", "d_flashes")},
        "motion": motion,
        "arrow": arrow,
    }
    if flashes_pending and cell["flashes"] is None:
        out["flashes_pending"] = True
    if rate_as_of and cell["rate_peak_mm_h"] is not None:
        out["rate_as_of"] = rate_as_of
    return out


def shown(cell: dict) -> bool:
    return cell["tier"] != "rain20" or (cell["area_km2"] or 0) >= RAIN_MIN_AREA_KM2


def build_display(catalogue: dict, detections: dict[str, TierDetection], grid: GridSpec,
                  policy: CellPolicy, revision: int = 0) -> dict:
    """The display file for a catalogue just built from ``detections``."""
    any_det = next(iter(detections.values()), None)
    ny, nx = any_det.labels.shape if any_det is not None else (0, 0)
    big = ny * nx > _BIG_GRID_PX
    inputs = catalogue["inputs"]
    variant = policy.display_motion
    field = MotionField.from_dict(catalogue.get("flow"), policy) if variant.startswith("field") else None
    unavailable = catalogue.get("unavailable", [])
    lightning_pending = LIGHTNING_PENDING in unavailable
    rate_time = inputs.get(SOURCE_OPERA_RATE)
    rate_as_of = rate_time if rate_time and rate_time != inputs.get(SOURCE_OPERA_DBZH) else None
    return {
        "schema": DISPLAY_SCHEMA,
        "policy_version": catalogue["policy_version"],
        "code_revision": catalogue.get("code_revision"),
        "valid_time": catalogue["valid_time"],
        "window_minutes": catalogue.get("window_minutes"),
        "times": {
            "radar": inputs.get(SOURCE_OPERA_DBZH),
            "rate": inputs.get(SOURCE_OPERA_RATE),
            "lightning": inputs.get(SOURCE_EUMETSAT_LI),
            "cloud_top": inputs.get(SOURCE_EUMETSAT_CTTH),
        },
        "rate_age_min": inputs.get("rate_age_min"),
        "revision": revision,
        "pending": ["lightning"] if lightning_pending else [],
        "unavailable": [u for u in unavailable if u != LIGHTNING_PENDING],
        "rain_min_area_km2": RAIN_MIN_AREA_KM2,
        "arrow_minutes": ARROW_MINUTES,
        "outlines": {
            tier.name: outlines(detections[tier.name], grid, 0, ny, 0, nx, outline_step(tier.name, big))
            for tier in policy.tiers if tier.name in detections
        },
        "motion_variant": variant,
        "cells": [display_cell(c, grid, variant=variant, field=field, policy=policy,
                               flashes_pending=lightning_pending, rate_as_of=rate_as_of)
                  for c in catalogue["cells"] if shown(c)],
    }


def write_display(path: Path, display: dict) -> int:
    """Write atomically, world-readable (it is rsynced to another machine and
    read there by a different user — ``mkstemp``'s 0600 would travel with it)."""
    import tempfile

    data = encode(display)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)
