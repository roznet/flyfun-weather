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

## Development Mode

Local development runs without Docker, OAuth, or MySQL — everything works out of the box.

### How it works

`ENVIRONMENT=development` (from `.env`) activates dev mode:

- **Auth bypass**: No OAuth flow. A middleware auto-injects a dev user (`dev-user-001`, email `dev@localhost`) into every request. No login page needed.
- **SQLite instead of MySQL**: DB connection string defaults to `sqlite:///data/weatherbrief.db` — no MySQL setup required. SQLAlchemy abstracts the dialect difference.
- **Same file storage**: Artifacts written to `data/packs/{user_id}/...` as in production.
- **Same API routes**: Everything works identically — the dev user is a real user row in the dev DB.

### Running locally

```bash
# 1. Activate venv
#    If in the main checkout:
source venv/bin/activate
#    If in a git worktree (e.g. multi-users/):
source ../main/venv/bin/activate

# 2. Install deps (first time or after pyproject.toml changes)
pip install -e ".[dev]"

# 3. Ensure .env has development mode (this is the default):
#    ENVIRONMENT=development
#    DATA_DIR=data
#    AIRPORTS_DB=<path to airports.db>
#    (no DATABASE_URL needed — SQLite is used automatically)

# 4. Run the app
uvicorn weatherbrief.api.app:app --reload --port 8020

# 5. Open http://localhost:8020 — logged in as dev user, no auth needed
```

On first startup, the app automatically:
- Creates `data/weatherbrief.db` (SQLite)
- Creates all tables
- Inserts the dev user (`dev-user-001`)

No manual DB setup or migration step needed for development.

### Production vs development summary

| Concern | Development | Production |
|---------|------------|------------|
| Auth | Auto-injected dev user | Google/Apple OAuth + JWT |
| Database | SQLite (file) | MySQL (shared-services) |
| Artifacts | `data/packs/...` | `/app/data/packs/...` (volume) |
| TLS | None (localhost) | Caddy auto-TLS |
| Rate limits | Disabled | Enforced per-user |
| Credential encryption | Same (Fernet) | Same (Fernet) |

## Database Schema (MySQL / SQLite via SQLAlchemy)

### users

| Column | Type | Notes |
|--------|------|-------|
| id | VARCHAR(36) PK | UUID |
| provider | VARCHAR(20) | `google` or `apple` |
| provider_sub | VARCHAR(255) UNIQUE | OAuth subject ID |
| email | VARCHAR(255) | From OAuth profile |
| display_name | VARCHAR(255) | |
| approved | BOOLEAN DEFAULT TRUE | Auto-approved on signup; admin can revoke |
| created_at | DATETIME | |
| last_login_at | DATETIME | |

### user_preferences

JSON-based storage for flexibility — individual settings serialized rather than separate columns.

| Column | Type | Notes |
|--------|------|-------|
| user_id | VARCHAR(36) PK FK | |
| defaults_json | TEXT | JSON: `{"cruise_altitude_ft": 8000, "flight_ceiling_ft": 18000, "models": ["gfs","ecmwf","icon"]}` |
| encrypted_autorouter_creds | TEXT | Fernet-encrypted JSON: `{"username": "...", "password": "..."}` |
| digest_config_json | TEXT | JSON: `{"config_name": "default"}` |

### flight_profiles

Named parameter templates for flights. Settings stored as flexible JSON.

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(36) FK | Cascade delete with user |
| name | VARCHAR(100) DEFAULT "Default" | Unique per user (case-insensitive) |
| is_default | BOOLEAN DEFAULT FALSE | One default per user |
| settings_json | TEXT | JSON: cruise_altitude_ft, flight_ceiling_ft, speed_kt, models, advisory_models, gramet/llm/icing toggles, advisory enable/params |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### flights

| Column | Type | Notes |
|--------|------|-------|
| id | VARCHAR(100) PK | `{user_id}_{route_slug}_{target_date}` |
| user_id | VARCHAR(36) FK | |
| profile_id | INT FK NULL | → flight_profiles.id, SET NULL on delete |
| route_name | VARCHAR(100) | |
| waypoints | JSON | `["EGTK","LFPB","LSGS"]` |
| target_date | DATE | |
| target_time_utc | INT DEFAULT 9 | |
| cruise_altitude_ft | INT DEFAULT 8000 | |
| flight_ceiling_ft | INT DEFAULT 18000 | |
| flight_duration_hours | FLOAT DEFAULT 0.0 | |
| created_at | DATETIME | |

