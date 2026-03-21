# Cost Attribution

> Full transparency on what each briefing costs to produce — infrastructure, API calls, LLM tokens, storage

## Intent

Radical cost transparency, not monetization. Every briefing's real cost is computed in USD and shown to users. The service is free and sponsored by the operator. If costs outgrow sponsorship, voluntary donations may be added — users will always have full visibility into actual costs. The `spending_limit` field on UserRow is dormant but ready for donation tracking.

## Architecture

```
costs.py                  — Pure cost computation (no DB/IO)
api/credits.py            — Cost API: charge, query, admin config, transparency
api/packs.py              — Hook: _charge_briefing_cost() called after each refresh
db/models.py              — CostConfigRow (weather-specific rate card)
tests/test_costs.py       — Formula arithmetic tests
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
| token_cost_per_1k_input | FLOAT | 0.003 |
| token_cost_per_1k_output | FLOAT | 0.015 |
| droplet_monthly_usd | FLOAT | 24.0 |
| misc_monthly_usd | FLOAT | 2.0 |
| subscriptions_monthly_usd | FLOAT | 30.0 |
| disk_cost_per_gb_monthly | FLOAT | 0.10 |
| estimated_monthly_briefings | INT | 500 |
| margin_percent | FLOAT | 30.0 |
| usd_per_credit | FLOAT | 0.01 |

Versioned: updating creates a new row, deactivates the previous. History queryable.

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/user/credits` | GET | User | Cost summary: total, this month, this week, recent transactions |
| `/api/admin/cost-config` | GET | Admin | Current active config |
| `/api/admin/cost-config` | PUT | Admin | Create new config version |
| `/api/admin/cost-config/history` | GET | Admin | All config versions |
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
- **settings-main.ts**: Cost summary (this week / this month / total) in settings page
- **user-costs-main.ts**: Admin per-user cost dashboard with stacked bar chart, transaction ledger with expandable USD breakdowns

## Key Choices

- **Pure computation module** (`costs.py`): frozen dataclasses, no DB imports, fully testable. The API layer handles DB/ORM.
- **Shared cost_ledger**: All flyfun apps write to the same table via `flyfun_common.costs.record_cost()`. Cross-app cost visibility without a separate hub service.
- **USD everywhere**: No credits abstraction. Cost is always positive USD. Simpler, honest.
- **No balance displayed**: The service is free — showing a balance is misleading. Will add back if donations are introduced.
- **spending_limit dormant**: `UserRow.spending_limit` still exists and auto-reloads at $5 USD. Ready for donation model but not shown to users.
- **Failure-safe charging**: `_charge_briefing_cost()` catches all exceptions. Broken cost system never blocks a briefing.
- **Versioned configs**: Admin updates create a new row. Old configs remain for auditing.

## Gotchas

- `charge_briefing()` uses `with_for_update()` on the user row to prevent concurrent balance corruption.
- The transparency endpoint is public (no auth) — intentionally exposes the cost structure.
- Historical `description` fields may still contain "credits" text from before the migration.
- `detail_json` holds the full CostBreakdown; `metadata_json` is for lightweight context. Don't mix them.
- `reference_id` stores `briefing_usage_id` as a string — admin queries cast it to int for the JOIN.

## References

- Pure cost computation: `src/weatherbrief/costs.py`
- Cost API & admin config: `src/weatherbrief/api/credits.py`
- DB models: `src/weatherbrief/db/models.py` (CostConfigRow)
- Shared ledger model: `flyfun_common.db.models.CostLedgerRow`
- Shared ledger utilities: `flyfun_common.costs.record_cost()`
- Charging hook: `src/weatherbrief/api/packs.py` → `_charge_briefing_cost()`
- Tests: `tests/test_costs.py`
