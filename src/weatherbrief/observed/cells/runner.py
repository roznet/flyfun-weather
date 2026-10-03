"""The observed-cells loop: collect every frame, analyse every radar frame once.

Runs on a home compute node (the MacBook while the analysis is being built,
the Mac mini as the live loop), never on the droplet.  Everything lives under
``WB_CELLS_ROOT``::

    frames/<source>/<stamp>.{h5,nc,json}   archive — never purged (retain_all)
    catalogues/<day>/<stamp>.json.gz       one per DBZH frame (catalogue.py)
    scores/<day>.jsonl                     self-scoring rows (scoring.py)
    runs/<day>.jsonl                       one row per processed frame + gaps
    state.json                             last tick, last sweep

The collector is the shared one (``observed.collect``) pointed at an archive
store; only the lookback and fetch budget differ.  ``WB_CELLS_ROOT`` is
deliberately separate from ``DATA_DIR/observed`` so this loop and a dev
server's collector never purge or race each other's frames.

**Idempotent.**  A frame with a catalogue is never re-processed; a restart or
a catch-up after sleep simply fills what is missing, oldest first.
"""

from __future__ import annotations

import json
import logging
import os
import resource
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .. import ctth as ctth_reader
from .. import lightning as li_reader
from .. import opera
from ..collect import DEFAULT_LOOKBACK, collect_once, due_sources
from ..frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
    GridFrame,
    frame_stamp,
)
from ..grid import GridSpec, GridWindow, compute_window
from .attributes import cloud_tops, flash_counts, rate_peaks, same_grid
from .catalogue import SCHEMA, catalogue_path, failure_path, r, read_catalogue, write_catalogue
from .detect import TierDetection, detect, footprint_runs, initial_bearing_deg, distance_km
from .lineage import PreviousCell, link, trend, trim_history
from .motion import FlowField, cell_motion, estimate_flow
from .policy import DEFAULT_POLICY, CellPolicy
from .scoring import append_scores, score_frame

logger = logging.getLogger(__name__)

CELLS_ROOT_ENV = "WB_CELLS_ROOT"
CELLS_SOURCES_ENV = "WB_CELLS_SOURCES"
CELLS_CATCHUP_ENV = "WB_CELLS_CATCHUP_HOURS"
# Dead-man URL (healthchecks.io), pinged after a tick that analysed at least one
# frame, at most every HEALTHCHECK_EVERY. Unset → no ping. Its silence is the
# alarm: no radar arriving, a wedged loop, or a dead daemon all look the same.
CELLS_HEALTHCHECK_ENV = "WB_CELLS_HEALTHCHECK_URL"
HEALTHCHECK_EVERY = timedelta(minutes=5)

# CTTH is opt-in: ~54 MB a frame, ~7–8 GB a day, and the EUMETSAT data-volume
# quota with several consumers is unchecked.  A separate variable from
# WB_OBSERVED_SOURCES because a worktree's .env is shared with the dev
# server's collector, which defaults to every source.
DEFAULT_CELLS_SOURCES = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, SOURCE_EUMETSAT_LI)

# How far back a sweep re-checks for frames.  The OPERA open cache keeps 24 h;
# 6 h covers a night of laptop sleep without hammering the provider.
DEFAULT_CATCHUP = timedelta(hours=6)
SWEEP_EVERY = timedelta(minutes=15)
# After a sweep with failures: try again sooner than SWEEP_EVERY, but never on
# every tick — one permanently broken file in the lookback must not turn the
# loop into a once-a-minute full sweep against the providers.
SWEEP_RETRY = timedelta(minutes=5)
# Per-source fetch budget of one sweep.  OPERA files arrive in about a second
# each; an LI product took ~20 s through the EUMETSAT Data Store (3 in 60 s
# with the search, 2026-10-03) and a CTTH granule is ~54 MB, so the EUMETSAT
# backlog is spread over a few sweeps (12 per sweep: 6 h of LI in three)
# rather than holding a tick for many minutes.  An earlier "2 minutes per
# product" reading was the laptop idle-sleeping, not the provider.
SWEEP_MAX_FETCH_OPERA = 72
SWEEP_MAX_FETCH_EUMETSAT = 12
# A new DBZH frame waits this long for its RATE and lightning frames before it
# is analysed without them — otherwise the live loop would routinely publish
# cells with lightning "unavailable" because LI lands a few minutes after
# radar.  Older frames (catch-up) never wait.
ATTRIBUTE_WAIT = timedelta(minutes=15)
TICK_SECONDS = 60
# A failed frame is retried once, this long after its first failure; only a
# second failure is final (until `retry-failed`).
FAILURE_RETRY = timedelta(minutes=30)
FAILURE_MAX_ATTEMPTS = 2
_OPERA = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)
_EUMETSAT = (SOURCE_EUMETSAT_LI, SOURCE_EUMETSAT_CTTH)


