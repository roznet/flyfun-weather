---
name: worktree-init
description: Create a new git worktree with its own venv, dependencies, .env, and shared data dir. Invoke with the branch name as the only argument.
disable-model-invocation: true
---

# Worktree initialization

Create a fresh git worktree set up so the user can immediately run `devserver` in it. Each worktree has its own venv (no more `../main/venv` sharing) but shares heavy data with `main/` so we don't re-download GRIB or rebuild nav.db.

## Inputs

- `<branch-name>` — required. The new worktree directory name AND the git branch name.
- `--from <base>` — optional. Branch to fork from (default `main`).

If the user invoked the skill without a branch name, ask for one and stop.

## Step 1 — Validate environment

1. Confirm CWD is the **main** worktree, not a sub-worktree. Check by running `git rev-parse --git-common-dir` and `git rev-parse --git-dir` — if they differ, we're in a sub-worktree. Refuse and tell the user to `cd` to the main checkout first.
2. Confirm working tree is clean enough for a worktree creation (uncommitted changes are fine — they stay in main; just inform the user). If the branch they're requesting already has a worktree (`git worktree list`), refuse and point at the existing one.

Resolve and remember:
- `MAIN_DIR` = `git rev-parse --show-toplevel` (absolute)
- `PARENT_DIR` = parent of `MAIN_DIR` (worktrees are siblings of `main/`)
- `WORKTREE_PATH` = `$PARENT_DIR/<branch-name>` (absolute)

## Step 2 — Create the git worktree

If branch `<branch-name>` already exists locally:
```bash
git worktree add "$WORKTREE_PATH" "<branch-name>"
```

Otherwise create it from the base branch:
```bash
git worktree add -b "<branch-name>" "$WORKTREE_PATH" "<base>"
```

(Default `<base>` is `main`.)

If the create fails, stop and surface the error verbatim.

## Step 3 — Create the per-worktree venv

```bash
cd "$WORKTREE_PATH"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

This installs the `weatherbrief` package in editable mode pointing at the worktree's `src/`, plus dev tools (`pytest`, `pytest-mock`, `responses` per `pyproject.toml`).

Sanity check after install:
```bash
python -c "import weatherbrief; print(weatherbrief.__file__)"
```
The path printed MUST start with `$WORKTREE_PATH`. If it points at `main/src` or anywhere else, stop and report — something is wrong with the editable install.

## Step 4 — Frontend deps

```bash
cd "$WORKTREE_PATH/web"
npm install
```

Run only `install`, not `npm run dev` — the user invokes that via `devserver`.

## Step 5 — Copy .env

```bash
cp "$MAIN_DIR/.env" "$WORKTREE_PATH/.env"
```

Main's `.env` already pins data paths (`DATA_DIR`, `AIRPORTS_DB`, `ECMWF_GRIB_DIR`, etc.) to absolute paths under `$MAIN_DIR/data`, so a verbatim copy is what we want — heavy data + the app DB are shared with main by virtue of the env vars.

**Do NOT create a `data/` directory or symlink in the worktree** (unless you are deliberately forking the DB — see below). The whole point is that `DATA_DIR` (and friends) come from `.env`. If something in the codebase tries to read or write `./data` directly, we *want* it to fail loudly so the bug is visible. A stray `data/` dir would silently swallow that signal.

After copying, scan the result for **relative** path values as a sanity check:
```bash
grep -nE '^[A-Z_]+=\.\.?/|^[A-Z_]+=[a-z][a-z0-9_-]*/' "$WORKTREE_PATH/.env" || true
```
If any matches turn up, warn the user — those would resolve relative to the worktree CWD and probably aren't what they want. Don't auto-rewrite; the user owns `.env`.

### DB sharing — and why `DATABASE_URL` is NOT the knob

The worktree shares main's app DB, because `.env` pins `DATA_DIR` at `$MAIN_DIR/data`
and the dev DB is `{DATA_DIR}/flyfun.db`. If this branch adds an alembic migration
it WILL mutate main's DB — and merely running `pytest` is enough to do it, since the
suite migrates on the configured DB.

**In dev the DB selector is `DATA_DIR`, not `DATABASE_URL`.** The two consumers
disagree, and that asymmetry is a live trap:

| Consumer | Resolution |
|---|---|
| `flyfun_common.db.get_engine()` | dev (`is_dev_mode()`): **always** `sqlite:///{DATA_DIR}/flyfun.db`. `DATABASE_URL` is read **only** in production. |
| `alembic/env.py::_get_url()` | `DATABASE_URL` if set, else `sqlite:///{DATA_DIR}/flyfun.db`. |

