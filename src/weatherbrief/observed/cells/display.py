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
* **clutter** — on a cell with non-meteorological-echo evidence (#696): its
  ``level`` (``suspect``/``confirmed``), ``score`` and the ``reasons`` that
  fired.  Absent on a clear cell, so clean frames keep the pre-#696 shape.
  ``robust_peak_dbz`` (the 3x3-median peak) rides along on every assessed
  cell; nothing displays or alerts on it yet;
* **suspect_outlines** — ``{tier: [index, ...]}`` into that tier's outlines,
  for the rings of an *unassessed* tier (``rain20``) that are a suspect
  echo's own skirt rather than weather (#702, see :func:`suspect_regions`).
  The rings stay in ``outlines`` so every map keeps drawing them; the
  droplet's route products skip them when suppression is on.  Absent on a
  frame with none;
* **within** — on a cell inside a cell of the next lower tier (a core41
  inside its core35, a core35 inside its rain20), that cell's id (#688), so
  the droplet groups the tiers of one storm without re-deriving geometry;
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
from .clutter import assessable
from .detect import TierDetection, distance_km, initial_bearing_deg
from .levels import suspect
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
             step: int, digits: int = 3, keep: np.ndarray | None = None) -> list:
    """Cell boundaries in the crop as ``[[lat, lon], ...]`` polylines.

    ``keep`` is a boolean lookup indexed by label: only those cells are traced
    (``keep[0]`` must be False).  ``None`` traces every cell."""
    import contourpy

    crop = det.labels[r0:r1, c0:c1]
    mask = crop > 0 if keep is None else keep[crop]
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


def enclosing_cells(catalogue_cells: list[dict], detections: dict[str, TierDetection],
                    policy: CellPolicy) -> dict[str, str]:
    """Cell id -> id of the cell of the next lower tier that contains it (#688).

    The tiers are nested thresholds on one frame, so every core41 pixel is a
    core35 pixel: the enclosing cell is read off the lower tier's labels under
    the cell's own pixels (the most common label, in case of a tie the lowest).
    A cell whose lower-tier region was dropped by that tier's minimum area has
    no entry."""
    ids = {(c["tier"], c["label"]): c["id"] for c in catalogue_cells if "label" in c}
    tiers = sorted((t for t in policy.tiers if t.name in detections), key=lambda t: t.threshold_dbz)
    out: dict[str, str] = {}
    for lower, upper in zip(tiers, tiers[1:]):
        lo, up = detections[lower.name], detections[upper.name]
        for cell in up.cells:
            own = ids.get((upper.name, cell.label))
            if own is None:
                continue
            sub = up.labels[cell.slice_rows, cell.slice_cols] == cell.label
            under = lo.labels[cell.slice_rows, cell.slice_cols][sub]
            under = under[under > 0]
            if under.size == 0:
                continue
            counts = np.bincount(under)
            parent = ids.get((lower.name, int(np.argmax(counts))))
            if parent is not None:
                out[own] = parent
    return out


def suspect_regions(catalogue_cells: list[dict], detections: dict[str, TierDetection],
                    policy: CellPolicy) -> dict[str, set[int]]:
    """Labels of the *unassessed* tiers' regions that are a suspect echo's own (#702).

    ``rain20`` cannot carry clutter evidence (``clutter.assessable``), so a
    clutter block's own bare rain skirt is judged by what it contains.  A
    region is the echo's own when all three hold:

    * it contains at least one core (a plain rain area has none, and is
      never touched);
    * **every** core in it is suspect (one genuine core makes it weather);
    * it is no bigger than ``rain_ratio_thin`` times those cores' own area —
      the same "barely any rain around the core" boundary the per-core score
      uses (``ClutterPolicy``), measured here over the region.  A genuine rain
      area holding a suspect core is bigger than that, keeps its band, and
      gets its intensity from what is left (the droplet's own member filter).

    Cores are the cells of the assessed tiers, read off their labels under
    the region; a core-tier blob below that tier's minimum area is no cell and
    is not counted either way.
    """
    flagged = {(c["tier"], c["label"]) for c in catalogue_cells if "label" in c and suspect(c)}
    if not flagged:
        return {}
    assessed = [t for t in policy.tiers if t.name in detections and assessable(t, policy.clutter)]
    out: dict[str, set[int]] = {}
    for tier in policy.tiers:
        if tier.name not in detections or assessable(tier, policy.clutter):
            continue
        rain = detections[tier.name]
        hosts: set[int] = set()
        for up in assessed:
            det = detections[up.name]
            for cell in det.cells:
                if (up.name, cell.label) not in flagged:
                    continue
                sub = det.labels[cell.slice_rows, cell.slice_cols] == cell.label
                under = rain.labels[cell.slice_rows, cell.slice_cols][sub]
                hosts.update(int(v) for v in np.unique(under[under > 0]))
        by_label = rain.cell_by_label()
        found = set()
        for label in sorted(hosts):
            region_cell = by_label.get(label)
            if region_cell is None:
                continue
            sl = (region_cell.slice_rows, region_cell.slice_cols)
            region = rain.labels[sl] == label
            core_px = np.zeros(region.shape, dtype=bool)
            genuine = False
            for up in assessed:
                labs = detections[up.name].labels[sl]
                inside = labs[region]
                if any((up.name, int(v)) not in flagged for v in np.unique(inside[inside > 0])):
                    genuine = True
                    break
                core_px |= region & (labs > 0)
            n_core = int(core_px.sum())
            if genuine or n_core == 0:
                continue
            if int(region.sum()) <= policy.clutter.rain_ratio_thin * n_core:
                found.add(label)
        if found:
            out[tier.name] = found
    return out


