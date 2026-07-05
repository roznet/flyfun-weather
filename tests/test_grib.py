"""Tests for GRIB2 enrichment layer: .idx parser, byte-range planner, cache, icing LWC,
and cloud diagnostics."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from weatherbrief.fetch.grib.gfs_idx import (
    ByteRange,
    CloudDiagByteRange,
    CloudDiagIdxEntry,
    IdxEntry,
    parse_cloud_diag_idx,
    parse_idx,
    plan_byte_ranges,
    plan_cloud_diag_byte_ranges,
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
from weatherbrief.fetch.grib.decode import build_cloud_diagnostics
from weatherbrief.analysis.sounding.icing import (
    _lwc_to_icing_severity,
    assess_icing_zones_ogimet_dd,
)
from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    HourlyForecast,
    IcingRisk,
    IcingType,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
    pressure_pa_to_altitude_ft,
)
from weatherbrief.fetch.grib import _apply_cloud_diagnostics


# --- .idx parser tests ---

SAMPLE_IDX = """\
1:0:d=2023102700:HGT:1000 mb:6 hour fcst:
2:45892:d=2023102700:TMP:1000 mb:6 hour fcst:
3:91784:d=2023102700:CLMR:1000 mb:6 hour fcst:
4:137676:d=2023102700:CLMR:975 mb:6 hour fcst:
5:183568:d=2023102700:CLMR:950 mb:6 hour fcst:
6:229460:d=2023102700:ICMR:1000 mb:6 hour fcst:
7:275352:d=2023102700:ICMR:975 mb:6 hour fcst:
8:321244:d=2023102700:RH:1000 mb:6 hour fcst:
"""


def test_parse_idx_filters_clwmr_icmr():
    """Only CLWMR and ICMR entries at pressure levels are returned."""
    entries = parse_idx(SAMPLE_IDX)
    assert len(entries) == 5  # 3 CLMR + 2 ICMR
    assert all(e.variable in ("CLMR", "ICMR") for e in entries)


def test_parse_idx_entry_fields():
    """Parsed entries have correct field values."""
    entries = parse_idx(SAMPLE_IDX)
    clwmr_1000 = [e for e in entries if e.variable == "CLMR" and e.level_hpa == 1000][0]
    assert clwmr_1000.sequence == 3
    assert clwmr_1000.byte_offset == 91784
    assert clwmr_1000.init_time == "2023102700"
    assert clwmr_1000.forecast_step == "6 hour fcst"


def test_parse_idx_ignores_non_pressure_levels():
    """Entries without pressure levels (like surface) are skipped."""
    idx = "1:0:d=2023102700:CLMR:surface:6 hour fcst:\n"
    entries = parse_idx(idx)
    assert len(entries) == 0


def test_plan_byte_ranges():
    """Byte ranges are correctly computed from .idx offsets."""
    ranges = plan_byte_ranges(SAMPLE_IDX)
    assert len(ranges) == 5

    # First CLMR range starts at 91784, ends at next entry - 1
    clwmr_1000 = [r for r in ranges if r.variable == "CLMR" and r.level_hpa == 1000][0]
    assert clwmr_1000.start == 91784
    assert clwmr_1000.end == 137676 - 1


def test_plan_byte_ranges_with_level_filter():
    """Only requested pressure levels are included."""
    ranges = plan_byte_ranges(SAMPLE_IDX, target_levels=[1000])
    assert len(ranges) == 2  # CLMR@1000 + ICMR@1000
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
    ck = cache_key(6, "CLWMR")
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
    """Cache key is route-independent (no bbox hash)."""
    ck = cache_key(6, "CLWMR")
    assert ck == "f006_CLWMR.grib2"


# --- LWC-based icing severity tests ---


def test_lwc_severity_thresholds():
    """LWC-to-severity mapping follows literature thresholds."""
    assert _lwc_to_icing_severity(0.0) == IcingRisk.NONE
    assert _lwc_to_icing_severity(0.05) == IcingRisk.LIGHT
    assert _lwc_to_icing_severity(0.1) == IcingRisk.MODERATE
    assert _lwc_to_icing_severity(0.3) == IcingRisk.MODERATE
    assert _lwc_to_icing_severity(0.6) == IcingRisk.SEVERE
    assert _lwc_to_icing_severity(1.0) == IcingRisk.SEVERE


def test_ogimet_dd_in_cloud_layer():
    """Ogimet-DD detects icing when level is within a cloud layer."""
    levels = [
        DerivedLevel(
            pressure_hpa=700, altitude_ft=10000,
            temperature_c=-7.0, dewpoint_c=-7.3,
            dewpoint_depression_c=0.3,
        ),
    ]
    zones = assess_icing_zones_ogimet_dd(
        levels,
        [EnhancedCloudLayer(base_ft=9000, top_ft=11000)],
    )
    assert len(zones) == 1
    assert zones[0].risk in (IcingRisk.MODERATE, IcingRisk.SEVERE)
    assert zones[0].icing_type == IcingType.MIXED


def test_ogimet_dd_warm_no_icing():
    """No icing above 0°C even inside cloud layer."""
    levels = [
        DerivedLevel(
            pressure_hpa=850, altitude_ft=5000,
            temperature_c=3.0, dewpoint_c=1.0,
            dewpoint_depression_c=2.0,
        ),
    ]
    zones = assess_icing_zones_ogimet_dd(
        levels, [EnhancedCloudLayer(base_ft=4000, top_ft=6000)],
    )
    assert len(zones) == 0


def test_ogimet_dd_outside_cloud_no_icing():
    """No icing when level is outside cloud layers, even with icing temperatures."""
    levels = [
        DerivedLevel(
            pressure_hpa=700, altitude_ft=10000,
            temperature_c=-7.0, dewpoint_c=-8.0,
            dewpoint_depression_c=1.0,
        ),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [])
    assert len(zones) == 0


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
    """Extended levels list has 28 entries (25 low/mid + 3 upper-atmosphere)."""
    from weatherbrief.fetch.variables import EXTENDED_PRESSURE_LEVELS
    assert len(EXTENDED_PRESSURE_LEVELS) == 28


def test_gfs_endpoint_uses_extended_levels():
    """GFS endpoint is configured with extended pressure levels."""
    from weatherbrief.fetch.variables import EXTENDED_PRESSURE_LEVELS, MODEL_ENDPOINTS
    gfs = MODEL_ENDPOINTS["gfs"]
    assert gfs.pressure_levels == EXTENDED_PRESSURE_LEVELS


def test_icon_endpoint_uses_icon_levels():
    """ICON endpoint uses its own level set (19 levels verified Mar 2026)."""
    from weatherbrief.fetch.variables import ICON_PRESSURE_LEVELS, MODEL_ENDPOINTS
    icon = MODEL_ENDPOINTS["icon"]
    assert icon.pressure_levels == ICON_PRESSURE_LEVELS
    assert len(icon.pressure_levels) == 19


def test_build_hourly_params_uses_endpoint_levels():
    """build_hourly_params uses the endpoint's pressure_levels, not global."""
    from weatherbrief.fetch.variables import MODEL_ENDPOINTS, build_hourly_params

    gfs_params = build_hourly_params(MODEL_ENDPOINTS["gfs"])
    ecmwf_params = build_hourly_params(MODEL_ENDPOINTS["ecmwf"])

    # GFS should have more pressure params (25 vs 8 levels)
    assert gfs_params.count("hPa") > ecmwf_params.count("hPa")

    # GFS should have 875hPa (extended), ECMWF should not
    assert "temperature_875hPa" in gfs_params
    assert "temperature_875hPa" not in ecmwf_params


