"""Pure cost computation — no DB or I/O dependencies."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class CostConfig:
    """Rate card for cost computation.

    Stored as JSON in CostConfigRow.config_json.  Adding a new cost dimension
    means adding a field here, updating compute_cost(), and updating
    CostBreakdown — no DB migration required.
    """

    token_cost_per_1k_input: float = 0.003
    token_cost_per_1k_output: float = 0.015
    droplet_monthly_usd: float = 24.0
    misc_monthly_usd: float = 2.0
    subscriptions_monthly_usd: float = 30.0
    subscription_details: dict | None = None  # informational, e.g. {"open_meteo": 30}
    disk_cost_per_gb_monthly: float = 0.10
    estimated_monthly_briefings: int = 500
    margin_percent: float = 30.0

    def to_json(self) -> str:
        """Serialize to JSON for DB storage."""
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> CostConfig:
        """Deserialize from JSON, ignoring unknown keys."""
        data = json.loads(raw)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# Default config instance — used for seeding and as a reference.
DEFAULT_CONFIG = CostConfig(subscription_details={"open_meteo": 30})


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
    config_id: int


_BYTES_PER_GB = 1024**3
_MIN_BRIEFINGS = 500


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    result_size_bytes: int,
    config: CostConfig,
    config_id: int,
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

    return CostBreakdown(
        token_cost_usd=round(token_cost, 6),
        infra_share_usd=round(infra_share, 6),
        subscription_share_usd=round(subscription_share, 6),
        storage_cost_usd=round(storage_cost, 6),
        subtotal_usd=round(subtotal, 6),
        margin_usd=round(margin, 6),
        total_usd=round(total, 6),
        config_id=config_id,
    )


def config_from_row(row) -> tuple[CostConfig, int]:
    """Convert a CostConfigRow ORM object to a (CostConfig, config_id) tuple."""
    return CostConfig.from_json(row.config_json), row.id


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
        "config_id": b.config_id,
    }
