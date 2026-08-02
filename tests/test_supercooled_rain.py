"""Supercooled-rain detection from qr + temperature (#530).

`qr > 0` at `T < 0 °C` is freezing rain / the large-droplet regime — physically
distinct from the supercooled CLOUD droplets the icing methods already see, and
considerably more hazardous. This module pins the threshold and, just as
importantly, pins the fact that the flag does NOT move any grade yet: the
detector has not been validated against a real winter case (#411). See
designs/meteorology-decisions.md §24.
"""

from __future__ import annotations

from weatherbrief.analysis.sounding import _enrich_lwc
from weatherbrief.analysis.sounding.icing_common import (
    SUPERCOOLED_RAIN_MAX_TEMP_C,
    SUPERCOOLED_RAIN_MIN_QR_KG_KG,
    is_supercooled_rain,
)
from weatherbrief.models import DerivedLevel, IcingRisk, PressureLevelData


class TestPredicate:
    def test_rain_below_freezing_is_supercooled(self):
        assert is_supercooled_rain(1e-4, -5.0)

    def test_rain_above_freezing_is_not(self):
        assert not is_supercooled_rain(1e-4, 2.0)

    def test_exactly_zero_c_is_not_supercooled(self):
        assert not is_supercooled_rain(1e-4, SUPERCOOLED_RAIN_MAX_TEMP_C)

    def test_trace_rain_is_below_threshold(self):
        """Numerical residue and advected traces must not raise a freezing-rain
        flag — the threshold is the entire judgement in this detector."""
        assert not is_supercooled_rain(SUPERCOOLED_RAIN_MIN_QR_KG_KG / 10, -5.0)
        assert is_supercooled_rain(SUPERCOOLED_RAIN_MIN_QR_KG_KG, -5.0)

    def test_threshold_admits_freezing_drizzle(self):
        """0.001 g/kg — light freezing drizzle, the dominant SLD hazard for GA,
        must not fall under the bar. A 1e-5 kg/kg threshold would erase it."""
        assert SUPERCOOLED_RAIN_MIN_QR_KG_KG <= 1e-6
        assert is_supercooled_rain(2e-6, -3.0)

    def test_missing_inputs_read_as_not_detected(self):
        """A model publishing no qr (ICON-EU, GFS, ECMWF) yields False — which
        means "not detected", never "no supercooled rain here"."""
        assert not is_supercooled_rain(None, -5.0)
        assert not is_supercooled_rain(1e-4, None)
        assert not is_supercooled_rain(None, None)


class TestEnrichment:
    def _enrich(self, qr: float | None, temp_c: float) -> DerivedLevel:
        dl = DerivedLevel(pressure_hpa=900, temperature_c=temp_c, altitude_ft=3000)
        raw = PressureLevelData(
            pressure_hpa=900,
            cloud_liquid_water_kg_kg=1e-5,
            rain_water_kg_kg=qr,
        )
        _enrich_lwc([dl], [raw])
        return dl

    def test_flag_produced_for_a_constructed_freezing_rain_profile(self):
        dl = self._enrich(qr=3e-4, temp_c=-4.0)
        assert dl.supercooled_rain is True
        assert dl.rain_water_g_kg == 0.3

    def test_warm_rain_sets_the_mixing_ratio_but_not_the_flag(self):
        dl = self._enrich(qr=3e-4, temp_c=6.0)
        assert dl.supercooled_rain is False
        assert dl.rain_water_g_kg == 0.3

    def test_model_without_qr_leaves_both_at_their_defaults(self):
        dl = self._enrich(qr=None, temp_c=-4.0)
        assert dl.supercooled_rain is False
        assert dl.rain_water_g_kg is None


class TestZoneRollUp:
    def _zone(self, supercooled: list[bool]):
        from weatherbrief.analysis.sounding.icing import _build_zone_simple
        from weatherbrief.models import IcingType

        items = [
            (
                DerivedLevel(
                    pressure_hpa=900 - 50 * i,
                    altitude_ft=3000 + 1000 * i,
                    temperature_c=-4.0,
                    supercooled_rain=flag,
                ),
                IcingType.RIME,
                IcingRisk.LIGHT,
                12.0,
            )
            for i, flag in enumerate(supercooled)
        ]
        return _build_zone_simple(items)

    def test_one_supercooled_level_flags_the_zone(self):
        """Freezing rain is not something a zone mean should dilute."""
        assert self._zone([False, True, False]).supercooled_rain is True

    def test_zone_without_any_stays_clear(self):
        assert self._zone([False, False]).supercooled_rain is False

    def test_flag_does_not_move_the_grade(self):
        """Reported, not graded (#411 gates the upgrade). A supercooled zone
        keeps the risk its icing index earned — nothing more."""
        assert self._zone([True, True]).risk is IcingRisk.LIGHT
        assert self._zone([False, False]).risk is IcingRisk.LIGHT

    def test_flag_alone_does_not_make_a_zone_hazardous(self):
        """`is_hazardous` is the advisory-facing predicate; wiring the flag
        into it would change grades without the validation #411 exists for."""
        from weatherbrief.models import IcingType, IcingZone

        zone = IcingZone(
            base_ft=3000, top_ft=5000,
            risk=IcingRisk.NONE, icing_type=IcingType.NONE,
            supercooled_rain=True,
        )
        assert zone.is_hazardous is False
