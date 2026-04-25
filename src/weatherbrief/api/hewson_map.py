"""Hewson synoptic-view map endpoints.

Reads the precomputed NPZ snapshots written by ``weatherbrief.hewson.precompute``
and serves one ``(model, init, level, metric, hour)`` slice at a time to the
forecast-page map (Phase D.1 of the Hewson rollout — see
``designs/future/hewson-fields-aviation-advisories.md`` § 7.3 / § 12a).

Three endpoints:

  GET /api/hewson-map/manifest
      → list of (model, init_time, levels, valid_times, grid bounds) tuples
        for every snapshot currently on disk

  GET /api/hewson-map
      → one (n_lat, n_lon) slice as JSON, with lat/lon coords and the
        valid_time the slice corresponds to

  GET /api/hewson-map/all-metrics
      → all six metric grids for one (model, init, level, hour) in a
        single response — feeds the cursor-following tooltip on the
        Synoptic Forecast tab, fetched in the background after the
        single-metric slice has rendered

All require an authenticated user (``current_user_id`` Depends, same as the
rest of the API).
"""

from __future__ import annotations

import logging
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from flyfun_common.db import current_user_id
from weatherbrief.hewson.precompute import (
    DEFAULT_LEVELS,
    DEFAULT_STRIDE_HOURS,
    resolve_output_dir,
    snapshot_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hewson-map", tags=["hewson-map"])


VALID_METRICS: tuple[str, ...] = (
    "theta_e",
    "gradient",
    "neg_laplacian",
    "tfp",
    "advection",
    "tendency",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_init(init_str: str) -> int:
    """ISO 8601 with Z → unix seconds. Raises HTTPException on bad input."""
    try:
        dt = datetime.fromisoformat(init_str.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"init must be ISO 8601 with Z (e.g. 2026-04-24T12:00:00Z); got {init_str!r}",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _format_init(init_time_unix: int) -> str:
    """Unix seconds → ISO 8601 Z (matches the on-disk filename stem)."""
    dt = datetime.fromtimestamp(init_time_unix, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _nan_to_none(arr: np.ndarray) -> list:
    """Convert (n_lat, n_lon) float array to nested list with NaN → None.

    JSON spec doesn't allow NaN; terrain-masked cells and tendency edges are
    NaN in the snapshot. Browser-side JSON parsers reject ``NaN`` literals,
    so we serialize as ``null``.

    Vectorised path is ~10× faster than the equivalent double-loop over
    ``arr.tolist()`` — matters for the all-metrics endpoint which hits this
    six times per request on ~20k-element grids.
    """
    mask = ~np.isfinite(arr)
    if not mask.any():
        # Hot path on grids with no terrain holes / NaN edges.
        return arr.tolist()
    out = arr.astype(object)
    out[mask] = None
    return out.tolist()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _read_snapshot_meta(path: Path) -> dict[str, Any] | None:
    """Read the cheap metadata fields from a snapshot without touching the
    bulky metric arrays. Returns ``None`` if the file can't be parsed.

    A partially-written NPZ (from a crashed precompute) raises
    ``zipfile.BadZipFile`` from inside ``np.load``, which inherits from
    ``Exception`` rather than ``OSError`` — so it must be caught
    explicitly. ``EOFError`` covers truncated files too.
    """
    try:
        with np.load(path) as npz:
            init_time_unix = int(npz["init_time_unix"])
            levels = [int(L) for L in npz["levels"]]
            stride_hours = int(npz["stride_hours"]) if "stride_hours" in npz.files else DEFAULT_STRIDE_HOURS
            valid_times = npz["valid_times"]
            lat = npz["lat"]
            lon = npz["lon"]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, EOFError):
        logger.warning("Hewson manifest: failed to parse %s", path, exc_info=True)
        return None

    valid_times_iso = [
        np.datetime_as_string(t, unit="s", timezone="UTC") for t in valid_times
    ]
    return {
        "init_time": _format_init(init_time_unix),
        "init_time_unix": init_time_unix,
        "levels": levels,
        "stride_hours": stride_hours,
        "valid_times": valid_times_iso,
        "n_hours": len(valid_times_iso),
        "lat_min": float(lat.min()),
        "lat_max": float(lat.max()),
        "lon_min": float(lon.min()),
        "lon_max": float(lon.max()),
        "n_lat": int(lat.size),
        "n_lon": int(lon.size),
    }


@router.get("/manifest")
def get_manifest(
    _user_id: str = Depends(current_user_id),
):
    """List every Hewson snapshot currently on disk, grouped by model.

    Used by the frontend to populate model/init/level/hour pickers without
    needing to know the on-disk layout. Cheap: reads only metadata fields,
    not the metric arrays.

    Scans every subdirectory under the snapshot root (not just the live
    forecast models) so ERA5 / historical-event cases dropped in by the
    ``era5-case`` CLI surface automatically. The ``terrain_mask.npz`` file
    that lives alongside the per-model dirs is filtered out by checking
    for ``.npz`` siblings inside the subdirectory.
    """
    root = resolve_output_dir()
    if not root.exists():
        return {"models": {}}

    out: dict[str, list[dict[str, Any]]] = {}
    for subdir in sorted(p for p in root.iterdir() if p.is_dir()):
        snaps: list[dict[str, Any]] = []
        for path in sorted(subdir.glob("*.npz")):
            meta = _read_snapshot_meta(path)
            if meta is not None:
                snaps.append(meta)
        if snaps:
            out[subdir.name] = snaps
    return {"models": out}


# ---------------------------------------------------------------------------
# Shared param validation + snapshot lookup
# ---------------------------------------------------------------------------


def _validate_common_params(model: str, level: int) -> None:
    """Apply the cheap syntactic checks shared by every read endpoint."""
    if not model or "/" in model or "\\" in model or model.startswith("."):
        raise HTTPException(
            status_code=400,
            detail=f"model must be a simple identifier; got {model!r}",
        )
    if level not in DEFAULT_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"level must be one of {DEFAULT_LEVELS}; got {level}",
        )


def _resolve_snapshot_path(model: str, init: str) -> tuple[Path, int]:
    """Parse the init timestamp and return ``(path, init_time_unix)``.

    Raises 404 if the snapshot file isn't on disk; raises 400 from
    ``_parse_init`` if the init string is malformed; raises 400 if the
    constructed path escapes the snapshot root (belt-and-suspenders on
    top of the syntactic checks in ``_validate_common_params``).
    """
    init_time_unix = _parse_init(init)
    path = snapshot_path(model, init_time_unix)
    root = resolve_output_dir()
    # Resolve both before comparing — protects against symlinks / .. that
    # somehow slipped past the syntactic guards in _validate_common_params.
    if not path.resolve().is_relative_to(root.resolve()):
        raise HTTPException(
            status_code=400,
            detail=f"invalid model {model!r}",
        )
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No snapshot for model={model} init={init}",
        )
    return path, init_time_unix


def _open_snapshot(path: Path):
    """``np.load(path)`` with corrupt-file errors converted to a 404.

    A snapshot whose zipfile is malformed (typically from a crashed
    precompute mid-write) is treated as "missing" rather than 500ing —
    the next clean precompute cycle replaces it.

    Current numpy raises BadZipFile / ValueError synchronously from
    ``np.load`` itself for malformed npz, so the explicit ``.files``
    touch is belt-and-braces — but if a future numpy version makes the
    parse lazy, the touch ensures the failure surfaces here. We close
    the partially-opened ``NpzFile`` defensively in that case.
    """
    npz = None
    try:
        npz = np.load(path)
        _ = npz.files  # force zip-catalog read
        return npz
    except (zipfile.BadZipFile, EOFError, OSError, ValueError):
        # ValueError covers numpy's "not a valid npz/npy" path on garbage
        # input; BadZipFile + EOFError cover truncated zips.
        if npz is not None:
            npz.close()
        logger.warning("Hewson endpoint: corrupt snapshot at %s", path, exc_info=True)
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot at {path.name} is unreadable",
        )


