"""Droplet side of the cell overlay (#656): ingest, store, read.

The home node analyses the radar (``observed/cells``) and pushes one small
*display file* per frame into an inbox here (``CELLS_INBOX_DIR``, bind-mounted
from ``HOST_CELLS_INBOX`` like the forecast offload's snapshot inbox).  The
droplet never analyses anything: it validates what lands, moves it into its
own store and serves it.

    inbox:  CELLS_INBOX_DIR/<stamp>.json.gz            written by the node's rsync
    store:  DATA_DIR/observed/cells/display/<stamp>.json.gz   owned and purged here (24 h)

Two directories rather than one because each has one writer: the node's ssh
user writes the inbox, the app's own user owns the store and is the only one
that deletes from it.

The stamp is the frame key (``<YYYYMMDD>T<HHMM>``), identical to the
droplet's own radar stamps, so a radar tile and a cell overlay pair by name.

Gated on ``WB_CELLS_INGEST_ENABLED`` (off by default): unset, nothing is
ingested and the endpoints say ``disabled``.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .cells.display import DISPLAY_SCHEMA
from .frames import _atomic_write, observed_root, parse_frame_stamp

logger = logging.getLogger(__name__)

CELLS_INGEST_ENV = "WB_CELLS_INGEST_ENABLED"
CELLS_INBOX_ENV = "CELLS_INBOX_DIR"
_CELLS_INBOX_DEFAULT = "/app/cells_inbox"

#: How long the store keeps a display file.  24 h, agreed on the issue
#: (2026-10-04): ~150 KB × 288 frames ≈ 43 MB.  Longer than the 3 h radar
#: retention on purpose — the overlay is cheap and a loop over past overlays
#: (a follow-up) should not be cut short by it.
RETENTION = timedelta(hours=24)

#: Past this age (from the overlay's own valid time) the overlay is not drawn
#: and the map says "cell analysis unavailable since HH:MMZ".  The home node
#: publishes a frame ~5–10 min after its valid time (radar lag + the wait for
#: its rain-rate and lightning slots, up to ATTRIBUTE_WAIT = 15 min), and a new
#: frame every 5 min, so a healthy feed is ~5–15 min old and at worst ~20.
#: 25 min keeps a healthy feed from flickering; a stopped loop shows as
#: unavailable within ~10–15 min of its last push.
STALE_AFTER = timedelta(minutes=25)

#: A display file for all of Europe is ~150 KB gzipped; refuse anything absurd
#: before decompressing it into memory.
MAX_FILE_BYTES = 5_000_000
MAX_JSON_BYTES = 40_000_000

_NAME = re.compile(r"^(\d{8}T\d{4})\.json\.gz$")
_POLICY = re.compile(r"^[A-Za-z0-9._-]+\+[0-9a-f]{4,64}$")
SUFFIX = ".json.gz"


class InvalidDisplay(ValueError):
    """A display file that must not be served."""


def cells_ingest_enabled() -> bool:
    return os.environ.get(CELLS_INGEST_ENV, "").strip().lower() in ("1", "true", "yes")


def inbox_dir() -> Path:
    return Path(os.environ.get(CELLS_INBOX_ENV, _CELLS_INBOX_DEFAULT).strip() or _CELLS_INBOX_DEFAULT)


def store_dir(data_dir: Path | str | None = None) -> Path:
    """``DATA_DIR/observed/cells/display`` — the same relative path as the node's."""
    return observed_root(data_dir) / "cells" / "display"


def stamp_of(name: str) -> str | None:
    """The frame stamp a display file is named after, or ``None``."""
    m = _NAME.match(name)
    if not m:
        return None
    try:
        parse_frame_stamp(m.group(1))
    except ValueError:
        return None
    return m.group(1)


