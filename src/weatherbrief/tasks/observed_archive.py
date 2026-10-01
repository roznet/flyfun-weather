"""Observed conditions at the verification watchlist, persisted as Parquet (#575).

Purpose
-------
Points the #574 corridor sampler at the ~620 standalone-verification airports
and writes what it saw — radar reflectivity and rain rate, total lightning,
satellite cloud tops — to ``DATA_DIR/archive/observed/``. Observations only:
**no forecast is read and nothing is scored here.** Pairing these rows with
forecasts needs its own observation-time alignment (distinct from the ETA
alignment the briefing uses) and belongs with the phase-2 verdict work.

Why now rather than with phase 2: neither side of the pairing can be
backfilled. OPERA's open cache is 24 hours deep and the local frame store keeps
1–3 h, while migration 092's convective ingredient columns have been recording
forward-only since 2026-08-25. A week without this running is a week of
forecast rows with no observed outcome to ever pair against.

Layout
------
::

    DATA_DIR/archive/observed/
      parts/<source>/YYYY-MM-DD/YYYYMMDDTHHMM.parquet   <- one per frame, transient
      <source>/YYYY-MM-DD.parquet                        <- compacted day
      <source>/YYYY-MM-DD.json                           <- rows, sha256, frames

Every source shares one schema (:data:`COLUMNS`), nullable where a column does
not apply, so ``'archive/observed/*/*.parquet'`` is one DuckDB glob across all
four. Rows are long: one per ``(frame, airport, radius)``.

A frame is archived as soon as it is seen, into its own part file. That makes
"has this frame been done?" a file-existence check and a crash mid-run costs at
most the frame in flight. Parts are compacted into one file per source per day
once the day is final (:data:`DAY_FINALITY` past midnight — longer than any
source's frame retention plus delivery lag, so no frame for that day can still
turn up), keeping the long-run file count at four a day.

Cadence
-------
Every frame the collector holds is sampled, subject to a per-source stride
(:data:`ARCHIVE_STRIDE`). Only DBZH is thinned: it is published every 5 min
but is a *rolling 10-minute maximum*, so the frames on the 10-minute marks
already tile the timeline without overlap. Keeping both halves would double the
largest stream for maxima the other half already contains.

The run rides the standalone METAR ingest loop (every 30 min). CTTH frames are
retained for one hour, so one missed tick is survivable and two in a row lose
that hour of cloud tops — a gap is recorded as missing frames in the day
manifest, never papered over.

Calibration caveats encoded in the schema
-----------------------------------------
- **Coverage is a measurement.** ``total_px``/``valid_px``/``nodata_px``/
  ``undetect_px``/``detected_px`` travel with every row, plus
  ``coverage_fraction`` and the ``insufficient_coverage`` flag against
  ``coverage_floor``. Statistics are kept even below the floor: the floor is an
  open calibration item, and deleting the values would bake today's 0.35 into
  the data permanently. A consumer must filter on ``insufficient_coverage`` (or
  its own floor) before grading — the column is there so it cannot be missed.
- **CTTH sees only the highest layer.** The full ``quality_method`` histogram
  is a map column, and ``qm_multilayer_px`` (code 9) is broken out so the
  multi-layer-suspect case is a plain filter.
- **Lightning detection efficiency degrades away from the sub-satellite
  point.** ``satellite_view_angle_deg`` (great-circle angle from MTG-I1 at 0°)
  is stored on every satellite row, plus ``area_km2`` and ``window_minutes``,
  so flash counts can be normalised across latitude later rather than compared
  raw.

Querying with DuckDB
--------------------
::

    -- radar at one airport, adequately covered only
    SELECT frame_valid_time, radius_nm, max_value
    FROM 'archive/observed/opera_dbzh/*.parquet'
    WHERE icao = 'LFPG' AND NOT insufficient_coverage;

    -- cloud tops where the retrieval did not suspect a second layer
    SELECT icao, frame_valid_time, highest_fl
    FROM 'archive/observed/eumetsat_ctth/*.parquet'
    WHERE radius_nm = 10 AND qm_multilayer_px = 0 AND detected_px > 0;
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import date as date_t, datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.models.observed import (
    MIN_COVERAGE_FRACTION,
    ObservedAnnulus,
    ObservedFlashAnnulus,
    ObservedTopsAnnulus,
)
from weatherbrief.observed.coverage import covers
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
    StoredFrame,
    frame_stamp,
)
from weatherbrief.observed.grid import nm_to_km
from weatherbrief.observed.sampler import (
    DEFAULT_RADII_NM,
    SampleStation,
    sample,
    sample_flashes,
)
from weatherbrief.tasks.verification_tiering import observed_archive_root

logger = logging.getLogger(__name__)

#: Bumped whenever a change to the sampler or to this module would make rows
#: written before and after it disagree for the same frame — new parallax
#: handling, a different disc membership rule, a changed statistic. Every row
#: carries it, so a later score can be split by the code that produced it.
ALGORITHM_VERSION = "observed-corridor/1"

#: Which frames are archived: those whose valid time falls on a multiple of the
#: stride since midnight UTC. See "Cadence" in the module docstring.
ARCHIVE_STRIDE: dict[str, timedelta] = {
    SOURCE_OPERA_DBZH: timedelta(minutes=10),
    SOURCE_OPERA_RATE: timedelta(minutes=15),
    SOURCE_EUMETSAT_LI: timedelta(minutes=10),
    SOURCE_EUMETSAT_CTTH: timedelta(minutes=10),
}

#: Day D is compacted once ``now >= D+1 00:00 + DAY_FINALITY``. Must exceed the
#: longest frame retention (3 h) plus delivery lag, or a frame for D could
#: still be on disk, unarchived, when its day is sealed.
DAY_FINALITY = timedelta(hours=4)

#: Stations per read band. Airports are sorted by latitude and sampled in
#: bands of this many, one window read per band. A single Europe-wide read is
#: the fastest option but a CTTH window spanning 35–72°N is ~1,500 full-width
#: rows × seven float64 variables — several hundred MB transient — whereas a
#: band is a fraction of that, for a handful of extra file opens per frame.
#: Still never per-station file access.
BAND_STATIONS = 100


class ObservedArchiveError(RuntimeError):
    """An archive write could not be verified and was abandoned."""


@dataclass(frozen=True)
class ArchiveStation:
    """A watchlist airport to sample around."""

    icao: str
    lat: float
    lon: float


@dataclass
class ObservedArchiveResult:
    """What one :func:`run_observed_archive` call did, per source."""

    frames: dict[str, int] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    compacted: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

#: Column name -> Arrow type name. One schema for every source; see the module
#: docstring for what each group is for.
COLUMNS: tuple[tuple[str, str], ...] = (
    # provenance
    ("source", "string"),
    ("product_id", "string"),
    ("quantity", "string"),
    ("units", "string"),
    ("frame_valid_time", "timestamp"),
    ("ingested_at", "timestamp"),
    ("retrieval_latency_s", "float64"),
    ("sampled_at", "timestamp"),
    ("algorithm_version", "string"),
    ("window_minutes", "float64"),
    ("attribution_producer", "string"),
    ("attribution_license", "string"),
    ("attribution_url", "string"),
    ("attribution_text", "string"),
    # where
    ("icao", "string"),
    ("lat", "float64"),
    ("lon", "float64"),
    ("satellite_view_angle_deg", "float64"),
    ("radius_nm", "float64"),
    ("area_km2", "float64"),
    # coverage (gridded sources; NULL for lightning, which has no coverage split)
    ("total_px", "int32"),
    ("valid_px", "int32"),
    ("nodata_px", "int32"),
    ("undetect_px", "int32"),
    ("detected_px", "int32"),
    ("coverage_fraction", "float64"),
    ("detected_fraction", "float64"),
    ("insufficient_coverage", "bool"),
    ("coverage_floor", "float64"),
    # value statistics over DETECTED pixels (radar dBZ / mm/h, CTTH metres)
    ("max_value", "float64"),
    ("mean_value", "float64"),
    ("p90_value", "float64"),
    # cloud tops
    ("highest_fl", "float64"),
    ("highest_aviation_fl", "float64"),
    ("coldest_top_k", "float64"),
    ("highest_cloudiness", "float64"),
    ("median_cloudiness", "float64"),
    ("qm_multilayer_px", "int32"),
    ("quality_method", "map_int_int"),
    ("fl_bins", "map_str_int"),
    ("fl_fine", "map_int_int"),
    # lightning
    ("flash_count", "int32"),
    ("flashes_per_1000km2_per_min", "float64"),
    ("nearest_flash_nm", "float64"),
    ("latest_flash_time", "timestamp"),
)

COLUMN_NAMES = tuple(name for name, _ in COLUMNS)

# Multi-layer suspect — the CTTH code the "can I get on top?" caveat hangs on.
_QM_MULTILAYER = "9"


def _require_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ObservedArchiveError(
            "pyarrow is required for the observed archive (pip install 'pyarrow>=15')"
        ) from exc
    return pa, pq


def arrow_schema():
    pa, _ = _require_pyarrow()
    types = {
        "string": pa.string(),
        "timestamp": pa.timestamp("us", tz="UTC"),
        "float64": pa.float64(),
        "int32": pa.int32(),
        "bool": pa.bool_(),
        "map_int_int": pa.map_(pa.int32(), pa.int32()),
        "map_str_int": pa.map_(pa.string(), pa.int32()),
    }
    return pa.schema([pa.field(name, types[kind]) for name, kind in COLUMNS])


# ---------------------------------------------------------------------------
# Paths and gates
# ---------------------------------------------------------------------------


def _day_of(valid_time: datetime) -> str:
    return valid_time.astimezone(timezone.utc).date().isoformat()


def part_path(root: Path, source: str, valid_time: datetime) -> Path:
    return root / "parts" / source / _day_of(valid_time) / f"{frame_stamp(valid_time)}.parquet"


def day_path(root: Path, source: str, day: str) -> Path:
    return root / source / f"{day}.parquet"


def manifest_path(root: Path, source: str, day: str) -> Path:
    return root / source / f"{day}.json"


def on_stride(source: str, valid_time: datetime) -> bool:
    """Whether this frame is one the archive keeps (see :data:`ARCHIVE_STRIDE`)."""
    stride = ARCHIVE_STRIDE.get(source)
    if stride is None:
        return False
    utc = valid_time.astimezone(timezone.utc)
    since_midnight = utc - utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return since_midnight % stride == timedelta(0)


def is_archived(root: Path, source: str, valid_time: datetime) -> bool:
    """A frame is done once its part exists or its day has been compacted.

    The compacted-day check is what stops a frame that is still on disk after
    compaction from being written again as an orphan part.
    """
    if part_path(root, source, valid_time).exists():
        return True
    return day_path(root, source, _day_of(valid_time)).exists()


def pending_frames(
    store: FrameStore, source: str, root: Path
) -> list[StoredFrame]:
    """Frames on disk, on stride and not yet archived — oldest first.

    Oldest first because the oldest are closest to being purged from the
    store: if a run is cut short, it should have spent its time on the frames
    the next run would no longer be able to reach.
    """
    frames = [
        f for f in store.list_frames(source)
        if on_stride(source, f.valid_time) and not is_archived(root, source, f.valid_time)
    ]
    frames.sort(key=lambda f: f.valid_time)
    return frames


# ---------------------------------------------------------------------------
# Sampling a frame into rows
# ---------------------------------------------------------------------------


def _product_id(stored: StoredFrame) -> str:
    """The provider's own identifier for the frame.

    EUMETSAT products carry one; OPERA keys are the object name in the open
    cache, which the collector records as the URL it fetched.
    """
    meta = stored.meta or {}
    if meta.get("product_id"):
        return str(meta["product_id"])
    if meta.get("url"):
        return str(meta["url"]).rsplit("/", 1)[-1]
    return stored.path.name


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _provenance(stored: StoredFrame, frame, sampled_at: datetime) -> dict:
    ingested_at = _parse_time((stored.meta or {}).get("received_at"))
    attribution = frame.attribution
    return {
        "source": stored.source,
        "product_id": _product_id(stored),
        "frame_valid_time": frame.valid_time,
        "ingested_at": ingested_at,
        "retrieval_latency_s": (
            (ingested_at - frame.valid_time).total_seconds() if ingested_at else None
        ),
        "sampled_at": sampled_at,
        "algorithm_version": ALGORITHM_VERSION,
        "window_minutes": frame.window_minutes,
        "attribution_producer": attribution.producer,
        "attribution_license": attribution.license,
        "attribution_url": attribution.url,
        "attribution_text": attribution.text,
    }


def _bands(stations: list[ArchiveStation]) -> list[list[ArchiveStation]]:
    ordered = sorted(stations, key=lambda s: (s.lat, s.lon))
    return [ordered[i:i + BAND_STATIONS] for i in range(0, len(ordered), BAND_STATIONS)]


def _where(station: ArchiveStation, source: str, radius_nm: float) -> dict:
    from weatherbrief.observed.ctth import sub_satellite_angle_deg

    satellite = source in (SOURCE_EUMETSAT_CTTH, SOURCE_EUMETSAT_LI)
    radius_km = nm_to_km(radius_nm)
    return {
        "icao": station.icao,
        "lat": station.lat,
        "lon": station.lon,
        "satellite_view_angle_deg": (
            round(sub_satellite_angle_deg(station.lat, station.lon), 3)
            if satellite
            else None
        ),
        "radius_nm": radius_nm,
        "area_km2": math.pi * radius_km**2,
    }


def _annulus_columns(annulus: ObservedAnnulus) -> dict:
    out = {
        "total_px": annulus.total_px,
        "valid_px": annulus.valid_px,
        "nodata_px": annulus.nodata_px,
        "undetect_px": annulus.undetect_px,
        "detected_px": annulus.detected_px,
        "coverage_fraction": annulus.coverage_fraction,
        "detected_fraction": annulus.detected_fraction,
        "insufficient_coverage": annulus.insufficient_coverage,
        "coverage_floor": MIN_COVERAGE_FRACTION,
        "max_value": annulus.max_value,
        "mean_value": annulus.mean_value,
        "p90_value": annulus.p90_value,
    }
    if isinstance(annulus, ObservedTopsAnnulus):
        out.update(
            highest_fl=annulus.highest_fl,
            highest_aviation_fl=annulus.highest_aviation_fl,
            coldest_top_k=annulus.coldest_top_k,
            highest_cloudiness=annulus.highest_cloudiness,
            median_cloudiness=annulus.median_cloudiness,
            qm_multilayer_px=int(annulus.quality_method.get(_QM_MULTILAYER, 0)),
            quality_method=[(int(k), int(v)) for k, v in annulus.quality_method.items()],
            fl_bins=[(k, int(v)) for k, v in annulus.fl_bins.items()],
            fl_fine=[(int(k), int(v)) for k, v in annulus.fl_fine.items()],
        )
    return out


def _flash_columns(annulus: ObservedFlashAnnulus) -> dict:
    return {
        "flash_count": annulus.flash_count,
        "flashes_per_1000km2_per_min": annulus.flashes_per_1000km2_per_min,
        "nearest_flash_nm": annulus.nearest_flash_nm,
        "latest_flash_time": annulus.latest_flash_time,
    }


def frame_rows(
    stored: StoredFrame,
    stations: list[ArchiveStation],
    *,
    radii_nm: tuple[float, ...] = DEFAULT_RADII_NM,
    sampled_at: datetime | None = None,
) -> list[dict]:
    """Sample one stored frame around every in-domain station.

    Stations outside the source's declared domain produce no rows at all —
    the same rule the briefing applies — so "no flashes" is never written for
    an airport the imager cannot see. Stations inside the domain but without
    radar (per-pixel ``nodata``) *are* written, with their coverage saying so.
    """
    from weatherbrief.observed import lightning
    from weatherbrief.observed.payload import read_grid_frame

    source = stored.source
    sampled_at = sampled_at or datetime.now(timezone.utc)
    in_domain = covers(source, [s.lat for s in stations], [s.lon for s in stations])
    covered = [s for s, ok in zip(stations, in_domain) if ok]
    if not covered:
        return []

    rows: list[dict] = []
    if source == SOURCE_EUMETSAT_LI:
        spec = SOURCE_SPECS[source]
        frame = lightning.read_flashes(
            stored.path, source=source, window_minutes=spec.window_minutes
        )
        common = _provenance(stored, frame, sampled_at) | {
            "quantity": "flash", "units": "count",
        }
        samples = sample_flashes(
            frame, [SampleStation(s.icao, s.lat, s.lon) for s in covered], radii_nm
        )
        for station in covered:
            for annulus in samples[station.icao]:
                rows.append(
                    common
                    | _where(station, source, annulus.radius_nm)
                    | _flash_columns(annulus)
                )
        return rows

    radius_km = nm_to_km(max(radii_nm))
    for band in _bands(covered):
        frame, window = read_grid_frame(
            stored, source, [s.lat for s in band], [s.lon for s in band], radius_km
        )
        common = _provenance(stored, frame, sampled_at) | {
            "quantity": frame.quantity, "units": frame.units,
        }
        samples = sample(
            frame, window, [SampleStation(s.icao, s.lat, s.lon) for s in band], radii_nm
        )
        for station in band:
            for annulus in samples[station.icao]:
                rows.append(
                    common
                    | _where(station, source, annulus.radius_nm)
                    | _annulus_columns(annulus)
                )
        # Release the band's arrays before the next read rather than holding
        # two windows at once.
        del frame
    return rows


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _rows_to_table(rows: list[dict]):
    pa, _ = _require_pyarrow()
    schema = arrow_schema()
    columns = {name: [row.get(name) for row in rows] for name in COLUMN_NAMES}
    return pa.Table.from_pydict(columns, schema=schema)


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_verified(table, path: Path) -> str:
    """Write ``table`` to ``path`` atomically; return the file's sha256.

    Temp file → fsync → re-read the row count from the file's own footer →
    rename → fsync the directory. A file whose footer disagrees with what was
    handed to the writer is deleted, not renamed into place.
    """
    _, pq = _require_pyarrow()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        pq.write_table(table, tmp, compression="zstd")
        _fsync(tmp)
        written = pq.ParquetFile(tmp).metadata.num_rows
        if written != table.num_rows:
            raise ObservedArchiveError(
                f"{path}: wrote {written} rows, expected {table.num_rows}"
            )
        digest = _sha256(tmp)
        os.replace(tmp, path)
        _fsync(path.parent)
        return digest
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_frame(
    stored: StoredFrame,
    stations: list[ArchiveStation],
    *,
    root: Path,
    radii_nm: tuple[float, ...] = DEFAULT_RADII_NM,
    sampled_at: datetime | None = None,
) -> int:
    """Sample one frame and write its part file. Returns the row count.

    A frame with no in-domain station still gets an (empty) part, so it is
    recorded as done rather than re-read every run.
    """
    rows = frame_rows(stored, stations, radii_nm=radii_nm, sampled_at=sampled_at)
    _write_verified(_rows_to_table(rows), part_path(root, stored.source, stored.valid_time))
    return len(rows)


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


def sealed_at(day: date_t) -> datetime:
    """When day ``day`` stops accepting frames (see :data:`DAY_FINALITY`)."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return midnight + timedelta(days=1) + DAY_FINALITY


