"""Tests for physically-grounded SLD detection."""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.sld import assess_sld_zones
from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    PrecipitationAssessment,
)
from weatherbrief.models.analysis import CloudCoverage


def _make_level(
    pressure_hpa: int,
    altitude_ft: float,
    temperature_c: float,
    wet_bulb_c: float | None = None,
    cloud_liquid_water_g_kg: float | None = None,
) -> DerivedLevel:
    return DerivedLevel(
        pressure_hpa=pressure_hpa,
        altitude_ft=altitude_ft,
        temperature_c=temperature_c,
        wet_bulb_c=wet_bulb_c,
        cloud_liquid_water_g_kg=cloud_liquid_water_g_kg,
    )


def _make_cloud(
    base_ft: float,
    top_ft: float,
    coverage: CloudCoverage = CloudCoverage.BKN,
) -> EnhancedCloudLayer:
    return EnhancedCloudLayer(
        base_ft=base_ft,
        top_ft=top_ft,
        coverage=coverage,
    )


# ── Mechanism 1: Warm-nose freezing rain ────────────────────────────


class TestWarmNoseSLD:
    """Tests for warm-nose freezing rain SLD detection."""

    def test_freezing_rain_produces_sld_zone(self):
        """When precipitation module detects freezing rain, SLD zone appears
        in the cold layer below the warm nose."""
        levels = [
            _make_level(1000, 500, -2.0, wet_bulb_c=-2.5),
            _make_level(950, 1500, -1.0, wet_bulb_c=-1.5),
            _make_level(900, 3000, 2.0, wet_bulb_c=1.5),   # warm nose
            _make_level(850, 5000, 3.0, wet_bulb_c=2.5),   # warm nose
            _make_level(800, 6500, -5.0, wet_bulb_c=-5.5),
        ]
        precip = PrecipitationAssessment(
            freezing_rain_risk=True,
            warm_nose_base_ft=3000.0,
            warm_nose_top_ft=5000.0,
        )

        zones = assess_sld_zones(levels, precipitation=precip)

        assert len(zones) == 1
        zone = zones[0]
        assert zone.mechanism == "warm_nose"
        assert zone.risk == IcingRisk.MODERATE
        # Zone should be below the warm nose
        assert zone.top_ft <= 3000.0

    def test_deep_warm_nose_is_severe(self):
        """A deep warm nose (>= 3000ft) produces severe SLD risk."""
        levels = [
            _make_level(1000, 500, -3.0, wet_bulb_c=-3.5),
            _make_level(950, 1500, -1.0, wet_bulb_c=-1.5),
            _make_level(900, 3000, 2.0, wet_bulb_c=1.5),
            _make_level(850, 5000, 4.0, wet_bulb_c=3.5),
            _make_level(800, 6500, 2.0, wet_bulb_c=1.5),  # deep warm nose
            _make_level(700, 10000, -8.0, wet_bulb_c=-8.5),
        ]
        precip = PrecipitationAssessment(
            freezing_rain_risk=True,
            warm_nose_base_ft=3000.0,
            warm_nose_top_ft=6500.0,  # 3500 ft deep
        )

        zones = assess_sld_zones(levels, precipitation=precip)

        assert len(zones) == 1
        assert zones[0].risk == IcingRisk.SEVERE

    def test_no_freezing_rain_no_sld(self):
        """Without freezing rain risk, no warm-nose SLD zone."""
        levels = [
            _make_level(1000, 500, -2.0),
            _make_level(900, 3000, -5.0),
        ]
        precip = PrecipitationAssessment(freezing_rain_risk=False)

        zones = assess_sld_zones(levels, precipitation=precip)

        assert len(zones) == 0

    def test_no_precipitation_data_no_warm_nose_sld(self):
        """Without precipitation assessment, warm-nose mechanism is skipped."""
        levels = [_make_level(1000, 500, -2.0)]
        zones = assess_sld_zones(levels)
        assert len(zones) == 0


# ── Mechanism 2: Collision-coalescence (disabled) ───────────────────


class TestCoalescenceDisabled:
    """Coalescence mechanism is disabled — verify it produces no zones."""

    def test_deep_warm_top_cloud_no_sld(self):
        """Deep warm-top cloud does NOT produce SLD when coalescence is disabled."""
        levels = [
            _make_level(1000, 500, 5.0),
            _make_level(950, 1500, 2.0),
            _make_level(900, 3000, 0.0),
            _make_level(850, 5000, -4.0),
            _make_level(800, 6500, -8.0),
        ]
        clouds = [_make_cloud(base_ft=2000, top_ft=6500)]
        freezing_level_ft = 3000.0

        zones = assess_sld_zones(
            levels,
            cloud_layers=clouds,
            freezing_level_ft=freezing_level_ft,
        )

        assert len(zones) == 0

    def test_cloud_data_alone_no_sld(self):
        """Cloud layers + freezing level without precip → no zones."""
        levels = [_make_level(1000, 500, -2.0)]
        clouds = [_make_cloud(base_ft=1000, top_ft=5000)]

        zones = assess_sld_zones(
            levels, cloud_layers=clouds, freezing_level_ft=3000.0,
        )

        assert len(zones) == 0


# ── Combined mechanisms ─────────────────────────────────────────────


class TestCombinedInputs:
    """Tests with all inputs provided."""

    def test_warm_nose_fires_even_with_cloud_data(self):
        """Warm-nose fires when precip has freezing rain, regardless of clouds."""
        levels = [
            _make_level(1000, 500, -3.0, wet_bulb_c=-3.5),
            _make_level(950, 1500, -1.0, wet_bulb_c=-1.5),
            _make_level(900, 3000, 2.0, wet_bulb_c=1.5),
            _make_level(850, 5000, 0.0, wet_bulb_c=-0.5),
            _make_level(800, 6500, -4.0),
        ]
        clouds = [_make_cloud(base_ft=5000, top_ft=10000)]
        precip = PrecipitationAssessment(
            freezing_rain_risk=True,
            warm_nose_base_ft=3000.0,
            warm_nose_top_ft=5000.0,
        )

        zones = assess_sld_zones(
            levels,
            cloud_layers=clouds,
            freezing_level_ft=5000.0,
            precipitation=precip,
        )

        assert len(zones) == 1
        assert zones[0].mechanism == "warm_nose"

    def test_no_fzra_with_deep_cloud_no_sld(self):
        """Deep cloud without freezing rain → no SLD (coalescence disabled)."""
        levels = [
            _make_level(1000, 500, 5.0),
            _make_level(900, 3000, 0.0),
            _make_level(800, 6500, -8.0),
        ]
        clouds = [_make_cloud(base_ft=2000, top_ft=6500)]
        precip = PrecipitationAssessment(freezing_rain_risk=False)

        zones = assess_sld_zones(
            levels,
            cloud_layers=clouds,
            freezing_level_ft=3000.0,
            precipitation=precip,
        )

        assert len(zones) == 0

    def test_empty_levels_returns_empty(self):
        """No levels → no zones."""
        zones = assess_sld_zones([])
        assert zones == []
