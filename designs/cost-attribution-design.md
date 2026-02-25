# Cost Attribution & Credit System

> Full transparency on what each briefing costs to produce — infrastructure, API calls, LLM tokens, storage

## Intent

The goal is radical cost transparency, not monetization. Every briefing's real cost is computed and shown to users so they understand what it takes to run the service. The site is currently free and sponsored by the operator. If costs grow significantly (more users, heavier LLM usage), the model may evolve to accept voluntary donations — but users will always have full visibility into actual costs, never a hidden pricing structure. The public transparency endpoint and per-briefing cost breakdowns exist to build trust and inform that conversation if it ever happens.

## Architecture

```
costs.py                  — Pure cost computation (no DB/IO)
api/credits.py            — Credit API: balance, charge, admin config, transparency
db/models.py              — CostConfigRow, CreditLedgerRow, UserRow.credit_balance
api/packs.py              — Hooks: _charge_briefing_cost() called after each refresh
alembic/versions/009_*    — Migration: cost_config, credit_ledger tables, credit_balance column
tests/test_costs.py       — Formula arithmetic tests
```

### Data Flow

```
Briefing refresh completes
  → _measure_pack_size() calculates artifact bytes
  → log_briefing_usage() records usage row (incl. result_size_bytes)
  → _charge_briefing_cost():
      1. Load active CostConfigRow
      2. compute_cost() → CostBreakdown (pure math)
      3. charge_briefing() → deduct from UserRow.credit_balance, append CreditLedgerRow
      4. If balance ≤ 0 → auto-reload to 500 credits
```

Cost computation failures are caught and logged — they never block the briefing.

## Cost Formula (Implemented)

```python
# 1. Token cost (variable, directly measured)
token_cost = (input_tokens / 1000) × rate_input
           + (output_tokens / 1000) × rate_output

# 2. Infrastructure share (fixed costs, per-briefing)
est = max(estimated_monthly_briefings, 500)   # floor prevents huge per-briefing costs
infra_share = (droplet_monthly + misc_monthly) / est

# 3. Subscription share (external API costs, per-briefing)
subscription_share = subscriptions_monthly / est

# 4. Storage cost (proportional to pack size)
storage_cost = (result_size_bytes / 1 GB) × disk_cost_per_gb_monthly

# 5. Margin
subtotal = token + infra + subscription + storage
margin = subtotal × (margin_percent / 100)
total_usd = subtotal + margin

# 6. Credits
credits = total_usd / usd_per_credit
```

The formula is simpler than the original design vision — CPU-weighted allocation and data source surcharges were deferred. The flat per-briefing share is sufficient for current scale.

## DB Schema

### cost_config (versioned admin rate card)

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| id | INT PK | auto | |
| active_from | DATETIME(tz) | now() | When this config became active |
| active_until | DATETIME(tz) | NULL | NULL = currently active |
| token_cost_per_1k_input | FLOAT | 0.003 | |
| token_cost_per_1k_output | FLOAT | 0.015 | |
| droplet_monthly_usd | FLOAT | 24.0 | |
| misc_monthly_usd | FLOAT | 2.0 | |
| subscriptions_monthly_usd | FLOAT | 30.0 | |
| subscription_details_json | TEXT | `{"open_meteo": 30}` | Breakdown of subscriptions |
| disk_cost_per_gb_monthly | FLOAT | 0.10 | |
| estimated_monthly_briefings | INT | 500 | Calibration divisor |
| margin_percent | FLOAT | 30.0 | |
| usd_per_credit | FLOAT | 0.01 | 1 credit = $0.01 |

Versioned: updating creates a new row, deactivates the previous. History queryable.

### credit_ledger (append-only transaction log)

| Column | Type | Notes |
|--------|------|-------|
| id | INT PK | auto |
| user_id | VARCHAR(64) FK | → users.id |
| timestamp | DATETIME(tz) | |
| amount | FLOAT | Negative for charges, positive for topups |
| balance_after | FLOAT | Running balance snapshot |
| category | VARCHAR(32) | `briefing`, `topup`, `purchase`, `refund`, `bonus` |
| description | VARCHAR(256) | Human-readable |
| breakdown_json | TEXT NULL | Full CostBreakdown for `briefing` entries |
| briefing_usage_id | INT FK NULL | → briefing_usage.id |
| cost_config_id | INT FK NULL | → cost_config.id |

### users (added column)

| Column | Type | Default |
|--------|------|---------|
| credit_balance | FLOAT | 500.0 |

### briefing_usage (added column)

