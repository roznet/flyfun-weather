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

**Revisions (#666).**  The node publishes a frame without waiting for its
lightning and re-issues it once the lightning lands: ``<stamp>.json.gz`` is
revision 0, ``<stamp>.r1.json.gz`` revision 1.  Each revision is its own
immutable file, addressed by its *key* ``<stamp>.r<n>``; the listing points at
each frame's newest revision.  A bare-stamp request (a client from before
#666) gets the newest revision, uncached.

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
#: Whether cells the node marked as non-meteorological are kept out of the
#: route products (#696).  **Off by default**: the node measures and records
#: the evidence from the day this ships, but acting on it changes what counts
#: as a storm, so it waits for a replay-set measurement rather than riding in
#: on the same deploy.  See designs/meteorology-decisions.md §42.
CELLS_CLUTTER_SUPPRESS_ENV = "WB_CELLS_CLUTTER_SUPPRESS"
_CELLS_INBOX_DEFAULT = "/app/cells_inbox"

#: How long the store keeps a display file.  24 h, agreed on the issue
#: (2026-10-04): ~150 KB × 288 frames ≈ 43 MB.  Longer than the 3 h radar
#: retention on purpose — the overlay is cheap and a loop over past overlays
#: (a follow-up) should not be cut short by it.
RETENTION = timedelta(hours=24)

#: Past this age (from the overlay's own valid time) the overlay is not drawn
#: and the map says "cell analysis unavailable since HH:MMZ".  Since #666 the
#: home node publishes a frame ~4.5 min after its valid time (it no longer
#: waits for rain rate or lightning) and a new one every 5 min, so a healthy
#: feed is ~5–10 min old.  25 min is kept from #656 — not tightened — so a
#: provider hiccup of a frame or two does not blank the overlay; a stopped
#: loop shows as unavailable within ~15–20 min of its last push.
STALE_AFTER = timedelta(minutes=25)

#: A display file for all of Europe is ~150 KB gzipped; refuse anything absurd
#: before decompressing it into memory.
MAX_FILE_BYTES = 5_000_000
MAX_JSON_BYTES = 40_000_000

# One spelling per revision ("one URL = one immutable body"): r0 is the bare
# file name and `.r0` only as an API key; no leading zeros.
_NAME = re.compile(r"^(\d{8}T\d{4})(?:\.r([1-9]\d?))?\.json\.gz$")
_KEY = re.compile(r"^(\d{8}T\d{4})(?:\.r(0|[1-9]\d?))?$")
_POLICY = re.compile(r"^[A-Za-z0-9._-]+\+[0-9a-f]{4,64}$")
SUFFIX = ".json.gz"


class InvalidDisplay(ValueError):
    """A display file that must not be served."""


def cells_ingest_enabled() -> bool:
    return os.environ.get(CELLS_INGEST_ENV, "").strip().lower() in ("1", "true", "yes")


def clutter_suppress_enabled() -> bool:
    """Whether to drop suspect echoes from storms, alerts and ribbon bands.

    Read per call rather than cached, so flipping it is a restart of the web
    app and not a deploy.  Both ``suspect`` and ``confirmed`` are dropped when
    it is on: the two levels say how much evidence there is, not whether to
    act — an echo with the isolation evidence against it has no business in a
    confident storm row either way (#696).

    It holds suspect cells out of the storm rows, the §41 alerts that read
    them, the glance and the ribbon's core bands (``operational_cells``), and
    the ``rain20`` rings the node marked as a suspect echo's own skirt out of
    the rain bands (``suspect_outlines``, #702).  A genuine rain area holding a
    suspect core keeps its band, at the intensity of what is left.
    """
    return os.environ.get(CELLS_CLUTTER_SUPPRESS_ENV, "").strip().lower() in ("1", "true", "yes")


def inbox_dir() -> Path:
    return Path(os.environ.get(CELLS_INBOX_ENV, _CELLS_INBOX_DEFAULT).strip() or _CELLS_INBOX_DEFAULT)


def store_dir(data_dir: Path | str | None = None) -> Path:
    """``DATA_DIR/observed/cells/display`` — the same relative path as the node's."""
    return observed_root(data_dir) / "cells" / "display"


def name_of(name: str) -> tuple[str, int] | None:
    """``(stamp, revision)`` a display file is named after, or ``None``."""
    m = _NAME.match(name)
    if not m:
        return None
    try:
        parse_frame_stamp(m.group(1))
    except ValueError:
        return None
    return m.group(1), int(m.group(2) or 0)


def stamp_of(name: str) -> str | None:
    """The frame stamp a display file is named after, or ``None``."""
    parsed = name_of(name)
    return parsed[0] if parsed else None


def parse_key(key: str) -> tuple[str, int | None]:
    """``"<stamp>"`` → ``(stamp, None)`` (newest revision); ``"<stamp>.r<n>"``
    → ``(stamp, n)``.  Raises ``ValueError`` for anything else."""
    m = _KEY.match(key)
    if not m:
        raise ValueError(f"bad display key {key!r}")
    parse_frame_stamp(m.group(1))
    return m.group(1), (int(m.group(2)) if m.group(2) is not None else None)


def display_key(stamp: str, revision: int) -> str:
    """How the API addresses one revision: always explicit, ``<stamp>.r<n>``."""
    return f"{stamp}.r{revision}"


def file_name(stamp: str, revision: int) -> str:
    """Revision 0 keeps the pre-#666 name; later ones carry ``.r<n>``."""
    return f"{stamp}{SUFFIX}" if revision == 0 else f"{stamp}.r{revision}{SUFFIX}"


def validate(raw: bytes, stamp: str, revision: int = 0) -> dict[str, Any]:
    """Decode and check a display file; raise :class:`InvalidDisplay`.

    Checks the shape the clients rely on, not every field: ``schema``, a
    ``policy_version`` of the form ``<name>+<digest>``, a ``valid_time`` and
    ``revision`` (absent = 0) that match the file's own name, and ``cells`` /
    ``outlines`` of the right type.
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
    if data.get("revision", 0) != revision:
        raise InvalidDisplay(f"revision {data.get('revision')!r} does not match the name (r{revision})")
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
    # Indices the droplet itself acts on (#702): a wrong one would hide the
    # wrong ring, so a malformed block refuses the file like a bad outline.
    marks = data.get("suspect_outlines", {})
    if not isinstance(marks, dict):
        raise InvalidDisplay("suspect_outlines is not an object")
    for tier, idx in marks.items():
        n = len(data["outlines"].get(tier) or [])
        if not isinstance(idx, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n for i in idx
        ):
            raise InvalidDisplay(f"suspect_outlines[{tier}] is not a list of indices into outlines[{tier}]")
    return data


def suspect_outlines(display: dict[str, Any]) -> dict[str, set[int]]:
    """``{tier: {ring index}}`` of the outlines the node marked as a suspect
    echo's own (#702); empty for a frame with none or from before #702."""
    marks = display.get("suspect_outlines")
    if not isinstance(marks, dict):
        return {}
    return {tier: {i for i in idx if isinstance(i, int)} for tier, idx in marks.items() if isinstance(idx, list)}


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
    """A frame's newest stored revision."""

    stamp: str
    valid_time: datetime
    received_at: datetime  # ingest time of this revision
    path: Path
    revision: int = 0
    first_received_at: datetime | None = None  # ingest time of revision 0, if still stored

    @property
    def key(self) -> str:
        return display_key(self.stamp, self.revision)

    def entry(self, now: datetime) -> dict[str, Any]:
        first = self.first_received_at or self.received_at
        return {
            "stamp": self.stamp,
            # The URL key of the newest revision: `url_template` with {stamp}
            # replaced by this (#666). Older clients use `stamp` and get the
            # newest revision uncached.
            "key": self.key,
            "revision": self.revision,
            "valid_time": self.valid_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "first_received_at": first.isoformat(),
            "age_minutes": round((now - self.valid_time).total_seconds() / 60.0, 1),
        }


class DisplayStore:
    """``DATA_DIR/observed/cells/display``: validated display files by stamp."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else store_dir()

    def path(self, stamp: str, revision: int = 0) -> Path:
        return self.root / file_name(stamp, revision)

    def list(self) -> list[StoredDisplay]:
        """Every stored frame at its newest revision, newest first.
        ``received_at`` is when that revision was ingested."""
        if not self.root.is_dir():
            return []
        newest: dict[str, tuple[int, Path, datetime]] = {}
        first: dict[str, datetime] = {}
        for path in self.root.iterdir():
            parsed = name_of(path.name)
            if parsed is None:
                continue
            stamp, revision = parsed
            try:
                received = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if revision == 0:
                first[stamp] = received
            if stamp not in newest or revision > newest[stamp][0]:
                newest[stamp] = (revision, path, received)
        out = [StoredDisplay(stamp, parse_frame_stamp(stamp), received, path, revision, first.get(stamp))
               for stamp, (revision, path, received) in newest.items()]
        out.sort(key=lambda d: d.valid_time, reverse=True)
        return out

    def latest_revision(self, stamp: str) -> int | None:
        """The newest stored revision of ``stamp``, or ``None``."""
        found = None
        for path in self.root.glob(f"{stamp}*{SUFFIX}") if self.root.is_dir() else []:
            parsed = name_of(path.name)
            if parsed and parsed[0] == stamp and (found is None or parsed[1] > found):
                found = parsed[1]
        return found

    def write(self, stamp: str, raw: bytes, revision: int = 0) -> Path:
        path = self.path(stamp, revision)
        _atomic_write(path, raw)
        return path

    def read(self, stamp: str, revision: int = 0) -> dict[str, Any] | None:
        """The validated file for ``stamp`` at ``revision``, or ``None`` if absent or invalid."""
        path = self.path(stamp, revision)
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        hit = _READ_CACHE.get(key)
        if hit is not None:
            return hit
        try:
            data = validate(path.read_bytes(), stamp, revision)
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


# Parsed files keyed by (path, mtime, size): a revision's file never changes once
# ingested, and the route map and the "Now" tab ask for the same few stamps.
_READ_CACHE: dict[tuple, dict[str, Any]] = {}
_READ_CACHE_SIZE = 8


@dataclass
class IngestResult:
    # File keys as pushed: `<stamp>` for revision 0, `<stamp>.r<n>` after.
    accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    expired: int = 0
    duplicates: int = 0


# When the home node wrote each ingested revision (#751): the inbox file's
# mtime, which ``rsync -t`` (``cells/push.py``) carries over from the node. Not
# written into the display file, which stays deterministic so a replay
# reproduces it byte for byte. In memory only: the live tick reads the newest
# frame (< 25 min old), so a restart costs at most that long without it.
_COMPUTED_AT: dict[tuple[str, int], datetime] = {}
_COMPUTED_AT_SIZE = 2048


def computed_at(stamp: str, revision: int = 0) -> datetime | None:
    """When the node wrote this revision, if ingested since the last restart."""
    return _COMPUTED_AT.get((stamp, revision))


def _note_computed_at(stamp: str, revision: int, mtime: float) -> None:
    _COMPUTED_AT[(stamp, revision)] = datetime.fromtimestamp(mtime, tz=timezone.utc)
    while len(_COMPUTED_AT) > _COMPUTED_AT_SIZE:
        _COMPUTED_AT.pop(next(iter(_COMPUTED_AT)))


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
        parsed = name_of(path.name)
        if parsed is None or not path.is_file():
            continue
        stamp, revision = parsed
        try:
            if parse_frame_stamp(stamp) < now - retention:
                path.unlink()
                result.expired += 1
                continue
            node_mtime = path.stat().st_mtime
            raw = path.read_bytes()
            validate(raw, stamp, revision)
        except InvalidDisplay as exc:
            logger.warning("Rejected cell display %s: %s", path.name, exc)
            _reject(inbox, path)
            result.rejected.append(path.name.removesuffix(SUFFIX))
            continue
        except OSError:
            logger.warning("Could not read cell display %s", path, exc_info=True)
            continue
        # A revision is written once: the API serves it as immutable, so a
        # second copy must never replace the first. An identical re-push (the
        # node lost its push state) is simply dropped; different bytes are set
        # aside. A changed frame comes as a new revision (#666), never as new
        # bytes under an old name.
        existing = store.path(stamp, revision)
        if existing.exists():
            try:
                same = existing.read_bytes() == raw
            except OSError:
                same = False
            if same:
                try:
                    path.unlink()
                except OSError:
                    pass
                result.duplicates += 1
            else:
                logger.warning("Rejected cell display %s: revision already stored with different "
                               "bytes (served as immutable; keeping the first)", path.name)
                _reject(inbox, path)
                result.rejected.append(path.name.removesuffix(SUFFIX))
            continue
        try:
            store.write(stamp, raw, revision)  # received_at = now (the file's mtime)
            _note_computed_at(stamp, revision, node_mtime)
            path.unlink()
        except OSError:
            logger.warning("Could not store cell display %s", path.name, exc_info=True)
            continue
        result.accepted.append(path.name.removesuffix(SUFFIX))
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
        # {stamp} takes a frame's `key` (newest revision, immutable) — or, for
        # a client from before #666, its bare `stamp` (newest, uncached).
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
    marks = suspect_outlines(display)
    out["outlines"] = {}
    kept_marks: dict[str, list[int]] = {}
    for tier, lines in display.get("outlines", {}).items():
        kept = [i for i, line in enumerate(lines) if line and _box_overlaps(line, south, west, north, east)]
        out["outlines"][tier] = [lines[i] for i in kept]
        # The marks are indices, so they follow the rings they point at (#702).
        new = [j for j, i in enumerate(kept) if i in marks.get(tier, ())]
        if new:
            kept_marks[tier] = new
    out.pop("suspect_outlines", None)
    if kept_marks:
        out["suspect_outlines"] = kept_marks
    out["bbox"] = [south, west, north, east]
    return out
