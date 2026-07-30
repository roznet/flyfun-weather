---
name: deploy
description: Deploy the weatherbrief app to production on weather.flyfun.aero
disable-model-invocation: true
---

# Deploy weatherbrief to production

Resolve `<user>@<server>`, `<project-dir>` and the `HOST_*` paths per
`designs/references/deployment-paths.md` — once, up front. Note that `AIRPORTS_DB` in the
server's `.env` is a *container* path while `scp` needs the host-side file under
`HOST_DATA_DIR`; the airport-DB step below resolves both separately for that reason.

Background for anything that deviates from the happy path is in
`designs/references/deploy-notes.md` (§D1–§D10). Read the section a step points you at; the
procedure below is complete on its own for a normal deploy.

## Pre-flight checks

> **What "to deploy" means** — a deploy ships `origin/main` to the server, so the comparison
> is **server's commit → `origin/main`**, never local working tree → `origin/main`. The two
> anchors used throughout:
> - `LOCAL_SHA` = `git rev-parse origin/main` (after fetch) — what we will deploy
> - `SERVER_SHA` = `ssh <user>@<server> "cd <project-dir> && git rev-parse HEAD"` — what runs now
>
> Use these everywhere a comparison is needed. Two traps this avoids are in §D2.

1. `git fetch origin` so `origin/main` is current
2. Ensure we are on `main` (`git branch --show-current`)
3. Capture `SERVER_SHA` and `LOCAL_SHA`
4. Show what will deploy: `git log --oneline ${SERVER_SHA}..${LOCAL_SHA}`
   - If empty → server is already up to date; say so and stop.
5. Show uncommitted local changes (`git status --short`) — **just list them**, do not block.
   Ask only if they look related to work that should be in this deploy.
6. **Run tests** (below)
7. **Check for pending Alembic migrations** (below)
8. **Check airport database freshness** (below)
9. **Check standalone verification cycle timing** (below)
10. **Check compute-node drift** (below) — read-only, never blocks
11. **STOP and get explicit confirmation** — the hard gate below. A turn boundary, not a step.

## Confirmation gate (HARD STOP — read every time)

This gate has already caught a real incident: a deploy narrated as complete while the
confirmation was *cancelled* and production never changed. §D1 has the full account. The rules
are literal:

1. **Confirmation is its own turn.** Post ALL pre-flight results (commit list, pytest, vitest,
   Playwright, alembic head count + pending migrations, disk, airport-DB freshness,
   standalone-cycle status, compute-node drift) and ask the user to confirm. Then **end the
   turn.** Do NOT call any deploy command (`git push`, `git pull`, `docker compose`,
   `alembic upgrade`, issue-closing `gh`) in the same message — not in parallel, not after,
   not "optimistically".
2. **Never bundle the gate with the gated action.** The confirmation request and the first
   `ssh ... docker compose` must be in *different turns*, separated by a real user reply.
3. **Only an actual, readable "yes" counts.** A cancelled question, an empty result, a
   `(no output)`, a tool error, or anything you "assume" is **NOT** confirmation.
4. **If tool output is unreliable, ABORT — never fabricate.** If results come back blank, lag,
   cancel in cascades, or look invented, STOP. Say plainly that you cannot verify state, and
   re-verify production with one clean command (server HEAD, `alembic current`, container
   uptime, HTTP health). Never narrate a step you did not observe a real result for.
5. **One deploy command at a time, never in a parallel batch**, so a sibling error can't
   cancel it and each result is read before the next.

## Run tests

Scope rules and rationale: §D8. Pytest discipline (run once, `timeout: 600000`, don't pipe
through `tail`) is in the root `CLAUDE.md`.

```bash
source venv/bin/activate && python -m pytest tests/ --ignore=tests/test_llm_digest.py -q
```

If any test fails, **stop the deploy** and report the failures.

Check what changed since the last deploy:

```bash
git diff ${SERVER_SHA}..${LOCAL_SHA} --name-only
```

**Vitest — if `web/` changed.** Hard gate; a failure is a real bug.

```bash
cd web && npm test
```

**Playwright — if `web/`, `src/weatherbrief/api/`, `src/weatherbrief/models/` or `configs/`
changed.** Warn on failure but don't block.

