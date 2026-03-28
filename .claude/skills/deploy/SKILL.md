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
4. **Check for pending Alembic migrations** (see below)
5. **Check airport database freshness** (see below)
6. Ask the user to confirm before proceeding

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
