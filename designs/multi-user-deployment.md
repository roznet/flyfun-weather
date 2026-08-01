# Multi-User Deployment

> Deploy WeatherBrief as a shared service for invited pilots at weather.flyfun.aero

## Intent

Make WeatherBrief available to a small group of trusted pilots (friends, not public). Each user manages their own flights and briefings, enters their own autorouter credentials, and has individual preferences. Usage is tracked per-user for cost awareness and rate limiting.

## Infrastructure

### Existing setup (DigitalOcean droplet — connect.flyfun.aero)

- **Caddy** reverse proxy with auto-TLS (sites-enabled pattern)
- **Shared MySQL 8.0** on `shared-services` Docker network
- **Docker Compose** per-app, all joining `shared-services` network
- **DNS** managed via DigitalOcean, Caddy auto-obtains Let's Encrypt certs

### Target architecture

```
weather.flyfun.aero (Caddy, auto-TLS)
    → reverse_proxy localhost:8020
        → weatherbrief Docker container (FastAPI + uvicorn)
            → shared-mysql (Docker network: shared-services)
            → /app/data volume (artifact files)
```

Port 8020 chosen to avoid conflicts with existing services (8000=maps, 8002=mcp, 8010=boarding).

### Content Security Policy

Caddy injects CSP headers via `deploy/weather.flyfun.aero.caddy`. Key directives:

| Directive | Value | Why |
|-----------|-------|-----|
| `script-src` | `'self' 'unsafe-inline'` | Bundled JS + inline theme/login scripts |
| `style-src` | `'self' 'unsafe-inline'` | Local CSS + inline styles in admin/costs pages |
| `img-src` | `'self' data: blob: *.tile.openstreetmap.org *.basemaps.cartocdn.com` | Leaflet map tiles (light/dark themes) |
| `connect-src` | `'self' *.flyfun.aero` | API calls + cross-subdomain (forms.flyfun.aero) |
| `form-action` | `'self' accounts.google.com` | Google OAuth redirect |
| `frame-ancestors` | `'self'` | No cross-origin embedding (also sets `font-src 'self'`, `base-uri 'self'`) |

**Gotcha**: Any new external resource (CDN script, external fetch, new tile provider) will be silently blocked. The `/code-review` command checks PRs against this policy. When adding external resources, either update the CSP in the Caddy file or refactor to stay within the policy.

## Development Mode

`ENVIRONMENT=development` (from `.env`) activates dev mode:

- **Auth bypass**: Middleware auto-injects a dev user (`dev-user-001`, email `dev@localhost`). No login page needed.
- **SQLite instead of MySQL**: defaults to `sqlite:///{DATA_DIR}/flyfun.db` (shared with other flyfun apps via the common engine). SQLAlchemy abstracts the dialect.
- **Same file storage and API routes**: everything works identically.

On first startup: creates DB, tables, and dev user automatically. No manual setup needed.

| Concern | Development | Production |
|---------|------------|------------|
| Auth | Auto-injected dev user + `/auth/dev-token` endpoint | Google OAuth + JWT |
| Database | SQLite (file) | MySQL (shared-services) |
| Artifacts | `data/packs/...` | `/app/data/packs/...` (volume) |
| TLS | None (localhost) | Caddy auto-TLS |
| Rate limits | Disabled | Enforced per-user |
| Credential encryption | Same (Fernet) | Same (Fernet) |

## Database Schema (MySQL / SQLite via SQLAlchemy)

Column definitions live in code — shared rows (`users`, `user_preferences`, `cost_ledger`, `api_tokens`) in **flyfun-common**, app-specific rows in `db/models.py`. This section records identity/keys, ownership, and the non-obvious design rules, not the column lists (read the models for those).