def cells_root() -> Path:
    raw = os.environ.get(CELLS_ROOT_ENV, "").strip()
    if not raw:
        # Fail loudly: a default under ./data would silently write gigabytes
        # into whatever directory the loop was started from.
        raise RuntimeError(f"{CELLS_ROOT_ENV} is not set — the cells loop needs an explicit store root")
    return Path(raw).expanduser()


def cells_sources() -> tuple[str, ...]:
    raw = os.environ.get(CELLS_SOURCES_ENV, "").strip()
    if not raw:
        return DEFAULT_CELLS_SOURCES
    wanted = tuple(s.strip() for s in raw.split(",") if s.strip())
    unknown = [s for s in wanted if s not in SOURCE_SPECS]
    if unknown:
        raise ValueError(f"Unknown sources in {CELLS_SOURCES_ENV}: {unknown}")
    if SOURCE_OPERA_DBZH not in wanted:
        raise ValueError(f"{CELLS_SOURCES_ENV} must include {SOURCE_OPERA_DBZH} — cells are detected on it")
    return wanted


def catchup_lookback() -> timedelta:
    raw = os.environ.get(CELLS_CATCHUP_ENV, "").strip()
    return timedelta(hours=float(raw)) if raw else DEFAULT_CATCHUP


def grid_to_dict(grid: GridSpec) -> dict:
    return {"proj4": grid.proj4, "nx": grid.nx, "ny": grid.ny, "x0": grid.x0,
            "y0": grid.y0, "dx": grid.dx, "dy": grid.dy}


def grid_from_dict(data: dict) -> GridSpec:
    return GridSpec(**data)


_REVISION: list[str | None] = []