# --- Cloud diagnostic .idx parser tests ---


CLOUD_DIAG_IDX = """\
1:0:d=2026022112:HGT:1000 mb:6 hour fcst:
630:437912319:d=2026022112:LCDC:low cloud layer:6 hour fcst:
631:438715784:d=2026022112:LCDC:low cloud layer:0-6 hour ave fcst:
632:439598564:d=2026022112:MCDC:middle cloud layer:6 hour fcst:
633:440173084:d=2026022112:MCDC:middle cloud layer:0-6 hour ave fcst:
634:440841444:d=2026022112:HCDC:high cloud layer:6 hour fcst:
636:442369379:d=2026022112:TCDC:entire atmosphere:6 hour fcst:
638:444082677:d=2026022112:HGT:cloud ceiling:6 hour fcst:
639:445193187:d=2026022112:PRES:convective cloud bottom level:6 hour fcst:
640:445715716:d=2026022112:PRES:low cloud bottom level:0-6 hour ave fcst:
641:447111239:d=2026022112:PRES:middle cloud bottom level:0-6 hour ave fcst:
644:450220663:d=2026022112:PRES:low cloud top level:0-6 hour ave fcst:
647:454147299:d=2026022112:TMP:low cloud top level:0-6 hour ave fcst:
650:457095069:d=2026022112:TCDC:convective cloud layer:6 hour fcst:
651:457790771:d=2026022112:TCDC:boundary layer cloud layer:0-6 hour ave fcst:
700:500000000:d=2026022112:TMP:2 m above ground:6 hour fcst:
"""


def test_parse_cloud_diag_idx_returns_correct_entries():
    """Cloud diag parser returns only cloud diagnostic entries."""
    entries = parse_cloud_diag_idx(CLOUD_DIAG_IDX)
    # Should not include HGT:1000 mb or TMP:2 m above ground
    assert all(isinstance(e, CloudDiagIdxEntry) for e in entries)
    vars_found = {(e.variable, e.level_str) for e in entries}
    assert ("LCDC", "low cloud layer") in vars_found
    assert ("HCDC", "high cloud layer") in vars_found
    assert ("HGT", "cloud ceiling") in vars_found
    assert ("PRES", "convective cloud bottom level") in vars_found
    assert ("TMP", "low cloud top level") in vars_found
    # Should NOT include pressure-level HGT or surface TMP
    assert ("HGT", "1000 mb") not in vars_found
    assert ("TMP", "2 m above ground") not in vars_found


def test_parse_cloud_diag_idx_prefers_instant_over_avg():
    """When both instant and averaged entries exist, only instant is kept."""
    entries = parse_cloud_diag_idx(CLOUD_DIAG_IDX)
    # LCDC has both "6 hour fcst" and "0-6 hour ave fcst"
    lcdc_entries = [e for e in entries if e.variable == "LCDC"]
    assert len(lcdc_entries) == 1
    assert "ave" not in lcdc_entries[0].forecast_step


def test_parse_cloud_diag_idx_keeps_avg_when_no_instant():
    """Averaged entries are kept when no instant version exists."""
    entries = parse_cloud_diag_idx(CLOUD_DIAG_IDX)
    # PRES low cloud bottom level only has averaged version
    pres_low_bottom = [e for e in entries
                       if e.variable == "PRES" and e.level_str == "low cloud bottom level"]
    assert len(pres_low_bottom) == 1
    assert "ave" in pres_low_bottom[0].forecast_step


def test_plan_cloud_diag_byte_ranges():
    """Cloud diag byte ranges are correctly computed."""
    ranges = plan_cloud_diag_byte_ranges(CLOUD_DIAG_IDX)
    assert len(ranges) > 0
    assert all(isinstance(r, CloudDiagByteRange) for r in ranges)

    # Check specific range: LCDC instant at offset 437912319
    lcdc = [r for r in ranges if r.variable == "LCDC"][0]
    assert lcdc.start == 437912319
    # Next entry is LCDC ave at 438715784
    assert lcdc.end == 438715784 - 1

    # HGT cloud ceiling
    hgt = [r for r in ranges if r.variable == "HGT" and r.level_str == "cloud ceiling"][0]
    assert hgt.start == 444082677


def test_parse_idx_unchanged_by_cloud_diag():
    """Existing parse_idx() is not affected by cloud diag additions."""
    entries = parse_idx(CLOUD_DIAG_IDX)
    # Only HGT:1000 mb would match but HGT is not in GRIB_VARIABLES
    assert len(entries) == 0


# --- Unit conversion tests ---


def test_pressure_pa_to_altitude_ft_sea_level():
    """Sea level pressure (101325 Pa) returns ~0 ft."""
    alt = pressure_pa_to_altitude_ft(101325.0)
    assert abs(alt) < 50  # Should be very close to 0


def test_pressure_pa_to_altitude_ft_typical_values():
    """Standard atmosphere pressure-altitude checks."""
    # 70000 Pa ≈ 700 hPa ≈ ~9882 ft
    alt_700 = pressure_pa_to_altitude_ft(70000.0)
    assert 9500 < alt_700 < 10500

    # 50000 Pa ≈ 500 hPa ≈ ~18289 ft
    alt_500 = pressure_pa_to_altitude_ft(50000.0)
    assert 17500 < alt_500 < 19000

    # 30000 Pa ≈ 300 hPa ≈ ~30065 ft
    alt_300 = pressure_pa_to_altitude_ft(30000.0)
    assert 29000 < alt_300 < 31000


def test_pressure_pa_to_altitude_ft_zero():
    """Zero pressure returns 0 ft (edge case)."""
    assert pressure_pa_to_altitude_ft(0.0) == 0.0


# --- NWPCloudDiagnostics data model tests ---


def test_nwp_cloud_diagnostics_defaults():
    """NWPCloudDiagnostics with defaults has all None values."""
    diag = NWPCloudDiagnostics()
    assert diag.low.cover_pct is None
    assert diag.low.base_ft is None
    assert diag.mid.top_ft is None
    assert diag.ceiling_ft is None
    assert diag.total_cover_pct is None


def test_nwp_cloud_diagnostics_creation():
    """NWPCloudDiagnostics can be created with values."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=80.0, base_ft=2000, top_ft=5000),
        mid=NWPCloudLayerDiag(cover_pct=40.0, base_ft=10000, top_ft=15000),
        ceiling_ft=2000,
    )
    assert diag.low.cover_pct == 80.0
    assert diag.low.base_ft == 2000
    assert diag.mid.cover_pct == 40.0
    assert diag.ceiling_ft == 2000


def test_nwp_cloud_diagnostics_serialization():
    """NWPCloudDiagnostics serializes to dict and back."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=50.0, base_ft=3000, top_ft=6000, top_temp_c=-5.0),
        total_cover_pct=75.0,
    )
    data = diag.model_dump()
    restored = NWPCloudDiagnostics.model_validate(data)
    assert restored.low.cover_pct == 50.0
    assert restored.low.top_temp_c == -5.0
    assert restored.total_cover_pct == 75.0
    # high should have defaults
    assert restored.high.cover_pct is None


# --- build_cloud_diagnostics tests ---