| Column | Type | Notes |
|--------|------|-------|
| result_size_bytes | INT NULL | Total artifact size on disk |

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/user/credits` | GET | User | Balance, recent transactions, daily/monthly usage |
| `/api/admin/cost-config` | GET | Admin | Current active config |
| `/api/admin/cost-config` | PUT | Admin | Create new config version |
| `/api/admin/cost-config/history` | GET | Admin | All config versions |
| `/api/admin/users/{id}/costs` | GET | Admin | Per-user cost detail (see below) |
| `/api/transparency` | GET | None | Public pricing structure |

## Frontend Integration

- **credits-adapter.ts**: `fetchCreditSummary()`, `fetchTransparency()` — typed API client
- **settings-main.ts**: Credit balance display in settings page
- **briefing-store.ts**: Credit info available for post-refresh cost display
- **flights-store.ts**: Credit balance in flight list header

### Admin User Costs Page (`user-costs.html`)

Dedicated per-user cost attribution dashboard at `/user-costs.html?user={id}`, linked from the admin user list:

- **User header**: Name, email, status badge, member since, credit balance (color-coded: green/amber/red)
- **Summary cards**: Credits used today, this month, all-time total, briefing count
- **Cost distribution chart**: Stacked horizontal bar showing category breakdown (LLM tokens, infrastructure, subscriptions, storage, margin) with dollar amounts and average cost per briefing
- **Recent flights table**: Route, date, time, altitude with links to individual briefings
- **Transaction ledger**: Date, type, description, amount, running balance; expandable detail rows showing per-transaction cost breakdown grid

**API endpoint** (`GET /admin/users/{id}/costs`): Returns user info, credit balance, summary aggregates (today/month/total), full transaction history with breakdowns, recent flights, and aggregate cost breakdown by category. Joins `CreditLedgerRow` with `BriefingUsageRow` to link transactions to flights.

## Key Choices

- **Pure computation module** (`costs.py`): frozen dataclasses, no DB imports, fully testable. The API layer (`credits.py`) handles DB/ORM concerns.
- **Auto-reload at 500 credits**: The service is free — when balance hits 0, it auto-reloads to 500. Credits exist to track cost, not to gate access. If donations are added later, the ledger infrastructure is already in place.
- **Failure-safe charging**: `_charge_briefing_cost()` catches all exceptions and logs. A broken cost system never blocks a briefing.
- **Versioned configs**: Admin updates create a new config row with `active_from` timestamp. Old configs remain for auditing. Ledger entries reference the config used via `cost_config_id`.
- **Floor on estimated briefings**: `max(estimated_monthly_briefings, 500)` prevents absurd per-briefing costs during low-volume periods.

## Gotchas

- The `credit_balance` column on `users` is a cached value derived from the ledger. The ledger is the source of truth, but for quick reads the balance column is used directly.
- `charge_briefing()` uses `with_for_update()` on the user row to prevent concurrent balance corruption.
- The transparency endpoint is public (no auth) — intentionally exposes the cost structure.
- Auto-reload creates a `topup` ledger entry. This is distinguishable from future `purchase` entries.

## Future Extensions (Not Yet Implemented)

From the original design vision, deferred for later:

- **Donation support**: Optional voluntary contributions if costs outgrow sponsorship
- **CPU-weighted infrastructure allocation**: Blend flat + CPU-proportional sharing
- **Data source surcharge**: Weight by number of models/soundings/stations queried
- **Briefing tiers**: Quick/Standard/Full/Trend classification for UX and estimates
- **Briefing retention policy**: Auto-delete old packs, optional paid retention
- **Low balance alerts**: Email notification at configurable threshold
- **Before-request estimates**: Show estimated cost before running expensive briefings
- **Transparency page UI**: Full public page (currently JSON endpoint only)
- **Monthly recalibration**: Auto-update estimated_monthly_briefings from trailing average
- **Group/club accounts**: Shared credit pool for flying clubs

## References

- Pure cost computation: `src/weatherbrief/costs.py`
- Credit API & admin config: `src/weatherbrief/api/credits.py`
- DB models: `src/weatherbrief/db/models.py` (CostConfigRow, CreditLedgerRow)
- Migration: `alembic/versions/009_cost_attribution.py`
- Tests: `tests/test_costs.py`
- Charging hook: `src/weatherbrief/api/packs.py` → `_charge_briefing_cost()`
- Architecture: [architecture.md](./architecture.md)
- Multi-user deployment: [multi-user-deployment.md](./multi-user-deployment.md)