### briefing_packs

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| flight_id | VARCHAR(100) FK | |
| fetch_timestamp | DATETIME | |
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
| token_hash | VARCHAR(64) UNIQUE | SHA-256 hex digest of `wb_...` token |
| name | VARCHAR(100) | Human-readable label (e.g., "CI bot") |
| created_at | DATETIME | |
| expires_at | DATETIME NULL | Optional expiry |
| last_used_at | DATETIME NULL | Updated on each authenticated request |
| revoked | BOOLEAN DEFAULT FALSE | Soft-delete for audit trail |

### briefing_usage

Per-briefing usage tracking with fixed columns per service (better for aggregation than flexible call_type approach).

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

## File Storage Layout

Large artifacts stay on disk (not in DB). User-scoped directories:

```
data/packs/
└── {user_id}/
    └── {flight_id}/
        └── {safe_timestamp}/
            ├── snapshot.json
            ├── cross_section.json
            ├── route_analyses.json
            ├── elevation_profile.json
            ├── gramet.pdf
            ├── skewt/
            │   ├── EGTK_gfs.png
            │   └── ...
            ├── digest.md
            └── digest.json
```

In Docker, `data/` is a volume mount. Cleanup policy TBD (oldest packs beyond N per flight, or older than M days).

## Authentication

### OAuth providers

Google and Apple Sign-In via `authlib` (lightweight, no Firebase dependency).

| Endpoint | Purpose |
|----------|---------|
| `GET /auth/login/{provider}` | Redirect to Google/Apple OAuth consent |
| `GET /auth/callback/{provider}` | Exchange code → lookup/create user → issue JWT |
| `POST /auth/logout` | Clear JWT cookie |
| `GET /auth/me` | Return current user info |

### Flow

1. User clicks "Sign in with Google" → redirected to Google consent screen
2. Google redirects back to `/auth/callback/google` with auth code
3. Server exchanges code for ID token, extracts `sub`, `email`, `name`
4. Lookup user by `(provider, provider_sub)` — create if first login (auto-approved)
5. On first login: send welcome email to user + notification email to admins
6. Check `users.approved = true` — if revoked, show "account inactive" page
7. Issue JWT (HS256, 7-day expiry) in httpOnly secure cookie
7. All `/api/*` routes validate JWT via FastAPI dependency

### Auto-approval & notifications

New users are **auto-approved** on first login — no admin action needed. On signup:
1. User account created with `approved=True`
2. **Welcome email** sent to user (site link + help guide)
3. **Notification email** sent to admins (FYI — user name, email, admin page link)

Admin can still **revoke** access by setting `approved=False` via the admin page. Revoked users see an "account inactive" message on login. The one-click HMAC-signed approval link in admin.py still works for re-enabling revoked users.

Admin identity is controlled by the `ADMIN_EMAILS` env var (comma-separated). In dev mode, the dev user is always treated as admin.

### API Token Authentication (bot/agent users)

For programmatic access (bots, agents, automation), admins can create agent users with API tokens via the admin page.

**Token format**: `wb_` prefix + 48 random characters. Tokens are SHA-256 hashed before storage — the plaintext is shown once at creation and cannot be recovered.

**Database**: `api_tokens` table with `token_hash`, `name`, `expires_at`, `last_used_at`, `revoked` fields. Each token belongs to a user via `user_id` FK.

**Auth flow**: `Authorization: Bearer wb_...` header → hash lookup → user resolution. Falls through to JWT cookie auth if no Bearer token. Both auth methods produce the same `user_id` dependency.

**Admin endpoints**:
- `POST /api/admin/agents` — create agent user (provider=`api`, auto-approved) with initial token
- `POST /api/admin/agents/{user_id}/tokens` — create additional token for existing agent
- `DELETE /api/admin/agents/{user_id}/tokens/{token_id}` — revoke token

**Admin UI**: Agent management section in `admin.html` with create/revoke controls, token display (copy-once), and last-used timestamps.

### Dev mode bypass

When `ENVIRONMENT=development`, the auth middleware skips JWT validation and injects a dev user. The `/auth/*` endpoints still exist but aren't needed.

## Rate Limiting & Usage Tracking

### Per-user limits