def tier_outlines(det: TierDetection, grid: GridSpec, step: int,
                  suspect_labels: set[int] | None = None) -> tuple[list, list[int]]:
    """One tier's outlines over the whole window, plus the indices of the rings
    traced from ``suspect_labels`` (#702).

    With no suspect labels this is exactly the single trace it always was, so
    a clean frame's display file does not change by a byte.  Otherwise the
    rest of the tier is traced first and the suspect regions after it, so
    their rings are a known tail of the list.
    """
    ny, nx = det.labels.shape
    if not suspect_labels:
        return outlines(det, grid, 0, ny, 0, nx, step), []
    bad = np.zeros(int(det.labels.max()) + 1, dtype=bool)
    bad[sorted(suspect_labels)] = True
    good = ~bad
    good[0] = False
    rest = outlines(det, grid, 0, ny, 0, nx, step, keep=good)
    own = outlines(det, grid, 0, ny, 0, nx, step, keep=bad)
    return rest + own, list(range(len(rest), len(rest) + len(own)))


def display_cell(cell: dict, grid: GridSpec, *, variant: str = "raw", field: MotionField | None = None,
                 policy: CellPolicy | None = None, flashes_pending: bool = False,
                 rate_as_of: str | None = None, within: str | None = None) -> dict:
    """One catalogue cell reduced to what the map shows.

    ``flashes_pending`` / ``rate_as_of`` / ``within`` are added only when set,
    so a frame with all its inputs keeps the pre-#666 cell shape."""
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
    if within is not None:
        out["within"] = within
    # Clutter evidence (#696) rides along only when there is something to say:
    # a clear cell keeps the pre-#696 shape, and a continent of clean cores
    # does not pay for a block per cell.  The reasons travel too — a client
    # that dims a cell has to be able to say why.
    clutter = cell.get("clutter")
    if clutter and clutter.get("level") != "clear":
        out["clutter"] = {k: clutter[k] for k in ("level", "score", "reasons") if k in clutter}
    # Robust peak alongside the single-pixel one; nothing reads it yet.
    robust = (clutter or {}).get("features", {}).get("robust_peak_dbz")
    if robust is not None:
        out["robust_peak_dbz"] = robust
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
    within = enclosing_cells(catalogue["cells"], detections, policy)
    suspect_labels = suspect_regions(catalogue["cells"], detections, policy)
    traced = {
        tier.name: tier_outlines(detections[tier.name], grid, outline_step(tier.name, big),
                                 suspect_labels.get(tier.name))
        for tier in policy.tiers if tier.name in detections
    }
    out = {
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
        "outlines": {name: lines for name, (lines, _) in traced.items()},
        "motion_variant": variant,
        "cells": [display_cell(c, grid, variant=variant, field=field, policy=policy,
                               flashes_pending=lightning_pending, rate_as_of=rate_as_of,
                               within=within.get(c["id"]))
                  for c in catalogue["cells"] if shown(c)],
    }
    suspect_outlines = {name: idx for name, (_, idx) in traced.items() if idx}
    if suspect_outlines:
        # Absent on a clean frame, like the cells' clutter block (#702).
        out["suspect_outlines"] = suspect_outlines
    return out


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
