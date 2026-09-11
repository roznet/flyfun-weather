"""Side-call pricing: a non-briefing LLM call costs its own model's tokens.

The trip paragraph was billed through the per-briefing ``compute_cost``, which
adds a droplet/subscription share and margin to every call — ~$0.62 for a
sub-cent Haiku call, more than the briefing it summarises. ``compute_call_cost``
is tokens only, at the model's own rate.
"""

import pytest

from weatherbrief.costs import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIERS,
    compute_call_cost,
    token_rates_for,
)

HAIKU = "claude-haiku-4-5-20251001"


class TestTokenRates:
    def test_a_dated_snapshot_resolves_to_its_family(self):
        assert token_rates_for(HAIKU) == (0.001, 0.005)

    def test_a_provider_prefix_is_accepted(self):
        assert token_rates_for(f"anthropic:{HAIKU}") == (0.001, 0.005)

    def test_an_unpriced_model_is_rejected_not_guessed(self):
        with pytest.raises(ValueError, match="Unpriced model"):
            token_rates_for("claude-sonnet-4-6")

    def test_a_family_does_not_match_a_longer_version(self):
        # "claude-haiku-4-5" must not silently price a "claude-haiku-4-50".
        with pytest.raises(ValueError):
            token_rates_for("claude-haiku-4-50")


class TestComputeCallCost:
    def test_tokens_only(self):
        # 1.5k in at $0.001/1k + 0.2k out at $0.005/1k — no fixed share, no margin.
        cost = compute_call_cost(HAIKU, input_tokens=1500, output_tokens=200)
        assert cost == pytest.approx(0.0025)

    def test_an_empty_call_costs_nothing(self):
        assert compute_call_cost(HAIKU, input_tokens=0, output_tokens=0) == 0.0

    def test_cache_tokens_are_repriced_subsets_of_input(self):
        cost = compute_call_cost(
            HAIKU, input_tokens=1000, output_tokens=0,
            cache_read_tokens=600, cache_write_tokens=400, cache_ttl="5m",
        )
        expected = (
            0.6 * 0.001 * CACHE_READ_MULTIPLIER
            + 0.4 * 0.001 * CACHE_WRITE_MULTIPLIERS["5m"]
        )
        assert cost == pytest.approx(expected)
