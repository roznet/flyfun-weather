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
| `frame-ancestors` | `'none'` | No embedding |

**Gotcha**: Any new external resource (CDN script, external fetch, new tile provider) will be silently blocked. The `/code-review` command checks PRs against this policy. When adding external resources, either update the CSP in the Caddy file or refactor to stay within the policy.

## Development Mode

`ENVIRONMENT=development` (from `.env`) activates dev mode:

- **Auth bypass**: Middleware auto-injects a dev user (`dev-user-001`, email `dev@localhost`). No login page needed.
- **SQLite instead of MySQL**: defaults to `sqlite:///data/weatherbrief.db`. SQLAlchemy abstracts the dialect.
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

### users (from flyfun-common)

| Column | Type | Notes |
|--------|------|-------|
| id | VARCHAR(36) PK | UUID |
| provider | VARCHAR(20) | `google`, `apple`, or `api` (bot/agent) |
| provider_sub | VARCHAR(255) UNIQUE | OAuth subject ID |
| email | VARCHAR(255) | From OAuth profile |
| display_name | VARCHAR(255) | |
| approved | BOOLEAN DEFAULT TRUE | Auto-approved on signup; admin can revoke |
| spending_limit | FLOAT DEFAULT 500.0 | Spending limit (was `credit_balance`) |
| created_at | DATETIME | |
| last_login_at | DATETIME | |

### user_preferences (from flyfun-common)

| Column | Type | Notes |
|--------|------|-------|
| user_id | VARCHAR(36) PK FK | |
| app_prefs_json | TEXT | JSON: defaults, service toggles, advisory prefs, digest config (was `defaults_json`; digest_config merged in) |
| encrypted_creds_json | TEXT | Fernet-encrypted JSON: `{"username": "...", "password": "..."}` (was `encrypted_autorouter_creds`) |
| setup_completed | BOOLEAN DEFAULT FALSE | True after user completes first-login wizard |

### flight_profiles

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(36) FK | Cascade delete with user |
| name | VARCHAR(100) DEFAULT "Default" | Unique per user (case-insensitive) |
| is_default | BOOLEAN DEFAULT FALSE | One default per user |
| settings_json | TEXT | JSON: cruise_altitude_ft, flight_ceiling_ft, speed_kt, models, advisory_models, gramet/llm/icing toggles, advisory enable/params |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### user_aircraft

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(64) FK | Cascade delete with user |
| icao_type | VARCHAR(4) NOT NULL | ICAO DOC 8643 designator (e.g. SR22, C172) |
| tail_number | VARCHAR(10) NULL | Privacy-gated: only shown to owner |
| nickname | VARCHAR(50) NULL | e.g. "Club SR22" — only shown to owner |
| is_ifr | BOOLEAN DEFAULT FALSE | IFR capable |
| is_fiki | BOOLEAN DEFAULT FALSE | FIKI equipped |
| cruise_speed_kt | INT NULL | Typical cruise speed |
| ceiling_ft | INT NULL | Service ceiling |
| is_default | BOOLEAN DEFAULT FALSE | One default per user |
| created_at | DATETIME | |

ICAO type data is a **static JSON file** (`configs/icao_aircraft_types.json`, ~150 GA types) loaded into memory — not a database table. Searched via `storage/aircraft_types.py`.

Aircraft and flight profiles are **independent** — aircraft provides physical capabilities (speed, ceiling, FIKI), profiles provide mission preferences (models, advisories, flight rules). Selecting an aircraft pre-fills defaults but doesn't constrain the profile.

**Privacy rule**: `tail_number` and `nickname` are only included in API responses when `viewer_id == aircraft.user_id`. All other fields (type, capabilities) are always visible. This applies uniformly: flight detail views, shared flights, and future PIREPs.

### flights

| Column | Type | Notes |
|--------|------|-------|
| id | VARCHAR(100) PK | `{route_slug}-{target_date}-{hash}` |
| user_id | VARCHAR(36) FK | |
| profile_id | INT FK NULL | → flight_profiles.id, SET NULL on delete |
| aircraft_id | INT FK NULL | → user_aircraft.id, SET NULL on delete |
| route_name | VARCHAR(100) | |
| waypoints | JSON | `["EGTK","LFPB","LSGS"]` |
| departure_time | DATETIME(6) NOT NULL | Aware UTC departure (replaces old target_date + target_time_utc) |
| cruise_altitude_ft | INT DEFAULT 8000 | |
| flight_ceiling_ft | INT DEFAULT 18000 | |
| flight_duration_hours | FLOAT DEFAULT 0.0 | |
| private | BOOLEAN DEFAULT FALSE | Hide from other users' shared briefing links |
| auto_refresh | BOOLEAN DEFAULT FALSE | Enable background auto-refresh |
| auto_refresh_hour | INT NULL | Preferred UTC hour for daily refresh (default: departure_time.hour − 1) |
| last_auto_refresh_at | DATETIME(6) NULL | Timestamp of last auto-refresh (for scheduling) |
| created_at | DATETIME | |

