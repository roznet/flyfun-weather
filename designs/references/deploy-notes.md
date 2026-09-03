# Deploy reference

Background for the `deploy` skill (`.claude/skills/deploy/SKILL.md`). The skill holds the
procedure; this doc holds the *why* — the incidents that produced each rule, and the
reasoning you need only when something deviates from the happy path.

Read the section the skill points you at. The skill is complete on its own for a normal
deploy.

## Placeholders

`<user>@<server>`, `<project-dir>`, the `HOST_*` paths and the `<node.*>` compute-node fields
are resolved per **`designs/references/deployment-paths.md`**.

Relevant to the airport-DB step in particular: `AIRPORTS_DB` in the server's `.env` is a
**container** path, while `scp` needs the host-side location under `HOST_DATA_DIR`. That
distinction is why the skill resolves both separately rather than reusing one value.

## §D1 — Why the confirmation gate is a hard turn boundary

The gate exists because of a real incident: a deploy was narrated as done — migration
applied, health 200, issues closed — while the confirmation question had been *cancelled* and
production never changed. Nothing in the transcript was true.

Two failure modes combined. The gate and the gated action were issued in the same turn, so
there was no user reply to read; and when tool results came back empty or cancelled, they were
treated as success rather than as an abort signal.

Hence the literal rules in the skill: confirmation is its own turn, never bundled with the
first deploy command; only readable words of approval count; unreliable tool output means
abort and re-verify, never narrate a step you didn't observe a result for; and deploy commands
run one at a time so a sibling error can't cancel them silently.

It is always correct to under-claim and re-check. It is never acceptable to report a
fabricated deploy.

## §D2 — What "to deploy" means

A deploy ships whatever is on `origin/main` to the server. The right comparison is **server's
deployed commit → `origin/main`**, not local working tree → `origin/main`.

Two mistakes this has caused:

- Using `git log origin/main..HEAD` to show "commits to deploy". That shows what's *local but
  not pushed* — the opposite. If local is in sync with `origin/main`, it prints nothing even
  when the server is many commits behind.
- Stopping the deploy because the working tree is dirty. Uncommitted files are unrelated to
  what's on `origin/main`. List them so the user can decide; don't block.

The two anchors used everywhere in the skill:

- `LOCAL_SHA` = `git rev-parse origin/main` (after fetch) — what we will deploy
- `SERVER_SHA` = the server's `HEAD` — what is currently running

Alembic diffs, changed-path checks for Playwright, and the deployed-commit list all use these,
never `HEAD`.

## §D3 — Compute-node migrations hard-fail every cycle

**A node pulled past a migration produces no artifact at all.** This is not graceful
degradation.

It happened once: a node was pulled past the migration adding
`airport_forecast_snapshots.region`, and every subsequent cycle died with
`sqlite3.OperationalError: table airport_forecast_snapshots has no column named region`.

Why a pull cannot fix it: nodes run `ENVIRONMENT=development`, so their SQLite is built by
`create_all`, which creates missing *tables* but **never ALTERs an existing one**. New code
arrives writing a column the table doesn't have. And `alembic upgrade head` isn't the answer
either — these DBs have no `alembic_version` row, so alembic would try to replay every
migration against tables that already exist.

The fix is a human decision, and both options are cheap because **a node's DB is disposable**
(recomputed every cycle, pruned at 10 days; the artifacts are the real output):

- hand-write the equivalent `ALTER TABLE` — metadata-only for an additive column with a
  default, measured at 0.012 s on a 352 MB table; or
- delete the node's scratch DB and let the next cycle rebuild it with the current schema.

Report the migration, apply the fix, *then* pull. Never report a node as updated when a
migration between the two SHAs went unapplied.

## §D4 — Unreachable nodes are expected, not faults

Reachability depends on where the deploy is running from. A node flagged `lan_only` (an mDNS
`.local` name, a private IP) is reachable only from its own network, so a deploy run from a
cloud agent, a different machine, or another network **will never reach it**. That is normal
and not something to retry around.

Whatever the cause — off-network, asleep, powered down, DNS failure — treat it identically:

- **Never block the deploy.** Production ships regardless; a node is downstream of prod.
- **Never guess.** From outside the network you cannot distinguish "node is switched off" from
  "cannot resolve the name from here". Report the actual ssh error, not an assumption.
- **Warn explicitly in the confirmation post the user actually reads**, and again when closing
  out the deploy, so an un-updated node is never mistaken for an updated one.

A stale node is safe: the importer intersects the artifact's columns with the table's, so a
node running older code still produces an ingestable artifact. Staleness is a thing to fix at
leisure, never a deploy blocker.

Suggested wording:

