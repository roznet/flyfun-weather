"""Tests for weatherbrief.frontal.gates — FrontGateConfig + preset registry."""

from __future__ import annotations

import pytest

from weatherbrief.frontal.gates import (
    FrontGateConfig,
    get_preset,
    preset_names,
)


class TestSerialization:
    def test_roundtrip_default(self):
        cfg = FrontGateConfig()
        assert FrontGateConfig.from_dict(cfg.to_dict()) == cfg

    def test_roundtrip_custom(self):
        cfg = FrontGateConfig(
            name="custom", level_hPa=925, gradient_min=7.5,
            delta_theta_e_min=6.0, approach_dh=None, use_anomaly_filter=False,
        )
        assert FrontGateConfig.from_dict(cfg.to_dict()) == cfg

    def test_to_dict_is_json_friendly(self):
        d = FrontGateConfig().to_dict()
        # Every value is a primitive (None allowed for approach_dh).
        assert all(
            isinstance(v, (str, int, float, bool)) or v is None
            for v in d.values()
        )
        assert d["name"] == "default"
        assert d["level_hPa"] == 850

    def test_from_dict_ignores_unknown_keys(self):
        """A newer artifact with an extra gate still loads (forward-compat)."""
        data = FrontGateConfig().to_dict()
        data["some_future_gate"] = 42.0
        cfg = FrontGateConfig.from_dict(data)
        assert cfg == FrontGateConfig()

    def test_from_dict_missing_keys_use_defaults(self):
        cfg = FrontGateConfig.from_dict({"name": "partial", "gradient_min": 9.0})
        assert cfg.name == "partial"
        assert cfg.gradient_min == 9.0
        assert cfg.delta_theta_e_min == FrontGateConfig().delta_theta_e_min


class TestOverrides:
    def test_with_overrides_is_pure(self):
        base = FrontGateConfig()
        derived = base.with_overrides(gradient_min=10.0, level_hPa=700)
        assert derived.gradient_min == 10.0
        assert derived.level_hPa == 700
        # base untouched (frozen, copy semantics)
        assert base.gradient_min == 6.0
        assert base.level_hPa == 850

    def test_frozen(self):
        cfg = FrontGateConfig()
        with pytest.raises(Exception):
            cfg.gradient_min = 1.0  # type: ignore[misc]


class TestPresets:
    def test_known_presets_exist(self):
        names = preset_names()
        for expected in ("default", "strict", "sensitive", "gradient-only"):
            assert expected in names

    def test_strict_is_tighter_than_default(self):
        d = get_preset("default")
        s = get_preset("strict")
        assert s.gradient_min > d.gradient_min
        assert s.delta_theta_e_min > d.delta_theta_e_min

    def test_sensitive_is_looser_than_default(self):
        d = get_preset("default")
        s = get_preset("sensitive")
        assert s.gradient_min < d.gradient_min
        assert s.delta_theta_e_min < d.delta_theta_e_min

    def test_gradient_only_drops_airmass_gate(self):
        assert get_preset("gradient-only").delta_theta_e_min == 0.0

    def test_level_override(self):
        cfg = get_preset("strict", level_hPa=925)
        assert cfg.level_hPa == 925
        assert cfg.name == "strict"
        # preset registry entry itself is unchanged
        assert get_preset("strict").level_hPa == 850

    def test_unknown_preset_raises_with_names(self):
        with pytest.raises(KeyError, match="unknown gate preset"):
            get_preset("nope")
