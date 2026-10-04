"""The cell catalogue: one small file per reflectivity frame.

This is the wire format the droplet will ingest (a later slice), so it is a
small explicit schema rather than a dump of internal objects.  ``schema``
versions the *shape*; ``policy_version`` versions the *numbers* that produced
it.

**Byte-for-byte reproducible.**  Same frames + same policy ⇒ identical bytes:
keys sorted, floats rounded, no wall-clock fields (processing time and the
like go to the run log, not here), and gzip written with ``mtime=0``.  That is
what lets ``--replay`` prove a refactor changed nothing.

Layout under an observed archive root (the same relative paths on the home
nodes, the NAS and — for ``cells/display`` — the droplet's ``observed/``)::

    <root>/opera_dbzh/20261003T1405.h5          raw frames, one dir per source
    <root>/cells/catalogues/20261003/20261003T1405.json.gz
    <root>/cells/display/20261003T1405.json.gz  map file pushed to the droplet (#656)
    <root>/cells/scores/<day>.jsonl, cells/runs/<day>.jsonl, cells/state.json

One directory per UTC day keeps catalogue listings short on a store that grows
by 288 files a day.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path

from ..frames import frame_stamp

logger = logging.getLogger(__name__)

SCHEMA = "observed-cells/1"


CELLS_DIR = "cells"


def cells_dir(root: Path) -> Path:
    """Where the analysis lives under an observed root (frames sit beside it)."""
    return Path(root) / CELLS_DIR


def catalogue_path(root: Path, valid_time: datetime) -> Path:
    stamp = frame_stamp(valid_time)
    return cells_dir(root) / "catalogues" / stamp[:8] / f"{stamp}.json.gz"


#: Most revisions a frame's display file can have (#666): r0 at analysis, r1
#: when its lightning lands later.  A bound, not a target.
MAX_DISPLAY_REVISION = 9


def display_key(valid_time: datetime, revision: int = 0) -> str:
    """``<stamp>`` for the first publication, ``<stamp>.r<n>`` for a revision.

    Revision 0 keeps the pre-#666 file name, so an older droplet still
    ingests it; the API addresses every revision explicitly as ``.r<n>``.
    """
    stamp = frame_stamp(valid_time)
    return stamp if revision == 0 else f"{stamp}.r{revision}"


def display_path(root: Path, valid_time: datetime, revision: int = 0) -> Path:
    """The map-ready file for one frame (#656): built on the home node, mirrored
    to the droplet's ``observed/cells/display/`` under the same name.  Each
    revision (#666) is its own immutable file."""
    return cells_dir(root) / "display" / f"{display_key(valid_time, revision)}.json.gz"


def latest_display(root: Path, valid_time: datetime) -> tuple[Path, int] | None:
    """The newest revision of a frame's display file, or ``None`` if none."""
    found = None
    for revision in range(MAX_DISPLAY_REVISION + 1):
        path = display_path(root, valid_time, revision)
        if not path.exists():
            break
        found = (path, revision)
    return found


def failure_path(root: Path, valid_time: datetime) -> Path:
    """Marker for a frame the loop could not analyse.

    Without it a failing or unreadable frame has no catalogue, so every tick
    for the whole catch-up window would decode it again and log again.  The
    loop retries a marked frame once after ``FAILURE_RETRY`` (a transient
    failure — memory, a disk blip — should not cost the frame); a second
    failure is final.  ``retry-failed`` clears markers; ``replay`` ignores them.
    """
    stamp = frame_stamp(valid_time)
    return cells_dir(root) / "catalogues" / stamp[:8] / f"{stamp}.failed.json"


def r(value, digits: int):
    """Round for the wire; NaN/inf become ``None`` (JSON has no NaN)."""
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    out = round(value, digits)
    return 0.0 if out == 0 else out  # no "-0.0" in the bytes


def encode(catalogue: dict) -> bytes:
    raw = json.dumps(catalogue, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return gzip.compress(raw.encode("utf-8"), compresslevel=6, mtime=0)


def write_catalogue(path: Path, catalogue: dict) -> int:
    data = encode(catalogue)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)


def read_catalogue(path: Path) -> dict | None:
    """The catalogue at ``path``, or ``None`` if absent **or unreadable**.

    A corrupt file is treated as missing (and logged) rather than raised: it is
    read as the next frame's lineage predecessor, and an exception there would
    fail the frames after it too.
    """
    try:
        return json.loads(gzip.decompress(path.read_bytes()))
    except FileNotFoundError:
        return None
    except (OSError, EOFError, ValueError) as exc:  # BadGzipFile is an OSError
        logger.warning("Unreadable cell catalogue %s, treated as missing: %r", path, exc)
        return None