def _read_manifest(path: Path) -> dict | None:
    """A day manifest, or ``None`` if it is missing or unreadable."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.warning("Observed archive: unreadable manifest %s", path, exc_info=True)
        return None


def _write_manifest(path: Path, manifest: dict) -> None:
    """Atomic, durable manifest write: same discipline as the Parquet files."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2))
        _fsync(tmp)
        os.replace(tmp, path)
        _fsync(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _frame_keys(table) -> set[tuple]:
    """Distinct ``(product_id, frame_valid_time)`` pairs in a table."""
    return set(zip(
        table.column("product_id").to_pylist(),
        table.column("frame_valid_time").to_pylist(),
    ))


def final_days(root: Path, source: str, now: datetime) -> list[str]:
    """Days with parts on disk that can no longer receive a frame."""
    parts_dir = root / "parts" / source
    if not parts_dir.is_dir():
        return []
    out: list[str] = []
    for day_dir in sorted(parts_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day = date_t.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if now >= sealed_at(day):
            out.append(day_dir.name)
    return out


def expected_frame_count(source: str) -> int:
    """Frames a complete day holds for ``source`` at the archive stride."""
    return int(timedelta(days=1) / ARCHIVE_STRIDE[source])


def compact_day(root: Path, source: str, day: str) -> int:
    """Merge a final day's parts into ``<source>/<day>.parquet``.

    An existing day file is merged in rather than overwritten, so a part that
    somehow arrives after compaction is folded into the day instead of being
    lost or clobbering it. Parts are deleted only after the merged file and
    its manifest are written and verified. Returns the day's row count.

    Idempotent across a crash at any point. If a previous compaction died
    after writing the day file but before deleting the parts (a timeout or
    OOM kill of the child), those parts are still on disk and their rows are
    already in the day file. A part whose frame (``product_id`` and
    ``frame_valid_time``) is already present in the day file is therefore
    skipped. Keyed on the data itself rather than on the manifest, because a
    crash between the Parquet rename and the manifest write leaves a manifest
    that does not yet list those frames.
    """
    pa, pq = _require_pyarrow()
    schema = arrow_schema()
    parts_dir = root / "parts" / source / day
    part_files = sorted(parts_dir.glob("*.parquet")) if parts_dir.is_dir() else []

    target = day_path(root, source, day)
    meta_path = manifest_path(root, source, day)
    previous = _read_manifest(meta_path) or {}

    tables = []
    frames = set(previous.get("frames", []))
    already_in_day: set[tuple] = set()
    if target.exists():
        existing = pq.read_table(target, schema=schema)
        tables.append(existing)
        already_in_day = _frame_keys(existing)
    skipped = 0
    for part in part_files:
        frames.add(part.stem)
        table = pq.read_table(part, schema=schema)
        if table.num_rows and _frame_keys(table) <= already_in_day:
            skipped += 1
            continue
        tables.append(table)
    if skipped:
        logger.warning(
            "Observed archive: %s %s: %d leftover part(s) already in the day "
            "file (interrupted compaction), not merged again",
            source, day, skipped,
        )
    if not tables:
        return 0

    merged = pa.concat_tables(tables).sort_by(
        [("frame_valid_time", "ascending"), ("icao", "ascending"), ("radius_nm", "ascending")]
    )
    digest = _write_verified(merged, target)
    missing = max(0, expected_frame_count(source) - len(frames))
    _write_manifest(meta_path, {
        "source": source,
        "day": day,
        "rows": merged.num_rows,
        "sha256": digest,
        "frames": sorted(frames),
        "frames_expected": expected_frame_count(source),
        "frames_missing": missing,
        "algorithm_versions": sorted(
            {v for v in merged.column("algorithm_version").to_pylist() if v}
        ),
        "compacted_at": datetime.now(timezone.utc).isoformat(),
    })
    if missing:
        # A lost frame cannot be recovered once the store has purged it, so
        # say so when it is sealed rather than only in a `verify` printout.
        logger.warning(
            "Observed archive: %s %s sealed with %d of %d frames missing",
            source, day, missing, expected_frame_count(source),
        )

    shutil.rmtree(parts_dir, ignore_errors=True)
    return merged.num_rows


def verify_observed_archive(root: Path | None = None) -> list[dict]:
    """Recheck every compacted day against its manifest (sha256 + row count)."""
    _, pq = _require_pyarrow()
    root = root or observed_archive_root()
    report: list[dict] = []
    for source in ARCHIVE_STRIDE:
        source_dir = root / source
        if not source_dir.is_dir():
            continue
        for meta_path in sorted(source_dir.glob("*.json")):
            day = meta_path.stem
            problem = ""
            manifest = _read_manifest(meta_path)
            target = day_path(root, source, day)
            if manifest is None:
                # One bad manifest must not abort the report for every day.
                manifest = {}
                problem = "manifest unreadable"
            elif not target.exists():
                problem = "parquet file missing"
            elif _sha256(target) != manifest.get("sha256"):
                problem = "sha256 mismatch"
            elif pq.ParquetFile(target).metadata.num_rows != manifest.get("rows"):
                problem = "row count mismatch"
            report.append({
                "source": source,
                "day": day,
                "rows": manifest.get("rows", 0),
                "frames_missing": manifest.get("frames_missing", 0),
                "ok": not problem,
                "problem": problem,
            })
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_observed_archive(
    stations: list[ArchiveStation],
    *,
    store: FrameStore | None = None,
    root: Path | None = None,
    sources: tuple[str, ...] | None = None,
    radii_nm: tuple[float, ...] = DEFAULT_RADII_NM,
    now: datetime | None = None,
) -> ObservedArchiveResult:
    """Archive every pending frame, then compact every final day.

    Pending frames first, compaction second, in the same run: a frame for a
    day that has just become final is written before that day is sealed.
    A frame or a source that fails is logged and skipped — the rest of the
    run carries on, and the frame is retried next run while it is still on
    disk.
    """
    store = store or FrameStore()
    root = root or observed_archive_root()
    now = now or datetime.now(timezone.utc)
    wanted = sources if sources is not None else tuple(ARCHIVE_STRIDE)
    result = ObservedArchiveResult()

    for source in wanted:
        result.frames[source] = 0
        result.rows[source] = 0
        try:
            pending = pending_frames(store, source, root)
        except Exception as exc:
            logger.warning("Observed archive: listing %s failed", source, exc_info=True)
            result.errors.append(f"{source}: {exc}")
            continue
        for stored in pending:
            try:
                rows = archive_frame(
                    stored, stations, root=root, radii_nm=radii_nm, sampled_at=now
                )
            except Exception as exc:
                logger.warning(
                    "Observed archive: %s frame %s failed",
                    source, frame_stamp(stored.valid_time), exc_info=True,
                )
                result.errors.append(f"{source} {frame_stamp(stored.valid_time)}: {exc}")
                continue
            result.frames[source] += 1
            result.rows[source] += rows

    for source in wanted:
        days = []
        for day in final_days(root, source, now):
            try:
                compact_day(root, source, day)
                days.append(day)
            except Exception as exc:
                logger.warning(
                    "Observed archive: compacting %s %s failed", source, day, exc_info=True,
                )
                result.errors.append(f"{source} {day} compaction: {exc}")
        if days:
            result.compacted[source] = days

    return result


def stations_from_watchlist(airports) -> list[ArchiveStation]:
    """Adapt ``load_watchlist_with_coords`` output to archive stations."""
    return [ArchiveStation(a.icao, float(a.lat), float(a.lon)) for a in airports]


__all__ = [
    "ALGORITHM_VERSION",
    "ARCHIVE_STRIDE",
    "ArchiveStation",
    "COLUMNS",
    "DAY_FINALITY",
    "ObservedArchiveResult",
    "archive_frame",
    "compact_day",
    "frame_rows",
    "pending_frames",
    "run_observed_archive",
    "stations_from_watchlist",
    "verify_observed_archive",
]