def validate(raw: bytes, stamp: str) -> dict[str, Any]:
    """Decode and check a display file; raise :class:`InvalidDisplay`.

    Checks the shape the clients rely on, not every field: ``schema``, a
    ``policy_version`` of the form ``<name>+<digest>``, a ``valid_time`` that
    matches the file's own name, and ``cells`` / ``outlines`` of the right type.
    """
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidDisplay(f"{len(raw)} bytes exceeds {MAX_FILE_BYTES}")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
            text = handle.read(MAX_JSON_BYTES + 1)
        if len(text) > MAX_JSON_BYTES:
            raise InvalidDisplay("decompressed size too large")
        data = json.loads(text)
    except InvalidDisplay:
        raise
    except (OSError, EOFError, ValueError) as exc:
        raise InvalidDisplay(f"not gzipped JSON: {exc!r}") from exc
    if not isinstance(data, dict):
        raise InvalidDisplay("not a JSON object")
    if data.get("schema") != DISPLAY_SCHEMA:
        raise InvalidDisplay(f"schema {data.get('schema')!r}, expected {DISPLAY_SCHEMA!r}")
    policy = data.get("policy_version")
    if not isinstance(policy, str) or not _POLICY.match(policy):
        raise InvalidDisplay(f"policy_version {policy!r}")
    try:
        valid = datetime.fromisoformat(str(data.get("valid_time")))
    except ValueError as exc:
        raise InvalidDisplay("valid_time unreadable") from exc
    if valid.tzinfo is None or valid != parse_frame_stamp(stamp):
        raise InvalidDisplay(f"valid_time {data.get('valid_time')} does not match {stamp}")
    if not isinstance(data.get("cells"), list) or not isinstance(data.get("outlines"), dict):
        raise InvalidDisplay("cells/outlines missing")
    # The fields the server itself reads (bbox filter) and every client draws
    # from: one malformed entry must be refused here, not 500 every request.
    for i, cell in enumerate(data["cells"]):
        if not isinstance(cell, dict) or not _is_latlon([cell.get("lat"), cell.get("lon")]):
            raise InvalidDisplay(f"cell {i} has no numeric lat/lon")
    for tier, lines in data["outlines"].items():
        if not isinstance(lines, list) or not all(
            isinstance(line, list) and all(_is_latlon(pt) for pt in line) for line in lines
        ):
            raise InvalidDisplay(f"outlines[{tier}] is not a list of [lat, lon] polylines")
    return data


def _is_latlon(point: Any) -> bool:
    return (
        isinstance(point, (list, tuple))
        and len(point) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in point)
        and -90 <= point[0] <= 90
        and -180 <= point[1] <= 180
    )


@dataclass(frozen=True)
class StoredDisplay:
    stamp: str
    valid_time: datetime
    received_at: datetime
    path: Path

    def entry(self, now: datetime) -> dict[str, Any]:
        return {
            "stamp": self.stamp,
            "valid_time": self.valid_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "age_minutes": round((now - self.valid_time).total_seconds() / 60.0, 1),
        }


class DisplayStore:
    """``DATA_DIR/observed/cells/display``: validated display files by stamp."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else store_dir()

    def path(self, stamp: str) -> Path:
        return self.root / f"{stamp}{SUFFIX}"

    def list(self) -> list[StoredDisplay]:
        """Every stored file, newest first.  ``received_at`` is when it was ingested."""
        if not self.root.is_dir():
            return []
        out = []
        for path in self.root.iterdir():
            stamp = stamp_of(path.name)
            if stamp is None:
                continue
            try:
                received = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            out.append(StoredDisplay(stamp, parse_frame_stamp(stamp), received, path))
        out.sort(key=lambda d: d.valid_time, reverse=True)
        return out

    def write(self, stamp: str, raw: bytes) -> Path:
        path = self.path(stamp)
        _atomic_write(path, raw)
        return path

    def read(self, stamp: str) -> dict[str, Any] | None:
        """The validated file for ``stamp``, or ``None`` if absent or invalid."""
        path = self.path(stamp)
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        hit = _READ_CACHE.get(key)
        if hit is not None:
            return hit
        try:
            data = validate(path.read_bytes(), stamp)
        except (OSError, InvalidDisplay) as exc:
            logger.warning("Cell display %s unreadable: %s", path.name, exc)
            return None
        _READ_CACHE[key] = data
        while len(_READ_CACHE) > _READ_CACHE_SIZE:
            _READ_CACHE.pop(next(iter(_READ_CACHE)))
        return data

    def purge(self, now: datetime | None = None, retention: timedelta = RETENTION) -> int:
        """Delete files whose *valid time* is older than ``retention``."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - retention
        removed = 0
        if not self.root.is_dir():
            return 0
        for path in self.root.iterdir():
            stamp = stamp_of(path.name)
            if stamp is None:
                # A dead writer's temp file: reclaim after a grace period.
                if path.name.startswith(".tmp-"):
                    try:
                        if path.stat().st_mtime < now.timestamp() - 900:
                            path.unlink()
                    except OSError:
                        pass
                continue
            if parse_frame_stamp(stamp) < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("Could not purge cell display %s", path, exc_info=True)
        return removed


# Parsed files keyed by (path, mtime, size): a stamp's file never changes once
# ingested, and the route map and the "Now" tab ask for the same few stamps.
_READ_CACHE: dict[tuple, dict[str, Any]] = {}
_READ_CACHE_SIZE = 8


