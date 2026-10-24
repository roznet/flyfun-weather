"""Hewson diagnostic snapshot precompute pipeline.

Core flow, shared by the scheduler loop and the ``python -m weatherbrief.hewson
precompute`` CLI:

    1. build / load terrain mask (cached to disk)
    2. for each model:
         a. resolve the model's GRIB run (ECMWF a2 on local disk, GFS on
            NOAA S3, ICON-EU on DWD open data — ``grib_source.py``, #911) and
            read 925/850/700 hPa T/RH/u/v per forecast step on the lattice.
            ``source="open-meteo"`` (and every backdated ``--date`` run)
            keeps the old Open-Meteo grid fetch instead.
         b. prepare per (hour, level) into 2D fields
         c. compute Hewson diagnostics (gradient / TFP / −∇²θe / advection)
         d. derive tendency ∂θe/∂t across consecutive hours
         e. write one NPZ per (model, init cycle) with level-suffixed keys
    3. purge snapshots older than the retention window

Snapshot schema (per designs/future/hewson-fields-aviation-advisories.md §6.1):

    data/hewson/<model>/<init_iso_z>.npz
        theta_e_{925,850,700}     (n_hour, n_lat, n_lon) float32, K
        gradient_{925,850,700}    (n_hour, n_lat, n_lon) float32, K / 100 km
        neg_laplacian_{925,850,700}
        tfp_{925,850,700}
        advection_{925,850,700}                           K / h
        tendency_{925,850,700}                            K / h  (edges nan)
        valid_times                                       (n_hour,) datetime64[ns]
        lat, lon                                          1D coord arrays
        init_time_unix                                    scalar int
        levels                                            1D int array
"""

from __future__ import annotations

import logging
import os
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.process_rss import log_memory
from weatherbrief.frontal.grid import (
    TERRAIN_MASK_THRESHOLD_M,
    build_grid_coords,
    build_terrain_elevation,
    fetch_grid_fields,
    prepare_level_fields,
    reshape_to_fields,
)

logger = logging.getLogger(__name__)


DEFAULT_MODELS: tuple[str, ...] = ("ecmwf", "gfs", "icon")
DEFAULT_LEVELS: tuple[int, ...] = (925, 850, 700)
DEFAULT_FORECAST_DAYS = 5  # § 4.3 horizon
DEFAULT_RETENTION_HOURS = 48  # § 4.6
# Synoptic-view temporal resolution. Open-Meteo delivers hourly; we decimate
# locally before the compute loop (3× compute speedup, ~3× smaller NPZ). The
# map slider, cross-section bands, and advisory evaluators all interpolate
# between bounding snapshot hours — 3 h gives ~50 km spatial blur on a moving
# front (§ 8 quotes ±50–100 km inherent TFP positional uncertainty) so this
# cadence is well inside the "qualitative, not pinpoint" error budget.
# Per-flight briefings compute at a finer grain at briefing time (§ 6.2
# stencil path) — they don't read the synoptic snapshot for leg advisories.
DEFAULT_STRIDE_HOURS = 3
# Where the fields come from. "grib" reads each model's own GRIB (0 Open-Meteo
# calls, #911); "open-meteo" is the pre-#911 grid fetch, kept for backdated
# calibration runs (no GRIB archive) and side-by-side source comparisons.
SOURCE_GRIB = "grib"
SOURCE_OPEN_METEO = "open-meteo"
SOURCES: tuple[str, ...] = (SOURCE_GRIB, SOURCE_OPEN_METEO)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def resolve_output_dir(output_dir: Path | str | None = None) -> Path:
    """Return the root directory where Hewson snapshots live.

    Resolution order (matches ``storage/snapshots.py``):
      1. explicit ``output_dir`` argument
      2. ``DATA_DIR`` env var → ``{DATA_DIR}/hewson``
      3. fallback ``data/hewson`` (dev convenience)
    """
    if output_dir is not None:
        return Path(output_dir)
    env = os.environ.get("DATA_DIR")
    base = Path(env) if env else Path("data")
    return base / "hewson"


