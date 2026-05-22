"""Tests for region-aware display-unit formatting."""

from weatherbrief.units import (
    format_qnh,
    format_temperature,
    format_visibility,
    normalize_preference,
    normalize_region,
    resolve_units_region,
)


class TestNormalizeRegion:
    def test_us_passes_through(self):
        assert normalize_region("us") == "us"

    def test_europe_and_unknown_default_to_europe(self):
        assert normalize_region("europe") == "europe"
        assert normalize_region(None) == "europe"
        assert normalize_region("garbage") == "europe"


class TestPreferenceResolution:
    def test_normalize_preference(self):
        assert normalize_preference("auto") == "auto"
        assert normalize_preference("us") == "us"
        assert normalize_preference("europe") == "europe"
        assert normalize_preference(None) == "auto"
        assert normalize_preference("garbage") == "auto"

    def test_forced_preference_wins(self):
        assert resolve_units_region("us", "europe") == "us"
        assert resolve_units_region("europe", "us") == "europe"

    def test_auto_uses_detected_region(self):
        assert resolve_units_region("auto", "us") == "us"
        assert resolve_units_region("auto", "europe") == "europe"

    def test_auto_falls_back_to_europe(self):
        assert resolve_units_region("auto", None) == "europe"
        assert resolve_units_region(None, None) == "europe"


class TestFormatVisibility:
    def test_none(self):
        assert format_visibility(None, "us") is None
        assert format_visibility(None, "europe") is None

    def test_us_statute_miles(self):
        assert format_visibility(4500, "us") == "2.8 SM"
        assert format_visibility(1609.34, "us") == "1.0 SM"

    def test_us_caps_at_10_sm(self):
        assert format_visibility(20000, "us") == ">10 SM"

    def test_europe_meters_below_5km(self):
        assert format_visibility(4500, "europe") == "4500 m"
        assert format_visibility(800, "europe") == "800 m"

    def test_europe_km_between_5_and_10(self):
        assert format_visibility(6000, "europe") == "6 km"
        assert format_visibility(8000, "europe") == "8 km"

    def test_europe_caps_at_10km(self):
        assert format_visibility(10000, "europe") == ">10 km"
        assert format_visibility(24140, "europe") == ">10 km"

    def test_default_region_is_europe(self):
        assert format_visibility(4500) == "4500 m"


class TestFormatQnh:
    def test_none(self):
        assert format_qnh(None, "us") is None

    def test_europe_hpa(self):
        assert format_qnh(1013, "europe") == "1013 hPa"
        assert format_qnh(1013.25, "europe") == "1013 hPa"

    def test_us_inhg(self):
        assert format_qnh(1013.25, "us") == "29.92 inHg"

    def test_default_region_is_europe(self):
        assert format_qnh(1013, ) == "1013 hPa"


class TestFormatTemperature:
    def test_none(self):
        assert format_temperature(None, "us") is None

    def test_europe_celsius(self):
        assert format_temperature(12, "europe") == "12°C"
        assert format_temperature(12.6, "europe", decimals=1) == "12.6°C"

    def test_us_fahrenheit(self):
        assert format_temperature(0, "us") == "32°F"
        assert format_temperature(20, "us") == "68°F"

    def test_default_region_is_europe(self):
        assert format_temperature(15) == "15°C"
