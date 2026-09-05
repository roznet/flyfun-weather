"""Frame model and on-disk frame store for observed products.

A *frame* is one source's grid (or point cloud) at one valid time, as it
arrived.  The collector writes frames here; the sampler reads windows out of
them.  Nothing between the two re-fetches: a briefing request performs **zero
network I/O** for observed conditions, which is the acceptance criterion the
whole layout exists to satisfy.

Layout, one directory per source under ``$DATA_DIR/observed``::

    observed/opera_dbzh/20260825T1405.h5
    observed/opera_dbzh/20260825T1405.json     <- sidecar metadata
    observed/eumetsat_ctth/20260825T1400.nc
    observed/eumetsat_ctth/20260825T1400.json

The sidecar carries what a consumer needs *without opening the payload*:
valid time, receipt time, grid descriptor and the attribution read out of the
frame's own ``how``/``license`` metadata.  That keeps "which frame is newest
and who made it" a directory listing rather than an HDF5 open.

Retention is per source, because the products differ by two orders of
magnitude in size: three hours of radar and lightning is ~30 MB, one hour of
CTTH is ~400 MB.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from weatherbrief.models.observed import ObservedAttribution

from .grid import GridSpec, GridWindow

logger = logging.getLogger(__name__)

# --- Source identities -----------------------------------------------------

SOURCE_OPERA_DBZH = "opera_dbzh"
SOURCE_OPERA_RATE = "opera_rate"
SOURCE_EUMETSAT_LI = "eumetsat_li"
SOURCE_EUMETSAT_CTTH = "eumetsat_ctth"

ALL_SOURCES = (
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_EUMETSAT_LI,
    SOURCE_EUMETSAT_CTTH,
)


@dataclass(frozen=True)
class SourceSpec:
    """Static shape of one observed source.

    ``interval`` is the publication cadence, ``retention`` how long frames are
    kept, and ``window_minutes`` the acquisition/accumulation window width
    (``0`` for an instantaneous retrieval). DBZH is a max-reflectivity
    composite whose contributing scans come from the preceding 10-minute
    window, so an echo may predate the frame time, on top of delivery lag.
    """

    key: str
    label: str
    quantity: str
    units: str
    extension: str
    interval: timedelta
    retention: timedelta
    window_minutes: float = 0.0
    # Beyond this, a frame stops being "current conditions".  Distinct from
    # ``retention``: an old radar frame is still worth keeping for the map's
    # age-faded lightning trail, but it must not be presented as what is
    # there now.
    max_display_age: timedelta = timedelta(minutes=30)
    # Approximate on-disk size of one frame, used only for the storage
    # estimate in the admin/status surfaces.
    typical_bytes: int = 0


SOURCE_SPECS: dict[str, SourceSpec] = {
    SOURCE_OPERA_DBZH: SourceSpec(
        key=SOURCE_OPERA_DBZH,
        label="OPERA radar reflectivity",
        quantity="DBZH",
        units="dBZ",
        extension="h5",
        interval=timedelta(minutes=5),
        retention=timedelta(hours=3),
        # ODIM product is the maximum over the preceding 10-minute window.
        window_minutes=10.0,
        typical_bytes=3_500_000,
    ),
    SOURCE_OPERA_RATE: SourceSpec(
        key=SOURCE_OPERA_RATE,
        label="OPERA surface rain rate",
        quantity="RATE",
        units="mm/h",
        extension="h5",
        interval=timedelta(minutes=15),
        retention=timedelta(hours=3),
        window_minutes=15.0,
        typical_bytes=3_500_000,
    ),
    SOURCE_EUMETSAT_LI: SourceSpec(
        key=SOURCE_EUMETSAT_LI,
        label="MTG Lightning Imager flashes",
        quantity="flash",
        units="count",
        extension="nc",
        interval=timedelta(minutes=10),
        retention=timedelta(hours=3),
        window_minutes=10.0,
        typical_bytes=6_000_000,
    ),
    SOURCE_EUMETSAT_CTTH: SourceSpec(
        key=SOURCE_EUMETSAT_CTTH,
        label="MTG cloud top height",
        quantity="cloud_top_height",
        units="m",
        extension="nc",
        interval=timedelta(minutes=10),
        # One hour only: ~95 MB a frame at a 10-minute cadence is ~570 MB/h,
        # and a cloud-top field older than an hour answers nothing a pilot is
        # asking at D-0.
        retention=timedelta(hours=1),
        typical_bytes=95_000_000,
    ),
}


# --- In-memory frames ------------------------------------------------------


@dataclass
class GridFrame:
    """One window of one gridded frame, decoded to physical units.

    The three-state discipline lives in the two masks.  ``nodata`` marks
    pixels the sensor does not cover; ``undetect`` marks pixels it covered and
    found empty.  ``values`` is NaN in both cases — a consumer that reads
    ``values`` alone cannot tell them apart and *must* consult the masks,
    which is the point.  ``detected`` is the complement of the two.
    """

    source: str
    quantity: str
    units: str
    valid_time: datetime
    window_minutes: float
    grid: GridSpec
    window: GridWindow
    values: np.ndarray
    nodata: np.ndarray
    undetect: np.ndarray
    attribution: ObservedAttribution = field(default_factory=ObservedAttribution)
    aux: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def detected(self) -> np.ndarray:
        return ~self.nodata & ~self.undetect

    def age_minutes(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.valid_time).total_seconds() / 60.0


@dataclass
class FlashFrame:
    """One lightning frame: a point cloud, not a grid.

    This point product carries no coverage mask. Zero means no detections
    reported in the acquisition window, not verified full-disc coverage.
    """

    source: str
    valid_time: datetime
    window_minutes: float
    lats: np.ndarray
    lons: np.ndarray
    times: np.ndarray  # datetime64[s], same length as lats/lons
    attribution: ObservedAttribution = field(default_factory=ObservedAttribution)

    def age_minutes(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.valid_time).total_seconds() / 60.0


# --- On-disk store ---------------------------------------------------------

_STAMP_FORMAT = "%Y%m%dT%H%M"


def frame_stamp(valid_time: datetime) -> str:
    """Filename stamp for a frame's valid time (UTC, minute resolution)."""
    if valid_time.tzinfo is None:
        raise ValueError("frame valid_time must be timezone-aware")
    return valid_time.astimezone(timezone.utc).strftime(_STAMP_FORMAT)