def _init_to_iso_z(init_time_unix: int) -> str:
    """Unix seconds → ISO 8601 with ``Z`` suffix (filename-safe)."""
    dt = datetime.fromtimestamp(init_time_unix, tz=timezone.utc)
    # dt.isoformat() produces "+00:00"; swap for Z
    return dt.isoformat().replace("+00:00", "Z")


def snapshot_path(
    model: str, init_time_unix: int, output_dir: Path | None = None,
) -> Path:
    """Canonical disk location for a (model, init) snapshot."""
    return resolve_output_dir(output_dir) / model / f"{_init_to_iso_z(init_time_unix)}.npz"


def _terrain_mask_path(output_dir: Path | None = None) -> Path:
    return resolve_output_dir(output_dir) / "terrain_mask.npz"


def latest_snapshot(
    model: str, output_dir: Path | None = None,
) -> Path | None:
    """Most-recent snapshot file for ``model`` (by ISO-8601-Z filename), or None.

    The front-detection stage reads the freshest snapshot per model; an init
    mismatch between it and the briefing's forecast cycle is acceptable for the
    smooth advective Hewson fields (see design §8). Filenames sort
    chronologically so ``max`` over the glob is the latest.
    """
    subdir = resolve_output_dir(output_dir) / model
    if not subdir.exists():
        return None
    snaps = sorted(subdir.glob("*.npz"))
    return snaps[-1] if snaps else None


def _snapshot_valid_window(path: Path) -> tuple[int, int] | None:
    """``(init_unix, last_valid_unix)`` a snapshot covers, or None if unreadable.

    Touches only the header members — ``init_time_unix`` and ``valid_times`` (a
    small 1-D timestamp array). The last valid time is read directly off
    ``valid_times[-1]`` rather than reconstructed from ``stride_hours``, so the
    window stays correct even for a non-uniform or stride-less snapshot. The
    large per-level metric stacks are never materialised, since np.load loads
    zip members lazily on access.
    """
    try:
        with np.load(path) as npz:
            init = int(npz["init_time_unix"])
            valid_times = npz["valid_times"]
            if valid_times.ndim != 1 or valid_times.shape[0] == 0:
                return None
            last = int(valid_times[-1].astype("datetime64[s]").astype(np.int64))
    except (OSError, ValueError, KeyError, EOFError, IndexError,
            zipfile.BadZipFile, zlib.error):
        # Missing/corrupt/partially-written snapshot during a filesystem scan —
        # skip it, but don't swallow unrelated bugs (e.g. a programming error).
        return None
    return init, last


def snapshot_for_window(
    model: str,
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
) -> Path | None:
    """Latest snapshot whose forecast hours bracket ``[start, end]``, else None.

    The front stage samples a flight at snapshot-relative hours; the *newest*
    init (:func:`latest_snapshot`) is wrong when the flight window falls outside
    it — a past flight lands at negative offsets and a far-future one past the
    horizon. This scans available snapshots newest-first and returns the most
    recent init whose valid-time span fully contains ``[start, end]``.

    Returns ``None`` when no retained snapshot brackets the window (the caller
    falls back to :func:`latest_snapshot` and notes the approximation).
    """
    subdir = resolve_output_dir(output_dir) / model
    if not subdir.exists():
        return None
    start_u = int(start.timestamp())
    end_u = int(end.timestamp())
    # ISO-8601-Z filenames sort chronologically; reverse → newest init first.
    for path in sorted(subdir.glob("*.npz"), reverse=True):
        window = _snapshot_valid_window(path)
        if window is None:
            continue
        init_u, last_u = window
        if init_u <= start_u and end_u <= last_u:
            return path
    return None


# ---------------------------------------------------------------------------
# Terrain-mask caching
# ---------------------------------------------------------------------------