| Resource | Limit | Rationale |
|----------|-------|-----------|
| Open-Meteo calls | 50/day per user | Free tier: 10K/day total |
| GRAMET calls | 10/day per user | Autorouter courtesy |
| LLM digest calls | 20/day per user | Token cost |

Limits checked before each external call. If exceeded, return 429 with message.

### Tracking

Every briefing refresh is logged to `briefing_usage` with per-service counters. This enables:
- Per-user usage dashboard (settings page)
- Admin overview of total consumption
- Cost attribution if needed later

### Freshness Check (Implemented)

The `check_freshness()` function in `fetch/model_status.py` queries Open-Meteo metadata to compare current model init times against the previous pack's `model_init_times`. The freshness endpoint (`GET /api/flights/{id}/packs/freshness`) returns a `DataStatus` with `fresh`, `stale_models`, and `model_init_times`. The frontend shows a freshness bar with "New data available" link when stale, and admins see a force-refresh link even when data is fresh.

## Encrypted Credential Storage

Autorouter credentials encrypted at rest using Fernet symmetric encryption.

- Encryption key: `CREDENTIAL_ENCRYPTION_KEY` env var (generated via `cryptography.fernet.Fernet.generate_key()`)
- Encrypt on write (settings save), decrypt on read (GRAMET fetch)
- Key stored only in `.env` on server (not in repo, not in DB)
- If key is lost, users re-enter credentials (no recovery needed)

## Phases

### Phase 1: Docker + DB + Deploy (Done)

**Goal**: App running at weather.flyfun.aero, single-user (you), no auth yet.

- [x] Create `Dockerfile` for weatherbrief (Python 3.13, uvicorn, euro_aip from GitHub)
- [x] Create `docker-compose.yml` joining `shared-services` network
- [x] Create MySQL init script `deploy/03-create-weatherbrief-db.sql`
- [x] Add SQLAlchemy models + Alembic migrations for all 5 tables
- [x] Refactor `storage/flights.py` from file-based to DB-backed
- [x] Add `deploy/weather.flyfun.aero.caddy` reverse proxy config
- [x] Add DNS A record for `weather.flyfun.aero` → 161.35.35.15
- [x] Deploy to server (copy repo, run `docker-compose up -d`)
- [x] Dev mode: SQLite fallback when `ENVIRONMENT=development`
- [x] Test: API works via Docker (health, flights CRUD)

### Phase 2: Auth + Multi-User (Done)

**Goal**: Google/Apple OAuth, JWT sessions, user-scoped data.

- [x] Add `authlib` dependency
- [x] Implement `/auth/login/{provider}`, `/auth/callback/{provider}`, `/auth/logout`
- [x] JWT dependency: extract user_id via Depends(current_user_id)
- [x] Auto-approve new users on first login; send welcome + admin notification emails
- [x] All API routes scoped by user_id (flights, packs, artifacts)
- [x] Login page with provider buttons (minimal HTML/CSS)
- [x] Dev mode bypass: auto-inject dev user, skip JWT validation
- [x] Register Google OAuth app (console.cloud.google.com)
- [ ] Register Apple Sign-In (developer.apple.com) — deferred
- [x] Test: two users see only their own flights

### Phase 3: Preferences + Credentials (Done)

**Goal**: Per-user settings, encrypted autorouter credentials.

- [x] Settings page UI (default altitude, models, autorouter creds)
- [x] `CREDENTIAL_ENCRYPTION_KEY` env var + Fernet encrypt/decrypt
- [x] GRAMET fetch uses per-user autorouter credentials (with per-user token cache)
- [x] Preferences applied as defaults when creating flights
- [x] Flight IDs include parameter hash to allow same route+date with different time/altitude
- [x] Test: preferences CRUD, credentials never in cleartext, flight defaults applied

### Phase 4: Usage Tracking + Rate Limits ✓

**Goal**: Per-user call counting, rate limits, usage visibility.

