"""ECMWF IFS GRIB2 fetch from ECPDS delivery directory.

ECMWF commercial data is delivered to a local directory via ECPDS.
Unlike GFS (S3 byte-range) and ICON-EU (DWD HTTP), files land on disk
directly — no HTTP download step.

The current subscription delivers **ifs-ens-cf** (IFS Ensemble Control
Forecast) data at 0.25° resolution over Europe, split into two file parts:
  - a1: surface/single-level fields (29 variables)
  - a2: pressure-level fields (10 variables × 25 levels)

Each file may contain multiple geographic sub-grids (e.g. main Europe +
Nordic extension).  The decoder handles these dynamically.

File naming convention (from ECMWF):
    xxx_cc_nnn_cl_ssss_t_YYYYMMDDTHHMMSSZ_YYYYMMDDTHHMMSSZ_h[_expver]

    xxx:    Destination name (ECPDS)
    cc:     Feed name / part (a1 = surface, a2 = pressure levels)
    nnn:    Model identifier (e.g. ifs-ens-cf, aifs-ens)
    cl:     Data class (od = operational)
    ssss:   Stream name (oper = main runs 00/12z, scda = short cutoff 06/18z;
            from IFS Cycle 50r1 / 12-May-2026, scda merges into oper for all
            cycles, so 06/18z forecasts also arrive with stream=oper)
    t:      Data type (e.g. fc = forecast)
    1st timestamp:  Base date/time (forecast init), ISO 8601
    2nd timestamp:  Valid date/time, ISO 8601
    h:      Forecast step (hours)
    expver: Experiment version (omitted for prod operational;
            "X0080" for TPREd / 50r1 Release Candidate phase)

Files may have no extension (ECPDS default) or .grib2/.grib.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Default ECMWF data directory — configurable via ECMWF_GRIB_DIR env var.
# In Docker: mounted as a read-only volume at /data/ecmwf.
# For local dev/test: point at wherever the sample files live.
DEFAULT_ECMWF_GRIB_DIR = "/data/ecmwf"

# Variables we want from ECMWF IFS for icing enrichment.
# Confirmed from sample data (ifs-ens-cf, April 2026).
ECMWF_MICROPHYSICS_VARS = {
    "clwc",   # Cloud liquid water content (kg/kg)
    "ciwc",   # Cloud ice water content (kg/kg)
    "cc",     # Cloud cover fraction per level (0-1, convert to 0-100%)
}

# Full sounding variables available on pressure levels (post-2026-04-22
# amendment: d dropped, gh added at all 25 levels).
ECMWF_SOUNDING_VARS = {
    "t",      # Temperature (K)
    "r",      # Relative humidity (%)
    "u",      # U wind component (m/s)
    "v",      # V wind component (m/s)
    "z",      # Geopotential (m²/s²), delivered at 1 hPa only (catalogue limit)
    "gh",     # Geopotential height (gpm), at all 25 pressure levels
    "w",      # Vertical velocity / omega (Pa/s)
    "cc",     # Cloud cover fraction (0-1)
    "clwc",   # Cloud liquid water content (kg/kg)
    "ciwc",   # Cloud ice water content (kg/kg)
}

# Publication delay: ifs-ens-cf typically available ~6-8h after init.
ECMWF_PUBLISH_DELAY_HOURS = 8

# IFS ensemble control forecast cycles. Per-cycle horizon depends on the
# subscription and is NOT inferred from the stream name: we compute it from
# the max step observed on disk per run (see find_best_ecmwf_run). This
# avoids drift every time ECMWF changes cycle naming (e.g. scda/fc -> oper/fc
# at 06/18z from IFS Cycle 50r1, 12-May-2026) or we amend our order.
ECMWF_CYCLES = [0, 6, 12, 18]

# Known file extensions to strip before parsing the ECMWF filename.
_KNOWN_SUFFIXES = (".grib2", ".grib", ".idx", ".bz2")

# File suffixes to skip entirely during directory scanning.
_SKIP_SUFFIXES = frozenset({".idx", ".txt", ".json", ".log", ".md"})

# Filename regex — handles the ECMWF naming convention.
# Groups: destination, feed, model, class, stream, type,
#         base_time, valid_time, step, optional experiment version.
_FILENAME_RE = re.compile(
    r"^(?P<destination>[^_]+)"
    r"_(?P<feed>[^_]+)"
    r"_(?P<model>[^_]+)"
    r"_(?P<dataclass>[^_]+)"
    r"_(?P<stream>[^_]+)"
    r"_(?P<datatype>[^_]+)"
    r"_(?P<base_time>\d{8}T\d{6}Z)"
    r"_(?P<valid_time>\d{8}T\d{6}Z)"
    r"_(?P<step>[^_]+)"
    r"(?:_(?P<expver>[A-Z]?\d+))?"
    r"$"
)


def ecmwf_grib_dir() -> Path:
    """Return the configured ECMWF GRIB data directory."""
    return Path(os.environ.get("ECMWF_GRIB_DIR", DEFAULT_ECMWF_GRIB_DIR))


@dataclass
class ECMWFFileInfo:
    """Parsed metadata from an ECMWF filename."""

    path: Path
    destination: str        # ECPDS destination name
    feed: str               # Feed name from PREd
    model: str              # Model identifier (e.g. "ifs", "aifs-ens")
    data_class: str         # Data class (e.g. "od" = operational)
    stream: str             # Stream (e.g. "oper", "enfo")
    data_type: str          # Type (e.g. "fc" = forecast)
    base_time: datetime     # Forecast init time (aware UTC)
    valid_time: datetime    # Forecast valid time (aware UTC)
    step_hours: int         # Forecast step in hours
    experiment: str | None  # Experiment version (None for operational)

    @property
    def init_date(self) -> str:
        """YYYYMMDD string for the base time."""
        return self.base_time.strftime("%Y%m%d")

    @property
    def init_hour(self) -> int:
        """Hour of the base time."""
        return self.base_time.hour

    @property
    def init_timestamp(self) -> int:
        """Unix timestamp of the base time."""
        return int(self.base_time.timestamp())

    @property
    def is_pressure_level(self) -> bool:
        """True if this file contains pressure-level data (a2 part)."""
        return self.feed == "a2"

    @property
    def is_surface(self) -> bool:
        """True if this file contains surface/single-level fields (a1 part)."""
        return self.feed == "a1"

    @property
    def is_operational(self) -> bool:
        """True if this is operational data (not an experiment).

        ECMWF delivers operational files with expver "0001" (4-digit) but
        the filename may truncate to "1".  Confirmed from sample data.

        TPREd Release Candidate feeds (IFS Cycle 50r1, 12-May-2026) use
        expver "X0080". Treated as experimental by default so prod stays
        strict; set ECMWF_ACCEPT_RCP_EXPVER=1 in staging to consume them.
        """
        if self.experiment in (None, "1", "0001"):
            return True
        if self.experiment == "X0080" and os.environ.get("ECMWF_ACCEPT_RCP_EXPVER") == "1":
            return True
        return False


def _parse_timestamp(ts: str) -> datetime:
    """Parse ECMWF ISO 8601 timestamp like '20260407T120000Z'."""
    return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _parse_step(step_str: str) -> int:
    """Parse forecast step string to hours.

    The step field may be plain hours (e.g. '24') or include a unit
    suffix (e.g. '24h', '1d'). We normalise to integer hours.
    """
    step_str = step_str.strip().lower()
    if step_str.endswith("d"):
        return int(step_str[:-1]) * 24
    if step_str.endswith("h"):
        return int(step_str[:-1])
    return int(step_str)


def parse_ecmwf_filename(path: Path) -> ECMWFFileInfo | None:
    """Parse an ECMWF filename into structured metadata.

    Returns None if the filename doesn't match the expected convention.
    """
    # Strip all known extensions (.grib2.bz2, .grib2, .grib, .idx, etc.)
    stem = path.name
    changed = True
    while changed:
        changed = False
        for suffix in _KNOWN_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                changed = True

    match = _FILENAME_RE.match(stem)
    if match is None:
        logger.debug("Filename does not match ECMWF convention: %s", path.name)
        return None

    try:
        return ECMWFFileInfo(
            path=path,
            destination=match.group("destination"),
            feed=match.group("feed"),
            model=match.group("model"),
            data_class=match.group("dataclass"),
            stream=match.group("stream"),
            data_type=match.group("datatype"),
            base_time=_parse_timestamp(match.group("base_time")),
            valid_time=_parse_timestamp(match.group("valid_time")),
            step_hours=_parse_step(match.group("step")),
            experiment=match.group("expver"),
        )
    except (ValueError, KeyError) as e:
        logger.debug("Failed to parse ECMWF filename %s: %s", path.name, e)
        return None


def scan_ecmwf_files(
    data_dir: Path | None = None,
    *,
    model: str | None = None,
    base_time: datetime | None = None,
    operational_only: bool = True,
) -> list[ECMWFFileInfo]:
    """Scan the ECPDS delivery directory for ECMWF GRIB files.

    Args:
        data_dir: Directory to scan. Defaults to ECMWF_GRIB_DIR env var.
        model: Filter by model identifier (e.g. "ifs"). None = all models.
        base_time: Filter by forecast init time. None = all times.
        operational_only: If True, skip experimental runs.

    Returns:
        List of parsed file info, sorted by base_time then step_hours.
    """
    scan_dir = data_dir or ecmwf_grib_dir()
    if not scan_dir.exists():
        logger.debug("ECMWF data directory does not exist: %s", scan_dir)
        return []

    files: list[ECMWFFileInfo] = []

    # Scan all files — ECMWF ECPDS files may have no extension.
    # parse_ecmwf_filename() rejects non-matching names.
    # Skip obvious non-GRIB files (.idx, .txt, .json, hidden files).
    for path in scan_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if any(path.name.endswith(s) for s in _SKIP_SUFFIXES):
            continue
        info = parse_ecmwf_filename(path)
        if info is None:
            continue
        if model is not None and info.model != model:
            continue
        if base_time is not None and info.base_time != base_time:
            continue
        if operational_only and not info.is_operational:
            continue
        files.append(info)

    files.sort(key=lambda f: (f.base_time, f.step_hours))
    logger.info("Found %d ECMWF GRIB files in %s", len(files), scan_dir)
    return files


def find_latest_ecmwf_run(
    data_dir: Path | None = None,
    *,
    model: str | None = None,
    operational_only: bool = True,
    require_ready: bool = True,
) -> datetime | None:
    """Find the most recent ECMWF forecast init time in the delivery directory.

    Args:
        require_ready: If True (default), only return runs that have a
            sentinel file written by the ECMWF watcher.  Set to False for
            debugging or when completeness checking is not needed.

    Returns:
        The latest base_time as an aware UTC datetime, or None if no files found.
    """
    files = scan_ecmwf_files(data_dir, model=model, operational_only=operational_only)
    if not files:
        return None

    if require_ready:
        files = filter_ready_runs(files, data_dir or ecmwf_grib_dir())
        if not files:
            return None

    return max(f.base_time for f in files)


def filter_ready_runs(
    files: list[ECMWFFileInfo],
    data_dir: Path,
) -> list[ECMWFFileInfo]:
    """Filter file list to only include runs with a readiness sentinel.

    Imports the watcher lazily to avoid circular dependencies.
    """
    from weatherbrief.fetch.grib.ecmwf_watcher import is_run_ready

    ready_times: dict[datetime, bool] = {}
    result = []
    for f in files:
        bt = f.base_time
        if bt not in ready_times:
            ready_times[bt] = is_run_ready(data_dir, base_time=bt)
        if ready_times[bt]:
            result.append(f)

    if len(result) < len(files):
        skipped = set(f.base_time for f in files) - set(f.base_time for f in result)
        for bt in sorted(skipped):
            logger.debug("ECMWF run %s not ready yet, skipping", bt.isoformat())

    return result


def find_best_ecmwf_run(
    all_files: list[ECMWFFileInfo],
    cover_until: datetime,
    *,
    require_ready: bool = True,
    data_dir: Path | None = None,
) -> list[ECMWFFileInfo]:
    """Select the best ECMWF run whose horizon covers the flight window.

    Tries runs in reverse chronological order.  Prefers the latest run
    whose observed horizon (base_time + max step_hours present on disk)
    reaches ``cover_until``.  Falls back to the latest run if none fully
    covers — the uncovered tail simply misses GRIB enrichment.

    The horizon is read from the files themselves rather than a stream-name
    lookup.  This keeps selection correct across subscription changes (e.g.
    2026-04-22 amendment trimming 00/12z from 192h to 168h) and cycle-name
    changes (IFS Cycle 50r1, 12-May-2026, merges 06/18z scda into oper).

    Args:
        all_files: Pre-scanned file list (all runs, already filtered for
            operational_only / as_of_time by the caller).
        cover_until: The latest valid-time the run must cover.

    Returns:
        Files for the selected run (may be empty if all_files is empty).
    """
    if not all_files:
        return []

    if require_ready:
        ready_dir = data_dir or ecmwf_grib_dir()
        all_files = filter_ready_runs(all_files, ready_dir)
        if not all_files:
            return []

    # Group files by base_time
    runs: dict[datetime, list[ECMWFFileInfo]] = {}
    for f in all_files:
        runs.setdefault(f.base_time, []).append(f)

    # Try runs newest-first
    for base_time in sorted(runs, reverse=True):
        run_files = runs[base_time]
        max_step = max(f.step_hours for f in run_files)
        horizon = base_time + timedelta(hours=max_step)
        if horizon >= cover_until:
            logger.info(
                "ECMWF run selection: %s %s (observed horizon %dh covers flight end)",
                base_time.isoformat(), run_files[0].stream, max_step,
            )
            return run_files
        logger.debug(
            "ECMWF run %s %s: observed horizon %dh doesn't reach flight end, skipping",
            base_time.isoformat(), run_files[0].stream, max_step,
        )

    # No run fully covers — fall back to the run whose horizon reaches
    # *furthest into the future* (minimizing the uncovered tail), with ties
    # broken by recency of the init time.  A 12z run with 168h horizon can
    # reach later in time than an 18z run with 144h, so "latest base_time"
    # is not the right fallback.
    def _horizon(bt: datetime) -> datetime:
        return bt + timedelta(hours=max(f.step_hours for f in runs[bt]))

    best_bt = max(runs, key=lambda bt: (_horizon(bt), bt))
    best_max_step = max(f.step_hours for f in runs[best_bt])
    logger.info(
        "ECMWF: no run covers flight end %s, falling back to %s (observed horizon %dh, reaches %s)",
        cover_until.isoformat(), best_bt.isoformat(), best_max_step,
        _horizon(best_bt).isoformat(),
    )
    return runs[best_bt]


def find_files_for_run(
    base_time: datetime,
    data_dir: Path | None = None,
    *,
    model: str | None = None,
    operational_only: bool = True,
) -> list[ECMWFFileInfo]:
    """Find all files belonging to a specific forecast run.

    Args:
        base_time: The forecast init time to match.
        data_dir: Directory to scan.
        model: Filter by model identifier.
        operational_only: If True, skip experimental runs.

    Returns:
        Files for the given run, sorted by step_hours.
    """
    return scan_ecmwf_files(
        data_dir, model=model, base_time=base_time, operational_only=operational_only,
    )


def find_file_for_step(
    files: list[ECMWFFileInfo],
    step_hours: int,
) -> ECMWFFileInfo | None:
    """Find a specific forecast step from a pre-scanned file list.

    Args:
        files: Pre-scanned file list (from find_files_for_run or scan_ecmwf_files).
        step_hours: The forecast step to find.

    Returns:
        Matching file info, or None.
    """
    for f in files:
        if f.step_hours == step_hours:
            return f
    return None