@dataclass
class IngestResult:
    accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    expired: int = 0


def ingest(inbox: Path, store: DisplayStore, now: datetime | None = None,
           retention: timedelta = RETENTION) -> IngestResult:
    """Move every valid display file from ``inbox`` into ``store``.

    Invalid files go to ``inbox/rejected/`` (kept 24 h for a look, logged once;
    ``purge_rejected``);
    files already past retention are dropped.  Temp files (rsync's
    ``.<name>.XXXXXX``) and anything not named ``<stamp>.json.gz`` are left
    alone.  Never raises for one bad file.
    """
    now = now or datetime.now(timezone.utc)
    result = IngestResult()
    if not inbox.is_dir():
        return result
    for path in sorted(inbox.iterdir()):
        stamp = stamp_of(path.name)
        if stamp is None or not path.is_file():
            continue
        try:
            if parse_frame_stamp(stamp) < now - retention:
                path.unlink()
                result.expired += 1
                continue
            raw = path.read_bytes()
            validate(raw, stamp)
        except InvalidDisplay as exc:
            logger.warning("Rejected cell display %s: %s", path.name, exc)
            _reject(inbox, path)
            result.rejected.append(stamp)
            continue
        except OSError:
            logger.warning("Could not read cell display %s", path, exc_info=True)
            continue
        try:
            store.write(stamp, raw)  # received_at = now (the file's mtime)
            path.unlink()
        except OSError:
            logger.warning("Could not store cell display %s", path.name, exc_info=True)
            continue
        result.accepted.append(stamp)
    return result


def purge_rejected(inbox: Path, now: datetime | None = None, retention: timedelta = RETENTION) -> int:
    """Drop set-aside files older than ``retention`` (by mtime): kept for a
    look, not forever — a broken node would otherwise add one every 5 min."""
    directory = inbox / "rejected"
    if not directory.is_dir():
        return 0
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - retention.total_seconds()
    removed = 0
    for path in directory.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _reject(inbox: Path, path: Path) -> None:
    try:
        target = inbox / "rejected"
        target.mkdir(exist_ok=True)
        shutil.move(str(path), str(target / path.name))
    except OSError:
        try:
            path.unlink()
        except OSError:
            logger.warning("Could not set aside rejected cell display %s", path, exc_info=True)


def frames_status(store: DisplayStore, now: datetime | None = None) -> dict[str, Any]:
    """The listing the clients use: frames newest first, plus the stale state.

    ``unavailable_since`` is the newest overlay's own valid time whenever it is
    too old to draw (``None`` when nothing was ever received) — the map's
    "cell analysis unavailable since HH:MMZ".
    """
    now = now or datetime.now(timezone.utc)
    stored = store.list()
    newest = stored[0] if stored else None
    stale = newest is None or now - newest.valid_time > STALE_AFTER
    return {
        "enabled": True,
        "frames": [d.entry(now) for d in stored],
        "newest": newest.entry(now) if newest else None,
        "stale": stale,
        "stale_after_minutes": STALE_AFTER.total_seconds() / 60.0,
        "unavailable_since": newest.valid_time.isoformat() if (stale and newest) else None,
        "url_template": "/api/observed/cells/{stamp}.json",
    }


def disabled_status() -> dict[str, Any]:
    return {
        "enabled": False,
        "frames": [],
        "newest": None,
        "stale": True,
        "stale_after_minutes": STALE_AFTER.total_seconds() / 60.0,
        "unavailable_since": None,
        "url_template": "/api/observed/cells/{stamp}.json",
    }


def _box_overlaps(points: list, south: float, west: float, north: float, east: float) -> bool:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return not (max(lats) < south or min(lats) > north or max(lons) < west or min(lons) > east)


def filter_bbox(display: dict[str, Any], south: float, west: float, north: float,
                east: float) -> dict[str, Any]:
    """A copy with only the cells inside, and the outlines touching, the box.

    A cell is kept when its centroid is inside; an outline when its own extent
    overlaps the box (a rain band crossing the route is drawn whole, not cut).
    """
    out = dict(display)
    out["cells"] = [c for c in display.get("cells", [])
                    if south <= c["lat"] <= north and west <= c["lon"] <= east]
    out["outlines"] = {
        tier: [line for line in lines if line and _box_overlaps(line, south, west, north, east)]
        for tier, lines in display.get("outlines", {}).items()
    }
    out["bbox"] = [south, west, north, east]
    return out
