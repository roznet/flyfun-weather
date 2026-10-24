"""Pure cost computation — no DB or I/O dependencies."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime


# --- Prompt-cache pricing ---------------------------------------------------
#
# Anthropic's published cache-write premiums, keyed by TTL.  A cache *read* is
# 0.1x on either tier.  This table is the only place a write premium is
# written down.
CACHE_WRITE_MULTIPLIERS: dict[str, float] = {"5m": 1.25, "1h": 2.0}
CACHE_READ_MULTIPLIER: float = 0.1

# The TTL the *charging* path writes with.  Single source of truth: the digest
# reads it (``digest/llm_digest.py:_system_content``) and the rate card below
# derives its write multiplier from it, so the two cannot drift.  Changing this
# to "5m" re-prices writes at 1.25x automatically; a TTL with no entry in the
# table above is rejected at the call site rather than silently mis-priced.
DIGEST_CACHE_TTL: str = "1h"


def cache_write_multiplier_for(ttl: str) -> float:
    """Write premium for a prompt-cache TTL.

    Raises ``ValueError`` for a TTL with no published premium — a new tier has
    to be priced here before anything can write with it.
    """
    try:
        return CACHE_WRITE_MULTIPLIERS[ttl]
    except KeyError:
        raise ValueError(
            f"Unpriced prompt-cache TTL {ttl!r}; "
            f"known: {', '.join(sorted(CACHE_WRITE_MULTIPLIERS))}"
        ) from None


@dataclass(frozen=True)
class CostConfig:
    """Rate card for cost computation.

    Stored as JSON in CostConfigRow.config_json.  Adding a new cost dimension
    means adding a field here, updating compute_cost(), and updating
    CostBreakdown — no DB migration required.
    """

    token_cost_per_1k_input: float = 0.003
    token_cost_per_1k_output: float = 0.015
    # Prompt-caching multipliers applied to token_cost_per_1k_input, derived
    # from the TTL the digest actually writes with — do not hardcode a number
    # here, or a TTL change silently mis-prices every write.
    cache_write_multiplier: float = CACHE_WRITE_MULTIPLIERS[DIGEST_CACHE_TTL]
    cache_read_multiplier: float = CACHE_READ_MULTIPLIER
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
    # What prompt caching saved on this briefing: the cached tokens priced at
    # 1x minus what they actually billed at their multipliers.  Informational —
    # already reflected inside token_cost_usd, never added to the subtotal.
    # Negative on a cache *write* (2x), positive once the reads come back.
    cache_saving_usd: float = 0.0


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

    ``CostBreakdown.cache_saving_usd`` reports what that split saved against
    pricing the same tokens at 1x — informational only, already inside
    ``token_cost_usd``.
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
    # What caching saved: the same cached tokens priced at 1x, minus what they
    # actually billed.  Uses the reported counts (not the clamped ones) so it
    # stays the exact complement of the two cache terms above.
    cache_saving = (
        (cache_read_tokens / 1000)
        * config.token_cost_per_1k_input
        * (1 - config.cache_read_multiplier)
        + (cache_write_tokens / 1000)
        * config.token_cost_per_1k_input
        * (1 - config.cache_write_multiplier)
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
        cache_saving_usd=round(cache_saving, 6),
    )


# --- Side calls, priced at their own model's rate ----------------------------
#
# USD per 1k tokens ``(input, output)``, Anthropic first-party list price.
# ``CostConfig`` carries ONE flat token rate (the digest model's) plus the
# per-briefing fixed-cost shares, so a side call routed through ``compute_cost``
# was billed like a briefing: the Haiku trip paragraph came out at ~$0.62, 98%
# of it amortised droplet/subscription share and margin, for a call that costs
# a fraction of a cent. A model must be priced here before anything bills it.
MODEL_TOKEN_RATES_PER_1K: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (0.001, 0.005),
    # Prompts of 100k input tokens or fewer; longer ones take the tier in
    # MODEL_LONG_PROMPT_RATES_PER_1K (#715).
    "claude-haiku-5-5": (0.0001, 0.0005),
    "claude-sonnet-4-6": (0.003, 0.015),
    # Thinking is billed as output, and the API reports it inside
    # ``usage.output_tokens`` (there is no separate thinking count), so the
    # output rate prices a 5.x digest with adaptive thinking in full (#717).
    "claude-sonnet-5-5": (0.002, 0.010),
}

# Models whose whole call bills at a higher rate once the prompt exceeds a
# size: ``{family: (input-token threshold, (input, output) USD per 1k)}``.
# Priced from the call's own ``input_tokens`` (cache reads and writes are
# subsets of it), so a long-prompt caller is never billed at the short rate
# silently (review on PR #716).
MODEL_LONG_PROMPT_RATES_PER_1K: dict[str, tuple[int, tuple[float, float]]] = {
    "claude-haiku-5-5": (100_000, (0.0005, 0.0025)),
}


def token_rates_for(model: str) -> tuple[float, float]:
    """``(input, output)`` USD per 1k tokens for a model id.

    Accepts a dated snapshot (``claude-haiku-4-5-20251001``) and a
    ``provider:`` prefix. Raises ``ValueError`` for an unpriced model — the same
    stance as ``cache_write_multiplier_for``: price it here, never guess.
    """
    name = model.split(":", 1)[-1]
    for family, rates in MODEL_TOKEN_RATES_PER_1K.items():
        if name == family or name.startswith(family + "-"):
            return rates
    raise ValueError(
        f"Unpriced model {model!r}; known: {', '.join(sorted(MODEL_TOKEN_RATES_PER_1K))}"
    )


def compute_call_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_ttl: str = "5m",
) -> float:
    """Token cost in USD of one LLM call that is not a briefing.

    Tokens only — no infra or subscription share and no margin. Those amortise
    the fixed costs over *briefings* (``compute_cost``); a side call adds none
    of them, and charging them per call is what billed a sub-cent paragraph at
    more than the briefing it summarises. Cache tokens are subsets of
    ``input_tokens``, re-priced at their multipliers exactly as in
    ``compute_cost``.
    """
    in_rate, out_rate = token_rates_for(model)
    name = model.split(":", 1)[-1]
    for family, (threshold, rates) in MODEL_LONG_PROMPT_RATES_PER_1K.items():
        if (name == family or name.startswith(family + "-")) and input_tokens > threshold:
            in_rate, out_rate = rates
    cached = min(cache_read_tokens + cache_write_tokens, input_tokens)
    cost = (
        ((input_tokens - cached) / 1000) * in_rate
        + (cache_read_tokens / 1000) * in_rate * CACHE_READ_MULTIPLIER
        + (cache_write_tokens / 1000) * in_rate * cache_write_multiplier_for(cache_ttl)
        + (output_tokens / 1000) * out_rate
    )
    return round(cost, 6)


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
    # What prompt caching saved over the window, summed from the same ledger
    # rows.  ``None`` when no row in the window carries the figure at all
    # (every briefing predates the field) — distinct from 0.0, which means
    # "measured, and nothing was cached".
    cache_saving_usd: float | None
    subtotal_usd: float  # fixed_prorated + variable
    margin_percent: float
    margin_usd: float
    total_usd: float
    num_briefings: int
    num_users: int
    cost_per_briefing_usd: float
    cost_per_user_usd: float
    config_id: int


#: Labels of the fixed lines that always exist; the cost table orders its
#: rows by them (server first, misc last).
SERVER_LINE = "Server (droplet)"
MISC_LINE = "Misc"


def fixed_monthly_lines(config: CostConfig) -> list[tuple[str, float]]:
    """The rate card's fixed lines as ``(label, monthly USD)``, display order."""
    lines = [(SERVER_LINE, config.droplet_monthly_usd)]
    details = config.subscription_details or {}
    if details:
        lines.extend((f"Subscription: {name}", float(m)) for name, m in details.items())
    elif config.subscriptions_monthly_usd:
        lines.append(("Subscriptions", config.subscriptions_monthly_usd))
    lines.append((MISC_LINE, config.misc_monthly_usd))
    return lines


