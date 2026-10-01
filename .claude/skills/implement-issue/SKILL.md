---
name: implement-issue
description: Implement a GitHub issue end-to-end — read the thread + design docs, branch, build, verify what this machine can verify, open a PR — and finish with a short owner's brief (plumbing vs pilot-facing, what could go wrong, how we know it works, what to ask) plus a "pick up on a Mac" checklist for anything that couldn't be built or tested here. Invoke with the issue number.
disable-model-invocation: true
---

# Implement issue

The user owns a safety-relevant product but doesn't review code line by line. The
deliverable is therefore **two things**: a PR that works, and a brief that lets them
understand and own what went in within a couple of minutes — without having to work
out what to ask. Often this runs unattended (cloud / background) while they're away;
they'll read the brief later, possibly on a phone, then do the final testing on a Mac.

## Inputs

- `<issue-number>` — required. If missing, ask for it and stop.

## Step 0 — Know where you are

Detect, don't assume, and remember the answers for the brief:

- **Cloud (Claude Code on the web) or local Mac?** Cloud if `CLAUDE_CODE_REMOTE=true`,
  or — as a fallback — the checkout is not under `~/Developer/public/flyfun-weather/`
  and has no sibling `main/` worktree. Cloud is already an isolated, throwaway
  checkout: **no worktree, no `/devserver`, no shared `DATA_DIR`/`.env`**.
- **Attended or unattended?** If this is a background/cloud session or the user said
  they're away, run **unattended**: never stop to ask — make the call, prefer the
  most conservative / reversible option, and record it under "Decisions I made for
  you". If attended, you may ask, but only for decisions that are genuinely theirs.
- **Toolchain.** Check each and note what's missing:
  - Python: locally, `./venv/bin/python` (never fall back to `../main/venv`). In the
    cloud, use whatever environment the sandbox provides (`python -c "import weatherbrief"`);
    if it's missing, set one up the way CI does (`.github/workflows/tests.yml`).
    Tests that need real GRIB/prod data can't run there — list them as unverified.
  - Web: `web/node_modules` exists.
  - iOS: `command -v xcodebuild` (macOS only). No Xcode is **fine** — write the Swift
    anyway, carefully, and hand build/test to the Mac checklist.
  - `gh` works (locally it must run with the sandbox disabled, else it returns empty
    with exit 0).

## Step 1 — Understand before touching code

1. `gh issue view <n> --comments` — read the **whole thread**; later comments
   supersede the body. Follow linked issues/PRs that the plan depends on.
2. Check it isn't already done: `git log --oneline --all --grep "#<n>"`, plus
   `git branch -a` for an existing branch. If work exists, say so and build on it
   rather than starting over.
3. Design docs first (`mcp__library-docs__list_libraries` → `get_design_doc`, or
   `designs/INDEX.md`). Honour the "read before you write" docs in CLAUDE.md
   (datetime columns, Alembic migrations, meteorology decisions).
4. **Classify the change** — this sets how careful the brief must be:
   - **Plumbing** — bug fix, refactor, infra, data pipeline. No pilot-visible change.
   - **Data collection** — new rows/columns/archives. Invisible now, but shapes
     future calibration; what is recorded matters.
   - **Pilot-facing** — UI, wording, what a briefing shows.
   - **Meteorology** — anything that changes a grade, threshold, advisory or digest
     judgement. Check `designs/meteorology-decisions.md`.
   Most issues are a mix; estimate the split.

   These four are a starting vocabulary, **not a closed list**. If part of the change
   carries a kind of risk they don't name, add your own label and treat it with the
   care it deserves — e.g. **privacy / data leaving the system** (a vendor, a log, a
   share link), **security / auth**, **cost** (LLM tokens, API quotas, droplet
   memory/disk), **user data** (migrations or deletes on rows users created). A new
   label is a signal to the owner, so prefer naming it over folding it into
   "plumbing".
5. **Meteorology or a real product choice, attended:** before implementing, give a
   5-line plan (what you'll change, the 1–3 choices that are theirs, your
   recommendation) and wait. Unattended: implement the conservative option and flag
   it prominently in the brief.

## Step 2 — Branch

Pick by the Step 0 environment:

- **Cloud:** no worktree. If the session already put you on a working branch, use
  it; otherwise `git checkout -b issue-<n>-<slug> origin/main`.
- **Local, already in a worktree for this issue** (e.g. the user launched you there):
  use it as is.
- **Local, in `main/`:** only here create a worktree `issue-<n>-<slug>`, by following
  `.claude/skills/worktree-init/SKILL.md` (it can't be invoked as a skill from here;
  fork from `origin/main`), and work inside it with its own venv. Never implement on
  `main/` itself — other sessions share that checkout.
- Locally, re-check HEAD and branch before every commit (concurrent sessions share
  checkouts).

## Step 3 — Implement

Follow CLAUDE.md principles (enhance the library over wrapping it; search before
duplicating; update **all** callers on a signature change). Add or update tests next
to the change. Test data uses fictional `ZZ-` registrations and made-up names — never
the user's real aircraft or personal details. If a hand-copied web↔iOS surface is
touched (preset tables, metrics catalog, debrief taxonomy, DTOs), update both clients
or say explicitly which one is left.

Keep scope to the issue. Things you notice but don't do go under "Follow-ups" in the
brief rather than into the diff.