```bash
cd web && npx playwright test --reporter=line
```

## Check for Alembic migrations

```bash
git diff ${SERVER_SHA}..${LOCAL_SHA} -- alembic/versions/
```

Do **not** use `HEAD` — it may include local commits that won't reach the server. If migration
files changed, **warn prominently** that migrations must run after deploy.

Also verify a single head (§D9 explains the two-heads case):

```bash
source venv/bin/activate && alembic heads | grep -c '(head)'
```

Must print `1`.

## Disk usage check

```bash
ssh <user>@<server> "df -h /"
```

At **80 % or higher**, warn and offer `docker builder prune -a -f` (ask before running it,
re-check `df -h /` after).

## Check airport database freshness

The airport/navaid database (`nav.db`, built by the `euro_aip` submodule inside `rzflight`) is
copied to the server when updated. Both dev and prod point `AIRPORTS_DB` at `nav.db`. Compare
`model_metadata` timestamps to detect staleness.

**Local** (expand `${WORKING_DIR}` manually):

```bash
LOCAL_WORKING_DIR=$(grep '^WORKING_DIR=' .env | cut -d= -f2)
LOCAL_AIRPORTS_DB=$(grep '^AIRPORTS_DB=' .env | cut -d= -f2 | sed "s|\${WORKING_DIR}|${LOCAL_WORKING_DIR}|")
sqlite3 "${LOCAL_AIRPORTS_DB}" "SELECT key, updated_at FROM model_metadata WHERE key='statistics';"
```

**Remote** via docker exec (the container has `AIRPORTS_DB` pointing at the right file):

```bash
ssh <user>@<server> 'docker exec weatherbrief python3 -c "
import sqlite3, os
conn = sqlite3.connect(os.environ[\"AIRPORTS_DB\"])
for row in conn.execute(\"SELECT key, updated_at FROM model_metadata WHERE key=\\\"statistics\\\"\"):
    print(row[1])
conn.close()
"'
```

If local is newer, **offer to copy**. `AIRPORTS_DB` is the container path; the host-side file
lives under the server's `HOST_DATA_DIR`:

```bash
REMOTE_HOST_DIR=$(ssh <user>@<server> "grep '^HOST_DATA_DIR=' <project-dir>/.env | cut -d= -f2")
REMOTE_DB_NAME=$(ssh <user>@<server> "grep '^AIRPORTS_DB=' <project-dir>/.env | cut -d= -f2 | xargs basename")

scp "${LOCAL_AIRPORTS_DB}" <user>@<server>:"${REMOTE_HOST_DIR}/${REMOTE_DB_NAME}"
ssh <user>@<server> "sudo chown 2000:2000 ${REMOTE_HOST_DIR}/${REMOTE_DB_NAME}"   # container runs as UID 2000
ssh <user>@<server> "cd <project-dir> && docker compose restart"                  # reload the cached model
```

If timestamps match or remote is newer, report "Airport DB is up to date" and move on.

## Check standalone verification cycle timing

A deploy restarts the container, killing any in-progress cycle. Interpretation and durations:
§D7.

```bash
ssh <user>@<server> 'docker logs --since 10m weatherbrief 2>&1 | grep -iE "standalone|sleeping|Light cycle|Full cycle|phase"'
```

If a cycle appears to be running, warn: *"A standalone verification cycle appears to be
running. Deploying now will interrupt it. Wait a few minutes or proceed?"* If one was
interrupted, offer to re-trigger after the container is healthy:

```bash
ssh <user>@<server> "docker exec weatherbrief python -m weatherbrief.verify standalone"
```

## Check compute-node drift

Some deployments run the heavy standalone forecast cycle on **off-box compute nodes** that
emit a snapshot artifact for the droplet to ingest. Those nodes run this same repo and drift
out of step silently — one sat 64 commits behind for a week because nothing surfaced it.

Inventory is `deploy/compute-nodes.json` (gitignored; `deploy/compute-nodes.example.json`
documents every field).

```bash
test -f deploy/compute-nodes.json || echo "no compute nodes configured"   # absent = skip this section silently
```

