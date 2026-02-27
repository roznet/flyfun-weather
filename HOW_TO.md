# How to Work on WeatherBrief with Claude Code

This guide walks you through setting up the WeatherBrief project and using [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to explore, run, debug, and contribute to the codebase.

## Let Claude do the setup

You can have Claude Code walk you through (and run) most of these steps automatically. After cloning the repo, start Claude Code and say:

```
> Read HOW_TO.md and help me set up this project from scratch
```

Claude will run through steps 1–3 (clone, create venv, install and register the MCP server), then ask you to restart so the MCP server can connect. After restarting, say:

```
> Continue setup from HOW_TO.md — pick up from step 4
```

The only things you'll need to provide manually are API keys (step 4) and any missing system prerequisites.

## Prerequisites

- Python 3.12+
- Node.js 22+
- [Git LFS](https://git-lfs.com/) (`brew install git-lfs && git lfs install` on macOS)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed (`npm install -g @anthropic-ai/claude-code`)
- A GitHub account with access to the repository

## Setup

### 1. Clone the repository

```bash
git clone git@github.com:roznet/flyfun-weather.git
cd flyfun-weather
```

### 2. Create the virtual environment

Create the project venv early so you can install the MCP server into it:

```bash
python -m venv venv
source venv/bin/activate
```

### 3. Install and register the mcp-library-docs MCP server

This project follows a pattern of maintaining design docs alongside code and exposing them to Claude via an MCP server called [mcp-library-docs](https://github.com/roznet/mcp-library-docs). The server gives Claude access to design documentation across the codebase and related libraries, letting it quickly understand how the system is architected rather than having to grep through hundreds of files. This pattern is not specific to WeatherBrief — any project can adopt it by adding an `INDEX.md` and design docs (see the [mcp-library-docs README](https://github.com/roznet/mcp-library-docs) for details).

Install it into the project venv:

```bash
pip install mcp-library-docs
```

> **Tip:** If you work on multiple projects that use design docs, you may prefer to install `mcp-library-docs` somewhere more permanent (e.g., via `pipx install mcp-library-docs` or into a shared venv) and point the `claude mcp add` command to that Python instead. This is left to your preference and setup.

Then register it with Claude Code, **using the venv's Python path explicitly**. This is important: Claude Code launches MCP servers outside your shell, so bare `python` would resolve to the system Python (which won't have the package installed). Use the full path to the venv interpreter instead:

```bash
claude mcp add library-docs -- "$(pwd)/venv/bin/python" -m mcp_library_docs
```

This creates a `.mcp.json` in the project that tells Claude Code to start the MCP server when you open a session here.

> **Why the full path?** `claude mcp add` saves the command verbatim. When Claude Code later spawns the MCP server, it does so from its own process — not from your activated venv. If you use bare `python`, it will pick up the system Python, fail to find `mcp_library_docs`, and the MCP tools won't be available. Using the absolute path to `venv/bin/python` ensures it always works regardless of how the process is started.

> **Restart required:** If you're running these steps inside Claude Code, exit (`/exit`) and restart `claude` now. The MCP server only connects at session startup. After restarting, continue from step 4.

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

### 5. Download the airports database

The app needs an airports database (SQLite, ~11 MB). It's stored with Git LFS in a separate repo:

```bash
git clone --depth 1 https://github.com/roznet/flyfun-apps.git /tmp/flyfun-apps
cp /tmp/flyfun-apps/data/airports.db data/airports.db
rm -rf /tmp/flyfun-apps
```

> **Note:** You need [Git LFS](https://git-lfs.com/) installed (`brew install git-lfs && git lfs install`) for the clone to pull the actual database file.

Then make sure `AIRPORTS_DB` in your `.env` points to it (e.g., `AIRPORTS_DB=./data/airports.db`).

### 6. Install project dependencies

The venv was already created in step 2. Activate it (if not already) and install the project:

```bash
source venv/bin/activate
pip install -e ".[dev]"
cd web && npm install && cd ..
```

### 7. Start Claude Code

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

Once running, open http://localhost:8000 in your browser.

### Creating a flight and viewing the briefing

If you're not familiar with aviation, here's a quick walkthrough to get a briefing on screen.

**From the web UI:**

1. Open http://localhost:8000
2. Click **New Flight**
3. In the **Waypoints** field, enter `LFAT LFMD` — these are [ICAO airport codes](https://en.wikipedia.org/wiki/ICAO_airport_code) for Le Touquet (northern France) and Cannes–Mandelieu (southern France), a scenic route across France
4. Set the **Departure date** to a few days from now (weather data is only available for the near future)
5. Set the **Duration** to `3h`
6. Click **Create**

Once the flight is created, click **Refresh** to fetch weather data. This takes a minute or two — it downloads forecasts from multiple weather models. When it's done, the briefing page shows the full weather analysis: route advisories, cross-sections, soundings, and a synopsis.

**From the command line (curl):**

You can also create flights via the API, which is handy for scripting or if you prefer the terminal:

```bash
# Create a flight from Le Touquet (LFAT) to Cannes (LFMD), departing in 2 days, 3h duration
curl -s -X POST http://localhost:8000/api/flights \
  -H "Content-Type: application/json" \
  -d '{
    "waypoints": ["LFAT", "LFMD"],
    "departure_time": "'$(date -u -v+2d '+%Y-%m-%dT10:00:00Z')'",
    "flight_duration_hours": 3.0
  }'
```

This returns a JSON response with the flight's `id`. Use it to trigger a weather refresh:

```bash
curl -s -X POST http://localhost:8000/api/flights/FLIGHT_ID/packs/refresh
```

Then open the briefing URL from the response in your browser to see the full analysis.

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


## Contributing Code

The examples below are taken from real development of this app. Each one started as a GitHub issue, was implemented by prompting Claude Code, and merged. We've tagged the codebase just before each change so you can try it yourself and compare your result with what actually shipped.

### Example 1 — Improve compact mode (frontend, simple)

**Issue:** [#19 — Make a better compact mode for briefing](https://github.com/roznet/flyfun-weather/issues/19)

The briefing page had a compact/annotated toggle, but compact mode still showed too much detail. The issue asked to rename the toggle, hide secondary advisories, trim the synopsis, and remove sounding analysis in compact mode.

**Try it:**

```bash
git checkout -b try/compact-mode example/simple1
```

Start Claude Code and get the dev server running:

```
> /devserver
```

Once the server is up at http://localhost:8000, create a test flight so you can see the current behavior. You can do this from the UI, or with curl:

```bash
# Create a flight from Le Touquet to Cannes, departing in 2 days, 3h duration
curl -s -X POST http://localhost:8000/api/flights \
  -H "Content-Type: application/json" \
  -d '{
    "waypoints": ["LFAT", "LFMD"],
    "departure_time": "'$(date -u -v+2d '+%Y-%m-%dT10:00:00Z')'",
    "flight_duration_hours": 3.0
  }'
```

Trigger a weather data refresh for it (replace `FLIGHT_ID` with the `id` from the response above):

```bash
curl -s -X POST http://localhost:8000/api/flights/FLIGHT_ID/packs/refresh
```

Open the briefing in your browser and click the compact/annotated toggle to see how it behaves _before_ the change. Note how compact mode still shows sounding analysis, model comparison, and secondary advisories.

Now prompt Claude to implement the fix:

```
> The briefing page has a compact/annotated toggle. Change it so that compact really only
> shows key information:
> - Rename the toggle to "Compact | Full Details"
> - Full Details shows everything
> - Compact should only show:
>   - Route advisories, except secondary ones (for now only "model confidence" is secondary)
>   - For the synopsis, only synoptic and trend
>   - Hide sounding analysis and model comparison sections
```

Claude will read the design docs, find the relevant frontend files, and implement the changes across the briefing UI. Once it's done, go back to your briefing in the browser and try the toggle again — the esbuild watcher will have rebuilt the frontend automatically so you can see the difference immediately.

**Compare with the real result:** see [commit 9e5450b](https://github.com/roznet/flyfun-weather/commit/9e5450b) which closed [issue #19](https://github.com/roznet/flyfun-weather/issues/19).

### Example 2 — Add VFR/IFR feasibility advisories (backend, advanced)

**Issue:** [#14 — VFR and IFR flight advisory](https://github.com/roznet/flyfun-weather/issues/14)

This is a more involved example: adding two entirely new advisory evaluators to the analysis pipeline. It requires understanding the advisory framework, the weather data model (airport conditions, en-route cloud layers, icing, convective activity), and the `@register` pattern that makes advisories auto-discovered. The issue is deliberately written as a natural-language spec with some ambiguity — exactly the kind of prompt you'd give Claude in practice.

**Try it:**

```bash
git checkout -b try/vfr-ifr-advisory example/advanced1
```

Start Claude Code and get the dev server running:

```
> /devserver
```

Create a test flight and refresh it (same as Example 1) so you have a briefing to test against. Then prompt Claude with the issue:

```
> Add two new advisories that indicate feasibility of a VFR and an IFR flight.
>
> These should be selectable in the profile, so different profiles may select
> VFR, IFR, or both.
>
> VFR advisory: check that at origin and destination the field is predicted to
> be VFR conditions and the selected cruising altitude is below the lowest
> ceiling within XX nautical miles of both origin and destination, and that
> altitude is in VMC for the en-route phase. XX should be a configurable
> parameter. Another parameter is minimum distance to cloud (e.g. 1000ft):
> ceiling and clouds should be at least that for origin, destination, and
> en-route — otherwise AMBER. If clear-of-cloud conditions are not fulfilled
> or IFR is reported at destination/origin, it should be RED.
>
> IFR advisory: GREEN as long as origin/destination are IFR or better. LIFR
> makes it AMBER. If cruise is not in VMC it's AMBER. If in icing for more
> than 30% of the route, RED. Crossing convective activity is RED; near
> convective activity but not in it at cruise altitude is AMBER. Parameters:
> % of cruise in icing, % of cruise in IMC, minimum ceiling for AMBER at
> destination/origin.
```

This one is significantly more complex than Example 1. Claude will need to:
1. Read the design docs to understand the advisory framework and `@register` pattern
2. Study existing advisories (e.g., icing, turbulence) to follow established conventions
3. Create two new evaluator modules with configurable parameters
4. Write tests

Once done, refresh your test flight and check the briefing — the new advisories should appear in the route analysis.

**Compare with the real result:** see [PR #15](https://github.com/roznet/flyfun-weather/pull/15) which closed [issue #14](https://github.com/roznet/flyfun-weather/issues/14). The PR was then automatically reviewed by a Gemini code-review bot (used as an independent validation agent), which caught a double-counting bug in the IFR evaluator. Claude addressed the feedback in a [follow-up commit](https://github.com/roznet/flyfun-weather/commit/c3a6f6f) — a good example of the AI-writes, AI-reviews, AI-fixes cycle.

---

## Tips

- **Design docs first** — When Claude calls `list_libraries` at the start of a task, it gets a map of the entire system. This is much faster than reading code files one by one.
- **Skills save time** — `/devserver` and `/investigateflight` encode multi-step workflows so you don't have to explain them each time.
- **Be specific** — "Why is icing RED at point 5 for GFS?" gets better results than "explain the icing advisory".
- **Claude can run code** — It can write and execute Python scripts against the project's venv, which is especially powerful for debugging weather data.
