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
    ssss:   Stream name (oper = main runs 00/12z, scda = short cutoff 06/18z)
    t:      Data type (e.g. fc = forecast)
    1st timestamp:  Base date/time (forecast init), ISO 8601
    2nd timestamp:  Valid date/time, ISO 8601
    h:      Forecast step (hours)
    expver: Experiment version (omitted for operational)

Files may have no extension (ECPDS default) or .grib2/.grib.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

# Full sounding variables available on pressure levels.
ECMWF_SOUNDING_VARS = {
    "t",      # Temperature (K)
    "r",      # Relative humidity (%)
    "u",      # U wind component (m/s)
    "v",      # V wind component (m/s)
    "z",      # Geopotential (m²/s²)
    "w",      # Vertical velocity / omega (Pa/s)
    "d",      # Divergence (1/s)
    "cc",     # Cloud cover fraction (0-1)
    "clwc",   # Cloud liquid water content (kg/kg)
    "ciwc",   # Cloud ice water content (kg/kg)
}

# Publication delay: ifs-ens-cf typically available ~6-8h after init.
ECMWF_PUBLISH_DELAY_HOURS = 8

# IFS ensemble control forecast cycles.
# oper stream at 00z/12z (0-192h), scda stream at 06z/18z (0-144h).
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
    r"(?:_(?P<expver>\d+))?"
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
        """
        return self.experiment in (None, "1", "0001")


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
        logger.info("ECMWF data directory does not exist: %s", scan_dir)
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
) -> datetime | None:
    """Find the most recent ECMWF forecast init time in the delivery directory.

    Returns:
        The latest base_time as an aware UTC datetime, or None if no files found.
    """
    files = scan_ecmwf_files(data_dir, model=model, operational_only=operational_only)
    if not files:
        return None
    return max(f.base_time for f in files)


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