### flight_subscriptions

Read-only sharing of a flight between pilots. A subscription lets a non-owner see the owner's latest briefing on their own flight list and flight/briefing pages. Subscribers cannot edit, refresh, or delete the flight; write endpoints still gate on `row.user_id == viewer_id`.

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| flight_id | VARCHAR(256) FK NOT NULL | → flights.id, CASCADE on delete |
| user_id | VARCHAR(64) FK NOT NULL | → users.id, CASCADE on delete |
| created_at | DATETIME(6) NOT NULL | Aware UTC |

Unique `(flight_id, user_id)` prevents double-subscription; `subscribe_flight` catches the resulting `IntegrityError` inside a SAVEPOINT to make concurrent POSTs idempotent. Privacy flip is enforced at query time in `list_flights_with_role`: subscribed flights disappear from the recipient's list when `flights.private = TRUE`.

### briefing_packs

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| flight_id | VARCHAR(100) FK | |
| fetch_timestamp | DATETIME(6) NOT NULL | Aware UTC, microsecond precision |
| days_out | INT | |
| has_gramet | BOOLEAN DEFAULT FALSE | |
| has_skewt | BOOLEAN DEFAULT FALSE | |
| has_digest | BOOLEAN DEFAULT FALSE | |
| assessment | VARCHAR(10) NULL | GREEN/AMBER/RED |
| assessment_reason | TEXT NULL | |
| model_init_times | TEXT NULL | JSON: NWP model init timestamps at fetch time |
| artifact_path | VARCHAR(500) | Relative path to pack directory |

### api_tokens

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(36) FK | Cascade delete with user |
| token_hash | VARCHAR(64) UNIQUE | SHA-256 hex digest of `ff_...` token (legacy `wb_` accepted) |
| name | VARCHAR(100) | Human-readable label (e.g., "CI bot") |
| created_at | DATETIME | |
| expires_at | DATETIME NULL | Optional expiry |
| last_used_at | DATETIME NULL | Updated on each authenticated request |
| revoked | BOOLEAN DEFAULT FALSE | Soft-delete for audit trail |

### briefing_usage

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(36) FK | |
| flight_id | VARCHAR(100) FK | Which flight was refreshed |
| timestamp | DATETIME | |
| open_meteo_calls | INT DEFAULT 0 | Number of Open-Meteo API calls in this refresh |
| gramet_fetched | BOOLEAN DEFAULT FALSE | Was GRAMET successfully fetched? |
| gramet_failed | BOOLEAN DEFAULT FALSE | Did GRAMET fetch fail? |
| llm_digest | BOOLEAN DEFAULT FALSE | Was LLM digest generated? |
| llm_model | VARCHAR(100) NULL | e.g. `anthropic:claude-sonnet-4-5-20250929` |
| llm_input_tokens | INT NULL | LLM input token count |
| llm_output_tokens | INT NULL | LLM output token count |
| result_size_bytes | INT NULL | Total artifact size on disk |
| elapsed_seconds | FLOAT NULL | Total time to complete refresh |
| queue_wait_seconds | FLOAT NULL | Time spent waiting in queue before execution |
| triggered_by | VARCHAR(16) NULL | `user`, `scheduler`, or `admin` |

### feedback

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(64) FK | → users.id |
| flight_id | VARCHAR(256) NULL | Nullable — feedback can be submitted without a flight |
| pack_timestamp | DATETIME(6) NULL | Specific pack within the flight |
| category | VARCHAR(32) | `data_issue`, `too_conservative`, `too_optimistic`, `incorrect_interpretation`, `other` |
| comment | TEXT | Required, 1-5000 chars |
| status | VARCHAR(16) DEFAULT 'pending' | Workflow: `pending` → `ready` → `replied` or `ignored` |
| classification | VARCHAR(32) NULL | AI triage: `BUG_FIXABLE`, `RESPOND_ONLY`, `NEEDS_INVESTIGATION`, `DEFER_TO_HUMAN` |
| ai_analysis | TEXT NULL | AI-generated reasoning |
| admin_reply | TEXT NULL | Draft/sent reply text |
| admin_notes | TEXT NULL | Internal admin notes |
| confidence | FLOAT NULL | AI classification confidence (0-1) |
| created_at | DATETIME | |
| processed_at | DATETIME NULL | When AI triage completed |
| replied_at | DATETIME NULL | When reply email sent to user |

### cost_ledger (from flyfun-common)

Append-only cost tracking table (replaces old credit_ledger for new transactions): `id`, `user_id`, `service`, `action`, `cost`, `metadata_json`, `created_at`.

### cost_config (app-specific)

See [cost-attribution-design.md](./cost-attribution-design.md) for full schema.

### pireps

Pilot weather reports — community observations. Schema in `designs/future/pirep-plan.md`.

