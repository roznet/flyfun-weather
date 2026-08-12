---
name: devserver
description: Start or restart the local dev server (backend + frontend) in a per-worktree tmux session. Use --https (or --simulator) to run the singleton TLS instance for iOS simulator testing.
disable-model-invocation: true
---

# Local dev server management

Manages tmux sessions running the FastAPI backend (`uvicorn --reload`) and esbuild frontend watcher.

Two modes:

| Mode | Flag | Session name | Port | Concurrency |
|------|------|--------------|------|-------------|
| HTTP (default) | (none) | `wb-<worktree-basename>` | 8000 for `main`, auto-pick 8001+ for others | One per worktree |
| HTTPS | `--https` or `--simulator` | `wb-https` (singleton) | 8443 | One only — kill+restart on invocation from any worktree |

## Step 1 — Parse args

- No flag → HTTP mode.
- `--https` or `--simulator` → HTTPS mode.

## Step 2 — Determine project root and basename

- `PROJECT_ROOT` = current working directory (assume the user invoked the skill from a worktree root).
- `BASENAME` = `basename "$PROJECT_ROOT"` (e.g. `main`, `issue-65`).

Sanity check it looks like a flyfun-weather worktree: `pyproject.toml` should exist with `name = "weatherbrief"`. If not, refuse and tell the user where they are.

## Step 3 — Resolve the venv

```bash
VENV_PATH="$PROJECT_ROOT/venv"
```

If `$VENV_PATH` does NOT exist, stop and tell the user:
- For `main`: `python3 -m venv venv && source venv/bin/activate && pip install -e ".[dev]"`
- For a worktree: `/worktree-init` should have created it. Re-run that, or set up manually.

**Do NOT fall back to `../main/venv`.** Per-worktree venvs are required (avoids the editable-install whack-a-mole).

### Verify editable install points at this worktree

```bash
source "$VENV_PATH/bin/activate"
python -c "import weatherbrief, sys; p = weatherbrief.__file__; sys.exit(0 if p.startswith('$PROJECT_ROOT') else 1)"
```

If this fails, the venv is wired to a different source tree. Stop and tell the user to re-run `pip install -e ".[dev]"` from `$PROJECT_ROOT`. Do not auto-fix — a wrong-pointed venv usually means something structural is off and a silent reinstall masks it.

## Step 4 — Check `.env`

If `$PROJECT_ROOT/.env` does not exist:
- If we're in a worktree (`PROJECT_ROOT` != `<parent>/main`), tell the user to run `/worktree-init` (which sets up `.env` with path rewrites + `data/` symlink). Don't auto-copy here — that path belongs to the init skill.
- If we're in `main`, warn that `.env` is missing and the server will likely fail.

## Step 5 — Pick session name and port

**HTTP mode:**
- `SESSION = "wb-$BASENAME"`
- If `$BASENAME` == `main`: `PORT = 8000`
- Else: scan ports 8001..8010, pick the first not in use:
  ```bash
  for p in 8001 8002 8003 8004 8005 8006 8007 8008 8009 8010; do
    if ! lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
  done
  ```
  If all are in use, stop and tell the user.
- Sticky-port behavior: if a tmux session named `wb-$BASENAME` already exists from a previous run, prefer to reuse its port (read it from the running uvicorn — see Step 6).