def _load_or_build_terrain_mask(
    lat: np.ndarray, lon: np.ndarray, output_dir: Path | None,
) -> np.ndarray:
    """Return the flat terrain mask for the current grid (back-compat).

    First invocation per environment builds via SRTM3 (~seconds) and caches the
    elevation grid + the derived flat mask to ``{DATA_DIR}/hewson/terrain_mask.npz``.
    Subsequent calls load from disk and verify the shape matches the active grid.
    A cache missing the ``elevation`` array (pre-#216) is treated as stale and
    rebuilt, so level-aware masking gets its data without a manual cache wipe.
    """
    cache_path = _terrain_mask_path(output_dir)
    if cache_path.exists():
        with np.load(cache_path) as npz:
            mask = npz["mask"]
            cached_lat = npz["lat"]
            cached_lon = npz["lon"]
            has_elevation = "elevation" in npz.files
        if (
            has_elevation
            and mask.shape == (len(lat), len(lon))
            and np.allclose(cached_lat, lat)
            and np.allclose(cached_lon, lon)
        ):
            return mask
        logger.info(
            "Terrain-mask cache at %s stale (grid changed or pre-elevation) — "
            "rebuilding", cache_path,
        )

    elevation = build_terrain_elevation(lat, lon)
    # Flat ≤1500 m (~850 hPa) mask, derived from the same scan so the two never
    # drift; level-aware consumers use the elevation grid via terrain_mask_for_level.
    mask = ~(np.nan_to_num(elevation, nan=-np.inf) > TERRAIN_MASK_THRESHOLD_M)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, mask=mask, lat=lat, lon=lon, elevation=elevation)
    return mask


def ensure_terrain_cache(output_dir: Path | str | None = None) -> np.ndarray:
    """Make ``terrain_mask.npz`` match the current grid; return the flat mask.

    The serving box reads the cache (fronts, map overlay) but only rewrites it
    when it runs a precompute itself. With every model delivered off-box it
    never does, so a grid change (#911) would leave readers on a stale cache
    and no terrain masking. The scheduler calls this once at startup.
    """
    lat, lon = build_grid_coords()
    return _load_or_build_terrain_mask(
        lat, lon, Path(output_dir) if output_dir is not None else None,
    )


# ---------------------------------------------------------------------------
# Tendency helper
# ---------------------------------------------------------------------------


def tendency_k_per_hour(
    theta_e_stack: np.ndarray, step_hours: int = 1,
) -> np.ndarray:
    """Centred-difference ∂θe/∂t in K/h from a (n_time, n_lat, n_lon) stack.

    ``step_hours`` is the wall-clock spacing between consecutive stack
    indices — 1 for hourly snapshots, 3 for a 3-h-cadence precompute. The
    returned tendency is in **K per hour** regardless of stride (we divide
    out the step) so callers read a uniform unit.

    Edges fall back to one-sided differences. With <2 indices the tendency
    is all-NaN — callers should treat as "unavailable".
    """
    n = theta_e_stack.shape[0]
    tendency = np.full_like(theta_e_stack, np.nan, dtype=np.float32)
    if step_hours <= 0:
        raise ValueError(f"step_hours must be positive, got {step_hours}")
    if n >= 2:
        tendency[0] = (theta_e_stack[1] - theta_e_stack[0]) / step_hours
        tendency[-1] = (theta_e_stack[-1] - theta_e_stack[-2]) / step_hours
    if n >= 3:
        tendency[1:-1] = (
            (theta_e_stack[2:] - theta_e_stack[:-2]) / (2 * step_hours)
        )
    return tendency


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SnapshotResult:
    """Outcome of one ``run_once()`` call.

    ``snapshots`` keys are model names; values are the written NPZ path (or
    ``None`` for dry-run / fetch failure). ``skipped`` maps model → reason
    for models that were skipped (already-fresh snapshot, fetch failure).
    ``api_calls_total`` is the aggregate HTTP call count from
    ``OpenMeteoClient.call_count`` across all models in this run — read
    once at end of ``run_once`` so the scheduler loop can push it to
    ``ApiUsageRow`` via :func:`weatherbrief.api.usage.log_api_usage`
    (matches the ``pipeline="verification"`` precedent in
    ``tasks/standalone_verification.py``).
    """

    snapshots: dict[str, Path | None] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    init_times: dict[str, int] = field(default_factory=dict)
    # model → where its fields came from, e.g. "gfs-s3 init 2026-10-24T00:00Z"
    # (GRIB) or "open-meteo" — the per-model source line the node logs.
    sources: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    purged: int = 0
    api_calls_total: int = 0
    # Per-model timing breakdown in seconds. Helpful when correlating slow
    # cycles with droplet load — fetch is network/disk-bound (GRIB download +
    # unpack, or Open-Meteo round trips), compute is GIL-bound (numpy gradients + MetPy θe), write is
    # disk-bound (np.savez_compressed).
    fetch_seconds: dict[str, float] = field(default_factory=dict)
    compute_seconds: dict[str, float] = field(default_factory=dict)
    write_seconds: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-model snapshot builder
