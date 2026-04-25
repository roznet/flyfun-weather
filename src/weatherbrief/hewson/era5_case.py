"""Build a synoptic-snapshot NPZ from an existing ERA5 calibration Case.

Lets the dev server display historical events (storms, memorable flying days)
on the Synoptic Forecast tab without running the live precompute pipeline.

Reads a ``Case`` directory (e.g. ``data/calibration/2023-11-02_era5_ciaran``)
that already has T / Td / theta_e / u / v on disk, computes Hewson diagnostics
per (hour, level), and writes one NPZ in the same schema as the live
precompute snapshots so the existing endpoints + UI consume it unchanged.

Single-level cases are supported — e.g. the original Ciarán case has only
850 hPa. The frontend reads ``levels`` from the snapshot to know which level
buttons to enable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from weatherbrief.frontal.case import Case, load_case
from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.frontal.grid import build_terrain_mask
from weatherbrief.hewson.precompute import (
    tendency_k_per_hour,
    write_snapshot,
    resolve_output_dir,
    snapshot_path,
)

logger = logging.getLogger(__name__)


# Reuse the "era5" model namespace under ${DATA_DIR}/hewson/era5/.
ERA5_MODEL_KEY = "era5"


def build_synoptic_from_case(
    case_dir: Path | str,
    output_dir: Path | str | None = None,
    levels: list[int] | None = None,
) -> Path:
    """Compute Hewson diagnostics over a Case and write a synoptic snapshot.

    The output filename is the ISO Z timestamp of the case's first valid
    time — matches the live precompute path convention so the
    ``/api/hewson-map`` slice endpoint can find it via the same
    ``(model, init)`` lookup.

    Parameters
    ----------
    case_dir : path to a Case directory (must have ``meta.json`` and
        ``raw/era5.npz``).
    output_dir : override the snapshot root (mostly for tests).
    levels : restrict to a subset of levels present in the case. Defaults
        to all of them.

    Returns
    -------
    Path to the written NPZ at ``${DATA_DIR}/hewson/era5/<init_iso_z>.npz``.
    """
    case_dir = Path(case_dir)
    case = load_case(case_dir)

    if ERA5_MODEL_KEY not in case.models:
        raise ValueError(
            f"Case {case_dir} has models {case.models} — expected {ERA5_MODEL_KEY!r}. "
            f"This builder only handles ERA5 cases.",
        )

    available_levels = case.available_levels(ERA5_MODEL_KEY)
    if levels is None:
        levels = available_levels
    else:
        for L in levels:
            if L not in available_levels:
                raise ValueError(
                    f"Level {L} hPa not in case (available: {available_levels})",
                )
    levels = sorted(int(L) for L in levels)

    hours = case.available_hours(ERA5_MODEL_KEY)
    if not hours:
        raise ValueError(f"Case {case_dir} has no hours for {ERA5_MODEL_KEY!r}")

    valid_times = case.valid_times[ERA5_MODEL_KEY]
    n_time = len(hours)
    n_lat, n_lon = len(case.lat), len(case.lon)

    # Stride between consecutive hours (used by the tendency calculation
    # and persisted in the snapshot so the frontend slider can adapt).
    if n_time >= 2:
        stride_hours = max(int(hours[1] - hours[0]), 1)
    else:
        stride_hours = 6  # safe default for ERA5

    terrain_mask = build_terrain_mask(case.lat, case.lon)

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

    for i, hour in enumerate(hours):
        for L in levels:
            fields = case.fields(ERA5_MODEL_KEY, hour, level_hPa=L)
            if fields is None:
                continue
            # ``case.fields(..., level_hPa=L)`` always returns the legacy
            # ``u850``/``v850`` keys regardless of the requested level —
            # the values are from level L (see Case.fields docstring in
            # frontal/case.py). Diagnostics are correctly computed at L.
            diag = compute_hewson_diagnostics(
                fields["theta_e"], case.lat, case.lon,
                fields["u850"], fields["v850"],
                terrain_mask=terrain_mask,
            )
            per_level[L]["theta_e"][i] = fields["theta_e"]
            per_level[L]["gradient"][i] = diag["gradient"]
            per_level[L]["neg_laplacian"][i] = diag["neg_laplacian"]
            per_level[L]["tfp"][i] = diag["tfp"]
            per_level[L]["advection"][i] = diag["advection"]

    # Tendency across the time stack — one-sided at edges per the live path.
    for L in levels:
        per_level[L]["tendency"] = tendency_k_per_hour(
            per_level[L]["theta_e"], step_hours=stride_hours,
        )

    # ERA5 cases have init_time=0 (no real model run). Use the first valid
    # time as the "init" so the snapshot filename and manifest sort sensibly
    # — the front-end treats it as just a label.
    first_valid = valid_times[0]
    init_time_unix = int(np.datetime64(first_valid, "s").astype("int64"))

    out_path = snapshot_path(ERA5_MODEL_KEY, init_time_unix, output_dir)

    write_snapshot(
        out_path,
        init_time_unix,
        np.asarray(valid_times, dtype="datetime64[ns]"),
        case.lat,
        case.lon,
        levels,
        stride_hours,
        per_level,
    )

    size_kb = out_path.stat().st_size / 1024
    logger.info(
        "ERA5 synoptic case: wrote %s (%d × %dh steps, levels=%s, %.1f KB)",
        out_path, n_time, stride_hours, levels, size_kb,
    )
    return out_path
