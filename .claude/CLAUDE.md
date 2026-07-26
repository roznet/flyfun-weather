## Always start here

Design docs exist to give you quick context — architecture, key exports, non-obvious decisions — so you can find the right files faster than by grepping. Use them first.

For any task that requires looking at code (new features, bug fixes, UX questions, "how does X work", refactors), BEFORE reading or grepping files:

1. Call the `mcp__library-docs__list_libraries` MCP tool (or read `designs/INDEX.md` if the MCP server is unavailable). Do NOT try to invoke this via the `Skill` tool — it is an MCP tool, not a skill.
2. If a relevant library or module appears, call `mcp__library-docs__get_design_doc` (or read `designs/<module>.md`).
3. Only then explore code with Grep/Read.

For `[library]` entries: import and reuse. For `[project]` entries: follow the patterns.

Skip this only for trivial edits (typo fix, single-line change in a file already in context).

## Working from a GitHub issue

Read the full comment thread, not just the issue body (`gh issue view <n> --comments`). Planning and design decisions frequently land in the comments and supersede the original description.

## Finding the code-review bot's review on a PR

The bot posts its findings as an ordinary **comment on the PR's conversation tab** — not as a GitHub "Review" (Approve / Request-changes) and not as inline diff comments. Look in `gh pr view <n> --comments` for a comment by `claude` whose **first line contains "Code Review"**.

Match on the text, not the markdown decoration: the comment shape is pinned in `.claude/commands/code-review.md` ("Output format") as `## Code Review`, but it has drifted to `**Code Review**` before, and a matcher that keys off `##` silently finds nothing when it does.

## Code Design Principles

- When adding logic around library calls, first consider whether it would be better to enhance the library itself rather than adding wrapper logic in the client code.
- Prefer pushing complexity into well-tested, reusable library code over ad-hoc client-side handling.
- Search for existing logic before duplicating — avoid code duplication.

## Changing Function Signatures

- When changing a function's signature (adding/removing/renaming parameters), grep for ALL callers across the codebase and update them — not just the obvious ones.
- Pay special attention to FastAPI dependency functions (`Depends(fn)`) that are also called directly. `Depends()` default values silently become real values when called outside DI, causing runtime errors with no static warning.
- Avoid functions used as both `Depends(fn)` and called directly. If unavoidable, split into a pure logic function (no `Depends` defaults) and a thin DI wrapper.

## Alembic Migrations

Dev uses SQLite, production uses MySQL. Migrations must work on both:

- **Always use `batch_alter_table`** for ALTER operations (add/drop columns, constraints). SQLite doesn't support ALTER natively — batch mode does a copy-and-move. MySQL handles it as a normal ALTER. The env.py has `render_as_batch=True` as a safety net, but prefer explicit `batch_alter_table` in migration code.
- **`op.create_table` / `op.drop_table`** work on both dialects without batch mode.
- **Named constraints** (e.g. `create_foreign_key("fk_flights_aircraft_id", ...)`) — use them so the downgrade can reference them by name.
- **Reference migration 004** (`004_flight_profiles.py`) as the canonical pattern for "create table + add FK column to existing table."
- **Column renames on MySQL** need `existing_type` parameter — see memory note.
- If dialect-specific logic is needed, use `op.get_bind().dialect.name == "mysql"` (see migrations 014, 015).

## Setup

- Frontend uses esbuild. `npm run dev` runs watch mode and rebuilds `web/dist/*.js` on change — usually already running in a tmux session managed by `/devserver`, so don't run `npm run build` manually.
- **Each worktree (including `main/`) has its own venv at `./venv`.** Do NOT fall back to `../main/venv` — that pattern caused editable-install bugs (the `weatherbrief` package would silently resolve to whichever directory last ran `pip install -e .`). If a worktree is missing its venv, run `/worktree-init` (for new worktrees) or `python3 -m venv venv && source venv/bin/activate && pip install -e ".[dev]"` (for `main`).
- Local environment variables live in `.env`. In worktrees, `.env` is copied verbatim from `main/.env` by `/worktree-init`; absolute paths in it (e.g. `DATA_DIR`, `DATABASE_URL`) keep heavy data and the DB shared with main. There is intentionally NO `data/` directory in a worktree — code that bypasses `DATA_DIR` and writes to `./data` should fail loudly.

## Worktree workflow

- `/worktree-init <branch-name>` — create a new worktree as a sibling of `main/` with its own venv, `npm install`, and `.env` copied from main. Does not run alembic, does not start the server.
- `/devserver` — start the dev server in a tmux session named `wb-<worktree-basename>`. Port: 8000 for `main`, auto-picked 8001+ for other worktrees, so multiple HTTP dev servers can run in parallel.
- `/devserver --https` (or `--simulator`) — start the singleton TLS instance at `https://localhost.ro-z.me:8443` (session `wb-https`) for iOS simulator testing. Only one HTTPS instance at a time across all worktrees; invoking from a different worktree kills+restarts it there.

## Running the Web App

Use `/devserver`. After it starts, open the URL it prints (e.g. `http://localhost:8000` for main, `http://localhost:8001` for a worktree) and attach to tmux with `tmux attach -t wb-<basename>`. Pane 0 is uvicorn (`--reload`), pane 1 is esbuild watch.

If you genuinely need to bypass the skill, the manual equivalent for `main` is:

```bash
source venv/bin/activate
uvicorn weatherbrief.api.app:app --reload --port 8000
# in another pane: cd web && npm run dev
```