def code_revision() -> str | None:
    """Short git SHA of the checkout running the loop, or ``None``.

    Stamped on every catalogue because ``policy_version`` only sees the
    numbers: a behaviour change that forgot to bump ``CellPolicy.name`` is
    still traceable.  Informational only — lineage and scoring match on
    ``policy_version``, or every deploy would restart every storm's history.
    """
    if not _REVISION:
        import subprocess

        try:
            out = subprocess.run(
                ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short=12", "HEAD"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            _REVISION.append(out.stdout.strip() or None)
        except (OSError, subprocess.SubprocessError):
            _REVISION.append(None)
        if _REVISION[0] is None:
            logger.warning("observed-cells: no git checkout found — catalogues will carry "
                           "code_revision=null, so behaviour changes are traceable only "
                           "through CellPolicy.name")
    return _REVISION[0]


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MB.

    ``ru_maxrss`` is **bytes on macOS and KiB on Linux** — the classic trap
    when the same loop runs on the mini and on a Linux node.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


# --- Workspace ----------------------------------------------------------------


@dataclass
class Workspace:
    """Where catalogues/scores/runs are written, and which frames they read.

    ``frames_root`` differs from ``root`` only for a replay, which reads the
    archive and writes its results somewhere else.
    """

    root: Path
    frames_root: Path | None = None
    frames: FrameStore = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.frames = FrameStore(Path(self.frames_root or self.root / "frames"), retain_all=True)

    def log_run(self, when: datetime, row: dict) -> None:
        path = self.root / "runs" / f"{when:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def load_state(self) -> dict:
        try:
            return json.loads((self.root / "state.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "state.json.tmp"
        tmp.write_text(json.dumps(state, sort_keys=True, default=str))
        os.replace(tmp, self.root / "state.json")


# --- Frame access -----------------------------------------------------------


class FrameCache:
    """The last few decoded DBZH frames and their detections.

    Consecutive frames reuse each other (flow pair, lineage predecessor), so a
    sequential run decodes each frame once.  A cache miss just re-reads —
    detection is deterministic, so the result is identical either way.

    Sized for one step: the frame, its lineage predecessor (−5) and its flow
    pair (−10/−15) — four frames, two detection sets.  At 3800 × 4400 a frame
    is ~120 MB decoded and a detection set ~200 MB, so this is the loop's main
    memory cost.
    """

    def __init__(self, store: FrameStore, size: int = 4, det_size: int = 2) -> None:
        self.store = store
        self.size = size
        self.det_size = det_size
        self._frames: dict[datetime, GridFrame | None] = {}
        self._dets: dict[tuple[datetime, str], dict[str, TierDetection]] = {}

    def dbzh(self, t: datetime) -> GridFrame | None:
        if t not in self._frames:
            self._frames[t] = read_dbzh(self.store, t)
            while len(self._frames) > self.size:
                self._frames.pop(min(self._frames))
        return self._frames[t]

    def detections(self, t: datetime, policy: CellPolicy) -> dict[str, TierDetection] | None:
        key = (t, policy.policy_version)
        if key not in self._dets:
            frame = self.dbzh(t)
            if frame is None:
                return None
            self._dets[key] = {tier.name: detect(frame, tier) for tier in policy.tiers}
            while len(self._dets) > self.det_size:
                self._dets.pop(min(self._dets))
        return self._dets[key]


def read_dbzh(store: FrameStore, t: datetime) -> GridFrame | None:
    if not store.has(SOURCE_OPERA_DBZH, t):
        return None
    path = store.payload_path(SOURCE_OPERA_DBZH, t)
    try:
        grid = opera.read_grid(path)
        return opera.read_window(
            path, "DBZH", GridWindow(0, grid.ny, 0, grid.nx),
            source=SOURCE_OPERA_DBZH, units="dBZ",
        )
    except Exception:
        logger.warning("Unreadable DBZH frame %s", path, exc_info=True)
        return None


def _slot(t: datetime, minutes: int) -> datetime:
    t = t.replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % minutes)


def _rate_frame(store: FrameStore, t: datetime, like: GridFrame):
    slot = _slot(t, 15)
    if not store.has(SOURCE_OPERA_RATE, slot):
        return None, "no RATE frame for this slot"
    path = store.payload_path(SOURCE_OPERA_RATE, slot)
    try:
        grid = opera.read_grid(path)
        if grid.proj4 != like.grid.proj4:
            return None, "RATE projection differs from DBZH"
        frame = opera.read_window(path, "RATE", GridWindow(0, grid.ny, 0, grid.nx),
                                  source=SOURCE_OPERA_RATE, units="mm/h")
    except Exception as exc:
        return None, f"unreadable RATE frame: {exc}"
    return frame, None


def _li_frame(store: FrameStore, t: datetime):
    slot = _slot(t, 10)
    if not store.has(SOURCE_EUMETSAT_LI, slot):
        return None, "no lightning frame for this slot"
    try:
        return li_reader.read_flashes(
            store.payload_path(SOURCE_EUMETSAT_LI, slot),
            source=SOURCE_EUMETSAT_LI, window_minutes=10.0,
        ), None
    except Exception as exc:
        return None, f"unreadable lightning frame: {exc}"


def _ctth_frame(store: FrameStore, t: datetime, detections: dict[str, TierDetection]):
    from ..ctth import parallax_pad_km

    lats, lons = [], []
    for det in detections.values():
        for cell in det.cells:
            s, w, n, e = cell.bbox
            lats += [s, n]
            lons += [w, e]
    if not lats:
        return None, None
    for slot in (_slot(t, 10), _slot(t, 10) - timedelta(minutes=10)):
        if not store.has(SOURCE_EUMETSAT_CTTH, slot):
            continue
        try:
            meta = json.loads(store.sidecar_path(SOURCE_EUMETSAT_CTTH, slot).read_text())
            grid = grid_from_dict(meta["grid"])
            window = compute_window(
                grid, lats, lons, radius_km=0.0,
                pad_km=parallax_pad_km(lats, lons), full_width=True,
            )
            if window.is_empty():
                return None, "cells outside the CTTH disc"
            return ctth_reader.read_window(
                store.payload_path(SOURCE_EUMETSAT_CTTH, slot), window,
                source=SOURCE_EUMETSAT_CTTH,
            ), None
        except Exception as exc:
            return None, f"unreadable CTTH frame: {exc}"
    return None, "no CTTH frame for this slot"


# --- One frame ----------------------------------------------------------------


def _finalize_motion(mo, event: str, cell, grid: GridSpec, policy: CellPolicy) -> dict:
    out = {"status": mo.status, "reason": mo.reason, "support": r(mo.support, 2),
           "speed_kt": None, "toward_deg": None, "drow_per_min": None, "dcol_per_min": None}
    if mo.status != "pending":
        return out
    if mo.support < policy.min_motion_support or mo.drow_per_min is None:
        out.update(status="unsupported",
                   reason=f"only {mo.support:.0%} of the cell lies in matched tiles")
        return out
    if event in ("split", "merge"):
        out.update(status="withheld", reason=f"{event} on this frame")
        return out
    lon1, lat1 = grid.colrow_to_lonlat(
        cell.centroid_col + mo.dcol_per_min * 60.0, cell.centroid_row + mo.drow_per_min * 60.0
    )
    km_per_h = distance_km(cell.lat, cell.lon, float(lat1), float(lon1))
    speed_kt = km_per_h / 1.852
    out.update(
        status="available",
        speed_kt=r(speed_kt, 1),
        toward_deg=r(initial_bearing_deg(cell.lat, cell.lon, float(lat1), float(lon1)), 0)
        if speed_kt >= 1.0 else None,
        drow_per_min=r(mo.drow_per_min, 4),
        dcol_per_min=r(mo.dcol_per_min, 4),
    )
    return out


def process_frame(
    ws: Workspace,
    valid_time: datetime,
    policy: CellPolicy = DEFAULT_POLICY,
    *,
    cache: FrameCache | None = None,
    sources: tuple[str, ...] = DEFAULT_CELLS_SOURCES,
) -> dict | None:
    """Analyse one DBZH frame and write its catalogue.  ``None`` if absent."""
    started = time.perf_counter()
    cache = cache or FrameCache(ws.frames)
    frame = cache.dbzh(valid_time)
    if frame is None:
        return None
    grid = frame.grid
    detections = cache.detections(valid_time, policy)
    unavailable: list[dict] = []
    inputs: dict = {SOURCE_OPERA_DBZH: valid_time.isoformat()}

    # Motion field from the preferred pair spacing that exists.
    flow: FlowField | None = None
    inputs["flow_pair"] = None
    for minutes in policy.pair_minutes:
        earlier_t = valid_time - timedelta(minutes=minutes)
        earlier = cache.dbzh(earlier_t)
        if earlier is None or not same_grid(earlier, frame):
            continue
        flow = estimate_flow(earlier, frame, policy, minutes)
        inputs["flow_pair"] = {"earlier": earlier_t.isoformat(), "minutes": minutes}
        break
    if flow is None:
        unavailable.append({"what": "motion", "reason": "no earlier DBZH frame to pair with"})

    # Lineage predecessor: the nearest earlier catalogue under the same policy.
    prev_t = prev_cat = prev_dets = None
    for gap in range(5, policy.lineage_max_gap_minutes + 1, 5):
        t = valid_time - timedelta(minutes=gap)
        cat = read_catalogue(catalogue_path(ws.root, t))
        if cat is not None and cat.get("policy_version") == policy.policy_version:
            dets = cache.detections(t, policy)
            if dets is not None:
                prev_t, prev_cat, prev_dets = t, cat, dets
                break
    inputs["lineage_from"] = prev_t.isoformat() if prev_t else None
    if prev_t is None:
        # Say so: every cell below is "born" here because there was no
        # predecessor to link to, not because the weather started now.
        unavailable.append({
            "what": "lineage",
            "reason": f"no catalogue under this policy in the previous "
                      f"{policy.lineage_max_gap_minutes} min (missing or failed frame, or a fresh start)",
        })

    rate = li = ctth = None
    if SOURCE_OPERA_RATE in sources:
        rate, why = _rate_frame(ws.frames, valid_time, frame)
        if why:
            unavailable.append({"what": "rate", "reason": why})
    if SOURCE_EUMETSAT_LI in sources:
        li, why = _li_frame(ws.frames, valid_time)
        if why:
            unavailable.append({"what": "lightning", "reason": why})
    if SOURCE_EUMETSAT_CTTH in sources:
        ctth, why = _ctth_frame(ws.frames, valid_time, detections)
        if why:
            unavailable.append({"what": "cloud_top", "reason": why})
    inputs[SOURCE_OPERA_RATE] = rate.valid_time.isoformat() if rate else None
    inputs[SOURCE_EUMETSAT_LI] = li.valid_time.isoformat() if li else None
    inputs[SOURCE_EUMETSAT_CTTH] = ctth.valid_time.isoformat() if ctth else None

    cells_out: list[dict] = []
    for tier in policy.tiers:
        det = detections[tier.name]
        n = len(det.cells)
        block_px = tier.block_px(grid.pixel_km)
        prev_map = None
        if prev_cat is not None:
            prev_map = {
                c["label"]: PreviousCell(
                    c["id"], c["label"], c["lineage"]["born_at"], c["history"],
                    c["motion"]["drow_per_min"], c["motion"]["dcol_per_min"],
                )
                for c in prev_cat["cells"] if c["tier"] == tier.name
            }
        gap_min = (valid_time - prev_t).total_seconds() / 60.0 if prev_t else 0.0
        lineage = link(det, prev_dets[tier.name] if prev_dets else None, prev_map,
                       gap_min, valid_time, policy)
        rates = rate_peaks(det, grid, rate) if rate is not None else [None] * n
        flashes = flash_counts(det, grid, li, policy.flash_buffer_km) if li is not None else [None] * n
        tops = cloud_tops(det, grid, ctth) if ctth is not None else [(None, None)] * n

        for k, cell in enumerate(det.cells):
            lin = lineage[cell.label]
            motion = _finalize_motion(
                cell_motion(flow, det.labels, cell.label, cell.slice_rows, cell.slice_cols),
                lin.event, cell, grid, policy,
            )
            now_entry = [valid_time.isoformat(), r(cell.peak_dbz, 1), r(cell.area_km2, 1), flashes[k]]
            history = trim_history(lin.history, valid_time, policy)
            age_min = (valid_time - datetime.fromisoformat(lin.born_at)).total_seconds() / 60.0
            cells_out.append({
                "id": lin.id,
                "tier": tier.name,
                "label": cell.label,
                "lat": r(cell.lat, 4),
                "lon": r(cell.lon, 4),
                "row": r(cell.centroid_row, 2),
                "col": r(cell.centroid_col, 2),
                "area_km2": r(cell.area_km2, 1),
                "peak_dbz": r(cell.peak_dbz, 1),
                "truncated": cell.truncated,
                "ellipse": {"major_km": r(cell.major_km, 1), "minor_km": r(cell.minor_km, 1),
                            "orientation_deg": r(cell.orientation_deg, 0)},
                "bbox": [r(v, 4) for v in cell.bbox],
                "footprint": {"block_px": block_px,
                              "runs": footprint_runs(det, cell, block_px)},
                "rate_peak_mm_h": r(rates[k], 1),
                "flashes": flashes[k],
                "top_fl": r(tops[k][0], 0),
                "top_px": tops[k][1],
                "motion": motion,
                "lineage": {"born_at": lin.born_at, "age_min": r(age_min, 1),
                            "event": lin.event, "parents": lin.parents},
                "trend": trend(history, now_entry, valid_time, policy),
                "history": history + [now_entry],
            })

    covered = ~np.asarray(frame.nodata)
    catalogue = {
        "schema": SCHEMA,
        "policy_version": policy.policy_version,
        "code_revision": code_revision(),
        "policy": policy.as_dict(),
        "valid_time": valid_time.isoformat(),
        "window_minutes": frame.window_minutes,
        "grid": grid_to_dict(grid),
        "window": [frame.window.row0, frame.window.row1, frame.window.col0, frame.window.col1],
        "inputs": inputs,
        "coverage": {
            "covered_fraction": r(covered.mean(), 4),
            "flow_tiles_tried": flow.tried if flow else 0,
            "flow_tiles_matched": flow.matched if flow else 0,
        },
        "unavailable": unavailable,
        "cells": cells_out,
    }
    size = write_catalogue(catalogue_path(ws.root, valid_time), catalogue)

    # The catalogue is written; a scoring failure must not turn it into a
    # failed frame.  Recorded on the run row instead.
    scoring_error = None
    try:
        scores = score_frame(ws.root, valid_time, grid, detections, covered, catalogue,
                             policy.score_leads_minutes, policy.policy_version, policy.score_margin_km)
        append_scores(ws.root, valid_time, scores)
    except Exception as exc:
        logger.exception("Scoring failed for %s", valid_time)
        scores, scoring_error = [], repr(exc)

    summary = {
        "type": "frame",
        "valid_time": valid_time.isoformat(),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "seconds": round(time.perf_counter() - started, 3),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "catalogue_bytes": size,
        "cells": {t.name: len(detections[t.name].cells) for t in policy.tiers},
        "with_motion": sum(1 for c in cells_out if c["motion"]["status"] == "available"),
        "flow_tiles": [catalogue["coverage"]["flow_tiles_matched"], catalogue["coverage"]["flow_tiles_tried"]],
        "unavailable": [u["what"] for u in unavailable],
        "scores": len(scores),
        "scoring_error": scoring_error,
        "policy_version": policy.policy_version,
    }
    ws.log_run(valid_time, summary)
    return summary


# --- Loop -----------------------------------------------------------------------


def dbzh_slots(start: datetime, end: datetime) -> list[datetime]:
    """Every 5-minute DBZH slot in ``[start, end]``, oldest first."""
    t = _slot(start, 5)
    if t < start:
        t += timedelta(minutes=5)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(minutes=5)
    return out


def collect_tick(ws: Workspace, sources: tuple[str, ...], now: datetime, *, sweep: bool,
                 lookback: timedelta, family: tuple[str, ...]):
    """Collect one family of sources (OPERA, or EUMETSAT) for this tick."""
    wanted = tuple(s for s in sources if s in family)
    if not wanted:
        return []
    if sweep:
        budget = SWEEP_MAX_FETCH_OPERA if family == _OPERA else SWEEP_MAX_FETCH_EUMETSAT
        return collect_once(ws.frames, now=now, sources=wanted, lookback=lookback, max_fetch=budget)
    due = due_sources(ws.frames, now=now, sources=wanted)
    if not due:
        return []
    return collect_once(ws.frames, now=now, sources=tuple(due), lookback=DEFAULT_LOOKBACK)


def attributes_ready(store: FrameStore, t: datetime, sources: tuple[str, ...]) -> bool:
    if SOURCE_OPERA_RATE in sources and not store.has(SOURCE_OPERA_RATE, _slot(t, 15)):
        return False
    if SOURCE_EUMETSAT_LI in sources and not store.has(SOURCE_EUMETSAT_LI, _slot(t, 10)):
        return False
    return True


def analyse_tick(ws: Workspace, now: datetime, lookback: timedelta, policy: CellPolicy,
                 cache: FrameCache, sources: tuple[str, ...]) -> int:
    done = 0
    for t in dbzh_slots(now - lookback, now):
        if catalogue_path(ws.root, t).exists() or not ws.frames.has(SOURCE_OPERA_DBZH, t):
            continue
        marker = read_failure(ws, t)
        if marker is not None and not failure_retry_due(marker, now):
            continue
        if now - t < ATTRIBUTE_WAIT and not attributes_ready(ws.frames, t, sources):
            # Stop here rather than skip: a later frame analysed first would
            # start its lineage without this one.
            break
        error = None
        try:
            summary = process_frame(ws, t, policy, cache=cache, sources=sources)
            if summary is None:
                error = "DBZH frame present but unreadable"
        except Exception as exc:
            logger.exception("Cell analysis failed for %s", t)
            summary, error = None, repr(exc)
        if error is not None:
            mark_failed(ws, t, error, now)
            continue
        if summary:
            done += 1
            logger.info(
                "cells %s: %s motion=%d tiles=%s %.1fs rss=%.0fMB %dB missing=%s",
                frame_stamp(t), summary["cells"], summary["with_motion"],
                summary["flow_tiles"], summary["seconds"], summary["peak_rss_mb"],
                summary["catalogue_bytes"], ",".join(summary["unavailable"]) or "-",
            )
    return done


def retry_failed(ws: Workspace) -> int:
    """Clear every failure marker so the loop tries those frames again."""
    removed = 0
    for marker in (ws.root / "catalogues").glob("*/*.failed.json"):
        marker.unlink(missing_ok=True)
        removed += 1
    return removed


def read_failure(ws: Workspace, t: datetime) -> dict | None:
    path = failure_path(ws.root, t)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        # An unreadable marker still means "failed": keep it final.
        return {"attempts": FAILURE_MAX_ATTEMPTS}


def failure_retry_due(marker: dict, now: datetime) -> bool:
    if marker.get("attempts", FAILURE_MAX_ATTEMPTS) >= FAILURE_MAX_ATTEMPTS:
        return False
    failed_at = marker.get("processed_at")
    return bool(failed_at) and now - datetime.fromisoformat(failed_at) >= FAILURE_RETRY


def mark_failed(ws: Workspace, t: datetime, error: str, now: datetime | None = None) -> None:
    """Record a failed attempt: once in the run log, once on disk.

    Never raises — a disk-full or permission error here must not escape the
    tick (the frame would then be retried every minute with no marker).
    """
    previous = read_failure(ws, t) or {}
    attempts = int(previous.get("attempts", 0)) + 1
    final = attempts >= FAILURE_MAX_ATTEMPTS
    logger.error("Cell analysis failed on %s (attempt %d%s): %s", frame_stamp(t), attempts,
                 ", giving up" if final else f", retrying in {FAILURE_RETRY}", error)
    row = {"type": "error", "valid_time": t.isoformat(), "error": error, "attempts": attempts,
           "final": final,
           "processed_at": (now or datetime.now(timezone.utc)).isoformat()}
    try:
        path = failure_path(ws.root, t)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, sort_keys=True))
        ws.log_run(t, row)
    except OSError:
        logger.exception("Could not record the failure of %s", frame_stamp(t))