# ---------------------------------------------------------------------------

_BuiltSnapshot = tuple[int, np.ndarray, dict[int, dict[str, np.ndarray]], float, float]


def _empty_stacks(
    levels: list[int], n_time: int, n_lat: int, n_lon: int,
) -> dict[int, dict[str, np.ndarray]]:
    """metric_name → (n_time, n_lat, n_lon) float32 NaN stacks, per level."""
    return {
        L: {
            metric: np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32)
            for metric in ("theta_e", "gradient", "neg_laplacian", "tfp", "advection")
        }
        for L in levels
    }


def _store_diagnostics(
    per_level: dict[int, dict[str, np.ndarray]],
    i: int,
    L: int,
    fields: dict,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray,
) -> None:
    """Compute the Hewson diagnostics of one (hour, level) into the stacks."""
    # prepare_level_fields returns level-specific winds under the historical
    # u850/v850 keys regardless of the actual level (see frontal/grid.py
    # reshape_to_fields docstring) — values here are from level L, not 850 hPa.
    diag = compute_hewson_diagnostics(
        fields["theta_e"], lat, lon,
        fields["u850"], fields["v850"],
        terrain_mask=terrain_mask,
    )
    per_level[L]["theta_e"][i] = fields["theta_e"]
    per_level[L]["gradient"][i] = diag["gradient"]
    per_level[L]["neg_laplacian"][i] = diag["neg_laplacian"]
    per_level[L]["tfp"][i] = diag["tfp"]
    per_level[L]["advection"][i] = diag["advection"]


def _add_tendency(
    per_level: dict[int, dict[str, np.ndarray]], levels: list[int], stride_hours: int,
) -> None:
    # Derive tendency across the decimated stack — step_hours matches stride
    # so the result is still K per hour regardless of cadence.
    for L in levels:
        per_level[L]["tendency"] = tendency_k_per_hour(
            per_level[L]["theta_e"], step_hours=stride_hours,
        )


def _build_one_snapshot_grib(
    run,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray,
    levels: list[int],
    hours: list[int],
    stride_hours: int,
) -> _BuiltSnapshot | None:
    """Read one resolved GRIB run step by step and compute its diagnostics.

    ``run`` is a :class:`~weatherbrief.hewson.grib_source.GribRun`. Steps are
    read and computed one at a time, so only one step's raw fields are held.
    Any step that cannot be read in full (after one retry) drops the model
    for this cycle — a snapshot with a hole would read as "no front" there.
    Returns ``(init_time_unix, valid_times, per_level_stacks, fetch_secs,
    compute_secs)`` or None.
    """
    from weatherbrief.hewson.grib_source import level_inputs, read_step

    n_time = len(hours)
    valid_times = np.array(
        [np.datetime64(run.init.replace(tzinfo=None) + timedelta(hours=h), "ns")
         for h in hours],
        dtype="datetime64[ns]",
    )
    per_level = _empty_stacks(levels, n_time, len(lat), len(lon))
    fetch_secs = compute_secs = 0.0
    for i, h in enumerate(hours):
        t0 = time.monotonic()
        step = read_step(run, h, levels, lat, lon)
        fetch_secs += time.monotonic() - t0
        if step is None:
            logger.error(
                "Hewson precompute: %s %s f%03d unreadable — skipping the model "
                "this cycle", run.model, run.describe(), h,
            )
            return None
        t0 = time.monotonic()
        for L in levels:
            inputs = level_inputs(step[L])
            fields = (
                prepare_level_fields(
                    *inputs, terrain_mask=terrain_mask, level_hPa=L, hour_index=h,
                )
                if inputs is not None else None
            )
            if fields is None:
                # Too many missing cells: a NaN slice would read as "no front".
                logger.error(
                    "Hewson precompute: %s %s f%03d %d hPa has too many missing "
                    "cells — skipping the model this cycle",
                    run.model, run.describe(), h, L,
                )
                return None
            _store_diagnostics(per_level, i, L, fields, lat, lon, terrain_mask)
        compute_secs += time.monotonic() - t0
    t0 = time.monotonic()
    _add_tendency(per_level, levels, stride_hours)
    compute_secs += time.monotonic() - t0
    return run.init_unix, valid_times, per_level, fetch_secs, compute_secs


