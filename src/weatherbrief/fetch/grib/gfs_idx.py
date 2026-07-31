"""GFS .idx file parser and byte-range planner.

GFS GRIB2 files on NOAA S3 have companion .idx files that list the byte offset
of every GRIB2 message. By parsing these, we can download only the specific
variables/levels we need via HTTP Range requests.

.idx format (one line per message):
    SEQ:OFFSET:d=INIT_TIME:VAR:LEVEL:FORECAST_STEP:
Example:
    1:0:d=2023102700:CLWMR:1000 mb:6 hour fcst:
    2:45892:d=2023102700:CLWMR:975 mb:6 hour fcst:

The parsers are parametrized on their variable sets so the HRRR fetcher (#457)
can reuse them: HRRR .idx files share the exact same format but need different
variables, and HRRR sounding levels are matched via the "mb" wildcard instead
of enumerated level strings.
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


def parse_idx(text: str, variables: set[str] | None = None) -> list[IdxEntry]:
    """Parse a GFS .idx file into structured entries.

    Only returns entries for the wanted variables at pressure levels.

    Args:
        text: Raw .idx file content.
        variables: Variable-name override; defaults to GRIB_VARIABLES.
    """
    wanted = GRIB_VARIABLES if variables is None else variables
    entries: list[IdxEntry] = []
    for line in text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) < 7:
            continue
        seq_str, offset_str, _d_prefix, var, level_str, fcst_step = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
        )

        if var not in wanted:
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


# Cover fields whose paired geometry (PRES cloud bottom/top, TMP cloud-top
# temperature) NCEP publishes ONLY as a time-average. The cover MUST use the
# averaged form too, so cover and geometry share the same statistical
# processing and temporal (window-midpoint) alignment. Everything else
# (TCDC entire-atmosphere total, TCDC convective) prefers the instantaneous
# snapshot. Boundary-layer TCDC is averaged-only, so its preference is moot
# here — but it is aligned as averaged downstream. (#441 finding #5)
_PREFER_AVERAGED_PAIRS: set[tuple[str, str]] = {
    ("LCDC", "low cloud layer"),
    ("MCDC", "middle cloud layer"),
    ("HCDC", "high cloud layer"),
}


def _pair_matches(var: str, level_str: str, pairs: set[tuple[str, str]]) -> bool:
    """Match an idx (variable, level) against a pair set.

    The level token ``"mb"`` acts as a wildcard for any pressure level
    ("NNN mb") — HRRR sounding fields exist at 40 pressure levels, and
    enumerating every level string would drown the variable sets in noise.
    """
    if (var, level_str) in pairs:
        return True
    return (var, "mb") in pairs and _LEVEL_RE.match(level_str) is not None


def parse_cloud_diag_idx(
    text: str,
    pairs: set[tuple[str, str]] | None = None,
    prefer_averaged: set[tuple[str, str]] | None = None,
) -> list[CloudDiagIdxEntry]:
    """Parse a GFS .idx file for cloud diagnostic entries.

    Returns entries matching the wanted (variable, level) pairs. For each
    (variable, level) that has both an instantaneous ("N hour fcst") and an
    averaged ("0-N hour ave fcst") form, selection is per-field:
    low/mid/high cover (``_PREFER_AVERAGED_PAIRS``) keep the AVERAGED entry to
    match their averaged-only geometry; all other fields keep the
    instantaneous entry. (#441 finding #5)

    Args:
        text: Raw .idx file content.
        pairs: (variable, level_str) override; defaults to _CLOUD_DIAG_PAIRS.
            The level token "mb" matches any pressure level (see
            _pair_matches).
        prefer_averaged: pairs that keep the averaged form; defaults to
            _PREFER_AVERAGED_PAIRS.
    """
    if pairs is None:
        pairs = _CLOUD_DIAG_PAIRS
    if prefer_averaged is None:
        prefer_averaged = _PREFER_AVERAGED_PAIRS
    all_entries: list[CloudDiagIdxEntry] = []
    for line in text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) < 7:
            continue
        seq_str, offset_str, _d_prefix, var, level_str, fcst_step = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
        )

        if not _pair_matches(var, level_str, pairs):
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

    # Per-field selection between instantaneous and averaged forms.
    avg_keys: set[tuple[str, str]] = set()
    instant_keys: set[tuple[str, str]] = set()
    for e in all_entries:
        key = (e.variable, e.level_str)
        if "ave" in e.forecast_step:
            avg_keys.add(key)
        else:
            instant_keys.add(key)

    result: list[CloudDiagIdxEntry] = []
    for e in all_entries:
        key = (e.variable, e.level_str)
        is_avg = "ave" in e.forecast_step
        if key in prefer_averaged:
            # Keep averaged; drop the instant form when an averaged one exists.
            if not is_avg and key in avg_keys:
                continue
        else:
            # Keep instantaneous; drop averaged when an instant one exists.
            if is_avg and key in instant_keys:
                continue
        result.append(e)

    return result


def plan_byte_ranges(
    idx_text: str,
    target_levels: list[int] | None = None,
    variables: set[str] | None = None,
) -> list[ByteRange]:
    """Parse .idx and compute byte ranges for CLWMR/ICMR downloads.

    Each GRIB2 message starts at the entry's byte_offset and ends at the next
    entry's byte_offset - 1. The last message extends to EOF (end=None).

    Args:
        idx_text: Raw .idx file content.
        target_levels: If set, only include these pressure levels.
        variables: Variable-name override; defaults to GRIB_VARIABLES.

    Returns:
        List of ByteRange objects for HTTP Range requests.
    """
    all_offsets = _collect_all_offsets(idx_text)

    # Get our target entries
    entries = parse_idx(idx_text, variables=variables)
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


def plan_cloud_diag_byte_ranges(
    idx_text: str,
    pairs: set[tuple[str, str]] | None = None,
    prefer_averaged: set[tuple[str, str]] | None = None,
) -> list[CloudDiagByteRange]:
    """Parse .idx and compute byte ranges for cloud diagnostic downloads.

    Args:
        idx_text: Raw .idx file content.
        pairs: (variable, level_str) override; defaults to _CLOUD_DIAG_PAIRS.
        prefer_averaged: pairs that keep the averaged form; defaults to
            _PREFER_AVERAGED_PAIRS.

    Returns:
        List of CloudDiagByteRange objects for HTTP Range requests.
    """
    all_offsets = _collect_all_offsets(idx_text)
    entries = parse_cloud_diag_idx(
        idx_text, pairs=pairs, prefer_averaged=prefer_averaged,
    )

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


# --- HRRR variable sets (#457) ---
#
# HRRR .idx files use the same line format as GFS, so the parametrized parsers
# above handle them unchanged; only the wanted variables differ. Level strings
# are idx-verbatim (checked against a real wrfprs message, 2026-07-31).

# Sounding (pressure-level) set — 40 levels, 25 hPa spacing, plus surface PRES.
HRRR_SOUNDING_VARIABLES: dict[str, set[str]] = {
    "TMP": {"mb"}, "DPT": {"mb"}, "RH": {"mb"},
    "UGRD": {"mb"}, "VGRD": {"mb"}, "VVEL": {"mb"},
    "HGT": {"mb"}, "CLMR": {"mb"}, "CIMIXR": {"mb"},
    "PRES": {"surface"},
}

# Diagnostics set (~15 MB/fhour).
HRRR_DIAG_VARIABLES: dict[str, set[str]] = {
    "LCDC": {"low cloud layer"},
    "MCDC": {"middle cloud layer"},
    "HCDC": {"high cloud layer"},
    "TCDC": {"entire atmosphere"},
    "HGT": {"cloud ceiling", "cloud base"},
    "CAPE": {"surface", "180-0 mb above ground"},
    "CIN": {"surface", "180-0 mb above ground"},
    "VIS": {"surface"},
    "GUST": {"surface"},
}


def _hrrr_pairs(variables: dict[str, set[str]]) -> set[tuple[str, str]]:
    """Flatten a variable→levels dict into (variable, level_str) pairs."""
    return {(var, lev) for var, levels in variables.items() for lev in levels}


def plan_hrrr_sounding_byte_ranges(idx_text: str) -> list[CloudDiagByteRange]:
    """Byte ranges for the HRRR sounding set (pressure levels + surface PRES).

    Uses the cloud-diag-style planner because HRRR pressure levels are
    level-strings like "925 mb" (matched via the "mb" wildcard) — they are
    parsed to hPa ints downstream by message decode, so the raw level strings
    are kept here for decode to group by. Instantaneous fields only: HRRR
    publishes no averaged forms for these, and prefer-instant is the safe
    default if one ever appears.
    """
    return plan_cloud_diag_byte_ranges(
        idx_text, pairs=_hrrr_pairs(HRRR_SOUNDING_VARIABLES),
        prefer_averaged=set(),
    )


def plan_hrrr_diag_byte_ranges(idx_text: str) -> list[CloudDiagByteRange]:
    """Byte ranges for the HRRR diagnostics set (~15 MB/fhour).

    Same cloud-diag-style planning as the sounding set, with HRRR's own
    diag variables (which add "cloud base" HGT and drop GFS's cloud-layer
    PRES/TMP geometry fields).
    """
    return plan_cloud_diag_byte_ranges(
        idx_text, pairs=_hrrr_pairs(HRRR_DIAG_VARIABLES),
        prefer_averaged=set(),
    )


def plan_hrrr_clmr_byte_ranges(idx_text: str) -> list[CloudDiagByteRange]:
    """Byte ranges for HRRR CLMR + CIMIXR only (the commit-1 patch set, #457).

    The full sounding set (~190 MB/fhour) belongs to the Task-5 replacement
    path; the patch enrichment only merges cloud liquid water and ice mixing
    ratio onto the existing Open-Meteo pressure levels, so only those two
    variables are worth the bandwidth.
    """
    return plan_cloud_diag_byte_ranges(
        idx_text,
        pairs=_hrrr_pairs({"CLMR": {"mb"}, "CIMIXR": {"mb"}}),
        prefer_averaged=set(),
    )
