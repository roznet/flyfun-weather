# Cost Attribution

> Full transparency on what each briefing costs to produce — infrastructure, API calls, LLM tokens, storage

## Intent

Radical cost transparency, not monetization. Every briefing's real cost is computed in USD and shown to users. The service is free and sponsored by the operator. If costs outgrow sponsorship, voluntary **donations** offset them (Stripe, web-only, multi-currency — see [Donations (Stripe)](#donations-stripe--planned) below); users always see actual costs, in their local currency. Donations are tracked in a dedicated `donation_ledger`, **not** `spending_limit` (which is an unrelated per-user *spend* balance).

## Architecture

```
costs.py                  — Pure cost computation (no DB/IO): per-briefing + program-wide
api/credits.py            — Cost API: charge, query, admin config, program report, transparency
api/packs.py              — Hook: _charge_briefing_cost() called after each refresh
db/models.py              — CostConfigRow (weather-specific rate card)
web/ts/admin-cost-view.ts — Admin "Cost" tab: program report + rate-card editor
web/cost-summary.html     — Program cost + viewer's own usage (admin-gated; → Settings later)
tests/test_costs.py       — Formula arithmetic tests (per-briefing + program)
tests/test_cost_report_api.py — Report endpoint + config auto-sum integration tests
```

The ledger itself (`CostLedgerRow`) lives in **flyfun-common** — shared across all apps. Weather writes to it via `flyfun_common.costs.record_cost()`.

### Data Flow

```
Briefing refresh completes
  → _measure_pack_size() calculates artifact bytes
  → log_briefing_usage() records usage row (incl. result_size_bytes)
  → _charge_briefing_cost():
      1. Load active CostConfigRow
      2. compute_cost() → CostBreakdown (pure math)
      3. charge_briefing() → record_cost() to shared cost_ledger
      4. Deduct from UserRow.spending_limit; auto-reload if ≤ 0
```

Cost computation failures are caught and logged — they never block the briefing.