def test_build_cloud_diagnostics_full():
    """build_cloud_diagnostics converts all raw values correctly."""
    raw = {
        "low_cover_pct": 80.0,
        "low_base_pa": 90000.0,      # ~3200 ft
        "low_top_pa": 70000.0,       # ~9900 ft
        "low_top_temp_k": 268.15,    # -5°C
        "mid_cover_pct": 40.0,
        "mid_base_pa": 50000.0,      # ~18300 ft
        "mid_top_pa": 40000.0,       # ~23600 ft
        "high_cover_pct": 20.0,
        "total_cover_pct": 90.0,
        "ceiling_gpm": 1000.0,       # → 3281 ft
        "convective_cover_pct": 10.0,
        "convective_base_pa": 85000.0,
        "convective_top_pa": 30000.0,
    }
    diag = build_cloud_diagnostics(raw)
    assert diag is not None

    # Cloud cover preserved as-is
    assert diag.low.cover_pct == 80.0
    assert diag.mid.cover_pct == 40.0
    assert diag.total_cover_pct == 90.0

    # Pa → ft conversion
    assert diag.low.base_ft is not None
    assert 2500 < diag.low.base_ft < 4000
    assert diag.low.top_ft is not None
    assert 9000 < diag.low.top_ft < 11000

    # K → °C conversion
    assert diag.low.top_temp_c == -5.0

    # gpm → ft conversion
    assert diag.ceiling_ft is not None
    assert abs(diag.ceiling_ft - 3281) < 5

    # Physical sanity: base < top
    assert diag.low.base_ft < diag.low.top_ft
    assert diag.mid.base_ft < diag.mid.top_ft


def test_build_cloud_diagnostics_empty():
    """build_cloud_diagnostics returns None for empty raw dict."""
    assert build_cloud_diagnostics({}) is None


def test_build_cloud_diagnostics_partial():
    """build_cloud_diagnostics handles partial data gracefully."""
    raw = {"low_cover_pct": 50.0, "ceiling_gpm": 500.0}
    diag = build_cloud_diagnostics(raw)
    assert diag is not None
    assert diag.low.cover_pct == 50.0
    assert diag.low.base_ft is None  # Not provided
    assert diag.ceiling_ft is not None
    assert diag.mid.cover_pct is None


# === ICON-EU enrichment tests ===


# --- ICON-EU URL format tests ---


