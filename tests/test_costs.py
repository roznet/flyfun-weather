"""Tests for the cost computation module."""

from weatherbrief.costs import (
    CostBreakdown,
    CostConfig,
    DEFAULT_CONFIG,
    breakdown_to_dict,
    compute_cost,
    compute_program_cost,
    program_report_to_dict,
)


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


class TestComputeProgramCost:
    """Program-wide window report: real fixed cost + actual variable cost."""

    def test_30d_proration_equals_monthly(self):
        """A 30-day window prorates fixed cost to exactly one month."""
        r = compute_program_cost(
            _default_config(), config_id=1, window_days=30,
            variable_token_usd=0.0, variable_storage_usd=0.0,
            num_briefings=0, num_users=0,
        )
        # 24 (droplet) + 2 (misc) + 30 (subs) = 56
        assert r.fixed_monthly_usd == 56.0
        assert r.fixed_prorated_usd == 56.0

    def test_7d_proration_scales_down(self):
        """A 7-day window prorates fixed cost by 7/30."""
        r = compute_program_cost(
            _default_config(), config_id=1, window_days=7,
            variable_token_usd=0.0, variable_storage_usd=0.0,
            num_briefings=0, num_users=0,
        )
        assert r.fixed_monthly_usd == 56.0
        assert abs(r.fixed_prorated_usd - 56.0 * 7 / 30) < 1e-3

    def test_variable_is_token_plus_storage(self):
        r = compute_program_cost(
            _default_config(), config_id=1, window_days=30,
            variable_token_usd=0.40, variable_storage_usd=0.10,
            num_briefings=100, num_users=10,
        )
        assert abs(r.variable_usd - 0.50) < 1e-6
        assert abs(r.subtotal_usd - (56.0 + 0.50)) < 1e-3

    def test_margin_included_in_total(self):
        """Total includes the configured margin on the combined subtotal."""
        r = compute_program_cost(
            _default_config(margin_percent=30.0), config_id=1, window_days=30,
            variable_token_usd=0.40, variable_storage_usd=0.10,
            num_briefings=100, num_users=10,
        )
        subtotal = 56.0 + 0.50
        assert abs(r.margin_usd - subtotal * 0.3) < 1e-3
        assert abs(r.total_usd - subtotal * 1.3) < 1e-3

    def test_per_briefing_and_per_user(self):
        r = compute_program_cost(
            _default_config(margin_percent=0.0), config_id=1, window_days=30,
            variable_token_usd=4.0, variable_storage_usd=0.0,
            num_briefings=100, num_users=10,
        )
        # subtotal = 56 + 4 = 60, margin 0 -> total 60
        assert abs(r.total_usd - 60.0) < 1e-3
        assert abs(r.cost_per_briefing_usd - 0.6) < 1e-3
        assert abs(r.cost_per_user_usd - 6.0) < 1e-3

    def test_zero_briefings_and_users_guarded(self):
        """No briefings/users must not divide by zero."""
        r = compute_program_cost(
            _default_config(), config_id=1, window_days=30,
            variable_token_usd=0.0, variable_storage_usd=0.0,
            num_briefings=0, num_users=0,
        )
        assert r.cost_per_briefing_usd == 0.0
        assert r.cost_per_user_usd == 0.0

    def test_subscription_line_items_itemized(self):
        """Each subscription_details entry becomes its own prorated fixed line."""
        cfg = _default_config(subscription_details={"open_meteo": 30, "ecmwf": 50})
        r = compute_program_cost(
            cfg, config_id=1, window_days=30,
            variable_token_usd=0.0, variable_storage_usd=0.0,
            num_briefings=0, num_users=0,
        )
        labels = [ln.label for ln in r.fixed_lines]
        assert "Subscription: open_meteo" in labels
        assert "Subscription: ecmwf" in labels
        # 24 + 2 + 30 + 50 = 106
        assert r.fixed_monthly_usd == 106.0

    def test_no_subscription_details_single_line(self):
        """Without itemization, a single Subscriptions line uses the total."""
        cfg = _default_config(subscription_details=None, subscriptions_monthly_usd=80.0)
        r = compute_program_cost(
            cfg, config_id=1, window_days=30,
            variable_token_usd=0.0, variable_storage_usd=0.0,
            num_briefings=0, num_users=0,
        )
        labels = [ln.label for ln in r.fixed_lines]
        assert "Subscriptions" in labels
        assert r.fixed_monthly_usd == 24.0 + 2.0 + 80.0

    def test_report_to_dict_keys(self):
        r = compute_program_cost(
            _default_config(), config_id=7, window_days=30,
            variable_token_usd=0.4, variable_storage_usd=0.1,
            num_briefings=10, num_users=3,
        )
        d = program_report_to_dict(r)
        assert d["config_id"] == 7
        assert set(d.keys()) == {
            "window_days", "fixed_lines", "fixed_monthly_usd", "fixed_prorated_usd",
            "variable_token_usd", "variable_storage_usd", "variable_usd",
            "subtotal_usd", "margin_percent", "margin_usd", "total_usd",
            "num_briefings", "num_users", "cost_per_briefing_usd",
            "cost_per_user_usd", "config_id",
        }
        assert isinstance(d["fixed_lines"], list)
        assert set(d["fixed_lines"][0].keys()) == {"label", "monthly_usd", "prorated_usd"}


