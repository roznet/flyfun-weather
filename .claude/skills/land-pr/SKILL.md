---
name: land-pr
description: Land a PR the user has judged ready — check CI and the review bot, verify iOS code locally (build, unit and the affected UI journeys) when CI didn't, merge with rebase, finish every remaining finding directly on main, tidy bookkeeping, and finish with a short landing summary and what /deploy will need. Invoke with the PR number. Never deploys.
disable-model-invocation: true
---

# Land PR

The user runs this once they have **decided** the PR lands. That decision is theirs:
everything still open — review findings, follow-ups, polish — gets finished with
direct commits on `main` after the merge, not by another PR round. Your job is to
land it cleanly, finish it on main, and hand back a summary they can absorb in a
minute.

**Only two things pause before the merge:**
1. iOS code that **doesn't compile or whose tests fail** (Step 2).
2. Something that **can't be fixed after the merge**: a close keyword that would
   wrongly close an outside reporter's issue, or a finding that would cause data loss
   or a security hole the moment it's live.

When you pause, **never run `/process-review` or anything else yourself.** Flag the
PR (`python3 scripts/ops/stage_labels.py flag <n> question`), report what you found
and offer the options (fix on the branch, land anyway and fix on main, send it back
through `/process-review`), with your recommendation. The user decides.

Run locally on the Mac (iOS verification needs Xcode). `gh` must run with the
sandbox disabled, otherwise it returns empty with exit 0. Run heavy jobs (xcodebuild, full pytest) one
at a time, never concurrently.

## Inputs

- `<pr-number>` — required; if missing, use the PR for the current branch, else ask
  and stop.

## Step 1 — Pre-merge checks

Mechanical checks first; only the cases marked **pause** stop the merge:

1. **State:** open, not draft, `mergeStateStatus` not `DIRTY` (conflicts).
   `gh pr view <n> --json state,isDraft,mergeStateStatus,headRefOid,baseRefName,body,commits`
2. **CI on the head commit:** `gh pr checks <n>`. Pending → wait for it (poll in the
   background, ~10 min max), never call a pending run green. A failing check → **pause**
   only if it's a build/test failure in the PR's own code; a flaky or unrelated job
   can be noted and landed.
3. **Review bot:** read **every** round's comment by `claude` whose first line
   contains "Code Review", not just the last. If there's no review on the head commit,
   note it and carry on. The user chose to land.
4. **Triage every finding** still open across all rounds into "fix on main",
   "issue (needs design)" or "skip (why)". **Pause** only for the narrow case above,
   data loss or security on going live. Regular bugs are fixed on main.
5. **Close keyword.** For each linked issue, check the author
   (`gh issue view <N> --json author`). Issues filed by the user (`roznet`) → `Closes #N`.
   Issues from an **outside reporter** → must be `Addresses #N`, in the PR body
   **and** every commit body, because GitHub acts on the commit:
   `git log origin/<base>..<head> --format=%B | grep -inE "close[sd]?|fixe?s?|resolve[sd]?"`.
   A stray `closes #N` for an outside reporter → **pause** (merging would close their
   issue before they can use the fix, and that can't be undone quietly).

## Step 2 — iOS verification (only if the PR touches `app/`)

1. **What CI already proved:** if the iOS workflow ran on the head commit and
   passed, the app **compiles** and the **unit** target passed — don't redo that.
   Rebuild locally only if CI didn't run it or it failed.
2. **UI journeys (CI doesn't run these).** Check the head out in a throwaway
   worktree (`git worktree add ../land-<n> <head-sha>`); never switch `main/`.
   Find the XCUI tests that exercise the changed views (grep
   `flyfun-weatherUITests` for the touched view names and accessibility IDs) and run
   them by **type** name with `-only-testing:flyfun-weatherUITests/<Type>`. Read the
   count back from the `.xcresult` (`xcrun xcresulttool get test-results summary`):
   a filter that matches nothing still prints `TEST SUCCEEDED`. If no UI test covers
   the change, say so.
3. **Human-only checks.** List the specific screens and states the user should look
   at in the simulator (layout, iPhone vs iPad, landscape, dark mode) — only the ones
   this diff could affect. Screenshots via an XCUI attachment are a bonus, not a
   substitute.
4. A failing build or test → **pause**: report the failure (error excerpt, which
   test) and offer the options, with your recommendation. Usually that's a fix pushed
   to the branch, since the merge would otherwise break main.
5. Remove the throwaway worktree when done.

## Step 3 — Merge

`gh pr merge <n> --rebase --delete-branch`. If the rebase doesn't apply, fall back
to `--merge` and say so. Confirm that the linked issues closed (or deliberately did
not, for `Addresses`).

## Step 4 — Finish it on main

- Sync a **clean** checkout of `main` to `origin/main`. If `main/` has someone else's
  uncommitted changes, don't touch it — work from a clean checkout. Re-check the
  branch before committing.
- Apply **all** the "fix on main" findings, plus any small "Follow-ups noticed" from the
  brief. Anything that genuinely needs design thought gets a
  **detailed issue** (root cause, call sites, acceptance criteria) for an
  implementation agent, not an in-session fix. Such an issue is born ready:
  `gh issue create --label to-start …` (a vaguer follow-up gets no label and the
  Action marks it `to-plan`; `designs/stage-labels.md`).
- Run the targeted tests for what you touched (web: `npx tsc --noEmit` / vitest in
  `web/`; Python: targeted pytest). **Swift:** build, unit tests, and the affected UI
  journey, on this Mac before pushing.
- One commit, `Follow-up to #<pr>: …`, specific paths only, attribution trailer.
  Push.

## Step 5 — Bookkeeping

- **Memory:** grep the memory dir for the issue/PR numbers. Any note that still
  calls this work pending, open, unmerged or "approved, not started" → update it to
  the landed state, or delete it if it's now only history.
- **Design docs (safety net):** `/implement-issue` should have updated the docs in
  the PR. Check the diff did. If it didn't, or your on-main fixes changed documented
  behaviour or choices, update the affected docs in the follow-up commit, scoped to
  what changed (locally `/sync-designs <doc path>`, never a full sync). Also fix any
  `designs/future/` status line the work made stale.
- **Local worktree** for the PR branch: if it's clean and merged, remove it
  (`git worktree remove`); if it's dirty, leave it and say so.

## Step 6 — Landing summary

Final message, **≤ ~15 lines**, plain words. A condensed version of the PR's Owner's
brief, updated with what actually happened here:

```
## Landed #<pr> — <title>   (closes #N)

**What it does:** <one line, pilot/owner terms>
**For the pilot:** <visible change, or "nothing visible">
**Fixed on main after merge:** <commit sha — what; or "none">
**Verified:** CI <green on sha> · iOS unit <CI / local> · UI journeys <which, N passed | none cover it>
**Your simulator check:** <specific screens/states, or "none needed">
**Deploy will need:** <migration / env var + container recreate / flag / prod command / app release — or "nothing special">
**User-facing (What's New candidate):** yes — <one-line pilot wording> | no
**Left open:** <issues opened, deferred findings, decisions still pending — or "none">
```

**Never deploy and never start `/deploy`.** Deploying is the user's separate,
confirmed step.
