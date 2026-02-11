## Code Design Principles

- When adding logic around library calls, first consider whether it would be better to enhance the library itself rather than adding wrapper logic in the client code.
- Prefer pushing complexity into well-tested, reusable library code over ad-hoc client-side handling.

- always be careful to review, abstract common logic. 
- search for existing logic before duplicating and avoid code duplication
- always try to think of a few ways to implement and compare pros and cons before deciding
- always consider maintainability, testability, readability and possible future extensions

## Before implementing or planning new functionality, and before exploring the code by reading files, follow these steps to leverage existing resources and get overall big picture first.

  Check for existing utilities and patterns:
  1. Call `list_libraries` to discover what's available across the codebase
  2. If something relevant exists, call `get_design_doc` for implementation details
  3. For `[library]` entries: import and reuse the code
  4. For `[project]` entries: use as inspiration for patterns or architecture to follow

## Setup

- don't run npm run build as for development we use npv run dev
- use venv activate if venv exist so we use correct library
- to run the web app, use tmux session with multiple panes for server, client, logs etc. let me know the session name and instruction to attach if needed
- use .env file to set environment variables for local development


