"""Hewson diagnostic snapshot precompute pipeline.

Core flow, shared by the scheduler loop and the ``python -m weatherbrief.hewson
precompute`` CLI:

    1. build / load terrain mask (cached to disk)
    2. for each model:
         a. fetch multi-level grid (925/850/700 hPa) via Open-Meteo
         b. reshape per (hour, level) into 2D fields
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.frontal.grid import (
    build_grid_coords,
    build_terrain_mask,
    fetch_grid_fields,
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


# ---------------------------------------------------------------------------
# Terrain-mask caching
# ---------------------------------------------------------------------------


def _load_or_build_terrain_mask(
    lat: np.ndarray, lon: np.ndarray, output_dir: Path | None,
) -> np.ndarray:
    """Return the terrain mask for the current grid.

    First invocation per environment builds via SRTM3 (~seconds) and caches
    to ``{DATA_DIR}/hewson/terrain_mask.npz``. Subsequent calls load from
    disk and verify the shape matches the active grid.
    """
    cache_path = _terrain_mask_path(output_dir)
    if cache_path.exists():
        with np.load(cache_path) as npz:
            mask = npz["mask"]
            cached_lat = npz["lat"]
            cached_lon = npz["lon"]
        if (
            mask.shape == (len(lat), len(lon))
            and np.allclose(cached_lat, lat)
            and np.allclose(cached_lon, lon)
        ):
            return mask
        logger.info(
            "Terrain-mask cache at %s stale (grid changed) — rebuilding",
            cache_path,
        )

    mask = build_terrain_mask(lat, lon)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, mask=mask, lat=lat, lon=lon)
    return mask


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
    elapsed_seconds: float = 0.0
    purged: int = 0
    api_calls_total: int = 0


# ---------------------------------------------------------------------------
# Per-model snapshot builder
# ---------------------------------------------------------------------------


def _build_one_snapshot(
    client,
    model: str,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray,
    levels: list[int],
    forecast_days: int,
    stride_hours: int = DEFAULT_STRIDE_HOURS,
) -> tuple[int, np.ndarray, dict[int, dict[str, np.ndarray]]] | None:
    """Fetch + compute diagnostics for one model.

    Fetches hourly data from Open-Meteo (its native cadence), then decimates
    to every ``stride_hours``-th forecast hour before the Hewson compute loop
    — this both shrinks the output NPZ and skips ``stride_hours - 1`` compute
    passes per step. See ``DEFAULT_STRIDE_HOURS`` for the rationale.

    Returns ``(init_time_unix, valid_times, per_level_stacks)`` where
    ``per_level_stacks[L]`` is a dict of metric-name → (n_time, n_lat, n_lon)
    float32 stacks. Returns ``None`` if Open-Meteo returns no usable data.
    """
    raw, timestamps, init_time = fetch_grid_fields(
        client, model, lat, lon,
        levels=levels, forecast_days=forecast_days,
    )
    if not timestamps:
        logger.warning("Hewson precompute: %s returned no timestamps", model)
        return None

    if stride_hours < 1:
        raise ValueError(f"stride_hours must be >= 1, got {stride_hours}")

    # Decimate to stride_hours cadence. Anchored at the model init (index 0)
    # so the series lands on synoptic hours when init is on a synoptic hour.
    keep_indices = list(range(0, len(timestamps), stride_hours))
    kept_timestamps = [timestamps[i] for i in keep_indices]

    n_time = len(kept_timestamps)
    n_lat, n_lon = len(lat), len(lon)
    valid_times = np.array(
        [np.datetime64(ts) for ts in kept_timestamps], dtype="datetime64[ns]",
    )

    # metric_name → (n_time, n_lat, n_lon) float32
    per_level: dict[int, dict[str, np.ndarray]] = {
        L: {
            "theta_e": np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32),
            "gradient": np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32),
            "neg_laplacian": np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32),
            "tfp": np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32),
            "advection": np.full((n_time, n_lat, n_lon), np.nan, dtype=np.float32),
        }
        for L in levels
    }

    for i, h in enumerate(keep_indices):
        for L in levels:
            fields = reshape_to_fields(raw, lat, lon, h, terrain_mask, level_hPa=L)
            if fields is None:
                continue
            # reshape_to_fields returns level-specific winds under the
            # historical u850/v850 keys regardless of the actual level
            # (see frontal/grid.py reshape_to_fields docstring) — values
            # here are from level L, not 850 hPa.
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

    # Derive tendency across the decimated stack — step_hours matches stride
    # so the result is still K per hour regardless of cadence.
    for L in levels:
        per_level[L]["tendency"] = tendency_k_per_hour(
            per_level[L]["theta_e"], step_hours=stride_hours,
        )

    return init_time, valid_times, per_level


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
    client=None,
) -> SnapshotResult:
    """Fetch + compute + write one snapshot per model.

    Single entry point shared by the scheduler loop and the ``hewson precompute``
    CLI. Safe to call any time — Open-Meteo serves whatever init it currently
    publishes, so calling twice in quick succession for the same cycle is a
    no-op when ``skip_existing=True`` (default).

    Parameters
    ----------
    models : list of model keys (default ``("ecmwf", "gfs", "icon")``).
    levels : pressure levels in hPa (default ``(925, 850, 700)``).
    output_dir : root dir for snapshots; defaults to ``${DATA_DIR}/hewson``.
    forecast_days : horizon requested from Open-Meteo (default 5, § 4.3).
    stride_hours : decimation cadence applied to hourly Open-Meteo output
        before the compute loop (default 3, see ``DEFAULT_STRIDE_HOURS``).
        Set to 1 to keep every hour.
    retention_hours : purge window applied after writing (default 48).
    dry_run : compute but do not write NPZ or purge.
    skip_existing : if an NPZ for a model's current init already exists,
        skip the fetch + compute. Disable for forced recomputation.
    client : optional OpenMeteoClient (for testing / dependency injection).
    """
    from weatherbrief.fetch.open_meteo import OpenMeteoClient

    import time

    models = list(models) if models else list(DEFAULT_MODELS)
    levels = sorted(int(L) for L in (levels if levels else DEFAULT_LEVELS))

    started = time.monotonic()
    result = SnapshotResult()

    client = client or OpenMeteoClient()
    lat, lon = build_grid_coords()
    terrain_mask = _load_or_build_terrain_mask(lat, lon, output_dir)

    for model in models:
        try:
            # Peek at init time first — lets us skip the grid fetch when the
            # snapshot for this cycle is already on disk.
            if skip_existing:
                from weatherbrief.fetch.model_status import fetch_model_metadata

                meta = fetch_model_metadata(models=[model])
                if model in meta:
                    expected_init = meta[model].last_init_time
                    expected_path = snapshot_path(model, expected_init, output_dir)
                    if expected_init and expected_path.exists():
                        logger.info(
                            "Hewson precompute: %s init %s already on disk, "
                            "skipping", model, _init_to_iso_z(expected_init),
                        )
                        result.skipped[model] = "fresh"
                        result.init_times[model] = expected_init
                        continue

            built = _build_one_snapshot(
                client, model, lat, lon, terrain_mask,
                levels, forecast_days, stride_hours,
            )
            if built is None:
                result.skipped[model] = "no-data"
                continue
            init_time, valid_times, per_level = built
            result.init_times[model] = init_time

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
                    "Hewson precompute (dry-run): would write %s", path,
                )
                result.snapshots[model] = None
            else:
                write_snapshot(
                    path, init_time, valid_times, lat, lon,
                    levels, stride_hours, per_level,
                )
                size_kb = path.stat().st_size / 1024
                logger.info(
                    "Hewson precompute: wrote %s "
                    "(%d timesteps × %d levels, stride=%dh, %.1f KB)",
                    path, len(valid_times), len(levels),
                    stride_hours, size_kb,
                )
                result.snapshots[model] = path
        except Exception:
            logger.exception("Hewson precompute: %s failed", model)
            result.skipped[model] = "error"

    if not dry_run:
        result.purged = purge_old_snapshots(
            output_dir=output_dir, retention_hours=retention_hours,
        )

    result.api_calls_total = getattr(client, "call_count", 0)
    result.elapsed_seconds = time.monotonic() - started
    logger.info(
        "Hewson precompute: done (%.1fs) — %d written, %d skipped, "
        "%d purged, %d Open-Meteo API calls",
        result.elapsed_seconds,
        len([p for p in result.snapshots.values() if p is not None]),
        len(result.skipped),
        result.purged,
        result.api_calls_total,
    )
    return result
