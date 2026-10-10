---
name: deploy
description: Deploy the weatherbrief app to production on weather.flyfun.aero
disable-model-invocation: true
---

# Deploy weatherbrief to production

`scripts/ops/deploy.py` does the mechanics; this skill keeps the gates and the judgement.
Background for anything off the happy path: `designs/references/deploy-notes.md` (§D1–§D11);
hosts and paths come from `deploy/hosts.json` via `scripts/ops/hosts.py`. Every command reports
`ok / problem / unknown / warn / skip` with evidence; exit 0 ok (warnings allowed), 1 a problem,
2 could not tell. Each Bash call is a fresh shell: carry `SERVER_SHA` / `LOCAL_SHA` / `SINCE`
as literal values into later commands.

## Pre-flight

```bash
python3 scripts/ops/deploy.py preflight
```

One call, read-only. It fetches origin and compares **the server's commit → `origin/main`**
(never the local working tree, §D2), and reports:
- the commits and changed areas, and which **test suites** this change needs (it does not
  run them — next section);
- the checkout: **behind `origin/main` is a problem** (tests and the rehearsal would check
  old code — pull first); unpushed local commits are a warning (they don't deploy);
- alembic: single head at `origin/main` (§D9), the server's revision, pending migrations,
  and the **MySQL rehearsal** of them from the server's revision (§D11);
- disk, nav.db local vs server, the **standalone cycle** state (§D7), each node's drift.

Its last lines are `SERVER_SHA=` and `LOCAL_SHA=`: use those two everywhere below.

How to read it:
- `range … nothing to deploy` → say so and stop.
- `range` problem (server not an ancestor of `origin/main`) → stop and ask: hotfix or rollback.
- **MySQL rehearsal** `problem` → **stop the deploy**: that is the failure production would hit;
  fix the migration. `unknown` (no local MySQL / `~/.my.cnf`) → not a failure, but say the gap
  in the summary and read the migration by eye.
- `disk /` warn (≥80 %) → offer `docker builder prune -a -f`, ask before running it.
- `nav.db` warn (local newer) → offer the copy (below).
- `standalone cycle` warn (running, or due within 5 min) → *"A standalone verification cycle
  appears to be running. Deploying now will interrupt it. Wait or proceed?"* `unknown` → say
  the log couldn't be read; ask.
- A node warn never blocks (unreachable `lan_only` = expected away from home; "WRONG MACHINE"
  = never update through it). Name it in the summary (§D4).

## Run tests

Run each suite preflight listed, once, `timeout: 600000`, no `| tail` (root `CLAUDE.md`):
pytest always (a failure **stops the deploy**), vitest when `web/` changed (hard gate),
Playwright when web/api/models/configs changed (warn only). Scope rules: §D8.

## Confirmation gate (HARD STOP — read every time)

This gate has already caught a real incident: a deploy narrated as complete while the
confirmation was *cancelled* and production never changed. §D1 has the full account. The rules
are literal:

1. **Confirmation is its own turn.** Post the preflight report (commits, migrations +
   rehearsal, disk, nav.db, standalone cycle, node drift) plus the test results, and ask the
   user to confirm. Then **end the
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

## Copy nav.db (only when preflight says local is newer, and the user agreed)

`AIRPORTS_DB` in the server `.env` is a container path; `hosts.py server` gives the host file
`<HOST_AIRPORTS_DB>` (translated through the container's mounts) and `hosts.py local` the
`<LOCAL_AIRPORTS_DB>`:

```bash
scp "<LOCAL_AIRPORTS_DB>" <SERVER_SSH>:"<HOST_AIRPORTS_DB>"
ssh <SERVER_SSH> "sudo chown 2000:2000 <HOST_AIRPORTS_DB>"   # hand this one to the user (sudo)
ssh <SERVER_SSH> "cd <SERVER_PROJECT_DIR> && docker compose restart"
```

## Deploy steps

> **Do not enter this section until the Confirmation gate passed with an actual, readable
> "yes" in a prior turn.** If you cannot point to the user's approving message, go back to the
> gate. Run each step as its own isolated tool call, never batched, and read its real result
> before moving on.

1. Only push if there are local commits ahead of `origin/main` AND the user confirmed they
   belong in this deploy (preflight's `unpushed` line). Normally skip.
2. Note the time, then rebuild — `SINCE` is what proves the image is new in step 4:
   ```bash
   date -u +%Y-%m-%dT%H:%M:%SZ        # SINCE
   ssh <SERVER_SSH> "cd <SERVER_PROJECT_DIR> && git checkout main && git pull && docker compose up -d --build"
   ```
   `git checkout main` is what returns the server to `main` after a `prod-prev` rollback (§D5).
   Logs survive the rebuild in journald (`journalctl CONTAINER_NAME=weatherbrief`).
3. **If preflight listed migrations**, run them now:
   ```bash
   ssh <SERVER_SSH> "docker exec weatherbrief alembic upgrade head"
   ```
   The rebuild has landed, so a failure here is an **outage**, not a safe abort: new code
   against the old schema. Treat it as live (§D11): apply the schema by hand or roll back.
4. **Verify the new code is live** — not just a healthy container on the right SHA, which
   is exactly what lied on 2026-08-26:
   ```bash
   python3 scripts/ops/deploy.py verify $SERVER_SHA $LOCAL_SHA --since $SINCE
   ```
   Server HEAD, container health, the running image is the current build *and* newer than
   `SINCE`, sha256 of the changed files inside the container vs `origin/main`, alembic at
   `origin/main`'s head, an ORM read of `briefing_packs` (`/health` is 200 even when that
   table is unreadable), public `/health`. Any problem → the deploy is not done: diagnose
   (`docker compose build weatherbrief && docker compose up -d` fixes a build the Docker
   upgrade race killed), never move on.
5. **Compute nodes** (prod first, then nodes, so a node never runs ahead of the box that
   ingests its output):
   ```bash
   python3 scripts/ops/deploy.py nodes $LOCAL_SHA              # dry run: what it would do
   python3 scripts/ops/deploy.py nodes $LOCAL_SHA --execute
   ```
   Per node: skipped if unreachable, on the wrong machine, a cycle is running (never pull
   under one), or its DB isn't alembic-stamped (§D3); otherwise checkout, `pull --ff-only`
   and `alembic upgrade head` **in one command** (no cycle can start between them, §D3), pip
   only if `pyproject.toml` changed, then it **checks the node reached `origin/main`** and its
   alembic head. A `daemon` warn (e.g. the mini's `aero.flyfun.observed-cells`) keeps running
   the old code: hand the user the printed `sudo launchctl kickstart -k …`. A node failure
   never fails the deploy — but never report it complete while a node was skipped: say
   production is deployed *and* name the nodes left behind.

## Track the deployed version (prod / prod-prev branches)

`prod` → what now runs (`LOCAL_SHA`), `prod-prev` → what ran before (`SERVER_SHA`), so a bad
deploy rolls back fast. Why branches and not tags: §D5.

```bash
python3 scripts/ops/deploy.py mark-prod $SERVER_SHA $LOCAL_SHA --since $SINCE             # dry run
python3 scripts/ops/deploy.py mark-prod $SERVER_SHA $LOCAL_SHA --since $SINCE --execute
```

It re-runs the whole `verify` first and moves nothing unless it passes — a failed deploy
must not move `prod`. It pushes explicit refspecs (no local branch involved). A
non-fast-forward means a **rollback deploy**: rerun with `--rollback` (`--force-with-lease`).

### Reverting to the previous version

```bash
ssh <SERVER_SSH> "cd <SERVER_PROJECT_DIR> && git fetch origin && git checkout -B prod-prev origin/prod-prev && docker compose up -d --build"
```

**If migrations ran in the bad deploy**, decide whether they need an `alembic downgrade`
*before* rolling back — schema changes are not undone by checking out an older commit.

## Notify (and close deferred) issues after deploy

Mostly a **notification** step: ~93 % of issues here are self-filed working notes already
closed at merge, so this just posts "Deployed to …". The close half fires only for deferred
outside-reporter issues. Full rationale, the keyword-whitelist reasoning, and two GitHub
auto-close gotchas: §D6.

Runs **only after** the health check returns 200.

```bash
python3 scripts/ops/notify_deploy_issues.py $SERVER_SHA $LOCAL_SHA
```

It walks the deployed commits to their PRs, reads each PR body for an explicit keyword
reference, and acts by issue state — OPEN: comment + close; CLOSED: comment once, never
reopen. `--dry-run` prints the same report without writing. Run it with the sandbox disabled
(otherwise `gh` returns empty with exit 0). Relay its report: which issues were **notified**
(already closed at merge — the common case) and which were **closed** (deferred
outside-reporter issues), or "no linked issues in this deploy".

It is a script rather than a shell loop because the agent's shell is zsh, which doesn't
word-split an unquoted `$PRS` — the old loop passed every PR number as one argument and failed.

**Skip when:** the deploy failed or the health check wasn't 200 — **do not close**, the fix
isn't live; `gh auth status` fails — tell the user so they can do it manually. A range with no
PRs (data-only changes) needs no skip: the script says so in one line.

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
ssh <SERVER_SSH> "docker exec -i weatherbrief python -m weatherbrief.release add \
  --title 'TITLE HERE' --category feature --body-file -" <<'EOF'
Full markdown body here.
EOF
```

Add `--highlight` only if the user approved it. Confirm it landed:

```bash
ssh <SERVER_SSH> "docker exec weatherbrief python -m weatherbrief.release list" | head
```

**Skip when:** nothing user-facing in the range (say so, no draft); or the user declines.

## If something goes wrong

- Logs: `ssh <SERVER_SSH> "docker logs --tail 50 weatherbrief"`
- The container runs on port 8020 internally
- Container runs as UID 2000 (`app`) — the data volume must be chowned to match
- `docker compose` (v2 syntax, NOT `docker-compose`)