So setting **only** `DATABASE_URL` in a dev worktree moves alembic onto the copy and
leaves the app on the shared DB. The migration lands in a file the app never opens.
The symptom is nasty because both halves look healthy: `alembic current` reports head,
while the app 500s with `no such column: <table>.<column_the_migration_just_added>`.
Do not "fix" that by re-running the migration — check which file each side resolved
first.

**To actually fork the DB**, repoint `DATA_DIR` and symlink the heavy payload back, so
only the DB is private:

```bash
mkdir -p "$WORKTREE_PATH/data"
# Symlink everything in main's data dir EXCEPT the app DB + its WAL sidecars.
for f in "$MAIN_DIR"/data/*; do
  case "$(basename "$f")" in
    flyfun.db|flyfun.db-shm|flyfun.db-wal) continue ;;
  esac
  ln -sfn "$f" "$WORKTREE_PATH/data/$(basename "$f")"
done
# Copy the DB itself. Use sqlite3 .backup, NOT cp: cp alone can miss a
# non-checkpointed WAL, and deleting the -wal sidecar silently discards
# whatever was only committed there.
sqlite3 "$MAIN_DIR/data/flyfun.db" ".backup '$WORKTREE_PATH/data/flyfun.db'"
# Point DATA_DIR at the worktree copy. Leave DATABASE_URL unset in dev.
sed -i '' "s#^DATA_DIR=.*#DATA_DIR=$WORKTREE_PATH/data#" "$WORKTREE_PATH/.env"
```

This is the one case where a worktree-local `data/` is correct: the `./data` canary
above is traded away deliberately, in exchange for migrations that cannot reach main.

**Verify the fork took** — this is the check that catches the trap:
```bash
cd "$WORKTREE_PATH" && source venv/bin/activate
python -c "
from dotenv import load_dotenv; load_dotenv()
from flyfun_common.db import get_engine
print('app     :', get_engine().url)
import os; print('alembic :', os.environ.get('DATABASE_URL') or f\"sqlite:///{os.environ['DATA_DIR']}/flyfun.db\")
"
```
Both lines MUST print the same path, and it MUST be under `$WORKTREE_PATH`. If they
differ, the fork is half-applied — stop and fix before running anything.

The skill does NOT fork automatically — print the recipe in the summary if the user
wants it.

## Step 6 — Print summary

Output a single block with:
- Worktree path
- Branch name (and base it was forked from, if newly created)
- Python: `python --version` from the new venv
- Editable install path verification result (from Step 3)
- `.env` copy status; flag any relative-path values found in Step 5
- One-line note: "DB and heavy data shared with main via `.env` — see skill notes if you need to fork."
- **If the branch adds an alembic migration**, say so explicitly and offer the fork
  recipe. Sharing is fine for most branches; a migration is the case where it isn't,
  because it leaves main's DB stamped at a revision main's checkout can't resolve.
- Next step: `cd <worktree-path> && /devserver`

Example:
```
Worktree ready at <parent>/flyfun-weather/issue-65
  Branch:    issue-65 (forked from main)
  Venv:      ./venv (Python 3.13.x)
  Editable:  weatherbrief → /.../issue-65/src/weatherbrief ✓
  npm:       installed
  .env:      copied from main (no relative paths found)

Next: cd /.../issue-65 && /devserver
DB note: shared with main via .env (DATA_DIR). If this branch adds migrations, fork
         it — see Step 5. Setting DATABASE_URL alone does NOT fork it in dev.
```

## Failure handling

- If any step fails, stop and surface the exact error. Don't try to clean up automatically — leave the partial state for the user to inspect, and tell them what to remove if they want to retry (`git worktree remove --force <path>` is the nuke).
- Do not run `alembic upgrade head` — `devserver` already does that check, and we don't want this skill touching the DB.
- Do not start `devserver` automatically. Print the next-step command and stop.
