"""Donation impact math — pure, no DB or I/O dependencies.

Donors and the community see *coverage* ("covers 1 user for ~8 months"), never
"you gave $X". The run-cost used here **excludes margin** — donations offset the
operator's real cost, not the buffer. Inputs come from the program cost report
(:mod:`weatherbrief.costs`); this module turns them into frozen dataclasses plus
a thin, i18n-friendly phrasing layer.

All money is USD-canonical. The frontend renders the viewer's currency from the
``fx`` block carried on the API response; this module never touches FX.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from weatherbrief.costs import ProgramCostReport

_DAYS_PER_MONTH = 30.0
# Below this, "covers one user for ~N years" reads more naturally than months.
_YEARS_THRESHOLD_MONTHS = 18.0


@dataclass(frozen=True)
class ProgramEconomics:
    """The denominators impact framing needs, derived from the cost report.

    ``monthly_run_cost_usd`` is the operator's real monthly cost with **margin
    excluded**. ``cost_per_user_month_usd`` is ``0.0`` when there are no active
    users (the impact layer treats that as a neutral empty state).
    """

    monthly_run_cost_usd: float
    active_users: int
    cost_per_user_month_usd: float

    @property
    def available(self) -> bool:
        """True when we can compute meaningful coverage numbers."""
        return self.active_users > 0 and self.cost_per_user_month_usd > 0


def economics_from_report(report: ProgramCostReport) -> ProgramEconomics:
    """Derive margin-excluded run-cost economics from a program cost report.

    Fixed cost is already monthly; variable cost is over the report window, so it
    is scaled to a 30-day month. Margin is intentionally dropped.
    """
    variable_monthly = (
        report.variable_usd * (_DAYS_PER_MONTH / report.window_days)
        if report.window_days
        else 0.0
    )
    monthly = report.fixed_monthly_usd + variable_monthly
    users = report.num_users
    cpum = monthly / users if users > 0 else 0.0
    return ProgramEconomics(
        monthly_run_cost_usd=round(monthly, 4),
        active_users=users,
        cost_per_user_month_usd=round(cpum, 6),
    )


def _months_until_year_end(now: datetime) -> float:
    """Whole-ish months from ``now`` to Dec 31, floored at a small epsilon."""
    year_end = datetime(now.year + 1, 1, 1, tzinfo=now.tzinfo)
    days = (year_end - now).total_seconds() / 86400.0
    return max(days / _DAYS_PER_MONTH, 0.1)


def _months_elapsed_this_year(now: datetime) -> float:
    """Whole-ish months from Jan 1 to ``now``, floored at a small epsilon."""
    year_start = datetime(now.year, 1, 1, tzinfo=now.tzinfo)
    days = (now - year_start).total_seconds() / 86400.0
    return max(days / _DAYS_PER_MONTH, 0.1)


@dataclass(frozen=True)
class DonationImpact:
    """Coverage a single donation (already in USD) buys. ``empty`` ⇒ neutral."""

    amount_usd: float
    user_months: float
    users_until_eoy: float
    months_until_eoy: float
    empty: bool


def donation_impact(
    amount_usd: float, economics: ProgramEconomics, *, now: datetime
) -> DonationImpact:
    """Coverage for ``amount_usd`` given program ``economics``.

    Returns a neutral empty impact when economics are unavailable (no active
    users yet) or the amount is non-positive.
    """
    if not economics.available or amount_usd <= 0:
        return DonationImpact(
            amount_usd=round(max(amount_usd, 0.0), 2),
            user_months=0.0,
            users_until_eoy=0.0,
            months_until_eoy=_months_until_year_end(now),
            empty=True,
        )
    cpum = economics.cost_per_user_month_usd
    months_left = _months_until_year_end(now)
    return DonationImpact(
        amount_usd=round(amount_usd, 2),
        user_months=round(amount_usd / cpum, 4),
        users_until_eoy=round(amount_usd / (cpum * months_left), 4),
        months_until_eoy=round(months_left, 4),
        empty=False,
    )


@dataclass(frozen=True)
class YearlyImpact:
    """Coverage this calendar year's community total buys. ``empty`` ⇒ neutral."""

    total_year_usd: float
    months_covered: float
    users_full_year: float
    coverage_ratio: float
    months_elapsed: float
    empty: bool


def yearly_impact(
    total_year_usd: float, economics: ProgramEconomics, *, now: datetime
) -> YearlyImpact:
    """Community coverage for this year's donation total given ``economics``."""
    months_elapsed = _months_elapsed_this_year(now)
    if not economics.available or total_year_usd <= 0:
        return YearlyImpact(
            total_year_usd=round(max(total_year_usd, 0.0), 2),
            months_covered=0.0,
            users_full_year=0.0,
            coverage_ratio=0.0,
            months_elapsed=round(months_elapsed, 4),
            empty=True,
        )
    monthly = economics.monthly_run_cost_usd
    cpum = economics.cost_per_user_month_usd
    return YearlyImpact(
        total_year_usd=round(total_year_usd, 2),
        months_covered=round(total_year_usd / monthly, 4),
        users_full_year=round(total_year_usd / (cpum * 12), 4),
        coverage_ratio=round(total_year_usd / (monthly * months_elapsed), 4),
        months_elapsed=round(months_elapsed, 4),
        empty=False,
    )


# ---------------------------------------------------------------------------
# Phrasing (thin, English default — frontend may localize from the raw numbers)
# ---------------------------------------------------------------------------


def format_user_coverage(impact: DonationImpact) -> str:
    """Natural-language coverage for one donation. ``""`` for the empty state.

    Picks the most natural unit and never shows "0": years for large donations,
    months otherwise, and an honest "part of a user's monthly cost" for amounts
    below a single user-month.
    """
    if impact.empty:
        return ""
    m = impact.user_months
    if m >= _YEARS_THRESHOLD_MONTHS:
        return f"covers one user for ~{m / 12:.1f} years"
    if m >= 1.5:
        return f"covers one user for ~{m:.0f} months"
    if m >= 0.95:
        return "covers one user for ~1 month"
    return "covers part of a user's monthly cost"


def format_yearly_coverage(yi: YearlyImpact) -> str:
    """Natural-language community coverage. ``""`` for the empty state."""
    if yi.empty:
        return ""
    mc = yi.months_covered
    if mc >= 12:
        return f"this year's donations cover ~{mc / 12:.1f} years of running costs"
    if mc >= 1.5:
        return f"this year's donations cover ~{mc:.1f} months of running costs"
    if mc >= 0.95:  # avoids the grammatically odd "~1.0 months"
        return "this year's donations cover ~1 month of running costs"
    return "this year's donations help cover the running costs"


def impact_to_dict(impact: DonationImpact) -> dict:
    """Serialize a DonationImpact (+ phrasing) for the API."""
    return {
        "amount_usd": impact.amount_usd,
        "user_months": impact.user_months,
        "users_until_eoy": impact.users_until_eoy,
        "months_until_eoy": impact.months_until_eoy,
        "empty": impact.empty,
        "summary": format_user_coverage(impact),
    }


def yearly_to_dict(yi: YearlyImpact) -> dict:
    """Serialize a YearlyImpact (+ phrasing) for the API."""
    return {
        "total_year_usd": yi.total_year_usd,
        "months_covered": yi.months_covered,
        "users_full_year": yi.users_full_year,
        "coverage_ratio": yi.coverage_ratio,
        "months_elapsed": yi.months_elapsed,
        "empty": yi.empty,
        "summary": format_yearly_coverage(yi),
    }
