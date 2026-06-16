"""Tests for the donation impact math (pure, margin-excluded coverage)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.costs import compute_program_cost, CostConfig
from weatherbrief.impact import (
    DonationImpact,
    ProgramEconomics,
    YearlyImpact,
    TRANSLATION_BRIEFINGS,
    TRANSLATION_PERSONAL_MONTHS,
    TRANSLATION_SERVICE_MONTHS,
    TRANSLATION_USER_MONTHS,
    TRANSLATION_USERS_FOR_MONTH,
    choose_translation,
    donation_impact,
    economics_from_report,
    format_personal_coverage,
    format_user_coverage,
    format_words_written,
    format_yearly_coverage,
    impact_to_dict,
    personal_impact,
    personal_to_dict,
    tokens_to_words,
    words_to_books,
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

    def test_yearly_retrospective_percent(self):
        # Below full coverage → retrospective "offset ~N%".
        yi = YearlyImpact(total_year_usd=112, months_covered=2.0, users_full_year=1,
                          coverage_ratio=0.62, months_elapsed=6, surplus_months=0.0, empty=False)
        assert format_yearly_coverage(yi) == (
            "this year's donations have offset ~62% of the running costs so far"
        )

    def test_yearly_fully_covered_with_overflow(self):
        # coverage_ratio >= 1.0 unlocks forward framing.
        yi = YearlyImpact(total_year_usd=900, months_covered=9, users_full_year=3,
                          coverage_ratio=1.5, months_elapsed=6, surplus_months=3.0, empty=False)
        assert format_yearly_coverage(yi) == (
            "this year's costs are fully covered, plus ~3 months ahead"
        )

    def test_yearly_fully_covered_no_overflow(self):
        yi = YearlyImpact(total_year_usd=340, months_covered=6.2, users_full_year=1,
                          coverage_ratio=1.03, months_elapsed=6, surplus_months=0.2, empty=False)
        assert format_yearly_coverage(yi) == "this year's costs are fully covered"


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
        assert "summary" in d and "surplus_months" in d


# Default config: droplet 24 + misc 2 + subscriptions 30 = $56/month fixed.
# With num_users=10 → cpum = $5.60; num_briefings = 30 → cost/briefing ≈ $1.867.


class TestTokensToWords:
    def test_output_tokens_only_075_per_token(self):
        assert tokens_to_words(1000) == 750
        assert tokens_to_words(0) == 0
        assert tokens_to_words(-5) == 0  # never negative

    def test_books_equivalence(self):
        # 1.8M words ≈ 20 novels at ~90k words each.
        assert words_to_books(1_800_000) == pytest.approx(20.0)
        assert words_to_books(0) == 0.0

    def test_words_summary_phrasing(self):
        assert "million words" in format_words_written(3_000_000)  # 2.25M words
        assert format_words_written(0) == ""
        # Mid-range rounds to thousands.
        s = format_words_written(20_000)  # 15,000 words
        assert "words of AI weather analysis" in s


class TestEconomicsCostPerBriefing:
    def test_margin_excluded_per_briefing(self):
        # $56/month over 30 briefings/30d window → ~$1.867 per briefing.
        econ = economics_from_report(_report(num_users=10))  # num_briefings = 30
        assert econ.cost_per_briefing_usd == pytest.approx(56.0 / 30.0, abs=1e-3)

    def test_zero_when_no_briefings(self):
        econ = economics_from_report(_report(num_users=0))
        assert econ.cost_per_briefing_usd == 0.0


class TestPersonalImpact:
    def _econ(self):
        return economics_from_report(_report(num_users=10))  # cpum 5.6, monthly 56

    def test_retrospective_below_full_coverage(self):
        # Lifetime cost $10, donated $5 → 50% covered, retrospective band.
        pi = personal_impact(5.0, 10.0, 2.0, self._econ(), site_covered=False)
        assert pi.band == "retrospective"
        assert pi.extra_pilots == 0
        assert pi.coverage_ratio == pytest.approx(0.5)
        # $5 / $2/mo burn = 2.5 months → "~2 months of your own usage so far"
        assert "your own usage so far" in format_personal_coverage(pi)

    def test_retrospective_percent_fallback_when_burn_thin(self):
        # Burn rate too small to round to a whole month → percent phrasing.
        pi = personal_impact(3.0, 10.0, 0.0, self._econ(), site_covered=False)
        assert pi.band == "retrospective"
        assert "% of what your usage has cost" in format_personal_coverage(pi)

    def test_covers_others_band_rounds_min_one(self):
        # Donated $20, lifetime cost $2 → surplus $18; cpum 5.6 → ~3 pilots.
        pi = personal_impact(20.0, 2.0, 1.0, self._econ(), site_covered=False)
        assert pi.band == "covers_others"
        assert pi.extra_pilots == round(18.0 / 5.6)
        assert pi.future_months == 0.0
        assert not pi.overflow_capped
        assert "other pilots" in format_personal_coverage(pi)

    def test_covers_others_caps_to_service_months_past_active_base(self):
        # Surplus would name ≥ active_users (10) pilots → cap to whole-service
        # months so we never claim more pilots than exist. $60 - $2 = $58 surplus,
        # cpum 5.6 → ~10 pilots ≥ 10 active; $58 / $56 ≈ 1 month of the service.
        pi = personal_impact(60.0, 2.0, 1.0, self._econ(), site_covered=False)
        assert pi.band == "covers_others"
        assert pi.overflow_capped
        s = format_personal_coverage(pi)
        assert "running the whole service" in s
        assert "pilot" not in s
        assert "~1 month " in s  # singular, never "~1 months"

    def test_retrospective_caps_at_a_year(self):
        # cr < 1 but >12 months of own usage → "over a year", not "~25 months".
        pi = personal_impact(50.0, 100.0, 2.0, self._econ(), site_covered=False)
        assert pi.band == "retrospective"
        assert format_personal_coverage(pi) == "covers over a year of your own usage so far"

    def test_covers_others_min_one_when_surplus_tiny(self):
        # Surplus rounds to 0 → bumped to "another pilot" (min 1, never a fraction).
        pi = personal_impact(2.5, 2.0, 1.0, self._econ(), site_covered=False)
        assert pi.band == "covers_others"
        assert pi.extra_pilots == 1
        assert "1 other pilot" in format_personal_coverage(pi)

    def test_future_band_only_when_site_covered(self):
        # Same over-coverage, but the whole site is covered → forward framing.
        pi = personal_impact(120.0, 2.0, 1.0, self._econ(), site_covered=True)
        assert pi.band == "future"
        assert pi.future_months > 0
        assert "toward the service ahead" in format_personal_coverage(pi)

    def test_no_history_treated_as_fully_covered(self):
        # Brand-new donor: lifetime cost 0 → surplus = full donation, covers_others.
        pi = personal_impact(20.0, 0.0, 0.0, self._econ(), site_covered=False)
        assert pi.band == "covers_others"
        assert pi.extra_pilots >= 1

    def test_no_history_coverage_ratio_is_none(self):
        # Ratio is donation ÷ 0 → exposed as None, not a sentinel/percentage.
        pi = personal_impact(20.0, 0.0, 0.0, self._econ(), site_covered=False)
        assert pi.coverage_ratio is None
        assert personal_to_dict(pi)["coverage_ratio"] is None

    def test_empty_when_no_economics(self):
        econ = ProgramEconomics(monthly_run_cost_usd=0.0, active_users=0,
                                cost_per_user_month_usd=0.0)
        pi = personal_impact(20.0, 5.0, 2.0, econ, site_covered=False)
        assert pi.empty
        assert format_personal_coverage(pi) == ""

    def test_empty_when_zero_donation(self):
        pi = personal_impact(0.0, 5.0, 2.0, self._econ(), site_covered=False)
        assert pi.empty

    def test_serialization_has_summary(self):
        d = personal_to_dict(personal_impact(5.0, 10.0, 2.0, self._econ(), site_covered=False))
        assert d["band"] == "retrospective"
        assert "summary" in d


class TestAdaptiveLadder:
    def _econ(self):
        return economics_from_report(_report(num_users=10))  # cpum 5.6, cpb ~1.867

    def test_small_uses_personal_when_history(self):
        # $20 at $5/mo personal burn → 4 months of your own usage.
        tc = choose_translation(20.0, self._econ(), burn_rate_monthly_usd=5.0)
        assert tc.kind == TRANSLATION_PERSONAL_MONTHS
        assert "your own usage" in tc.summary
        assert round(tc.value) == 4

    def test_small_falls_back_to_program_average(self):
        # No burn rate → one-pilot-for-N-months. $20 / 5.6 ≈ 3.6 months.
        tc = choose_translation(20.0, self._econ())
        assert tc.kind == TRANSLATION_USER_MONTHS
        assert "one pilot for" in tc.summary

    def test_medium_uses_pilots_for_a_month(self):
        # $50 / 5.6 ≈ 9 pilots for a month (stays under the 10-pilot active base).
        tc = choose_translation(50.0, self._econ())
        assert tc.kind == TRANSLATION_USERS_FOR_MONTH
        assert "pilots for a month" in tc.summary
        assert round(tc.value) == round(50.0 / 5.6)

    def test_medium_caps_pilots_to_service_when_over_active_base(self):
        # $100 / 5.6 ≈ 18 pilots > 10 active → switch to whole-service months
        # rather than claim more pilots than exist.
        tc = choose_translation(100.0, self._econ())
        assert tc.kind == TRANSLATION_SERVICE_MONTHS
        assert "running the whole service" in tc.summary
        assert "pilots for a month" not in tc.summary

    def test_small_personal_months_cap_at_year_plus_pilots(self):
        # Tiny personal burn ($0.25/mo) → $20 covers >1yr of own usage → cap at
        # "1 year of your own usage" and spill the rest into pilots helped.
        tc = choose_translation(20.0, self._econ(), burn_rate_monthly_usd=0.25)
        assert tc.kind == TRANSLATION_PERSONAL_MONTHS
        assert tc.summary.startswith("covers ~1 year of your own usage + ~")
        assert "other pilot" in tc.summary

    def test_large_uses_service_months(self):
        # $200 / $56/mo ≈ 3.6 months of the whole service.
        tc = choose_translation(200.0, self._econ())
        assert tc.kind == TRANSLATION_SERVICE_MONTHS
        assert "running the whole service" in tc.summary

    def test_never_shows_zero(self):
        # Every band's chosen value rounds to a non-zero, readable number.
        econ = self._econ()
        for amount in (5, 10, 25, 50, 100, 150, 250, 1000):
            tc = choose_translation(float(amount), econ)
            assert not tc.empty
            assert tc.summary and "~0 " not in tc.summary

    def test_empty_when_no_economics(self):
        econ = ProgramEconomics(monthly_run_cost_usd=0.0, active_users=0,
                                cost_per_user_month_usd=0.0)
        tc = choose_translation(20.0, econ)
        assert tc.empty and tc.summary == ""

    def test_large_band_fallback_singular_pilot(self):
        # Very high per-pilot economics: a big amount is sub-month for the whole
        # service AND ~1 pilot-month → must read "1 pilot", never "1 pilots".
        econ = ProgramEconomics(monthly_run_cost_usd=100_000.0, active_users=1,
                                cost_per_user_month_usd=200.0, cost_per_briefing_usd=50.0)
        tc = choose_translation(200.0, econ)  # service_months tiny; user_months = 1
        assert tc.kind == TRANSLATION_USERS_FOR_MONTH
        assert "~1 pilot for a month" in tc.summary

    def test_briefings_kind_for_tiny_amount(self):
        # Below a user-month but ≥2 briefings → "funds ~N briefings".
        tc = choose_translation(5.0, self._econ())  # 5/5.6 < 1.5 months; 5/1.867 ≈ 2.7 briefings
        assert tc.kind == TRANSLATION_BRIEFINGS
        assert "briefings" in tc.summary


class TestYearlyOverflow:
    def test_surplus_months_zero_below_coverage(self):
        econ = economics_from_report(_report(num_users=10))  # monthly 56
        yi = yearly_impact(112.0, econ, now=NOW)  # 2 months covered, ~6 elapsed
        assert yi.coverage_ratio < 1.0
        assert yi.surplus_months == 0.0

    def test_surplus_months_positive_when_over_covered(self):
        econ = economics_from_report(_report(num_users=10))  # monthly 56
        # 12 months covered vs ~6 elapsed → ~6 months ahead.
        yi = yearly_impact(56.0 * 12, econ, now=NOW)
        assert yi.coverage_ratio >= 1.0
        assert yi.surplus_months == pytest.approx(12 - yi.months_elapsed, abs=1e-3)
