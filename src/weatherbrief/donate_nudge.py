"""Donate-nudge gate and lifecycle — pure logic, no DB or I/O.

Decides *when* to ask a pilot to contribute, never *what to say* (copy lives in
the frontend locale files) and never *what it cost* (that is
:func:`weatherbrief.impact.usage_footprint`). The API layer in
:mod:`weatherbrief.api.donations` does the DB work and calls in here.

Two asks share one piece of state:

* **Evergreen** — personal, retrospective, for a pilot who has used the service
  a while and never contributed. Capped at **three asks in a lifetime**: each
  cost rung in :data:`RUNGS` fires once, ever.
* **Campaign** — collective, an annual window configured by env
  (``WB_DONATE_CAMPAIGN``). Off entirely when unset.

Three layers, in order:

1. **Does an ask exist?** (:func:`decide`, gate section) — six conditions, all
   of which must pass for an ask to *open*. An open ask does not expire on its
   own; it is closed by an answer, an exhausted impression budget, or a
   backstop.
2. **Does the chip render on this page view?** — no impression today, budget
   left. The remaining render condition, "assessment is not RED", is
   **client-side**: the server does not know the assessment. The client must
   also withhold the ``shown`` ack when it suppresses, or impressions burn on
   views that painted nothing.
3. **The click** — :func:`close_ask` on Contribute or Maybe later.

**Silence is an answer.** An ask that burns its budget unclicked consumes its
rung exactly as a dismissal does, which is why nothing ever escalates.

State is one ``donate_nudge`` key in ``app_prefs_json``. No migration.
See ``designs/plans/donate-nudge.md`` for the measured calibration behind every
threshold here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

PREFS_KEY = "donate_nudge"

KIND_EVERGREEN = "evergreen"
KIND_CAMPAIGN = "campaign"

# Cost rungs, as multiples of ``cost_per_user_month_usd`` — never hardcoded
# dollars, so they self-calibrate when ECMWF reprices or the pilot base grows.
# Measured against prod 2026-08-31: of 94 eligible pilots, 54 clear K=1.5, 24
# clear K=4 and 4 clear K=10. An earlier [3, 12, 30] was measured dead (K=30
# caught nobody), which is why these are lower than they look.
RUNGS: tuple[float, ...] = (1.5, 4.0, 10.0)

# Engagement floor — "used it for a while" (operator-confirmed 2026-09-01).
MIN_DISTINCT_FLIGHTS = 5
MIN_ACCOUNT_AGE_DAYS = 60
# Floor between any two asks of either kind, so a heavy user cannot trip two
# rungs within weeks.
MIN_DAYS_BETWEEN_ASKS = 90
# Yearly touch for a light-but-loyal pilot whose accumulated cost may never
# reach K=1.5. Anchors on *eligibility* (or the last ask), never on "never
# asked" — reading unset as "infinitely long ago" would fire for the whole
# eligible base on rollout day and make the cost ladder decorative.
ELIGIBILITY_FALLBACK_DAYS = 365
# Impression budget for one ask: 4, at most one per calendar day. At measured
# briefing frequency that is ~16 days (median) to ~39 days (light user).
MAX_IMPRESSIONS_PER_ASK = 4
# ...plus a calendar backstop for the pilot who briefs once a month, whose four
# impressions would otherwise stretch past four months.
ASK_BACKSTOP_DAYS = 90

# A seasonal collective ask needs less runway than an unprompted personal one.
CAMPAIGN_MIN_ACCOUNT_AGE_DAYS = 14
# Catches someone who gave off-cycle via the Settings link, so they are not
# asked again five months later — while still asking last year's campaign donors.
CAMPAIGN_DONOR_LOOKBACK_DAYS = 274  # ~9 months

# Reasons ``decide`` reports for withholding. Stable identifiers, safe to log
# and to surface on the endpoint for debugging; never shown to a pilot.
REASON_SHOW = "show"
REASON_NOT_CONFIGURED = "stripe_not_configured"
REASON_DONATED = "already_donated"
REASON_TOO_FEW_FLIGHTS = "too_few_flights"
REASON_ACCOUNT_TOO_NEW = "account_too_new"
REASON_NO_RUNG_CROSSED = "no_rung_crossed"
REASON_RUNGS_EXHAUSTED = "rungs_exhausted"
REASON_ASKED_RECENTLY = "asked_recently"
REASON_SHOWN_TODAY = "shown_today"
REASON_NO_CAMPAIGN = "no_active_campaign"
REASON_CAMPAIGN_CLOSED = "campaign_answered"


# ---------------------------------------------------------------------------
# Campaign window (env-configured, like STRIPE_SECRET_KEY: set + restart)
# ---------------------------------------------------------------------------

_CAMPAIGN_RE = re.compile(
    r"^\s*(?P<id>[A-Za-z0-9_-]{1,32})\s*:\s*"
    r"(?P<opens>\d{4}-\d{2}-\d{2})\s*\.\.\s*(?P<closes>\d{4}-\d{2}-\d{2})\s*$"
)


@dataclass(frozen=True)
class CampaignWindow:
    """One configured annual campaign: ``2027:2027-04-05..2027-04-26``.

    The window *is* the fatigue control, so a campaign carries no backoff
    ladder. ``id`` is what per-user state is keyed on, so a stale block for a
    previous campaign self-invalidates — no cleanup job.
    """

    id: str
    opens: date
    closes: date

    def active(self, today: date) -> bool:
        return self.opens <= today <= self.closes


def parse_campaign(raw: str | None) -> CampaignWindow | None:
    """Parse ``WB_DONATE_CAMPAIGN``; ``None`` when unset or malformed.

    A bad value disables the campaign rather than raising — the same
    fail-quiet shape as ``stripe_configured()``, so a typo in env can never
    take the briefing page down.
    """
    if not raw:
        return None
    m = _CAMPAIGN_RE.match(raw)
    if not m:
        return None
    try:
        opens = date.fromisoformat(m.group("opens"))
        closes = date.fromisoformat(m.group("closes"))
    except ValueError:
        return None
    if closes < opens:
        return None
    return CampaignWindow(id=m.group("id"), opens=opens, closes=closes)


# ---------------------------------------------------------------------------
# State — one ``donate_nudge`` key in app_prefs_json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenAsk:
    """The live ask. ``opened`` is what the 90-day backstop measures from."""

    kind: str
    opened: date
    shown: int = 0
    last_shown: date | None = None
    # 1..3 for evergreen (index into RUNGS + 1); 0 for a campaign ask.
    rung: int = 0
    # The campaign this ask belongs to; "" for evergreen.
    campaign_id: str = ""


@dataclass(frozen=True)
class CampaignState:
    """Per-campaign counters, discarded wholesale when the campaign id changes."""

    id: str
    closed: bool = False
    shown: int = 0


@dataclass(frozen=True)
class NudgeState:
    """Everything the nudge remembers about one pilot.

    ``last_ask_at`` is shared by both kinds — a campaign impression silently
    pushes the evergreen 90-day floor out, which is the whole of the
    coordination between them. ``tier_asked`` is the highest ``RUNGS`` value
    consumed, so it advances whether the pilot clicked, dismissed or ignored.
    """

    last_ask_at: date | None = None
    asks: int = 0
    tier_asked: float = 0.0
    open_ask: OpenAsk | None = None
    campaign: CampaignState | None = None


def _as_date(value: object) -> date | None:
    """Parse an ISO date from prefs; ``None`` for anything unreadable."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _as_int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def load_state(blob: object) -> NudgeState:
    """Read :class:`NudgeState` out of the ``donate_nudge`` prefs value.

    Tolerant by design: an unrecognised or corrupt blob reads as a fresh state
    rather than raising, because the alternative is a 500 on the briefing page's
    hottest call for a stored value nobody can fix from the UI.
    """
    if not isinstance(blob, dict):
        return NudgeState()

    open_ask = None
    raw_ask = blob.get("open_ask")
    if isinstance(raw_ask, dict):
        opened = _as_date(raw_ask.get("opened"))
        kind = raw_ask.get("kind")
        if opened is not None and kind in (KIND_EVERGREEN, KIND_CAMPAIGN):
            open_ask = OpenAsk(
                kind=kind,
                opened=opened,
                shown=max(_as_int(raw_ask.get("shown")), 0),
                last_shown=_as_date(raw_ask.get("last_shown")),
                rung=max(_as_int(raw_ask.get("rung")), 0),
                campaign_id=raw_ask.get("campaign_id") if isinstance(raw_ask.get("campaign_id"), str) else "",
            )

    campaign = None
    raw_campaign = blob.get("campaign")
    if isinstance(raw_campaign, dict) and isinstance(raw_campaign.get("id"), str):
        campaign = CampaignState(
            id=raw_campaign["id"],
            closed=bool(raw_campaign.get("closed", raw_campaign.get("dismissed", False))),
            shown=max(_as_int(raw_campaign.get("shown")), 0),
        )

    tier = blob.get("tier_asked")
    return NudgeState(
        last_ask_at=_as_date(blob.get("last_ask_at")),
        asks=max(_as_int(blob.get("asks")), 0),
        tier_asked=float(tier) if isinstance(tier, (int, float)) and not isinstance(tier, bool) else 0.0,
        open_ask=open_ask,
        campaign=campaign,
    )


