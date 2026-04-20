## Always start here

Design docs exist to give you quick context — architecture, key exports, non-obvious decisions — so you can find the right files faster than by grepping. Use them first.

For any task that requires looking at code (new features, bug fixes, UX questions, "how does X work", refactors), BEFORE reading or grepping files:

1. Call the `mcp__library-docs__list_libraries` MCP tool (or read `designs/INDEX.md` if the MCP server is unavailable). Do NOT try to invoke this via the `Skill` tool — it is an MCP tool, not a skill.
2. If a relevant library or module appears, call `mcp__library-docs__get_design_doc` (or read `designs/<module>.md`).
3. Only then explore code with Grep/Read.

For `[library]` entries: import and reuse. For `[project]` entries: follow the patterns.

Skip this only for trivial edits (typo fix, single-line change in a file already in context).

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

- Frontend uses esbuild. `npm run dev` runs watch mode and rebuilds `web/dist/*.js` on change — it is typically already running in tmux (see "Running the Web App" below), so don't run `npm run build` manually.
- Activate venv before running Python. If there's no venv in the current directory, check `../main/venv` and use that (we're in a git worktree and the main venv is shared).
- Local environment variables live in `.env`.

## Running the Web App

Start both servers in a tmux session:

```bash
tmux new-session -s weatherbrief
# Pane 0 — backend (FastAPI on :8000)
source venv/bin/activate
uvicorn weatherbrief.api.app:app --reload --port 8000

# Pane 1 (Ctrl-b %) — frontend (esbuild watch)
cd web && npm run dev
```

Open http://localhost:8000 in the browser. The backend serves static files from `web/`.
esbuild watches TypeScript sources and rebuilds `web/dist/*.js` on change.
Attach to the session with `tmux attach -t weatherbrief`.