class TestIconEuFetch:
    """Tests for ICON-EU fetch module."""

    def test_icon_eu_file_url_format(self):
        """URL follows DWD naming convention."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_file_url

        url = icon_eu_file_url("20260221", 0, 6, 50, "qc")
        assert url == (
            "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/qc/"
            "icon-eu_europe_regular-lat-lon_model-level_"
            "2026022100_006_50_QC.grib2.bz2"
        )

    def test_icon_eu_file_url_pressure(self):
        """Pressure variable URL works correctly."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_file_url

        url = icon_eu_file_url("20260221", 12, 0, 74, "p")
        assert "icon-eu_europe_regular-lat-lon_model-level_" in url
        assert "_000_74_P.grib2.bz2" in url
        assert "/12/p/" in url

    def test_icon_eu_file_url_level_padding(self):
        """Level number is zero-padded to 2 digits."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_file_url

        url = icon_eu_file_url("20260221", 0, 0, 5, "qi")
        assert "_05_QI" in url

    # --- Domain check tests ---

    def test_route_in_domain_all_inside(self):
        """All points inside ICON-EU domain returns True."""
        from weatherbrief.fetch.grib.icon_eu_fetch import route_in_icon_eu_domain
        from weatherbrief.models import RoutePoint

        points = [
            RoutePoint(lat=48.0, lon=11.0, distance_from_origin_nm=0),
            RoutePoint(lat=51.5, lon=-0.1, distance_from_origin_nm=100),
            RoutePoint(lat=40.0, lon=2.0, distance_from_origin_nm=200),
        ]
        assert route_in_icon_eu_domain(points) is True

    def test_route_outside_domain_lat(self):
        """Point north of domain returns False."""
        from weatherbrief.fetch.grib.icon_eu_fetch import route_in_icon_eu_domain
        from weatherbrief.models import RoutePoint

        points = [
            RoutePoint(lat=48.0, lon=11.0, distance_from_origin_nm=0),
            RoutePoint(lat=75.0, lon=11.0, distance_from_origin_nm=100),
        ]
        assert route_in_icon_eu_domain(points) is False

    def test_route_outside_domain_lon(self):
        """Point west of domain returns False."""
        from weatherbrief.fetch.grib.icon_eu_fetch import route_in_icon_eu_domain
        from weatherbrief.models import RoutePoint

        points = [
            RoutePoint(lat=48.0, lon=-30.0, distance_from_origin_nm=0),
        ]
        assert route_in_icon_eu_domain(points) is False

    def test_route_outside_domain_usa(self):
        """US route is outside ICON-EU domain."""
        from weatherbrief.fetch.grib.icon_eu_fetch import route_in_icon_eu_domain
        from weatherbrief.models import RoutePoint

        points = [
            RoutePoint(lat=40.7, lon=-74.0, distance_from_origin_nm=0),
            RoutePoint(lat=33.9, lon=-118.4, distance_from_origin_nm=500),
        ]
        assert route_in_icon_eu_domain(points) is False

    # --- Forecast hour bracketing tests ---

    def test_bracket_hourly_region(self):
        """Within 0-78h, 1-hourly spacing."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import bracket_icon_eu_forecast_hours

        target = datetime(2026, 2, 21, 6, 30, tzinfo=timezone.utc)
        f_prev, f_next = bracket_icon_eu_forecast_hours("20260221", 0, target)
        assert f_prev == 6
        assert f_next == 7

    def test_bracket_3hourly_region(self):
        """Beyond 78h, 3-hourly spacing."""
        from datetime import datetime, timedelta, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import bracket_icon_eu_forecast_hours

        # 85h after init
        target = datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc) + timedelta(hours=85)
        f_prev, f_next = bracket_icon_eu_forecast_hours("20260221", 0, target)
        assert f_prev == 84
        assert f_next == 87

    def test_bracket_at_boundary(self):
        """Exactly at 78h returns f78/f79 (still hourly)."""
        from datetime import datetime, timedelta, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import bracket_icon_eu_forecast_hours

        target = datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc) + timedelta(hours=78)
        f_prev, f_next = bracket_icon_eu_forecast_hours("20260221", 0, target)
        assert f_prev == 78
        assert f_next == 79

    def test_bracket_clamp_max(self):
        """Forecast hours clamped to 120."""
        from datetime import datetime, timedelta, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import bracket_icon_eu_forecast_hours

        target = datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc) + timedelta(hours=200)
        f_prev, f_next = bracket_icon_eu_forecast_hours("20260221", 0, target)
        assert f_prev == 120
        assert f_next == 120

    # --- Run discovery tests ---

    def test_find_latest_run_probes_correct_url(self):
        """find_latest_icon_eu_run uses HEAD request on a P file."""
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import find_latest_icon_eu_run

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.head.return_value = mock_resp

        # Fix "now" to a specific time so we can predict which cycle is tried
        fixed_now = datetime(2026, 2, 21, 10, 0, tzinfo=timezone.utc)
        with patch("weatherbrief.fetch.grib.icon_eu_fetch.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.strptime = datetime.strptime
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = find_latest_icon_eu_run(fixed_now, session=mock_session)

        assert result is not None
        # At 10:00 UTC, cycles 06z (4h ago > 3h delay) should be tried first
        assert mock_session.head.called
        head_url = mock_session.head.call_args[0][0]
        assert "icon-eu_europe_regular-lat-lon_model-level_" in head_url
        assert "_74_P.grib2.bz2" in head_url


# --- ICON-EU level interpolation tests ---


class TestIconEuLevels:
    """Tests for model-level to pressure-level interpolation."""

    def test_basic_interpolation(self):
        """Linear-in-log-p interpolation produces correct values."""
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        # Simple case: two model levels straddling 700 hPa
        model_p = [60000.0, 80000.0]  # 600 and 800 hPa in Pa
        model_v = [0.0001, 0.0005]

        result = interpolate_model_to_pressure_levels(model_p, model_v, [700])
        assert 700 in result
        # Value should be between 0.0001 and 0.0005
        assert 0.0001 < result[700] < 0.0005

    def test_exact_level_match(self):
        """When target exactly matches a model level, returns exact value."""
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        model_p = [50000.0, 70000.0, 85000.0]  # 500, 700, 850 hPa
        model_v = [0.0001, 0.0003, 0.0005]

        result = interpolate_model_to_pressure_levels(model_p, model_v, [700])
        assert 700 in result
        assert abs(result[700] - 0.0003) < 1e-10

    def test_out_of_range_excluded(self):
        """Target levels outside model pressure range are excluded."""
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        model_p = [60000.0, 80000.0]  # 600–800 hPa range
        model_v = [0.0001, 0.0005]

        result = interpolate_model_to_pressure_levels(model_p, model_v, [300, 700, 1000])
        # Only 700 is within range
        assert 700 in result
        assert 300 not in result
        assert 1000 not in result

    def test_negative_values_clamped(self):
        """Negative interpolation results clamped to zero."""
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        # Values that could produce negative interpolation
        model_p = [50000.0, 60000.0, 70000.0]
        model_v = [0.0, 0.0, 0.0]

        result = interpolate_model_to_pressure_levels(model_p, model_v, [600])
        assert result.get(600, 0.0) >= 0.0

    def test_insufficient_data(self):
        """Less than 2 valid points returns empty."""
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        result = interpolate_model_to_pressure_levels([50000.0], [0.001], [500])
        assert result == {}

    def test_mismatched_lengths(self):
        """Mismatched input lengths returns empty."""
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        result = interpolate_model_to_pressure_levels(
            [50000.0, 70000.0], [0.001], [500, 700],
        )
        assert result == {}

    def test_uses_extended_pressure_levels_by_default(self):
        """Default target levels are EXTENDED_PRESSURE_LEVELS (28 levels)."""
        from weatherbrief.fetch.grib.icon_eu_levels import TARGET_PRESSURE_LEVELS_HPA, interpolate_model_to_pressure_levels

        # Wide pressure range covering all levels
        model_p = [3000.0, 100000.0]  # 30–1000 hPa in Pa
        model_v = [0.0001, 0.0010]

        result = interpolate_model_to_pressure_levels(model_p, model_v)
        for level in TARGET_PRESSURE_LEVELS_HPA:
            assert level in result, f"Missing level {level}"
        assert len(result) == len(TARGET_PRESSURE_LEVELS_HPA)

    def test_many_model_levels(self):
        """Handles realistic number of model levels (40)."""
        import numpy as np
        from weatherbrief.fetch.grib.icon_eu_levels import interpolate_model_to_pressure_levels

        # Simulate 40 model levels from 300 to 1000 hPa
        n_levels = 40
        model_p = list(np.linspace(30000, 100000, n_levels))
        # QC profile: peak around 700 hPa
        model_v = [max(0, 0.0005 * np.exp(-((p/100 - 700)**2) / (200**2)))
                    for p in model_p]

        result = interpolate_model_to_pressure_levels(model_p, model_v, [700, 500, 850])
        assert len(result) == 3
        # Peak should be at 700 hPa
        assert result[700] >= result[500]
        assert result[700] >= result[850]


# --- ICON-EU variable mapping tests ---


class TestIconEuDecode:
    """Tests for ICON-EU variable mapping in decode module."""

    def test_qc_mapped_to_cloud_liquid_water(self):
        """QC maps to cloud_liquid_water_kg_kg."""
        from weatherbrief.fetch.grib.decode import _VAR_MAP

        assert _VAR_MAP["qc"] == "cloud_liquid_water_kg_kg"

    def test_qi_mapped_to_ice_mixing_ratio(self):
        """QI maps to ice_mixing_ratio_kg_kg."""
        from weatherbrief.fetch.grib.decode import _VAR_MAP

        assert _VAR_MAP["qi"] == "ice_mixing_ratio_kg_kg"

    def test_gfs_vars_still_mapped(self):
        """GFS variable mappings unchanged."""
        from weatherbrief.fetch.grib.decode import _VAR_MAP

        assert _VAR_MAP["clmr"] == "cloud_liquid_water_kg_kg"
        assert _VAR_MAP["clwmr"] == "cloud_liquid_water_kg_kg"
        assert _VAR_MAP["icmr"] == "ice_mixing_ratio_kg_kg"

    def test_icon_sounding_vars_in_chunked_decoder(self):
        """Sounding variables in ICON_EU_VARIABLES have matching decoder mappings."""
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_VARIABLES

        # These are the vars the chunked decoder knows how to process
        decoder_vars = {"qc", "qi", "clc", "p", "t", "qv", "u", "v", "w"}
        for var in ICON_EU_VARIABLES:
            assert var in decoder_vars, f"{var} not handled by chunked decoder"

    def test_sounding_vars_produce_pressure_level_fields(self):
        """Raw sounding fields from ICON decode convert to PressureLevelData fields."""
        from weatherbrief.fetch.grib.decode import _convert_raw_sounding

        # Simulate what ICON decode produces after model→pressure interpolation
        raw = {
            "cloud_liquid_water_kg_kg": 1e-5,
            "ice_mixing_ratio_kg_kg": 2e-6,
            "cloud_area_fraction_pct": 30.0,
            "raw_temperature_k": 270.0,
            "raw_specific_humidity_kg_kg": 0.003,
            "raw_u_wind_m_s": 5.0,
            "raw_v_wind_m_s": -10.0,
            "raw_w_m_s": -0.5,
        }
        result = _convert_raw_sounding(raw, 700)
        assert result["temperature_c"] is not None
        assert result["relative_humidity_pct"] is not None
        assert result["dewpoint_c"] is not None
        assert result["wind_speed_kt"] is not None
        assert result["wind_direction_deg"] is not None
        # W (m/s) should be converted to omega (Pa/s)
        assert "vertical_velocity_pa_s" in result
        # Downward W (-0.5 m/s) → positive omega (subsidence)
        assert result["vertical_velocity_pa_s"] > 0

    def test_w_to_omega_conversion(self):
        """Physical W (m/s) converts to omega (Pa/s) via -ρgw."""
        from weatherbrief.fetch.grib.decode import _convert_raw_sounding

        raw = {
            "raw_temperature_k": 270.0,
            "raw_w_m_s": 1.0,  # 1 m/s upward
        }
        result = _convert_raw_sounding(raw, 500)
        # omega = -ρ·g·w = -(500*100 / (287.05*270)) * 9.80665 * 1.0
        # ρ = 50000 / 77503.5 ≈ 0.6451, omega ≈ -6.33 Pa/s
        assert "vertical_velocity_pa_s" in result
        omega = result["vertical_velocity_pa_s"]
        assert omega < 0, "Upward W should give negative omega"
        assert abs(omega - (-6.33)) < 0.1

    def test_w_conversion_without_temperature(self):
        """W conversion requires temperature — should be skipped if missing."""
        from weatherbrief.fetch.grib.decode import _convert_raw_sounding

        raw = {"raw_w_m_s": 1.0}
        result = _convert_raw_sounding(raw, 500)
        assert "vertical_velocity_pa_s" not in result

    def test_icon_cloud_diag_field_map(self):
        """ICON cloud diagnostic field map has correct entries."""
        from weatherbrief.fetch.grib.decode import _ICON_CLOUD_DIAG_FIELD_MAP

        assert _ICON_CLOUD_DIAG_FIELD_MAP["ceiling"] == "ceiling_m"
        assert _ICON_CLOUD_DIAG_FIELD_MAP["hbas_con"] == "convective_cloud_base_m"
        assert _ICON_CLOUD_DIAG_FIELD_MAP["htop_con"] == "convective_cloud_top_m"


# --- ICON-EU single-level URL format tests ---


class TestIconEuSingleLevel:
    """Tests for ICON-EU single-level URL builder and cloud diagnostic builder."""

    def test_single_level_url_ceiling(self):
        """Single-level URL follows DWD naming convention."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_single_level_url

        url = icon_eu_single_level_url("20260221", 0, 6, "ceiling")
        assert url == (
            "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/ceiling/"
            "icon-eu_europe_regular-lat-lon_single-level_"
            "2026022100_006_CEILING.grib2.bz2"
        )

    def test_single_level_url_hbas_con(self):
        """Convective cloud base URL format."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_single_level_url

        url = icon_eu_single_level_url("20260221", 12, 24, "hbas_con")
        assert "icon-eu_europe_regular-lat-lon_single-level_" in url
        assert "_024_HBAS_CON.grib2.bz2" in url
        assert "/12/hbas_con/" in url

    def test_single_level_url_htop_con(self):
        """Convective cloud top URL format."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_single_level_url

        url = icon_eu_single_level_url("20260221", 6, 0, "htop_con")
        assert "_000_HTOP_CON.grib2.bz2" in url
        assert "/06/htop_con/" in url

    def test_cloud_diag_variables_include_cloud_cover(self):
        """ICON_EU_CLOUD_DIAG_VARIABLES includes CLCL/CLCM/CLCH/CLCT."""
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_CLOUD_DIAG_VARIABLES

        for var in ("clcl", "clcm", "clch", "clct"):
            assert var in ICON_EU_CLOUD_DIAG_VARIABLES, f"{var} missing"

    def test_single_level_url_no_level_number(self):
        """Single-level URLs have no level number (unlike model-level)."""
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_single_level_url, icon_eu_file_url

        sl_url = icon_eu_single_level_url("20260221", 0, 6, "ceiling")
        ml_url = icon_eu_file_url("20260221", 0, 6, 50, "qc")

        # Single-level: ..._006_CEILING.grib2.bz2 (no level between fhour and var)
        assert "single-level" in sl_url
        assert "model-level" in ml_url
        # Model-level has extra level number segment
        assert "_006_50_" in ml_url
        assert "_006_CEILING" in sl_url


# --- build_icon_cloud_diagnostics tests ---


class TestBuildIconCloudDiagnostics:
    """Tests for ICON-EU cloud diagnostic builder (m→ft conversion)."""

    def test_ceiling_m_to_ft(self):
        """Ceiling height correctly converted from meters to feet."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {"ceiling_m": 1000.0}  # 1000m = 3281 ft
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.ceiling_ft is not None
        assert abs(diag.ceiling_ft - 3281) < 2

    def test_convective_base_top_m_to_ft(self):
        """Convective cloud base/top converted from meters to feet."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {
            "convective_cloud_base_m": 500.0,   # ~1640 ft
            "convective_cloud_top_m": 10000.0,  # ~32808 ft
        }
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.convective_base_ft is not None
        assert abs(diag.convective_base_ft - 1640) < 5
        assert diag.convective_top_ft is not None
        assert abs(diag.convective_top_ft - 32808) < 5

    def test_empty_returns_none(self):
        """Empty raw dict returns None."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        assert build_icon_cloud_diagnostics({}) is None

    def test_zero_ceiling_is_fog(self):
        """Zero ceiling_m means fog/cloud at surface — should be valid, not None."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {"ceiling_m": 0.0, "convective_cloud_base_m": -1.0}
        diag = build_icon_cloud_diagnostics(raw)
        # ceiling_m=0 is valid (fog), convective_cloud_base_m < 0 is missing
        assert diag is not None
        assert diag.ceiling_ft == 0.0
        assert diag.convective_base_ft is None

    def test_negative_values_returns_none(self):
        """Negative meter values treated as missing."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {"convective_cloud_base_m": -1.0}
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is None

    def test_partial_data(self):
        """Only ceiling populated, convective fields None."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {"ceiling_m": 300.0}  # ~984 ft
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.ceiling_ft is not None
        assert abs(diag.ceiling_ft - 984) < 2
        assert diag.convective_base_ft is None
        assert diag.convective_top_ft is None
        # Low/mid/high not populated when only ceiling provided
        assert diag.low.cover_pct is None

    def test_ml_cape_cin_surfaced(self):
        """ICON mixed-layer CAPE/CIN (cape_ml/cin_ml) surfaced on the diag (#283)."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {"ml_cape_jkg": 850.0, "ml_cin_jkg": -120.0}
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.ml_cape_jkg == 850.0
        assert diag.ml_cin_jkg == -120.0

    def test_ecmwf_native_stability_surfaced(self):
        """ECMWF a1 native stability indices surfaced on the diag (#283).

        kx/totalx/mlcape100/mlcin100 are instantaneous and surfaced directly
        (cp is accumulated and rate-computed in the merge loop, not here).
        """
        from weatherbrief.fetch.grib.decode import build_ecmwf_cloud_diagnostics

        raw = {
            "k_index_c": 38.0,
            "total_totals_c": 52.0,
            "ml_cape_jkg": 1200.0,
            "ml_cin_jkg": -45.0,
        }
        diag = build_ecmwf_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.k_index == 38.0
        assert diag.total_totals == 52.0
        assert diag.ml_cape_jkg == 1200.0
        assert diag.ml_cin_jkg == -45.0

    def test_ecmwf_k_index_kelvin_normalized(self):
        """ECMWF kx arrives in Kelvin → normalized to °C (#283 review).

        A Kelvin K-index (always >100) is converted; a °C one (never >100) is
        passed through unchanged, so the corroboration threshold compares like
        with like regardless of the source unit.
        """
        from weatherbrief.fetch.grib.decode import build_ecmwf_cloud_diagnostics

        # Kelvin: 35 °C K-index encoded as 35 + 273.15 = 308.15.
        kelvin = build_ecmwf_cloud_diagnostics({"k_index_c": 308.15})
        assert kelvin is not None
        assert abs(kelvin.k_index - 35.0) < 0.01

        # Already °C: passed through unchanged.
        celsius = build_ecmwf_cloud_diagnostics({"k_index_c": 35.0})
        assert celsius is not None
        assert celsius.k_index == 35.0

        # Total Totals is offset-immune — never normalized.
        tt = build_ecmwf_cloud_diagnostics({"total_totals_c": 52.0})
        assert tt is not None
        assert tt.total_totals == 52.0

    def test_cloud_cover_percentages(self):
        """CLCL/CLCM/CLCH/CLCT cloud cover percentages stored correctly."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {
            "low_cover_pct": 80.0,
            "mid_cover_pct": 40.0,
            "high_cover_pct": 10.0,
            "total_cover_pct": 85.0,
        }
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.low.cover_pct == 80.0
        assert diag.mid.cover_pct == 40.0
        assert diag.high.cover_pct == 10.0
        assert diag.total_cover_pct == 85.0
        # No height data provided
        assert diag.ceiling_ft is None
        assert diag.low.base_ft is None

    def test_cloud_cover_with_ceiling(self):
        """Cloud cover percentages combined with ceiling height."""
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        raw = {
            "ceiling_m": 500.0,
            "low_cover_pct": 90.0,
            "mid_cover_pct": 20.0,
            "high_cover_pct": 0.0,
            "total_cover_pct": 92.0,
        }
        diag = build_icon_cloud_diagnostics(raw)
        assert diag is not None
        assert diag.ceiling_ft is not None
        assert abs(diag.ceiling_ft - 1640) < 5
        assert diag.low.cover_pct == 90.0
        assert diag.high.cover_pct == 0.0

    def test_icon_cloud_diag_field_map_has_cover_entries(self):
        """ICON cloud diag field map includes CLCL/CLCM/CLCH/CLCT."""
        from weatherbrief.fetch.grib.decode import _ICON_CLOUD_DIAG_FIELD_MAP

        assert _ICON_CLOUD_DIAG_FIELD_MAP["clcl"] == "low_cover_pct"
        assert _ICON_CLOUD_DIAG_FIELD_MAP["clcm"] == "mid_cover_pct"
        assert _ICON_CLOUD_DIAG_FIELD_MAP["clch"] == "high_cover_pct"
        assert _ICON_CLOUD_DIAG_FIELD_MAP["clct"] == "total_cover_pct"


# --- Cache model parameter tests ---


class TestCacheModelParam:
    """Tests for cache generalization with model parameter."""

    def test_cache_dir_default_gfs(self, tmp_path):
        """Default model is gfs (backward compatible)."""
        run_dir = cache_dir_for_run(tmp_path, "20260221", 0)
        assert run_dir == tmp_path / ".cache" / "grib" / "gfs" / "20260221_00z"

    def test_cache_dir_icon_eu(self, tmp_path):
        """model='icon-eu' produces icon-eu path."""
        run_dir = cache_dir_for_run(tmp_path, "20260221", 6, model="icon-eu")
        assert run_dir == tmp_path / ".cache" / "grib" / "icon-eu" / "20260221_06z"

    def test_cache_roundtrip_icon_eu(self, tmp_path):
        """Cache works for icon-eu model."""
        run_dir = cache_dir_for_run(tmp_path, "20260221", 0, model="icon-eu")
        ck = cache_key(3, "ICON_EU_QC_QI_P")
        data = b"fake icon-eu grib2"

        put_cached(run_dir, ck, data)
        result = get_cached(run_dir, ck)
        assert result == data

    def test_different_models_isolated(self, tmp_path):
        """GFS and ICON-EU caches don't interfere."""
        gfs_dir = cache_dir_for_run(tmp_path, "20260221", 0, model="gfs")
        icon_dir = cache_dir_for_run(tmp_path, "20260221", 0, model="icon-eu")

        put_cached(gfs_dir, "test.grib2", b"gfs data")
        put_cached(icon_dir, "test.grib2", b"icon data")

        assert get_cached(gfs_dir, "test.grib2") == b"gfs data"
        assert get_cached(icon_dir, "test.grib2") == b"icon data"


# --- Cloud cover override tests ---


class TestCloudCoverPreservation:
    """Tests that Open-Meteo cloud_cover_*_pct values are preserved by _apply_cloud_diagnostics."""

    def test_open_meteo_values_preserved(self):
        """Open-Meteo cloud_cover_*_pct are NOT overwritten by GRIB diagnostics."""
        from datetime import datetime, timezone

        hourly = HourlyForecast(
            time=datetime(2026, 2, 22, 12, tzinfo=timezone.utc),
            cloud_cover_low_pct=10.0,   # Open-Meteo value
            cloud_cover_mid_pct=0.0,    # Open-Meteo value
            cloud_cover_high_pct=50.0,  # Open-Meteo value
        )
        diag = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=80.0),
            mid=NWPCloudLayerDiag(cover_pct=96.0),
            high=NWPCloudLayerDiag(cover_pct=5.0),
        )

        _apply_cloud_diagnostics(hourly, diag)

        assert hourly.nwp_cloud_diagnostics is diag
        # Open-Meteo values preserved, NOT replaced by GRIB values
        assert hourly.cloud_cover_low_pct == 10.0
        assert hourly.cloud_cover_mid_pct == 0.0
        assert hourly.cloud_cover_high_pct == 50.0

    def test_diagnostics_attached_without_original_cloud_cover(self):
        """Diagnostics are attached even when hourly has no cloud cover values."""
        from datetime import datetime, timezone

        hourly = HourlyForecast(
            time=datetime(2026, 2, 22, 12, tzinfo=timezone.utc),
        )
        diag = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=50.0),
            mid=NWPCloudLayerDiag(cover_pct=25.0),
            high=NWPCloudLayerDiag(cover_pct=0.0),
        )

        _apply_cloud_diagnostics(hourly, diag)

        assert hourly.nwp_cloud_diagnostics is diag
        # Scalar fields remain None (not set from GRIB)
        assert hourly.cloud_cover_low_pct is None
        assert hourly.cloud_cover_mid_pct is None
        assert hourly.cloud_cover_high_pct is None