def compute_program_cost(
    config: CostConfig,
    config_id: int,
    window_days: int,
    variable_token_usd: float,
    variable_storage_usd: float,
    num_briefings: int,
    num_users: int,
    cache_saving_usd: float | None = None,
) -> ProgramCostReport:
    """Compute a program-wide cost report for a time window.

    Fixed costs (droplet, misc, each subscription line) are prorated to the
    window.  Variable costs are the actual token/storage totals from the
    ledger.  Margin is applied to the combined subtotal so the rate card stays
    internally consistent.  Per-briefing/per-user figures are guarded for zero.

    ``cache_saving_usd`` is reporting-only — it is *already* inside
    ``variable_token_usd`` and must never be added to or subtracted from any
    total.  ``None`` means no ledger row in the window recorded it.
    """
    months = window_days / _DAYS_PER_MONTH

    def _line(label: str, monthly: float) -> FixedCostLine:
        return FixedCostLine(
            label=label,
            monthly_usd=round(monthly, 4),
            prorated_usd=round(monthly * months, 4),
        )

    lines = [_line(label, monthly) for label, monthly in fixed_monthly_lines(config)]

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
        cache_saving_usd=(
            None if cache_saving_usd is None else round(cache_saving_usd, 4)
        ),
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
        "cache_saving_usd": r.cache_saving_usd,
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