def dump_state(state: NudgeState) -> dict:
    """Serialize :class:`NudgeState` back into the prefs blob (JSON-safe)."""
    blob: dict = {"asks": state.asks, "tier_asked": state.tier_asked}
    if state.last_ask_at is not None:
        blob["last_ask_at"] = state.last_ask_at.isoformat()
    if state.open_ask is not None:
        ask = state.open_ask
        entry: dict = {"kind": ask.kind, "opened": ask.opened.isoformat(), "shown": ask.shown}
        if ask.last_shown is not None:
            entry["last_shown"] = ask.last_shown.isoformat()
        if ask.rung:
            entry["rung"] = ask.rung
        if ask.campaign_id:
            entry["campaign_id"] = ask.campaign_id
        blob["open_ask"] = entry
    if state.campaign is not None:
        blob["campaign"] = {
            "id": state.campaign.id,
            "closed": state.campaign.closed,
            "shown": state.campaign.shown,
        }
    return blob


# ---------------------------------------------------------------------------
# Gate + render decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateInputs:
    """Everything the gate needs about one pilot, at one moment.

    The endpoint fills this in **cheap-first** and bails via
    :func:`blocked_cheaply` before touching the costly fields — ``_economics()``
    JSON-parses every briefing ledger row in the 30-day window, which is fine on
    a rare donate-page load and not fine on every briefing page view.
    ``true_lifetime_cost_usd`` is the recomputed figure from
    :func:`weatherbrief.impact.usage_footprint`, never the ledger sum (which
    blends two amortization bases across the 2026-08-31 rate-card bump).
    """

    today: date
    stripe_configured: bool
    has_donated: bool
    # A recurring donor is suppressed indefinitely: until subscription
    # cancellation is tracked, a time-windowed rule would nag someone who is
    # actively paying.
    has_recurring_donation: bool = False
    last_donation_at: date | None = None
    distinct_flights: int = 0
    account_age_days: int = 0
    # The date the pilot passed the engagement floor (flights + age) — the
    # anchor for the 12-month fallback when they have never been asked.
    eligible_since: date | None = None
    true_lifetime_cost_usd: float = 0.0
    cost_per_user_month_usd: float = 0.0
    campaign: CampaignWindow | None = None


