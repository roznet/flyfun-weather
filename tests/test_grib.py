"""Tests for GRIB2 enrichment layer: .idx parser, byte-range planner, cache, and icing LWC."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from weatherbrief.fetch.grib.gfs_idx import (
    ByteRange,
    IdxEntry,
    parse_idx,
    plan_byte_ranges,
)
from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,
    get_cached,
    put_cached,
)
from weatherbrief.fetch.grib.grib_fetch import (
    bracket_forecast_hours,
    gfs_grib2_url,
    gfs_idx_url,
)
from weatherbrief.analysis.sounding.icing import (
    _lwc_to_icing_severity,
    assess_icing_zones,
)
from weatherbrief.models import DerivedLevel, EnhancedCloudLayer, IcingRisk, IcingType


# --- .idx parser tests ---

SAMPLE_IDX = """\
1:0:d=2023102700:HGT:1000 mb:6 hour fcst:
2:45892:d=2023102700:TMP:1000 mb:6 hour fcst:
3:91784:d=2023102700:CLWMR:1000 mb:6 hour fcst:
4:137676:d=2023102700:CLWMR:975 mb:6 hour fcst:
5:183568:d=2023102700:CLWMR:950 mb:6 hour fcst:
6:229460:d=2023102700:ICMR:1000 mb:6 hour fcst:
7:275352:d=2023102700:ICMR:975 mb:6 hour fcst:
8:321244:d=2023102700:RH:1000 mb:6 hour fcst:
"""


def test_parse_idx_filters_clwmr_icmr():
    """Only CLWMR and ICMR entries at pressure levels are returned."""
    entries = parse_idx(SAMPLE_IDX)
    assert len(entries) == 5  # 3 CLWMR + 2 ICMR
    assert all(e.variable in ("CLWMR", "ICMR") for e in entries)


def test_parse_idx_entry_fields():
    """Parsed entries have correct field values."""
    entries = parse_idx(SAMPLE_IDX)
    clwmr_1000 = [e for e in entries if e.variable == "CLWMR" and e.level_hpa == 1000][0]
    assert clwmr_1000.sequence == 3
    assert clwmr_1000.byte_offset == 91784
    assert clwmr_1000.init_time == "2023102700"
    assert clwmr_1000.forecast_step == "6 hour fcst"


def test_parse_idx_ignores_non_pressure_levels():
    """Entries without pressure levels (like surface) are skipped."""
    idx = "1:0:d=2023102700:CLWMR:surface:6 hour fcst:\n"
    entries = parse_idx(idx)
    assert len(entries) == 0


def test_plan_byte_ranges():
    """Byte ranges are correctly computed from .idx offsets."""
    ranges = plan_byte_ranges(SAMPLE_IDX)
    assert len(ranges) == 5

    # First CLWMR range starts at 91784, ends at next entry - 1
    clwmr_1000 = [r for r in ranges if r.variable == "CLWMR" and r.level_hpa == 1000][0]
    assert clwmr_1000.start == 91784
    assert clwmr_1000.end == 137676 - 1


def test_plan_byte_ranges_with_level_filter():
    """Only requested pressure levels are included."""
    ranges = plan_byte_ranges(SAMPLE_IDX, target_levels=[1000])
    assert len(ranges) == 2  # CLWMR@1000 + ICMR@1000
    assert all(r.level_hpa == 1000 for r in ranges)


def test_plan_byte_ranges_empty():
    """No variables of interest returns empty list."""
    idx = "1:0:d=2023102700:TMP:1000 mb:6 hour fcst:\n"
    ranges = plan_byte_ranges(idx)
    assert ranges == []


# --- URL builder tests ---


def test_gfs_grib2_url():
    url = gfs_grib2_url("20231027", 0, 6)
    assert url == (
        "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20231027/00/atmos/"
        "gfs.t00z.pgrb2.0p25.f006"
    )


def test_gfs_idx_url():
    url = gfs_idx_url("20231027", 12, 24)
    assert url.endswith("gfs.t12z.pgrb2.0p25.f024.idx")


# --- Bracket forecast hours tests ---


def test_bracket_hourly_region():
    """Within f000–f120, 1-hourly spacing."""
    from datetime import datetime, timezone

    # Init at 00z, target at 06:30z → brackets f006 and f007
    target = datetime(2023, 10, 27, 6, 30, tzinfo=timezone.utc)
    f_prev, f_next = bracket_forecast_hours("20231027", 0, target)
    assert f_prev == 6
    assert f_next == 7


def test_bracket_3hourly_region():
    """Beyond f120, 3-hourly spacing."""
    from datetime import datetime, timezone

    # Init at 00z, target at 130h later (beyond f120)
    target = datetime(2023, 10, 27, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    target = target + timedelta(hours=130)
    f_prev, f_next = bracket_forecast_hours("20231027", 0, target)
    assert f_prev == 129
    assert f_next == 132


# --- Cache tests ---


def test_cache_roundtrip(tmp_path):
    """Data written to cache can be retrieved."""
    run_dir = cache_dir_for_run(tmp_path, "20231027", 0)
    ck = cache_key(6, "CLWMR", (45, 55, -5, 10))
    data = b"fake grib2 data"

    put_cached(run_dir, ck, data)
    result = get_cached(run_dir, ck)
    assert result == data


def test_cache_miss(tmp_path):
    """Cache returns None for non-existent entries."""
    run_dir = cache_dir_for_run(tmp_path, "20231027", 0)
    result = get_cached(run_dir, "nonexistent.grib2")
    assert result is None


def test_cache_dir_structure(tmp_path):
    """Cache directory follows expected naming convention."""
    run_dir = cache_dir_for_run(tmp_path, "20231027", 12)
    assert run_dir == tmp_path / ".cache" / "grib" / "gfs" / "20231027_12z"


def test_cache_key_format():
    """Cache key has expected format."""
    ck = cache_key(6, "CLWMR", (45, 55, -5, 10))
    assert ck.startswith("f006_CLWMR_")
    assert ck.endswith(".grib2")


# --- LWC-based icing severity tests ---


def test_lwc_severity_thresholds():
    """LWC-to-severity mapping follows literature thresholds."""
    assert _lwc_to_icing_severity(0.0) == IcingRisk.NONE
    assert _lwc_to_icing_severity(0.05) == IcingRisk.LIGHT
    assert _lwc_to_icing_severity(0.1) == IcingRisk.MODERATE
    assert _lwc_to_icing_severity(0.3) == IcingRisk.MODERATE
    assert _lwc_to_icing_severity(0.6) == IcingRisk.SEVERE
    assert _lwc_to_icing_severity(1.0) == IcingRisk.SEVERE


def test_lwc_icing_detection():
    """Icing zone detected from direct LWC data, even without cloud proximity."""
    levels = [
        DerivedLevel(
            pressure_hpa=700, altitude_ft=10000,
            temperature_c=-7.0, dewpoint_c=-15.0,  # dry! no cloud nearby
            dewpoint_depression_c=8.0,
            cloud_liquid_water_g_m3=0.3,  # but LWC says there's liquid water
        ),
    ]
    # No cloud layers — Ogimet would skip this level
    zones = assess_icing_zones(levels, [])
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.MODERATE
    assert zones[0].icing_type == IcingType.MIXED


def test_lwc_icing_warm_temperature():
    """LWC with warm temperature (>0°C) does not produce icing."""
    levels = [
        DerivedLevel(
            pressure_hpa=850, altitude_ft=5000,
            temperature_c=3.0, dewpoint_c=1.0,
            dewpoint_depression_c=2.0,
            cloud_liquid_water_g_m3=0.5,  # liquid water but warm
        ),
    ]
    zones = assess_icing_zones(levels, [])
    assert len(zones) == 0


def test_lwc_fallback_to_ogimet():
    """Without LWC data, falls back to Ogimet index (existing behavior)."""
    levels = [
        DerivedLevel(
            pressure_hpa=700, altitude_ft=10000,
            temperature_c=-7.0, dewpoint_c=-8.0,
            dewpoint_depression_c=1.0,
            # No cloud_liquid_water_g_m3 set
        ),
    ]
    zones = assess_icing_zones(
        levels,
        [EnhancedCloudLayer(base_ft=9000, top_ft=11000)],
    )
    assert len(zones) == 1
    # Should use Ogimet index (same as existing behavior)
    assert zones[0].risk in (IcingRisk.MODERATE, IcingRisk.SEVERE)


# --- Data model tests ---


def test_pressure_level_data_new_fields():
    """PressureLevelData accepts CLWMR/ICMR fields."""
    from weatherbrief.models import PressureLevelData

    pl = PressureLevelData(
        pressure_hpa=700,
        temperature_c=-7.0,
        cloud_liquid_water_kg_kg=0.0003,
        ice_mixing_ratio_kg_kg=0.0001,
    )
    assert pl.cloud_liquid_water_kg_kg == 0.0003
    assert pl.ice_mixing_ratio_kg_kg == 0.0001


def test_derived_level_lwc_field():
    """DerivedLevel accepts cloud_liquid_water_g_m3 field."""
    dl = DerivedLevel(
        pressure_hpa=700,
        cloud_liquid_water_g_m3=0.25,
    )
    assert dl.cloud_liquid_water_g_m3 == 0.25


def test_derived_level_lwc_default_none():
    """cloud_liquid_water_g_m3 defaults to None."""
    dl = DerivedLevel(pressure_hpa=700)
    assert dl.cloud_liquid_water_g_m3 is None


# --- LWC enrichment in sounding analysis ---


def test_enrich_lwc():
    """_enrich_lwc converts CLWMR to g/m³ on derived levels."""
    from weatherbrief.analysis.sounding import _enrich_lwc
    from weatherbrief.models import PressureLevelData

    raw = [
        PressureLevelData(
            pressure_hpa=700,
            temperature_c=-7.0,
            cloud_liquid_water_kg_kg=0.0003,  # 0.3 g/kg
        ),
    ]
    derived = [
        DerivedLevel(
            pressure_hpa=700,
            temperature_c=-7.0,
            altitude_ft=10000,
        ),
    ]

    _enrich_lwc(derived, raw)

    # At 700 hPa, -7°C: ρ ≈ 70000 / (287.05 × 266.15) ≈ 0.916 kg/m³
    # LWC ≈ 0.0003 × 0.916 × 1000 ≈ 0.275 g/m³
    assert derived[0].cloud_liquid_water_g_m3 is not None
    assert 0.20 < derived[0].cloud_liquid_water_g_m3 < 0.35


def test_enrich_lwc_no_clwmr():
    """Without CLWMR, cloud_liquid_water_g_m3 stays None."""
    from weatherbrief.analysis.sounding import _enrich_lwc
    from weatherbrief.models import PressureLevelData

    raw = [PressureLevelData(pressure_hpa=700, temperature_c=-7.0)]
    derived = [DerivedLevel(pressure_hpa=700, temperature_c=-7.0)]

    _enrich_lwc(derived, raw)
    assert derived[0].cloud_liquid_water_g_m3 is None


# --- Extended pressure levels tests ---


def test_extended_pressure_levels_count():
    """Extended levels list has 25 entries."""
    from weatherbrief.fetch.variables import EXTENDED_PRESSURE_LEVELS
    assert len(EXTENDED_PRESSURE_LEVELS) == 25


def test_gfs_endpoint_uses_extended_levels():
    """GFS endpoint is configured with extended pressure levels."""
    from weatherbrief.fetch.variables import EXTENDED_PRESSURE_LEVELS, MODEL_ENDPOINTS
    gfs = MODEL_ENDPOINTS["gfs"]
    assert gfs.pressure_levels == EXTENDED_PRESSURE_LEVELS


def test_icon_endpoint_uses_base_levels():
    """ICON endpoint defaults to base pressure levels."""
    from weatherbrief.fetch.variables import BASE_PRESSURE_LEVELS, MODEL_ENDPOINTS
    icon = MODEL_ENDPOINTS["icon"]
    assert icon.pressure_levels == BASE_PRESSURE_LEVELS


def test_build_hourly_params_uses_endpoint_levels():
    """build_hourly_params uses the endpoint's pressure_levels, not global."""
    from weatherbrief.fetch.variables import MODEL_ENDPOINTS, build_hourly_params

    gfs_params = build_hourly_params(MODEL_ENDPOINTS["gfs"])
    icon_params = build_hourly_params(MODEL_ENDPOINTS["icon"])

    # GFS should have more pressure params (25 vs 8 levels)
    assert gfs_params.count("hPa") > icon_params.count("hPa")

    # GFS should have 975hPa (extended), ICON should not
    assert "temperature_975hPa" in gfs_params
    assert "temperature_975hPa" not in icon_params
