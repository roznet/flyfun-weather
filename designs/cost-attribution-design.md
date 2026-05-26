# Cost Attribution

> Full transparency on what each briefing costs to produce — infrastructure, API calls, LLM tokens, storage

## Intent

Radical cost transparency, not monetization. Every briefing's real cost is computed in USD and shown to users. The service is free and sponsored by the operator. If costs outgrow sponsorship, voluntary donations may be added — users will always have full visibility into actual costs. The `spending_limit` field on UserRow is dormant but ready for donation tracking.

## Architecture

```
costs.py                  — Pure cost computation (no DB/IO): per-briefing + program-wide
api/credits.py            — Cost API: charge, query, admin config, program report, transparency
api/packs.py              — Hook: _charge_briefing_cost() called after each refresh
db/models.py              — CostConfigRow (weather-specific rate card)
web/admin-cost-view.ts    — Admin "Cost" tab: program report + rate-card editor
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

- **credits-adapter.ts**: `fetchCostSummary()`, `fetchTransparency()` — typed API client
- **admin-adapter.ts**: `fetchCostReport()`, `fetchCostConfig()`, `fetchCostConfigHistory()`, `updateCostConfig()` — program report + rate-card editing
- **user-costs-main.ts**: Admin per-user cost dashboard with stacked bar chart, transaction ledger with expandable USD breakdowns
- **admin-cost-view.ts**: Admin "Cost" tab — program report (7d/30d toggle, summary cards, fixed-line table) + rate-card editor (itemized subscription rows with live subtotal, versioned save, config history)
- **cost-summary.html / cost-summary-main.ts**: Program cost (what it costs to run the service, fixed breakdown + composition bar + per-briefing/per-user economics) plus the viewer's own usage. Admin-gated now via `is_admin`; designed to move into a Settings tab + add a donation link + a public program endpoint later.

## Key Choices

- **Pure computation module** (`costs.py`): frozen dataclasses, no DB imports, fully testable. The API layer handles DB/ORM.
- **Shared cost_ledger**: All flyfun apps write to the same table via `flyfun_common.costs.record_cost()`. Cross-app cost visibility without a separate hub service.
- **USD everywhere**: No credits abstraction. Cost is always positive USD. Simpler, honest.
- **No balance displayed**: The service is free — showing a balance is misleading. Will add back if donations are introduced.
- **spending_limit dormant**: `UserRow.spending_limit` still exists and auto-reloads at $5 USD. Ready for donation model but not shown to users.
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