Key columns: `id`, `client_uuid` (unique, offline dedup), `submitted_at`, `observed_at`, `latitude`/`longitude`, `gps_altitude_ft`, `reported_altitude_ft`, `in_cloud`, `icing_intensity`/`icing_type`, `turbulence_intensity`, `ceiling_msl_ft`, `tops_msl_ft`/`tops_basis`, `temp_c`, `wind_dir`/`wind_speed_kt`, `remarks`, `aircraft_id` (FK → user_aircraft, SET NULL), `pack_id` (FK → briefing_packs, SET NULL), `source` (manual/inflight/postflight), `user_id` (FK → users, SET NULL for anonymization on account deletion).

**Permission gating**: `pirep_can_view` and `pirep_can_publish` flags in `app_prefs_json`. Admin can set per-user or bulk. Feature-gating 403s must NOT trigger login redirect in frontend (see `apiFetch` in `web/ts/utils.ts`).

**Flight query**: PIREPs queried by `flight_id` match via `pack_id` OR (for PIREPs without pack_id) by user + flight time window (departure ±2h).

**Retention**: PIREPs are never deleted. Packs linked to PIREPs are exempt from T1/T2 retention cleanup.

### device_tokens

APNs push notification tokens (M2, table created but not yet used): `id`, `user_id` (FK → users, CASCADE), `token` (unique), `environment` (sandbox/production), `updated_at`.

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

**Schema.** `magic_link_tokens` (id, email, token_hash, otp_code_hash, created_at, expires_at, used_at, requested_ip) and `magic_link_consume_attempts` (id, ip, attempted_at). Migration `056_magic_link_tokens.py` creates both tables and adds `ix_users_email` since email-keyed lookup runs on every consume.

### API Token Authentication (bot/agent users)

**Token format**: `ff_` prefix + 48 random characters (legacy `wb_` prefix still accepted). SHA-256 hashed before storage.

**Auth flow**: `Authorization: Bearer ff_...` header → hash lookup → user resolution. Falls through to JWT cookie if no Bearer token.

**Admin endpoints**: `POST /api/admin/agents` (create agent + token), `POST .../tokens` (add token), `DELETE .../tokens/{id}` (revoke).

### Admin Auth Unification

Admin endpoints accept both JWT cookies (browser sessions) and Bearer API tokens. The `current_user_id()` dependency checks for a `Bearer` token first, then falls back to the JWT cookie.

### Account Deletion

`DELETE /auth/me/account` triggers `_on_delete_user()` callback: cascade-deletes all flights (which cascade-deletes packs), profiles, aircraft, device tokens, usage records, feedback, and removes artifact files from disk. PIREPs are **anonymized** (user_id/aircraft_id set to NULL) rather than deleted — observation data is preserved.

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
| Concurrent refreshes (server) | 5 | Returns 503 when full; scheduler bypasses queue limit |
| Concurrent refreshes (per user) | 2 | Returns 429 when exceeded; prevents one user hogging the queue |
| PDF generation | 3 concurrent | Semaphore in `api/throttle.py` |
| Skew-T plot generation | 2 concurrent | Semaphore in `api/throttle.py` |

`RefreshRegistry` (in-memory) tracks active refreshes by flight and user, queue depth, and timing. Each `RefreshEntry` carries `user_id` for per-user accounting. `GET /api/admin/metrics` returns live queue state + 24h/7d/30d timing statistics. `GET /api/refresh/stats` returns 7-day average refresh time (public, used by frontend progress hint).

### Refresh Progress UX

During a refresh, the SSE stream and freshness bar show:
- Pipeline stage labels with progress percentage (13 stages, 5%–95%)
- **"You can close this page"** hint with average refresh time (from `/api/refresh/stats`)
- **"Email me when done"** checkbox — passes `?notify_email=true` to the stream endpoint, which calls `send_briefing_email()` on completion

## Auto-Refresh Scheduler

Background scheduler (`scheduler.py`) polls every 10 minutes for flights with `auto_refresh=True` that are due.

**Scheduling formula**: `next_due = min(next_regular, flight_start − 2h)` where `next_regular` is the next occurrence of the user's preferred hour (`auto_refresh_hour`, defaulting to `departure_time.hour − 1`) after the last refresh.

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

## Deploying to Server

### First-time setup

```bash
# 1. Clone repo on server
git clone https://github.com/roznet/flyfun-weather.git && cd flyfun-weather

# 2. Create MySQL database
docker exec -i shared-mysql mysql -u root -p < deploy/03-create-weatherbrief-db.sql

# 3. Create .env with production settings
# (ENVIRONMENT=production, DATABASE_URL, DATA_DIR, AIRPORTS_DB, API keys)

# 4. Copy airports.db into data/
mkdir -p data && cp /path/to/airports.db data/

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
| `AIRPORTS_DB` | Yes | — | Path to euro-aip airports.db |
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

## References

- Server infra: `~/Developer/private/digitalocean/CLAUDE.md`
- Architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Cost attribution: [cost-attribution-design.md](./cost-attribution-design.md)
