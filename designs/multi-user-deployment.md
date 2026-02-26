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

`ENVIRONMENT=development` (from `.env`) activates dev mode:

- **Auth bypass**: Middleware auto-injects a dev user (`dev-user-001`, email `dev@localhost`). No login page needed.
- **SQLite instead of MySQL**: defaults to `sqlite:///data/weatherbrief.db`. SQLAlchemy abstracts the dialect.
- **Same file storage and API routes**: everything works identically.

On first startup: creates DB, tables, and dev user automatically. No manual setup needed.

| Concern | Development | Production |
|---------|------------|------------|
| Auth | Auto-injected dev user | Google OAuth + JWT |
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
| provider | VARCHAR(20) | `google`, `apple`, or `api` (bot/agent) |
| provider_sub | VARCHAR(255) UNIQUE | OAuth subject ID |
| email | VARCHAR(255) | From OAuth profile |
| display_name | VARCHAR(255) | |
| approved | BOOLEAN DEFAULT TRUE | Auto-approved on signup; admin can revoke |
| credit_balance | FLOAT DEFAULT 500.0 | Cached credit balance (derived from ledger) |
| created_at | DATETIME | |
| last_login_at | DATETIME | |

### user_preferences

| Column | Type | Notes |
|--------|------|-------|
| user_id | VARCHAR(36) PK FK | |
| defaults_json | TEXT | JSON: `{"cruise_altitude_ft": 8000, "flight_ceiling_ft": 18000, "models": ["gfs","ecmwf","icon"]}` |
| encrypted_autorouter_creds | TEXT | Fernet-encrypted JSON: `{"username": "...", "password": "..."}` |
| digest_config_json | TEXT | JSON: `{"config_name": "default"}` |
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
| private | BOOLEAN DEFAULT FALSE | Hide from other users' shared briefing links |
| auto_refresh | BOOLEAN DEFAULT FALSE | Enable background auto-refresh |
| auto_refresh_hour | INT NULL | Preferred UTC hour for daily refresh (default: target_time − 1) |
| last_auto_refresh_at | DATETIME NULL | Timestamp of last auto-refresh (for scheduling) |
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

### feedback

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(64) FK | → users.id |
| flight_id | VARCHAR(256) | |
| pack_timestamp | VARCHAR(64) | Specific pack within the flight |
| category | VARCHAR(32) | `data_issue`, `too_conservative`, `too_optimistic`, `incorrect_interpretation`, `other` |
| comment | TEXT | Required, non-empty |
| created_at | DATETIME | |

### cost_config & credit_ledger

See [cost-attribution-design.md](./cost-attribution-design.md) for full schema.

## Authentication

### OAuth flow

Google Sign-In via `authlib`. JWT (HS256, 7-day expiry) in httpOnly secure cookie.

1. User clicks "Sign in with Google" → Google consent screen
2. Callback exchanges code for ID token → lookup/create user (auto-approved)
3. On first login: welcome email to user + notification email to admins
4. Issue JWT cookie; all `/api/*` routes validate via FastAPI dependency

Admin identity: `ADMIN_EMAILS` env var (comma-separated). Dev user is always admin.

### API Token Authentication (bot/agent users)

**Token format**: `wb_` prefix + 48 random characters. SHA-256 hashed before storage.

**Auth flow**: `Authorization: Bearer wb_...` header → hash lookup → user resolution. Falls through to JWT cookie if no Bearer token.

**Admin endpoints**: `POST /api/admin/agents` (create agent + token), `POST .../tokens` (add token), `DELETE .../tokens/{id}` (revoke).

### Dev mode bypass

`ENVIRONMENT=development` → auth middleware injects dev user, skips JWT.

## Rate Limiting & Usage Tracking

| Resource | Limit | Rationale |
|----------|-------|-----------|
| Open-Meteo calls | 50/day per user | Free tier: 10K/day total |
| GRAMET calls | 10/day per user | Autorouter courtesy |
| LLM digest calls | 20/day per user | Token cost |

Every refresh logged to `briefing_usage`. Freshness check via `check_freshness()` in `fetch/model_status.py`.

## Auto-Refresh Scheduler

Background scheduler (`scheduler.py`) polls every 10 minutes for flights with `auto_refresh=True` that are due.

**Scheduling formula**: `next_due = min(next_regular, flight_start − 2h)` where `next_regular` is the next occurrence of the user's preferred hour (`auto_refresh_hour`, defaulting to `target_time_utc − 1`) after the last refresh.

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

## References

- Server infra: `~/Developer/private/digitalocean/CLAUDE.md`
- Architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Cost attribution: [cost-attribution-design.md](./cost-attribution-design.md)
