# How to Work on WeatherBrief with Claude Code

This guide walks you through setting up the WeatherBrief project and using [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to explore, run, debug, and contribute to the codebase.

## Prerequisites

- Python 3.12+
- Node.js 22+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed (`npm install -g @anthropic-ai/claude-code`)
- A GitHub account with access to the repository

## Setup

### 1. Clone the repository

```bash
git clone git@github.com:roznet/flyfun-weather.git
cd flyfun-weather
```

### 2. Install the mcp-library-docs MCP server

The project uses an MCP server called [mcp-library-docs](https://github.com/roznet/mcp-library-docs) that gives Claude access to design documentation across the codebase and related libraries. This is what lets Claude quickly understand how the system is architected rather than having to grep through hundreds of files.

```bash
pip install mcp-library-docs
```

### 3. Add the MCP server to Claude Code

From the project directory, register the server:

```bash
cd flyfun-weather
claude mcp add library-docs -- python -m mcp_library_docs
```

This creates a `.mcp.json` in the project that tells Claude Code to start the MCP server when you open a session here.

### 4. Set up environment variables

```bash
cp .env.sample .env
```

Edit `.env` and fill in at least:
- `WORKING_DIR` — path to a directory where data will be stored (e.g., `./data`)

Optional but useful:
- `ANTHROPIC_API_KEY` — for LLM-powered weather digest generation
- `AUTOROUTER_USERNAME` / `AUTOROUTER_PASSWORD` — for GRAMET cross-sections (free account at [autorouter.aero](https://www.autorouter.aero))

In development mode the app uses SQLite and auto-creates a dev user, so no OAuth setup is needed.

### 5. Create the virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cd web && npm install && cd ..
```

### 6. Start Claude Code

```bash
claude
```

Claude will automatically detect the project's `.claude/CLAUDE.md` instructions and connect to the MCP server. You're ready to go.

---

## Example Tasks

### Understanding the code — Ask questions

One of the most powerful ways to use Claude Code on this project is simply asking questions. Claude has access to design docs, source code, and can trace through the full analysis pipeline.

**Try it:**

```
> How is the icing index computed? What's the difference between the Ogimet index and SFIP?
```

Claude will:
1. Call `list_libraries` to discover available design docs
2. Read the relevant design doc (analysis.md) for the high-level picture
3. Dive into the source code (`src/weatherbrief/analysis/sounding/icing.py`, `sfip.py`) for implementation details
4. Explain both approaches, their physics, and how they're used in advisories

**More examples to try:**

```
> How are cloud layers detected? What's the difference between sounding-derived clouds and NWP cloud diagnostics?

> Walk me through what happens when a user clicks "Refresh" on a flight — from the API endpoint to the final briefing pack on disk.

> How does the cross-section visualization work? How does hovering sync between the cross-section, route graph, and map?

> What weather models are available and what variables does each one provide?
```

### Running the dev server

Claude has a built-in skill for managing the local development server.

**Try it:**

```
> /devserver
```

Claude will:
1. Check for existing tmux sessions
2. Verify the venv and `.env` are in place
3. Check for pending Alembic migrations
4. Start a tmux session with the FastAPI backend (port 8000) and esbuild frontend watcher

Once running, open http://localhost:8000 in your browser. Create a flight, trigger a refresh, and explore the briefing.

### Investigating a flight briefing

After you have a flight with data (either from the dev server or production), you can use Claude to debug and understand exactly how the weather analysis was computed for that specific flight.

**Try it:**

1. Open a flight briefing in your browser (e.g., `http://localhost:8000/briefing.html?flight=egtf_lfqa_lsgs-2026-03-15-1a52`)
2. Copy the URL and use the investigate skill:

```
> /investigateflight http://localhost:8000/briefing.html?flight=egtf_lfqa_lsgs-2026-03-15-1a52
```

Claude will load the flight's pack data from disk and give you a summary of what's in it. From there, you can ask targeted questions:

```
> How was the cloud layer computed at route point 5 for the GFS model?

> Why is the icing advisory RED for ECMWF but GREEN for GFS?

> Show me the raw pressure level data at waypoint LFQA for all models — I want to compare relative humidity profiles.

> Recompute the advisories with a terrain margin of 3000ft instead of 2000ft and show me how the results change.
```

Claude will write and execute Python scripts that load the pack artifacts, call the analysis functions, and show you intermediate results — the same code paths the production pipeline uses.

### Investigating a production flight

You can also investigate flights from the production server at `weather.flyfun.aero`. Claude will rsync the pack data locally and then work with it the same way:

```
> /investigateflight https://weather.flyfun.aero/briefing.html?flight=egtf_lfqa_lsgs-2026-03-15-1a52
```

---

## Contributing Code

_This section is a work in progress — check back for guided examples with tagged starting points._

<!-- Future plan: create git tags before features, give people example prompts to implement them
     e.g. git checkout -b test/add-visibility-advisory v0.x-before-visibility
     then prompt Claude: "Add a new visibility advisory that checks..." -->

---

## Tips

- **Design docs first** — When Claude calls `list_libraries` at the start of a task, it gets a map of the entire system. This is much faster than reading code files one by one.
- **Skills save time** — `/devserver` and `/investigateflight` encode multi-step workflows so you don't have to explain them each time.
- **Be specific** — "Why is icing RED at point 5 for GFS?" gets better results than "explain the icing advisory".
- **Claude can run code** — It can write and execute Python scripts against the project's venv, which is especially powerful for debugging weather data.
