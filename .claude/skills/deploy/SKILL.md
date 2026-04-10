---
name: deploy
description: Deploy the weatherbrief app to production on weather.flyfun.aero
disable-model-invocation: true
---

# Deploy weatherbrief to production

Use the SSH user and server IP for flyfun.aero deployment from user config.
The project directory on the server is `flyfun-weather`.

## Pre-flight checks

1. Ensure the working tree is clean (`git status`)
2. Ensure we are on the `main` branch
3. Show the commits that will be deployed: `git log --oneline origin/main..HEAD`
4. **Run tests** (see below)
5. **Check for pending Alembic migrations** (see below)
6. **Check airport database freshness** (see below)
7. **Check standalone verification cycle timing** (see below)
8. Ask the user to confirm before proceeding

## Run tests

**Always run Python tests** — they take ~15s and cover the backend:
```bash
source venv/bin/activate && python -m pytest tests/ --ignore=tests/test_llm_digest.py -q
```
Slow GRIB decode tests are skipped by default (via `addopts` in pyproject.toml).
Run pytest exactly **once** — if output seems slow, check `ps aux | grep pytest` before
launching another instance. Multiple concurrent pytest processes thrash the CPU and make
all of them crawl.

If any test fails, **stop the deploy** and report the failures.

**Run Playwright tests if frontend or API changed.** Check what changed since the last deploy:
```bash
git diff origin/main..HEAD --name-only
```
Run Playwright if ANY of these paths have changes:
- `web/` (frontend code, templates, static files)
- `src/weatherbrief/api/` (API endpoints)
- `src/weatherbrief/models/` (data models served to frontend)
- `configs/` (prompt configs, guidance presets)

```bash
cd web && npx playwright test --reporter=line
```
If Playwright tests fail, **warn the user** but don't block — failures may be from stale selectors rather than real bugs. Let the user decide.

## Check for Alembic migrations

Get the commit currently deployed on the server:
```
ssh <user>@<server> "cd flyfun-weather && git rev-parse HEAD"
```

Then check if any migration files changed between that commit and the local HEAD:
```
git diff <server_commit>..HEAD -- alembic/versions/
```

If new or changed migration files are found, **warn the user prominently** that migrations will need to run after deploy.

## Disk usage check

Before deploying, check disk usage on the server:
```
ssh <user>@<server> "df -h /"
```

If usage is **80% or higher**, warn the user and suggest cleaning the Docker build cache:
```
ssh <user>@<server> "docker builder prune -a -f"
```

Ask the user to confirm the cleanup before running it. After cleanup, re-check `df -h /` to confirm space was freed.

## Check airport database freshness

The airport/navaid database (`nav.db` / `airports.db`) is built locally and must be copied to the server when updated. Compare the `model_metadata` timestamps to detect drift.

**Resolve local DB path** from `.env` (expand `${WORKING_DIR}` manually):
```bash
# Parse WORKING_DIR and AIRPORTS_DB from .env
LOCAL_WORKING_DIR=$(grep '^WORKING_DIR=' .env | cut -d= -f2)
LOCAL_AIRPORTS_DB=$(grep '^AIRPORTS_DB=' .env | cut -d= -f2 | sed "s|\${WORKING_DIR}|${LOCAL_WORKING_DIR}|")
echo "Local DB: ${LOCAL_AIRPORTS_DB}"
sqlite3 "${LOCAL_AIRPORTS_DB}" "SELECT key, updated_at FROM model_metadata WHERE key='statistics';"
```

**Query remote DB** via docker exec (the container has `AIRPORTS_DB` env var pointing to the right file):
```bash
ssh <user>@<server> 'docker exec weatherbrief python3 -c "
import sqlite3, os
conn = sqlite3.connect(os.environ[\"AIRPORTS_DB\"])
for row in conn.execute(\"SELECT key, updated_at FROM model_metadata WHERE key=\\\"statistics\\\"\"):
    print(row[1])
conn.close()
"'
```