def parse_frame_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class StoredFrame:
    """A frame on disk, described by its sidecar."""

    source: str
    valid_time: datetime
    path: Path
    meta: dict[str, Any]

    @property
    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def attribution(self) -> ObservedAttribution:
        return ObservedAttribution.model_validate(self.meta.get("attribution") or {})

    def age_minutes(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.valid_time).total_seconds() / 60.0


def observed_root(data_dir: Path | str | None = None) -> Path:
    base = Path(data_dir) if data_dir else Path(os.environ.get("DATA_DIR", "data"))
    return base / "observed"


class FrameStore:
    """Directory-per-source frame store.

    Deliberately dumb: no index, no database.  A directory listing of at most
    a few dozen sidecars is faster than any index we could keep consistent
    across a collector restart, and a frame that vanished from disk cannot
    linger in it.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else observed_root()

    def source_dir(self, source: str) -> Path:
        return self.root / source

    def payload_path(self, source: str, valid_time: datetime) -> Path:
        spec = SOURCE_SPECS[source]
        return self.source_dir(source) / f"{frame_stamp(valid_time)}.{spec.extension}"

    def sidecar_path(self, source: str, valid_time: datetime) -> Path:
        return self.source_dir(source) / f"{frame_stamp(valid_time)}.json"

    def has(self, source: str, valid_time: datetime) -> bool:
        """True only when *both* payload and sidecar are present.

        A payload without its sidecar is a half-written frame — an interrupted
        collector, or a purge that got as far as the metadata.  Treating it as
        absent makes the collector re-fetch it rather than leaving a frame the
        store can never describe.
        """
        return (
            self.payload_path(source, valid_time).exists()
            and self.sidecar_path(source, valid_time).exists()
        )

    def write_payload(self, source: str, valid_time: datetime, payload: bytes) -> Path:
        """Atomically store the frame's bytes and return their path.

        Split from :meth:`write_sidecar` so the collector can describe a frame
        by *reading the file it just wrote* — the attribution and grid
        descriptor live inside the payload, and a ~54 MB CTTH granule should
        not be buffered twice to get at them.
        """
        payload_path = self.payload_path(source, valid_time)
        _atomic_write(payload_path, payload)
        return payload_path

    def write_sidecar(
        self, source: str, valid_time: datetime, meta: dict[str, Any]
    ) -> StoredFrame:
        """Publish a frame by writing its sidecar.

        Always last: :meth:`has` and :meth:`list_frames` key off the sidecar,
        so a crash before this point leaves the frame invisible and re-fetched
        rather than advertised-but-truncated.
        """
        payload_path = self.payload_path(source, valid_time)
        full_meta = dict(meta)
        full_meta.setdefault("source", source)
        full_meta["valid_time"] = valid_time.astimezone(timezone.utc).isoformat()
        full_meta.setdefault("received_at", datetime.now(timezone.utc).isoformat())
        full_meta["file"] = payload_path.name
        try:
            full_meta["bytes"] = payload_path.stat().st_size
        except OSError:
            full_meta["bytes"] = 0
        _atomic_write(
            self.sidecar_path(source, valid_time),
            json.dumps(full_meta, indent=2, default=str).encode("utf-8"),
        )
        return StoredFrame(source, valid_time, payload_path, full_meta)

    def write(
        self,
        source: str,
        valid_time: datetime,
        payload: bytes,
        meta: dict[str, Any],
    ) -> StoredFrame:
        """Store one frame plus its sidecar, payload first."""
        self.write_payload(source, valid_time, payload)
        return self.write_sidecar(source, valid_time, meta)

    def list_frames(self, source: str) -> list[StoredFrame]:
        """Every complete frame for ``source``, newest first."""
        directory = self.source_dir(source)
        if not directory.is_dir():
            return []
        frames: list[StoredFrame] = []
        for sidecar in directory.glob("*.json"):
            try:
                valid_time = parse_frame_stamp(sidecar.stem)
            except ValueError:
                continue
            payload = self.payload_path(source, valid_time)
            if not payload.exists():
                continue
            try:
                meta = json.loads(sidecar.read_text())
            except (OSError, json.JSONDecodeError):
                logger.warning("Unreadable observed sidecar %s", sidecar)
                continue
            frames.append(StoredFrame(source, valid_time, payload, meta))
        frames.sort(key=lambda f: f.valid_time, reverse=True)
        return frames

    def latest(
        self,
        source: str,
        *,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> StoredFrame | None:
        """Newest frame for ``source``, or ``None`` if none is fresh enough.

        ``max_age`` is measured from the frame's own valid time, never from a
        shared clock the payload would then have to pretend the sources agree
        on.
        """
        frames = self.list_frames(source)
        if not frames:
            return None
        newest = frames[0]
        if max_age is not None:
            now = now or datetime.now(timezone.utc)
            if newest.valid_time < now - max_age:
                return None
        return newest

    def purge(
        self,
        source: str,
        *,
        retention: timedelta | None = None,
        now: datetime | None = None,
    ) -> int:
        """Delete frames older than the source's retention.  Returns the count."""
        spec = SOURCE_SPECS[source]
        retention = retention if retention is not None else spec.retention
        now = now or datetime.now(timezone.utc)
        cutoff = now - retention
        removed = 0
        for frame in self.list_frames(source):
            if frame.valid_time >= cutoff:
                continue
            for path in (frame.path, self.sidecar_path(source, frame.valid_time)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Could not purge observed frame %s", path, exc_info=True)
            removed += 1
        # Sweep orphaned payloads whose sidecar was lost: list_frames() skips
        # them, so nothing else would ever reclaim their bytes.
        directory = self.source_dir(source)
        if directory.is_dir():
            for payload in directory.glob(f"*.{spec.extension}"):
                try:
                    valid_time = parse_frame_stamp(payload.stem)
                except ValueError:
                    continue
                if valid_time >= cutoff:
                    continue
                if self.sidecar_path(source, valid_time).exists():
                    continue
                try:
                    payload.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def disk_usage(self) -> dict[str, int]:
        """Bytes on disk per source — for the admin storage readout."""
        usage: dict[str, int] = {}
        for source in ALL_SOURCES:
            directory = self.source_dir(source)
            if not directory.is_dir():
                usage[source] = 0
                continue
            usage[source] = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        return usage


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