**HTTPS mode:**
- `SESSION = "wb-https"`
- `PORT = 8443`
- Verify the cert files are readable:
  ```bash
  test -r /usr/local/etc/letsencrypt/live/ro-z.me/privkey.pem && \
  test -r /usr/local/etc/letsencrypt/live/ro-z.me/fullchain.pem
  ```
  If not, stop and tell the user. Print the chmod they need to run (don't sudo). Typical fix:
  ```
  sudo chmod -R a+rX /usr/local/etc/letsencrypt/{live,archive}/ro-z.me
  ```

## Step 6 — Handle existing session

```bash
tmux has-session -t "$SESSION" 2>/dev/null
```

If it exists:
1. Read its CWD: `tmux display-message -t "$SESSION" -p '#{pane_current_path}'`
2. **Same CWD as `$PROJECT_ROOT`** → already running for this worktree. Tell the user the URL (`http://localhost:$PORT` or `https://localhost.ro-z.me:8443`) and stop. No restart.
3. **Different CWD** → only legitimate for `wb-https` (HTTP sessions are namespaced by basename so they can't collide). Kill and restart:
   ```bash
   tmux kill-session -t "$SESSION"
   ```
   Tell the user the HTTPS session moved from `<old-path>` to `$PROJECT_ROOT`.

## Step 7 — Pending alembic migrations check

With venv activated:
```bash
alembic current 2>/dev/null
alembic heads 2>/dev/null
```

If they differ, DO NOT reflexively tell the user to run `alembic upgrade head` —
first figure out *why* they differ. There are two very different causes and only
one is fixed by an upgrade.

### Know which DB you're looking at (avoid inspecting the wrong file)

In dev, `.env` usually does **not** set `DATABASE_URL`. Instead it sets
`DATA_DIR=${WORKING_DIR}/data`, and both the app (`get_engine()`) and
`alembic/env.py::_get_url()` resolve the URL as:
```
sqlite:///{DATA_DIR}/flyfun.db      # => <worktree>/data/flyfun.db
```
So the real dev DB is **`data/flyfun.db`** — NOT `alembic.ini`'s literal
`sqlalchemy.url` (a fallback alembic env.py overrides), and NOT the several stale
siblings in `data/` (`weatherbrief.db`, `weather.db`, `triage.db`). If you inspect
the schema directly (SQLAlchemy `inspect`, `sqlite3`), resolve the path the same
way — never hardcode a default DB name, or you'll diagnose the wrong file. Confirm
with: `python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.environ.get('DATABASE_URL') or f\"sqlite:///{os.environ.get('DATA_DIR','data')}/flyfun.db\")"`.

### Cause A — genuinely pending migration (stamp behind, schema behind)

The normal case. Warn prominently and offer to run it:
> Pending Alembic migrations detected. Run `alembic upgrade head` before starting, or the app may fail.

Ask whether to run `alembic upgrade head` now or skip.

### Cause B — schema AHEAD of the stamp (create_all built it first)

The app calls `Base.metadata.create_all()` on startup, which creates any **missing
tables** from the *current* models — so a DB the app has already run against can
hold a head-revision table/column while `alembic_version` still reads an older
revision. Here `alembic upgrade head` will **fail**, not help — e.g.
`duplicate column name: notify_override` or `table … already exists` — because it
tries to re-create objects that are already there.

If an upgrade fails that way (or before running it, if you suspect this), inspect
the real DB (path resolved as above) for head's objects:
- **All of head's objects already present and matching** → schema is effectively at
  head; reconcile the marker only, non-destructively:
  ```bash
  alembic stamp head
  ```
  (updates `alembic_version`, runs no DDL — safe on a large dev DB).
- **Only partially present** → do NOT force or drop anything. Stop and tell the
  user; a partial create_all/migration divergence needs a human decision.

Never run destructive DDL (drop column/table, batch-rebuild) on the dev DB to make
a migration apply — `data/flyfun.db` can be >1 GB of real data.

**Note:** the worktree's `.env` (copied from main by `/worktree-init`) points `DATA_DIR`/`DATABASE_URL` at main's DB by default, so any upgrade/stamp here acts on the shared DB. If that's not what you want, stop and fork the DB first (see `worktree-init` Step 5).

## Step 8 — Start the tmux session

**HTTP mode:**
```bash
tmux new-session -d -s "$SESSION" -c "$PROJECT_ROOT"
tmux send-keys -t "$SESSION" \
  "source $VENV_PATH/bin/activate && uvicorn weatherbrief.api.app:app --reload --port $PORT" Enter
tmux split-window -h -t "$SESSION" -c "$PROJECT_ROOT/web"
tmux send-keys -t "$SESSION" "npm run dev" Enter
```

**HTTPS mode:**
```bash
tmux new-session -d -s "$SESSION" -c "$PROJECT_ROOT"
tmux send-keys -t "$SESSION" \
  "source $VENV_PATH/bin/activate && uvicorn weatherbrief.api.app:app --reload \
   --host :: --port 8443 \
   --ssl-keyfile /usr/local/etc/letsencrypt/live/ro-z.me/privkey.pem \
   --ssl-certfile /usr/local/etc/letsencrypt/live/ro-z.me/fullchain.pem" Enter
tmux split-window -h -t "$SESSION" -c "$PROJECT_ROOT/web"
tmux send-keys -t "$SESSION" "npm run dev" Enter
```

## Step 9 — Cross-mode banner

After starting in HTTP mode, also check whether a `wb-https` session is running:
```bash
tmux has-session -t wb-https 2>/dev/null
```
If yes, get its CWD. If that CWD is **different** from `$PROJECT_ROOT`, print a banner:
> Note: `wb-https` (simulator mode) is currently anchored to `<other-path>`. The iOS simulator at `https://localhost.ro-z.me:8443` is serving that branch's code, not this one. Run `/devserver --https` here to repoint it.

(In HTTPS mode, no banner needed — we just told the user we (re)started it for this worktree.)

## Step 10 — Report to user

**HTTP:**
> Backend: http://localhost:$PORT
> Attach:  tmux attach -t $SESSION
> Pane 0 = uvicorn (--reload), Pane 1 = esbuild watch

**HTTPS:**
> Backend: https://localhost.ro-z.me:8443  (singleton; iOS simulator URL)
> Attach:  tmux attach -t wb-https
> Pane 0 = uvicorn (TLS, --reload), Pane 1 = esbuild watch

Include the cross-mode banner from Step 9 if applicable.

## Notes

- The `.env` is loaded automatically by the app (python-dotenv); no need to source it.
- `uvicorn --reload` watches Python file changes; `npm run dev` rebuilds `web/dist/*.js` on TypeScript change.
- Production: 8020 (docker, HTTP behind Caddy). Dev HTTP: 8000 (main) / 8001+ (worktrees). Dev HTTPS: 8443.
- The `localhost.ro-z.me` host resolves to **both** `127.0.0.1` and `::1` (already configured) so the wildcard cert validates without DNS shenanigans.
- **HTTPS mode must bind `--host ::`, not `0.0.0.0`.** Because the host resolves to both families and macOS/iOS prefer IPv6, the simulator dials `::1:8443` first. An IPv4-only listener refuses that, and URLSession does not reliably fall back — the app just reports the server as unreachable while `curl --ipv4` to the same URL works fine, which makes it look like a client bug. On macOS `net.inet6.ip6.v6only` is 0, so an `AF_INET6` socket bound to `::` accepts IPv4 too; exposure is the same as `0.0.0.0` (all interfaces). Symptom in the iOS log:
  ```
  nw_socket_handle_socket_event [C2.1.1.1:3] Socket SO_ERROR [61: Connection refused]
  nw_endpoint_flow_failed_with_error [C2.1.1.1 ::1.8443 ...]
  ```
  Quick check: `curl --ipv6 https://localhost.ro-z.me:8443/` must connect, not just `curl --ipv4`.
- OAuth callbacks are configured for `:8000` only; that's expected — local dev runs in single-user/admin mode and doesn't exercise OAuth.
