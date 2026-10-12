"""Tests for weatherbrief.atmo — ISA airspeed conversion + cruise-speed resolution.

Mirrors the frontend web/ts/utils/atmo.ts; the values here should match it.
"""

from __future__ import annotations

import math

import pytest

from weatherbrief.atmo import ias_to_tas_isa, resolve_cruise_speed_ias


class TestIasToTasIsa:
    def test_sea_level_is_identity(self):
        assert ias_to_tas_isa(120, 0) == pytest.approx(120, abs=0.01)

    def test_tas_exceeds_ias_with_altitude(self):
        # ~2% per 1000ft rule of thumb: 150 IAS @ 10,000ft ≈ 174 TAS.
        tas = ias_to_tas_isa(150, 10000)
        assert tas > 150
        assert tas == pytest.approx(174, abs=2)

    def test_monotonic_in_altitude(self):
        assert ias_to_tas_isa(150, 12000) > ias_to_tas_isa(150, 6000)

    def test_clamped_at_tropopause(self):
        # Above the tropopause the formula is clamped, so FL360 and FL400 match.
        assert ias_to_tas_isa(150, 36089) == pytest.approx(ias_to_tas_isa(150, 40000), abs=0.5)

    def test_matches_isa_formula(self):
        alt_m = 8000 / 3.28084
        ratio = (1 - 0.0065 * alt_m / 288.15) ** 4.2561
        assert ias_to_tas_isa(150, 8000) == pytest.approx(150 / math.sqrt(ratio), abs=1e-6)


class TestResolveCruiseSpeedIas:
    """Speed comes only from the aircraft (#777) — no profile fallback."""

    def test_aircraft_speed_used(self):
        assert resolve_cruise_speed_ias(150) == 150.0

    def test_none_when_aircraft_has_no_speed(self):
        assert resolve_cruise_speed_ias(None) is None

    def test_nonpositive_is_unusable(self):
        assert resolve_cruise_speed_ias(0) is None
        assert resolve_cruise_speed_ias(-5) is None
