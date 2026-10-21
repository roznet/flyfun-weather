"""Icing zones merge across neighbours in the model's own level table (#835)."""

from __future__ import annotations

from weatherbrief.analysis.sounding.icing import assess_icing_zones_ogimet_dd
from weatherbrief.analysis.sounding.icing_common import group_icing_levels
from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
)


def _lv(p, alt, t=-5.0):
    return DerivedLevel(
        pressure_hpa=p, altitude_ft=alt, temperature_c=t,
        dewpoint_c=t - 0.5, dewpoint_depression_c=0.5,
    )


def _zones(all_levels, icing_pressures):
    icing = [
        (lv, IcingType.MIXED, IcingRisk.MODERATE, 50.0)
        for lv in all_levels if lv.pressure_hpa in icing_pressures
    ]
    return group_icing_levels(icing, lambda items: [lv.pressure_hpa for lv, *_ in items],
                              all_levels=all_levels)


# Open-Meteo ECMWF: 13 levels, nothing between 850 and 700 hPa.
_OM_ECMWF = [_lv(1000, 300), _lv(925, 2500), _lv(850, 5100), _lv(700, 10100), _lv(600, 14000)]
# ECMWF GRIB: 800 hPa present.
_GRIB_ECMWF = [_lv(1000, 300), _lv(925, 2500), _lv(850, 5100), _lv(800, 6600),
               _lv(700, 10100), _lv(600, 14000)]


def test_coarse_table_neighbours_merge():
    assert _zones(_OM_ECMWF, {850, 700}) == [[850, 700]]


def test_dense_table_unchanged():
    assert _zones(_GRIB_ECMWF, {850, 800, 700}) == [[850, 800, 700]]


def test_measured_clear_level_still_splits():
    assert _zones(_GRIB_ECMWF, {850, 700}) == [[850], [700]]


def test_sanity_cap_splits_degraded_sounding():
    # 850 → 600 with nothing between (levels dropped upstream): 250 hPa > cap.
    sparse = [_lv(850, 5100), _lv(600, 14000)]
    assert _zones(sparse, {850, 600}) == [[850], [600]]


def test_legacy_pressure_only_path_unchanged():
    icing = [(lv, IcingType.MIXED, IcingRisk.MODERATE, 50.0)
             for lv in _OM_ECMWF if lv.pressure_hpa in {850, 700}]
    zones = group_icing_levels(icing, lambda items: [lv.pressure_hpa for lv, *_ in items])
    assert zones == [[850], [700]]


def test_cruise_fl080_inside_zone_on_coarse_table():
    """The issue's case: FL080 between 850 and 700 hPa icing reads as in-zone."""
    levels = [
        _lv(1000, 300, t=8.0), _lv(925, 2500, t=4.0),
        _lv(850, 5100, t=-3.0), _lv(700, 10100, t=-10.0),
        _lv(600, 14000, t=-20.0),
    ]
    clouds = [EnhancedCloudLayer(base_ft=4500, top_ft=10600, coverage=CloudCoverage.OVC)]
    zones = assess_icing_zones_ogimet_dd(levels, clouds)
    assert len(zones) == 1
    assert zones[0].base_ft <= 8000 <= zones[0].top_ft