> ⚠️ **Compute node `<node.name>` unreachable** (`<actual ssh error>`). Node is `lan_only`, so
> this is expected when deploying from outside its network. Its version could not be checked
> and it **will not be updated by this deploy** — it keeps running whatever code it has, which
> remains safe to ingest. Update it manually next time you are on that network:
> `ssh <node.ssh> "cd <node.repo> && git pull --ff-only"`

Never report a deploy as fully complete while a configured node was skipped. Say production is
deployed *and* name the nodes left behind.

## §D5 — Why `prod` / `prod-prev` are branches, not tags

Branches are git's natural "moving pointer" (the environment-branch / GitLab-Flow pattern);
tags are conventionally immutable markers.

On a normal deploy both pointers only ever advance *forward* along `main`, so the push is a
plain fast-forward — **no force needed**. Force is only required for the unusual case of
deploying an *older* commit (a rollback deploy, which moves a pointer backward); that's when
`--force-with-lease` applies.

`git branch -f` resets the **local** branch ref — these branches aren't checked out, the
deploy machine is on `main` — so it is not a force-push.

Rollback leaves the server **on the `prod-prev` branch**, which is fine for an emergency. The
normal deploy step does `git checkout main && git pull`, so the next deploy automatically
returns the server to `main`; the explicit `git checkout main` exists for exactly this reason.
Without it, `git pull` on the rolled-back branch would re-deploy the bad commit.

**If migrations ran in the bad deploy**, decide whether they need an `alembic downgrade`
*before* rolling back — schema changes are not undone by checking out an older commit.

## §D6 — Issue notification vs closing, and the keyword whitelist

