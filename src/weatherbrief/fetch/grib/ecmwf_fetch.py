"""ECMWF IFS GRIB2 fetch from ECPDS delivery directory.

ECMWF commercial data is delivered to a local directory via ECPDS.
Unlike GFS (S3 byte-range) and ICON-EU (DWD HTTP), files land on disk
directly — no HTTP download step.

File naming convention (from ECMWF):
    xxx_cc_nnn_cl_ssss_t_YYYYMMDDTHHMMSSZ_YYYYMMDDTHHMMSSZ_h[_expver]

    xxx:    Destination name (ECPDS)
    cc:     Feed name (PREd)
    nnn:    Model identifier (e.g. ifs, aifs-ens)
    cl:     Data class (od = operational)
    ssss:   Stream name (e.g. oper, enfo)
    t:      Data type (e.g. fc = forecast)
    1st timestamp:  Base date/time (forecast init), ISO 8601
    2nd timestamp:  Valid date/time, ISO 8601
    h:      Forecast step (hours)
    expver: Experiment version (omitted for operational)
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

# Variables we want from ECMWF IFS for icing enrichment (Phase 1).
# ECMWF shortNames — may differ from what cfgrib reports; we'll
# confirm the actual names during Phase 0 sample validation.
ECMWF_MICROPHYSICS_VARS = {
    "clwc",   # Cloud liquid water content (kg/kg)
    "ciwc",   # Cloud ice water content (kg/kg)
}

# Full sounding variables for Phase 3.
ECMWF_SOUNDING_VARS = {
    "t",      # Temperature (K)
    "q",      # Specific humidity (kg/kg)
    "u",      # U wind component (m/s)
    "v",      # V wind component (m/s)
}

# Publication delay: IFS HRES typically available ~6-8h after init.
ECMWF_PUBLISH_DELAY_HOURS = 8

# IFS HRES cycles.
ECMWF_CYCLES = [0, 12]  # 00z and 12z are the main HRES runs

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
    def is_operational(self) -> bool:
        """True if this is operational data (not an experiment).

        ECMWF delivers operational files with expver "0001" (4-digit) but
        the filename may truncate to "1". Accept both until confirmed
        from actual sample files.
        """
        return self.experiment is None or self.experiment.lstrip("0") in ("", "1")


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
    _KNOWN_SUFFIXES = (".grib2", ".grib", ".idx", ".bz2")
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
        logger.warning("Failed to parse ECMWF filename %s: %s", path.name, e)
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

    # Scan for GRIB files (including subdirectories)
    for pattern in ("**/*.grib2", "**/*.grib"):
        for path in scan_dir.glob(pattern):
            if not path.is_file():
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

    # Deduplicate (glob patterns may overlap)
    seen: set[Path] = set()
    unique: list[ECMWFFileInfo] = []
    for f in files:
        if f.path not in seen:
            seen.add(f.path)
            unique.append(f)

    unique.sort(key=lambda f: (f.base_time, f.step_hours))
    logger.info("Found %d ECMWF GRIB files in %s", len(unique), scan_dir)
    return unique


def find_latest_ecmwf_run(
    data_dir: Path | None = None,
    *,
    model: str | None = None,
) -> datetime | None:
    """Find the most recent ECMWF forecast init time in the delivery directory.

    Returns:
        The latest base_time as an aware UTC datetime, or None if no files found.
    """
    files = scan_ecmwf_files(data_dir, model=model)
    if not files:
        return None
    return max(f.base_time for f in files)


def find_files_for_run(
    base_time: datetime,
    data_dir: Path | None = None,
    *,
    model: str | None = None,
) -> list[ECMWFFileInfo]:
    """Find all files belonging to a specific forecast run.

    Args:
        base_time: The forecast init time to match.
        data_dir: Directory to scan.
        model: Filter by model identifier.

    Returns:
        Files for the given run, sorted by step_hours.
    """
    return scan_ecmwf_files(data_dir, model=model, base_time=base_time)


def find_file_for_step(
    base_time: datetime,
    step_hours: int,
    data_dir: Path | None = None,
    *,
    model: str | None = None,
) -> ECMWFFileInfo | None:
    """Find a specific forecast step file.

    Args:
        base_time: The forecast init time.
        step_hours: The forecast step to find.
        data_dir: Directory to scan.
        model: Filter by model identifier.

    Returns:
        Matching file info, or None.
    """
    for f in find_files_for_run(base_time, data_dir, model=model):
        if f.step_hours == step_hours:
            return f
    return None