def _build_one_snapshot_open_meteo(
    client,
    model: str,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray,
    levels: list[int],
    forecast_days: int,
    stride_hours: int = DEFAULT_STRIDE_HOURS,
    start_date: str | None = None,
    end_date: str | None = None,
) -> _BuiltSnapshot | None:
    """Fetch + compute diagnostics for one model from Open-Meteo (pre-#911 path).

    Kept for backdated calibration runs and side-by-side source comparisons;
    the live precompute reads GRIB (:func:`_build_one_snapshot_grib`).

    Fetches hourly data from Open-Meteo (its native cadence), then decimates
    to every ``stride_hours``-th forecast hour before the Hewson compute loop
    — this both shrinks the output NPZ and skips ``stride_hours - 1`` compute
    passes per step. See ``DEFAULT_STRIDE_HOURS`` for the rationale. Note the
    series is anchored at 00 Z of the request day, not at the model init.

    Returns ``(init_time_unix, valid_times, per_level_stacks, fetch_secs,
    compute_secs)``; None if Open-Meteo returns no usable data.

    When ``start_date`` and ``end_date`` are set the fetch covers that
    historical (or forward) window instead of the live forecast. The
    returned ``init_time_unix`` is overridden to ``start_date`` 00 Z so
    the snapshot is filed under a stable, calibration-friendly key
    rather than the live model's current init timestamp.
    """
    fetch_t0 = time.monotonic()
    raw, timestamps, init_time = fetch_grid_fields(
        client, model, lat, lon,
        levels=levels, forecast_days=forecast_days,
        start_date=start_date, end_date=end_date,
    )
    fetch_secs = time.monotonic() - fetch_t0
    if not timestamps:
        logger.warning("Hewson precompute: %s returned no timestamps", model)
        return None

    if start_date:
        # Override init_time so the snapshot is keyed by the requested date,
        # not whatever live cycle Open-Meteo happened to serve.
        init_dt = datetime.fromisoformat(f"{start_date}T00:00:00+00:00")
        init_time = int(init_dt.timestamp())

    if stride_hours < 1:
        raise ValueError(f"stride_hours must be >= 1, got {stride_hours}")

    # Decimate to stride_hours cadence, anchored at index 0.
    keep_indices = list(range(0, len(timestamps), stride_hours))
    kept_timestamps = [timestamps[i] for i in keep_indices]
    valid_times = np.array(
        [np.datetime64(ts) for ts in kept_timestamps], dtype="datetime64[ns]",
    )
    per_level = _empty_stacks(levels, len(kept_timestamps), len(lat), len(lon))

    compute_t0 = time.monotonic()
    for i, h in enumerate(keep_indices):
        for L in levels:
            fields = reshape_to_fields(raw, lat, lon, h, terrain_mask, level_hPa=L)
            if fields is not None:
                _store_diagnostics(per_level, i, L, fields, lat, lon, terrain_mask)
    _add_tendency(per_level, levels, stride_hours)
    compute_secs = time.monotonic() - compute_t0

    return init_time, valid_times, per_level, fetch_secs, compute_secs


# ---------------------------------------------------------------------------
# NPZ write
# ---------------------------------------------------------------------------


