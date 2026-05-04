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

Main's `.env` already pins data paths (`DATA_DIR`, `AIRPORTS_DB`, `ECMWF_GRIB_DIR`, `DATABASE_URL`, etc.) to absolute paths under `$MAIN_DIR/data`, so a verbatim copy is what we want — heavy data + the app DB are shared with main by virtue of the env vars.

**Do NOT create a `data/` directory or symlink in the worktree.** The whole point is that `DATA_DIR` (and friends) come from `.env`. If something in the codebase tries to read or write `./data` directly, we *want* it to fail loudly so the bug is visible. A stray `data/` dir would silently swallow that signal.

After copying, scan the result for **relative** path values as a sanity check:
```bash
grep -nE '^[A-Z_]+=\.\.?/|^[A-Z_]+=[a-z][a-z0-9_-]*/' "$WORKTREE_PATH/.env" || true
```
If any matches turn up, warn the user — those would resolve relative to the worktree CWD and probably aren't what they want. Don't auto-rewrite; the user owns `.env`.

**DB sharing:** because `.env` pins `DATABASE_URL` to main's DB, the worktree shares it. If this branch introduces alembic migrations, it WILL mutate main's DB. To isolate:
1. Copy main's DB file: `cp $MAIN_DIR/data/flyfun.db $WORKTREE_PATH/flyfun.db` (or wherever)
2. Edit `$WORKTREE_PATH/.env` and rewrite `DATABASE_URL` (and any other DB-pointing vars) to the new path.

The skill does NOT do this automatically — print the recipe in the summary if the user wants it.

## Step 6 — Print summary

Output a single block with:
- Worktree path
- Branch name (and base it was forked from, if newly created)
- Python: `python --version` from the new venv
- Editable install path verification result (from Step 3)
- `.env` copy status; flag any relative-path values found in Step 5
- One-line note: "DB and heavy data shared with main via `.env` — see skill notes if you need to fork."
- Next step: `cd <worktree-path> && /devserver`

Example:
```
Worktree ready at /Users/brice/Developer/public/flyfun-weather/issue-65
  Branch:    issue-65 (forked from main)
  Venv:      ./venv (Python 3.13.x)
  Editable:  weatherbrief → /.../issue-65/src/weatherbrief ✓
  npm:       installed
  .env:      copied from main (no relative paths found)

Next: cd /.../issue-65 && /devserver
DB note: shared with main via .env. If this branch adds migrations, see skill Step 5 notes to fork.
```

## Failure handling

- If any step fails, stop and surface the exact error. Don't try to clean up automatically — leave the partial state for the user to inspect, and tell them what to remove if they want to retry (`git worktree remove --force <path>` is the nuke).
- Do not run `alembic upgrade head` — `devserver` already does that check, and we don't want this skill touching the DB.
- Do not start `devserver` automatically. Print the next-step command and stop.
