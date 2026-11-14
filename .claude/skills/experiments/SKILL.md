---
name: experiments
description: Status of the running experiments and standing data checks (ICON-CH1 logger, derived visibility, verification cycles, ...) from the registry in scripts/ops/experiments.toml, as one short table with decision dates. Use when the user asks "how are the experiments doing", "where are we on CH1 / visibility", or wants to add or retire an experiment check.
---

# Experiments status

One place for "how is that experiment doing?". The registry
`scripts/ops/experiments.toml` lists each live experiment: where it runs, the
read-only commands that show its state, what good looks like, and when the owner
wants to decide. `scripts/ops/experiments.py` runs them. **Run the script; don't
rebuild the ssh or SQL by hand.** Hand-built status checks cost millions of tokens
per question; the script costs one command.

Needs host access, so it runs on the Mac (it reads `deploy/hosts.json` through
`hosts.py`). In a cloud session every entry reports `unreachable`: say so in one
line and stop, don't guess.

## Run

```bash
python3 scripts/ops/experiments.py            # every entry
python3 scripts/ops/experiments.py <id> ...   # just these
python3 scripts/ops/experiments.py --list     # registry and decision dates only
```

Exit 0 all ran, 1 a command failed, 2 a host was unreachable (a `lan_only` node
away from home is "could not tell", not down).

## Report

One table, then stop:

| Experiment | State | Headline | Decision |
|---|---|---|---|
| plain-words name | on track / needs a look / failing / unreachable | the one number that matters, compared with "good looks like" | "decide in N d" or "OVERDUE: decide or remove" |

- Judge each entry against its `good` line, not your own idea of healthy.
- Lead with anything failing or overdue. For an overdue entry, put its `decision`
  question to the user.
- Don't dig further unless the user asks. If they do, start from the entry's
  `thread` and the relevant skill (e.g. `check-health` for the verification cycle).

## Add or retire an experiment

- **Add** when an experiment starts collecting data: a new `[id]` table with
  `title`, `host`, read-only `commands`, `good`, `decision`, `decide_by` and
  `thread`. Use `keep` regexes or `max_lines` so the output stays short. Only use
  `{PLACEHOLDERS}` that `hosts.py` exports for that host; the test
  `tests/test_ops_experiments.py` checks this.
- **Retire** once the decision is taken: delete the table in the same PR or commit
  that acts on the decision.
- Every command must be read-only. A check that needs to write is not a check:
  make it a separate script and a separate ask.
- Try a new entry once with `--dry-run`, then for real, before committing it.
