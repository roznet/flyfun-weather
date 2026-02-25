"""Pure cost computation — no DB or I/O dependencies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """Typed mirror of a CostConfigRow for computation."""

    id: int
    token_cost_per_1k_input: float
    token_cost_per_1k_output: float
    droplet_monthly_usd: float
    misc_monthly_usd: float
    subscriptions_monthly_usd: float
    disk_cost_per_gb_monthly: float
    estimated_monthly_briefings: int
    margin_percent: float
    usd_per_credit: float


@dataclass(frozen=True)
class CostBreakdown:
    """Full cost breakdown for a single briefing."""

    token_cost_usd: float
    infra_share_usd: float
    subscription_share_usd: float
    storage_cost_usd: float
    subtotal_usd: float
    margin_usd: float
    total_usd: float
    credits_charged: float
    config_id: int


_BYTES_PER_GB = 1024**3
_MIN_BRIEFINGS = 500


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    result_size_bytes: int,
    config: CostConfig,
) -> CostBreakdown:
    """Compute the full cost breakdown for a briefing.

    All inputs are non-negative integers; config supplies the rate card.
    Returns a frozen CostBreakdown with all components.
    """
    est = max(config.estimated_monthly_briefings, _MIN_BRIEFINGS)

    token_cost = (
        (input_tokens / 1000) * config.token_cost_per_1k_input
        + (output_tokens / 1000) * config.token_cost_per_1k_output
    )
    infra_share = (config.droplet_monthly_usd + config.misc_monthly_usd) / est
    subscription_share = config.subscriptions_monthly_usd / est
    storage_cost = (result_size_bytes / _BYTES_PER_GB) * config.disk_cost_per_gb_monthly

    subtotal = token_cost + infra_share + subscription_share + storage_cost
    margin = subtotal * (config.margin_percent / 100)
    total = subtotal + margin
    credits = total / config.usd_per_credit if config.usd_per_credit > 0 else 0.0

    return CostBreakdown(
        token_cost_usd=round(token_cost, 6),
        infra_share_usd=round(infra_share, 6),
        subscription_share_usd=round(subscription_share, 6),
        storage_cost_usd=round(storage_cost, 6),
        subtotal_usd=round(subtotal, 6),
        margin_usd=round(margin, 6),
        total_usd=round(total, 6),
        credits_charged=round(credits, 2),
        config_id=config.id,
    )


def config_from_row(row) -> CostConfig:
    """Convert a CostConfigRow ORM object to a CostConfig dataclass."""
    return CostConfig(
        id=row.id,
        token_cost_per_1k_input=row.token_cost_per_1k_input,
        token_cost_per_1k_output=row.token_cost_per_1k_output,
        droplet_monthly_usd=row.droplet_monthly_usd,
        misc_monthly_usd=row.misc_monthly_usd,
        subscriptions_monthly_usd=row.subscriptions_monthly_usd,
        disk_cost_per_gb_monthly=row.disk_cost_per_gb_monthly,
        estimated_monthly_briefings=row.estimated_monthly_briefings,
        margin_percent=row.margin_percent,
        usd_per_credit=row.usd_per_credit,
    )


def breakdown_to_dict(b: CostBreakdown) -> dict:
    """Serialize a CostBreakdown for JSON storage in the ledger."""
    return {
        "token_cost_usd": b.token_cost_usd,
        "infra_share_usd": b.infra_share_usd,
        "subscription_share_usd": b.subscription_share_usd,
        "storage_cost_usd": b.storage_cost_usd,
        "subtotal_usd": b.subtotal_usd,
        "margin_usd": b.margin_usd,
        "total_usd": b.total_usd,
        "credits_charged": b.credits_charged,
        "config_id": b.config_id,
    }
