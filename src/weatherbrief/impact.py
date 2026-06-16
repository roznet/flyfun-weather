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

# Tokens → words: an output token is ~0.75 English words. Output tokens only —
# that is what the AI *wrote* (input/total tokens are data fed in, not prose).
_WORDS_PER_TOKEN = 0.75
# Rough novel length, for the optional "~N books" flourish (clearly approximate).
_WORDS_PER_NOVEL = 90_000.0


@dataclass(frozen=True)
class ProgramEconomics:
    """The denominators impact framing needs, derived from the cost report.

    ``monthly_run_cost_usd`` is the operator's real monthly cost with **margin
    excluded**. ``cost_per_user_month_usd`` is ``0.0`` when there are no active
    users (the impact layer treats that as a neutral empty state).
    ``cost_per_briefing_usd`` is the margin-excluded per-briefing run cost, used
    for the relatable "X briefings funded" translation.
    """

    monthly_run_cost_usd: float
    active_users: int
    cost_per_user_month_usd: float
    cost_per_briefing_usd: float = 0.0

    @property
    def available(self) -> bool:
        """True when we can compute meaningful coverage numbers."""
        return self.active_users > 0 and self.cost_per_user_month_usd > 0


def economics_from_report(report: ProgramCostReport) -> ProgramEconomics:
    """Derive margin-excluded run-cost economics from a program cost report.

    Fixed cost is already monthly; variable cost is over the report window, so it
    is scaled to a 30-day month. Margin is intentionally dropped. The
    per-briefing cost is the monthly run cost divided by the window's briefing
    count scaled to a month — so it, too, excludes margin (unlike
    ``report.cost_per_briefing_usd``, which includes it).
    """
    scale = _DAYS_PER_MONTH / report.window_days if report.window_days else 0.0
    variable_monthly = report.variable_usd * scale
    monthly = report.fixed_monthly_usd + variable_monthly
    users = report.num_users
    cpum = monthly / users if users > 0 else 0.0
    briefings_per_month = report.num_briefings * scale
    cpb = monthly / briefings_per_month if briefings_per_month > 0 else 0.0
    return ProgramEconomics(
        monthly_run_cost_usd=round(monthly, 4),
        active_users=users,
        cost_per_user_month_usd=round(cpum, 6),
        cost_per_briefing_usd=round(cpb, 6),
    )


def tokens_to_words(output_tokens: int) -> int:
    """AI words written from output token count (~0.75 words/token)."""
    return int(max(output_tokens, 0) * _WORDS_PER_TOKEN)


def words_to_books(words: int) -> float:
    """Approximate novel-equivalents for a word count (~90k words/novel)."""
    return round(max(words, 0) / _WORDS_PER_NOVEL, 1)


def format_words_written(output_tokens: int) -> str:
    """Headline phrasing for the AI-analysis stat. Words is the headline; the
    book equivalence is an optional, clearly-approximate flourish appended by the
    frontend from ``words_to_books`` if it wants it."""
    words = tokens_to_words(output_tokens)
    if words <= 0:
        return ""
    if words >= 1_000_000:
        return f"{words / 1_000_000:.1f} million words of AI weather analysis"
    if words >= 10_000:
        return f"{round(words / 1_000) * 1000:,} words of AI weather analysis"
    return f"{words:,} words of AI weather analysis"


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
    """Coverage this calendar year's community total buys. ``empty`` ⇒ neutral.

    ``coverage_ratio`` ≥ 1.0 means this year's donations have offset everything
    spent so far; ``surplus_months`` is then how far *ahead* the surplus reaches
    (donations beyond cost-incurred-so-far, in months of run cost). Below 1.0 it
    is the expected retrospective case and ``surplus_months`` is 0.
    """

    total_year_usd: float
    months_covered: float
    users_full_year: float
    coverage_ratio: float
    months_elapsed: float
    surplus_months: float
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
            surplus_months=0.0,
            empty=True,
        )
    monthly = economics.monthly_run_cost_usd
    cpum = economics.cost_per_user_month_usd
    months_covered = total_year_usd / monthly
    # "Ahead" only counts donations beyond what's been spent so far this year.
    surplus_months = max(months_covered - months_elapsed, 0.0)
    return YearlyImpact(
        total_year_usd=round(total_year_usd, 2),
        months_covered=round(months_covered, 4),
        users_full_year=round(total_year_usd / (cpum * 12), 4),
        coverage_ratio=round(total_year_usd / (monthly * months_elapsed), 4),
        months_elapsed=round(months_elapsed, 4),
        surplus_months=round(surplus_months, 4),
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
    """Natural-language community coverage. ``""`` for the empty state.

    Below full coverage this is retrospective ("offset ~62% of the running costs
    so far"). Once ``coverage_ratio`` ≥ 1.0 the year is fully covered and forward
    framing unlocks: "fully covered, plus ~N months ahead."
    """
    if yi.empty:
        return ""
    if yi.coverage_ratio >= 1.0:
        ahead = round(yi.surplus_months)
        if ahead >= 1:
            return f"this year's costs are fully covered, plus ~{ahead} months ahead"
        return "this year's costs are fully covered"
    pct = round(yi.coverage_ratio * 100)
    return f"this year's donations have offset ~{pct}% of the running costs so far"


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
        "surplus_months": yi.surplus_months,
        "empty": yi.empty,
        "summary": format_yearly_coverage(yi),
    }


