# Donate nudge on the briefing page — logic + rollout plan

> **Status (2026-09-01): built (issue #588). All seven build-order steps
> shipped.** Read `designs/cost-attribution-design.md` § "Donations (Stripe)"
> first — that is the architecture. This file is only the *when do we ask*
> layer plus the briefing-page surface.
>
> As-built inventory:
>
> ```
> weatherbrief/donate_nudge.py          — gate, lifecycle, campaign window (pure)
> weatherbrief/impact.py                — usage_footprint(): true-cost basis (pure)
> weatherbrief/api/credits.py           — user_usage_stats(): the DB half of it
> weatherbrief/api/donations.py         — GET /nudge, POST /nudge/ack,
>                                         invoice.payment_succeeded, /me usage block
> web/ts/managers/donate-nudge-ui.ts    — chip + popover, RED suppression
> web/ts/donate-main.ts                 — never-donor panel
> tests/test_donate_nudge.py            — gate truth table, lifecycle, arithmetic
> web/tests/unit/donate-nudge.test.ts   — the two client-side rules
> ```
>
> Where the build departs from the plan below, it says so in
> "As built — deviations from this plan" at the foot of the file. Read that
> section before treating any paragraph here as a description of the code.

## Why

Donations are live and the donate page works, but nothing ever points a pilot at
it: the only entry points are a Settings link and a direct URL. We want an ask on
the briefing page, next to the 👍/👎 widget — without turning a free safety tool
into something that begs.

Two cost drivers shape the cadence, and they are different in kind:

| Cost | Shape | Ask it belongs to |
|---|---|---|
| ECMWF commercial data | fixed, annual, shared by everyone | **campaign** — collective, seasonal |
| LLM tokens | variable, per refresh, attributable to a person | **evergreen** — personal, retrospective |

So there are two asks, not one, and they carry different framing.

## Decision summary

- One **server-side** decision, exposed on a **web-only** endpoint. Never folded
  into an existing briefing payload.
- **Evergreen ask**: for pilots who have used the service a while and never
  contributed. Capped at **three asks in a lifetime**.
- **Campaign ask**: an annual window anchored on the ECMWF renewal. Additive;
  ships later.
- **Framing comes from `impact.py`**, never hand-written copy and never a raw
  ledger figure.
- State lives in `app_prefs_json`. No migration.

## Where the decision lives

`GET /api/donations/nudge`, called only by `web/ts`. The flag must **not** ride
on `/api/flights`, pack meta, or any DTO iOS consumes.

This is structural, not stylistic. `cost-attribution-design.md` is explicit that
the donate flow stays web-only: Apple requires donations from
non-registered-nonprofits to go through IAP, so a donate button inside the app
binary is a review rejection. If the flag is reachable from a shared payload,
someone eventually renders it. A separate endpoint makes that impossible rather
than merely discouraged.

## Evergreen ask

### Gates

All must hold:

| Gate | Rule | Source |
|---|---|---|
| Feature live | `stripe_configured()` | `api/preferences.py:618` — the existing global switch |
| Earned it | `COUNT(DISTINCT flight_id) >= 5` **and** `account_age >= 60d` | `briefing_usage`, `users.created_at` |
| Never contributed | no succeeded donation, ever | `donation_ledger` |
| Not dismissed | no active dismissal | `app_prefs_json` |
| Cadence | see ladder below | `app_prefs_json` |
| Not RED | briefing assessment is not RED | client-side |

**Count distinct flights, not `briefing_usage` rows.** A usage row is written per
*refresh*; a pilot who hammers refresh on one flight racks up rows without having
had five briefings' worth of value. Use `briefing_usage` (never purged) rather
than `briefing_packs` (deleted by T2 retention) or `analytics_briefings_dim`
(undercounts ~2x).

**Suppress on RED.** Asking for money beside a serious-hazard assessment is bad
taste and competes with the safety content. Cheap to implement, non-negotiable.

### Repeat cadence

The trigger is **accumulated cost**, not elapsed time — a flat clock would nag a
pilot who briefs twice a year at the same rate as one who briefs three times a
week, despite a ~50x difference in what they consumed.

```
K = [1.5, 4, 10]
fire when true_lifetime_cost >= K × economics.cost_per_user_month_usd
     OR  months_since_last_ask >= 12
```

- **K=1.5** ≈ a month and a half of an average pilot's share
- **K=4** ≈ four average pilot-months
- **K=10** ≈ most of a year's worth

Calibrated against prod on 2026-08-31 — see "Measured economics" below. An
earlier `[3, 12, 30]` was **measured dead**: of 94 eligible pilots, K=12 caught
one and K=30 caught none (the largest eligible lifetime cost is $62, below
K=30's $85 threshold), so rungs 2 and 3 would never have fired and the 12-month
clause would have carried the whole cadence.

Each rung fires **once, ever** (`tier_asked` records the highest used), so there
are **at most three evergreen asks in a pilot's lifetime**. That cap is the real
fatigue guarantee; the rest is about placing those three well.

**"Maybe later" ends the ask, it does not snooze it.** The rung is consumed
(`tier_asked` advances) and the next ask is the next rung, potentially months
out. This is deliberate - a snooze that re-fires in a week is how a once-in-a-
lifetime-times-three ask turns into nagging.

**The 12-month fallback anchors on eligibility, not on null.** For a pilot who
has never been asked, `last_ask_at` is unset - and if that reads as "infinitely
long ago", the fallback clause fires for everyone eligible the day this ships,
making K=1.5 decorative for the whole existing base. Measured at rollout that is
the difference between **54 and 94** pilots asked in the first days. So the clock
runs from the date the pilot became eligible (passed flights + age), and the
cost ladder does the work until it matures.

Two floors on top:

- **90 days minimum between any two asks** — stops a heavy user tripping K=1.5
  and K=4 within weeks.
- **Engagement floor** (5 flights, 60 days) — stops a week-one burst firing rung 1.
- **One impression per calendar day**, across flights.
- **Max 4 impressions per ask** before it goes quiet — silent ignoring is an answer.

**Why multiples, not dollars.** `cost_per_user_month_usd` is
`monthly_run_cost / active_users`, recomputed live from the rate card and the
current pilot count. If ECMWF reprices or the base triples, the thresholds move
with it. Hardcoded `$2/$10/$25` would silently stop meaning anything, and nothing
would fail to say so.

**Why the `OR months >= 12` clause.** `cost_per_user_month_usd` is an average
dominated by *fixed* cost spread over all pilots, so "3 pilot-months" is a
share-of-the-whole unit, not 3x personal token spend. A genuinely light but loyal
pilot might take years to reach K=1.5, or never — exactly the cohort we most want
to reach. The time clause guarantees a yearly touch. **It becomes redundant once
campaigns ship** (the campaign delivers the annual rhythm); it is the stopgap
covering the period before the first campaign - which, with an April window, is
about seven months from now.

## Campaign ask (phase 2)

An annual window in **April**, at the start of the flying season. (Earlier
drafts anchored on the ECMWF renewal of 2026-03-27, then on the app's 2026-02-13
launch anniversary; both lose to seasonality, since fewer pilots brief in
winter. See the Copy section.)

```
campaign_active(now)
AND distinct_flights >= 5 AND account_age >= 14d
AND last_donation_at < campaign.opens - 9 months
AND not dismissed_this_campaign
AND assessment != RED
AND impressions_this_campaign < 4
```

The annual window *is* the fatigue control, so a campaign needs no backoff
ladder. Per-user state collapses to a single key that is discarded wholesale when
the campaign id changes — a stale `"campaign": "2026"` self-invalidates, so no
cleanup job.

The engagement floor is lower than evergreen's (14d vs 60d): a seasonal
collective ask needs less runway than an unprompted personal one.

**Donor suppression falls out for free.** "Donated since this campaign opened" ⇒
suppressed for this campaign, asked again next year. That *is* the annual
cadence, with no separate rolling window. The 9-month lookback catches someone
who gave off-cycle via the Settings link so they aren't asked five months later,
while still asking last year's campaign donors.

**The campaign needs no recency gate; the surface is self-gating.** The chip only
renders on the briefing page, so anyone who sees it is opening a briefing at that
moment - a dormant user never loads the page. Measured 2026-09-01, an explicit
recency condition is nearly a no-op: of 112 pilots passing the campaign gate,
112 had briefed within 180 days and 101 within 90, because only **9 of 750**
users have gone more than 180 days without a briefing. The gate that does the
work is the flight count: >=1 flight admits 523 pilots, >=5 admits 112, so the
threshold is what keeps a one-and-done user who wanders back in April from being
asked.


Config via env, matching the `STRIPE_SECRET_KEY` operational shape (set +
restart, no redeploy; off entirely when unset):

```
WB_DONATE_CAMPAIGN=2027:2027-04-05..2027-04-26
```

## Ask lifecycle — from gate to click

Three layers. An ask *opens* when the gate passes, *renders* on qualifying page
views, and *closes* on an answer, an exhausted budget, or a backstop.

### Layer 1 — does an ask exist? (server-side)

| # | Condition |
|---|---|
| 1 | `stripe_configured()` |
| 2 | `COUNT(DISTINCT flight_id) >= 5` |
| 3 | account age >= 60 days |
| 4 | never donated |
| 5 | cost rung crossed (`true_lifetime_cost >= K x cost_per_user_month_usd`) OR 12 months since eligibility |
| 6 | >= 90 days since the last ask |

All six pass ⇒ one ask **opens**. An open ask does not expire on its own.

### Layer 2 — does the chip render on this page view?

| # | Condition |
|---|---|
| 7 | assessment is not RED |
| 8 | no impression already recorded today |
| 9 | fewer than 4 impressions used on this ask |

### Layer 3 — the click

Chip → popover → `Contribute` (to `/donate.html`) or `Maybe later`. Both close
the ask.

### How long the button stays

**Not until they click.** The ask carries an impression budget of **4, at most
one per day**, then goes quiet by itself. Against measured briefing frequency in
the eligible cohort (2026-09-01):

| Pilot | Briefings/month | 4 impressions takes |
|---|---|---|
| Median | 7.4 | ~16 days |
| Light (p25) | 3.1 | ~39 days |

So an ignored ask resolves in roughly two to six weeks of normal use with no
calendar rule needed. Add a **90-day backstop** anyway for the pilot who briefs
once a month, whose four impressions would otherwise stretch past four months.

**Silence is an answer.** An ask that burns its budget unclicked consumes the
rung exactly as "Maybe later" does. Ignoring therefore costs one of the three
lifetime asks and buys months of quiet - which is the behaviour we want, and the
reason no ask ever needs to escalate.

### Closing conditions

| Trigger | Rung consumed? |
|---|---|
| `Contribute` clicked | yes (and a donation ends the evergreen path entirely) |
| `Maybe later` clicked | yes |
| 4 impressions used, never clicked | yes |
| 90-day backstop elapsed | yes |
| Campaign window closes | yes, for that campaign |
| Popover opened, then dismissed with Esc / click-outside | **no** - they answered nothing; but the impression already counted, so it self-limits |

The campaign carries a second bound: the ~3-week window, so it is effectively
4 impressions *or* window close, whichever comes first.

## Measured economics (prod, 2026-08-31)

Everything above is calibrated against these. Re-check when the rate card or the
pilot base moves materially — the thresholds are relative, so they track
automatically, but the *cohort sizes* below will drift.

| Quantity | Value |
|---|---|
| Fixed monthly | **$250** (droplet 48 + misc 2 + Open-Meteo 30 + ECMWF 170) |
| Variable monthly (tokens + storage) | **$87.82** |
| **Run cost** (margin-excluded) | **$337.82 / month** |
| Active pilots (30d) | **119** |
| Briefings (30d) | **1632** |
| **`cost_per_user_month_usd`** | **$2.84** |
| `cost_per_briefing_usd` | $0.207 |
| Donors (all time) | 17 |

Cohort funnel — 539 users have billed briefings, but the base is dominated by
one-and-done users (population median lifetime cost **$0.38**, p25 $0.19):

| Filter | Count |
|---|---|
| Users with billed briefings | 539 |
| ...minus donors | 522 |
| **Eligible** (≥5 distinct flights, ≥60d, never donated) | **94** |

Eligible cohort's true lifetime cost: p25 $2.27, **median $5.72**, p75 $12.34,
p90 $22.29, max $62.43. Burn rate: median **$1.51/month**, p75 $3.76.

Ladder coverage against that cohort, and months-of-use to reach each rung at the
median burn:

| Rung | Threshold | Eligible pilots at or above | Months at median burn |
|---|---|---|---|
| K=1.5 | $4.26 | 54 (57%) | ~2.8 |
| K=4 | $11.36 | 24 (26%) | ~7.5 |
| K=10 | $28.39 | 4 (4%) | ~19 |

So a median eligible pilot gets rung 1 fairly soon and rung 2 within the year;
only genuinely heavy users ever see rung 3. That is the intended shape.

## Composition

One shared `last_ask_at` governs both asks, so a campaign impression silently
pushes the evergreen 90-day floor out. No coordination logic needed.

Donating at any point fails the never-contributed gate permanently, so a donor
leaves the evergreen path for good and only ever sees campaigns.

## State

One key in `app_prefs_json` (`UserPreferencesRow`, flyfun-common). No migration.

```json
{"donate_nudge": {
  "last_ask_at": "2026-08-31",
  "asks": 2,
  "tier_asked": 4,
  "open_ask": {"kind": "evergreen", "opened": "2026-08-20",
               "shown": 2, "last_shown": "2026-08-31"},
  "campaign": {"id": "2027", "dismissed": true, "shown": 3, "last_shown": "..."}
}}
```

`open_ask` is the live one; it is cleared on every closing condition in the
lifecycle table, and `opened` is what the 90-day backstop measures from.
`tier_asked` records the highest K rung consumed, so it advances whether the
pilot clicked, dismissed, or ignored.

Impressions are recorded by the client `POST`ing an ack when the chip actually
renders — a GET must not write, and a prefetch must not burn an impression. The
server upsert is idempotent within the day cap.

Emit analytics events (`donate.nudge_shown` / `_clicked` / `_dismissed`) for
measurement, but keep the **gating** state in prefs: analytics rows get rolled up
and pruned.

## Framing — reuse `impact.py`, do not write copy

### The gap

`personal_impact()` returns `empty=True` when `donation_total_usd <= 0`
(`impact.py:340`), and `renderPersonal()` early-returns at `me.total_usd <= 0`
(`web/ts/donate-main.ts:177`). **A pilot who has never donated sees no personal
panel on the donate page at all.**

The "your usage / N pilots / N briefings" reframing is `choose_translation()`,
and it only fires when the user *types an amount into the form*
(`previewAmount`, debounced) — it is prospective, never retrospective, never
unprompted. Its personal path already consumes a `burn_rate_monthly_usd` from
`api/credits.py`, so the per-user cost is already computed and already on the
wire. It is simply never shown to the people the nudge targets. (As built, that
burn rate comes from `user_usage_stats` on the recomputed basis; the
ledger-derived `user_cost_stats` it replaced has been deleted.)

### The fix

Feed the pilot's **own lifetime cost** through the ladder that already exists:

```python
choose_translation(amount_usd=true_lifetime_cost, economics, burn_rate)
```

A donation equal to what you have used is the honest ask, so this yields both
the framing *and* a suggested amount, in the existing vocabulary ("covers ~4
months of your own usage", "funds ~12 briefings", "covers ~2 pilots for a
month") — already i18n-shaped, already under the offset/cover verb policy,
already tested.

`usage_footprint()` is therefore a thin wrapper in `impact.py`, not a new
subsystem.

### The one piece of real math — the ledger is not the true cost

`lifetime_cost_usd` sums `cost_ledger.cost`, which is **not** what the usage
actually cost the operator. `compute_cost` amortizes fixed cost over
`max(estimated_monthly_briefings, 500)` and then adds margin. The active rate
card carries `estimated_monthly_briefings = 500` while prod is running **1632
briefings/month**, so every briefing is billed a $0.50 fixed share against a
true share of $0.153.

Measured on 2026-08-31, the arithmetic closes exactly:

```
1632 x $0.50  +  $83.60 tokens  +  $4.22 storage  =  $903.82
$903.82 x 1.10 margin                             =  $994.20   <- charged sum
true run cost: $250 fixed + $87.82 variable       =  $337.82
                                        over-recovery: 2.94x
```

So **de-margining is not the fix**. `margin_percent` is 10 in the active card
(not the 30.0 dataclass default), and the margin is the *small* part of the
distortion - the stale volume estimate is ~2.7x of it. Dividing by
`(1 + margin_percent/100)`, as an earlier draft of this plan proposed, would
still overstate the ask by nearly 3x.

`usage_footprint()` must therefore recompute from the true basis rather than
scale the ledger:

```python
true_cost = own_variable_usd + (fixed_monthly_usd / briefings_per_month) * own_briefings
```

Both inputs already exist on the program report. `own_variable_usd` is the sum
of `token_cost_usd + storage_cost_usd` from the user's `detail_json` rows - the
same parse `_program_variable_and_counts` already does program-wide, so treat a
missing key as unknown, never `0.0`.

### This is a live bug on the donate page, not just a nudge concern

The same 2.94x inflation flows into `personal_impact()`, whose
`coverage_ratio = donation_total / lifetime_cost_usd` therefore reads **~3x too
low**. Donors are currently being told they covered a smaller fraction of their
own usage than they really did, and the `covers_others` / `future` bands unlock
later than they should. Worth fixing on its own account.

`estimated_monthly_briefings` was bumped 500 -> 1600 in the rate card on
2026-08-31 (cost_config v4), so the fixed share billed per briefing is now
$0.156 against a true $0.153 - within ~2%, plus the 10% margin.

**That makes recomputation more necessary, not less.** The ~7k rows written
before the bump carry a $0.50 fixed share; rows after it carry $0.156. The
ledger is therefore no longer internally consistent over time - a pilot's
lifetime sum is now a blend of two amortization bases, weighted by when they
happened to fly. Only a recomputed `true_cost` is comparable across users.

Note the error direction also flipped: with est now slightly *above* actual
volume, a fall in volume would make the ledger *understate*. For a donation ask
that is the safe direction - never overstate what someone's usage cost.

### Side benefit

The same call closes the never-donor hole on `donate.html`. Drop the
early-return and a pilot who has never given still sees what their usage cost and
what it maps to, instead of a blank section. **This is the more valuable half of
the change** — it stands on its own even if the nudge never ships, and the nudge
is pointless without it (it would point at a page with nothing to say to the
person it just nudged).

## UI

The toolbar slot already solves the space problem. `.digest-feedback-slot` is
`position: relative` with the comment form as an absolutely-positioned 300px
popover (`web/css/style.css:1689–1723`; slot at `web/briefing.html:67`, rendered
by `renderDigestFeedbackToolbar`, `web/ts/managers/briefing-ui.ts:2281`).

Inline cost is **one chip**, placed to the **left** of the feedback label and
thumbs:

```
♥ Donate   Was this useful?  👍  👎
```

**Left, not right, so the thumbs never move** (operator decision, 2026-09-01).
`.digest-feedback-slot` carries `margin-left: auto` (`style.css:1689`) inside a
`display:flex` toolbar, so the group is anchored to the toolbar's **right** edge
and grows leftward. A chip added to the right of the thumbs widens the group and
pushes the thumbs left by its width; a chip added to the left grows into empty
toolbar space and leaves the thumbs exactly where they were. Left placement is
the only option that adds an element without displacing a permanent one.

### The chip needs its own slot, not a place inside the existing one

`renderDigestFeedbackToolbar` assigns `slot.innerHTML = digestFeedbackHtml(...)`
on every render, and `clearDigestFeedbackToolbar` sets `display:none` on the
whole slot when no digest is shown. A chip living inside that slot would be
destroyed on re-render and hidden whenever a digest is absent - though the
donation ask has nothing to do with whether a digest exists.

So: a sibling slot, plus a shared wrapper that takes over the right-anchoring
(leaving `margin-left:auto` on the feedback slot alone would push it to the far
right and strand the chip mid-toolbar):

```html
<div class="toolbar-feedback-group">          <!-- margin-left: auto moves here -->
  <div id="donate-nudge-slot"   style="display:none;"></div>
  <div id="digest-feedback-slot" class="digest-feedback-slot" style="display:none;"></div>
</div>
```

Right edge pinned, thumbs immovable, chip grows into free space, two independent
lifecycles.

### Popover anchoring

Anchor the donate popover `right: 0` on the wrapper, matching
`.digest-feedback-slot .digest-feedback-form` (`style.css:1705`). Anchoring it
`left: 0` to the chip would read as more directly attached, but the chip sits at
the *left* edge of a right-anchored group, so a 300px popover could overflow the
viewport on narrow layouts. The group's own width is close to the popover's, so
`right: 0` lands it under the group either way.

### Accepted tradeoff

In LTR reading order the chip now precedes "Was this useful?", which slightly
promotes the ask over the feedback prompt. Accepted: the thumbs are the primary
action and stay visually primary, the chip is rare, and layout stability on a
page pilots read under time pressure matters more than reading order.

Then the popover, reusing the existing geometry:

```
┌────────────────────────────────────────┐
│  You've had 23 briefings since April.  │
│  We hope you found them useful.        │
│                                        │
│  Flyfun Weather is free and stays      │
│  free. If you'd like to help offset    │
│  what it costs to run (AI, data,       │
│  servers), anything is welcome.        │
│                                        │
│           [ Contribute ]               │
│                                        │
│              Maybe later               │
└────────────────────────────────────────┘
```

**No "I already donated" link.** It was proposed as the escape hatch for
anonymous donors (`DonationRow.user_id` is nullable, so a logged-out donation is
invisible to the gate). Dropped: pilots are effectively always logged in when
they reach the donate page, so the hole is theoretical and not worth the UI
weight or the extra state field.

### The label: `♥ Donate`

One label, always, in every state. Fixed text so the chip never changes width -
an earlier draft had it expand once a 👍 collapsed the thumbs to "Thanks!",
which would have reintroduced exactly the layout shift the left placement exists
to prevent.

- **"Donate", not "Support"** (operator decision, 2026-09-01). In a software
  toolbar "Support" reads as *help desk* - and this toolbar already carries a
  tour button (`?`, "Take the tour") with a help catalog behind it, so the
  collision is live rather than theoretical. A pilot clicking what looks like
  help and getting a donation ask is a small bait-and-switch even though nothing
  dishonest happened. "Donate" has no competing meaning in a UI.
- The vocabulary-consistency argument for "Support" (the donate page's `<h1>` is
  "Support Flyfun Weather", the Settings entry is a "Support" link) does not
  carry: that phrasing works as a page title because the reader navigated there
  deliberately and the verb has its object in the sentence. A bare toolbar chip
  has neither.
- **"Donate" is not too transactional.** A donation is definitionally not a
  purchase, and everything that keeps it that way - no perks, coverage framing
  instead of amounts, the VAT constraint - lives in the popover and the donate
  page. The label does not have to carry it.
- **`♥` (U+2665), not `❤️` (emoji).** The text glyph inherits `currentColor`, so
  it takes the toolbar's text colour and follows light/dark themes. The emoji is
  a fixed-colour platform image that matches nothing and differs per OS. The
  heart also softens the ask before the word is read.
- The chip stays visually subordinate to the 👍👎 emoji beside it, which is
  correct - the thumbs are the primary action.
- The popover's primary button stays **Contribute**: the chip opens the ask, the
  button leaves for `/donate.html`. Two `Donate`s in one interaction would read
  as a repeated demand rather than two steps.

Do **not** auto-open the popover after a 👍. Opening a donation ask the instant
someone gives feedback reads as a bait-and-switch.

Check narrow-viewport wrapping of the new `toolbar-feedback-group` when adding
the chip - `.briefing-toolbar` is `flex-wrap: wrap`, so the wrapper can drop to
its own line on narrow layouts, where `right: 0` popover anchoring still holds.

## Copy (operator-approved 2026-09-01 — do not rewrite)

Both live as i18n keys (`en`/`fr`/`de`/`es` locale files), not inline strings.
`{braced}` fields are runtime values; everything else is fixed text. **Neither
variant carries a money figure** (operator decision, 2026-09-01), so no `fx`
block and no currency formatting is involved in the popover at all - one less
thing to get wrong in four locales.

Verb policy applies to both (from `impact.py`): **offset / contribute / cover /
help cover**. Never "pay for", never "fund your next N months", never a perk or
anything that reads as buying something — a perk turns a donation into a
digital-services supply and triggers EU OSS VAT.

### Evergreen — "your usage so far"

Chip label: **♥ Donate**

> You've had **{briefing_count} briefings** since {first_month}. We hope you
> found them useful.
>
> Flyfun Weather is free and stays free. If you'd like to help offset what it
> costs to run (AI, data, servers), anything is welcome.
>
> **[ Contribute ]**   ·   Maybe later

- `{briefing_count}` — count of the pilot's `cost_ledger` briefing rows.
- `{first_month}` — month of their first briefing ("April").

**No cost figure and no coverage phrasing here** (operator decision,
2026-09-01). The popover is a hook; the donate page already carries the amount,
the coverage ladder, the stats trio and the run cost. Two sentences, two
substitutions.

This also settles the bespoke-vs-reuse tension: the text is bespoke but trivial
(no money, no coverage claim), so there is no `impact.py` phrasing to keep in
step and nothing for the verb policy to police beyond "offset", which is already
in family. Four locales, two short strings.

**The true-cost work is still required** — it moved from the text to the
trigger. `usage_footprint()` no longer supplies a number to *display*, but the
K ladder still gates on `true_lifetime_cost >= K x cost_per_user_month_usd`, and
that comparison is only meaningful on a recomputed basis (the ledger is a blend
of two amortization bases across the 2026-08-31 rate-card bump). Do not drop
step 1 of the build order on the grounds that the copy no longer shows a figure.

### Campaign — the yearly ask

Chip label: **♥ Donate**

> Another year of FlyFun Weather is starting. **{n_pilots}** pilots generated
> **{n_briefings}** briefings over the last year.
>
> It's free and stays free. Running it costs real money: weather data like the
> ECMWF subscription, AI models, and servers. If you'd like to help offset that,
> anything is welcome.
>
> **[ Contribute ]**   ·   Maybe later

- `{n_pilots}` / `{n_briefings}` — distinct users and briefing count over the
  trailing 365 days, from `briefing_usage`. **This window does not exist yet:**
  `/api/donations/summary` serves `active_pilots_30d`, `briefings_all_time` and
  `briefings_last_30d` only, so the 12-month pair is a small code addition.
  Count `briefing_usage` (never purged), never `briefing_packs` (T2 retention
  deletes rows, so history would shrink over time) and never
  `analytics_briefings_dim` (undercounts ~2x). For scale: 546 pilots / 7,188
  briefings as of 2026-09-01, which is also all-time since the app is 6.5 months
  old.
- Suppress the stats sentence entirely if either figure is missing or
  implausibly small, the same way `impact.py` returns neutral empty states
  rather than rendering a number that undercuts the point.

**Community stats are not the money figures the popover rule bans.** The
no-figures decision was about *cost*: a euro amount in a donation ask sets an
anchor and invites the reader to price their own share. Activity stats do the
opposite - they give scale without asking anyone to compute anything. Keep cost
out, keep these in.

**Window is April, set by the flying season** (operator decision, 2026-09-01).
The anniversary framing survives but the date does not: the app launched
2026-02-13 and ECMWF was ordered 2026-03-27, yet fewer pilots fly in winter, so
an ask in February or March reaches a thinner, less engaged audience. April is
the start of the season. Since the chip only appears on the briefing page, a
window placed where more pilots are actually briefing converts into more
impressions with no change to the rules.

**"Another year is starting" is forward-looking on purpose.** It asserts nothing
about a specific anniversary date, which is what lets the window sit in April
while the app launched in February. "It's been another year" would be a small
factual stretch there; "another year is starting" is simply true, and stays true
whenever the window is placed.

**ECMWF appears as an example, never as the trigger.** Naming a specific vendor
as the reason for asking edges toward "we need money for X" — which is not true
(the service is sponsored and not at risk) and would date badly if the vendor
mix changed. As one item in a list it is concrete and credible to GA pilots.

**No first-year special case, no `{n}` substitution.** One string, true every
year, nothing to rewrite each April.

**No figures in the popover**, matching the evergreen decision. The donate page
carries the run cost, the stats trio and community coverage.

Note this copy makes no claim about the individual pilot, so the low
`account_age >= 14d` campaign gate is safe: a pilot who joined last month still
reads it as true.

### Constraints if this is ever revised

- Keep it to ~3 short lines. The popover is a hook, not the pitch — the donate
  page carries the stats trio, community coverage and the amount ladder.
- No *money* figures in either variant. Community activity stats (pilots,
  briefings) are fine and deliberate; a cost or donation amount is not. If a
  money number feels needed, that is a signal the donate page should be doing
  the work instead.
- No urgency language, no guilt, no "help us survive". The service is sponsored
  and not at risk; saying otherwise would be false.
- Nothing here may imply a benefit in return.

## API surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/donations/nudge` | GET | `{show, kind: "evergreen"\|"campaign", summary, reason}` — web-only |
| `/api/donations/nudge/ack` | POST | `{action: "shown"\|"dismissed"\|"clicked"}` |

## Known gap to close first

The webhook (`api/donations.py:264`) handles `checkout.session.completed`,
`charge.updated` and `charge.refunded` — but **not `invoice.payment_succeeded`**.
A recurring donor's renewals therefore never write ledger rows; only the pledge
start does. Any time-windowed donor suppression would start nagging an actively
paying subscriber once the window passed the initial charge.

Two defences, do both:

1. Treat `recurring=True` as **indefinite** suppression until cancellation is tracked.
2. Add the `invoice.payment_succeeded` handler so renewals are recorded.

Refunded donations already fall out correctly — the shared helpers filter
`status == "succeeded"`.

## Implementation traps

### The gate must short-circuit before touching economics

`_economics()` (`api/donations.py:92`) calls `build_program_report()` ->
`_program_variable_and_counts()` (`api/credits.py:394`), which **fetches and
JSON-parses every briefing ledger row in the 30-day window** - ~1,632 rows as of
2026-09-01. That is fine today because it only runs on donate-page loads, which
are rare and deliberate. The nudge endpoint would run on **every briefing page
load**, the hottest page in the app.

Two defences, do both:

1. **Order the gate cheap-first.** Prefs blob (is an ask open? asked today?),
   donation existence, flight count and account age are all cheap indexed reads,
   and only ~112 pilots pass them. Compute `cost_per_user_month_usd` only for a
   pilot who has already cleared everything else.
2. **Cache the economics.** It moves slowly - a TTL of an hour or more is ample,
   and the app already has response-cache precedent.

A nudge check must never make a briefing page slower to load. If it cannot be
made cheap, it should return "no ask" rather than block.

### Analytics events must be registered, not just emitted

`analytics/events.py` rejects any event name not in `ALLOWED_EVENTS`, and the
`Event` enum has to be mirrored in `web/ts/analytics/events.ts`. Adding
`donate.nudge_shown` / `_clicked` / `_dismissed` means editing both files;
emitting them without registering silently fails at ingest.

Keep `props` low-cardinality per that module's conventions (e.g. `{kind:
"evergreen"|"campaign", rung: 1|2|3}`) - no user ids, no timestamps, no amounts.

### RED suppression is client-side, so the ack must be too

The server does not know the briefing's assessment; the client does. So the
client decides not to render on RED - and **must not send the `shown` ack when
it suppresses**, or impressions burn on page views that displayed nothing. Same
rule for any other client-side reason the chip does not paint.

### Test coverage worth having

- `usage_footprint()` true-cost arithmetic, including a row whose `detail_json`
  lacks a key (must read unknown, never `0.0`) and the pre/post rate-card-bump
  blend.
- Gate truth table: each condition failing in isolation.
- Lifecycle: impression cap, one-per-day, ignore-consumes-rung, backstop, and
  popover-dismissed-without-choosing not consuming.
- A donation mid-ask closes the open ask.

## Rejected alternatives

- **Hardcoded dollar tiers ($2 / $10 / $25).** Stale the moment ECMWF reprices or
  the user base grows, with no failure signal. Replaced by multiples of
  `cost_per_user_month_usd`.
- **A 6-month rolling donor-suppression window.** Too frequent (operator call),
  and it fights the annual shape of both the costs and the flying season.
- **Campaign-only, no evergreen.** Leaves a pilot who joins in month 8 waiting
  until April, and does nothing today since no campaign is configured.
- **Evergreen-only, no campaign.** Loses the honest, well-timed collective ask
  and the natural annual rhythm.
- **A dismissible banner under the toolbar.** More presence, but banners train
  reflexive dismissal; a once-a-year chip persisting across the window is seen anyway.
- **Gating the ask on a 👍.** Converts better and is self-limiting, but loses most
  of the audience — wrong trade for an ask that fires at most three times ever.
- **Reusing `impact.py` phrasing verbatim in the popover.** Was the plan while
  the copy showed a cost figure, to avoid a second place to edit text under the
  verb policy. Overtaken: the operator cut all figures and coverage claims, so
  each variant is now two short sentences with nothing for `impact.py` to own.
  The reuse argument still holds for the donate page, where the numbers live.

## Build order

1. **`impact.py`: `usage_footprint()` + true-cost basis**, with tests. Pure, no DB.
2. ~~**Rate card: `estimated_monthly_briefings` 500 -> 1600**~~ — **done 2026-08-31**, cost_config v4.
3. **`donate.html` never-donor panel** — drop the `total_usd <= 0` early-return.
   Ships value on its own.
4. **`invoice.payment_succeeded` webhook handler** + `recurring` suppression rule.
5. **Nudge endpoint + ack + prefs state**, evergreen path only.
6. **Chip + popover** in the briefing toolbar; analytics events.
7. **Campaign config + window logic** — before April. Includes the trailing-365d
   pilots/briefings pair the campaign copy needs (`/api/donations/summary`
   currently has 30d and all-time only).

## Settled

- **60 days + 5 distinct flights** is the engagement floor (operator confirmed
  2026-09-01). It was the one threshold picked by judgement rather than
  measurement; it stands. For scale it admits 94 pilots, of whom 54 also clear
  the K=1.5 cost rung at rollout.

No open items.


## As built — deviations from this plan

Everything above is the plan as agreed. These are the places the implementation
deliberately does something else, and why. Each is a decision, not a shortcut.

### The never-donor panel does not come from `personal_impact()`

The plan (and issue #588 step 3) says to drop the `donation_total_usd <= 0`
early-return in `personal_impact()`. Taken literally that produces "covers ~0%
of what your usage has cost" for someone who has given nothing — a coverage
sentence about a donation that does not exist.

So `personal_impact()` keeps its neutral empty state, which is the honest
answer to "what does your donation cover" when there is no donation. The panel
is instead driven by a new `usage` block on `/api/donations/me`
(`UsageFootprint` + a `choose_translation` caption), and `renderPersonal()` in
`web/ts/donate-main.ts` branches on `total_usd > 0` to pick which of the two it
renders. The hole the step existed to close — a blank section on the page the
nudge points at — is closed; the coverage vocabulary just stays reserved for
actual coverage.

### An unreadable breakdown row is dropped, not imputed

`usage_footprint()` treats a `detail_json` that is missing, unparseable, or
lacking either of `token_cost_usd` / `storage_cost_usd` as **unknown**, per the
plan. Concretely it excludes that row from `variable_usd`, still charges it its
fixed share (the briefing demonstrably ran), and reports the count in
`unknown_variable_rows`.

The alternative — imputing the mean of the readable rows — is more accurate on
average but can only *overstate* a particular pilot's cost, which is the wrong
direction for a donation ask. Dropping understates, and the error is bounded by
the variable share (~26% of run cost).

### The ledger sum is used as a cheap upper bound before the parse

The plan requires ordering the gate cheap-first. One more short-circuit was
added between the cheap reads and `usage_footprint()`: a single SQL `SUM` of the
pilot's `cost_ledger.cost`, compared against `RUNGS[0] * cost_per_user_month`.

The ledger amortizes fixed cost over an estimate at or below actual volume and
then adds 10% margin, so `ledger_cost >= true_cost` holds for every row ever
written. A pilot whose ledger total misses the lowest rung therefore cannot
clear it on the true basis either, and their per-row breakdown is never parsed.
**If the rate card's `estimated_monthly_briefings` is ever set materially above
actual volume, that inequality flips and the pre-filter starts hiding eligible
pilots** — it fails closed (no ask), never open.

### The ladder is climbed from the bottom, not from the highest rung crossed

`tier_asked` records the highest rung consumed, as planned. What fires is the
**lowest unused rung that has been crossed**, so a pilot who is already past
K=4 on rollout day still gets K=1.5 first and K=4 no sooner than 90 days later.

Firing the highest crossed rung instead would consume two of the three lifetime
asks in one go, for exactly the pilots who use the service most. It would also
make the plan's own justification for the 90-day floor ("stops a heavy user
tripping K=1.5 and K=4 within weeks") describe something that could not happen.

### A backstopped ask arms the 90-day floor even if it was never shown

The closing-conditions table says the 90-day backstop consumes the rung. It does
— and it also stamps `last_ask_at`. Without that, an ask that opened on a
prefetch and expired unseen would consume its rung *and* leave the floor
unarmed, letting the next rung open on the very next page view: three lifetime
asks burned in three days by a pilot who never saw one.

### The GET persists lifecycle transitions

"A GET must not write" is implemented as "a GET must not record an impression".
`GET /api/donations/nudge` does persist an ask *opening* and an expired ask
*closing*, because `opened` is what the backstop measures from and `tier_asked`
is what stops the ladder repeating. Impressions come only from
`POST /nudge/ack`, so a prefetch cannot burn one.

A prefetch can therefore open an ask that is never shown. That is the intended
reading of "an ask opens when the gate passes": the rung is consumed at open
regardless of what happens next, and the ask renders on the next non-RED view.

### Recurring donations are still refused at checkout

Step 4's `invoice.payment_succeeded` handler is built and tested, but
`POST /donations/checkout` still rejects `recurring=true` (422). The handler is
the *prerequisite* for lifting that, not the whole of it — subscription
cancellation is still untracked.

Renewal **attribution** also needs a change in flyfun-common that is out of this
repo's reach: `create_checkout_session` sets `metadata` on the Checkout session,
and Stripe does not copy session metadata onto the subscription, so a renewal
invoice carries no `user_id` unless `subscription_data.metadata` is set at
creation. The handler reads every place the metadata could legitimately land and
records the donation anonymously when it finds none — real money still counts
toward the community total. **This is why the gate suppresses any recurring
donor indefinitely rather than on a window**: it must not depend on
attribution that may not arrive.

### A pilot's true lifetime cost drifts with program volume

`true_cost` amortizes *historical* briefings at *current* monthly volume, so the
same pilot's figure falls as the service grows. This is deliberate and matches
the rungs, which are multiples of `cost_per_user_month_usd` and move the same
way — but it means the number on the donate page is "what your usage costs to
run at today's scale", not a frozen historical total. Worth knowing before
anyone treats it as an invoice.