def ping_healthcheck(state: dict, now: datetime) -> None:
    """Ping the dead-man URL; never raises, never logs the URL (it is a secret)."""
    url = os.environ.get(CELLS_HEALTHCHECK_ENV, "").strip()
    if not url:
        return
    last = state.get("last_ping")
    if last and now - datetime.fromisoformat(last) < HEALTHCHECK_EVERY:
        return
    try:
        import requests

        requests.get(url, timeout=10)
        state["last_ping"] = now.isoformat()
    except Exception as exc:
        logger.warning("Healthcheck ping failed: %s", type(exc).__name__)


def record_downtime(ws: Workspace, state: dict, now: datetime, lookback: timedelta) -> None:
    """Log a gap when the loop was down longer than a sweep can recover."""
    last = state.get("last_tick")
    if not last:
        return
    last_t = datetime.fromisoformat(last)
    if now - last_t > lookback:
        ws.log_run(now, {"type": "gap", "from": last_t.isoformat(),
                         "until": (now - lookback).isoformat(),
                         "reason": "loop down longer than the catch-up lookback"})


def run_tick(ws: Workspace, policy: CellPolicy, cache: FrameCache, sources: tuple[str, ...],
             lookback: timedelta, now: datetime | None = None) -> None:
    now_is_live = now is None
    now = now or datetime.now(timezone.utc)
    state = ws.load_state()
    record_downtime(ws, state, now, lookback)
    last_sweep = state.get("last_sweep")
    last_try = state.get("last_sweep_attempt")
    sweep = not last_sweep or now - datetime.fromisoformat(last_sweep) >= SWEEP_EVERY
    if sweep and last_try and now - datetime.fromisoformat(last_try) < SWEEP_RETRY:
        sweep = False  # the last attempt failed recently: back off
    # Radar first and analysed straight away; the slow EUMETSAT downloads come
    # after, so they never hold the newest radar frame back.
    collected_ok = True
    analysed = 0
    for family in (_OPERA, _EUMETSAT):
        fetched = 0
        try:
            for res in collect_tick(ws, sources, now, sweep=sweep, lookback=lookback, family=family):
                fetched += res.fetched
                if res.failed:
                    collected_ok = False
                    if sweep:
                        logger.warning("sweep: %s failed %d item(s): %s", res.source, res.failed,
                                       "; ".join(res.errors[:3]))
                if res.fetched or res.failed:
                    logger.info("collect %s: fetched=%d missing=%d failed=%d %s", res.source,
                                res.fetched, res.missing, res.failed, "; ".join(res.errors[:2]))
        except Exception:
            collected_ok = False
            logger.exception("Collection failed for %s", ",".join(family))
        # After EUMETSAT, only re-scan when it brought something a waiting
        # frame could use.
        if family == _OPERA or fetched:
            analysed += analyse_tick(ws, datetime.now(timezone.utc) if now_is_live else now,
                                     lookback, policy, cache, sources)
    if sweep:
        failed = [t for t in dbzh_slots(now - lookback, now) if failure_path(ws.root, t).exists()]
        if failed:
            logger.warning("%d frame(s) in the last %s marked failed (status lists them; "
                           "retry-failed clears them)", len(failed), lookback)
    # `last_tick` means "the loop was alive" (what the downtime gap measures);
    # `last_sweep` only advances when the catch-up actually worked, so a failed
    # sweep is retried after SWEEP_RETRY rather than 15 minutes later.
    state["last_tick"] = now.isoformat()
    if analysed:
        ping_healthcheck(state, now)
    if sweep:
        state["last_sweep_attempt"] = now.isoformat()
        if collected_ok:
            state["last_sweep"] = now.isoformat()
    ws.save_state(state)


