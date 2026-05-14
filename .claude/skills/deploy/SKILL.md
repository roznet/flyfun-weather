---
name: deploy
description: Deploy the weatherbrief app to production on weather.flyfun.aero
disable-model-invocation: true
---

# Deploy weatherbrief to production

Use the SSH user and server IP for flyfun.aero deployment from user config.
The project directory on the server is `flyfun-weather`.

## Pre-flight checks

> **CRITICAL — what "to deploy" means.** A deploy ships whatever is on `origin/main` to the server. The right comparison is **server's deployed commit → `origin/main`**, NOT local working tree → `origin/main`. Past mistakes:
> - Using `git log origin/main..HEAD` to show "commits to deploy" — this shows what's *local but not pushed*, which is the opposite of what we want. If local is up to date with `origin/main`, that command is empty even when the server is many commits behind.
> - Stopping the deploy because the working tree is dirty — uncommitted files are unrelated to what's on `origin/main`. List them so the user can decide whether to ignore them, but don't block.
>
> The two anchor SHAs for the whole deploy are:
> - `LOCAL_SHA` = `git rev-parse origin/main` (after `git fetch`) — what we will deploy
> - `SERVER_SHA` = `ssh <user>@<server> "cd flyfun-weather && git rev-parse HEAD"` — what is currently running
>
> Use these two everywhere a comparison is needed (commit list, migrations diff, changed-paths for Playwright).

1. `git fetch origin` so `origin/main` is current
2. Ensure we are on the `main` branch (`git branch --show-current`)
3. Capture `SERVER_SHA` and `LOCAL_SHA` (definitions above)
4. Show the commits that will be deployed: `git log --oneline ${SERVER_SHA}..${LOCAL_SHA}`
   - If empty → server is already up to date; tell the user and stop (nothing to deploy).
5. Show uncommitted local changes (`git status --short`) — **just list them**, do not block. Ask the user only if they look related to work that should be in this deploy.
6. **Run tests** (see below)
7. **Check for pending Alembic migrations** (see below)
8. **Check airport database freshness** (see below)
9. **Check standalone verification cycle timing** (see below)
10. Ask the user to confirm before proceeding

## Run tests

**Always run Python tests** — full suite is ~85s and covers the backend:
```bash
source venv/bin/activate && python -m pytest tests/ --ignore=tests/test_llm_digest.py -q
```
Slow GRIB decode tests are skipped by default (via `addopts` in pyproject.toml).

**Discipline:**
- Run pytest exactly **once** with `timeout: 600000`. If output seems slow,
  check `ps aux | grep pytest` before launching another instance —
  concurrent pytest processes thrash the CPU and turn 85s into many minutes.