# ---------------------------------------------------------------------------
# Personal lifetime coverage — retrospective, with forward overflow
# ---------------------------------------------------------------------------
#
# A donation is *retrospective*: it offsets cost a pilot has already incurred,
# not a prepayment. So the personal panel is donation-total ÷ the pilot's own
# lifetime cost. The escalation ladder:
#
#   1. below 100% of own usage  → retrospective ("covers ~N months of your own
#      usage so far" / "~Y% of what your usage has cost").
#   2. at/above own usage       → surplus goes to *other pilots* first
#      ("…plus ~N other pilots", whole number, min 1) — NOT the future.
#   3. only once the whole site is covered (community coverage ≥ 1.0) does
#      forward framing unlock ("…and contributed ~N months toward the service
#      ahead").

_BAND_RETROSPECTIVE = "retrospective"
_BAND_COVERS_OTHERS = "covers_others"
_BAND_FUTURE = "future"


@dataclass(frozen=True)
class PersonalImpact:
    """A pilot's lifetime coverage. ``empty`` ⇒ neutral (no economics/donations).

    ``band`` is one of ``retrospective`` / ``covers_others`` / ``future``.
    ``own_months_covered`` is the donation total against the pilot's realized
    monthly burn rate; ``coverage_ratio`` is against their lifetime cost.
    ``extra_pilots`` (whole, ≥1 in the overflow bands) and ``future_months``
    quantify the surplus once own usage is covered.
    """

    donation_total_usd: float
    lifetime_cost_usd: float
    own_months_covered: float
    # None when the pilot has no realized cost yet (brand-new donor): the ratio
    # is undefined (donation ÷ 0), so we expose null rather than a sentinel that
    # a consumer would render as a nonsensical percentage.
    coverage_ratio: float | None
    extra_pilots: int
    future_months: float
    band: str
    empty: bool


