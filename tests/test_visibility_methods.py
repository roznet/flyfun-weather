"""Derived visibility and fog methods (#979 phase 1)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.visibility import methods as m
from weatherbrief.analysis.visibility.methods import VisibilityInputs as VI


def _fog_inputs(**kw):
    base = dict(temperature_2m_c=5.0, dewpoint_2m_c=4.8, surface_pressure_hpa=1013.25,
                wind_speed_10m_kt=2.0, cloud_cover_low_pct=0.0)
    base.update(kw)
    return VI(**base)


class TestVisibility:
    def test_sw99_cloud_water_worked_value(self):
        # rho = 101325 / (287.05 * 278.15) = 1.269 kg m-3; qc 1e-4 → 0.1269 g m-3
        # beta = 144.7 * 0.1269^0.88 = 23.5 km-1 → vis = 3.912 / 23.5 km = 166 m
        inp = _fog_inputs(lowest_level_cloud_water_kg_kg=1e-4)
        rho = 101325 / (287.05 * 278.15)
        beta = 144.7 * (1e-4 * rho * 1000) ** 0.88
        assert m.vis_sw99(inp) == pytest.approx(3912 / beta, rel=1e-6)
        assert m.vis_sw99(inp) == pytest.approx(166, abs=1)

    def test_sw99_dry_air_is_the_cap(self):
        inp = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0, precipitation_mm=0.0,
                          snowfall_cm=0.0)
        assert m.vis_sw99(inp) == m.VIS_CAP_M

    def test_sw99_needs_cloud_water(self):
        assert m.vis_sw99(_fog_inputs()) is None
        assert m.vis_sw99(VI(lowest_level_cloud_water_kg_kg=1e-4)) is None  # no T

    def test_sw99_rain_from_the_rate_when_no_mixing_ratio(self):
        # 1 mm/h: W = 0.089 g m-3, beta = 2.24 * 0.089^0.75 = 0.36 km-1 → ~10.9 km
        inp = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0, precipitation_mm=1.0,
                          precip_period_h=1, snowfall_cm=0.0)
        assert m.vis_sw99(inp) == pytest.approx(3912 / (2.24 * 0.089 ** 0.75), rel=1e-6)
        # A 3 h total is three times the hourly amount, not the rate.
        inp3 = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0, precipitation_mm=3.0,
                           precip_period_h=3, snowfall_cm=0.0)
        assert m.vis_sw99(inp3) == pytest.approx(m.vis_sw99(inp))

    def test_sw99_mixing_ratio_wins_over_the_rate(self):
        with_qr = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0,
                              lowest_level_rain_kg_kg=0.0, precipitation_mm=5.0)
        assert m.vis_sw99(with_qr) == m.VIS_CAP_M

    def test_snow_reduces_more_than_rain(self):
        rain = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0, precipitation_mm=1.0,
                           snowfall_cm=0.0)
        snow = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0, precipitation_mm=1.0,
                           snowfall_cm=0.7)  # 1 mm water as snow
        assert m.vis_sw99(snow) < m.vis_sw99(rain) / 3

    def test_gultepe_worked_value(self):
        # LWC 0.1 g m-3, Nd 100 → 1.002 / 10^0.6473 km = 225 m
        rho = 101325 / (287.05 * 278.15)
        inp = _fog_inputs(lowest_level_cloud_water_kg_kg=0.1 / rho / 1000)
        assert m.vis_gultepe06(inp) == pytest.approx(1002 / 10 ** 0.6473, rel=1e-6)

    def test_gultepe_no_fog_water_is_the_cap(self):
        assert m.vis_gultepe06(_fog_inputs(lowest_level_cloud_water_kg_kg=1e-8)) == m.VIS_CAP_M

    def test_rh_haze(self):
        assert m.vis_rh_haze(VI(temperature_2m_c=10, dewpoint_2m_c=10)) == pytest.approx(
            60000 * math.exp(-2.5 * 85 / 80))
        assert m.vis_rh_haze(VI(temperature_2m_c=25, dewpoint_2m_c=0)) == m.VIS_CAP_M
        assert m.vis_rh_haze(VI(temperature_2m_c=10)) is None

    def test_rap_blend_is_the_min(self):
        inp = _fog_inputs(lowest_level_cloud_water_kg_kg=0.0, precipitation_mm=0.0,
                          snowfall_cm=0.0)
        assert m.vis_rap_blend(inp) == m.vis_rh_haze(inp) < m.VIS_CAP_M
        assert m.vis_rap_blend(_fog_inputs()) is None  # no sw99

    def test_native_passthrough(self):
        assert m.vis_native(VI(visibility_m=35000)) == m.VIS_CAP_M
        assert m.vis_native(VI(visibility_m=800)) == 800
        assert m.vis_native(VI(visibility_m=-1)) is None
        assert m.vis_native(VI()) is None


class TestFog:
    def test_fog_model(self):
        assert m.fog_model(VI(lowest_level_cloud_cover_pct=95)) is True
        assert m.fog_model(VI(lowest_level_cloud_cover_pct=50)) is False
        assert m.fog_model(_fog_inputs(lowest_level_cloud_water_kg_kg=1e-4)) is True
        assert m.fog_model(VI()) is None

    def test_radiation(self):
        assert m.fog_radiation(_fog_inputs(is_night=True)) is True
        assert m.fog_radiation(_fog_inputs(is_night=False)) is False
        assert m.fog_radiation(_fog_inputs(is_night=True, wind_speed_10m_kt=10)) is False
        assert m.fog_radiation(_fog_inputs(is_night=True, cloud_cover_low_pct=80)) is False
        assert m.fog_radiation(_fog_inputs()) is None  # night unknown

    def test_fsi(self):
        # 2*(5-3) + 2*0.2 + 10 = 14.4 < 31 (= 4*5 - 2*(3+4.8) + 10)
        inp = _fog_inputs(t850_c=3.0, wind_speed_850_kt=10.0)
        assert m.fog_stability_index(inp) == pytest.approx(14.4)
        assert m.fog_fsi(inp) is True
        assert m.fog_fsi(_fog_inputs(t850_c=-10.0, wind_speed_850_kt=30.0)) is False
        assert m.fog_fsi(_fog_inputs()) is None

    @pytest.mark.parametrize("rules,vis,expected", [
        ({"fog_model": True, "fog_radiation": False, "fog_fsi": False}, None, "LIKELY"),
        ({"fog_model": False, "fog_radiation": None, "fog_fsi": None}, 800, "LIKELY"),
        ({"fog_model": False, "fog_radiation": True, "fog_fsi": True}, None, "LIKELY"),
        ({"fog_model": False, "fog_radiation": True, "fog_fsi": False}, None, "POSSIBLE"),
        ({"fog_model": False, "fog_radiation": None, "fog_fsi": True}, None, "POSSIBLE"),
        # No model fog and no derived vis (ECMWF / GFS, pre-#980 runs): the
        # tier cannot see fog, so it is unknown, never NONE or POSSIBLE.
        ({"fog_model": None, "fog_radiation": True, "fog_fsi": True}, None, None),
        ({"fog_model": None, "fog_radiation": False, "fog_fsi": False}, None, None),
        ({"fog_model": False, "fog_radiation": False, "fog_fsi": False}, 20000, "NONE"),
        ({"fog_model": None, "fog_radiation": None, "fog_fsi": None}, None, None),
    ])
    def test_tier(self, rules, vis, expected):
        assert m.fog_tier(VI(temperature_2m_c=10, dewpoint_2m_c=5), rules, vis) == expected

    def test_tier_humid_haze_is_possible(self):
        rules = {"fog_model": False, "fog_radiation": False, "fog_fsi": False}
        humid = VI(temperature_2m_c=10, dewpoint_2m_c=9.5)  # RH ~97 %
        assert m.fog_tier(humid, rules, 3000) == "POSSIBLE"
        assert m.fog_tier(VI(temperature_2m_c=10, dewpoint_2m_c=5), rules, 3000) == "NONE"


def test_run_all_skips_missing_methods():
    out = m.run_all(VI(temperature_2m_c=10, dewpoint_2m_c=9, visibility_m=4000))
    assert set(out) == {"native", "rh_haze"}  # no fog rule evaluable: no tier
    full = m.run_all(_fog_inputs(
        lowest_level_cloud_water_kg_kg=1e-4, lowest_level_cloud_cover_pct=100,
        visibility_m=200, t850_c=3.0, wind_speed_850_kt=10.0, is_night=True,
        precipitation_mm=0.0, snowfall_cm=0.0,
    ))
    assert set(out) < set(full)
    assert full["fog_tier"] == "LIKELY"
    json.dumps(full)  # storable as is


def test_is_night():
    assert m.is_night(48.0, 2.0, datetime(2026, 11, 8, 2, tzinfo=timezone.utc)) is True
    assert m.is_night(48.0, 2.0, datetime(2026, 11, 8, 12, tzinfo=timezone.utc)) is False