@dataclass(frozen=True)
class NudgeDecision:
    """Outcome of one evaluation: possibly-updated state plus what to render."""

    state: NudgeState
    show: bool
    kind: str
    rung: int
    reason: str
    # True when ``state`` differs from what was passed in and must be persisted.
    changed: bool


def blocked_cheaply(state: NudgeState, inputs: GateInputs) -> str | None:
    """A reason no ask can render *right now*, from cheap inputs only, or None.

    Strictly a subset of what :func:`decide` checks, so it can only produce a
    false "no ask", never a false ask — :func:`decide` remains the single
    authority and re-checks everything. Its whole job is to let the endpoint
    return before computing flight counts or economics; measured on prod, only
    ~112 pilots get past it.

    Only inputs that are free or a single indexed read are consulted:
    ``stripe_configured``, donation existence, and the stored state.
    """
    if not inputs.stripe_configured:
        return REASON_NOT_CONFIGURED

    campaign_live = inputs.campaign is not None and inputs.campaign.active(inputs.today)

    # A donor leaves the evergreen path for good; only campaigns can still ask.
    if inputs.has_donated and not campaign_live:
        return REASON_DONATED
    if inputs.has_recurring_donation:
        return REASON_DONATED

    ask = state.open_ask
    if ask is not None:
        # An open ask that has already been seen today renders nothing, and
        # nothing about it can change until tomorrow.
        if ask.last_shown == inputs.today:
            return REASON_SHOWN_TODAY
        return None

    if not campaign_live:
        # No open ask and no campaign: only the evergreen path is left, and it
        # is closed while the 90-day floor holds or all three rungs are spent.
        if state.tier_asked >= RUNGS[-1]:
            return REASON_RUNGS_EXHAUSTED
        if _asked_recently(state, inputs.today):
            return REASON_ASKED_RECENTLY
    return None


