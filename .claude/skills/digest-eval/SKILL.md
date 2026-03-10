---
name: digest-eval
description: Run LLM digest eval — replay saved weather contexts through the LLM and compare assessments
---

# Digest Eval

Evaluate the LLM weather digest pipeline against saved fixtures. Each fixture contains a real context string (the exact user message sent to the LLM) and the original digest output for comparison.

## Setup

```bash
source venv/bin/activate
```

## Workflow

### 1. Re-extract fixtures (if packs or context assembly changed)

```bash
python scripts/extract_digest_eval.py --dedupe --prune
```

This reads `data/packs/`, builds context strings via `build_digest_context()`, and saves compact fixtures to `tests/eval_data/digests/`. The `--prune` flag keeps ~30 fixtures (endpoints + assessment transitions, no redundant same-route/same-assessment repeats).

### 2. Dry run — review what will be tested

```bash
python scripts/run_digest_eval.py --dry-run
```

Shows all fixtures with their assessment, advisory counts, and context size. No LLM calls.

### 3. Run the eval

```bash
# Full run (all fixtures, ~30 LLM calls)
python scripts/run_digest_eval.py --output eval_results.json

# Subset runs
python scripts/run_digest_eval.py --limit 3                    # first N only
python scripts/run_digest_eval.py --filter "egtf_lfat"         # by fixture ID
python scripts/run_digest_eval.py --assessment AMBER            # by original assessment

# With alternative prompt
python scripts/run_digest_eval.py --prompt configs/weather_digest/prompts/briefer_v2.md --output eval_v2.json
```

### 4. Inspect a specific fixture

```bash
python scripts/run_digest_eval.py --show <fixture_id>
```

Prints the full context string, original assessment, and advisory breakdown. No LLM call.

### 5. Compare results

After a run, load the results JSON and compare old vs new assessments. Key metrics:
- **Changed count**: how many assessments shifted
- **Distribution**: GREEN/AMBER/RED balance (watch for AMBER over-cautiousness)
- **Direction**: `v` = downgraded (e.g. AMBER→GREEN), `^` = upgraded (e.g. GREEN→AMBER)

## Key files

| File | Purpose |
|------|---------|
| `scripts/extract_digest_eval.py` | Extracts fixtures from `data/packs/` |
| `scripts/run_digest_eval.py` | Replays fixtures through LLM |
| `tests/eval_data/digests/` | Fixture directory (context.txt + digest.json + meta.json per fixture) |
| `configs/weather_digest/prompts/briefer_v1.md` | Current system prompt |
| `src/weatherbrief/digest/prompt_builder.py` | Context string assembly |
| `src/weatherbrief/digest/llm_digest.py` | WeatherDigest model + LangGraph pipeline |

## AMBER bias investigation

Known issue: ~70% of digests are AMBER. Suspects to check:
- Fixtures with **all-green advisories but AMBER assessment** — LLM is being overcautious
- Check if `assessment_reason` cites model uncertainty or minor concerns that shouldn't override green advisories
- The prompt calibration section in `briefer_v1.md` controls GREEN/AMBER/RED thresholds