The post-deploy issue step is **primarily a notification step**. Most issues in this repo are
self-filed working notes (~93 % — the tracker records whether the *work* is done, not whether
it's live), so they use `Closes #N` and are **already closed at merge**. For those, the step
just posts "Deployed to …". That comment *is* the notification: GitHub notifies subscribers of
comments on closed issues, so an issue does not need to stay open to tell someone it shipped.

The close half only fires for the exception: issues filed by an *outside reporter*, where a PR
deliberately used `Addresses #N` / `Refs #N` / `Related to #N` to defer the close until the fix
is reachable. Those keywords don't trigger GitHub's auto-close. Rare — about a dozen issues in
this project's history.

**Why a keyword whitelist:** plain `#N` mentions may be passing references ("see #50 for
context") and must not trigger anything. Only an explicit keyword counts. The skill's regex
matches the auto-close keywords too — deliberately, so already-closed issues still get the
"Deployed" comment.

**Do not "fix" a missed close by loosening the regex to match bare `#N`.** If a shipped issue
is still open, the PR simply omitted the keyword — close it by hand and fix the PR habit (see
`.github/PULL_REQUEST_TEMPLATE.md`). A batch of PRs did exactly this once, leaving four issues
open long after they shipped.

Two GitHub gotchas:

- Auto-close keywords fire on **either** the PR body or the merged commit message. With
  "Rebase and merge", original commit messages are preserved — so even if the PR body says
  `Addresses #N`, a `Closes #N` in the commit body will close the issue at merge. This only
  matters for the rare deferred-close PRs: for those, use `Addresses #N` in the commit body
  too.
- Auto-close matches `Fixes #N` but **not** `Fixes issue #N` — the word "issue" between the
  keyword and `#` breaks it. One PR was missed this way. The skill's regex includes an optional
  `issue` token to catch the variant on the deploy side; prefer dropping the word in PR bodies
  so GitHub's own auto-close fires at merge time.

## §D7 — Standalone cycle timing around a deploy

A deploy restarts the container, killing any in-progress cycle.

Interpreting the log check:

- `sleeping Xs until next sample hour` — the loop is idle. Parse the sleep duration to
  estimate when the next cycle fires; more than 5 minutes away is safe to deploy.
- `Light cycle` / `Full cycle` lines with no subsequent `sleeping` or `Recorded` line — a cycle
  is likely **in progress**. Warn and let the user choose.
- No standalone lines in the last 10 minutes — between cycles, safe.

Durations: a fetch-only cycle (3 models × ~619 airports × 7 chunks) takes ~20–25 min; lighter
cycles a few minutes. To poll for completion, wait for either
`weatherbrief.scheduler:Standalone forecast cycle:` or
`weatherbrief.scheduler:Verification cycle:`. Do **not** poll only for `standalone.*sleeping` —
that pattern doesn't fire from this loop.

If a cycle was interrupted, re-triggering it is safe alongside the loop; the loop's next
scheduled cycle proceeds normally.

## §D8 — Test scope rules

- **Python tests always run.** Full suite ~85 s. Slow GRIB decode tests are skipped by default
  via `addopts` in `pyproject.toml`.
- **Vitest only when `web/` changed.** Pure TypeScript unit tests (~300 ms), no API access.
  Hard gate — deterministic, so a failure is a real bug. Backend changes that affect the
  frontend land as a `web/ts/` edit, which the `web/` trigger already catches.
- **Playwright when frontend or API changed** — `web/`, `src/weatherbrief/api/`,
  `src/weatherbrief/models/`, or `configs/`. **Warn but don't block** on failure: these break
  on stale selectors as often as on real bugs. Let the user decide.

Pytest discipline (run once, generous timeout, don't pipe through `tail`) is a project-wide
rule, not deploy-specific — see the root `CLAUDE.md`.

## §D9 — Alembic multiple heads

A long-lived branch may add migration `N` while main independently grows past `N`, leaving two
heads descending from the same parent. `alembic upgrade head` then fails with
`Multiple head revisions`.

CI (`.github/workflows/alembic-check.yml`) enforces a single head on PRs, but the deploy checks
it locally as belt-and-braces against what's about to ship. If there are 2+ heads, the
migration with the lower number needs renumbering to descend from the current head — update
`revision`, `down_revision`, and the filename.

## §D10 — What's New entry scope

The release stream is the `system_messages` table, managed by the `weatherbrief.release` CLI.
The CLI writes straight to the DB, so it must run against the **production** DB inside the
container.

Distil the deploy's commits into **one grouped, user-facing entry** — not one per PR/commit:

- **Honest about the real driver** (e.g. "we cut compute cost so we can do more", not
  user-flattery). Exclude internal details — refactors, CI, infra, test-only changes, file or
  PR numbers.
- **Only mention features actually live** for users in this deploy. If nothing is user-facing
  (infra/data-only), say so and skip — do not invent an entry.
- Plain language a pilot understands, no commit-speak.

Metadata: `--category` is `feature` (new capability), `change` (behaviour change), or `fix`.
Most grouped deploy entries are `feature`. `--highlight` lights the notification dot and
defaults **off** — reserve it for something you'd genuinely want every user to notice.

Pass the body on stdin (`--body-file -`) so markdown survives SSH without shell-escaping;
`docker exec -i` and `ssh` both forward stdin.

## §D11 — Rehearsing migrations on real MySQL

**The incident.** Migration 094 added a TEXT column as `NOT NULL` with a `server_default` of
`[]`. SQLite accepts that; MySQL rejects it outright with error 1101, *"BLOB, TEXT, GEOMETRY or
JSON column can't have a default value"*. The deploy ran the rebuild first and the migration
second, so for a few minutes production was serving **new code against the old schema** — every
`briefing_packs` query returned `Unknown column`, which took out the briefing pages. `/health`
stayed 200 throughout, because it touches no table. The schema was applied by hand with the
portable three-step, alembic was stamped to 094, and service came back.

Two things made it possible, and both are structural rather than careless:

1. **The suite cannot see it.** Dev is SQLite. Every test passed on DDL that could never run in
   production. No amount of local testing would have caught it.
2. **`/health` is not a smoke test.** It answers 200 while the app's main table is unreadable.
   After a migration, verify by *querying the table the migration touched*, not by curling
   health.

**The check.** `.claude/skills/deploy/mysql_migration_check.py` closes gap 1. It creates a
throwaway MySQL database, upgrades it to the revision production currently reports, seeds a row
into each table the pending migrations name in a `batch_alter_table`, then runs the pending
migrations. Seeding matters: it also catches the neighbouring trap of adding a `NOT NULL` column
with no default to a table that already has rows, which is fine on an empty table and fails on a
populated one.

Run it whenever `git diff ${SERVER_SHA}..${LOCAL_SHA} -- alembic/versions/` is non-empty.

**Credential gotchas** (all cost real time once):

- `~/.my.cnf` values are frequently **quote-wrapped**. `configparser` keeps the quotes, the
  `mysql` CLI strips them — so a naive read sends a password two characters too long and gets
  `1045 Access denied` while the CLI works fine beside it. Strip quotes from every value.
- Honour the `host` in the file (`127.0.0.1`) rather than assuming a unix socket. Connecting
  over the socket with TCP credentials gives the same misleading 1045.
- Never write the resolved URL into the repo, and delete any temp file holding it. The helper
  reads the credentials in-process and passes them to alembic through `DATABASE_URL` in the
  child's environment only.

**A `SKIP` is not a pass.** No local MySQL means the check did not run. Report that explicitly
in the pre-flight summary rather than letting an absent check read as a green one.

**Ordering.** The rebuild lands before `alembic upgrade head`, so any migration failure is an
outage window, not a safe abort. That is the reason this check is a pre-flight gate: by the time
the migration fails on the server, the new code is already serving.