**Shared rows (flyfun-common):**
- **users** — `id` UUID PK; `provider` (`google`/`apple`/`api` for bot/agent) + `provider_sub` UNIQUE OAuth subject; `approved` (auto-approved on signup, admin-revocable); `spending_limit` FLOAT (renamed from `credit_balance`).
- **user_preferences** — `user_id` PK/FK; `app_prefs_json` (defaults, service toggles, advisory prefs, digest config — renamed from `defaults_json`, digest merged in); `encrypted_creds_json` (Fernet-encrypted autorouter creds — renamed from `encrypted_autorouter_creds`); `setup_completed`.
- **cost_ledger** — append-only USD cost tracking (replaced the old credit_ledger). See [cost-attribution-design.md](./cost-attribution-design.md).
- **api_tokens** — `token_hash` = SHA-256 of an `ff_…` token (legacy `wb_` still accepted); `revoked` is a soft-delete kept for the audit trail.

**App-specific rows (`db/models.py`):**

### flight_profiles & user_aircraft
Mission preferences (`flight_profiles` — models/advisories/flight-rules in `settings_json`, `system_template_key` → `configs/system_profiles.json`) vs physical capabilities (`user_aircraft` — cruise speed, ceiling, FIKI, IFR). They are **independent**: selecting an aircraft pre-fills profile defaults but doesn't constrain the profile. ICAO type data is a static JSON file (`configs/icao_aircraft_types.json`, ~150 GA types loaded into memory), not a table — searched via `storage/aircraft_types.py`. **Privacy rule:** `tail_number`/`nickname` are returned only when `viewer_id == aircraft.user_id`; type/capabilities are always visible. This applies uniformly across flight-detail, shared flights, and PIREPs.