**Resolve remote host path** for scp (needed if copying). `AIRPORTS_DB` is the container path; the host-side file is under `HOST_DATA_DIR`:
```bash
# Get HOST_DATA_DIR and DB basename from server .env
REMOTE_HOST_DIR=$(ssh <user>@<server> "grep '^HOST_DATA_DIR=' flyfun-weather/.env | cut -d= -f2")
REMOTE_DB_NAME=$(ssh <user>@<server> "grep '^AIRPORTS_DB=' flyfun-weather/.env | cut -d= -f2 | xargs basename")
# e.g. /mnt/flyfun_data/weather/data/airports.db
```

**Compare timestamps:**
- If local `updated_at` is newer than remote → the server has a stale airport database
- **Offer to copy**: ask the user if they want to update the remote DB
- If they confirm:
```bash
# Copy local DB to server (using resolved paths from above)
scp "${LOCAL_AIRPORTS_DB}" <user>@<server>:"${REMOTE_HOST_DIR}/${REMOTE_DB_NAME}"

# Fix ownership (container runs as UID 2000)
ssh <user>@<server> "sudo chown 2000:2000 ${REMOTE_HOST_DIR}/${REMOTE_DB_NAME}"

# Restart container to reload the cached model
ssh <user>@<server> "cd flyfun-weather && docker compose restart"
```
- If the timestamps match or remote is newer, report "Airport DB is up to date" and move on

## Check standalone verification cycle timing

The standalone verification loop runs at sample hours [6, 9, 12, 15, 18] UTC (full cycles at 6 and 18, light cycles at the others). A deploy restarts the container, killing any in-progress cycle. Check whether a cycle might be running or about to start:

```bash
ssh <user>@<server> 'docker logs --since 10m weatherbrief 2>&1 | grep -iE "standalone|sleeping|Light cycle|Full cycle|phase"'
```

**Interpret the output:**
- If you see `sleeping Xs until next sample hour` — the loop is idle. Parse the sleep duration and `started_at` to estimate when the next cycle fires. If it's more than 5 minutes away, safe to deploy.
- If you see `Light cycle` or `Full cycle` log lines but no subsequent `sleeping` or `Recorded` line — a cycle is likely **in progress**. Warn the user: *"A standalone verification cycle appears to be running. Deploying now will interrupt it. Wait a few minutes or proceed?"*
- If no standalone lines appear in the last 10 minutes — the loop is between cycles, safe to deploy.

Full cycles (hours 6, 18) take ~2 minutes; light cycles take ~1 minute. If a cycle just started, suggest waiting 2-3 minutes.

**If a cycle was interrupted** (user chose to deploy anyway, or the deploy already happened), offer to re-trigger it after the container is healthy:
```bash
ssh <user>@<server> "docker exec weatherbrief python -m weatherbrief.verify standalone"
```
This runs a single full cycle (fetch forecasts + observations + score) and exits. Safe to run alongside the loop — the loop's next scheduled cycle will proceed normally.

## Deploy steps

1. Push to remote: `git push origin main`
2. SSH to the server and deploy:
   ```
   ssh <user>@<server> "cd flyfun-weather && git pull && docker compose up -d --build"
   ```
3. **If migrations were detected in pre-flight**, run them now:
   ```
   ssh <user>@<server> "docker exec weatherbrief alembic upgrade head"
   ```
4. Wait a few seconds, then verify the health check:
   ```
   ssh <user>@<server> "docker inspect --format='{{.State.Health.Status}}' weatherbrief"
   ```
5. Also check the endpoint is responding:
   ```
   curl -s -o /dev/null -w '%{http_code}' https://weather.flyfun.aero/health
   ```

## If something goes wrong

- Check logs: `ssh <user>@<server> "docker logs --tail 50 weatherbrief"`
- The container runs on port 8020 internally
- Docker container runs as UID 2000 (`app` user) — data volume must be chowned to match
- `docker compose` (v2 syntax, NOT `docker-compose`)
