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
5. Ask the user to confirm before proceeding

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
