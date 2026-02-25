---
name: devserver
description: Start or restart the local dev server (backend + frontend) in a tmux session
disable-model-invocation: true
---

# Local dev server management

Manage the `weatherbrief` tmux session that runs the FastAPI backend and esbuild frontend watcher.

## Step 1 — Determine the project root

Figure out the correct project root (`PROJECT_ROOT`):
- Use the current working directory
- If we are in a git worktree (no `src/` dir, or `.git` is a file not a directory), the working directory IS the project root for that worktree

## Step 2 — Resolve the venv

- If `$PROJECT_ROOT/venv/` exists, use it
- Otherwise check `$PROJECT_ROOT/../main/venv/` (worktree case sharing main's venv)
- If neither exists, tell the user and stop

Store the resolved path as `VENV_PATH`.

## Step 3 — Check for .env file

- If `$PROJECT_ROOT/.env` exists, good — nothing to do
- If it does NOT exist, check if `$PROJECT_ROOT/../main/.env` exists (worktree case)
  - If found, copy it: `cp ../main/.env $PROJECT_ROOT/.env`
  - Tell the user it was copied
- If neither exists, warn the user that the `.env` file is missing and the server will likely fail to start

## Step 4 — Check for existing tmux session

Run: `tmux has-session -t weatherbrief 2>/dev/null`

If a session exists:
1. Check what directory it's running in: `tmux display-message -t weatherbrief -p '#{pane_current_path}'`
2. Compare that path to `$PROJECT_ROOT`
3. **If the directory matches** and the session looks healthy, tell the user:
   > Dev server already running at http://localhost:8000 — attach with `tmux attach -t weatherbrief`

   Then stop (no restart needed).
4. **If the directory does NOT match** (e.g., switched worktrees), kill the session:
   ```
   tmux kill-session -t weatherbrief
   ```
   Then continue to Step 5 to create a fresh one.

## Step 5 — Check for pending Alembic migrations

With the venv activated, run:
```
alembic current  # shows what the DB is at
alembic heads    # shows the latest migration in code
```

If they differ (i.e., there are unapplied migrations), **warn the user prominently** before starting the server:
> Pending Alembic migrations detected. Run `alembic upgrade head` before starting the server, or the app may fail.

Ask the user whether to run `alembic upgrade head` now or skip.

## Step 6 — Start the tmux session


Create a new tmux session with two panes:

```bash
# Create detached session — pane 0 runs the backend
tmux new-session -d -s weatherbrief -c "$PROJECT_ROOT"

# Pane 0: backend (FastAPI with reload)
tmux send-keys -t weatherbrief "source $VENV_PATH/bin/activate && uvicorn weatherbrief.api.app:app --reload --port 8000" Enter

# Create pane 1 (vertical split) for the frontend watcher
tmux split-window -h -t weatherbrief -c "$PROJECT_ROOT/web"
tmux send-keys -t weatherbrief "npm run dev" Enter
```

## Step 7 — Report to user

Tell the user:
- Backend running at **http://localhost:8000**
- Attach to tmux with: `tmux attach -t weatherbrief`
- Pane 0 = backend (uvicorn with --reload), Pane 1 = frontend (esbuild watch)

## Notes

- The `.env` file is loaded automatically by the app (python-dotenv), no need to source it manually
- `uvicorn --reload` watches for Python file changes automatically
- `npm run dev` in `web/` runs esbuild in watch mode for all TypeScript bundles
- Production uses port 8020 (docker), dev uses port 8000
