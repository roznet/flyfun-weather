"""GFS/HRRR .idx file parser and byte-range planner.

NCEP GRIB2 files on NOAA S3 have companion .idx files that list the byte offset
of every GRIB2 message. By parsing these, we can download only the specific
variables/levels we need via HTTP Range requests.

The format is shared across NCEP products (GFS pgrb2, HRRR wrfprs), so the
parse/plan helpers take the wanted variable sets as parameters; the module
defaults keep the original GFS behaviour (issue #457).

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
    # Convective ingredients (#566). GFS was the only model with no CAPE, CIN
    # or convective precipitation from GRIB, so it alone fell back to
    # Open-Meteo's *surface-based* CAPE while ECMWF and ICON supply
    # mixed-layer values natively — a cross-model comparison was partly
    # comparing parcel definitions rather than skill.
    #
    # Level strings verified against a live index (gfs.20260820/06z f006), not
    # assumed: the ICON `crr` episode is the precedent for a plausible-looking
    # key silently matching nothing.
    #
    #   CAPE:180-0 mb above ground:6 hour fcst    <- mixed layer, instantaneous
    #   CIN:180-0 mb above ground:6 hour fcst
    #   CPRAT:surface:6 hour fcst                 <- instantaneous rate
    #   CPRAT:surface:0-6 hour ave fcst           <- averaged twin
    #
    # `parse_cloud_diag_idx` keeps the instantaneous entry for anything not in
    # `_PREFER_AVERAGED_PAIRS`, so CPRAT's duplicate resolves correctly without
    # further work (#441 finding #5). Instantaneous is what we want: unlike
    # ECMWF `cp` and ICON `crr` — both accumulated since init — CPRAT needs no
    # de-accumulation, only kg/m2/s -> mm/h.
    #
    # 180-0 mb is GFS's mixed-layer parcel; ECMWF's `mlcape100` is the lowest
    # 100 hPa and ICON's `cape_ml` its own. All three are mixed-layer, but the
    # depths differ — far closer than surface-vs-most-unstable, yet not
    # identical, which `nwp_cape_type` exists to record.
    "CAPE": {"180-0 mb above ground"},
    "CIN": {"180-0 mb above ground"},
    "CPRAT": {"surface"},
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


def parse_idx(
    text: str,
    variables: set[str] | frozenset[str] | None = None,
) -> list[IdxEntry]:
    """Parse an NCEP .idx file into structured pressure-level entries.

    Only returns entries for ``variables`` (default :data:`GRIB_VARIABLES`,
    the GFS cloud-water pair) at integer-hPa pressure levels. Non-integer
    levels (HRRR's near-surface ``1013.2 mb`` extra) are skipped — the app's
    ``PressureLevelData.pressure_hpa`` is integral and the surface anchor
    comes from the hourly's own surface fields.
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


def parse_cloud_diag_idx(
    text: str,
    pairs: set[tuple[str, str]] | None = None,
    prefer_averaged: set[tuple[str, str]] | None = None,
) -> list[CloudDiagIdxEntry]:
    """Parse an NCEP .idx file for cloud diagnostic entries.

    Returns entries matching ``pairs`` (default the GFS
    :data:`CLOUD_DIAG_VARIABLES` set) at their accepted levels. For each
    (variable, level) that has both an instantaneous ("N hour fcst") and an
    averaged ("0-N hour ave fcst") form, selection is per-field: fields in
    ``prefer_averaged`` (default GFS :data:`_PREFER_AVERAGED_PAIRS`) keep the
    AVERAGED entry to match their averaged-only geometry; all other fields
    keep the instantaneous entry. (#441 finding #5) HRRR publishes only
    instantaneous forms, so the selection logic is inert there.
    """
    wanted_pairs = _CLOUD_DIAG_PAIRS if pairs is None else pairs
    avg_preferred = (
        _PREFER_AVERAGED_PAIRS if prefer_averaged is None else prefer_averaged
    )
    all_entries: list[CloudDiagIdxEntry] = []
    for line in text.strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) < 7:
            continue
        seq_str, offset_str, _d_prefix, var, level_str, fcst_step = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
        )

        if (var, level_str) not in wanted_pairs:
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
        if key in avg_preferred:
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
    variables: set[str] | frozenset[str] | None = None,
    min_level_hpa: int | None = None,
) -> list[ByteRange]:
    """Parse .idx and compute byte ranges for pressure-level downloads.

    Each GRIB2 message starts at the entry's byte_offset and ends at the next
    entry's byte_offset - 1. The last message extends to EOF (end=None).

    Args:
        idx_text: Raw .idx file content.
        target_levels: If set, only include these pressure levels.
        variables: Idx variable names to plan (default: GFS CLMR/ICMR).
        min_level_hpa: If set, drop levels above this altitude (lower hPa).
            Robust to the source adding/removing levels, unlike an explicit
            ``target_levels`` list (HRRR uses this to skip 50–125 hPa).

    Returns:
        List of ByteRange objects for HTTP Range requests.
    """
    all_offsets = _collect_all_offsets(idx_text)

    # Get our target entries
    entries = parse_idx(idx_text, variables=variables)
    if target_levels is not None:
        level_set = set(target_levels)
        entries = [e for e in entries if e.level_hpa in level_set]
    if min_level_hpa is not None:
        entries = [e for e in entries if e.level_hpa >= min_level_hpa]

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
        pairs: (variable, level_str) pairs to plan (default: GFS set).
        prefer_averaged: Pairs whose averaged form wins (default: GFS set).

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
