## Always start here

Design docs give you architecture, key exports, and non-obvious decisions faster than grepping. For any task that touches code (features, bug fixes, UX questions, "how does X work", refactors), BEFORE reading or grepping:

1. Call the `mcp__library-docs__list_libraries` MCP tool (or read `designs/INDEX.md` if the server is unavailable). It is an MCP tool, not a skill — do NOT invoke it via `Skill`.
2. If a relevant module appears, call `mcp__library-docs__get_design_doc` (or read `designs/<module>.md`).
3. Only then explore with Grep/Read.

For `[library]` entries: import and reuse. For `[project]` entries: follow the patterns. Skip this only for trivial edits (typo, one-line change in a file already in context).

## Read the design doc before you write

- **Adding a datetime column** → `designs/time-alignment-audit.md` (`TZDateTime`; when `fsp=6` is required).
- **Writing an Alembic migration** → `designs/migrations.md` (dev SQLite / prod MySQL; `batch_alter_table` is mandatory).
- **Changing a meteorology choice** → `designs/meteorology-decisions.md`.

## Working from a GitHub issue

Read the full comment thread (`gh issue view <n> --comments`), not just the body — planning and design decisions land in comments and supersede the original description.

## Finding the code-review bot's review on a PR

The bot posts an ordinary **comment on the PR's conversation tab** — not a GitHub "Review" (Approve / Request-changes), not inline diff comments. Look in `gh pr view <n> --comments` for a comment by `claude` whose **first line contains "Code Review"**.

Match on the text, not the markdown decoration: the shape is pinned in `.claude/commands/code-review.md` as `## Code Review`, but it has drifted to `**Code Review**` before, and a matcher keyed off `##` silently finds nothing when it does.

## Code Design Principles

- When adding logic around a library call, first consider enhancing the library instead of wrapping it in client code.
- Prefer pushing complexity into well-tested, reusable library code over ad-hoc client-side handling.
- Search for existing logic before duplicating.

## Changing Function Signatures

- Grep for ALL callers across the codebase and update them — not just the obvious ones.
- Watch FastAPI dependency functions (`Depends(fn)`) that are also called directly: `Depends()` defaults silently become real values outside DI, causing runtime errors with no static warning.
- Avoid a function used both as `Depends(fn)` and called directly. If unavoidable, split into a pure logic function (no `Depends` defaults) and a thin DI wrapper.

## Setup

- **Each worktree (including `main/`) has its own venv at `./venv`.** Never fall back to `../main/venv` — that caused editable-install bugs where `weatherbrief` silently resolved to whichever directory last ran `pip install -e .`. Missing venv → `/worktree-init`.
- **CI runs the iOS unit target only** — the XCUI journeys aren't gated, so green CI says nothing about them. Run `-only-testing:flyfun-weatherUITests` locally (~5 min) before merging a UI change; nightly on main via `.github/workflows/ios-ui-nightly.yml`.
- **There is intentionally no `data/` directory in a worktree** — `.env` is copied from main and its absolute paths (`DATA_DIR`, `DATABASE_URL`) keep heavy data and the DB shared. Code that bypasses `DATA_DIR` and writes `./data` should fail loudly.
- Frontend is esbuild; `/devserver` runs the watch that rebuilds `web/dist/*.js`. Don't run `npm run build` by hand.

## Worktree & dev server

`/worktree-init <branch>` creates a sibling worktree with its own venv, npm install, and `.env`; it does not run alembic or start a server. `/devserver` starts tmux session `wb-<basename>` (pane 0 uvicorn `--reload`, pane 1 esbuild) — port 8000 for `main`, 8001+ for worktrees, so several can run at once. `/devserver --https` is a **singleton** at `https://localhost.ro-z.me:8443` for iOS simulator testing; invoking it from another worktree kills and restarts it there.