# --- Admin cost table (7d / 30d / 365d side by side) ------------------------


def prorate_fixed(
    versions: Sequence[tuple[CostConfig, datetime, datetime | None]],
    start: datetime,
    end: datetime,
) -> dict[str, float]:
    """Fixed cost per line over ``[start, end)``, version by version.

    Each rate-card version ``(config, active_from, active_until)`` is charged
    only for the part of the window it was active, so a year-long window does
    not price the months before a price change at today's card.  Time before
    the first version is not charged at all.
    """
    out: dict[str, float] = {}
    for config, active_from, active_until in versions:
        lo = max(start, active_from)
        hi = min(end, active_until or end)
        if hi <= lo:
            continue
        months = (hi - lo).total_seconds() / 86400 / _DAYS_PER_MONTH
        for label, monthly in fixed_monthly_lines(config):
            out[label] = out.get(label, 0.0) + monthly * months
    return out


def rate_card_coverage(
    versions: Sequence[tuple[CostConfig, datetime, datetime | None]],
    start: datetime,
    end: datetime,
) -> tuple[datetime | None, float]:
    """When rate-card coverage of ``[start, end)`` begins, and how many days it covers.

    Same overlap rule as ``prorate_fixed``, so a window reaching before the
    first card *or* across a gap between versions reads as partial: fixed cost
    is only charged for the days counted here.
    """
    first: datetime | None = None
    seconds = 0.0
    for _config, active_from, active_until in versions:
        lo = max(start, active_from)
        hi = min(end, active_until or end)
        if hi <= lo:
            continue
        first = lo if first is None else min(first, lo)
        seconds += (hi - lo).total_seconds()
    return first, seconds / 86400


def briefing_ledger_figures(detail_json: str | None) -> tuple[float, float, float | None]:
    """``(token, storage, cache_saving)`` USD from a briefing ledger row's detail.

    Tolerant of rows that are empty, not JSON, not an object, or carry a null
    figure: one bad row reads as zero rather than failing a whole report.
    ``cache_saving`` stays ``None`` when the row does not record it.
    """
    try:
        bd = json.loads(detail_json) if detail_json else {}
    except (json.JSONDecodeError, TypeError):
        bd = {}
    if not isinstance(bd, dict):
        bd = {}

    def _num(key: str) -> float | None:
        try:
            return None if bd.get(key) is None else float(bd[key])
        except (TypeError, ValueError):
            return None

    return _num("token_cost_usd") or 0.0, _num("storage_cost_usd") or 0.0, _num("cache_saving_usd")


@dataclass(frozen=True)
class CostWindowInputs:
    """What one column of the admin cost table is computed from."""

    key: str  # "7d", "30d", "365d"
    days: int
    covered_from: datetime | None  # ``rate_card_coverage``
    covered_days: float  # days of the window some rate card was active
    fixed: dict[str, float]  # label → prorated USD (``prorate_fixed``)
    briefing_token_usd: float
    storage_usd: float
    other_llm_usd: dict[str, float]  # ledger category → USD
    cache_saving_usd: float | None  # None: no row in the window records it
    num_briefings: int
    num_users: int
    num_signups: int