### flights
PK `id = {route_slug}-{target_date}-{hash}` — same route+date with different time/altitude is a different flight. `departure_time` is aware-UTC (replaced the old `target_date` + `target_time_utc`); `alt_departure_time` drives "what-if" alt-time advisories. Notable flags: `private` (hide from others' shared links), `auto_refresh`/`auto_refresh_hour`/`last_auto_refresh_at`, `verification_status`, `raw_route` + `parser_version` (the Field-15 route the pilot typed), `share_code` (random base62 token for `/s/{code}`). FKs to `flight_profiles`/`user_aircraft` are SET NULL on delete; `user_id` cascades.

### flight_subscriptions
Read-only sharing between pilots — surfaces the owner's latest briefing on the subscriber's flight list and flight/briefing pages; write endpoints still gate on `row.user_id == viewer_id`. UNIQUE `(flight_id, user_id)` prevents double-subscription, and `subscribe_flight` catches the resulting `IntegrityError` inside a SAVEPOINT to make concurrent POSTs idempotent. The privacy flip is enforced at query time in `list_flights_with_role`: subscribed flights disappear from the recipient's list when `flights.private = TRUE`.

### briefing_packs
Pack metadata only — the artifacts themselves are files (see [data-models.md](./data-models.md)). Notable columns: `assessment` GREEN/AMBER/RED + reason, `integrity_hmac` (tamper check over pack contents), `model_init_times_json`, `artifact_path`. JSON/flag columns omitted for brevity: `grib_init_times_json`, `model_sources_json`, `models_skipped_region_json`, `diagnostics_json`, the alt-time fields (`alt_assessment*`, `has_alt_advisories`), and the DWD / Met Office surface-chart reference columns (`{dwd,metoffice}_charts_run_cycle`/`_default_id`/`_in_coverage`/`_within_horizon`). Chart bytes live in the shared `DATA_DIR` cache; the row stores only references.

### briefing_usage & feedback
`briefing_usage` — per-refresh resource accounting (Open-Meteo call count, GRAMET success/fail, LLM model + input/output tokens, result size, elapsed + queue-wait seconds, `triggered_by` = user/scheduler/admin) that feeds cost attribution. `feedback` — user submission with `category`, workflow `status` (`pending → ready → replied`/`ignored`), and AI triage (`classification` = BUG_FIXABLE/RESPOND_ONLY/NEEDS_INVESTIGATION/DEFER_TO_HUMAN, `confidence`, `ai_analysis`, plus audit `triage_prompt`/`triage_raw_response` stored as MEDIUMTEXT on MySQL), `admin_reply`/`admin_notes`.

### pireps
Pilot weather reports (community observations) — full schema in `designs/future/pirep-plan.md`. `client_uuid` UNIQUE for offline dedup; `source` = manual/inflight/postflight; `aircraft_id`/`pack_id`/`user_id` are all SET NULL on delete (`user_id` nulled to anonymize on account deletion). **Permission gating:** `pirep_can_view`/`pirep_can_publish` flags in `app_prefs_json` (admin sets per-user or bulk); feature-gating 403s must NOT trigger a login redirect in the frontend (see `apiFetch` in `web/ts/utils.ts`). **Flight query:** matched by `pack_id`, or for pack-less PIREPs by user + flight time window (departure ±2h). **Retention:** never deleted, and packs linked to PIREPs are exempt from T1/T2 retention cleanup.

### device_tokens
APNs push tokens (M2 — table created but not yet used): `user_id` FK CASCADE, `token` UNIQUE, `environment` sandbox/production.

## Authentication (via flyfun-common)

Auth, JWT, encryption, DB engine, and user models are provided by `flyfun-common` (shared across flyfun apps). WeatherBrief mounts the common auth router and extends it with a custom `/auth/me` endpoint (registered first for priority).

### OAuth flow

Google Sign-In and Sign in with Apple via `authlib`. JWT (HS256, 7-day expiry) in httpOnly secure cookie (`flyfun_auth`, cross-subdomain on `.flyfun.aero`).

1. User clicks "Sign in with Google/Apple" → consent screen
2. Callback exchanges code for ID token → lookup/create user (auto-approved)
3. `on_new_user` callback creates `UserPreferencesRow` + sends welcome/admin emails
4. Issue JWT cookie; all `/api/*` routes validate via `current_user_id()` dependency from flyfun-common

Admin identity: `ADMIN_EMAILS` env var (comma-separated). Dev user is always admin. Custom `/auth/me` adds `is_admin` and `setup_completed` fields.

### Magic-link (email) flow

A third sign-in path alongside Google/Apple, for users who don't want to link their flying activity to either SSO. Mints the *same* `flyfun_auth` JWT — the only difference is identification. Implementation lives in `flyfun_common.auth.magic_link`; weather wires the email callback at `api/app.py:create_app`.

1. `POST /auth/magic-link/request` `{email}` — issues a 256-bit token (`secrets.token_urlsafe(32)`) and a 6-digit OTP, stores SHA-256 hashes only in `magic_link_tokens`, invokes `weatherbrief.notify.magic_link_email.send_magic_link_email`. Always returns 200 (no account enumeration).
2. `GET /auth/verify?token=...` — **does not consume the token**; 302s to `/auth-verify.html?token=...` so email scanners (Outlook ATP, Proofpoint, Mimecast) that pre-click links can't burn single-use tokens.
3. `POST /auth/magic-link/consume` `{token}` — looks up by `lower(email)`, finds-or-creates user (case-insensitive). Existing Google/Apple users with the same email are **re-used as-is** — their `provider` field is preserved. Mints JWT + `flyfun_auth` cookie, 302s.
4. `POST /auth/magic-link/consume-code` `{email, code}` — iOS OTP variant. Same logic, returns `{token, user_id}` JSON (no cookie). Lets the iOS app accept a 6-digit code typed in by a user reading the email on their laptop.

**Token lifecycle.** 15-min expiry, single-use, deleted 24h after expiry by `purge_expired_magic_link_tokens` called from the daily retention loop (`scheduler.py`).

**Rate limits.** DB-backed sliding windows (in `flyfun_common.auth.rate_limit`): 3 requests/hour per email, 10/hour per IP, 5 consume attempts/minute per IP. Bypassed when `is_dev_mode()`.

**Apple Private Relay rejection.** `@privaterelay.appleid.com` addresses can't receive our mail; `/request` returns 400 with a "use Sign in with Apple" message.

**Pending-user parity.** Magic-link consume for an unapproved user returns the same `/login.html?status=pending` redirect as the OAuth callback. The token is *not* marked used so the same link can succeed after admin approval (within 15 min).

**Email content.** `weatherbrief.notify.magic_link_email.send_magic_link_email` sends an HTML + plain-text email containing both the click-through link and the 6-digit code, plus the requesting IP (so the user can spot misuse). Dev mode logs the link/code instead of sending.

**Schema.** `magic_link_tokens` (id, email, token_hash, otp_code_hash, created_at, expires_at, used_at, requested_ip) and `magic_link_consume_attempts` (id, ip, attempted_at). Migration `059_magic_link_tokens.py` creates both tables and adds `ix_users_email` since email-keyed lookup runs on every consume.

### API Token Authentication (bot/agent users)

**Token format**: `ff_` prefix + 48 random characters (legacy `wb_` prefix still accepted). SHA-256 hashed before storage.

**Auth flow**: `Authorization: Bearer ff_...` header → hash lookup → user resolution. Falls through to JWT cookie if no Bearer token.

**Admin endpoints**: `POST /api/admin/agents` (create agent + token), `POST .../tokens` (add token), `DELETE .../tokens/{id}` (revoke).

### Admin Auth Unification

Admin endpoints accept both JWT cookies (browser sessions) and Bearer API tokens. The `current_user_id()` dependency checks for a `Bearer` token first, then falls back to the JWT cookie.

### Account Deletion

`DELETE /auth/me/account` triggers `_on_delete_user()` callback (in `api/app.py`): cascade-deletes all flights (which cascade-deletes packs), profiles, aircraft, device tokens, usage records, feedback, and removes artifact files from disk. PIREPs are **anonymized** (user_id/aircraft_id set to NULL) rather than deleted — observation data is preserved.

`GET /api/account/export` (in `api/account_export.py`) is the GDPR right-to-portability counterpart: a read-only JSON dump of everything `_on_delete_user` would erase. Keep the two mirrored — when a new user-owned table is added, wire it into both. Secrets/server-internal columns are stripped via a per-table omit-list (e.g. the device-token value). PII must not be logged in the clear: use `mask_email()` from `privacy.py` (GDPR ops-logging rule).

### Admin Hub (Cross-App)

`create_hub_router()` from `flyfun-common` provides cross-app admin endpoints at `/api/admin/hub/*`. Registers "flyfun-weather", "flyfun-maps", "flyfun-forms" with user cost view links. Powers the Systems tab on the admin page.

### Dev mode bypass

`ENVIRONMENT=development` → auth middleware injects dev user, skips JWT.

## Rate Limiting & Usage Tracking

| Resource | Limit | Rationale |
|----------|-------|-----------|
| Open-Meteo calls | 50/day per user | Free tier: 10K/day total |
| GRAMET calls | 20/day per user | Autorouter courtesy |
| LLM digest calls | 20/day per user | Token cost |
| PIREP submit (burst) | 1 per 2 min per user | Prevent spam |
| PIREP submit (daily) | 50/day per user | Reasonable cap; batch sync counts items not calls |

Every refresh logged to `briefing_usage` with timing metrics (`elapsed_seconds`, `queue_wait_seconds`, `triggered_by`). Freshness check via `check_freshness()` in `fetch/model_status.py`.

### Refresh Queue & Concurrency

| Resource | Limit | Notes |
|----------|-------|-------|
| Refresh queue depth (server) | 5 | `MAX_QUEUE_DEPTH`; returns 503 when full; scheduler bypasses queue limit |
| Concurrent refreshes (per user) | 2 | `MAX_PER_USER`; returns 429 when exceeded; prevents one user hogging the queue |
| Heavy generation (PDF + Skew-T) | 3 concurrent | Single combined `threading.Semaphore(3)` in `api/throttle.py`; returns 503 |
| PDF render rate | 10/hr | `pdf_limiter` sliding window (WeasyPrint is very heavy) |
| Skew-T / hodograph render rate | 60/hr | `plot_limiter` sliding window (combined budget) |

`RefreshRegistry` (in-memory, mirrored into `briefing_refresh_jobs` so an interrupted refresh survives a restart — see [refresh-durability.md](./refresh-durability.md)) tracks active refreshes by flight and user, queue depth, and timing. Each `RefreshEntry` carries `user_id` for per-user accounting. `GET /api/admin/metrics` returns live queue state + 24h/7d/30d timing statistics. `GET /api/refresh/stats` returns 7-day average refresh time (public, used by frontend progress hint).

### Refresh Progress UX

During a refresh, the SSE stream and freshness bar show:
- Pipeline stage labels with progress percentage (14 stages, 5%–95%; `_STAGE_LABELS`/`_STAGE_PROGRESS` in `api/packs.py`)
- **"You can close this page"** hint with average refresh time (from `/api/refresh/stats`)
- **"Email me when done"** checkbox — passes `?notify_email=true` to the stream endpoint, which calls `send_briefing_email()` on completion

## Auto-Refresh Scheduler

Background scheduler (`scheduler.py`) polls every 10 minutes for flights with `auto_refresh=True` that are due.

**Scheduling formula**: `next_due = min(next_regular, flight_start − 2h)` where `next_regular` is the next occurrence of the user's preferred hour (`auto_refresh_hour`, defaulting to `departure_time.hour − 1`) after the last refresh.

**Model-update-aware timing (issue #192)**: the `next_regular` term may be *deferred* a bounded amount so a briefing rides an imminent horizon-extending model run instead of being stale-at-birth. `_defer_regular_for_model_update` (in `scheduler.py`) consults the freshness `MarkerStore` for the next ECMWF full-horizon (00/12Z, 168h) delivery (~06:40 / 18:40 UTC; the shorter 06/18Z cycles, which reach 144h, are excluded via `registry.next_full_horizon_run`). If the regular slot falls within `_MODEL_UPDATE_WAIT_WINDOW` (2h) *before* that delivery, the slot is pushed to `delivery + 20m`, capped at `slot + 2h30m` (so a slipping run never delays indefinitely). It **only ever defers**, never touches the preflight term, and is gated to slots `≥ 2` calendar days before the flight (never day-of / day-before — timeliness wins). Two levers drive whether it applies: the silent NULL-`auto_refresh_hour` default (snaps the `departure − 1` majority out of the pre-delivery dead-zone) and the opt-in account toggle `defer_email_for_model_update` (default off) for explicit-hour users.

**Behavior**:
- Skips flights whose start time has passed
- Checks data freshness before refreshing (skips if models unchanged)
- Uses `RefreshRegistry` to prevent concurrent refreshes
- Sends email notification on successful refresh (if SMTP configured)
- Records `last_auto_refresh_at` timestamp after each refresh

**Integration**: started as an `asyncio.Task` from the FastAPI app lifespan (30s startup delay).

## Encrypted Credential Storage

Autorouter credentials encrypted at rest using Fernet symmetric encryption.

- Key: `CREDENTIAL_ENCRYPTION_KEY` env var
- Encrypt on write (settings save), decrypt on read (GRAMET fetch)
- If key is lost, users re-enter credentials (no recovery needed)

## Feedback Triage Sandbox

The feedback-triage worker (`python -m weatherbrief.triage process`) feeds
**attacker-authored** feedback text into `claude -p` with Read/Grep/Glob enabled.
To contain prompt-injection (security-audit finding C1), it runs as a dedicated,
OS-isolated `triage` system user on the droplet — NOT as the app user. Three
layers: a sparse source checkout (no `.env`/`configs` beyond `configs/triage/`),
a Unix user that can't read secrets outside its sandbox dir, and a scoped MySQL
user (`weatherbrief_triage`) limited to `users` (read), `feedback` (read/update),
`cost_ledger` (insert). Code tripwire `_assert_sandboxed()` refuses to run as any
other UID unless `TRIAGE_ALLOW_UNSAFE=1`. Full droplet setup + ongoing-ops
runbook lives in **[triage-sandbox.md](./triage-sandbox.md)**.

## Deploying to Server

### First-time setup

```bash
# 1. Clone repo on server
git clone https://github.com/roznet/flyfun-weather.git && cd flyfun-weather

# 2. Create MySQL database
docker exec -i shared-mysql mysql -u root -p < deploy/03-create-weatherbrief-db.sql

# 3. Create .env with production settings
# (ENVIRONMENT=production, DATABASE_URL, DATA_DIR, AIRPORTS_DB, API keys)

# 4. Copy nav.db into data/ (AIRPORTS_DB points at data/nav.db; built by euro_aip)
mkdir -p data && cp /path/to/nav.db data/

# 5. Build, start, migrate
docker compose up -d --build
docker exec weatherbrief alembic upgrade head

# 6. Add Caddy config
cp deploy/weather.flyfun.aero.caddy /etc/caddy/sites-enabled/
caddy reload --config /etc/caddy/Caddyfile
```

### Updating

```bash
git pull && docker compose up -d --build
# If new migrations: docker exec weatherbrief alembic upgrade head
```

### Key files

| File | Purpose |
|------|---------|
| `Dockerfile` | App image (python:3.13-slim, non-root UID 2000) |
| `docker-compose.yml` | Service config, port 8020, shared-services network |
| `deploy/weather.flyfun.aero.caddy` | Caddy reverse proxy with security headers |
| `deploy/03-create-weatherbrief-db.sql` | MySQL database + user creation template |
| `alembic.ini` + `alembic/` | Schema migrations (prod only) |

### Environment variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `ENVIRONMENT` | No | `development` | `production` for Docker/MySQL |
| `DATABASE_URL` | Prod only | — | MySQL connection string |
| `DATA_DIR` | No | `data` | Artifact storage root |
| `AIRPORTS_DB` | Yes | — | Path to `data/nav.db` (built by euro_aip) |
| `OPENAI_API_KEY` | For LLM digest | — | |
| `ANTHROPIC_API_KEY` | For LLM digest | — | |
| `AUTOROUTER_USERNAME` | For GRAMET | — | Fallback; per-user creds preferred |
| `AUTOROUTER_PASSWORD` | For GRAMET | — | Fallback; per-user creds preferred |
| `CREDENTIAL_ENCRYPTION_KEY` | Prod only | derived from JWT_SECRET in dev | Fernet key |
| `ADMIN_EMAILS` | Prod only | — | Comma-separated admin emails |
| `RESEND_API_KEY` | No | — | Resend email API key (falls back to SMTP if absent) |
| `RESEND_FROM` | No | — | Resend sender address |
| `RESEND_REPLY_TO` | No | — | Resend reply-to address |
| `HMAC_SECRET` | Prod only | derived from JWT_SECRET | HMAC key for pack integrity + admin approval links |
| `WB_REFRESH_MAX_ATTEMPTS` | No | `2` | Total attempts per briefing refresh, counting the original run. `1` disables resume-after-restart; see [refresh-durability.md](./refresh-durability.md) |
| `DISABLE_REFRESH_RESUME` | No | — | `1` skips the boot-time reconciliation pass entirely (rows are still written) |
| `VERIFICATION_GLOBAL_ROLLUP_READS` | No | `0` | #522 Phase 1. `1` points unfiltered dashboard/digest aggregates at the global rollup tables. Requires the backfill first — see [verification-data-tiering.md](./plans/verification-data-tiering.md) |
| `VERIFICATION_ARCHIVE_ENABLED` | No | `0` | #522 Phase 2. `1` runs the Parquet archive writer from the daily retention loop. The `verify archive` CLI works regardless |
| `VERIFICATION_RAW_RETENTION_DAYS` | No | `9999` (disabled) | #522 Phase 3. Online window for raw obs/scores/TAF scores. Target value `180` |
| `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` | No | `1` | Safety belt: no verified `archive_manifest` row → no delete, and the snapshot prune stalls on unarchived days. Only turn off if abandoning the archive |
| `SNAPSHOT_INBOX_RETENTION_DAYS` | No | `0` (keep forever) | #522 Phase 3. Rotates `eu-*/us-*.sqlite` out of `SNAPSHOT_INBOX_DIR`. Target value `30` |
| `VERIFICATION_MONTHLY_ROLLUP_ENABLED` | No | `0` | #522 Phase 4. `1` rolls completed months into `verification_monthly_stats` from the daily table |
| `VERIFICATION_DAILY_STATS_RETENTION_MONTHS` | No | `0` (keep) | #522 Phase 4 follow-up. Prunes `verification_daily_stats` older than N months. Enable only after monthly rollups are validated against the daily data |

## References

- Server infra: `~/Developer/private/digitalocean/CLAUDE.md`
- Architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Cost attribution: [cost-attribution-design.md](./cost-attribution-design.md)
- Feedback triage sandbox (setup + ops runbook): [triage-sandbox.md](./triage-sandbox.md)
