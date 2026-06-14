"""Tests for the donation impact math (pure, margin-excluded coverage)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.costs import compute_program_cost, CostConfig
from weatherbrief.impact import (
    DonationImpact,
    ProgramEconomics,
    YearlyImpact,
    donation_impact,
    economics_from_report,
    format_user_coverage,
    format_yearly_coverage,
    impact_to_dict,
    yearly_impact,
    yearly_to_dict,
)

# Mid-year reference so "until end of year" / "elapsed this year" are non-trivial.
NOW = datetime(2026, 7, 2, tzinfo=timezone.utc)  # 183 days in, 183 left


def _report(*, num_users: int, window_days: int = 30, variable_usd: float = 0.0):
    """Build a program cost report with a known fixed monthly cost.

    Default config: droplet 24 + misc 2 + subscriptions 30 = $56/month fixed.
    """
    cfg = CostConfig(subscription_details={"open_meteo": 30})
    # Split the requested variable evenly across token/storage; the split is
    # irrelevant to economics, only the sum matters.
    return compute_program_cost(
        config=cfg,
        config_id=1,
        window_days=window_days,
        variable_token_usd=variable_usd,
        variable_storage_usd=0.0,
        num_briefings=num_users * 3,
        num_users=num_users,
    )


class TestEconomicsFromReport:
    def test_fixed_only_30d_window(self):
        econ = economics_from_report(_report(num_users=10))
        assert econ.monthly_run_cost_usd == pytest.approx(56.0)
        assert econ.active_users == 10
        assert econ.cost_per_user_month_usd == pytest.approx(5.6)
        assert econ.available

    def test_variable_scaled_to_month(self):
        # 7-day window, $14 variable → $60/month variable; +56 fixed = 116.
        econ = economics_from_report(_report(num_users=4, window_days=7, variable_usd=14.0))
        assert econ.monthly_run_cost_usd == pytest.approx(56.0 + 60.0)
        assert econ.cost_per_user_month_usd == pytest.approx(116.0 / 4)

    def test_no_users_is_unavailable(self):
        econ = economics_from_report(_report(num_users=0))
        assert econ.active_users == 0
        assert econ.cost_per_user_month_usd == 0.0
        assert not econ.available


class TestDonationImpact:
    def test_basic_coverage(self):
        econ = economics_from_report(_report(num_users=10))  # cpum = 5.6
        impact = donation_impact(56.0, econ, now=NOW)
        assert not impact.empty
        assert impact.user_months == pytest.approx(10.0)  # 56 / 5.6
        # ~6 months left in the year → covers ~10/6 users until EOY
        assert impact.users_until_eoy == pytest.approx(
            56.0 / (5.6 * impact.months_until_eoy), abs=1e-3
        )

    def test_empty_when_no_economics(self):
        econ = ProgramEconomics(monthly_run_cost_usd=0.0, active_users=0, cost_per_user_month_usd=0.0)
        impact = donation_impact(50.0, econ, now=NOW)
        assert impact.empty
        assert impact.user_months == 0.0
        assert format_user_coverage(impact) == ""

    def test_empty_when_zero_amount(self):
        econ = economics_from_report(_report(num_users=10))
        impact = donation_impact(0.0, econ, now=NOW)
        assert impact.empty


class TestYearlyImpact:
    def test_basic(self):
        econ = economics_from_report(_report(num_users=10))  # monthly 56, cpum 5.6
        yi = yearly_impact(112.0, econ, now=NOW)
        assert not yi.empty
        assert yi.months_covered == pytest.approx(2.0)  # 112 / 56
        assert yi.users_full_year == pytest.approx(112.0 / (5.6 * 12), abs=1e-3)
        # coverage_ratio vs months elapsed (~6)
        assert yi.coverage_ratio == pytest.approx(
            112.0 / (56.0 * yi.months_elapsed), abs=1e-3
        )

    def test_empty_when_no_donations(self):
        econ = economics_from_report(_report(num_users=10))
        yi = yearly_impact(0.0, econ, now=NOW)
        assert yi.empty
        assert format_yearly_coverage(yi) == ""


class TestPhrasing:
    def test_user_years_for_large(self):
        impact = DonationImpact(amount_usd=200, user_months=24, users_until_eoy=4,
                                months_until_eoy=6, empty=False)
        assert format_user_coverage(impact) == "covers one user for ~2.0 years"

    def test_user_months_midrange(self):
        impact = DonationImpact(amount_usd=50, user_months=8, users_until_eoy=1,
                                months_until_eoy=6, empty=False)
        assert format_user_coverage(impact) == "covers one user for ~8 months"

    def test_user_one_month(self):
        impact = DonationImpact(amount_usd=6, user_months=1.0, users_until_eoy=0.1,
                                months_until_eoy=6, empty=False)
        assert format_user_coverage(impact) == "covers one user for ~1 month"

    def test_user_small_amount_non_zero(self):
        impact = DonationImpact(amount_usd=1, user_months=0.2, users_until_eoy=0.0,
                                months_until_eoy=6, empty=False)
        assert format_user_coverage(impact) == "covers part of a user's monthly cost"

    def test_yearly_years(self):
        yi = YearlyImpact(total_year_usd=900, months_covered=18, users_full_year=3,
                          coverage_ratio=3, months_elapsed=6, empty=False)
        assert format_yearly_coverage(yi) == "this year's donations cover ~1.5 years of running costs"

    def test_yearly_months(self):
        yi = YearlyImpact(total_year_usd=112, months_covered=2.0, users_full_year=1,
                          coverage_ratio=0.3, months_elapsed=6, empty=False)
        assert format_yearly_coverage(yi) == "this year's donations cover ~2.0 months of running costs"


class TestSerialization:
    def test_impact_to_dict_has_summary(self):
        econ = economics_from_report(_report(num_users=10))
        d = impact_to_dict(donation_impact(56.0, econ, now=NOW))
        assert d["user_months"] == pytest.approx(10.0)
        assert "summary" in d and d["summary"]

    def test_yearly_to_dict_has_summary(self):
        econ = economics_from_report(_report(num_users=10))
        d = yearly_to_dict(yearly_impact(112.0, econ, now=NOW))
        assert d["months_covered"] == pytest.approx(2.0)
        assert "summary" in d