- [x] Per-briefing usage logging (`briefing_usage` table replacing `usage_log`)
- [x] Daily rate limiter: Open-Meteo 50/day, GRAMET 10/day, LLM digest 20/day → HTTP 429
- [x] Usage summary on settings page (today with quota bars + monthly totals)
- [x] LLM token extraction via `include_raw=True` on structured output
- [x] `GET /api/user/usage` endpoint with today/month aggregation
- [x] Test: rate limit triggers, usage counts, summary aggregation (13 tests)
- [x] Admin page with user list, usage overview, and user management
- [x] HMAC-signed approval links for re-enabling revoked users (7-day expiry)
- [x] Auto-approve + welcome email on signup; admin notification email (`ADMIN_EMAILS` env var)
- [x] Admin gate: dev user always admin; production checks JWT email against `ADMIN_EMAILS`
- [x] Shareable briefing links: any authenticated user can view any flight's briefings
- [x] Ownership model: only flight owner can refresh/delete; frontend hides action buttons for non-owners

### Phase 5: Flight Profiles ✓

**Goal**: Named parameter profiles for flights — reusable templates for altitude, models, advisory settings.

- [x] `flight_profiles` table with flexible JSON settings (migration 004)
- [x] Profile CRUD API (`/user/profiles`): list, create, update, delete, duplicate
- [x] Auto-create default profile on first access (migrates legacy `defaults_json`)
- [x] One default profile per user, enforced by API
- [x] Flights link to profiles via `profile_id` FK (nullable, SET NULL on delete)
- [x] Briefing refresh applies profile settings (models, toggles, advisory config)
- [x] Advisory recalculation uses profile's enabled/params overrides
- [x] Settings UI: profile selector, create/rename/duplicate/delete, single form for all settings
- [x] Icing severity enhancement toggle (`icing_severity_enhance`) in profile settings
- [x] Advisory aggregation mode (`advisories.aggregation`): worst vs majority — see [advisories.md](./advisories.md)

### UX Improvements ✓

- [x] Flight list sorted by flight time descending (future flights first), not creation time
- [x] Past flight detection: `isFlightPast()` compares current UTC against start + duration
- [x] "Past" badge on flight cards for flights whose end time has passed
- [x] Refresh button disabled on briefing page for past flights (no auto-refresh either)
- [x] Settings page defaults to Flight Profiles tab (not Account)

## Deploying to Server

### First-time setup

```bash
# 1. On the server, clone the repo
git clone https://github.com/roznet/flyfun-weather.git
cd flyfun-weather
git checkout main

# 2. Create the MySQL database (on the shared MySQL container)
docker exec -i shared-mysql mysql -u root -p < deploy/03-create-weatherbrief-db.sql
# Edit the SQL first to replace CHANGE_ME with a real password

# 3. Create .env with production settings
cat > .env <<'ENVEOF'
ENVIRONMENT=production
DATABASE_URL=mysql+pymysql://weatherbrief:YOUR_PASSWORD@shared-mysql/weatherbrief
DATA_DIR=/app/data
AIRPORTS_DB=/app/data/airports.db
# Add API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)
ENVEOF

# 4. Copy airports.db into the data directory
mkdir -p data
cp /path/to/airports.db data/

# 5. Build and start
docker-compose up -d --build

# 6. Run Alembic migrations
docker exec weatherbrief alembic upgrade head

# 7. Add Caddy site config
cp deploy/weather.flyfun.aero.caddy /etc/caddy/sites-enabled/
caddy reload --config /etc/caddy/Caddyfile
```

### Updating

```bash
git pull
docker-compose up -d --build
# If there are new migrations:
docker exec weatherbrief alembic upgrade head
```

### Key files

| File | Purpose |
|------|---------|
| `Dockerfile` | App image (python:3.13-slim, non-root UID 2000) |
| `docker-compose.yml` | Service config, port 8020, shared-services network |
| `.dockerignore` | Excludes .env, tests, data, venv from build context |
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
| `CREDENTIAL_ENCRYPTION_KEY` | Prod only | derived from JWT_SECRET in dev | Fernet key for encrypting autorouter creds |
| `ADMIN_EMAILS` | Prod only | — | Comma-separated admin email addresses; dev user is always admin |

## References

- Existing Docker patterns: `~/Developer/public/flyfun-apps/main/designs/DOCKER_DEPLOYMENT.md`
- Server infra: `~/Developer/private/digitalocean/CLAUDE.md`
- Shared MySQL: `~/Developer/private/digitalocean/shared-infra/docker-compose.yml`
- Caddy sites: `~/Developer/private/digitalocean/flyfun.aero/etc/caddy/sites-enabled/`
- Current WeatherBrief architecture: [architecture.md](./architecture.md)
- Current data models: [data-models.md](./data-models.md)
- Current fetch layer: [fetch.md](./fetch.md)
