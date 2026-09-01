"""Donate-nudge gate, lifecycle and true-cost arithmetic — all pure.

No DB, no app: :mod:`weatherbrief.donate_nudge` and
:func:`weatherbrief.impact.usage_footprint` are deliberately I/O-free so the
rules that decide when a pilot gets asked for money can be pinned exactly.
The endpoint wiring is covered in ``test_donations_api.py``.

Numbers here are the ones measured on prod 2026-08-31 and recorded in
``designs/plans/donate-nudge.md``: ``cost_per_user_month_usd`` $2.84, fixed
$250/month over 1632 briefings/month.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from weatherbrief import donate_nudge as nudge
from weatherbrief.impact import usage_footprint

TODAY = date(2026, 9, 1)
CPUM = 2.84  # cost_per_user_month_usd, measured
FIXED_MONTHLY = 250.0
BRIEFINGS_PER_MONTH = 1632.0

# Comfortably past K=1.5 ($4.26) but short of K=4 ($11.36).
RUNG1_COST = 6.0


def _eligible(**overrides) -> nudge.GateInputs:
    """Gate inputs for a pilot who passes every condition — override to break one."""
    base = dict(
        today=TODAY,
        stripe_configured=True,
        has_donated=False,
        has_recurring_donation=False,
        last_donation_at=None,
        distinct_flights=8,
        account_age_days=200,
        eligible_since=TODAY - timedelta(days=100),
        true_lifetime_cost_usd=RUNG1_COST,
        cost_per_user_month_usd=CPUM,
        campaign=None,
    )
    base.update(overrides)
    return nudge.GateInputs(**base)


# ---------------------------------------------------------------------------
# usage_footprint — the true-cost basis
# ---------------------------------------------------------------------------


class TestUsageFootprint:
    def test_recomputes_from_the_true_basis_not_the_ledger(self):
        """Ten briefings: variable as measured, fixed at the *actual* volume."""
        details = [{"token_cost_usd": 0.05, "storage_cost_usd": 0.003}] * 10
        fp = usage_footprint(
            details,
            ledger_cost_usd=5.50,  # what the ledger charged (over-recovered)
            fixed_monthly_usd=FIXED_MONTHLY,
            briefings_per_month=BRIEFINGS_PER_MONTH,
        )
        assert fp.briefings == 10
        assert fp.variable_usd == pytest.approx(0.53)
        # 250 / 1632 = $0.15319 per briefing, ten of them.
        assert fp.fixed_share_usd == pytest.approx(1.5319, abs=1e-3)
        assert fp.true_cost_usd == pytest.approx(2.0619, abs=1e-3)
        # The point of the exercise: the ledger says something quite different.
        assert fp.ledger_cost_usd == pytest.approx(5.50)
        assert fp.complete

    def test_de_margining_the_ledger_would_not_have_worked(self):
        """The stale volume estimate dwarfs the 10% margin — see the design doc.

        Pre-bump, a briefing was billed a $0.50 fixed share against a true
        $0.153. Dividing the ledger by 1.10 lands nowhere near the truth, which
        is why ``usage_footprint`` recomputes instead of scaling.
        """
        details = [{"token_cost_usd": 0.05, "storage_cost_usd": 0.003}] * 10
        ledger = (10 * 0.50 + 0.53) * 1.10  # amortized at est=500, plus margin
        fp = usage_footprint(
            details,
            ledger_cost_usd=ledger,
            fixed_monthly_usd=FIXED_MONTHLY,
            briefings_per_month=BRIEFINGS_PER_MONTH,
        )
        de_margined = ledger / 1.10
        assert de_margined > fp.true_cost_usd * 2.5

    def test_blends_pre_and_post_rate_card_bump(self):
        """A lifetime sum spanning cost_config v4 mixes two amortization bases.

        The ledger total is not comparable across pilots after the bump; the
        recomputed figure is, because it never reads the amortized share at all.
        """
        variable = {"token_cost_usd": 0.05, "storage_cost_usd": 0.003}
        pre = [variable] * 5  # charged at est=500 → $0.50 fixed each
        post = [variable] * 5  # charged at est=1600 → $0.156 fixed each
        blended_ledger = (5 * 0.50 + 5 * 0.156 + 10 * 0.053) * 1.10
        fp = usage_footprint(
            pre + post,
            ledger_cost_usd=blended_ledger,
            fixed_monthly_usd=FIXED_MONTHLY,
            briefings_per_month=BRIEFINGS_PER_MONTH,
        )
        # Every briefing gets the same true share regardless of when it ran.
        assert fp.fixed_share_usd == pytest.approx(10 * FIXED_MONTHLY / BRIEFINGS_PER_MONTH, abs=1e-6)
        assert fp.true_cost_usd == pytest.approx(0.53 + 10 * FIXED_MONTHLY / BRIEFINGS_PER_MONTH, abs=1e-6)

    @pytest.mark.parametrize(
        "detail",
        [
            None,
            {},
            {"token_cost_usd": 0.05},  # storage missing
            {"storage_cost_usd": 0.003},  # tokens missing
            {"token_cost_usd": "0.05", "storage_cost_usd": 0.003},  # wrong type
            "not-a-mapping",
        ],
        ids=["null", "empty", "no-storage", "no-tokens", "string-value", "not-a-dict"],
    )
    def test_a_missing_key_reads_unknown_never_zero(self, detail):
        """An unreadable row must not fold in as a measured $0.00.

        It still carries its fixed share — the briefing demonstrably ran — so the
        total only ever *understates*, which is the safe direction for a
        donation ask.
        """
        good = {"token_cost_usd": 0.05, "storage_cost_usd": 0.003}
        fp = usage_footprint(
            [good, detail],
            ledger_cost_usd=1.0,
            fixed_monthly_usd=FIXED_MONTHLY,
            briefings_per_month=BRIEFINGS_PER_MONTH,
        )
        assert fp.unknown_variable_rows == 1
        assert not fp.complete
        assert fp.briefings == 2
        assert fp.variable_usd == pytest.approx(0.053)
        # Both briefings are amortized, only one is priced.
        assert fp.fixed_share_usd == pytest.approx(2 * FIXED_MONTHLY / BRIEFINGS_PER_MONTH, abs=1e-6)

    def test_no_briefings_is_empty(self):
        fp = usage_footprint(
            [], ledger_cost_usd=0.0, fixed_monthly_usd=FIXED_MONTHLY,
            briefings_per_month=BRIEFINGS_PER_MONTH,
        )
        assert fp.empty and fp.true_cost_usd == 0.0

    def test_no_volume_to_amortize_over_drops_the_fixed_share(self):
        """With no measured volume we decline to guess rather than divide by zero."""
        fp = usage_footprint(
            [{"token_cost_usd": 0.05, "storage_cost_usd": 0.003}],
            ledger_cost_usd=0.5, fixed_monthly_usd=FIXED_MONTHLY, briefings_per_month=0.0,
        )
        assert fp.fixed_share_usd == 0.0
        assert fp.true_cost_usd == pytest.approx(0.053)


# ---------------------------------------------------------------------------
# Layer 1 — the gate truth table
# ---------------------------------------------------------------------------


class TestEvergreenGate:
    def test_all_conditions_pass_opens_an_ask(self):
        d = nudge.decide(nudge.NudgeState(), _eligible())
        assert d.show and d.kind == nudge.KIND_EVERGREEN and d.rung == 1
        assert d.changed
        assert d.state.open_ask is not None
        assert d.state.asks == 1
        assert d.state.tier_asked == 1.5

    @pytest.mark.parametrize(
        "override, reason",
        [
            ({"stripe_configured": False}, nudge.REASON_NOT_CONFIGURED),
            ({"distinct_flights": 4}, nudge.REASON_TOO_FEW_FLIGHTS),
            ({"account_age_days": 59}, nudge.REASON_ACCOUNT_TOO_NEW),
            ({"has_donated": True}, nudge.REASON_DONATED),
            ({"true_lifetime_cost_usd": 4.25}, nudge.REASON_NO_RUNG_CROSSED),
        ],
        ids=["no-stripe", "too-few-flights", "account-too-new", "donated", "under-rung-1"],
    )
    def test_each_condition_failing_in_isolation(self, override, reason):
        d = nudge.decide(nudge.NudgeState(), _eligible(**override))
        assert not d.show
        assert d.reason == reason

    def test_ninety_day_floor_since_the_last_ask(self):
        state = nudge.NudgeState(last_ask_at=TODAY - timedelta(days=89), tier_asked=1.5)
        assert nudge.decide(state, _eligible(true_lifetime_cost_usd=20.0)).reason == (
            nudge.REASON_ASKED_RECENTLY
        )
        aged = replace(state, last_ask_at=TODAY - timedelta(days=90))
        assert nudge.decide(aged, _eligible(true_lifetime_cost_usd=20.0)).show

    def test_rung_boundary_is_the_multiple_not_a_dollar_amount(self):
        """K=1.5 fires at 1.5 x cost_per_user_month_usd, whatever that is today."""
        assert not nudge.decide(
            nudge.NudgeState(), _eligible(true_lifetime_cost_usd=1.5 * CPUM - 0.01)
        ).show
        assert nudge.decide(
            nudge.NudgeState(), _eligible(true_lifetime_cost_usd=1.5 * CPUM)
        ).show
        # Double the platform's per-pilot cost and the same pilot no longer qualifies.
        assert not nudge.decide(
            nudge.NudgeState(),
            _eligible(true_lifetime_cost_usd=1.5 * CPUM, cost_per_user_month_usd=CPUM * 2),
        ).show

    def test_ladder_is_climbed_one_rung_at_a_time(self):
        """A pilot already past K=4 still gets K=1.5 first.

        Consuming both at once would silently halve the three lifetime asks for
        exactly the pilots who use the service most.
        """
        d = nudge.decide(nudge.NudgeState(), _eligible(true_lifetime_cost_usd=50.0))
        assert d.rung == 1 and d.state.tier_asked == 1.5

    def test_three_asks_is_the_lifetime_cap(self):
        state = nudge.NudgeState(
            tier_asked=10.0, last_ask_at=TODAY - timedelta(days=400), asks=3
        )
        d = nudge.decide(state, _eligible(true_lifetime_cost_usd=1000.0))
        assert not d.show and d.reason == nudge.REASON_RUNGS_EXHAUSTED

    def test_recurring_donor_is_suppressed_indefinitely(self):
        """Renewals may not be attributable, so an active subscriber is never asked."""
        d = nudge.decide(
            nudge.NudgeState(), _eligible(has_donated=False, has_recurring_donation=True)
        )
        assert not d.show and d.reason == nudge.REASON_DONATED


class TestTwelveMonthFallback:
    def test_fires_a_year_after_eligibility_for_a_light_pilot(self):
        """The cohort we most want to reach may never accumulate K=1.5."""
        inputs = _eligible(
            true_lifetime_cost_usd=0.40, eligible_since=TODAY - timedelta(days=365)
        )
        d = nudge.decide(nudge.NudgeState(), inputs)
        assert d.show and d.rung == 1

    def test_does_not_fire_before_a_year(self):
        inputs = _eligible(
            true_lifetime_cost_usd=0.40, eligible_since=TODAY - timedelta(days=364)
        )
        assert not nudge.decide(nudge.NudgeState(), inputs).show

    def test_unset_eligibility_is_not_read_as_long_ago(self):
        """Otherwise the clause fires for the whole eligible base on rollout day."""
        inputs = _eligible(true_lifetime_cost_usd=0.40, eligible_since=None)
        d = nudge.decide(nudge.NudgeState(), inputs)
        assert not d.show and d.reason == nudge.REASON_NO_RUNG_CROSSED

    def test_anchors_on_the_last_ask_once_there_is_one(self):
        state = nudge.NudgeState(last_ask_at=TODAY - timedelta(days=200), tier_asked=1.5)
        inputs = _eligible(
            true_lifetime_cost_usd=0.40, eligible_since=TODAY - timedelta(days=900)
        )
        assert not nudge.decide(state, inputs).show
        older = replace(state, last_ask_at=TODAY - timedelta(days=365))
        assert nudge.decide(older, inputs).show


# ---------------------------------------------------------------------------
# Layer 2 + 3 — impressions, answers, closing conditions
# ---------------------------------------------------------------------------


class TestLifecycle:
    def _open(self) -> nudge.NudgeState:
        return nudge.decide(nudge.NudgeState(), _eligible()).state

    def test_one_impression_per_calendar_day(self):
        state = nudge.record_shown(self._open(), TODAY)
        assert state.open_ask.shown == 1
        # Same day again: idempotent, and the chip does not render.
        assert nudge.record_shown(state, TODAY) is state
        assert nudge.decide(state, _eligible()).reason == nudge.REASON_SHOWN_TODAY
        # Tomorrow it is back.
        assert nudge.decide(state, _eligible(today=TODAY + timedelta(days=1))).show

    def test_impression_cap_closes_the_ask(self):
        state = self._open()
        for offset in range(nudge.MAX_IMPRESSIONS_PER_ASK):
            state = nudge.record_shown(state, TODAY + timedelta(days=offset))
        assert state.open_ask is None
        assert state.tier_asked == 1.5  # rung stays consumed

    def test_ignoring_consumes_the_rung_exactly_as_a_dismissal_does(self):
        """Silence is an answer — which is why nothing ever escalates."""
        ignored = self._open()
        for offset in range(nudge.MAX_IMPRESSIONS_PER_ASK):
            ignored = nudge.record_shown(ignored, TODAY + timedelta(days=offset))
        dismissed = nudge.close_ask(nudge.record_shown(self._open(), TODAY), TODAY)
        assert ignored.tier_asked == dismissed.tier_asked == 1.5
        assert ignored.open_ask is dismissed.open_ask is None

    def test_maybe_later_ends_the_ask_it_does_not_snooze_it(self):
        state = nudge.close_ask(nudge.record_shown(self._open(), TODAY), TODAY)
        # A week later, with the cost well past K=4, still nothing: the 90-day
        # floor holds and the next ask is the next rung.
        later = _eligible(today=TODAY + timedelta(days=7), true_lifetime_cost_usd=50.0)
        assert nudge.decide(state, later).reason == nudge.REASON_ASKED_RECENTLY

    def test_ninety_day_backstop_closes_a_slow_burning_ask(self):
        """For the pilot who briefs once a month, four impressions take too long."""
        state = nudge.record_shown(self._open(), TODAY)
        d = nudge.decide(state, _eligible(today=TODAY + timedelta(days=90)))
        assert d.state.open_ask is None
        assert not d.show

    def test_popover_dismissed_without_choosing_does_not_consume_the_ask(self):
        """Esc / click-outside answers nothing; only the impression counted."""
        state = nudge.record_shown(self._open(), TODAY)
        # The client sends no ack for an Esc, so state is untouched...
        assert state.open_ask is not None and state.open_ask.shown == 1
        # ...and the chip is back tomorrow.
        assert nudge.decide(state, _eligible(today=TODAY + timedelta(days=1))).show

    def test_a_backstopped_ask_still_arms_the_ninety_day_floor(self):
        """An ask that expires unseen must not let the next rung open at once."""
        state = self._open()  # opened, never shown
        d = nudge.decide(state, _eligible(today=TODAY + timedelta(days=90),
                                          true_lifetime_cost_usd=50.0))
        assert d.state.open_ask is None
        assert d.state.last_ask_at == TODAY + timedelta(days=90)
        assert nudge.decide(
            d.state, _eligible(today=TODAY + timedelta(days=91), true_lifetime_cost_usd=50.0)
        ).reason == nudge.REASON_ASKED_RECENTLY

    def test_a_donation_mid_ask_closes_the_open_ask(self):
        state = nudge.record_shown(self._open(), TODAY)
        d = nudge.decide(state, _eligible(has_donated=True))
        assert d.state.open_ask is None
        assert not d.show and d.reason == nudge.REASON_DONATED


# ---------------------------------------------------------------------------
# The cheap short-circuit in front of the gate
# ---------------------------------------------------------------------------


class TestCheapShortCircuit:
    """``blocked_cheaply`` may only ever produce a *false no*, never a false yes."""

    @pytest.mark.parametrize(
        "state, override",
        [
            (nudge.NudgeState(), {}),
            (nudge.NudgeState(), {"stripe_configured": False}),
            (nudge.NudgeState(), {"has_donated": True}),
            (nudge.NudgeState(), {"has_recurring_donation": True}),
            (nudge.NudgeState(last_ask_at=TODAY - timedelta(days=10)), {}),
            (nudge.NudgeState(tier_asked=10.0), {}),
            (nudge.NudgeState(last_ask_at=TODAY - timedelta(days=200)), {}),
        ],
    )
    def test_never_blocks_an_ask_the_gate_would_have_opened(self, state, override):
        inputs = _eligible(**override)
        if nudge.blocked_cheaply(state, inputs) is not None:
            assert not nudge.decide(state, inputs).show

    def test_settles_the_common_case_without_the_costly_fields(self):
        """A pilot with no donation history and no state: only cheap inputs read."""
        bare = nudge.GateInputs(today=TODAY, stripe_configured=True, has_donated=True)
        assert nudge.blocked_cheaply(nudge.NudgeState(), bare) == nudge.REASON_DONATED


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


class TestCampaignConfig:
    def test_parses_the_env_shape(self):
        w = nudge.parse_campaign("2027:2027-04-05..2027-04-26")
        assert w is not None
        assert (w.id, w.opens, w.closes) == ("2027", date(2027, 4, 5), date(2027, 4, 26))
        assert w.active(date(2027, 4, 5)) and w.active(date(2027, 4, 26))
        assert not w.active(date(2027, 4, 27))

    @pytest.mark.parametrize(
        "raw",
        [None, "", "2027", "2027:2027-04-05", "2027:2027-04-26..2027-04-05",
         "2027:not-a-date..2027-04-26"],
    )
    def test_a_bad_value_disables_rather_than_raises(self, raw):
        assert nudge.parse_campaign(raw) is None


class TestCampaignGate:
    WINDOW = nudge.CampaignWindow("2027", date(2026, 8, 20), date(2026, 9, 10))

    def _inputs(self, **overrides):
        base = dict(campaign=self.WINDOW, account_age_days=20, true_lifetime_cost_usd=0.0)
        base.update(overrides)
        return _eligible(**base)

    def test_opens_inside_the_window_for_a_pilot_no_evergreen_ask_would_reach(self):
        d = nudge.decide(nudge.NudgeState(), self._inputs())
        assert d.show and d.kind == nudge.KIND_CAMPAIGN
        assert d.state.campaign is not None and d.state.campaign.id == "2027"

    def test_lower_engagement_floor_than_evergreen_but_still_five_flights(self):
        assert nudge.decide(nudge.NudgeState(), self._inputs(account_age_days=14)).show
        assert not nudge.decide(nudge.NudgeState(), self._inputs(account_age_days=13)).show
        assert not nudge.decide(nudge.NudgeState(), self._inputs(distinct_flights=4)).show

    def test_reaches_a_donor_the_evergreen_path_released(self):
        old_gift = self.WINDOW.opens - timedelta(days=400)
        d = nudge.decide(
            nudge.NudgeState(), self._inputs(has_donated=True, last_donation_at=old_gift)
        )
        assert d.show and d.kind == nudge.KIND_CAMPAIGN

    def test_nine_month_lookback_suppresses_a_recent_donor(self):
        recent = self.WINDOW.opens - timedelta(days=100)
        d = nudge.decide(
            nudge.NudgeState(), self._inputs(has_donated=True, last_donation_at=recent)
        )
        assert not d.show and d.reason == nudge.REASON_DONATED

    def test_answered_campaign_does_not_reopen_in_the_same_window(self):
        state = nudge.close_ask(
            nudge.decide(nudge.NudgeState(), self._inputs()).state, TODAY
        )
        d = nudge.decide(state, self._inputs(today=TODAY + timedelta(days=2)))
        assert not d.show and d.reason == nudge.REASON_CAMPAIGN_CLOSED

    def test_a_stale_campaign_block_self_invalidates(self):
        state = nudge.NudgeState(campaign=nudge.CampaignState(id="2026", closed=True))
        assert nudge.decide(state, self._inputs()).show

    def test_window_close_closes_an_open_campaign_ask(self):
        state = nudge.decide(nudge.NudgeState(), self._inputs()).state
        after = nudge.decide(state, self._inputs(today=self.WINDOW.closes + timedelta(days=1)))
        assert after.state.open_ask is None

    def test_campaign_wins_when_both_would_fire(self):
        d = nudge.decide(nudge.NudgeState(), self._inputs(true_lifetime_cost_usd=50.0))
        assert d.kind == nudge.KIND_CAMPAIGN

    def test_a_campaign_impression_pushes_the_evergreen_floor_out(self):
        """One shared last_ask_at is the whole of the coordination between them."""
        state = nudge.record_shown(
            nudge.decide(nudge.NudgeState(), self._inputs()).state, TODAY
        )
        assert state.last_ask_at == TODAY
        after_window = _eligible(
            today=TODAY + timedelta(days=30), true_lifetime_cost_usd=50.0
        )
        assert nudge.decide(state, after_window).reason == nudge.REASON_ASKED_RECENTLY


# ---------------------------------------------------------------------------
# State round-trip (one app_prefs_json key, no migration)
# ---------------------------------------------------------------------------


class TestStateSerialization:
    def test_round_trips(self):
        state = nudge.record_shown(nudge.decide(nudge.NudgeState(), _eligible()).state, TODAY)
        assert nudge.load_state(nudge.dump_state(state)) == state

    @pytest.mark.parametrize(
        "blob", [None, "nonsense", 42, [], {"open_ask": "broken"}, {"asks": "many"}]
    )
    def test_a_corrupt_blob_reads_as_a_fresh_state(self, blob):
        """A 500 on the briefing page's hottest call is not an acceptable
        response to a stored value nobody can fix from the UI."""
        assert nudge.load_state(blob) == nudge.NudgeState()

    def test_an_ask_with_no_open_date_is_discarded(self):
        state = nudge.load_state({"open_ask": {"kind": "evergreen", "shown": 2}})
        assert state.open_ask is None
