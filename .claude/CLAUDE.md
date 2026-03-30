## Code Design Principles

- When adding logic around library calls, first consider whether it would be better to enhance the library itself rather than adding wrapper logic in the client code.
- Prefer pushing complexity into well-tested, reusable library code over ad-hoc client-side handling.

- always be careful to review, abstract common logic. 
- search for existing logic before duplicating and avoid code duplication
- always try to think of a few ways to implement and compare pros and cons before deciding
- always consider maintainability, testability, readability and possible future extensions

## Changing Function Signatures

- When changing a function's signature (adding/removing/renaming parameters), grep for ALL callers across the codebase and update them — not just the obvious ones.
- Pay special attention to FastAPI dependency functions (`Depends(fn)`) that are also called directly. `Depends()` default values silently become real values when called outside DI, causing runtime errors with no static warning.
- Avoid functions used as both `Depends(fn)` and called directly. If unavoidable, split into a pure logic function (no `Depends` defaults) and a thin DI wrapper.

## Before implementing or planning new functionality, and before exploring the code by reading files, follow these steps to leverage existing resources and get overall big picture first.

  Check for existing utilities and patterns:
  1. Call `list_libraries` to discover what's available across the codebase
  2. If something relevant exists, call `get_design_doc` for implementation details
  3. For `[library]` entries: import and reuse the code
  4. For `[project]` entries: use as inspiration for patterns or architecture to follow

  If the MCP tools above (`list_libraries`, `get_design_doc`) are not available, use the design docs instead:
  1. Read `designs/INDEX.md` to get an overview of all modules and their key exports
  2. Read the relevant `designs/<module>.md` file identified from the index for detailed design and implementation guidance
  3. For `[library]` entries: import and reuse the code
  4. For `[project]` entries: use as inspiration for patterns or architecture to follow

## Alembic Migrations

Dev uses SQLite, production uses MySQL. Migrations must work on both:

- **Always use `batch_alter_table`** for ALTER operations (add/drop columns, constraints). SQLite doesn't support ALTER natively — batch mode does a copy-and-move. MySQL handles it as a normal ALTER. The env.py has `render_as_batch=True` as a safety net, but prefer explicit `batch_alter_table` in migration code.
- **`op.create_table` / `op.drop_table`** work on both dialects without batch mode.
- **Named constraints** (e.g. `create_foreign_key("fk_flights_aircraft_id", ...)`) — use them so the downgrade can reference them by name.
- **Reference migration 004** (`004_flight_profiles.py`) as the canonical pattern for "create table + add FK column to existing table."
- **Column renames on MySQL** need `existing_type` parameter — see memory note.
- If dialect-specific logic is needed, use `op.get_bind().dialect.name == "mysql"` (see migrations 014, 015).

## Setup

- don't run npm run build as for development we use npv run dev
- use venv activate if venv exist so we use correct library. If there are no venv in current directory, check ../main/venv and activate this. It means we are in a git worktree and main venv should be used
- use .env file to set environment variables for local development

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