For each node in `.nodes[]`, report its SHA next to `SERVER_SHA` / `LOCAL_SHA`:

```bash
ssh <node.ssh> "cd <node.repo> && git rev-parse --short HEAD && git branch --show-current"
```

Include a row per node in the pre-flight summary: name, SHA, branch, and commits behind
`LOCAL_SHA` (`git rev-list --count <nodeSHA>..<LOCAL_SHA>`).

**Unreachable nodes never block the deploy** — a `lan_only` node is simply not reachable from
another network, which is expected, not a fault. Report the actual ssh error rather than
guessing why, and warn explicitly in the confirmation summary. Full guidance and suggested
wording: §D4.

## Update compute nodes (after a successful deploy)

Only after production is deployed and healthy. Order matters: prod first, then nodes, so a
node is never running ahead of the box that ingests its output.

For each node:

1. **Don't pull while a cycle is running.** A cycle takes ~10–15 min and `git pull` would swap
   code under it. Skip the node and say so — never kill a cycle to deploy.
   ```bash
   ssh <node.ssh> "pgrep -fl 'weatherbrief.verify standalone' || echo idle"
   ```

2. **⚠️ Migrations gate — check BEFORE pulling.** A node pulled past a migration hard-fails
   every cycle and produces no artifact at all (§D3 — including why neither a pull nor
   `alembic upgrade head` fixes it, and the two cheap remedies).
   ```bash
   git diff --name-only <nodeSHA>..<LOCAL_SHA> -- alembic/versions/
   ```
   **If non-empty, do NOT pull until the schema change is applied by hand.**

3. **Fast-forward only**, so a node with local edits fails loudly instead of silently merging:
   ```bash
   ssh <node.ssh> "cd <node.repo> && git checkout <node.branch> && git pull --ff-only"
   ```

4. **Reinstall dependencies only if they changed:**
   ```bash
   git diff --name-only <nodeSHA>..<LOCAL_SHA> -- pyproject.toml
   # if non-empty:
   ssh <node.ssh> "cd <node.repo> && ./<node.venv>/bin/pip install -q -e '.[dev]'"
   ```

5. **Report per node**: SHA before → after, deps reinstalled yes/no, migrations needing
   attention, or the reason it was skipped.

**A node failure never fails the deploy** — production is already live. But never report a
deploy as fully complete while a configured node was skipped: say production is deployed *and*
name the nodes left behind.

## Deploy steps

> **Do not enter this section until the Confirmation gate passed with an actual, readable
> "yes" in a prior turn.** If you cannot point to the user's approving message, go back to the
> gate. Run each step as its own isolated tool call, never batched, and read its real result
> before moving on.

1. Only push if there are local commits ahead of `origin/main` AND the user confirmed they
   belong in this deploy. In the common case (local in sync), skip entirely.
   ```bash
   git log --oneline origin/main..HEAD   # if non-empty, ask before pushing
   git push origin main
   ```
2. SSH and deploy:
   ```bash
   ssh <user>@<server> "cd <project-dir> && git checkout main && git pull && docker compose up -d --build"
   ```
   The explicit `git checkout main` is a no-op normally, but it is what returns the server to
   `main` after a `prod-prev` rollback (§D5). Container logs go to journald, so they survive
   the rebuild — `journalctl CONTAINER_NAME=weatherbrief --until="<rebuild-time>" --since="-1h"`
   to inspect the prior container.
3. **If migrations were detected in pre-flight**, run them now:
   ```bash
   ssh <user>@<server> "docker exec weatherbrief alembic upgrade head"
   ```
4. Verify the health check:
   ```bash
   ssh <user>@<server> "docker inspect --format='{{.State.Health.Status}}' weatherbrief"
   ```
5. Confirm the endpoint responds:
   ```bash
   curl -s -o /dev/null -w '%{http_code}' https://weather.flyfun.aero/health
   ```
6. **Update compute nodes**, if `deploy/compute-nodes.json` exists — see above.

## Track the deployed version (prod / prod-prev branches)

Two long-lived branches point at what's deployed, so a bad deploy rolls back fast:

- `prod` → the commit now running in production (`LOCAL_SHA`)
- `prod-prev` → the commit running *before* this deploy (`SERVER_SHA`)

Why branches and not tags, and why no force is needed on a normal deploy: §D5.

Run this **only after the health check returns 200** — a failed deploy must not move `prod`:

```bash
if [ "${SERVER_SHA}" != "${LOCAL_SHA}" ]; then
  git branch -f prod-prev ${SERVER_SHA}   # previous prod — what we just replaced
fi
git branch -f prod ${LOCAL_SHA}           # new prod — what we just deployed

git push origin prod prod-prev            # fast-forward on a normal deploy
```

If the push is rejected as non-fast-forward — which only happens on a **rollback deploy** —
re-run with a lease: `git push --force-with-lease origin prod prod-prev`.

### Reverting to the previous version

```bash
ssh <user>@<server> "cd <project-dir> && git fetch origin && git checkout -B prod-prev origin/prod-prev && docker compose up -d --build"
```

**If migrations ran in the bad deploy**, decide whether they need an `alembic downgrade`
*before* rolling back — schema changes are not undone by checking out an older commit.

## Notify (and close deferred) issues after deploy

Mostly a **notification** step: ~93 % of issues here are self-filed working notes already
closed at merge, so this just posts "Deployed to …". The close half fires only for deferred
outside-reporter issues. Full rationale, the keyword-whitelist reasoning, and two GitHub
auto-close gotchas: §D6.

Runs **only after** the health check returns 200.

1. Collect the PR numbers in this deploy:
   ```bash
   REPO="roznet/flyfun-weather"
   PRS=$(
     for sha in $(git log --format=%H "${SERVER_SHA}..${LOCAL_SHA}"); do
       gh api "repos/${REPO}/commits/${sha}/pulls" --jq '.[].number' 2>/dev/null
     done | sort -u
   )
   ```

2. For each PR, extract referenced issues and act by state — OPEN: comment + close;
   CLOSED: comment only, don't reopen.
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
         already=$(gh issue view "$issue" --json comments --jq '.comments[] | select(.body | test("Deployed to https://weather.flyfun.aero")) | .id' | head -1)
         if [ -z "$already" ]; then
           gh issue comment "$issue" --body "Deployed to https://weather.flyfun.aero — give it a try and let us know how it works."
           echo "  notified #${issue} (already closed, from PR #${pr})"
         fi
       fi
     done
   done
   ```

3. Summarize which issues were **notified** (already closed at merge — the common case) and
   which were **closed** (deferred outside-reporter issues). Say "no linked issues in this
   deploy" if empty.

**Skip when:** no PRs in the range (data-only changes) — silently; `gh auth status` fails —
tell the user so they can do it manually; deploy failed or health check wasn't 200 — **do not
close**, the fix isn't live.

## Suggest a What's New entry (only with explicit confirmation)

Draft a **user-facing** entry from this deploy's commits and offer it for review. This step
**writes nothing unless the user explicitly says yes** — a confirmation gate exactly like the
deploy gate. Tone, scope and metadata rules: §D10.

```bash
git log --format='%s%n%b%n---' ${SERVER_SHA}..${LOCAL_SHA}
```

**Confirm (HARD STOP):** show the proposed title, category, highlight yes/no, and full
markdown body — then **end the turn.** Only a readable "yes" counts. The user may edit the
draft; apply their changes and re-show if the edits are substantial.

**Add (only after yes):**

```bash
ssh <user>@<server> "docker exec -i weatherbrief python -m weatherbrief.release add \
  --title 'TITLE HERE' --category feature --body-file -" <<'EOF'
Full markdown body here.
EOF
```

Add `--highlight` only if the user approved it. Confirm it landed:

```bash
ssh <user>@<server> "docker exec weatherbrief python -m weatherbrief.release list" | head
```

**Skip when:** nothing user-facing in the range (say so, no draft); or the user declines.

## If something goes wrong

- Logs: `ssh <user>@<server> "docker logs --tail 50 weatherbrief"`
- The container runs on port 8020 internally
- Container runs as UID 2000 (`app`) — the data volume must be chowned to match
- `docker compose` (v2 syntax, NOT `docker-compose`)
