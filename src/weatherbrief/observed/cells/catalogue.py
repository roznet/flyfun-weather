"""The cell catalogue: one small file per reflectivity frame.

This is the wire format the droplet will ingest (a later slice), so it is a
small explicit schema rather than a dump of internal objects.  ``schema``
versions the *shape*; ``policy_version`` versions the *numbers* that produced
it.

**Byte-for-byte reproducible.**  Same frames + same policy ⇒ identical bytes:
keys sorted, floats rounded, no wall-clock fields (processing time and the
like go to the run log, not here), and gzip written with ``mtime=0``.  That is
what lets ``--replay`` prove a refactor changed nothing.

Layout under the cells root::

    catalogues/20261003/20261003T1405.json.gz

One directory per UTC day keeps listings short on a store that grows by 288
files a day.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path

from ..frames import frame_stamp

SCHEMA = "observed-cells/1"


def catalogue_path(root: Path, valid_time: datetime) -> Path:
    stamp = frame_stamp(valid_time)
    return root / "catalogues" / stamp[:8] / f"{stamp}.json.gz"


def failure_path(root: Path, valid_time: datetime) -> Path:
    """Marker for a frame the loop could not analyse — it is not retried.

    Without it a failing or unreadable frame has no catalogue, so every tick
    for the whole catch-up window would decode it again and log again.
    ``replay`` ignores markers: it is the tool for re-trying after a fix.
    """
    stamp = frame_stamp(valid_time)
    return root / "catalogues" / stamp[:8] / f"{stamp}.failed.json"


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
    try:
        return json.loads(gzip.decompress(path.read_bytes()))
    except FileNotFoundError:
        return None