def personal_impact(
    donation_total_usd: float,
    lifetime_cost_usd: float,
    burn_rate_monthly_usd: float,
    economics: ProgramEconomics,
    *,
    site_covered: bool,
) -> PersonalImpact:
    """Lifetime coverage for one pilot.

    ``site_covered`` is the community coverage gate (yearly ``coverage_ratio`` ≥
    1.0): forward framing only unlocks when the whole site is paid for. Returns a
    neutral empty impact when economics are unavailable or nothing was donated.
    """
    if not economics.available or donation_total_usd <= 0:
        return PersonalImpact(
            donation_total_usd=round(max(donation_total_usd, 0.0), 2),
            lifetime_cost_usd=round(max(lifetime_cost_usd, 0.0), 4),
            own_months_covered=0.0,
            coverage_ratio=0.0,
            extra_pilots=0,
            future_months=0.0,
            band=_BAND_RETROSPECTIVE,
            empty=True,
        )

    own_months = donation_total_usd / burn_rate_monthly_usd if burn_rate_monthly_usd > 0 else 0.0
    # No realized cost yet (brand-new donor) ⇒ treat as fully covered so the
    # surplus framing kicks in rather than a divide-by-zero.
    coverage_ratio = (
        donation_total_usd / lifetime_cost_usd if lifetime_cost_usd > 0 else float("inf")
    )
    surplus = max(donation_total_usd - max(lifetime_cost_usd, 0.0), 0.0)
    avg_user_cost = economics.cost_per_user_month_usd  # representative per-pilot unit

    if coverage_ratio < 1.0:
        band, extra_pilots, future_months = _BAND_RETROSPECTIVE, 0, 0.0
    elif not site_covered:
        # Round 0.x up to "plus another pilot" — never a fraction, never zero.
        extra_pilots = max(1, round(surplus / avg_user_cost)) if avg_user_cost > 0 else 1
        band, future_months = _BAND_COVERS_OTHERS, 0.0
    else:
        extra_pilots = max(1, round(surplus / avg_user_cost)) if avg_user_cost > 0 else 1
        monthly = economics.monthly_run_cost_usd
        future_months = surplus / monthly if monthly > 0 else 0.0
        band = _BAND_FUTURE

    return PersonalImpact(
        donation_total_usd=round(donation_total_usd, 2),
        lifetime_cost_usd=round(lifetime_cost_usd, 4),
        own_months_covered=round(own_months, 4),
        coverage_ratio=round(coverage_ratio, 4) if coverage_ratio != float("inf") else None,
        extra_pilots=int(extra_pilots),
        future_months=round(future_months, 4),
        band=band,
        empty=False,
    )


def format_personal_coverage(pi: PersonalImpact) -> str:
    """Natural-language personal coverage. ``""`` for the empty state.

    Verbs stay in the offset/contribute/cover family — never "pay for" or "fund
    your next N months". All counts are whole numbers so the phrasing never shows
    a fraction.
    """
    if pi.empty:
        return ""
    if pi.band == _BAND_RETROSPECTIVE:
        months = round(pi.own_months_covered)
        if months >= 1:
            return f"covers ~{months} months of your own usage so far"
        # Coverage ratio is the honest fallback when burn rate is too thin to
        # round to a whole month.
        ratio = pi.coverage_ratio
        if ratio is None:  # no realized cost — never reaches the retrospective band
            return "covers your usage so far"
        pct = max(1, round(ratio * 100))
        return f"covers ~{pct}% of what your usage has cost"
    pilots = pi.extra_pilots
    pilot_word = "pilot" if pilots == 1 else "pilots"
    if pi.band == _BAND_COVERS_OTHERS:
        return f"fully covers your own usage — plus ~{pilots} other {pilot_word}"
    months_ahead = round(pi.future_months)
    if months_ahead >= 1:
        return (
            "fully covers your own usage and helped others — and contributes "
            f"~{months_ahead} months toward the service ahead"
        )
    return f"fully covers your own usage — plus ~{pilots} other {pilot_word}"


def personal_to_dict(pi: PersonalImpact) -> dict:
    """Serialize a PersonalImpact (+ phrasing) for the API."""
    return {
        "donation_total_usd": pi.donation_total_usd,
        "lifetime_cost_usd": pi.lifetime_cost_usd,
        "own_months_covered": pi.own_months_covered,
        "coverage_ratio": pi.coverage_ratio,
        "extra_pilots": pi.extra_pilots,
        "future_months": pi.future_months,
        "band": pi.band,
        "empty": pi.empty,
        "summary": format_personal_coverage(pi),
    }


# ---------------------------------------------------------------------------
# Adaptive translation ladder (prospective "donate €X" preview)
# ---------------------------------------------------------------------------
#
# Picks the *type* of translation so the chosen number lands in a satisfying
# range (~2–24) rather than "0" or "0.3". Bands by amount, with graceful
# fallthrough when a band's number would read poorly. Pure + testable.

# Translation kinds (stable identifiers the frontend can branch on).
TRANSLATION_PERSONAL_MONTHS = "personal_months"
TRANSLATION_USER_MONTHS = "user_months"
TRANSLATION_USERS_FOR_MONTH = "users_for_month"
TRANSLATION_BRIEFINGS = "briefings"
TRANSLATION_SERVICE_MONTHS = "service_months"

_SMALL_MAX = 25.0
_MEDIUM_MAX = 150.0


@dataclass(frozen=True)
class TranslationChoice:
    """One chosen prospective-donation translation. ``empty`` ⇒ neutral."""

    amount_usd: float
    kind: str
    value: float
    summary: str
    empty: bool


