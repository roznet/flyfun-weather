# How to Work on WeatherBrief with Claude Code

This guide has two audiences:

- **You want to work on WeatherBrief** → skip to [Setting up this project](#setting-up-this-project), then try the [example tasks](#example-tasks).
- **You want the working method for your own project** → read [How we work](#how-we-work) and the [adoption checklist](#adopting-this-in-your-own-project). Nothing there is specific to weather or aviation. You can point your own Claude at this file and say *"read HOW_TO.md from roznet/flyfun-weather and help me adopt these practices in this repo"*.

---

## How we work

Almost all of the code in this project is written by Claude Code. The owner decides *what* to build and *whether it lands*. Claude does the rest: research, implementation, tests, design docs, review, follow-ups and deploy. A whole issue often runs unattended in the cloud and comes back as a PR with a brief that can be read on a phone.

Better prompting is a small part of why this works. Most of it comes from a few habits that give each new session the context the last one had. They're listed below, most important first.

### 1. Design docs are Claude's map of the codebase

Every session starts with no memory of the code. Without help, it greps through hundreds of files, misses the decisions behind them, and "fixes" things that were deliberate. So the repo keeps a `designs/` folder of short notes, written by Claude for the next Claude:

- **`designs/INDEX.md`** has one entry per module: a 1–2 line description, its key exports, and `→ Full doc: name.md`. It is the map.
- **One doc per component**, under ~300 lines, with fixed sections: *Intent* (what must not change), *Architecture*, *Key choices and why*, *Patterns*, *Gotchas*, *References*. A doc is not a changelog and doesn't repeat the code. It holds what you can't read *from* the code.
- **[mcp-library-docs](https://github.com/roznet/mcp-library-docs)** serves the index and docs to Claude through two tools: `list_libraries` (the map, covering this repo and any sibling libraries that have their own `designs/INDEX.md`) and `get_design_doc`. Reading a 50-line doc beats grepping 500 lines of code.
- **`.claude/CLAUDE.md` makes it a rule**: "before reading or grepping, call `list_libraries`, then `get_design_doc`." It also lists a few specific "read this before you write" triggers, e.g. *adding a datetime column → `time-alignment-audit.md`*, *writing a migration → `migrations.md`*, *changing a meteorology choice → `meteorology-decisions.md`*. Those triggers stop the mistakes that keep coming back.
- **Docs change in the same PR as the code.** The implementing agent updates the docs for what it touched, and records the decisions it made so the next agent doesn't undo them. The `/sync-designs <doc>` skill does this for one doc. `/sync-all-designs` runs a multi-agent workflow (one subagent per doc) that re-checks every doc against the code. It's expensive, so it runs only now and then.
- **Lifecycle folders:** `designs/future/` holds ideas and brainstorms, `designs/plans/` holds rollout plans in flight, and `designs/archive/` holds finished plans. When a feature ships, the as-built part moves into a main doc and the plan is archived. `INDEX.md` lists only what exists now.

A decision log (`meteorology-decisions.md`, numbered entries, each with its rationale) is worth having for whichever area is most opinionated in your domain. It stops agents from re-arguing settled choices.

### 2. The GitHub issue is the spec, and its comments supersede the body

Work starts as an issue. Usually the owner and Claude discuss the idea in a local session, and Claude then writes a **detailed issue**: the problem, root cause or call sites, the plan, the decisions already made, and acceptance criteria. Later planning goes into comments. CLAUDE.md tells every agent to read the **whole thread** (`gh issue view <n> --comments`), because the last comment often overrides the first.

A well-written issue is what makes the cloud step reliable. The agent that implements it doesn't need the conversation that produced it.

### 3. `/implement-issue <n>`, often in the cloud, unattended

[`.claude/skills/implement-issue/SKILL.md`](.claude/skills/implement-issue/SKILL.md) is the main skill. It runs in [Claude Code on the web](https://claude.ai/code) as well as locally, and it:

1. Works out where it is (cloud or Mac, attended or unattended, which toolchains exist) and adapts. If nobody is watching, it never stops to ask. It picks the conservative option and records it as a decision.
2. Reads the issue thread and the design docs, checks the work isn't already done, and **classifies the change**: plumbing, data collection, pilot-facing, meteorology, or a risk label it names itself (privacy, cost, user data…).
3. Branches. Locally that means its own worktree; in the cloud it uses the session's branch.
4. Implements with tests, keeps to the issue's scope (anything else goes under "follow-ups noticed"), and updates the design docs.
5. Verifies only what that machine can verify, and reports counts rather than "looks good". In the cloud it writes Swift it can't compile and says so, then lists the exact commands for a "pick up on a Mac" checklist.
6. Opens a PR whose body is the **Owner's brief**.

### 4. The Owner's brief: understanding without reading code

The owner doesn't review code line by line, but the product is safety-relevant, so they need to understand and agree with what goes in. Their bottleneck was *working out what to ask*. The brief does that work for them, in under 30 lines:

- **Kind** (e.g. 60% plumbing · 25% meteorology · 15% pilot-facing). Depth scales with risk: a plumbing fix is two lines; a meteorology change gets the full treatment.
- **What changes for the pilot.**
- **Decisions I made for you**: each one with the alternative, the reason, and whether it's easy to flip.
- **How it could go wrong**, and the symptom you'd see.
- **Verified vs. not verified.** Only what can be reproduced counts: tests in the diff, CI on the head commit.
- **2–4 questions worth asking**, specific to this diff.
- Design docs updated, deploy notes, follow-ups.

The skill includes a checklist of what self-reports tend to miss: unbounded growth, departures from the issue, numbers copied from old comments, other screens that show the same value, and "verified" claims that aren't. For high-risk PRs, **`/brief-check <pr>`** gets a second agent to write the brief again from the diff alone and post the differences (missed / understated / overstated).

### 5. Automated review → `/process-review` → `/land-pr`

- **A review bot runs on every push** ([`.github/workflows/claude-code-review.yml`](.github/workflows/claude-code-review.yml), driven by [`.claude/commands/code-review.md`](.claude/commands/code-review.md)). It posts one PR comment titled "Code Review" with Critical / Important / Minor sections. A separate reviewer catches what the author missed.
- **`/process-review`** waits for that comment, sorts each finding into *blocker*, *cosmetic* or *unsure*, and reports back. Two rules come from experience. **Every push triggers a new paid review round**, so it pushes only for real blockers, and when it does push it includes every worthwhile fix in that one push. **It stops after 2 rounds** and hands back to the owner, who decides "fix in the PR" vs "merge and fix on main".
- **`/land-pr <n>`** runs once the owner has decided the PR lands. It checks CI and every review round, runs what CI doesn't (iOS UI journeys), merges with `--rebase`, makes the remaining fixes **as direct commits on main** instead of another PR round, updates memory and design docs, removes the worktree, and ends with a short summary that includes what the deploy will need.
- **`/deploy`** is a separate step that always needs explicit confirmation. Nothing deploys as a side effect.

Skills with side effects (`deploy`, `archive`, `land-pr`, `worktree-init`, `implement-issue`…) set `disable-model-invocation: true`, so they run only when you type them.

### 6. Worktrees: many sessions in parallel, each one testable

Several Claude sessions work at once, e.g. two issues, a review, and an investigation. Each needs its own checkout that it can **run and test** in isolation. **`/worktree-init <branch>`** ([skill](.claude/skills/worktree-init/SKILL.md)) creates a sibling worktree that's ready to go:

- **Its own venv** with an editable install, then checks that `import weatherbrief` resolves to *this* worktree. A shared venv once caused the package to load from whichever directory last ran `pip install -e .`, and the result was subtle, wrong test results.
- **`npm install`** for the frontend.
- **`.env` copied from main.** It uses absolute paths, so the heavy data (GRIB caches, nav DB) and the dev DB are shared rather than downloaded again. There's deliberately **no `data/` directory** in a worktree, so code that writes `./data` instead of honouring `DATA_DIR` fails loudly.
- **A recipe for giving a branch its own copy of the DB** when it adds a migration, plus the check that catches the half-applied version (app and Alembic reading different DB files).

Then **`/devserver`** starts a per-worktree tmux session (uvicorn `--reload` plus the esbuild watcher) on its own port: 8000 for `main`, 8001+ for worktrees. That way several branches can run side by side and each can be clicked through in a browser.

Rule for concurrent sessions: **check HEAD and the branch again right before every commit**, because another session may have switched it.

### 7. Memory: corrections persist

Claude Code's auto-memory (`~/.claude/projects/<project>/memory/`) keeps one fact per file, indexed by `MEMORY.md`. The memories that pay off most are **feedback**: a correction or a confirmed preference, saved with *why* and *how to apply*. Examples: "PR blocker threshold is strict, defer cosmetic findings", "never run two `xcodebuild test` at once", "use fictional test data", "lead with the broken premise before the consequences". Each correction then happens once instead of every session.

Two rules keep it useful:

- **Keep `CLAUDE.md` and `MEMORY.md` small.** Both load into every turn, so every line costs something on every turn. Write one line per rule. Reasoning and detail go into the memory file or a design doc.
- **Memory is not the repo.** Don't save what code, git history or design docs already record. Before relying on a "pending" memory, check it against git, because the work may have landed since.

### 8. Skills turn repeated explanations into one command

If you've explained a procedure to Claude twice, make it a skill (`.claude/skills/<name>/SKILL.md`). Examples here: `/devserver`, `/investigateflight` (load a briefing's data and reproduce its analysis), `/sync-ecmwf`, `/check-health` (production health check), `/sync-ios-web` (find drift between hand-copied web and iOS surfaces), `/archive` (App Store build). A skill is also where lessons go: when one fails, fix the skill rather than adding a note to a prompt.

### 9. Multi-agent and background work

- **Background sessions** for long work, such as implementing, babysitting a PR, or a long investigation. Each ends with a short report: what was done, where it is, and the next command.
- **Subagents** for noisy searches (log trawls, wide grep sweeps), so only the findings come back to the main context.
- **Workflows** for fan-out with a fixed structure, e.g. one agent per design doc, or one per review dimension followed by a verification pass. They are expensive, so use them deliberately.
- **Heavy local jobs run one at a time** (full test suite, `xcodebuild test`, big data decodes). Parallel agents don't make the Mac bigger.

### 10. How Claude reports back

These are saved as feedback memories and apply everywhere:

- **Name the broken assumption first**, then the consequences.
- **Report verification honestly**: counts, the real command, and what was *not* run. "Written, not compiled" is a legitimate status.
- **Estimate in agent time** and call out slow external steps (a CI run, a 2 GB download).
- **Give a recommendation, not a survey of options.** Ask only when the decision really belongs to the owner.

---

## Setting up this project

### Let Claude do the setup

You can have Claude Code walk you through (and run) most of these steps automatically. After cloning the repo, start Claude Code and say:

```
> Read HOW_TO.md and help me set up this project from scratch
```

Claude will run through steps 1–3 (clone, create venv, install and register the MCP server), then ask you to restart so the MCP server can connect. After restarting, say:

```
> Continue setup from HOW_TO.md — pick up from step 4
```

The only things you'll need to provide manually are API keys (step 4) and any missing system prerequisites.

### Prerequisites

- Python 3.12+
- Node.js 22+
- [Git LFS](https://git-lfs.com/) (`brew install git-lfs && git lfs install` on macOS)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed (`npm install -g @anthropic-ai/claude-code`)
- A GitHub account with access to the repository


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

> **Tip (recommended if you'll use design docs in more than one project):** install `mcp-library-docs` once in a long-lived venv or with `pipx`, and register it at **user scope** so every project gets it. That is how the maintainer runs it. The server also discovers sibling libraries' `designs/INDEX.md`, so a single install covers all of them:
> ```bash
> claude mcp add -s user library-docs -- /path/to/long-lived/venv/bin/python -m mcp_library_docs
> ```
> Per-worktree venvs come and go, so don't point a user-scope server at one.

Then register it with Claude Code, **using the venv's Python path explicitly**. This is important: Claude Code launches MCP servers outside your shell, so bare `python` would resolve to the system Python (which won't have the package installed). Use the full path to the venv interpreter instead:

```bash
claude mcp add library-docs -- "$(pwd)/venv/bin/python" -m mcp_library_docs
```

By default this registers the server for this project directory only (stored in your `~/.claude.json`). Use `-s user` (tip above) for all projects, or `-s project` to write a shareable `.mcp.json` into the repo.

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

### 8. Work on a branch in its own worktree

Leave the main checkout for small fixes and keep feature work out of it. From the main checkout:

```
> /worktree-init my-feature
```

This creates `../my-feature` as a sibling worktree with its own venv (editable install verified), `npm install` done, and `.env` copied so data and the dev DB are shared with main. Then start a session there:

```bash
cd ../my-feature && claude
> /devserver          # tmux session on its own port (8001+), so main's server keeps running
```

If the branch adds an Alembic migration, ask Claude for the DB-fork recipe printed by `/worktree-init`. Otherwise the migration will also change main's dev DB. When the branch has merged, `git worktree remove ../my-feature` (`/land-pr` does this for you).

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

**Compare with the real result:** see [PR #15](https://github.com/roznet/flyfun-weather/pull/15) which closed [issue #14](https://github.com/roznet/flyfun-weather/issues/14). The PR was then automatically reviewed by a Gemini code-review bot (used as an independent validation agent), which caught a double-counting bug in the IFR evaluator. Claude addressed the feedback in a [follow-up commit](https://github.com/roznet/flyfun-weather/commit/c3a6f6f) — a good example of the AI-writes, AI-reviews, AI-fixes cycle. (That was an early version of the loop. Today the reviewer is the Claude review bot on every push, triaged with `/process-review` and landed with `/land-pr`. See [How we work §5](#5-automated-review--process-review--land-pr).)

**Today's version of this exercise:** create a worktree (`/worktree-init try-vfr`), then in a session there run `/implement-issue 14` against a copy of the issue in your own fork. Compare its Owner's brief with what shipped in PR #15. The decisions it says it made for you are where its reading of the ambiguous spec differs from the original.

---

## Tips

- **Design docs first** — When Claude calls `list_libraries` at the start of a task, it gets a map of the entire system. This is much faster than reading code files one by one.
- **Skills save time** — `/devserver` and `/investigateflight` encode multi-step workflows so you don't have to explain them each time.
- **Be specific** — "Why is icing RED at point 5 for GFS?" gets better results than "explain the icing advisory".
- **Claude can run code** — It can write and execute Python scripts against the project's venv, which is especially powerful for debugging weather data.
- **Brainstorm locally, implement remotely** — discuss an idea in a local session, ask Claude to write it up as a detailed issue (plan, decisions, acceptance criteria), then run `/implement-issue <n>` in the cloud and read the brief later.
- **Correct once, then make it stick** — when Claude gets something wrong that it will get wrong again, say *"remember that…"* so it saves a feedback memory, or ask it to fix the skill that misled it.
- **Ask for the brief, not the diff** — for ad-hoc work too: "what changes for the user, which decisions did you make, what's unverified?"
- **Small fixes go straight to main, features go through PRs** — the review round is worth its cost for features, not for a typo.

---

## Adopting this in your own project

A checklist for a Claude session in **another** repo. Do it in this order; each step is useful without the ones after it. The files linked here are working examples to adapt; they aren't templates to copy word for word, because they contain project specifics (iOS, Alembic, a weather data pipeline).

1. **Design docs.** Install [mcp-library-docs](https://github.com/roznet/mcp-library-docs) at user scope (Setup §3 tip). Create `designs/INDEX.md` with `→ Full doc: name.md` entries, then write the first 3–5 docs for the most-touched components. Ask Claude to explore each one and write *Intent / Architecture / Key choices / Patterns / Gotchas / References*, under 300 lines. A design-sync skill like the one described in §1 keeps them current. A copy is worth putting in `~/.claude/skills/` so every project can use it.
2. **A short `.claude/CLAUDE.md`.** An "Always start here" rule (call `list_libraries`, then `get_design_doc`, *then* grep). A few "read X before you write Y" triggers for the mistakes that keep recurring. Setup rules (venv location, how to run tests). One line per rule. See [ours](.claude/CLAUDE.md).
3. **Issues as specs.** Tell Claude in CLAUDE.md to read full issue threads. Get into the habit of having Claude write the issue at the end of a discussion.
4. **An implement-issue skill** adapted from [ours](.claude/skills/implement-issue/SKILL.md). Keep the environment detection (cloud vs local, attended vs unattended), the risk classification, "update design docs in the same PR", honest verification, and the Owner's brief template with its five-point checklist. Set it up in [Claude Code on the web](https://claude.ai/code) so it can run while you're away.
5. **A worktree-init skill** adapted from [ours](.claude/skills/worktree-init/SKILL.md): a venv per worktree with an import check, deps installed, `.env` copied with absolute data paths, and no local `data/` dir. Add a dev-server skill that gives each worktree its own port.
6. **A review bot plus a triage skill.** Install the [Claude GitHub app](https://github.com/anthropics/claude-code-action) and add a workflow like [`claude-code-review.yml`](.github/workflows/claude-code-review.yml) that runs a `/code-review` command and posts one comment with a fixed first line. Then add [`/process-review`](.claude/commands/process-review.md) (strict blocker threshold, batch fixes into one push, 2-round cap) and [`/land-pr`](.claude/skills/land-pr/SKILL.md) (merge, then finish the rest on main).
7. **Let memory accumulate.** Each time you correct Claude, ask it to save the correction with *why* and *how to apply*. Prune `MEMORY.md` when it gets long.
8. **Turn every repeated explanation into a skill.** Mark the ones with side effects `disable-model-invocation: true`.