def _resolve_hour_index(npz, hour: int) -> tuple[int, int, str]:
    """Validate the ``hour`` param against the snapshot's stride/horizon
    and return ``(idx, stride_hours, valid_time_iso)``."""
    stride_hours = int(npz["stride_hours"]) if "stride_hours" in npz.files else DEFAULT_STRIDE_HOURS
    valid_times = npz["valid_times"]
    n_time = valid_times.shape[0]

    if hour % stride_hours != 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"hour={hour} is not aligned to this snapshot's stride "
                f"({stride_hours} h). Valid hours are 0, {stride_hours}, "
                f"{2 * stride_hours}, ..."
            ),
        )
    idx = hour // stride_hours
    if idx >= n_time:
        max_hour = (n_time - 1) * stride_hours
        raise HTTPException(
            status_code=400,
            detail=f"hour={hour} is past the snapshot horizon (max {max_hour})",
        )
    valid_time_iso = np.datetime_as_string(valid_times[idx], unit="s", timezone="UTC")
    return idx, stride_hours, valid_time_iso


# ---------------------------------------------------------------------------
# Slice (one metric)
# ---------------------------------------------------------------------------


@router.get("")
def get_slice(
    response: Response,
    model: str = Query(..., description="ecmwf | gfs | icon"),
    init: str = Query(..., description="ISO 8601 with Z, e.g. 2026-04-24T12:00:00Z"),
    level: int = Query(..., description="Pressure level in hPa: 925, 850, or 700"),
    metric: str = Query(..., description=" | ".join(VALID_METRICS)),
    hour: int = Query(..., ge=0, description="Forecast hour offset from init"),
    _user_id: str = Depends(current_user_id),
):
    """Return one ``(n_lat, n_lon)`` slice of a Hewson snapshot.

    Parameters are validated up-front; on success the response is small JSON
    (≈80 KB at the default 0.25° grid — see § 7.2 of the design doc).

    The ``(model, init)`` tuple identifies an immutable snapshot file —
    response is cacheable for a long time.
    """
    _validate_common_params(model, level)
    if metric not in VALID_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of {VALID_METRICS}; got {metric!r}",
        )

    path, init_time_unix = _resolve_snapshot_path(model, init)
    key = f"{metric}_{level}"
    # Lazy NPZ access — only decompress the slice we need, not all 18+ stacks.
    with _open_snapshot(path) as npz:
        if key not in npz.files:
            raise HTTPException(
                status_code=404,
                detail=f"Snapshot is missing {key!r} (was it built with this level?)",
            )
        idx, stride_hours, valid_time_iso = _resolve_hour_index(npz, hour)
        slice_arr = npz[key][idx]
        lat = npz["lat"]
        lon = npz["lon"]

    # Slice for an immutable (model, init) snapshot — long cache is safe.
    # ``private`` (not ``public``): the response is auth-gated, so shared
    # proxies / CDNs must not store it. ``immutable`` is still correct
    # because the (model, init) tuple identifies a frozen snapshot.
    response.headers["Cache-Control"] = "private, max-age=86400, immutable"

    return {
        "model": model,
        "init_time": _format_init(init_time_unix),
        "valid_time": valid_time_iso,
        "level": level,
        "metric": metric,
        "hour": hour,
        "stride_hours": stride_hours,
        "lat": lat.tolist(),
        "lon": lon.tolist(),
        "values": _nan_to_none(slice_arr),
    }