def run_forever(ws: Workspace, policy: CellPolicy = DEFAULT_POLICY, *,
                sources: tuple[str, ...] = DEFAULT_CELLS_SOURCES,
                lookback: timedelta = DEFAULT_CATCHUP) -> None:
    cache = FrameCache(ws.frames)
    logger.info("observed-cells loop: root=%s sources=%s policy=%s", ws.root, ",".join(sources),
                policy.policy_version)
    while True:
        started = time.monotonic()
        try:
            run_tick(ws, policy, cache, sources, lookback)
        except Exception:
            logger.exception("Cells tick failed")
        time.sleep(max(1.0, TICK_SECONDS - (time.monotonic() - started)))


# --- Replay ---------------------------------------------------------------------


def replay(src_root: Path, out_root: Path, start: datetime, end: datetime,
           policy: CellPolicy = DEFAULT_POLICY, *, force: bool = False,
           sources: tuple[str, ...] = DEFAULT_CELLS_SOURCES) -> int:
    """Re-run the analysis over archived frames into ``out_root``.

    Never writes into the live root.  ``force`` clears earlier replay output in
    ``out_root`` first, so scores are not appended twice.
    """
    src_root = Path(src_root).resolve()
    out_root = Path(out_root).resolve()
    if out_root == src_root:
        raise ValueError("replay output must not be the live cells root")
    existing = [out_root / "catalogues", out_root / "scores", out_root / "runs"]
    if any(p.exists() for p in existing):
        if not force:
            raise FileExistsError(f"{out_root} already holds replay output; pass --force to replace it")
        for p in existing:
            shutil.rmtree(p, ignore_errors=True)
    ws = Workspace(out_root, frames_root=src_root / "frames")
    cache = FrameCache(ws.frames)
    done = failed = 0
    for t in dbzh_slots(start, end):
        try:
            if process_frame(ws, t, policy, cache=cache, sources=sources):
                done += 1
        except Exception as exc:
            # One bad frame must not lose a replay over days: count, log, go on.
            failed += 1
            logger.exception("Replay failed on %s", frame_stamp(t))
            ws.log_run(t, {"type": "error", "valid_time": t.isoformat(), "error": repr(exc),
                           "replay": True})
    if failed:
        logger.warning("Replay: %d frame(s) failed, see %s/runs", failed, out_root)
    return done


