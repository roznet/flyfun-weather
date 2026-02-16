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

# Variables we want to extract
# GFS uses "CLMR" (not "CLWMR") for Cloud Liquid Water Mixing Ratio
GRIB_VARIABLES = {"CLMR", "ICMR"}

# Pattern to parse pressure level from .idx level field
_LEVEL_RE = re.compile(r"^(\d+)\s+mb$")


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
class ByteRange:
    """A byte range for downloading a specific GRIB2 message."""

    variable: str
    level_hpa: int
    start: int
    end: int | None  # None means to end of file


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
    # Parse full .idx to get all entry offsets (needed for range calculation)
    all_offsets: list[int] = []
    for line in idx_text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 2:
            try:
                all_offsets.append(int(parts[1]))
            except ValueError:
                continue
    all_offsets.sort()

    # Get our target entries
    entries = parse_idx(idx_text)
    if target_levels is not None:
        level_set = set(target_levels)
        entries = [e for e in entries if e.level_hpa in level_set]

    ranges: list[ByteRange] = []
    for entry in entries:
        start = entry.byte_offset
        # Find next offset after this one
        end = None
        for offset in all_offsets:
            if offset > start:
                end = offset - 1
                break

        ranges.append(ByteRange(
            variable=entry.variable,
            level_hpa=entry.level_hpa,
            start=start,
            end=end,
        ))

    return ranges