## Step 4 — Verify what this machine can verify

Run only what's relevant, once each, and record the real outcome (counts, not
"looks good"):

- **Python:** `pytest <targeted paths> -q` with the Step 0 interpreter (600 s timeout, no
  `| tail`). Full suite only if the change is cross-cutting.
- **Migrations:** locally `alembic upgrade head` against the dev DB (in the cloud the
  test suite's own migrate-from-empty is the check); batch mode per
  `designs/migrations.md`.
- **Web** (in `web/`): `npx tsc --noEmit` and `npx vitest run`. Don't run
  `npm run build` by hand locally (the devserver's esbuild owns `dist/`).
- **iOS, if Xcode is present:** from `app/flyfun-weather/` build with
  `xcodebuild -scheme flyfun-weather -project flyfun-weather.xcodeproj -destination 'generic/platform=iOS Simulator' -configuration Debug build`,
  then unit tests with `-only-testing:flyfun-weatherTests`. If a UI journey changed,
  run `-only-testing:flyfun-weatherUITests` too (CI does **not** gate these). Always
  read the test count back from the `.xcresult`; a filter matching nothing still
  prints `TEST SUCCEEDED`.
- **No Xcode:** don't pretend. Re-read every Swift file you touched for type and
  optional mistakes, check new types/APIs exist in the codebase (grep), and put the
  exact commands in the Mac checklist.

Anything you could not run is **unverified** — the brief must say so.

## Step 5 — PR

- Stage specific paths only (never `git add -A`). Commit messages carry the
  attribution trailer; no personal details.
- `gh pr create` with `Closes #<n>` and a body = short summary of the change + **the
  full Owner's brief below**. The PR is where the user will read it later, so it must
  stand alone there.
- Pushing triggers the review bot. Don't wait on it here — point to `/process-review`.
- If the change includes meteorology, data collection, user data or another risk
  label, end by suggesting `/brief-check <pr>` for an independent second opinion.

## Step 6 — The Owner's brief

Print it as your final message **and** put it in the PR body. Keep it to what fits
on one phone screen or two — **≤ ~30 lines**, plain words, no code unless a name is
the clearest handle. The user will ask for detail; this is the map, not the
territory. Omit a section only when it is genuinely empty, and then say "none".

**Before writing it, re-read your own diff against this checklist.** These are the
gaps independent reviews of real briefs (PRs #632–634) found most often — an
honest agent still misses them because they live outside what it set out to do:

1. **Unbounded growth.** Anything that grows without limit — archives, tables,
   caches, logs, downloads per cycle. State the retention (or "none, kept forever")
   as a decision, and the expected size/rate if you can estimate it.
2. **Departures from the issue.** Every place the implementation differs from what
   the issue or its thread asked — including *reversals* (issue said "refuse X", you
   keep X flagged), not just changes of approach. Each is a decision for the owner.
3. **Inherited numbers.** Any figure, interval or premise copied from an old comment,
   docstring or design doc ("samples are ~24 h apart"): re-derive it from the code
   before repeating it in the brief, docstrings or tests.
4. **Other surfaces showing the same thing.** If you changed how a value is computed
   or displayed, grep for every other place that shows it (HTML report, refresh
   deltas, the other client, MCP, digest) and say whether they now disagree.
5. **Verified vs claimed.** Only count what's reproducible: tests in the diff, and CI
   on the **head** commit (check it; say "pending" if it is). Live checks you did in
   the session are "checked once, not in the repo". Unanswered review-bot findings
   on the head commit are listed, not ignored.

```
## Owner's brief — #<n> <title>

**Kind:** ~60% plumbing · 25% meteorology · 15% pilot-facing   (rough split; use whichever labels fit, incl. new ones)
**In one line:** <what this PR does, in pilot/owner terms>

**What changes for the pilot**
- <visible change, or "Nothing visible — internal only">

**Decisions I made for you**   (the ones you might have made differently)
- <choice> — over <alternative>, because <reason>. <"Easy to flip" / "Hard to undo">

**How it could go wrong**
- <concrete failure> → you'd notice it as <symptom / log line / metric>

**How we know it works**
- Verified: <what ran, with counts — e.g. "pytest: 42 passed (tests/x)">
- Not verified: <what didn't, and why>

**Worth making sure you understand**   (2–4 questions you'd want answered)
- <e.g. "Why is the threshold 30 % and not the 25 % in the issue?">

**Deploy notes:** <migration / env var / prod command / backfill / app release — or "none">

**Pick up on a Mac**   (only what wasn't run here)
- [ ] <exact command, e.g. iOS build + unit tests>
- [ ] <e.g. run UI journey X; check screen Y in the simulator for Z>

**Follow-ups noticed (not done):** <or "none">
```

Rules for the brief:
- **Lead with what matters to the owner**, not the order you worked in.
- **Questions must be specific to this diff** — the places where your judgement
  replaced theirs, a number you picked, a behaviour that changed silently. Never
  generic ("Do the tests pass?").
- **Depth follows the classification:** pure plumbing can be a few lines; anything
  touching meteorology, pilot-facing output or a risk label you added (privacy,
  cost, user data…) gets the full treatment, with the decisions spelled out.
- Be honest: an unverified Swift change is "written, not compiled", not "done".