class TestPromptCachePricing:
    """Cached input tokens must bill at their own multipliers, not full price.

    langchain-anthropic reports one ``input_tokens`` total that already
    *includes* the cached tokens, so compute_cost has to subtract them back out
    before pricing. Getting this wrong is silent: the ledger would keep showing
    the pre-caching cost and the saving would be invisible.
    """

    def test_defaults_reproduce_uncached_pricing(self):
        """Omitting the cache args must match the old behaviour exactly."""
        config = _default_config()
        without = compute_cost(
            input_tokens=10000, output_tokens=1000,
            result_size_bytes=0, config=config, config_id=1,
        )
        explicit_zero = compute_cost(
            input_tokens=10000, output_tokens=1000,
            result_size_bytes=0, config=config, config_id=1,
            cache_read_tokens=0, cache_write_tokens=0,
        )
        assert without.token_cost_usd == explicit_zero.token_cost_usd
        # (10000/1000)*0.003 + (1000/1000)*0.015 = 0.03 + 0.015
        assert abs(without.token_cost_usd - 0.045) < 1e-6

    def test_cache_read_is_cheaper_than_full_price(self):
        """A read bills 0.1x, so 5k cached of 10k input costs less than 10k raw."""
        config = _default_config()
        b = compute_cost(
            input_tokens=10000, output_tokens=1000,
            result_size_bytes=0, config=config, config_id=1,
            cache_read_tokens=5000,
        )
        # uncached 5000 @ 0.003 = 0.015; read 5000 @ 0.0003 = 0.0015; out 0.015
        assert abs(b.token_cost_usd - 0.0315) < 1e-6
        assert b.token_cost_usd < 0.045

    def test_cache_write_is_dearer_than_full_price(self):
        """A 1h write bills 2x — a first, uncached call must cost more."""
        config = _default_config()
        b = compute_cost(
            input_tokens=10000, output_tokens=1000,
            result_size_bytes=0, config=config, config_id=1,
            cache_write_tokens=5000,
        )
        # uncached 5000 @ 0.003 = 0.015; write 5000 @ 0.006 = 0.030; out 0.015
        assert abs(b.token_cost_usd - 0.060) < 1e-6
        assert b.token_cost_usd > 0.045

    def test_read_saving_outweighs_write_cost_at_measured_hit_rate(self):
        """Sanity-check the whole reason for enabling caching.

        Prod gaps put the hit rate near 84% at the 1h TTL, well above the 53%
        break-even. Averaging one write against ~5 reads must come out ahead of
        never caching at all.
        """
        config = _default_config()
        prefix, ctx, out = 4661, 6800, 1200
        total_in = prefix + ctx

        def cost(**kw):
            return compute_cost(
                input_tokens=total_in, output_tokens=out,
                result_size_bytes=0, config=config, config_id=1, **kw
            ).token_cost_usd

        baseline = cost()
        write = cost(cache_write_tokens=prefix)
        read = cost(cache_read_tokens=prefix)
        assert write > baseline > read
        # One miss then four hits — the shape of an 80% hit rate.
        assert (write + 4 * read) / 5 < baseline

    def test_cached_never_exceeds_input_total(self):
        """Defends against a provider change making the figures siblings.

        If cache tokens ever stopped being a subset of input_tokens, an
        unclamped subtraction would drive the uncached count negative and
        refund the user. Clamping keeps the bill non-negative.
        """
        config = _default_config()
        b = compute_cost(
            input_tokens=1000, output_tokens=0,
            result_size_bytes=0, config=config, config_id=1,
            cache_read_tokens=5000, cache_write_tokens=5000,
        )
        assert b.token_cost_usd >= 0