def _asked_recently(state: NudgeState, today: date) -> bool:
    return (
        state.last_ask_at is not None
        and (today - state.last_ask_at).days < MIN_DAYS_BETWEEN_ASKS
    )


def _next_rung(state: NudgeState, inputs: GateInputs) -> int:
    """The lowest unused rung whose cost threshold is crossed, else 0.

    Lowest-unused rather than highest-crossed, so the ladder is climbed one
    step at a time: combined with the 90-day floor, a heavy pilot who is
    already past K=4 on rollout day still gets K=1.5 first and K=4 no sooner
    than three months later. Consuming both at once would silently halve the
    three lifetime asks for exactly the pilots who use the service most.
    """
    cpum = inputs.cost_per_user_month_usd
    if cpum <= 0:
        return 0
    for index, k in enumerate(RUNGS, start=1):
        if k <= state.tier_asked:
            continue
        if inputs.true_lifetime_cost_usd >= k * cpum:
            return index
        # Rungs ascend, so the first uncrossed one ends the search.
        return 0
    return 0


def _fallback_rung(state: NudgeState, inputs: GateInputs) -> int:
    """The lowest unused rung when the 12-month clause fires, else 0.

    Anchored on the last ask, or on the date the pilot became eligible when
    they have never been asked. With no eligibility date we decline rather than
    treating "unknown" as "long ago".
    """
    anchor = state.last_ask_at or inputs.eligible_since
    if anchor is None:
        return 0
    if (inputs.today - anchor).days < ELIGIBILITY_FALLBACK_DAYS:
        return 0
    for index, k in enumerate(RUNGS, start=1):
        if k > state.tier_asked:
            return index
    return 0


def _close(state: NudgeState, today: date) -> NudgeState:
    """Drop the open ask, leaving its rung consumed and the 90-day floor armed.

    ``last_ask_at`` is stamped here as well as on each impression, so an ask
    that expires on the backstop *without ever being shown* still starts the
    floor. Otherwise its rung would be consumed and the next one could open on
    the very next page view — three lifetime asks burned in three days by a
    pilot who never saw one.
    """
    if state.open_ask is None:
        return state
    campaign = state.campaign
    if state.open_ask.kind == KIND_CAMPAIGN and campaign is not None:
        campaign = replace(campaign, closed=True)
    return replace(
        state, open_ask=None, campaign=campaign, last_ask_at=state.last_ask_at or today
    )


def _campaign_state_for(state: NudgeState, window: CampaignWindow) -> CampaignState:
    """This campaign's counters — a block for another campaign self-invalidates."""
    if state.campaign is not None and state.campaign.id == window.id:
        return state.campaign
    return CampaignState(id=window.id)