> Step 4 is **slated for removal** — nothing reads `spending_limit` to gate anything.
> See [Donations → Retiring `spending_limit`](#what-spending_limit-is-and-is-not).

## Cost Formula

```python
token_cost = (input_tokens / 1000) × rate_input
           + (output_tokens / 1000) × rate_output

est = max(estimated_monthly_briefings, 500)   # floor
infra_share = (droplet_monthly + misc_monthly) / est
subscription_share = subscriptions_monthly / est
storage_cost = (result_size_bytes / 1 GB) × disk_cost_per_gb_monthly

subtotal = token + infra + subscription + storage
margin = subtotal × (margin_percent / 100)
total_usd = subtotal + margin
```

## Program Cost Report

The per-briefing formula amortizes fixed cost over a *fixed estimate* (`est`), so the
amortized share rarely matches what the operator actually pays. For program-wide
reporting (admin "Cost" tab + the cost-summary page) we instead compute fixed and
variable cost separately, in `compute_program_cost()`:

```python
months = window_days / 30
fixed_prorated = (droplet + misc + Σ subscription_details) × months   # real cost, prorated
variable       = Σ (token_cost_usd + storage_cost_usd) over the window  # actuals from ledger
subtotal       = fixed_prorated + variable
margin         = subtotal × (margin_percent / 100)                      # margin kept (decision)
total          = subtotal + margin
cost_per_briefing = total / num_briefings   # 0 when none
cost_per_user     = total / num_users        # distinct briefing users in window
```

- **Fixed** comes straight from the rate card, prorated by `window_days/30` and broken
  out per line (server, misc, each `subscription_details` item). Decoupled from the
  per-briefing amortization so it reflects reality.
- **Variable** is the actual token + storage cost summed from each briefing's stored
  `detail_json` over the window — independent of the current rate card.
- **Margin is included** in headline totals (product decision — keeps reported numbers
  consistent with the ledger's intent).
- At low briefing volume, `cost_per_briefing` is dominated by fixed cost (e.g. 40
  briefings/mo → ~$1.89 each vs the ~$0.12 amortized per-briefing charge). That gap is
  the point of the report.

`ProgramCostReport` (frozen dataclass) → `program_report_to_dict()` for the API.

## DB Schema

### cost_ledger (shared, in flyfun-common)

| Column | Type | Notes |
|--------|------|-------|
| id | INT PK | auto |
| user_id | VARCHAR(64) | indexed, no FK (survives user deletion) |
| service | VARCHAR(64) | `"flyfun-weather"`, `"flyfun-maps"`, `"flyfun-forms"` |
| action | VARCHAR(64) | `"briefing"`, `"topup"`, `"chat"`, `"generate"` |
| cost | FLOAT | Always positive USD. Topups have cost=0.0 |
| metadata_json | TEXT NULL | Lightweight context (token counts, model) |
| created_at | DATETIME(tz) | |
| category | VARCHAR(32) NULL | `"briefing"`, `"topup"` |
| description | VARCHAR(256) NULL | Human-readable |
| detail_json | TEXT NULL | Rich breakdown (weather's CostBreakdown JSON) |
| reference_id | VARCHAR(128) NULL | App-level FK as string (e.g. briefing_usage_id) |

### cost_config (weather-specific, versioned admin rate card)

| Column | Type | Default |
|--------|------|---------|
| id | INT PK | auto |
| active_from | DATETIME(tz) | now() |
| active_until | DATETIME(tz) | NULL = currently active |
| config_json | TEXT | serialized `CostConfig` |

Since migration 028 the rate card lives entirely in `config_json` (one row per
version) — adding a cost dimension means adding a field to the `CostConfig` dataclass,
no migration. The serialized `CostConfig` keys: `token_cost_per_1k_input`,
`token_cost_per_1k_output`, `droplet_monthly_usd`, `misc_monthly_usd`,
`subscriptions_monthly_usd`, `subscription_details` (dict, itemized),
`disk_cost_per_gb_monthly`, `estimated_monthly_briefings`, `margin_percent`.

Versioned: updating creates a new row, deactivates the previous. History queryable.
`subscriptions_monthly_usd` is kept in sync server-side as the sum of
`subscription_details` on PUT.

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/user/credits` | GET | User | Cost summary: total, this month, this week, recent transactions |
| `/api/admin/cost-config` | GET | Admin | Current active config |
| `/api/admin/cost-config` | PUT | Admin | Create new config version (auto-sums subscriptions) |
| `/api/admin/cost-config/history` | GET | Admin | All config versions |
| `/api/admin/cost-report` | GET | Admin | Program-wide report (`?window=7d\|30d`): fixed/variable/total + per-briefing/per-user |
| `/api/admin/users/{id}/costs` | GET | Admin | Per-user cost detail |
| `/api/transparency` | GET | None | Public pricing structure |

### GET /api/user/credits response

```json
{
  "total_cost_usd": 3.17,
  "cost_this_month_usd": 1.42,
  "cost_this_week_usd": 0.38,
  "total_briefings": 13,
  "recent_transactions": [
    {"id": 1, "timestamp": "...", "cost_usd": 0.1976, "category": "briefing",
     "description": "Briefing ($0.1976)", "breakdown": {...}}
  ]
}
```

## Frontend Integration

- **adapters/credits-adapter.ts**: `fetchCostSummary()`, `fetchTransparency()` — typed API client
- **adapters/admin-adapter.ts**: `fetchCostReport()`, `fetchCostConfig()`, `fetchCostConfigHistory()`, `updateCostConfig()` — program report + rate-card editing
- **user-costs-main.ts**: Admin per-user cost dashboard with stacked bar chart, transaction ledger with expandable USD breakdowns
- **admin-cost-view.ts**: Admin "Cost" tab — program report (7d/30d toggle, summary cards, fixed-line table) + rate-card editor (itemized subscription rows with live subtotal, versioned save, config history)
- **cost-summary.html / cost-summary-main.ts**: Program cost (what it costs to run the service, fixed breakdown + composition bar + per-briefing/per-user economics) plus the viewer's own usage. Admin-gated now via `is_admin`; designed to move into a Settings tab + add a donation link + a public program endpoint later.

## Donations (Stripe)

> **Status: built.** flyfun-common ships the shared Stripe/FX/ledger plumbing
> (≥0.5.0); weatherbrief adds the impact math, endpoints, webhook, and web UI
> (issue #186). The only remaining manual step is the Stripe account setup
> (test/live keys, a recurring price, the registered webhook endpoint).
> Voluntary, unconditional donations to offset running cost. No perks, no
> goods/services in return — see "VAT & legal" below.

### Goals

- Accept one-time and recurring donations via **Stripe**, **web-only**.
- **Multi-currency**: donors pay in their local currency; USD stays the canonical
  accounting/reference currency. Costs are shown in the viewer's currency too.
- Show donors **impact, not raw amounts**: "your donation covers 1 user for ~8 months"
  / "covers ~3 users until the end of the year".
- Show the **community total**: "donations this year cover ~5 months of running costs"
  / "~4 users for a full year".

### What `spending_limit` is (and is not) — being retired

`UserRow.spending_limit` is a **per-user spend balance**, not a donation store.
`charge_briefing()` decrements it by each briefing's USD cost; when it hits 0,
`_auto_reload()` resets it to $5 and logs a $0 `topup` entry. It never blocks anything
(auto-reload keeps the free tier flowing) and is not shown in the UI.

**Decision: retire it.** Nothing reads `spending_limit` to gate anything — the
decrement/auto-reload is write-only churn, and its only reader (flyfun-common's
`check_budget()`) is itself unused (called only by its own test; no other flyfun app
references the column). Removed in two steps:

- **weatherbrief:** `charge_briefing()` drops the decrement + `_auto_reload` and just
  records cost (also removes the `topup`/auto-reload noise from the ledger).
- **flyfun-common:** drop the `spending_limit` column and `check_budget()` — foldable
  into the `donation_ledger` migration, since that already touches the shared `users`
  schema. An orphaned column is harmless in the interim (SQLAlchemy ignores it).

Donations are a different shape entirely — *money in* with currency, a Stripe reference,
and refund status — so they get their own ledger (`donation_ledger`) regardless.

### Architecture (proposed)

Money handling is cross-app, like `cost_ledger`, so it lives in **flyfun-common**:

```
flyfun_common/payments/stripe_client.py  — Stripe SDK wrapper: create Checkout Session, verify webhook
flyfun_common/payments/donations.py      — record_donation(), per-user + yearly aggregation queries
flyfun_common/fx.py                       — daily ECB rates (Frankfurter), USD<->local conversion, cache
flyfun_common/db/models.py                — DonationRow (+ FxRateRow cache)
```

Weather-specific pieces stay in weatherbrief (built):

```
weatherbrief/impact.py                    — donation impact math (pure; margin-excluded run cost)
weatherbrief/api/donations.py             — /checkout, /webhook, /me, /summary
weatherbrief/api/credits.py               — build_program_report() (shared), fx block on /user/credits + /transparency
weatherbrief/api/preferences.py           — display_currency pref + fx_block_for_user() resolver
web/donate.html / ts/donate-main.ts       — donate page (amount/currency picker, impact, community total)
web/donate-thanks.html / donate-cancel.html — Stripe redirect targets
web/settings.html                         — display-currency picker + "Support" link (web-only)
tests/test_impact.py, test_donations_api.py, test_credits_changes.py
```

**Web-only:** the donate flow lives entirely in the web frontend (a standalone
`/donate.html` page + a Settings link); the iOS app binary must not surface a
donate button (App Store IAP rule). `/api/donations/me` is read-only and safe to
surface in-app later.

**Rollout order:** flyfun-common lands first (model + migration + Stripe/FX helpers),
then weatherbrief bumps the flyfun-common pin and adds the impact framing, endpoints,
webhook, and UI. A Stripe account (test-mode keys, a recurring product/price, a
registered webhook endpoint) is a manual prerequisite for the weatherbrief side.

### donation_ledger (shared, in flyfun-common)

| Column | Type | Notes |
|--------|------|-------|
| id | INT PK | auto |
| user_id | VARCHAR(64) NULL | indexed, no FK (survives user deletion; NULL = anonymous/logged-out donor) |
| service | VARCHAR(64) | app the donation came through (`"flyfun-weather"`) |
| amount | FLOAT | charged amount in `currency`, positive |
| currency | VARCHAR(3) | ISO 4217 as charged (`"EUR"`, `"GBP"`, `"USD"`) |
| amount_usd | FLOAT | converted to USD reference at donation time (for aggregation) |
| fx_rate | FLOAT | rate used, recorded explicitly so historical conversion is auditable |
| net_usd | FLOAT NULL | `amount_usd` minus Stripe fee, when known from the balance transaction |
| recurring | BOOL | one-time vs subscription |
| status | VARCHAR(32) | `"succeeded"`, `"refunded"` |
| provider | VARCHAR(32) | `"stripe"` |
| provider_ref | VARCHAR(191) | Stripe PaymentIntent / Checkout Session id — **unique** for webhook idempotency |
| created_at | DATETIME(tz) | indexed for yearly rollups |

> Migration uses `op.create_table` (works on SQLite + MySQL without batch mode);
> `provider_ref` capped at 191 chars for MySQL `utf8mb4` unique-index limits.

### Multi-currency & FX

- **USD is canonical.** Costs are computed and stored in USD; `cost_ledger` is unchanged.
- **Donor currency** is whatever Stripe charges (Checkout can present the donor's local
  currency). We persist `amount` + `currency` and convert to `amount_usd` *at the moment
  the webhook confirms*, using that day's rate, so historical totals don't drift with FX.
- **Display** of cost *and* donations in the viewer's currency is a presentation layer:
  API responses stay USD and carry an `fx` block
  (`{ "currency": "EUR", "rate": 0.92, "as_of": "2026-05-26" }`); the frontend formats.
  Single source of truth stays USD.
- **Rate source:** ECB daily reference rates via the free, key-less **Frankfurter** API
  (EUR-based — natural fit for a EU-primary app). Cached daily in `fx_rates` (or in-memory
  with a date guard); on fetch failure, fall back to the last cached rate. Weekends/holidays
  have no new ECB rate — reuse the latest available.
- **Display-currency preference:** add `display_currency` to `app_prefs_json` (e.g. `"EUR"`,
  `"USD"`, `"GBP"`, `"auto"`). Default derives from the existing `units_region`
  (`europe→EUR`, `us→USD`) or the browser locale; the user can override.

### Impact framing (pure, testable — like `costs.py`)

Donors see coverage, never "you gave $X". The framing is **retrospective**: a
donation *offsets cost already incurred*, it is not a forward prepayment (the
droplet/ECMWF/model bills are largely sunk + fixed, so a gift doesn't draw down
anyone's marginal bill). Verb policy, enforced in the phrasing layer:
**offset / contribute / cover / help cover** — never "pay for" or "fund your next
N months". Inputs come from the program cost report (`impact.py`,
`economics_from_report`):

```python
# Operator's real monthly run cost — margin EXCLUDED (donations cover real cost, not the buffer)
scale                = 30 / window_days
monthly_run_cost_usd = fixed_monthly_usd + variable_usd * scale
active_users         = num_users                         # distinct briefing users, 30d window
cost_per_user_month  = monthly_run_cost_usd / active_users           # guard active_users == 0
cost_per_briefing    = monthly_run_cost_usd / (num_briefings * scale) # margin-excluded, for "X briefings funded"
```

**Denominators (decided):**

- **Community = this calendar year.** `coverage_ratio = total_year_usd /
  (monthly_run_cost_usd * months_elapsed_this_year)` — donations-this-year ÷
  cost-incurred-this-year-so-far. Resets annually.
- **Personal = lifetime.** The user's whole relationship with the app: lifetime
  cost from `cost_ledger` (`credits.user_cost_stats`, which also yields the
  realized **monthly burn rate** = lifetime cost ÷ months active) vs lifetime
  donations (`get_user_total_usd`).

#### Personal panel — retrospective with forward overflow

`impact.personal_impact()` returns a `PersonalImpact` with a `band`:

1. **`retrospective`** (`coverage_ratio < 1.0`, the normal case): "covers ~N
   months of your own usage so far" (donation ÷ burn rate) or, when the burn
   rate is too thin to round to a whole month, "covers ~Y% of what your usage
   has cost." Big month counts (≥ 18) roll up to years ("~3.3 years of your own
   usage so far") rather than reading as "~40 months".
2. **`covers_others`** (own usage covered, but the whole site is *not*):
   "fully covers your own usage — plus ~N other pilots", where N = surplus ÷
   `cost_per_user_month`, **rounded to a whole number, minimum 1** (0.x rounds up
   to "another pilot"; never a fraction). This intermediate band stops us
   jumping to a future promise the moment a small footprint over-covers.
   **Overflow cap (`overflow_capped`):** once N would reach the *actual* active
   pilot count (equivalently the surplus ≥ 1 month of the whole platform) or the
   surplus ≥ 2 months, we never claim more pilots than exist — the phrasing
   switches to "…— and ~N months of running the whole service" (N = surplus ÷
   monthly run cost). Both thresholds read live from the report, so they
   self-adjust as the platform grows. `service_months` carries the value.
3. **`future`** (only once community `coverage_ratio ≥ 1.0` — `site_covered`):
   forward framing unlocks — "fully covers your own usage and helped others —
   and contributes ~N months toward the service ahead" (N = surplus ÷ monthly
   run cost).

All counts singularize ("~1 month", "~1 other pilot") so the page never renders
"~1 months". The prospective preview ladder (`choose_translation`) applies the
same pilot cap — the "N pilots for a month" framing only shows while N stays
below the active base — and the same year roll-up for personal-usage months.

#### Community panel

`impact.yearly_impact()` → `YearlyImpact`; phrasing flips at full coverage:

- below 1.0 → "this year's donations have offset ~{coverage_ratio*100:.0f}% of
  the running costs so far."
- `coverage_ratio ≥ 1.0` → "this year's costs are fully covered" (+ "plus ~N
  months ahead" when `surplus_months = months_covered − months_elapsed ≥ 1`).

#### Adaptive translation ladder (prospective "donate €X" preview)

`impact.choose_translation()` adapts the *translation type* (not just the unit)
so the chosen number lands ~2–24 and never reads "0"/"0.3". `GET
/api/donations/preview?amount=&currency=` serializes it; logged-in pilots with
history get the personal path (small amounts vs their own burn rate), everyone
else the program average.

| Amount (USD, rough) | Preferred translation (`kind`) | Source |
|---|---|---|
| small (≤25) | "covers ~N months of **your own** usage" (`personal_months`, logged-in w/ history); else "covers one pilot for ~N months" (`user_months`); else "funds ~N briefings" (`briefings`) | burn rate / `cost_per_user_month` / `cost_per_briefing` |
| medium (≤150) | "covers ~N pilots for a month" (`users_for_month`) or "funds ~N briefings" (`briefings`) | `cost_per_user_month` / `cost_per_briefing` |
| large (>150) | "covers ~N months of running the whole service" (`service_months`) | `monthly_run_cost_usd` |

#### Stats trio (transparency header on the donate page)

`GET /api/donations/summary` carries a `stats` block + a `run_cost` block:

- **active_pilots_30d** — `num_users` from the 30d cost report.
- **briefings_all_time** — `COUNT(*)` over `briefing_packs`.
- **analysis_words_all_time** — `SUM(briefing_usage.llm_output_tokens) × 0.75`.
  **Output tokens only** (what the AI *wrote*, not input/total). `words_to_books`
  (~90,000 words/novel) is an optional, clearly-approximate flourish; words is
  the headline.
- **run_cost** — `monthly_run_cost_usd` + `cost_per_user_month_usd`, rendered in
  the viewer's currency. Publishing active pilots + run cost lets a reader back
  out per-pilot cost — **intended** (transparency), not a leak. (A standalone
  public cost-breakdown page/link is deferred; the donate page itself is the
  public transparency surface for now.)

Frozen dataclasses (`ProgramEconomics`, `DonationImpact`, `YearlyImpact`,
`PersonalImpact`, `TranslationChoice`) hold the raw numbers; phrasing lives in a
thin formatter (i18n-friendly). Every path returns a neutral empty state when
there are no donations or `active_users == 0`.

### API endpoints (proposed)

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/donations/checkout` | POST | User or anon | Create a Stripe Checkout Session (`{amount, currency, recurring}`) → returns redirect URL |
| `/api/donations/webhook` | POST | Stripe sig | **Source of truth**: record/refund on `checkout.session.completed`, `charge.refunded`; verifies `STRIPE_WEBHOOK_SECRET`; idempotent on `provider_ref` |
| `/api/donations/me` | GET | User | Viewer's donation total + program-average `impact` + retrospective `personal` panel (lifetime cost, "+N pilots"/forward overflow) (USD + `fx`) |
| `/api/donations/summary` | GET | None | Public: this-year community coverage + `stats` trio (active pilots 30d, briefings all-time, AI words) + `run_cost` block |
| `/api/donations/preview` | GET | User or anon | Adaptive-ladder translation of a prospective `?amount=&currency=` (personal path when logged-in w/ history, else program average) |

### Stripe flow

1. Donor picks amount + currency (defaults to their display currency) on the web donate page.
2. Server creates a **Checkout Session** (hosted page → minimal PCI scope; supports cards,
   Apple/Google Pay, SEPA, iDEAL). `mode=payment` for one-time, `mode=subscription` for recurring.
3. Donor pays on Stripe; redirected back to a thank-you page.
4. **Webhook** confirms and writes the `donation_ledger` row (never trust the client redirect).
   Idempotent on `provider_ref`/event id against Stripe's retries.
5. `charge.refunded` flips `status` and removes the contribution from aggregation.

### VAT & legal

- **Pure, unconditional donation = outside the scope of VAT** (EU) and not a taxable sale
  (US): no goods/services in return. This must stay true — *any* perk turns it into a
  digital-services supply and triggers EU **OSS** VAT at the donor's local rate. Keep
  donations perk-free.
- Operator is **merchant of record** (Stripe is the processor, not the seller of record).
  Donations received may be **income** depending on the operator's legal structure — an
  income-tax matter, separate from VAT.
- **iOS App Store:** keep the donate flow **web-only**; do not surface a donate button
  inside the app binary. Apple requires donations from non-registered-nonprofits to go
  through IAP (30% cut) otherwise. Web-only sidesteps this.

### Key choices

- **Separate `donation_ledger`, not `cost_ledger`**: money-in with currency/refund/Stripe-ref
  is a different shape from positive-USD money-out; keeps the cost ledger's "always positive
  = cost" invariant intact.
- **In flyfun-common**: payments are cross-app like the cost ledger; weather only adds the
  impact framing.
- **USD canonical, display-only conversion**: avoids FX drift in stored totals, one source of truth.
- **Webhook is source of truth**: never record from the client redirect.
- **Impact over amount**: show coverage ("1 user for ~8 months"), never "you gave $X";
  margin excluded from the run-cost used for impact.
- **Anonymous donations allowed**: `user_id` nullable; attribute when logged in.

### Open questions

- Recurring donations: Stripe Billing subscription vs. recurring Checkout — and how to
  show/cancel an active recurring pledge.
- Whether to surface a logged-in donor's own running impact in the iOS app (read-only, no
  donate button) once `/api/donations/me` exists.
- `active_users` denominator: 30d distinct vs. a rolling 90d average — affects how volatile
  the "covers N users" number looks month to month.
- Fee transparency: show gross vs. `net_usd` (after Stripe fee) in the community total.

## Key Choices

- **Pure computation module** (`costs.py`): frozen dataclasses, no DB imports, fully testable. The API layer handles DB/ORM.
- **Shared cost_ledger**: All flyfun apps write to the same table via `flyfun_common.costs.record_cost()`. Cross-app cost visibility without a separate hub service.
- **USD everywhere**: No credits abstraction. Cost is always positive USD. Simpler, honest.
- **No balance displayed**: The service is free — showing a spend balance is misleading. Donations are surfaced as *impact* (coverage), never a balance — see [Donations (Stripe)](#donations-stripe--planned).
- **spending_limit is a spend balance, not donations**: `UserRow.spending_limit` decrements per briefing and auto-reloads at $5 USD; never shown, never blocks. Donations are tracked separately in `donation_ledger` — see [Donations (Stripe)](#donations-stripe--planned).
- **Failure-safe charging**: `_charge_briefing_cost()` catches all exceptions. Broken cost system never blocks a briefing.
- **Versioned configs**: Admin updates create a new row. Old configs remain for auditing.
- **Program report ≠ amortized per-briefing**: For operator-facing reporting, fixed cost is the *real* rate-card amount prorated to the window, and variable is the *actual* token+storage from the ledger — not the `est`-amortized share each briefing was charged. The two intentionally diverge at low volume.
- **Itemized subscriptions**: `subscription_details` is the source of truth in the editor; `subscriptions_monthly_usd` is recomputed as its sum on PUT so the rate card never drifts from its line items.

## Gotchas

- `charge_briefing()` uses `with_for_update()` on the user row to prevent concurrent balance corruption.
- The transparency endpoint is public (no auth) — intentionally exposes the cost structure.
- Historical `description` fields may still contain "credits" text from before the migration.
- `detail_json` holds the full CostBreakdown; `metadata_json` is for lightweight context. Don't mix them.
- `reference_id` stores `briefing_usage_id` as a string — admin queries cast it to int for the JOIN.
- The program report sums variable cost by parsing each briefing's `detail_json` in Python (dialect-agnostic) rather than SQL JSON extraction; bounded by briefings-per-window so it's cheap.
- `/api/admin/cost-report` returns `null` (not 404) when no active config exists — the frontend renders an empty state.

## References

- Pure cost computation: `src/weatherbrief/costs.py` (`compute_cost`, `compute_program_cost`)
- Cost API, admin config & program report: `src/weatherbrief/api/credits.py`
- DB models: `src/weatherbrief/db/models.py` (CostConfigRow)
- Shared ledger model: `flyfun_common.db.models.CostLedgerRow`
- Shared ledger utilities: `flyfun_common.costs.record_cost()`
- Charging hook: `src/weatherbrief/api/packs.py` → `_charge_briefing_cost()`
- Admin Cost tab: `web/ts/admin-cost-view.ts`, `web/admin.html`
- Program cost-summary page: `web/cost-summary.html`, `web/ts/cost-summary-main.ts`
- Tests: `tests/test_costs.py`, `tests/test_cost_report_api.py`