def coverage_report(ws: Workspace, start: datetime, end: datetime, sources: tuple[str, ...]) -> dict:
    """Expected vs stored frames per source, with the missing runs as ranges."""
    report = {}
    for source in sources:
        spec = SOURCE_SPECS[source]
        step = int(spec.interval.total_seconds() // 60)
        t = _slot(start, step)
        expected = stored = 0
        gaps: list[list[str]] = []
        run_start = None
        while t <= end:
            expected += 1
            if ws.frames.has(source, t):
                stored += 1
                if run_start is not None:
                    gaps.append([run_start.isoformat(), (t - timedelta(minutes=step)).isoformat()])
                    run_start = None
            elif run_start is None:
                run_start = t
            t += timedelta(minutes=step)
        if run_start is not None:
            gaps.append([run_start.isoformat(), (t - timedelta(minutes=step)).isoformat()])
        report[source] = {"expected": expected, "stored": stored, "gaps": gaps}
    slots = dbzh_slots(start, end)
    report["catalogues"] = sum(1 for t in slots if catalogue_path(ws.root, t).exists())
    report["failed"] = [
        {"valid_time": t.isoformat(), **(read_failure(ws, t) or {})}
        for t in slots if failure_path(ws.root, t).exists()
    ]
    return report


__all__ = [
    "Workspace", "FrameCache", "process_frame", "run_tick", "run_forever", "replay",
    "coverage_report", "retry_failed", "cells_root", "cells_sources", "catchup_lookback",
    "grid_from_dict", "grid_to_dict", "code_revision",
]
