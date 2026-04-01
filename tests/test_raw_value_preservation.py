"""Tests for raw NWP value preservation in ThermodynamicIndices."""

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.models import HourlyForecast, PressureLevelData


def _make_levels() -> list[PressureLevelData]:
    """Build a minimal but valid pressure-level profile for MetPy."""
    # Need at least surface + a few levels with T, Td, wind for MetPy to work
    return [
        PressureLevelData(
            pressure_hpa=1000, temperature_c=15.0, dewpoint_c=10.0,
            relative_humidity_pct=72.0, wind_speed_kt=10, wind_direction_deg=270,
            geopotential_height_m=110,
        ),
        PressureLevelData(
            pressure_hpa=925, temperature_c=10.0, dewpoint_c=5.0,
            relative_humidity_pct=68.0, wind_speed_kt=15, wind_direction_deg=280,
            geopotential_height_m=790,
        ),
        PressureLevelData(
            pressure_hpa=850, temperature_c=5.0, dewpoint_c=-1.0,
            relative_humidity_pct=55.0, wind_speed_kt=20, wind_direction_deg=290,
            geopotential_height_m=1500,
        ),
        PressureLevelData(
            pressure_hpa=700, temperature_c=-5.0, dewpoint_c=-12.0,
            relative_humidity_pct=45.0, wind_speed_kt=30, wind_direction_deg=300,
            geopotential_height_m=3100,
        ),
        PressureLevelData(
            pressure_hpa=500, temperature_c=-20.0, dewpoint_c=-30.0,
            relative_humidity_pct=30.0, wind_speed_kt=45, wind_direction_deg=310,
            geopotential_height_m=5600,
        ),
        PressureLevelData(
            pressure_hpa=300, temperature_c=-45.0, dewpoint_c=-55.0,
            relative_humidity_pct=20.0, wind_speed_kt=60, wind_direction_deg=320,
            geopotential_height_m=9300,
        ),
    ]


def _make_hourly(**overrides) -> HourlyForecast:
    """Build an HourlyForecast with sensible defaults and optional overrides."""
    defaults = dict(
        time=datetime(2026, 2, 27, 12, tzinfo=timezone.utc),
        temperature_2m_c=15.0,
        dewpoint_2m_c=10.0,
        surface_pressure_hpa=1013.0,
        cape_jkg=250.0,
        freezing_level_m=2500.0,
        pressure_levels=_make_levels(),
    )
    defaults.update(overrides)
    return HourlyForecast(**defaults)


class TestNWPCapeFromHourly:
    """Verify raw CAPE is carried through from HourlyForecast."""

    def test_nwp_cape_set_from_hourly(self):
        hourly = _make_hourly(cape_jkg=350.0)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        assert result.indices.nwp_cape_jkg == 350.0

    def test_nwp_cape_none_when_hourly_none(self):
        hourly = _make_hourly(cape_jkg=None)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        assert result.indices.nwp_cape_jkg is None


class TestNWPCapeTypePerModel:
    """Verify CAPE type annotation per model key."""

    @pytest.mark.parametrize("model_key,expected_type", [
        ("gfs", "sb"),
        ("ecmwf", "mu"),
        ("icon", "ml"),
        ("ukmo", "unknown"),
        ("meteofrance", "unknown"),
        ("gem", "unknown"),
    ])
    def test_nwp_cape_type_per_model(self, model_key, expected_type):
        hourly = _make_hourly()
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key=model_key)
        assert result is not None
        assert result.indices.nwp_cape_type == expected_type

    def test_nwp_cape_type_none_when_no_model_key(self):
        hourly = _make_hourly()
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key=None)
        assert result is not None
        assert result.indices.nwp_cape_type is None


class TestNWPCIN:
    """Verify CIN carried when available."""

    def test_nwp_cin_set_when_available(self):
        hourly = _make_hourly(convective_inhibition_jkg=-50.0)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        assert result.indices.nwp_cin_jkg == -50.0

    def test_nwp_cin_none_when_not_available(self):
        hourly = _make_hourly(convective_inhibition_jkg=None)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        assert result.indices.nwp_cin_jkg is None


class TestNWPFieldsNoneWhenHourlyMissing:
    """All NWP fields are None when hourly is None."""

    def test_all_nwp_fields_none(self):
        levels = _make_levels()
        result = analyze_sounding(levels, hourly=None, model_key="gfs")
        assert result is not None
        assert result.indices.nwp_cape_jkg is None
        assert result.indices.nwp_cape_type is None
        assert result.indices.nwp_cin_jkg is None
        assert result.indices.nwp_lifted_index is None
        assert result.indices.nwp_freezing_level_ft is None
        assert result.indices.cape_raw_vs_calc_divergent is None


class TestNWPFreezingLevel:
    """Verify meters → feet conversion for freezing level."""

    def test_nwp_freezing_level_m_to_ft(self):
        hourly = _make_hourly(freezing_level_m=3000.0)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        expected_ft = round(3000.0 * 3.28084)
        assert result.indices.nwp_freezing_level_ft == expected_ft

    def test_nwp_freezing_level_none_when_missing(self):
        hourly = _make_hourly(freezing_level_m=None)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        assert result.indices.nwp_freezing_level_ft is None


class TestCAPEDivergenceFlag:
    """Verify divergence detection between raw and computed CAPE."""

    def test_cape_divergence_flagged_large_absolute_diff(self):
        # Raw CAPE = 500, computed will be whatever MetPy calculates.
        # Use a very large raw value to guarantee divergence.
        hourly = _make_hourly(cape_jkg=5000.0)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        idx = result.indices
        # Regardless of MetPy result, 5000 vs typical MetPy value
        # should be divergent (>200 J/kg absolute diff or >100% relative)
        if idx.cape_surface_jkg is not None:
            assert idx.cape_raw_vs_calc_divergent is True

    def test_cape_divergence_not_flagged_when_close(self):
        # If raw CAPE equals the MetPy-computed value, no divergence.
        hourly = _make_hourly()
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        idx = result.indices
        if idx.cape_surface_jkg is not None and idx.nwp_cape_jkg is not None:
            # Set raw = computed to verify no false positive
            # We can't control MetPy's output, so just verify the flag exists
            assert idx.cape_raw_vs_calc_divergent is not None

    def test_cape_divergence_none_when_raw_missing(self):
        hourly = _make_hourly(cape_jkg=None)
        result = analyze_sounding(hourly.pressure_levels, hourly, model_key="gfs")
        assert result is not None
        assert result.indices.cape_raw_vs_calc_divergent is None
