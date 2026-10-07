---
name: devserver
description: Start or restart the local dev server (backend + frontend) in a per-worktree tmux session. Use --https (or --simulator) to run the singleton TLS instance for iOS simulator testing.
disable-model-invocation: true
---

# Local dev server

`scripts/ops/devserver.py` runs uvicorn `--reload` (pane 0) and the esbuild watch (pane 1) in
tmux, from the checkout root:

| Mode | Command | Session | URL |
|---|---|---|---|
| HTTP | `python3 scripts/ops/devserver.py` | `wb-<basename>` | `http://localhost:8000` (main), 8001–8010 (worktrees) |
| HTTPS (iOS simulator) | `python3 scripts/ops/devserver.py --https` | `wb-https`, **singleton** | `https://localhost.ro-z.me:8443` |

Also `--status` (every `wb-*` session, its checkout, URL, up or not), `--stop [--https]`,
and `--dry-run`. `--https` from another checkout **moves** the singleton here (it says from
where) — tell the user, since the simulator then serves this branch.

Before starting it checks the checkout, that `./venv` exists and its editable install
resolves here (never `../main/venv`; a mis-wired venv is reported, not auto-fixed), `.env`,
the TLS cert for `--https`, and **alembic current == heads**. After starting it waits for
`/health`. An already-running session for this checkout is reported, not restarted. Each
session records its checkout and port in tmux (`WB_ROOT`/`WB_PORT`) — the active pane's cwd
is the esbuild pane in `web/`, which is why "same checkout?" can't be read from it.

Exit 0 → relay the URL and `tmux attach -t <session>`. Exit 1 → read the `problem` line:
- **missing venv / `.env`** in a worktree → `/worktree-init` creates them; in `main/`, the
  `.env` is backed up in `~/scripts/config/roznet/flyfun-weather`.
- **TLS cert unreadable** → hand the user the printed `sudo chmod` (never run sudo).
- **alembic** → diagnose below, then rerun.

## alembic current ≠ heads: diagnose before upgrading

Two causes; only one is fixed by an upgrade. Inspect the DB the app really opens —
`python3 scripts/ops/hosts.py local` prints it (`dev DB`, i.e. `{DATA_DIR}/flyfun.db`, not
`alembic.ini`'s URL nor the stale `weatherbrief.db` / `weather.db` siblings).

**A — migration genuinely pending** (stamp and schema both behind). Normal case: tell the
user and ask whether to run `venv/bin/alembic upgrade head` now.

**B — schema ahead of the stamp.** The app's startup `create_all()` creates missing tables
from current models, so a DB can hold head's objects while `alembic_version` is older; then
`upgrade head` *fails* (`duplicate column name`, `table … already exists`). Check the DB for
head's objects:
- all present and matching → `venv/bin/alembic stamp head` (marker only, no DDL);
- only partly present → stop and tell the user; that needs a human decision.

Never run destructive DDL on the dev DB to make a migration apply — it can be >1 GB of real
data. A worktree shares main's DB unless it was forked (`worktree-init --fork-db`), so an
upgrade or stamp there acts on main's DB too.

## Notes

- `.env` is loaded by the app (python-dotenv). `--reload` catches `.py` changes only — restart
  (`--stop`, then start) after a `.env` change.
- Ports: production 8020 (docker behind Caddy); dev 8000 / 8001+; HTTPS 8443. OAuth callbacks
  are configured for `:8000` only — dev runs single-user admin mode without OAuth.
- **HTTPS binds `--host ::1` (IPv6 loopback), never `::` or `0.0.0.0`.**
  `localhost.ro-z.me` resolves to both `127.0.0.1` and `::1`, and macOS/iOS prefer IPv6, so
  the simulator dials `::1:8443` first; an IPv4-only listener refuses it and URLSession does
  not reliably fall back — the app reports the server unreachable while `curl --ipv4` works.
  `::1` serves the simulator and keeps the server off the network: dev mode has no auth
  (every request is the admin dev user) and the env holds real API keys, so `::` (which on
  macOS also accepts IPv4 on every interface) would hand an admin server to anyone on the
  same Wi-Fi. Quick check: `curl --ipv6 https://localhost.ro-z.me:8443/` must connect.
