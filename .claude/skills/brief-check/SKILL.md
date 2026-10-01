---
name: brief-check
description: Independently re-derive the Owner's brief for a PR from its diff and issue, compare it with the implementing agent's brief, and post the delta (missed / understated / overstated) as a PR comment. On demand, for PRs touching meteorology, data collection, user data or other high-risk areas. Invoke with the PR number.
disable-model-invocation: true
---

# Brief check

`/implement-issue` ends every PR with an **Owner's brief** — the implementing
agent's own account of what it did. Self-reports are honest but predictably miss
things outside what the agent set out to do. This skill is the second opinion:
write the brief again **from the diff alone**, then show the owner where the two
disagree. It is not a code review (that's the bot on every push) — the question
here is "does the owner have an accurate picture?", not "is the code correct?".

Read-only on code: no edits, commits or checkouts. The only write is one PR comment.

## Inputs

- `<pr-number>` — required. If missing, use the PR for the current branch
  (`gh pr view --json number`); if none, ask and stop.

Locally, `gh` must run with the sandbox disabled (else it returns empty, exit 0).

## Step 1 — Gather

1. `gh pr view <n> --comments` — find the agent's **Owner's brief** (PR body or a
   comment). If there is none, say so: you'll still write yours, and the delta
   section becomes "no brief to compare".
2. The linked issue(s) from `Closes #N`: `gh issue view <N> --comments`, full thread.
   **Check the premise:** confirm which issue the PR actually implements before
   assuming.
3. `gh pr diff <n>` — read all of it. Grep the base branch for surrounding code where
   needed to judge behaviour: callers, config defaults, env flags, where data is
   written, retention, schedules.
4. Head-commit status: `gh pr checks <n>` and any review-bot comment on the head
   commit (first line contains "Code Review"), noting which findings are unanswered.

## Step 2 — Write your own brief

Use the template and rules in `.claude/skills/implement-issue/SKILL.md` Step 6,
including its five-point checklist. Build it from the diff, **not** by editing
theirs. ≤ ~30 lines.

## Step 3 — The delta

At most ~12 bullets, most important first, each tagged:

- **MISSED** — a decision, failure mode, deploy step or departure from the issue
  that their brief doesn't mention.
- **UNDERSTATED** — mentioned, but the size or risk is bigger than they say (re-derive
  numbers yourself).
- **OVERSTATED** — a verification claim the repo can't back (CI pending on head,
  a live check that isn't a test, "compiles" with no build), or a risk smaller than
  stated. Good news counts too.
- **AGREE** — only where it's notable that they got a hard thing right.

Cite `file:line` from the diff. Be fair: if their brief is accurate, say so plainly —
a short delta is a good result.

## Step 4 — Verdict and post

One line: anything here that should block merge, or that must be settled before a
flag is turned on / a release ships? Usually "no blocker; X before enabling".

Post one comment with `gh pr comment <n> --body-file <file>`:

```
## Brief check (independent)

**Verdict:** <one line>

### Delta vs the agent's brief
- **MISSED:** …

<details><summary>Independent brief</summary>

<your brief>

</details>
```

The first line must not contain "Code Review" (that string is how `/process-review`
finds the review bot). Then print the verdict and delta in the terminal.