def decide(state: NudgeState, inputs: GateInputs) -> NudgeDecision:
    """Evaluate the gate, the lifecycle and the render condition in one pass.

    Returns the (possibly updated) state alongside the verdict; the caller
    persists it when ``changed``. This closes expired asks and opens new ones,
    so it is the single authority on nudge state — :func:`blocked_cheaply` is
    only an optimization in front of it.

    RED suppression is **not** here: the server does not know the briefing's
    assessment. The client withholds both the chip and the ``shown`` ack.
    """
    original = state
    reason = REASON_SHOW

    if not inputs.stripe_configured:
        return NudgeDecision(state, False, "", 0, REASON_NOT_CONFIGURED, False)

    window = inputs.campaign
    campaign_live = window is not None and window.active(inputs.today)

    # A donation mid-ask closes the open ask. For evergreen that is permanent:
    # the never-contributed gate has failed and the path is over. For a
    # campaign it is this campaign only — "donated since this campaign opened"
    # means suppressed until next year, and the same donor-lookback that would
    # refuse to *open* the ask has to be able to close one already open, or a
    # pilot who donates mid-window keeps being asked for the rest of it.
    open_ask = state.open_ask
    if inputs.has_donated and open_ask is not None:
        if open_ask.kind == KIND_EVERGREEN:
            state = _close(state, inputs.today)
        elif window is not None and open_ask.campaign_id == window.id:
            campaign = _campaign_state_for(state, window)
            if _campaign_blocked(state, inputs, window, campaign) == REASON_DONATED:
                state = _close(state, inputs.today)

    if inputs.has_recurring_donation:
        return NudgeDecision(state, False, "", 0, REASON_DONATED, state is not original)

    # --- close an ask that has run out of road -----------------------------
    ask = state.open_ask
    if ask is not None:
        expired = (inputs.today - ask.opened).days >= ASK_BACKSTOP_DAYS
        exhausted = ask.shown >= MAX_IMPRESSIONS_PER_ASK
        stale_campaign = ask.kind == KIND_CAMPAIGN and (
            window is None or window.id != ask.campaign_id or not window.active(inputs.today)
        )
        if expired or exhausted or stale_campaign:
            state = _close(state, inputs.today)
            ask = None

    # --- open one, if the gate passes --------------------------------------
    if state.open_ask is None:
        opened, reason = _try_open(state, inputs, window, campaign_live)
        if opened is not None:
            state = opened

    ask = state.open_ask
    if ask is None:
        return NudgeDecision(state, False, "", 0, reason, state is not original)

    # --- layer 2: does the chip paint on this page view? --------------------
    if ask.last_shown == inputs.today:
        return NudgeDecision(state, False, ask.kind, ask.rung, REASON_SHOWN_TODAY, state is not original)

    return NudgeDecision(state, True, ask.kind, ask.rung, REASON_SHOW, state is not original)


def _try_open(
    state: NudgeState,
    inputs: GateInputs,
    window: CampaignWindow | None,
    campaign_live: bool,
) -> tuple[NudgeState | None, str]:
    """Open a campaign ask, else an evergreen one; ``(None, reason)`` if neither.

    The campaign wins when both would fire: it is the seasonal collective ask,
    and it reaches donors the evergreen path has permanently released.
    """
    if campaign_live and window is not None:
        campaign = _campaign_state_for(state, window)
        blocked = _campaign_blocked(state, inputs, window, campaign)
        if blocked is None:
            return (
                replace(
                    state,
                    asks=state.asks + 1,
                    campaign=campaign,
                    open_ask=OpenAsk(
                        kind=KIND_CAMPAIGN, opened=inputs.today, campaign_id=window.id
                    ),
                ),
                REASON_SHOW,
            )
        campaign_reason = blocked
    else:
        campaign_reason = REASON_NO_CAMPAIGN

    opened, evergreen_reason = _try_open_evergreen(state, inputs)
    if opened is not None:
        return opened, REASON_SHOW
    # Report the evergreen reason unless a live campaign is what actually
    # withheld — a "no active campaign" reason on a pilot who simply has not
    # crossed a rung would read as the wrong diagnosis.
    return None, evergreen_reason if campaign_reason == REASON_NO_CAMPAIGN else campaign_reason