def _choice(amount_usd: float, kind: str, value: float, summary: str) -> TranslationChoice:
    return TranslationChoice(
        amount_usd=round(amount_usd, 2), kind=kind, value=round(value, 4),
        summary=summary, empty=False,
    )


def choose_translation(
    amount_usd: float,
    economics: ProgramEconomics,
    *,
    burn_rate_monthly_usd: float = 0.0,
) -> TranslationChoice:
    """Pick the most relatable translation for a prospective donation.

    For a logged-in pilot with usage history (``burn_rate_monthly_usd`` > 0) a
    small amount is translated against *their own* usage; otherwise the program
    average is used. Larger amounts climb to per-pilot, briefings-funded, and
    whole-service framings. Degrades to a neutral empty state when economics are
    unavailable.
    """
    if not economics.available or amount_usd <= 0:
        return TranslationChoice(
            amount_usd=round(max(amount_usd, 0.0), 2), kind="", value=0.0,
            summary="", empty=True,
        )

    cpum = economics.cost_per_user_month_usd
    cpb = economics.cost_per_briefing_usd
    monthly = economics.monthly_run_cost_usd
    user_months = amount_usd / cpum if cpum > 0 else 0.0
    personal_months = amount_usd / burn_rate_monthly_usd if burn_rate_monthly_usd > 0 else 0.0
    briefings = amount_usd / cpb if cpb > 0 else 0.0
    service_months = amount_usd / monthly if monthly > 0 else 0.0

    # Small: relate to the donor's own usage when we can, else one pilot's.
    if amount_usd <= _SMALL_MAX:
        if personal_months >= 1.5:
            return _choice(
                amount_usd, TRANSLATION_PERSONAL_MONTHS, personal_months,
                f"covers ~{round(personal_months)} months of your own usage",
            )
        if user_months >= 1.5:
            return _choice(
                amount_usd, TRANSLATION_USER_MONTHS, user_months,
                f"covers one pilot for ~{round(user_months)} months",
            )
        # Tiny relative to cost — briefings keep the number off "0".
        if briefings >= 2:
            return _choice(
                amount_usd, TRANSLATION_BRIEFINGS, briefings,
                f"funds ~{round(briefings)} briefings",
            )
        return _choice(
            amount_usd, TRANSLATION_USER_MONTHS, max(user_months, 0.0),
            "helps cover one pilot's usage",
        )

    # Medium: "N pilots for a month" reads better than "one pilot for N months"
    # once N ≥ 2; otherwise the concrete "briefings funded".
    if amount_usd <= _MEDIUM_MAX:
        if user_months >= 2:
            return _choice(
                amount_usd, TRANSLATION_USERS_FOR_MONTH, user_months,
                f"covers ~{round(user_months)} pilots for a month",
            )
        if briefings >= 2:
            return _choice(
                amount_usd, TRANSLATION_BRIEFINGS, briefings,
                f"funds ~{round(briefings)} briefings",
            )
        return _choice(
            amount_usd, TRANSLATION_SERVICE_MONTHS, max(service_months, 0.0),
            "helps cover the running costs",
        )

    # Large (and site totals): whole-service months.
    if service_months >= 1.5:
        return _choice(
            amount_usd, TRANSLATION_SERVICE_MONTHS, service_months,
            f"covers ~{round(service_months)} months of running the whole service",
        )
    if service_months >= 0.95:
        return _choice(
            amount_usd, TRANSLATION_SERVICE_MONTHS, service_months,
            "covers ~1 month of running the whole service",
        )
    # Very large per-pilot economics make even a big amount sub-month — fall back
    # to pilots-for-a-month so the number stays meaningful.
    n = max(1, round(user_months))
    pilot_word = "pilot" if n == 1 else "pilots"
    return _choice(
        amount_usd, TRANSLATION_USERS_FOR_MONTH, max(user_months, 0.0),
        f"covers ~{n} {pilot_word} for a month",
    )


def translation_to_dict(tc: TranslationChoice) -> dict:
    """Serialize a TranslationChoice for the API."""
    return {
        "amount_usd": tc.amount_usd,
        "kind": tc.kind,
        "value": tc.value,
        "summary": tc.summary,
        "empty": tc.empty,
    }
