"""GFS .idx file parser and byte-range planner.

GFS GRIB2 files on NOAA S3 have companion .idx files that list the byte offset
of every GRIB2 message. By parsing these, we can download only the specific
variables/levels we need via HTTP Range requests.

.idx format (one line per message):
    SEQ:OFFSET:d=INIT_TIME:VAR:LEVEL:FORECAST_STEP:
Example:
    1:0:d=2023102700:CLWMR:1000 mb:6 hour fcst:
    2:45892:d=2023102700:CLWMR:975 mb:6 hour fcst:
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Variables we want to extract (pressure-level)
# GFS uses "CLMR" (not "CLWMR") for Cloud Liquid Water Mixing Ratio
GRIB_VARIABLES = {"CLMR", "ICMR"}

# Pattern to parse pressure level from .idx level field
_LEVEL_RE = re.compile(r"^(\d+)\s+mb$")

# Cloud diagnostic variables and their accepted level strings.
# These are surface-type scalar variables (one value per grid point).
CLOUD_DIAG_VARIABLES: dict[str, set[str]] = {
    "LCDC": {"low cloud layer"},
    "MCDC": {"middle cloud layer"},
    "HCDC": {"high cloud layer"},
    "TCDC": {"entire atmosphere", "convective cloud layer",
             "boundary layer cloud layer"},
    "PRES": {"low cloud bottom level", "low cloud top level",
             "middle cloud bottom level", "middle cloud top level",
             "high cloud bottom level", "high cloud top level",
             "convective cloud bottom level", "convective cloud top level"},
    "TMP": {"low cloud top level", "middle cloud top level",
            "high cloud top level"},
    "HGT": {"cloud ceiling"},
}

# Build a flat set of (variable, level_str) tuples for fast lookup
_CLOUD_DIAG_PAIRS: set[tuple[str, str]] = set()
for _var, _levels in CLOUD_DIAG_VARIABLES.items():
    for _lev in _levels:
        _CLOUD_DIAG_PAIRS.add((_var, _lev))


@dataclass
class IdxEntry:
    """A parsed entry from a GFS .idx file."""

    sequence: int
    byte_offset: int
    init_time: str  # e.g. "2023102700"
    variable: str  # e.g. "CLWMR"
    level_hpa: int  # pressure level in hPa
    forecast_step: str  # e.g. "6 hour fcst"


@dataclass
class CloudDiagIdxEntry:
    """A parsed entry for a cloud diagnostic variable."""

    sequence: int
    byte_offset: int
    init_time: str
    variable: str  # e.g. "LCDC", "PRES", "TMP"
    level_str: str  # e.g. "low cloud layer", "cloud ceiling"
    forecast_step: str


@dataclass
class ByteRange:
    """A byte range for downloading a specific GRIB2 message."""

    variable: str
    level_hpa: int
    start: int
    end: int | None  # None means to end of file


@dataclass
class CloudDiagByteRange:
    """A byte range for downloading a cloud diagnostic GRIB2 message."""

    variable: str
    level_str: str
    start: int
    end: int | None  # None means to end of file


def _collect_all_offsets(idx_text: str) -> list[int]:
    """Extract and sort all byte offsets from a .idx file.

    Shared by both plan_byte_ranges() and plan_cloud_diag_byte_ranges().
    """
    offsets: list[int] = []
    for line in idx_text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 2:
            try:
                offsets.append(int(parts[1]))
            except ValueError:
                continue
    offsets.sort()
    return offsets


def _find_next_offset(all_offsets: list[int], start: int) -> int | None:
    """Find the first offset strictly greater than start."""
    for offset in all_offsets:
        if offset > start:
            return offset - 1
    return None


def parse_idx(text: str) -> list[IdxEntry]:
    """Parse a GFS .idx file into structured entries.

    Only returns entries for variables in GRIB_VARIABLES at pressure levels.
    """
    entries: list[IdxEntry] = []
    for line in text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) < 7:
            continue
        seq_str, offset_str, _d_prefix, var, level_str, fcst_step = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
        )

        if var not in GRIB_VARIABLES:
            continue

        level_match = _LEVEL_RE.match(level_str)
        if not level_match:
            continue

        try:
            entries.append(IdxEntry(
                sequence=int(seq_str),
                byte_offset=int(offset_str),
                init_time=_d_prefix.replace("d=", ""),
                variable=var,
                level_hpa=int(level_match.group(1)),
                forecast_step=fcst_step,
            ))
        except (ValueError, IndexError):
            logger.debug("Skipping malformed .idx line: %s", line)
            continue

    return entries


def parse_cloud_diag_idx(text: str) -> list[CloudDiagIdxEntry]:
    """Parse a GFS .idx file for cloud diagnostic entries.

    Returns entries matching CLOUD_DIAG_VARIABLES at their accepted levels.
    Prefers instantaneous ("N hour fcst") over averaged ("0-N hour ave fcst")
    when both exist, by returning only instantaneous entries for variables
    where an instantaneous version is available.
    """
    all_entries: list[CloudDiagIdxEntry] = []
    for line in text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) < 7:
            continue
        seq_str, offset_str, _d_prefix, var, level_str, fcst_step = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
        )

        if (var, level_str) not in _CLOUD_DIAG_PAIRS:
            continue

        try:
            all_entries.append(CloudDiagIdxEntry(
                sequence=int(seq_str),
                byte_offset=int(offset_str),
                init_time=_d_prefix.replace("d=", ""),
                variable=var,
                level_str=level_str,
                forecast_step=fcst_step,
            ))
        except (ValueError, IndexError):
            logger.debug("Skipping malformed cloud diag .idx line: %s", line)
            continue

    # Prefer instantaneous over averaged: for each (var, level_str) if we
    # have an instant entry, drop the averaged one.
    instant_keys: set[tuple[str, str]] = set()
    for e in all_entries:
        if "ave" not in e.forecast_step:
            instant_keys.add((e.variable, e.level_str))

    result: list[CloudDiagIdxEntry] = []
    for e in all_entries:
        key = (e.variable, e.level_str)
        if "ave" in e.forecast_step and key in instant_keys:
            continue  # Skip averaged when instant is available
        result.append(e)

    return result


def plan_byte_ranges(
    idx_text: str,
    target_levels: list[int] | None = None,
) -> list[ByteRange]:
    """Parse .idx and compute byte ranges for CLWMR/ICMR downloads.

    Each GRIB2 message starts at the entry's byte_offset and ends at the next
    entry's byte_offset - 1. The last message extends to EOF (end=None).

    Args:
        idx_text: Raw .idx file content.
        target_levels: If set, only include these pressure levels.

    Returns:
        List of ByteRange objects for HTTP Range requests.
    """
    all_offsets = _collect_all_offsets(idx_text)

    # Get our target entries
    entries = parse_idx(idx_text)
    if target_levels is not None:
        level_set = set(target_levels)
        entries = [e for e in entries if e.level_hpa in level_set]

    ranges: list[ByteRange] = []
    for entry in entries:
        end = _find_next_offset(all_offsets, entry.byte_offset)
        ranges.append(ByteRange(
            variable=entry.variable,
            level_hpa=entry.level_hpa,
            start=entry.byte_offset,
            end=end,
        ))

    return ranges


def plan_cloud_diag_byte_ranges(idx_text: str) -> list[CloudDiagByteRange]:
    """Parse .idx and compute byte ranges for cloud diagnostic downloads.

    Args:
        idx_text: Raw .idx file content.

    Returns:
        List of CloudDiagByteRange objects for HTTP Range requests.
    """
    all_offsets = _collect_all_offsets(idx_text)
    entries = parse_cloud_diag_idx(idx_text)

    ranges: list[CloudDiagByteRange] = []
    for entry in entries:
        end = _find_next_offset(all_offsets, entry.byte_offset)
        ranges.append(CloudDiagByteRange(
            variable=entry.variable,
            level_str=entry.level_str,
            start=entry.byte_offset,
            end=end,
        ))

    return ranges