def _campaign_blocked(
    state: NudgeState,
    inputs: GateInputs,
    window: CampaignWindow,
    campaign: CampaignState,
) -> str | None:
    """Why this pilot gets no campaign ask in this window, or None."""
    if campaign.closed:
        return REASON_CAMPAIGN_CLOSED
    if campaign.shown >= MAX_IMPRESSIONS_PER_ASK:
        return REASON_CAMPAIGN_CLOSED
    if inputs.distinct_flights < MIN_DISTINCT_FLIGHTS:
        return REASON_TOO_FEW_FLIGHTS
    if inputs.account_age_days < CAMPAIGN_MIN_ACCOUNT_AGE_DAYS:
        return REASON_ACCOUNT_TOO_NEW
    # "Donated since this campaign opened" ⇒ suppressed for this campaign,
    # asked again next year. The lookback also catches an off-cycle donation.
    if inputs.last_donation_at is not None:
        cutoff = window.opens - timedelta(days=CAMPAIGN_DONOR_LOOKBACK_DAYS)
        if inputs.last_donation_at >= cutoff:
            return REASON_DONATED
    return None


def _try_open_evergreen(state: NudgeState, inputs: GateInputs) -> tuple[NudgeState | None, str]:
    """Open an evergreen ask, or explain which of the six conditions failed."""
    if inputs.has_donated:
        return None, REASON_DONATED
    if inputs.distinct_flights < MIN_DISTINCT_FLIGHTS:
        return None, REASON_TOO_FEW_FLIGHTS
    if inputs.account_age_days < MIN_ACCOUNT_AGE_DAYS:
        return None, REASON_ACCOUNT_TOO_NEW
    if state.tier_asked >= RUNGS[-1]:
        return None, REASON_RUNGS_EXHAUSTED
    if _asked_recently(state, inputs.today):
        return None, REASON_ASKED_RECENTLY

    rung = _next_rung(state, inputs) or _fallback_rung(state, inputs)
    if rung == 0:
        return None, REASON_NO_RUNG_CROSSED

    return (
        replace(
            state,
            asks=state.asks + 1,
            # Consumed at open: silence answers the ask exactly as a dismissal
            # does, so the rung must not depend on what the pilot goes on to do.
            tier_asked=RUNGS[rung - 1],
            open_ask=OpenAsk(kind=KIND_EVERGREEN, opened=inputs.today, rung=rung),
        ),
        REASON_SHOW,
    )


# ---------------------------------------------------------------------------
# Acks
# ---------------------------------------------------------------------------


def record_shown(state: NudgeState, today: date) -> NudgeState:
    """Record one impression. Idempotent within the calendar day.

    Advances the shared ``last_ask_at``, so a campaign impression pushes the
    evergreen 90-day floor out with no coordination logic. Closes the ask once
    the budget is spent — silence is an answer, and the rung is already
    consumed.
    """
    ask = state.open_ask
    if ask is None or ask.last_shown == today:
        return state
    shown = ask.shown + 1
    campaign = state.campaign
    if ask.kind == KIND_CAMPAIGN and campaign is not None and campaign.id == ask.campaign_id:
        campaign = replace(campaign, shown=campaign.shown + 1)
    state = replace(
        state,
        last_ask_at=today,
        campaign=campaign,
        open_ask=replace(ask, shown=shown, last_shown=today),
    )
    if shown >= MAX_IMPRESSIONS_PER_ASK:
        state = _close(state, today)
    return state


def close_ask(state: NudgeState, today: date) -> NudgeState:
    """Close the open ask on an answer (Contribute or Maybe later).

    "Maybe later" **ends** the ask; it does not snooze it. The rung was
    consumed when the ask opened, so the next ask is the next rung, potentially
    months out — a snooze that re-fires in a week is how three-asks-in-a-
    lifetime turns into nagging.

    A popover opened and dismissed with Esc or a click outside must *not* call
    this: the pilot answered nothing. The impression already counted, which is
    what self-limits it.
    """
    return _close(state, today)


# ---------------------------------------------------------------------------
# Small date helpers shared with the API layer
# ---------------------------------------------------------------------------


def today_utc() -> date:
    """The nudge's calendar day. UTC everywhere, matching the rest of the app."""
    return datetime.now(timezone.utc).date()


def as_utc_date(value: datetime | None) -> date | None:
    """Normalize a stored datetime (naive-UTC in the DB) to a UTC date."""
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).date()