_OTHER_LLM_LABELS = {
    "live_highlight": "LLM – live highlight",
    "trip_summary": "LLM – trip summary",
}


def _fixed_sort_key(label: str) -> int:
    # Server first, Misc last, subscriptions in between in first-seen order.
    return 0 if label == SERVER_LINE else 2 if label == MISC_LINE else 1


def build_cost_table(windows: Sequence[CostWindowInputs], margin_percent: float) -> dict:
    """Assemble the admin cost table: one value per window on every row.

    Sections carry their own subtotal (``values``) so the header row reads as
    the total of its lines.  "Extra" is the rate card's margin percent, applied
    to everything else in the table (fixed + all variable, including the
    per-call LLM features), and sits inside Variable.
    """
    n = len(windows)

    def _row(label: str, values: list[float | None], key: str | None = None) -> dict:
        row = {"label": label, "values": [None if v is None else round(v, 4) for v in values]}
        if key:
            row["key"] = key
        return row

    fixed_labels: list[str] = []
    for w in windows:
        fixed_labels.extend(k for k in w.fixed if k not in fixed_labels)
    fixed_labels.sort(key=_fixed_sort_key)
    fixed_rows = [_row(k, [w.fixed.get(k, 0.0) for w in windows]) for k in fixed_labels]
    fixed_tot = [sum(w.fixed.values()) for w in windows]

    # Other LLM categories, heaviest over the widest window first.
    widest = max(windows, key=lambda w: w.days) if windows else None
    cats: list[str] = []
    for w in windows:
        cats.extend(c for c in w.other_llm_usd if c not in cats)
    if widest is not None:
        cats.sort(key=lambda c: -widest.other_llm_usd.get(c, 0.0))

    pre_extra = [
        fixed_tot[i] + w.briefing_token_usd + w.storage_usd + sum(w.other_llm_usd.values())
        for i, w in enumerate(windows)
    ]
    extra = [p * margin_percent / 100 for p in pre_extra]
    variable_rows = [
        # Keyed: the page hangs the cache-saving aside under this row.
        _row("LLM tokens – briefings", [w.briefing_token_usd for w in windows], "briefing_tokens"),
        *(
            _row(
                _OTHER_LLM_LABELS.get(c, f"LLM – {c.replace('_', ' ')}"),
                [w.other_llm_usd.get(c, 0.0) for w in windows],
            )
            for c in cats
        ),
        _row("Disk / storage", [w.storage_usd for w in windows]),
        _row(f"Extra ({margin_percent:g}%)", extra),
    ]
    variable_tot = [pre_extra[i] - fixed_tot[i] + extra[i] for i in range(n)]
    total = [fixed_tot[i] + variable_tot[i] for i in range(n)]

    def _per(i: int, count: int) -> float | None:
        return total[i] / count if count else None

    return {
        "columns": [
            {
                "key": w.key,
                "days": w.days,
                "covered_days": round(w.covered_days, 1),
                "covered_from": w.covered_from.isoformat() if w.covered_from else None,
            }
            for w in windows
        ],
        "margin_percent": margin_percent,
        "sections": [
            {**_row("Fixed", fixed_tot), "rows": fixed_rows},
            {**_row("Variable", variable_tot), "rows": variable_rows},
        ],
        "total": [round(t, 4) for t in total],
        # Reporting-only: already inside the briefing token line.
        "cache_saving_usd": [
            None if w.cache_saving_usd is None else round(w.cache_saving_usd, 4)
            for w in windows
        ],
        "usage": [
            {"kind": "count", **_row("Active users (made a briefing)", [w.num_users for w in windows])},
            {"kind": "count", **_row("Briefings", [w.num_briefings for w in windows])},
            {"kind": "count", **_row("New sign-ups", [w.num_signups for w in windows])},
            {"kind": "usd", **_row("Cost / briefing", [_per(i, w.num_briefings) for i, w in enumerate(windows)])},
            {"kind": "usd", **_row("Cost / active user", [_per(i, w.num_users) for i, w in enumerate(windows)])},
        ],
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
        # Additive: rows written before this field existed simply lack the key.
        # Readers must treat "absent" as unknown, not as a real zero.
        "cache_saving_usd": b.cache_saving_usd,
    }
