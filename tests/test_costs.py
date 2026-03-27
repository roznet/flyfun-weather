"""Tests for the cost computation module."""

from weatherbrief.costs import CostBreakdown, CostConfig, DEFAULT_CONFIG, compute_cost, breakdown_to_dict


def _default_config(**overrides) -> CostConfig:
    defaults = dict(
        token_cost_per_1k_input=0.003,
        token_cost_per_1k_output=0.015,
        droplet_monthly_usd=24.0,
        misc_monthly_usd=2.0,
        subscriptions_monthly_usd=30.0,
        disk_cost_per_gb_monthly=0.10,
        estimated_monthly_briefings=500,
        margin_percent=30.0,
    )
    defaults.update(overrides)
    return CostConfig(**defaults)


class TestComputeCost:
    """Verify formula arithmetic for compute_cost."""

    def test_typical_llm_briefing(self):
        """A briefing with LLM tokens and modest pack size."""
        config = _default_config()
        b = compute_cost(
            input_tokens=5000,
            output_tokens=2000,
            result_size_bytes=10 * 1024 * 1024,  # 10 MB
            config=config,
            config_id=1,
        )

        # Token cost: (5000/1000)*0.003 + (2000/1000)*0.015 = 0.015 + 0.030 = 0.045
        assert abs(b.token_cost_usd - 0.045) < 1e-5

        # Infra share: (24 + 2) / 500 = 0.052
        assert abs(b.infra_share_usd - 0.052) < 1e-5

        # Subscription share: 30 / 500 = 0.06
        assert abs(b.subscription_share_usd - 0.06) < 1e-5

        # Storage: 10MB / 1GB * 0.10 = ~0.000977
        expected_storage = (10 * 1024 * 1024 / (1024**3)) * 0.10
        assert abs(b.storage_cost_usd - expected_storage) < 1e-5

        # Subtotal
        expected_subtotal = 0.045 + 0.052 + 0.06 + expected_storage
        assert abs(b.subtotal_usd - expected_subtotal) < 1e-4

        # Margin = 30%
        assert abs(b.margin_usd - expected_subtotal * 0.3) < 1e-4

        # Total
        expected_total = expected_subtotal * 1.3
        assert abs(b.total_usd - expected_total) < 1e-4

        assert b.config_id == 1

    def test_zero_tokens(self):
        """A non-LLM briefing — no token cost, only infra/subs/storage."""
        config = _default_config()
        b = compute_cost(
            input_tokens=0,
            output_tokens=0,
            result_size_bytes=5 * 1024 * 1024,
            config=config,
            config_id=1,
        )
        assert b.token_cost_usd == 0.0
        # Should still have infra + subs + storage
        assert b.infra_share_usd > 0
        assert b.subscription_share_usd > 0
        assert b.total_usd > 0

    def test_zero_pack_size(self):
        """Zero storage bytes — storage cost should be zero."""
        config = _default_config()
        b = compute_cost(
            input_tokens=1000,
            output_tokens=500,
            result_size_bytes=0,
            config=config,
            config_id=1,
        )
        assert b.storage_cost_usd == 0.0
        assert b.token_cost_usd > 0

    def test_large_pack(self):
        """A 1 GB pack — storage cost should be exactly disk_cost_per_gb_monthly."""
        config = _default_config()
        b = compute_cost(
            input_tokens=0,
            output_tokens=0,
            result_size_bytes=1024**3,  # 1 GB
            config=config,
            config_id=1,
        )
        assert abs(b.storage_cost_usd - 0.10) < 1e-6

    def test_min_briefings_floor(self):
        """estimated_monthly_briefings below 500 gets floored to 500."""
        config = _default_config(estimated_monthly_briefings=10)
        b = compute_cost(
            input_tokens=0, output_tokens=0, result_size_bytes=0,
            config=config, config_id=1,
        )
        # Infra share should use 500 as divisor, not 10
        expected_infra = (24.0 + 2.0) / 500
        assert abs(b.infra_share_usd - expected_infra) < 1e-6

    def test_zero_margin(self):
        """With 0% margin, total equals subtotal."""
        config = _default_config(margin_percent=0.0)
        b = compute_cost(
            input_tokens=1000, output_tokens=500, result_size_bytes=1024 * 1024,
            config=config, config_id=1,
        )
        assert b.margin_usd == 0.0
        assert abs(b.total_usd - b.subtotal_usd) < 1e-6

    def test_high_margin(self):
        """100% margin doubles the subtotal."""
        config = _default_config(margin_percent=100.0)
        b = compute_cost(
            input_tokens=1000, output_tokens=500, result_size_bytes=0,
            config=config, config_id=1,
        )
        assert abs(b.total_usd - b.subtotal_usd * 2) < 1e-4

    def test_breakdown_is_frozen(self):
        """CostBreakdown should be immutable."""
        config = _default_config()
        b = compute_cost(
            input_tokens=1000, output_tokens=500, result_size_bytes=0,
            config=config, config_id=1,
        )
        assert isinstance(b, CostBreakdown)
        try:
            b.total_usd = 999.0  # type: ignore[misc]
            assert False, "Should not be able to set attribute on frozen dataclass"
        except AttributeError:
            pass


class TestCostConfigJson:
    """Verify CostConfig JSON round-trip and defaults."""

    def test_roundtrip(self):
        config = _default_config()
        json_str = config.to_json()
        restored = CostConfig.from_json(json_str)
        assert restored == config

    def test_ignores_unknown_keys(self):
        """Old configs with removed keys (e.g. usd_per_credit) parse cleanly."""
        import json
        data = json.loads(DEFAULT_CONFIG.to_json())
        data["usd_per_credit"] = 0.01  # legacy key
        restored = CostConfig.from_json(json.dumps(data))
        assert restored.token_cost_per_1k_input == DEFAULT_CONFIG.token_cost_per_1k_input

    def test_defaults_applied(self):
        """Missing keys in JSON fall back to dataclass defaults."""
        config = CostConfig.from_json('{"margin_percent": 50.0}')
        assert config.margin_percent == 50.0
        assert config.token_cost_per_1k_input == 0.003  # default

    def test_default_config_has_subscription_details(self):
        assert DEFAULT_CONFIG.subscription_details == {"open_meteo": 30}


class TestBreakdownToDict:
    def test_roundtrip(self):
        config = _default_config()
        b = compute_cost(
            input_tokens=3000, output_tokens=1000, result_size_bytes=2 * 1024 * 1024,
            config=config, config_id=1,
        )
        d = breakdown_to_dict(b)
        assert d["total_usd"] == b.total_usd
        assert d["config_id"] == 1
        assert set(d.keys()) == {
            "token_cost_usd", "infra_share_usd", "subscription_share_usd",
            "storage_cost_usd", "subtotal_usd", "margin_usd", "total_usd",
            "config_id",
        }