# ---------------------------------------------------------------------------
# All-metrics slice (one (model, init, level, hour), every metric in one call)
# ---------------------------------------------------------------------------


@router.get("/all-metrics")
def get_all_metrics(
    response: Response,
    model: str = Query(...),
    init: str = Query(...),
    level: int = Query(...),
    hour: int = Query(..., ge=0),
    _user_id: str = Depends(current_user_id),
):
    """Return every metric grid for one (model, init, level, hour) in one
    response. Used by the cursor-following tooltip on the synoptic map: the
    frontend caches this once per hour change and reads all six values from
    memory on mousemove.

    Larger payload than the single-metric ``/`` endpoint (~6× depending on
    NaN density), but saves five network round-trips every time the user
    changes model / init / level / hour.
    """
    _validate_common_params(model, level)
    path, init_time_unix = _resolve_snapshot_path(model, init)

    with _open_snapshot(path) as npz:
        idx, stride_hours, valid_time_iso = _resolve_hour_index(npz, hour)
        lat = npz["lat"]
        lon = npz["lon"]
        metrics_out: dict[str, Any] = {}
        missing: list[str] = []
        for metric in VALID_METRICS:
            key = f"{metric}_{level}"
            if key not in npz.files:
                missing.append(metric)
                continue
            metrics_out[metric] = _nan_to_none(npz[key][idx])

    if not metrics_out:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot has no metrics at level={level} (was it built with this level?)",
        )

    # ``private`` (not ``public``): the response is auth-gated, so shared
    # proxies / CDNs must not store it. ``immutable`` is still correct
    # because the (model, init) tuple identifies a frozen snapshot.
    response.headers["Cache-Control"] = "private, max-age=86400, immutable"

    return {
        "model": model,
        "init_time": _format_init(init_time_unix),
        "valid_time": valid_time_iso,
        "level": level,
        "hour": hour,
        "stride_hours": stride_hours,
        "lat": lat.tolist(),
        "lon": lon.tolist(),
        "metrics": metrics_out,
        # Some legacy snapshots may be missing a metric; surface this so
        # the client can hide tooltip rows for those.
        "missing_metrics": missing,
    }
