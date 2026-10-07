---
name: worktree-init
description: Create a new git worktree with its own venv, dependencies, .env, and shared data dir. Invoke with the branch name as the only argument.
disable-model-invocation: true
---

# Worktree initialization

Create a sibling worktree the user can immediately run `/devserver` in. Each worktree has its
own venv but shares heavy data and the dev DB with `main/` through the `.env` it copies.

## Run it

```bash
python3 scripts/ops/worktree_init.py <branch> [--from <base>] [--fork-db]
```

No branch name given → ask for one and stop. Add `--dry-run` to see what it would do.

The script does every step and checks each one (`scripts/ops/worktree_init.py` docstring):
- **Where:** `<parent of main>/<branch>`, from whichever checkout you run it.
- **Base:** a new branch forks from **`origin/main`** (fetched first), not local main —
  local main often holds unpushed commits that would ride into the PR. It says how many it
  left out. An existing local branch is checked out as is; an origin-only branch tracks it.
- **venv** with main's Python, editable install that must resolve to the worktree's `src/`
  (checked with `PYTHONPATH` cleared), `npm install` in `web/`, main's `.env` copied
  verbatim and scanned for relative paths (reported, never rewritten — the user owns `.env`).
- **Migrations:** flags a branch that adds `alembic/versions/` files vs `origin/main`.

Exit 0 = ready; 1 = a step failed, it stopped and printed how to remove the partial state
(don't clean up yourself); 2 = ready but something needs a look (a relative `.env` value, a
failed fetch, a migration on a shared DB). Relay the report, then the next step it prints:
`cd <path> && /devserver`.

Never run `alembic upgrade head` or start the server here.

## The shared DB — and when to fork it

The worktree's `.env` points `DATA_DIR` at main's data, so the dev DB is **shared**. If the
branch adds an alembic migration it WILL mutate main's DB — merely running `pytest` does it,
since the suite migrates the configured DB.

**In dev the DB selector is `DATA_DIR`, not `DATABASE_URL`**, and the two consumers disagree:

| Consumer | Resolution |
|---|---|
| `flyfun_common.db.get_engine()` | dev: **always** `sqlite:///{DATA_DIR}/flyfun.db`; `DATABASE_URL` only in production |
| `alembic/env.py::_get_url()` | `DATABASE_URL` if set, else `sqlite:///{DATA_DIR}/flyfun.db` |

Setting only `DATABASE_URL` moves alembic onto a copy while the app stays on the shared DB:
`alembic current` says head while the app 500s with `no such column`. Don't "fix" that by
re-running the migration — check which file each side opened.

**To fork:** `--fork-db` (or re-run on a fresh worktree). It symlinks main's `data/` entries
except the DB, copies `flyfun.db` with SQLite's backup API (a plain `cp` can miss
un-checkpointed WAL pages), repoints `DATA_DIR`, and then **proves** the app and alembic open
the same private file — a `problem` on `fork DB check` means a half-applied fork; stop. This is
the one case where a non-empty worktree `data/` is right: the `./data` canary (the repo
tracks only `data/.gitkeep`, so it is normally empty) is traded for migrations that can't
reach main. Recommend it whenever the report flags migrations.
