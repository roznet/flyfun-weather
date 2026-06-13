---
description: Watch a PR for the code-review bot's comment, then triage and act on it (fix blockers & push, or recommend merge + fix-on-main)
argument-hint: "[PR number] (default: PR for current branch)"
allowed-tools: Bash(gh:*), Bash(git:*), Bash(npm:*), Bash(pytest:*), Bash(source:*), Read, Edit, Write, Grep, Glob
---

# Process bot review

Watch the target PR for the code-review bot's review, then triage and act on it.

## Target PR

- If `$ARGUMENTS` is a number, that is the PR.
- Otherwise resolve the PR for the current branch:
  `gh pr view --json number,headRefName,headRefOid,url`
- If there is no PR for the current branch, stop and tell me.

## The review bot

The reviewer is the GitHub user **`claude`**, driven by a **GitHub Action that fires automatically on every push** to the PR. It posts a single **top-level PR comment** (not inline threads) whose body starts with `## Code Review` and groups findings under **Critical**, **Important**, and **Minor** headings. A clean review posts a brief approval comment with no findings.

**Cost model — every push = one full review round.** Because the Action re-reviews on each push, an extra push is not free. Two rules follow:
1. **Only push when there's a real blocker to fix.** If the findings are cosmetic-only, do *not* push — merge and fix on main, so you don't trigger (and wait on) another round.
2. **If you're pushing anyway, batch in everything worth doing.** Once a blocker forces a push, the round is already paid for — so apply *every* fix you judge worth doing in that same push, cosmetic ones included. Don't defer a worthwhile fix to main when you're already pushing. (Deferral is only for the no-blocker case in rule 1.)

Never manually re-trigger the bot; pushing is what triggers it.

## Step 1 — Wait for the review (watch)

> **Watch = polling.** There is no passive "subscribe" available to a local agent — GitHub offers no push/long-poll channel to `gh` for new comments (the only event-driven path is a GitHub Action on `issue_comment`/`pull_request_review`, which runs in CI, not here). So poll, but poll *efficiently*: sleep-and-recheck via the harness scheduler (ScheduleWakeup) or a background Bash loop, **not** a tight token-burning loop.

The review is "ready" when a comment by `claude` starting with `## Code Review` exists that was created **after the latest commit on the PR branch**. This is a PR-branch-freshness check (so we don't act on a stale review from a previous round) — it is *not* about whether `main` has moved; that's handled separately in Step 2.

1. Capture the head commit date:
   `gh pr view <num> --json commits --jq '.commits[-1].committedDate'`
2. Poll for the review comment, latest-wins. Note: `gh --jq` does **not** accept `--arg`, so pipe to standalone `jq`:
   ```
   gh pr view <num> --json comments | jq -r --arg t "<head-date>" \
     '[.comments[] | select(.author.login=="claude" and .createdAt > $t and (.body|startswith("## Code Review")))] | last | (.body // "NO_REVIEW_YET")'
   ```
3. Poll about every 60s. **Do not block on a foreground `sleep`** (the harness blocks it) — run the poll as a background Bash loop that exits 0 once the comment is found, or use ScheduleWakeup to re-check. Give up after ~20 min and report that no review appeared (the bot may not have run — tell me so I can trigger it).

## Step 2 — Check for main divergence (before fixing)

Before making any blocker fixes, check whether the PR branch is behind `main`:
`gh pr view <num> --json mergeStateStatus,baseRefName` and/or `git fetch origin main && git rev-list --left-right --count origin/main...HEAD`.

If `main` has moved ahead, **warn me and offer to rebase the PR branch onto `main` first** (I prefer `--rebase`) so fixes land on top of current main and don't reintroduce resolved conflicts. **Do not auto-rebase** — wait for my go-ahead, since a rebase can need conflict resolution. If the branch is up to date, proceed.

## Step 3 — Triage

Read the full review body. Classify every finding into one of three buckets. **Be strict about what counts as a blocker** (per my standing preference): only correctness/logic/security/data-loss issues and clear design-intent violations block a merge.

- **Blocker** — bug, logic error, security issue, data corruption, broken behavior, or a real architecture/design-doc violation. Anything the bot put under **Critical**, and **Important** findings that are genuinely about correctness.
- **Cosmetic / non-blocking** — naming, comments, micro-optimizations, style, "consider…" suggestions with no behavioral impact. Most **Minor** findings.
- **Unsure** — you cannot confidently tell whether it changes behavior, or fixing it on the PR vs. on main is a judgment call.

Before acting, post a short triage summary to me: a one-line-per-finding table with bucket + your reasoning. Then proceed per the rules below.

## Step 4 — Act

**Blockers → fix and push to the PR branch.**
- Make the fixes. Reuse existing helpers; check sibling modules if the bot flagged a pattern that repeats (cross-metric/cross-model).
- Sanity-check before pushing: run the relevant tests / `npm run build` if frontend, and re-read the finding to confirm the fix actually addresses it.
- Stage **specific paths** (never `git add -A` — it pulls in `.claude` runtime files). Commit with a message referencing the finding, and end the commit body with the standard `Co-Authored-By` trailer.
- Push to the PR branch. Then post a reply comment on the PR summarizing what was fixed and what was deferred (audit trail), so the next review round has context.

**Cosmetic / non-blocking only → recommend merge + fix-on-main.**
- Do **not** push to the PR for these — a push would trigger another full review-Action round for no real gain. Tell me they're non-blocking and recommend merging (I prefer `--rebase` for clean history), then folding the cosmetic fixes into a small direct-to-main commit afterward.
- List the deferred items concretely so they're not lost — offer to either (a) apply them as a direct commit on main after merge, or (b) open a tracking issue.

**Unsure → ask me.**
- Present the specific finding and ask: fix on the PR, or merge and fix on main? Don't guess when guessing is costlier than the round-trip.

**Mixed (blockers + cosmetic)** → you're pushing for the blockers anyway, so **batch in every cosmetic fix worth doing** in the same push (the review round is already paid for). Only skip a cosmetic item if it's genuinely not worth doing at all — not because it's "merely cosmetic." Don't split worthwhile fixes across the PR and main when one push covers both.

**Clean review (no findings)** → confirm CI checks are green (`gh pr checks <num>`), then tell me it's ready and recommend merge (`--rebase`). Don't auto-merge.

## Notes / guardrails

- **Never merge automatically** — always leave the merge to me. You may run the merge only if I explicitly say so.
- After pushing blocker fixes, the review Action fires automatically and posts a fresh round — **do not re-trigger it.** Loop back to Step 1 to pick up that next review, but **cap it at 2 rounds** — after that, summarize remaining findings and hand back to me rather than iterating indefinitely.
- If any flagged issue traces to an externally-reported GitHub issue, remember the `Addresses #N` convention (in PR body and commit) so the deploy skill can close it.
- Keep me in the loop with concise narration: what the bot found, your triage, what you pushed, what you deferred.
