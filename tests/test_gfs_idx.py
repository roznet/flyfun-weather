"""Tests for the parametrized gfs_idx parsers (#457).

The GFS defaults (GRIB_VARIABLES, CLOUD_DIAG pairs, averaged-preference) must
behave exactly as before — those are covered by tests/test_grib.py. Here we
pin the new override parameters and the HRRR sounding/diag planners.
"""

from __future__ import annotations

from weatherbrief.fetch.grib.gfs_idx import (
    parse_cloud_diag_idx,
    parse_idx,
    plan_byte_ranges,
    plan_hrrr_diag_byte_ranges,
    plan_hrrr_sounding_byte_ranges,
)

GFS_STYLE_IDX = """\
1:0:d=2023102700:HGT:1000 mb:6 hour fcst:
2:45892:d=2023102700:TMP:1000 mb:6 hour fcst:
3:91784:d=2023102700:CLMR:1000 mb:6 hour fcst:
4:137676:d=2023102700:ICMR:1000 mb:6 hour fcst:
"""


def test_parse_idx_variables_override():
    entries = parse_idx(GFS_STYLE_IDX, variables={"TMP", "HGT"})
    assert [e.variable for e in entries] == ["HGT", "TMP"]
    assert all(e.level_hpa == 1000 for e in entries)


def test_parse_idx_default_still_clmr_icmr():
    entries = parse_idx(GFS_STYLE_IDX)
    assert [e.variable for e in entries] == ["CLMR", "ICMR"]


def test_plan_byte_ranges_variables_override():
    ranges = plan_byte_ranges(GFS_STYLE_IDX, variables={"TMP"})
    assert len(ranges) == 1
    assert ranges[0].variable == "TMP"
    assert ranges[0].start == 45892
    assert ranges[0].end == 91784 - 1


AVE_IDX = """\
1:0:d=2023102700:LCDC:low cloud layer:6 hour fcst:
2:50000:d=2023102700:LCDC:low cloud layer:0-6 hour ave fcst:
"""


def test_parse_cloud_diag_idx_pairs_override():
    entries = parse_cloud_diag_idx(AVE_IDX, pairs={("LCDC", "low cloud layer")})
    assert len(entries) == 1
    assert entries[0].variable == "LCDC"


def test_parse_cloud_diag_idx_prefer_averaged_override():
    pair = {("LCDC", "low cloud layer")}
    # Default for an unknown pair: instantaneous wins over averaged.
    instant = parse_cloud_diag_idx(AVE_IDX, pairs=pair, prefer_averaged=set())
    assert [e.forecast_step for e in instant] == ["6 hour fcst"]
    # Explicitly requesting the averaged form keeps it instead.
    averaged = parse_cloud_diag_idx(AVE_IDX, pairs=pair, prefer_averaged=pair)
    assert [e.forecast_step for e in averaged] == ["0-6 hour ave fcst"]


def test_parse_cloud_diag_idx_mb_wildcard():
    """The level token "mb" in a pair matches any pressure level (NNN mb)."""
    idx = (
        "1:0:d=2026073112:TMP:1000 mb:12 hour fcst:\n"
        "2:50000:d=2026073112:TMP:925 mb:12 hour fcst:\n"
        "3:100000:d=2026073112:TMP:2 m above ground:12 hour fcst:\n"
    )
    entries = parse_cloud_diag_idx(idx, pairs={("TMP", "mb")})
    assert [e.level_str for e in entries] == ["1000 mb", "925 mb"]


# --- HRRR planners ---

HRRR_SOUNDING_IDX = """\
1:0:d=2026073112:HGT:1000 mb:12 hour fcst:
2:60000:d=2026073112:TMP:1000 mb:12 hour fcst:
3:120000:d=2026073112:TMP:925 mb:12 hour fcst:
4:180000:d=2026073112:RH:925 mb:12 hour fcst:
5:240000:d=2026073112:UGRD:925 mb:12 hour fcst:
6:300000:d=2026073112:VGRD:925 mb:12 hour fcst:
7:360000:d=2026073112:CLMR:925 mb:12 hour fcst:
8:420000:d=2026073112:PRES:surface:12 hour fcst:
9:480000:d=2026073112:TMP:2 m above ground:12 hour fcst:
10:540000:d=2026073112:REFC:entire atmosphere:12 hour fcst:
"""


def test_plan_hrrr_sounding_byte_ranges():
    """Pressure-level vars at any mb level + surface PRES; level strings raw."""
    ranges = plan_hrrr_sounding_byte_ranges(HRRR_SOUNDING_IDX)
    assert len(ranges) == 8
    # Near-surface/near-sfc non-matching lines are excluded.
    assert all(r.variable != "REFC" for r in ranges)
    assert ("TMP", "2 m above ground") not in {(r.variable, r.level_str) for r in ranges}
    # Raw level strings kept (decode parses "925 mb" → hPa downstream).
    assert ("TMP", "925 mb") in {(r.variable, r.level_str) for r in ranges}
    assert ("PRES", "surface") in {(r.variable, r.level_str) for r in ranges}
    # Byte ranges come from neighbouring offsets.
    first = [r for r in ranges if r.variable == "HGT"][0]
    assert (first.start, first.end) == (0, 60000 - 1)
    pres = [r for r in ranges if r.variable == "PRES"][0]
    assert (pres.start, pres.end) == (420000, 480000 - 1)


HRRR_DIAG_IDX = """\
1:0:d=2026073112:LCDC:low cloud layer:12 hour fcst:
2:50000:d=2026073112:MCDC:middle cloud layer:12 hour fcst:
3:100000:d=2026073112:HCDC:high cloud layer:12 hour fcst:
4:150000:d=2026073112:TCDC:entire atmosphere:12 hour fcst:
5:200000:d=2026073112:HGT:cloud ceiling:12 hour fcst:
6:250000:d=2026073112:HGT:cloud base:12 hour fcst:
7:300000:d=2026073112:CAPE:surface:12 hour fcst:
8:350000:d=2026073112:CAPE:180-0 mb above ground:12 hour fcst:
9:400000:d=2026073112:CIN:surface:12 hour fcst:
10:450000:d=2026073112:VIS:surface:12 hour fcst:
11:500000:d=2026073112:GUST:surface:12 hour fcst:
12:550000:d=2026073112:REFC:entire atmosphere:12 hour fcst:
13:600000:d=2026073112:HGT:cloud top:12 hour fcst:
"""


def test_plan_hrrr_diag_byte_ranges():
    """The ~15 MB/fhour diagnostics set; unlisted vars/levels excluded."""
    ranges = plan_hrrr_diag_byte_ranges(HRRR_DIAG_IDX)
    keys = {(r.variable, r.level_str) for r in ranges}
    assert len(ranges) == 11
    assert ("HGT", "cloud base") in keys  # HRRR adds cloud base vs GFS set
    assert ("HGT", "cloud top") not in keys
    assert ("REFC", "entire atmosphere") not in keys
    assert ("CAPE", "180-0 mb above ground") in keys
    # Last wanted message ends just before the next idx offset.
    gust = [r for r in ranges if r.variable == "GUST"][0]
    assert (gust.start, gust.end) == (500000, 550000 - 1)


def test_hrrr_planners_prefer_instantaneous_over_averaged():
    idx = (
        "1:0:d=2026073112:LCDC:low cloud layer:12 hour fcst:\n"
        "2:50000:d=2026073112:LCDC:low cloud layer:0-12 hour ave fcst:\n"
    )
    ranges = plan_hrrr_diag_byte_ranges(idx)
    assert len(ranges) == 1
    assert ranges[0].start == 0