- **Do not pipe pytest through `tail`/`head`.** The summary line
  (`N passed, M failed`) is what we need; piping hides it behind any
  late stderr output (e.g. background-thread tracebacks that print
  *after* pytest's summary). Capture full output instead and grep for
  `passed|failed|error` to fish the summary out:
  ```bash
  source venv/bin/activate && python -m pytest tests/ --ignore=tests/test_llm_digest.py -q 2>&1 \
    | tee /tmp/pytest.out | grep -E "passed|failed|error" | tail -5
  ```
  If a failure shows up, the full log is in `/tmp/pytest.out` — no need to re-run.

If any test fails, **stop the deploy** and report the failures.

**Check what changed since the last deploy** (server → origin/main, not local working tree):
```bash
git diff ${SERVER_SHA}..${LOCAL_SHA} --name-only
```

**Run Vitest unit tests if `web/` changed.** Pure TypeScript unit tests on frontend code (~300ms). Hard gate — these are deterministic, a failure is a real bug:
```bash
cd web && npm test
```
If any vitest test fails, **stop the deploy** and report the failures.

> Vitest does not need to run on backend-only changes — fixtures are pure TS and tests don't hit the API. Any backend change that affects the frontend lands as a `web/ts/` edit, which the `web/` trigger already catches.

**Run Playwright tests if frontend or API changed.** Run Playwright if ANY of these paths have changes:
- `web/` (frontend code, templates, static files)
- `src/weatherbrief/api/` (API endpoints)
- `src/weatherbrief/models/` (data models served to frontend)
- `configs/` (prompt configs, guidance presets)

```bash
cd web && npx playwright test --reporter=line
```
If Playwright tests fail, **warn the user** but don't block — failures may be from stale selectors rather than real bugs. Let the user decide.

## Check for Alembic migrations

Using the `SERVER_SHA` and `LOCAL_SHA` captured in pre-flight, check if any migration files changed between what's running and what we're about to deploy:
```
git diff ${SERVER_SHA}..${LOCAL_SHA} -- alembic/versions/
```

> Do **not** use `HEAD` here — `HEAD` may include local commits not yet pushed, which won't reach the server. Always diff against `origin/main` (= `LOCAL_SHA`).

If new or changed migration files are found, **warn the user prominently** that migrations will need to run after deploy.

**Also verify a single alembic head.** A long-lived branch may add a migration numbered `N` while main has independently grown past `N`, leaving two heads both descending from the same parent. `alembic upgrade head` will fail with `Multiple head revisions` in that case. CI (`.github/workflows/alembic-check.yml`) enforces this on PRs, but run it locally as a belt-and-suspenders check against what's about to deploy:
```bash
source venv/bin/activate && alembic heads | grep -c '(head)'
```
Must print `1`. If it prints `2+`, the migration with the lower number needs to be renumbered to descend from the current head (update `revision`, `down_revision`, and the filename).

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

The airport/navaid database (`nav.db`, built by the `euro_aip` submodule inside `rzflight`) is copied to the server when updated. Both dev and prod point `AIRPORTS_DB` at `nav.db`; there is no longer any dev/prod filename drift (the droplet used to call it `airports.db` — aligned to `nav.db` on 2026-04-24). Compare the `model_metadata` timestamps to detect staleness.

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
# e.g. /mnt/flyfun_data/weather/data/nav.db
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

Cycle duration depends on what it samples:
- **Fetch-only cycle** (3 models × 619 airports × 7 chunks each): ~20-25 minutes. The completion log line comes from `weatherbrief.scheduler` and looks like `Standalone forecast cycle: N models, M snapshots, ... (NNNNNNms)`.
- **Lighter cycles** (single model or fewer airports): a few minutes.

If a cycle just started, plan on **20+ min wait** unless you can confirm it's a lighter variant. To poll for completion, wait for either:
- `weatherbrief.scheduler:Standalone forecast cycle:` (fetch-only completion), OR
- `weatherbrief.scheduler:Verification cycle:` (full scoring completion).
Do NOT poll only for `standalone.*sleeping` — that pattern doesn't fire from this loop.

**If a cycle was interrupted** (user chose to deploy anyway, or the deploy already happened), offer to re-trigger it after the container is healthy:
```bash
ssh <user>@<server> "docker exec weatherbrief python -m weatherbrief.verify standalone"
```
This runs a single full cycle (fetch forecasts + observations + score) and exits. Safe to run alongside the loop — the loop's next scheduled cycle will proceed normally.

## Deploy steps

1. Only push if there are local commits ahead of `origin/main` AND the user has confirmed they should be part of this deploy:
   ```
   git log --oneline origin/main..HEAD   # if non-empty, ask before pushing
   git push origin main
   ```
   In the common case (local already in sync with `origin/main`), skip this step entirely.
2. SSH to the server and deploy:
   ```
   ssh <user>@<server> "cd flyfun-weather && git pull && docker compose up -d --build"
   ```
   Container logs go to journald (see `docker-compose.yml` logging config), so they survive the rebuild — query with `journalctl CONTAINER_NAME=weatherbrief --until="<time-of-rebuild>" --since="-1h"` if you need to inspect the prior container's last logs after the fact.
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

## Close "Addresses" issues after deploy

PRs that close an issue on **deploy** (as opposed to on merge) use `Addresses #N` / `Refs #N` / `Related to #N` in their body — these keywords do NOT trigger GitHub's auto-close on merge, so the issue is still open now. Once the deploy is live and healthy, post a comment and close those issues.

> Why a keyword whitelist: `Closes`/`Fixes`/`Resolves` already closed the issue at merge time. Plain `#N` mentions may be passing references ("see #50 for context") and should NOT trigger a close. Only the explicit `Addresses`/`Refs`/`References`/`Related to` set is treated as close-on-deploy intent.

### Steps

Runs **only after** the health check returns 200 — never close issues if the deploy didn't actually go live. Uses `SERVER_SHA` (pre-deploy) and `LOCAL_SHA` (just deployed) from pre-flight.

1. Collect the PR numbers whose commits are in this deploy:
   ```bash
   REPO="roznet/flyfun-weather"
   PRS=$(
     for sha in $(git log --format=%H "${SERVER_SHA}..${LOCAL_SHA}"); do
       gh api "repos/${REPO}/commits/${sha}/pulls" --jq '.[].number' 2>/dev/null
     done | sort -u
   )
   ```

2. For each PR, extract issues referenced with `Addresses`/`Refs`/`References`/`Related to`/`Closes`/`Fixes`/`Resolves` (case-insensitive — note we include the auto-close keywords too, so already-closed issues still get a "Deployed" comment for the reporter). For each:
   - If **OPEN**: comment + close.
   - If **CLOSED** (e.g. closed automatically at merge): comment only — don't reopen.
   ```bash
   for pr in $PRS; do
     body=$(gh pr view "$pr" --json body --jq .body)
     issues=$(printf '%s\n' "$body" \
       | grep -oiE '(addresses|refs?|references|related to|closes?|closed|fix(es|ed)?|resolves?|resolved)[[:space:]]+(issue[[:space:]]+)?#[0-9]+' \
       | grep -oE '[0-9]+' | sort -u)
     for issue in $issues; do
       state=$(gh issue view "$issue" --json state --jq .state 2>/dev/null)
       if [ "$state" = "OPEN" ]; then
         gh issue comment "$issue" --body "Deployed to https://weather.flyfun.aero — give it a try and let us know how it works."
         gh issue close "$issue"
         echo "  closed #${issue} (from PR #${pr})"
       elif [ "$state" = "CLOSED" ]; then
         # Only comment if we haven't already posted a "Deployed" notice (avoid dupes on re-deploys).
         already=$(gh issue view "$issue" --json comments --jq '.comments[] | select(.body | test("Deployed to https://weather.flyfun.aero")) | .id' | head -1)
         if [ -z "$already" ]; then
           gh issue comment "$issue" --body "Deployed to https://weather.flyfun.aero — give it a try and let us know how it works."
           echo "  notified #${issue} (already closed, from PR #${pr})"
         fi
       fi
     done
   done
   ```

> **Gotcha**: GitHub's auto-close keywords (`Closes`/`Fixes`/`Resolves`) fire on **either** the PR body or the merged commit message. With "Rebase and merge", original commit messages are preserved — so even if the PR body uses `Addresses #N`, a `Closes #N` in the commit message body will close the issue at merge time. When writing commit messages for close-on-deploy PRs, use `Addresses #N` (or just `#N`) in the commit body too.
>
> **Also**: GitHub's auto-close only matches `Fixes #N`, not `Fixes issue #N` (the word "issue" between keyword and `#` breaks it). PR #139 was an example — its body said "Fixes issue #133" so neither GitHub nor an earlier version of this regex caught it. The regex above includes an optional `issue` token to handle that variant on the deploy side; prefer dropping the word "issue" in PR bodies so GitHub's own auto-close fires at merge time.

3. Summarize what was closed at the end (or say "no Addresses-linked issues to close" if the list is empty).

### Skip conditions

- No PRs in the deploy range (e.g. data-only changes): skip silently.
- `gh auth status` fails: skip and tell the user so they can do it manually.
- Deploy failed or health check didn't return 200: **do not close** — the issues aren't actually live for users yet.

## If something goes wrong

- Check logs: `ssh <user>@<server> "docker logs --tail 50 weatherbrief"`
- The container runs on port 8020 internally
- Docker container runs as UID 2000 (`app` user) — data volume must be chowned to match
- `docker compose` (v2 syntax, NOT `docker-compose`)
