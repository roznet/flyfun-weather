"""Ingest Hewson snapshots computed off-box (compute offload).

A home compute node runs the same ``run_once`` precompute and rsyncs its
``hewson/<model>/<init>.npz`` tree into an inbox the serving box mounts
read-only. This module copies each valid snapshot from that inbox into the
serving box's own ``{DATA_DIR}/hewson`` tree, which is the only place readers
(``tasks/fronts.py``, the Hewson map endpoints) ever look — so a node that goes
dark changes nothing for them, and the local fallback writes to the same place.

Same shape as the forecast-cycle offload (``tasks/snapshot_artifact.py``):

* the inbox is never written (mounted ``:ro``); the node's ``rsync --delete``
  mirror is what keeps it pruned to the node's own retention window;
* a file is ingested only after it validates in full — every array is read, so
  a torn or corrupt drop fails its zip CRC here instead of in a briefing;
* the copy lands under a dot-prefixed temp name and is ``os.replace``-d into
  place, so a reader globbing ``*.npz`` never sees a partial file.
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from weatherbrief.frontal.grid import build_grid_coords
from weatherbrief.hewson.precompute import (
    DEFAULT_MODELS,
    DEFAULT_RETENTION_HOURS,
    _init_to_iso_z,
    resolve_output_dir,
    snapshot_path,
)

logger = logging.getLogger(__name__)

# Every per-level stack ``write_snapshot`` emits. A snapshot missing one would
# load fine and then KeyError deep inside a reader, so it is rejected up front.
REQUIRED_METRICS: tuple[str, ...] = (
    "theta_e", "gradient", "neg_laplacian", "tfp", "advection", "tendency",
)


class SnapshotValidationError(ValueError):
    """An inbox snapshot is unreadable or does not match the serving grid."""


@dataclass
class InboxIngestResult:
    """What one pass over the inbox did.

    ``ingested`` maps model → init times (unix) copied in this pass;
    ``rejected`` lists inbox paths that failed validation (the caller keeps
    them in a skip set so a bad drop is not re-read every poll).
    """

    ingested: dict[str, list[int]] = field(default_factory=dict)
    rejected: list[Path] = field(default_factory=list)


def _init_from_filename(path: Path) -> int:
    """``2026-10-16T00:00:00Z.npz`` → unix seconds; raises ValueError."""
    dt = datetime.fromisoformat(path.stem.replace("Z", "+00:00"))
    return int(dt.timestamp())


def validate_snapshot(
    path: Path, lat: np.ndarray, lon: np.ndarray,
) -> int:
    """Check an inbox snapshot end to end; return its init time (unix).

    Reads every member, one array at a time (~3 MB each), so the zip CRC of
    each is verified without holding the whole snapshot in memory.
    """
    try:
        name_init = _init_from_filename(path)
    except ValueError as exc:
        raise SnapshotValidationError(f"unrecognised filename {path.name}") from exc

    try:
        with np.load(path) as npz:
            files = set(npz.files)
            for key in ("init_time_unix", "valid_times", "lat", "lon", "levels"):
                if key not in files:
                    raise SnapshotValidationError(f"{path.name}: missing {key}")
            init = int(npz["init_time_unix"])
            if init != name_init:
                raise SnapshotValidationError(
                    f"{path.name}: init_time_unix {_init_to_iso_z(init)} "
                    f"does not match the filename",
                )
            valid_times = npz["valid_times"]
            if valid_times.ndim != 1 or valid_times.shape[0] == 0:
                raise SnapshotValidationError(f"{path.name}: empty valid_times")
            if not (
                npz["lat"].shape == lat.shape and np.allclose(npz["lat"], lat)
                and npz["lon"].shape == lon.shape and np.allclose(npz["lon"], lon)
            ):
                raise SnapshotValidationError(
                    f"{path.name}: grid does not match the serving grid",
                )
            expected_shape = (valid_times.shape[0], len(lat), len(lon))
            for level in (int(L) for L in npz["levels"]):
                for metric in REQUIRED_METRICS:
                    key = f"{metric}_{level}"
                    if key not in files:
                        raise SnapshotValidationError(f"{path.name}: missing {key}")
                    arr = npz[key]
                    if arr.shape != expected_shape:
                        raise SnapshotValidationError(
                            f"{path.name}: {key} shape {arr.shape} != "
                            f"{expected_shape}",
                        )
    except SnapshotValidationError:
        raise
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile,
            zlib.error) as exc:
        raise SnapshotValidationError(f"{path.name}: unreadable ({exc})") from exc
    return init


def ingest_inbox(
    inbox: Path | str,
    output_dir: Path | str | None = None,
    *,
    models: list[str] | tuple[str, ...] = DEFAULT_MODELS,
    skip: set[Path] | None = None,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
    now: datetime | None = None,
) -> InboxIngestResult:
    """Copy every valid inbox snapshot the serving box does not have yet.

    Skips files already present locally (by filename — the init), dot-prefixed
    names (rsync temp files), paths in ``skip``, and inits older than the
    retention window — otherwise a snapshot the local purge just removed would
    be copied straight back from the node's mirror on the next poll.
    """
    inbox = Path(inbox)
    out_root = Path(output_dir) if output_dir is not None else None
    skip = skip or set()
    now = now or datetime.now(timezone.utc)
    cutoff = int((now - timedelta(hours=retention_hours)).timestamp())
    lat, lon = build_grid_coords()
    result = InboxIngestResult()

    for model in models:
        src_dir = inbox / model
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.glob("*.npz")):
            if src.name.startswith(".") or src in skip:
                continue
            try:
                name_init = _init_from_filename(src)
            except ValueError:
                continue
            if name_init < cutoff:
                continue
            dest = snapshot_path(model, name_init, out_root)
            if dest.exists():
                continue
            try:
                init = validate_snapshot(src, lat, lon)
            except SnapshotValidationError:
                logger.warning(
                    "Hewson inbox: rejected %s/%s", model, src.name, exc_info=True,
                )
                result.rejected.append(src)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.ingest-tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
            result.ingested.setdefault(model, []).append(init)
            logger.info(
                "Hewson inbox: ingested %s %s (%.1f MB)",
                model, src.name, dest.stat().st_size / 1e6,
            )
    return result


def newest_local_init(
    model: str, output_dir: Path | str | None = None,
) -> int | None:
    """Init time (unix) of the newest local snapshot for ``model``, or None."""
    subdir = resolve_output_dir(output_dir) / model
    newest: int | None = None
    for path in subdir.glob("*.npz") if subdir.is_dir() else ():
        if path.name.startswith("."):
            continue
        try:
            init = _init_from_filename(path)
        except ValueError:
            continue
        if newest is None or init > newest:
            newest = init
    return newest