# --- Per-hour GRIB enrichment tests ---


class TestForecastHourToUtc:
    """Tests for _forecast_hour_to_utc helper."""

    def test_basic(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib import _forecast_hour_to_utc

        result = _forecast_hour_to_utc("20260227", 0, 9)
        assert result == datetime(2026, 2, 27, 9, 0, tzinfo=timezone.utc)

    def test_crosses_midnight(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib import _forecast_hour_to_utc

        result = _forecast_hour_to_utc("20260227", 18, 12)
        assert result == datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc)

    def test_f000(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib import _forecast_hour_to_utc

        result = _forecast_hour_to_utc("20260227", 12, 0)
        assert result == datetime(2026, 2, 27, 12, 0, tzinfo=timezone.utc)


class TestComputeFlightWindowHours:
    """Tests for compute_flight_window_hours (GFS)."""

    def test_zero_duration(self):
        """Duration=0 produces single forecast hour."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

        dep = datetime(2026, 2, 27, 9, tzinfo=timezone.utc)
        hours = compute_flight_window_hours("20260227", 0, dep, 0.0)
        assert len(hours) == 1

    def test_short_flight(self):
        """2-hour flight produces 3 forecast hours (dep, dep+1, dep+2)."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

        dep = datetime(2026, 2, 27, 9, tzinfo=timezone.utc)
        hours = compute_flight_window_hours("20260227", 0, dep, 2.0)
        # dep at f9, +1h at f10, +2h at f11 → 3 unique hours
        assert len(hours) == 3
        assert hours == [9, 10, 11]

    def test_fractional_duration(self):
        """1.5-hour flight → ceil(1.5)+1 = 3 hours."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

        dep = datetime(2026, 2, 27, 9, tzinfo=timezone.utc)
        hours = compute_flight_window_hours("20260227", 0, dep, 1.5)
        assert len(hours) == 3
        assert hours == [9, 10, 11]

    def test_3hourly_region(self):
        """Flight in >120h region snaps to 3-hourly grid."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

        # Departure 130h after init → 3-hourly region
        dep = datetime(2026, 2, 26, 10, tzinfo=timezone.utc)
        hours = compute_flight_window_hours("20260221", 0, dep, 2.0)
        # All should be multiples of 3 past 120
        for h in hours:
            if h > 120:
                assert (h - 120) % 3 == 0

    def test_sorted_and_deduped(self):
        """Output is sorted and deduplicated."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

        dep = datetime(2026, 2, 27, 9, tzinfo=timezone.utc)
        hours = compute_flight_window_hours("20260227", 0, dep, 4.0)
        assert hours == sorted(set(hours))

    def test_3hourly_floor_included(self):
        """Floor native hour is included for forward-fill in the 3-hourly region.

        Departure at ~f140 should include f138 (floor) so forward-fill can
        propagate diagnostics to the departure hour.
        """
        from datetime import datetime, timedelta, timezone
        from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

        init_dt = datetime(2026, 3, 1, 0, tzinfo=timezone.utc)
        # Departure 140h after init → in the 3-hourly region
        dep = init_dt + timedelta(hours=140)
        hours = compute_flight_window_hours("20260301", 0, dep, 2.0)
        # f138 = 120 + 6*3 = floor of 140 in 3-hourly grid
        assert 138 in hours
        # f141 = 120 + 7*3 = nearest round of 140
        assert 141 in hours


class TestSnapToGfsGridFloor:
    """Tests for _snap_to_gfs_grid_floor."""

    def test_hourly_region(self):
        from weatherbrief.fetch.grib.grib_fetch import _snap_to_gfs_grid_floor

        assert _snap_to_gfs_grid_floor(50.7) == 50
        assert _snap_to_gfs_grid_floor(120.0) == 120

    def test_3hourly_region(self):
        from weatherbrief.fetch.grib.grib_fetch import _snap_to_gfs_grid_floor

        # 140 → 120 + int((140-120)/3)*3 = 120 + 6*3 = 138
        assert _snap_to_gfs_grid_floor(140.0) == 138
        # 141 → same as 140
        assert _snap_to_gfs_grid_floor(141.0) == 141
        # 142.9 → 120 + int(22.9/3)*3 = 120 + 7*3 = 141
        assert _snap_to_gfs_grid_floor(142.9) == 141

    def test_clamp_to_384(self):
        from weatherbrief.fetch.grib.grib_fetch import _snap_to_gfs_grid_floor

        assert _snap_to_gfs_grid_floor(500.0) == 384


class TestComputeIconEuFlightWindowHours:
    """Tests for compute_icon_eu_flight_window_hours."""

    def test_short_flight(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import compute_icon_eu_flight_window_hours

        dep = datetime(2026, 2, 27, 9, tzinfo=timezone.utc)
        hours = compute_icon_eu_flight_window_hours("20260227", 0, dep, 2.0)
        assert hours == [9, 10, 11]

    def test_clamp_to_120(self):
        """Forecast hours clamped to max 120."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import compute_icon_eu_flight_window_hours

        # Very far out → all clamped to 120
        dep = datetime(2026, 2, 27, 9, tzinfo=timezone.utc)
        hours = compute_icon_eu_flight_window_hours("20260220", 0, dep, 2.0)
        assert all(h <= 120 for h in hours)

    def test_3hourly_floor_included(self):
        """Floor native hour is included for forward-fill in the 3-hourly region.

        Departure at ~f80 should include f78 (floor) so forward-fill can
        propagate diagnostics to the departure hour.
        """
        from datetime import datetime, timedelta, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import compute_icon_eu_flight_window_hours

        init_dt = datetime(2026, 3, 1, 0, tzinfo=timezone.utc)
        # Departure 80h after init → just into the 3-hourly region
        dep = init_dt + timedelta(hours=80)
        hours = compute_icon_eu_flight_window_hours("20260301", 0, dep, 2.0)
        # f78 = floor of 80 in the 3-hourly grid
        assert 78 in hours


class TestSnapToIconEuGridFloor:
    """Tests for _snap_to_icon_eu_grid_floor."""

    def test_hourly_region(self):
        from weatherbrief.fetch.grib.icon_eu_fetch import _snap_to_icon_eu_grid_floor

        assert _snap_to_icon_eu_grid_floor(50.7) == 50
        assert _snap_to_icon_eu_grid_floor(78.0) == 78

    def test_3hourly_region(self):
        from weatherbrief.fetch.grib.icon_eu_fetch import _snap_to_icon_eu_grid_floor

        # 80 → 78 + int((80-78)/3)*3 = 78 + 0 = 78
        assert _snap_to_icon_eu_grid_floor(80.0) == 78
        # 81 → 78 + int(3/3)*3 = 78 + 3 = 81
        assert _snap_to_icon_eu_grid_floor(81.0) == 81

    def test_clamp_to_120(self):
        from weatherbrief.fetch.grib.icon_eu_fetch import _snap_to_icon_eu_grid_floor

        assert _snap_to_icon_eu_grid_floor(200.0) == 120


class TestMergeValidHourFilter:
    """Tests for valid_utc filtering in _merge_cloud_water_into_sections."""

    @staticmethod
    def _make_cross_section(hourlies):
        """Helper to build a cross-section with the given hourly entries."""
        from datetime import datetime, timezone
        from weatherbrief.models import (
            PressureLevelData,
            RouteCrossSection,
            RoutePoint,
            WaypointForecast,
        )

        rp = RoutePoint(lat=51.0, lon=-0.5, distance_from_origin_nm=0)
        wf = WaypointForecast(
            waypoint={"icao": "EGTF", "name": "Test", "lat": 51.0, "lon": -0.5},
            model="gfs",
            fetched_at=datetime(2026, 2, 27, 8, tzinfo=timezone.utc),
            hourly=hourlies,
        )
        cs = RouteCrossSection(
            model="gfs",
            route_points=[rp],
            fetched_at=datetime(2026, 2, 27, 8, tzinfo=timezone.utc),
            point_forecasts=[wf],
        )
        return cs, rp

    def test_valid_hour_filters_correctly(self):
        """Only matching hour gets enriched."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib import _merge_cloud_water_into_sections
        from weatherbrief.models import PressureLevelData

        hourlies = []
        for h in (9, 10, 11):
            hourlies.append(HourlyForecast(
                time=datetime(2026, 2, 27, h, tzinfo=timezone.utc),
                pressure_levels=[
                    PressureLevelData(pressure_hpa=700, temperature_c=-7.0),
                ],
            ))

        cs, rp = self._make_cross_section(hourlies)
        decoded = [
            {700: {"cloud_liquid_water_kg_kg": 0.0003, "ice_mixing_ratio_kg_kg": 0.0001}},
        ]

        valid_utc = datetime(2026, 2, 27, 10, tzinfo=timezone.utc)
        count = _merge_cloud_water_into_sections(
            [cs], [], [rp], decoded, "gfs", valid_utc=valid_utc,
        )

        assert count == 1
        assert hourlies[0].pressure_levels[0].cloud_liquid_water_kg_kg is None  # hour 9
        assert hourlies[1].pressure_levels[0].cloud_liquid_water_kg_kg == 0.0003  # hour 10
        assert hourlies[2].pressure_levels[0].cloud_liquid_water_kg_kg is None  # hour 11

    def test_valid_hour_none_enriches_all(self):
        """valid_utc=None enriches all hours (backward compat)."""
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib import _merge_cloud_water_into_sections
        from weatherbrief.models import PressureLevelData

        hourlies = []
        for h in (9, 10, 11):
            hourlies.append(HourlyForecast(
                time=datetime(2026, 2, 27, h, tzinfo=timezone.utc),
                pressure_levels=[
                    PressureLevelData(pressure_hpa=700, temperature_c=-7.0),
                ],
            ))

        cs, rp = self._make_cross_section(hourlies)
        decoded = [
            {700: {"cloud_liquid_water_kg_kg": 0.0003}},
        ]

        count = _merge_cloud_water_into_sections(
            [cs], [], [rp], decoded, "gfs", valid_utc=None,
        )

        assert count == 3
        for h in hourlies:
            assert h.pressure_levels[0].cloud_liquid_water_kg_kg == 0.0003


class TestFracGridIndices:
    """Unit tests for the bilinear-interp helper that maps target lat/lon
    onto fractional grid indices. The descending-coord, exact-boundary, and
    out-of-range paths are otherwise only exercised via real GRIB samples."""

    def test_ascending_uniform(self):
        import numpy as np
        from weatherbrief.fetch.grib.decode import _frac_grid_indices

        coord = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        frac, ok = _frac_grid_indices(coord, [0.0, 0.5, 1.0, 0.625])
        assert ok.tolist() == [True, True, True, True]
        np.testing.assert_allclose(frac, [0.0, 2.0, 4.0, 2.5])

    def test_descending_uniform(self):
        # ECMWF lat is descending (e.g. 71.5 → 60.0 in 0.25° steps).
        import numpy as np
        from weatherbrief.fetch.grib.decode import _frac_grid_indices

        coord = np.array([71.5, 71.25, 71.0, 70.75, 70.5])
        frac, ok = _frac_grid_indices(coord, [71.5, 70.5, 71.0])
        assert ok.tolist() == [True, True, True]
        # 71.5 sits at index 0 of the original (descending) array; 70.5 at 4.
        np.testing.assert_allclose(frac, [0.0, 4.0, 2.0])

    def test_out_of_range_marks_nan(self):
        import numpy as np
        from weatherbrief.fetch.grib.decode import _frac_grid_indices

        coord = np.array([0.0, 1.0, 2.0])
        frac, ok = _frac_grid_indices(coord, [-0.1, 0.0, 2.0, 2.1])
        # endpoints are in-bounds; everything strictly outside is NaN.
        assert ok.tolist() == [False, True, True, False]
        assert np.isnan(frac[0]) and np.isnan(frac[3])
        np.testing.assert_allclose(frac[1:3], [0.0, 2.0])

    def test_non_uniform_spacing(self):
        # np.interp handles uneven coords — verify we don't assume a step.
        import numpy as np
        from weatherbrief.fetch.grib.decode import _frac_grid_indices

        coord = np.array([0.0, 1.0, 4.0, 9.0])  # non-uniform spacing
        frac, ok = _frac_grid_indices(coord, [0.0, 2.5, 9.0])
        assert ok.tolist() == [True, True, True]
        # 2.5 sits halfway between coord[1]=1.0 and coord[2]=4.0 in coord-space,
        # so its fractional index is 1 + 0.5 = 1.5.
        np.testing.assert_allclose(frac, [0.0, 1.5, 3.0])

    def test_degenerate_coord_returns_all_nan(self):
        import numpy as np
        from weatherbrief.fetch.grib.decode import _frac_grid_indices

        coord = np.array([42.0])  # n < 2 — can't interpolate
        frac, ok = _frac_grid_indices(coord, [42.0, 43.0])
        assert ok.tolist() == [False, False]
        assert np.all(np.isnan(frac))


class TestIconEuWindowOutOfRange:
    """Deterministic (no-network) horizon classifier used to distinguish an
    expected 'flight beyond ICON-EU horizon' skip from a genuine failure.
    ICON-EU model-level horizon is 120h (5 days) for main runs."""

    def test_within_horizon_is_not_out_of_range(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_window_out_of_range

        as_of = datetime(2026, 7, 5, 22, 0, tzinfo=timezone.utc)
        # Departs ~2 days out — well inside the 120h horizon.
        target = datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)
        assert icon_eu_window_out_of_range(target, 2.25, as_of) is False

    def test_beyond_horizon_is_out_of_range(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_window_out_of_range

        # Reproduces the reported egtf_egei-2026-07-11 flight: pack built
        # 2026-07-05 22:23Z, departs 2026-07-11 07:00Z (~129h out). The latest
        # publishable run (18z 07-05) reaches only 07-10 18:00Z — short of the
        # flight — so no run covers the window.
        as_of = datetime(2026, 7, 5, 22, 23, tzinfo=timezone.utc)
        target = datetime(2026, 7, 11, 7, 0, tzinfo=timezone.utc)
        assert icon_eu_window_out_of_range(target, 2.25, as_of) is True

    def test_boundary_just_inside_120h(self):
        from datetime import datetime, timezone
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_window_out_of_range

        # 00z run on the reference day, published (>3h old). Its 120h horizon
        # reaches exactly ref_day 00:00 + 120h. A flight ending an hour before
        # that is still covered.
        as_of = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
        target = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)  # 00z+119h
        assert icon_eu_window_out_of_range(target, 0.0, as_of) is False


class TestGribSkipDiagnostics:
    """`_build_fetch_diagnostics` must render an accurate message for each
    ICON GRIB skip reason instead of the generic 'unavailable' warning."""

    def _diags(self, skip_reasons):
        from weatherbrief.tasks.fetch import _build_fetch_diagnostics

        return _build_fetch_diagnostics(
            requested_models=["gfs", "icon"],
            models_fetched=["gfs", "icon"],
            models_skipped_region=[],
            enrich_grib=True,
            grib_enriched=True,
            grib_enrichment_failed=False,
            grib_init_times={"gfs": 1},  # gfs enriched, icon not
            grib_skip_reasons=skip_reasons,
        )

    def test_out_of_range_message(self):
        from weatherbrief.models.diagnostic_codes import FetchCode

        icon = [d for d in self._diags({"icon": "out_of_range"})
                if d.code == FetchCode.GRIB_SKIPPED_OUT_OF_RANGE]
        assert len(icon) == 1
        assert icon[0].level == "info"
        assert "exceeds forecast range" in icon[0].message

    def test_out_of_domain_message(self):
        from weatherbrief.models.diagnostic_codes import FetchCode

        icon = [d for d in self._diags({"icon": "out_of_domain"})
                if d.code == FetchCode.GRIB_SKIPPED_OUT_OF_DOMAIN]
        assert len(icon) == 1
        assert icon[0].level == "info"
        assert "coverage area" in icon[0].message

    def test_unknown_reason_falls_back_to_generic_warning(self):
        from weatherbrief.models.diagnostic_codes import FetchCode

        # No skip reason recorded (genuine failure) → generic warn message.
        diags = self._diags({})
        generic = [d for d in diags if d.code == FetchCode.GRIB_UNAVAILABLE_FOR_MODEL]
        assert len(generic) == 1
        assert generic[0].level == "warn"
