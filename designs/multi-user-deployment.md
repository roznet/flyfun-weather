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
# 1. Activate venv (has all deps)
source venv/bin/activate

# 2. Ensure .env has development mode
#    ENVIRONMENT=development
#    AIRPORTS_DB=data/airports.db
#    (no MYSQL_*, no GOOGLE_CLIENT_*, no APPLE_CLIENT_*)

# 3. Run the app
uvicorn weatherbrief.api.app:create_app --factory --reload --port 8020

# 4. Open http://localhost:8020 — logged in as dev user, no auth needed
```

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
| approved | BOOLEAN DEFAULT FALSE | Admin flips to grant access |
| created_at | DATETIME | |
| last_login_at | DATETIME | |

### user_preferences

| Column | Type | Notes |
|--------|------|-------|
| user_id | VARCHAR(36) PK FK | |
| default_cruise_altitude_ft | INT DEFAULT 8000 | |
| default_flight_ceiling_ft | INT DEFAULT 18000 | |
| default_models | VARCHAR(255) DEFAULT 'gfs,ecmwf,icon' | Comma-separated |
| autorouter_username | VARCHAR(255) NULL | Encrypted (Fernet) |
| autorouter_password | BLOB NULL | Encrypted (Fernet) |
| digest_config | VARCHAR(50) DEFAULT 'default' | LLM digest config name |

### flights

| Column | Type | Notes |
|--------|------|-------|
| id | VARCHAR(100) PK | `{user_id}_{route_slug}_{target_date}` |
| user_id | VARCHAR(36) FK | |
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
| artifact_path | VARCHAR(500) | Relative path to pack directory |

### usage_log

| Column | Type | Notes |
|--------|------|-------|
| id | INT AUTO_INCREMENT PK | |
| user_id | VARCHAR(36) FK | |
| timestamp | DATETIME | |
| call_type | VARCHAR(30) | `open_meteo`, `gramet`, `llm_tokens`, `dwd_text` |
| detail | JSON NULL | e.g. `{"model":"gfs","points":26}` or `{"tokens_in":2000,"tokens_out":500}` |
| skipped | BOOLEAN DEFAULT FALSE | True if a freshness check determined no update needed |

**Note on usage_log.skipped**: A future `should_update_briefing()` function will check whether NWP model data has been refreshed since the last fetch. When it determines no new data is available, the pipeline skips the fetch and logs with `skipped=true`. This avoids wasting API quota on redundant calls. The freshness check implementation is independent work — the schema is ready for it.

## File Storage Layout

Large artifacts stay on disk (not in DB). User-scoped directories:

```
data/packs/
└── {user_id}/
    └── {flight_id}/
        └── {safe_timestamp}/
            ├── snapshot.json
            ├── cross_section.json
            ├── gramet.png
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
4. Lookup user by `(provider, provider_sub)` — create if first login
5. Check `users.approved = true` — if not, show "awaiting approval" page
6. Issue JWT (HS256, 7-day expiry) in httpOnly secure cookie
7. All `/api/*` routes validate JWT via FastAPI dependency

### Approval workflow

After a user authenticates for the first time, their `approved` flag is `false`. Admin (you) flips it to `true` via:
- Direct SQL: `UPDATE users SET approved=1 WHERE email='friend@gmail.com'`
- Future: simple admin page listing pending users

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

Every external API call is logged to `usage_log` with user_id and call_type. This enables:
- Per-user usage dashboard (settings page)
- Admin overview of total consumption
- Cost attribution if needed later

### Freshness check (future)

The rate limiting design assumes a `should_update_briefing(flight, last_pack) -> bool` function will exist to check whether NWP models have published new data since the last fetch. When this returns `false`, the pipeline logs `skipped=true` in usage_log and returns the existing pack. This is **separate work** — not part of the multi-user deployment phases.

## Encrypted Credential Storage

Autorouter credentials encrypted at rest using Fernet symmetric encryption.

- Encryption key: `CREDENTIAL_ENCRYPTION_KEY` env var (generated via `cryptography.fernet.Fernet.generate_key()`)
- Encrypt on write (settings save), decrypt on read (GRAMET fetch)
- Key stored only in `.env` on server (not in repo, not in DB)
- If key is lost, users re-enter credentials (no recovery needed)

## Phases

### Phase 1: Docker + DB + Deploy

**Goal**: App running at weather.flyfun.aero, single-user (you), no auth yet.

- [ ] Create `Dockerfile` for weatherbrief (Python 3.13, uvicorn, euro_aip from GitHub)
- [ ] Create `docker-compose.yml` joining `shared-services` network
- [ ] Create MySQL init script `03-create-weatherbrief-db.sql` in shared-infra
- [ ] Add SQLAlchemy models + Alembic migrations for all tables
- [ ] Refactor `storage/flights.py` from file-based to DB-backed
- [ ] Refactor `storage/snapshots.py` — metadata in DB, files in volume
- [ ] Add `weather.flyfun.aero.caddy` to sites-enabled
- [ ] Add DNS A record for `weather.flyfun.aero` → 161.35.35.15
- [ ] Dev mode: SQLite fallback when `ENVIRONMENT=development`
- [ ] Test: full pipeline works via API and CLI

### Phase 2: Auth + Multi-User

**Goal**: Google/Apple OAuth, JWT sessions, user-scoped data.

- [ ] Add `authlib` dependency
- [ ] Implement `/auth/login/{provider}`, `/auth/callback/{provider}`, `/auth/logout`
- [ ] JWT middleware: extract user_id, inject into request state
- [ ] Approval gate: unapproved users see "awaiting approval" message
- [ ] All API routes scoped by user_id (flights, packs, artifacts)
- [ ] Login page with provider buttons (minimal HTML/CSS)
- [ ] Dev mode bypass: auto-inject dev user, skip JWT validation
- [ ] Register Google OAuth app (console.cloud.google.com)
- [ ] Register Apple Sign-In (developer.apple.com)
- [ ] Test: two users see only their own flights

### Phase 3: Preferences + Credentials

**Goal**: Per-user settings, encrypted autorouter credentials.

- [ ] Settings page UI (default altitude, models, autorouter creds)
- [ ] `CREDENTIAL_ENCRYPTION_KEY` env var + Fernet encrypt/decrypt
- [ ] GRAMET fetch uses per-user autorouter credentials
- [ ] Preferences applied as defaults when creating flights
- [ ] Test: user with autorouter creds gets GRAMET, user without doesn't

### Phase 4: Usage Tracking + Rate Limits

**Goal**: Per-user call counting, rate limits, usage visibility.

- [ ] Logging middleware: write to `usage_log` on every external call
- [ ] Rate limiter: check daily counts before fetch, return 429 if exceeded
- [ ] Usage summary on settings page (calls today/this week)
- [ ] Admin endpoint or page: all users + usage overview
- [ ] Hook point for `should_update_briefing()` freshness check (logs skipped=true)
- [ ] Test: rate limit triggers correctly, usage counts accurate

## References

- Existing Docker patterns: `~/Developer/public/flyfun-apps/main/designs/DOCKER_DEPLOYMENT.md`
- Server infra: `~/Developer/private/digitalocean/CLAUDE.md`
- Shared MySQL: `~/Developer/private/digitalocean/shared-infra/docker-compose.yml`
- Caddy sites: `~/Developer/private/digitalocean/flyfun.aero/etc/caddy/sites-enabled/`
- Current WeatherBrief architecture: [architecture.md](./architecture.md)
- Current data models: [data-models.md](./data-models.md)
- Current fetch layer: [fetch.md](./fetch.md)