def write_snapshot(
    path: Path,
    init_time_unix: int,
    valid_times: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    levels: list[int],
    stride_hours: int,
    per_level: dict[int, dict[str, np.ndarray]],
) -> None:
    """Serialize a snapshot to NPZ with level-suffixed keys per § 6.1."""
    data: dict[str, np.ndarray] = {
        "valid_times": valid_times,
        "lat": lat.astype(np.float64),
        "lon": lon.astype(np.float64),
        "init_time_unix": np.array(init_time_unix, dtype=np.int64),
        "levels": np.array(levels, dtype=np.int32),
        "stride_hours": np.array(stride_hours, dtype=np.int32),
    }
    for L in levels:
        for metric, arr in per_level[L].items():
            data[f"{metric}_{L}"] = arr
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **data)


def load_snapshot(path: Path | str) -> dict:
    """Load a precomputed snapshot, returning a dict of its contents.

    Convenience wrapper for readers (map endpoint, cross-section bands,
    advisory evaluators). Keys mirror the NPZ file — see module docstring
    for the schema. Arrays are materialized in memory on load.

    Size note: a default snapshot is ~46 MB compressed (3 h stride × 40
    timesteps × 3 levels × 6 metrics × float32, see § 5 of the design
    doc). ``np.load`` decompresses each array on first access, which can
    take ~100–300 ms for the full dict on a cold disk. Callers running
    inside an async event loop (FastAPI route handlers etc.) must invoke
    this from ``asyncio.to_thread`` or an equivalent threadpool offload,
    or index the NPZ lazily (``with np.load(path) as npz: npz['metric_L'][h]``)
    to decompress only the slice they need.
    """
    with np.load(Path(path)) as npz:
        return {k: npz[k] for k in npz.files}


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def purge_old_snapshots(
    model: str | None = None,
    output_dir: Path | None = None,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
    now: datetime | None = None,
) -> int:
    """Delete snapshots older than ``retention_hours``. Returns count deleted.

    When ``model`` is None, iterates every per-model subdirectory under the
    output root. Snapshots are identified by their ISO-8601-Z filename
    (``<init>.npz``) so parsing is straightforward.
    """
    root = resolve_output_dir(output_dir)
    if not root.exists():
        return 0

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=retention_hours)

    if model is not None:
        subdirs = [root / model]
    else:
        subdirs = [d for d in root.iterdir() if d.is_dir()]

    removed = 0
    for subdir in subdirs:
        if not subdir.exists():
            continue
        for path in subdir.glob("*.npz"):
            stem = path.stem  # "2026-04-24T12:00:00Z"
            try:
                # datetime.fromisoformat handles "+00:00" but not "Z" on 3.10.
                # Replace Z → +00:00 for round-trip.
                dt = datetime.fromisoformat(stem.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(
                    "Hewson purge: skipping unrecognised filename %s", path,
                )
                continue
            if dt < cutoff:
                path.unlink()
                removed += 1
    if removed:
        logger.info("Hewson purge: removed %d snapshot(s)", removed)
    return removed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_once(
    models: list[str] | tuple[str, ...] | None = None,
    *,
    levels: list[int] | tuple[int, ...] | None = None,
    output_dir: Path | str | None = None,
    forecast_days: int = DEFAULT_FORECAST_DAYS,
    stride_hours: int = DEFAULT_STRIDE_HOURS,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
    dry_run: bool = False,
    skip_existing: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str = SOURCE_GRIB,
    client=None,
    now: datetime | None = None,
    init: datetime | None = None,
    require_current: bool = True,
) -> SnapshotResult:
    """Fetch + compute + write one snapshot per model.

    Single entry point shared by the scheduler loop and the ``hewson precompute``
    CLI. Safe to call any time — each model's newest complete run is resolved
    first, so calling twice in quick succession for the same cycle is a
    no-op when ``skip_existing=True`` (default).

    Parameters
    ----------
    models : list of model keys (default ``("ecmwf", "gfs", "icon")``).
    levels : pressure levels in hPa (default ``(925, 850, 700)``).
    output_dir : root dir for snapshots; defaults to ``${DATA_DIR}/hewson``.
    forecast_days : horizon (default 5, § 4.3): forecast hours
        ``0, stride, …`` below ``forecast_days × 24`` from init.
    stride_hours : cadence of the stored steps (default 3, see
        ``DEFAULT_STRIDE_HOURS``). Set to 1 to keep every hour.
    retention_hours : purge window applied after writing (default 48).
    dry_run : compute but do not write NPZ or purge.
    skip_existing : if an NPZ for a model's current init already exists,
        skip the fetch + compute. Disable for forced recomputation.
        Automatically forced to False when ``start_date`` is set.
    start_date, end_date : ``YYYY-MM-DD`` strings. When set, the fetch
        targets that historical (or forward) window instead of the live
        forecast, from Open-Meteo (there is no GRIB archive to read). The
        snapshot is keyed by ``start_date`` 00 Z. Purge is skipped so
        calibration runs aren't garbage-collected by retention.
    source : ``"grib"`` (default, #911: ECMWF a2 / GFS S3 / ICON-EU DWD, no
        Open-Meteo call) or ``"open-meteo"`` (the pre-#911 grid fetch, for
        side-by-side comparisons).
    client : optional OpenMeteoClient (Open-Meteo source only; tests).
    now : reference time for GRIB run resolution (tests).
    init : GRIB source only — read this run (aware UTC) instead of the newest,
        e.g. to recompute a cycle that has an Open-Meteo snapshot for the
        side-by-side check. Purge is skipped, like a backdated run.
    require_current : GRIB source, newest run only. A model whose newest
        complete run is older than ``grib_source.expected_init`` (its
        delivery or sync failed) is skipped as ``"stale-init"`` and logged at
        ERROR, rather than keeping or publishing the older run. The other
        models still run.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    models = list(models) if models else list(DEFAULT_MODELS)
    levels = sorted(int(L) for L in (levels if levels else DEFAULT_LEVELS))
    backdated = bool(start_date)
    if backdated and not end_date:
        end_date = start_date
    if init is not None and (backdated or source != SOURCE_GRIB):
        raise ValueError("init= pins a GRIB run; it cannot combine with "
                         "start_date or the Open-Meteo source")
    if backdated:
        skip_existing = False
        if source != SOURCE_OPEN_METEO:
            logger.info(
                "Hewson precompute: backdated run (%s) reads Open-Meteo's "
                "archived forecast — no GRIB archive", start_date,
            )
            source = SOURCE_OPEN_METEO

    started = time.monotonic()
    result = SnapshotResult()

    if source == SOURCE_OPEN_METEO and client is None:
        from weatherbrief.fetch.open_meteo import OpenMeteoClient

        client = OpenMeteoClient()
    lat, lon = build_grid_coords()
    terrain_mask = _load_or_build_terrain_mask(lat, lon, output_dir)
    hours = None
    if source == SOURCE_GRIB:
        from weatherbrief.hewson.grib_source import forecast_step_hours

        hours = forecast_step_hours(forecast_days, stride_hours)

    for model in models:
        try:
            run = None
            if source == SOURCE_GRIB:
                from weatherbrief.hewson.grib_source import resolve_run

                run = resolve_run(model, hours, levels, now=now, init=init)
                if run is None:
                    result.skipped[model] = "no-grib-run"
                    continue
                result.sources[model] = run.describe()
                if require_current and init is None:
                    from weatherbrief.hewson.grib_source import (
                        expected_init as cycle_expected_init,
                    )

                    want = cycle_expected_init(model, now)
                    if run.init < want:
                        # Never quietly keep (or publish) an older run: the
                        # delivery or sync for this cycle failed (#911).
                        logger.error(
                            "Hewson precompute: %s newest complete GRIB run is "
                            "%s, expected init %s or later; delivery missing or "
                            "incomplete, %s not updated this cycle",
                            model, run.describe(),
                            want.strftime("%Y-%m-%dT%H:%MZ"), model,
                        )
                        result.skipped[model] = "stale-init"
                        continue
                logger.info(
                    "Hewson precompute: %s from GRIB %s (%d steps, f%03d-f%03d)",
                    model, run.describe(), len(hours), hours[0], hours[-1],
                )
                expected_init = run.init_unix
            elif skip_existing:
                # Peek at init time first — lets us skip the grid fetch when
                # the snapshot for this cycle is already on disk.
                from weatherbrief.fetch.model_status import fetch_model_metadata

                meta = fetch_model_metadata(models=[model])
                expected_init = meta[model].last_init_time if model in meta else 0
            else:
                expected_init = 0

            if skip_existing and expected_init:
                expected_path = snapshot_path(model, expected_init, output_dir)
                if expected_path.exists():
                    logger.info(
                        "Hewson precompute: %s init %s already on disk, "
                        "skipping", model, _init_to_iso_z(expected_init),
                    )
                    result.skipped[model] = "fresh"
                    result.init_times[model] = expected_init
                    continue

            if run is not None:
                built = _build_one_snapshot_grib(
                    run, lat, lon, terrain_mask, levels, hours, stride_hours,
                )
                if built is None:
                    result.skipped[model] = "incomplete"
                    continue
            else:
                result.sources[model] = SOURCE_OPEN_METEO
                built = _build_one_snapshot_open_meteo(
                    client, model, lat, lon, terrain_mask,
                    levels, forecast_days, stride_hours,
                    start_date=start_date, end_date=end_date,
                )
                if built is None:
                    result.skipped[model] = "no-data"
                    continue
            init_time, valid_times, per_level, fetch_secs, compute_secs = built
            result.init_times[model] = init_time
            result.fetch_seconds[model] = fetch_secs
            result.compute_seconds[model] = compute_secs

            if init_time == 0:
                logger.warning(
                    "Hewson precompute: %s has init_time=0 — skipping write",
                    model,
                )
                result.skipped[model] = "no-init-time"
                continue

            path = snapshot_path(model, init_time, output_dir)
            if dry_run:
                logger.info(
                    "Hewson precompute (dry-run): would write %s "
                    "(fetch=%.1fs compute=%.1fs)",
                    path, fetch_secs, compute_secs,
                )
                result.snapshots[model] = None
            else:
                write_t0 = time.monotonic()
                write_snapshot(
                    path, init_time, valid_times, lat, lon,
                    levels, stride_hours, per_level,
                )
                write_secs = time.monotonic() - write_t0
                result.write_seconds[model] = write_secs
                size_kb = path.stat().st_size / 1024
                logger.info(
                    "Hewson precompute: wrote %s from %s "
                    "(%d timesteps × %d levels, stride=%dh, %.1f KB) "
                    "fetch=%.1fs compute=%.1fs write=%.1fs",
                    path, result.sources.get(model, source), len(valid_times),
                    len(levels), stride_hours, size_kb,
                    fetch_secs, compute_secs, write_secs,
                )
                result.snapshots[model] = path
        except Exception:
            logger.exception("Hewson precompute: %s failed", model)
            result.skipped[model] = "error"

    if not dry_run and not backdated and init is None:
        result.purged = purge_old_snapshots(
            output_dir=output_dir, retention_hours=retention_hours,
        )

    result.api_calls_total = getattr(client, "call_count", 0) if client is not None else 0
    result.elapsed_seconds = time.monotonic() - started
    fetch_total = sum(result.fetch_seconds.values())
    compute_total = sum(result.compute_seconds.values())
    write_total = sum(result.write_seconds.values())
    logger.info(
        "Hewson precompute: done (%.1fs) — %d written, %d skipped, "
        "%d purged, %d Open-Meteo API calls, sources %s "
        "[fetch=%.1fs compute=%.1fs write=%.1fs]",
        result.elapsed_seconds,
        len([p for p in result.snapshots.values() if p is not None]),
        len(result.skipped),
        result.purged,
        result.api_calls_total,
        result.sources or "-",
        fetch_total, compute_total, write_total,
    )
    log_memory("hewson precompute", logger)
    return result
