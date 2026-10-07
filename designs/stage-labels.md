# Stage labels

> Where the owner looks, the label says what the item needs from them.

Status: in use since 2026-10. Code: `scripts/ops/stage_labels.py`, workflow
`.github/workflows/stage-labels.yml`, one step at the end of
`.github/workflows/claude-code-review.yml`. Callers: the `implement-issue`,
`process-review` and `land-pr` skills.

## Why

The owner runs many issues at once and reads the GitHub lists on a phone. The
question when opening a list is "what needs me", not "where is this in a
process". So each label is a queue named after the owner's next action, and a
label lives on the object the owner is looking at for that stage: the issue
until a PR exists, the PR from then on. Merging closes both, so nothing is ever
cleared by hand. Deploy is not tracked here: it is a global action over
`prod..origin/main`, which `/deploy` already shows.

Labels are never maintained by hand. Every transition is either mechanical (a
GitHub Action on an event GitHub already emits) or made by the skill that is at
that step anyway.

## The labels

Issues carry exactly one of:

| Label | Means | Owner's action |
|---|---|---|
| `to-plan` | An idea, no agreed plan | A planning conversation in the thread |
| `to-start` | Plan agreed in the thread, nobody started | `/implement-issue n` |
| `implementing` | A PR exists | Nothing here, look at the PR list |

PRs carry exactly one of:

| Label | Means | Owner's action |
|---|---|---|
| `working` | Agent on it, or a bot review pending on the head commit | Nothing |
| `to-review` | Fresh bot review and brief on the current head | Read, then `/process-review`, `/land-pr`, or comment |
| `to-land` | Review loop concluded clean | `/land-pr n` |

Flags, zero or more, on either:

| Label | Owner's action |
|---|---|
| `mac` | On the Mac, run the "Pick up on a Mac" checklist in the PR body |
| `question` | Answer in the thread, an agent could not proceed |
| `blocked` | Nothing, waiting on something external |

Queries: `is:issue is:open -label:implementing` is everything that needs the
owner on the issue side; `is:pr is:open label:to-review`, `label:to-land`,
`label:mac`, `label:question` are the PR queues.

## Transitions

Mechanical, in `stage-labels.yml` via `stage_labels.py event`:

| Event | Effect |
|---|---|
| Issue opened | `to-plan`, unless created already staged |
| Owner comments exactly `ready` on an issue | `to-start` (phone fallback for planning done elsewhere) |
| PR opened or ready for review, not draft | PR `to-review`; referenced issues `implementing` |
| PR opened as draft, converted to draft, or pushed | PR `working`; referenced issues `implementing` |
| PR touches `app/` | flag `mac` (CI never runs the UI journeys, so every iOS PR has Mac work) |
| PR closed unmerged | referenced open issues back to `to-start` |
| PR merged | referenced issues still open (`Addresses #n`, outside reporter) lose `implementing` |
| Review bot posts (end of `claude-code-review.yml`) | PR `to-review` |

Push then review gives `working` then `to-review`, so `to-review` means
exactly "a review exists on this head", the freshness check `process-review`
used to do by hand.

Agent-set, each in the skill that is at that step:

| Label | Set by | Cleared by |
|---|---|---|
| `to-start` | The agent that posts an agreed plan to the issue; `land-pr` when it opens a follow-up issue (born with root cause and acceptance criteria) | `implementing` |
| `implementing` (early) | `implement-issue` Step 2 at branch time, so a second session does not start the same issue | the Action on PR close |
| `to-land` | `process-review` when triage finds no blocker or its fix round comes back clean | merge |
| `question` | `process-review` unsure bucket; `implement-issue` or `land-pr` pausing for the owner | the skill that reads the answer |
| `blocked` | On request | On request |
| `mac` (non-iOS) | `implement-issue` Step 5 when the Mac checklist is non-empty for another reason (GRIB or prod-data tests) | merge |

The one judgement call is `to-plan` vs `to-start` when an agent creates an
issue: `to-start` if another agent could implement from the thread alone
(root cause, call sites, acceptance criteria); otherwise `to-plan`. Posting an
agreed plan to an existing issue flips it to `to-start`.

## Referenced issues

A PR references an issue through a close keyword in its body (`Closes`,
`Fixes`, `Resolves`, `Addresses` + `#n`) or a branch named `issue-N-*` or
`N-*`. A bare `#n` mention in the title or body does not count: titles cite
related issues too often.

## Reconciliation

`stage_labels.py sync` recomputes every open issue and PR from state (draft,
head commit date, bot comments, changed files, referencing PRs) and applies the
difference; `sync --dry-run` only reports it. It never flips `to-plan` to
`to-start` or back, and keeps `to-land` while the head is unchanged, since
those are judgements. `status` prints the in-flight view grouped by label plus
the inconsistencies: open items with no stage, `implementing` with no open
PR, PRs that reference no issue, and whatever `sync` would change. Run it
when the lists look wrong, or after a session died mid-flight.

## Gotchas

- Labels are one namespace for issues and PRs. The two stage families are
  disjoint so a label never means two things; the flags share meaning.
- `GITHUB_TOKEN` label edits do not trigger other workflows, so there is no
  recursion to guard against, and nothing listens to `labeled`.
- The PR-closed handler re-reads each referenced issue: GitHub closes
  `Closes #n` issues in the same instant as the merge, and a closed issue is
  left alone.
- `gh api --paginate --slurp` needs gh 2.49+.
- Only this script touches stage labels. A skill that runs `gh issue edit
  --add-label` directly would break one-stage-at-a-time.
