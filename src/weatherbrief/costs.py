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
    # Prompt-caching multipliers applied to token_cost_per_1k_input. Anthropic
    # bills a cache write at 1.25x on the 5-minute TTL and 2x on the 1h TTL we
    # use for digests; a cache read is 0.1x either way.
    cache_write_multiplier: float = 2.0
    cache_read_multiplier: float = 0.1
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
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> CostBreakdown:
    """Compute the full cost breakdown for a briefing.

    All inputs are non-negative integers; config supplies the rate card.
    Returns a frozen CostBreakdown with all components.

    ``cache_read_tokens`` and ``cache_write_tokens`` are **subsets** of
    ``input_tokens``, not extras — langchain-anthropic reports a single total
    that already includes both. They are subtracted out and re-priced at their
    own multipliers, so the three tiers each bill correctly. Both default to 0,
    which reproduces the pre-caching behaviour exactly for historical rows.
    """
    est = max(config.estimated_monthly_briefings, _MIN_BRIEFINGS)

    # Clamp rather than trust: a provider-side reporting change that made the
    # cache figures siblings of input_tokens instead of subsets would otherwise
    # drive uncached negative and silently *refund* users.
    cached = min(cache_read_tokens + cache_write_tokens, input_tokens)
    uncached_input = input_tokens - cached

    token_cost = (
        (uncached_input / 1000) * config.token_cost_per_1k_input
        + (cache_read_tokens / 1000)
        * config.token_cost_per_1k_input
        * config.cache_read_multiplier
        + (cache_write_tokens / 1000)
        * config.token_cost_per_1k_input
        * config.cache_write_multiplier
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


_DAYS_PER_MONTH = 30.0


@dataclass(frozen=True)
class FixedCostLine:
    """One amortizable fixed-cost line item (server, a subscription, ...)."""

    label: str
    monthly_usd: float
    prorated_usd: float  # monthly_usd scaled to the report window


@dataclass(frozen=True)
class ProgramCostReport:
    """Program-wide cost over a window: real fixed cost + actual variable cost.

    Fixed costs come straight from the config (prorated by window), decoupled
    from the per-briefing amortization, so they reflect what the operator
    actually pays.  Variable costs are the actual token + storage charges
    summed from the ledger over the same window.
    """

    window_days: int
    fixed_lines: tuple[FixedCostLine, ...]
    fixed_monthly_usd: float
    fixed_prorated_usd: float
    variable_token_usd: float
    variable_storage_usd: float
    variable_usd: float
    subtotal_usd: float  # fixed_prorated + variable
    margin_percent: float
    margin_usd: float
    total_usd: float
    num_briefings: int
    num_users: int
    cost_per_briefing_usd: float
    cost_per_user_usd: float
    config_id: int


def compute_program_cost(
    config: CostConfig,
    config_id: int,
    window_days: int,
    variable_token_usd: float,
    variable_storage_usd: float,
    num_briefings: int,
    num_users: int,
) -> ProgramCostReport:
    """Compute a program-wide cost report for a time window.

    Fixed costs (droplet, misc, each subscription line) are prorated to the
    window.  Variable costs are the actual token/storage totals from the
    ledger.  Margin is applied to the combined subtotal so the rate card stays
    internally consistent.  Per-briefing/per-user figures are guarded for zero.
    """
    months = window_days / _DAYS_PER_MONTH

    def _line(label: str, monthly: float) -> FixedCostLine:
        return FixedCostLine(
            label=label,
            monthly_usd=round(monthly, 4),
            prorated_usd=round(monthly * months, 4),
        )

    lines: list[FixedCostLine] = [
        _line("Server (droplet)", config.droplet_monthly_usd),
        _line("Misc", config.misc_monthly_usd),
    ]
    details = config.subscription_details or {}
    if details:
        for name, monthly in details.items():
            lines.append(_line(f"Subscription: {name}", float(monthly)))
    elif config.subscriptions_monthly_usd:
        lines.append(_line("Subscriptions", config.subscriptions_monthly_usd))

    fixed_monthly = sum(line.monthly_usd for line in lines)
    fixed_prorated = sum(line.prorated_usd for line in lines)
    variable = variable_token_usd + variable_storage_usd
    subtotal = fixed_prorated + variable
    margin = subtotal * (config.margin_percent / 100)
    total = subtotal + margin

    return ProgramCostReport(
        window_days=window_days,
        fixed_lines=tuple(lines),
        fixed_monthly_usd=round(fixed_monthly, 4),
        fixed_prorated_usd=round(fixed_prorated, 4),
        variable_token_usd=round(variable_token_usd, 4),
        variable_storage_usd=round(variable_storage_usd, 4),
        variable_usd=round(variable, 4),
        subtotal_usd=round(subtotal, 4),
        margin_percent=config.margin_percent,
        margin_usd=round(margin, 4),
        total_usd=round(total, 4),
        num_briefings=num_briefings,
        num_users=num_users,
        cost_per_briefing_usd=round(total / num_briefings, 4) if num_briefings > 0 else 0.0,
        cost_per_user_usd=round(total / num_users, 4) if num_users > 0 else 0.0,
        config_id=config_id,
    )


def program_report_to_dict(r: ProgramCostReport) -> dict:
    """Serialize a ProgramCostReport for the API response."""
    return {
        "window_days": r.window_days,
        "fixed_lines": [
            {"label": ln.label, "monthly_usd": ln.monthly_usd, "prorated_usd": ln.prorated_usd}
            for ln in r.fixed_lines
        ],
        "fixed_monthly_usd": r.fixed_monthly_usd,
        "fixed_prorated_usd": r.fixed_prorated_usd,
        "variable_token_usd": r.variable_token_usd,
        "variable_storage_usd": r.variable_storage_usd,
        "variable_usd": r.variable_usd,
        "subtotal_usd": r.subtotal_usd,
        "margin_percent": r.margin_percent,
        "margin_usd": r.margin_usd,
        "total_usd": r.total_usd,
        "num_briefings": r.num_briefings,
        "num_users": r.num_users,
        "cost_per_briefing_usd": r.cost_per_briefing_usd,
        "cost_per_user_usd": r.cost_per_user_usd,
        "config_id": r.config_id,
    }


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
