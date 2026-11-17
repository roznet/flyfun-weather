---
name: eval-digest
description: Run LLM digest eval — replay saved weather contexts through the LLM and compare assessments
---

# Digest Eval

Evaluate the LLM weather digest against the labelled eval set. Each entry is a prod pack with its byte-faithful `digest_context.txt` (the exact user message the briefer saw) and an SME golden label per guidance preset; the eval replays the context and scores the new GREEN/AMBER/RED against that label.

> Building or maintaining the eval **set** itself — pulling prod briefings, attaching pilot debriefs, re-running advisories vs saved baselines, promoting staging→corpus — is the **`eval-workbench`** skill. This one is only the LLM replay.

## Setup

```bash
source venv/bin/activate   # .env must set EVAL_CORPUS_DIR (see eval-workbench)
```

## Workflow

### 1. Dry run — review what will be scored

```bash
python scripts/run_digest_eval.py --dry-run
```

Lists what would run and what is excluded from scoring (long-range entries, entries whose context isn't pulled yet, unlabelled entries). No LLM calls. Missing contexts come back with `scripts/pull_eval_corpus.py`.

### 2. Run the eval

```bash
# Corpus, every labelled guidance preset
python scripts/run_digest_eval.py --output eval_results.json

# Subsets
python scripts/run_digest_eval.py --guidance balanced           # one preset (balanced / conservative / tolerant)
python scripts/run_digest_eval.py --area both                   # corpus + staging
python scripts/run_digest_eval.py --filter egtf_lfat --limit 3  # by corpus_id
python scripts/run_digest_eval.py --priority 1                  # SME triage priority
python scripts/run_digest_eval.py --situation icing             # situation tag

# Candidate prompt or config, with drift against a previous run
python scripts/run_digest_eval.py --prompt configs/weather_digest/prompts/<candidate>.md \
  --baseline eval_results.json --output eval_candidate.json
```

### 3. Inspect one entry

```bash
python scripts/run_digest_eval.py --show <corpus_id>
```

Prints the full context string. No LLM call.

### 4. Compare results

After a run, load the results JSON and compare old vs new assessments. Key metrics:
- **Changed count**: how many assessments shifted
- **Distribution**: GREEN/AMBER/RED balance (watch for AMBER over-cautiousness)
- **Direction**: `v` = downgraded (e.g. AMBER→GREEN), `^` = upgraded (e.g. GREEN→AMBER)

### 5. Model / prompt A/B on recent prod briefings (#717)

The labelled corpus is small and ages; a candidate model or prompt also needs
checking against what pilots see today. Each recent prod pack stores the
byte-faithful `digest_context.txt` AND the digest prod showed, so only the
candidate needs running:

```bash
# on the droplet (see the script docstring for the scp/docker cp recipe)
python export_digest_replay.py --thumbs-since <current prompt's go-live date> --last-hours 24
# locally
python scripts/replay_prod_digests.py <export_dir> --config <candidate> --output replay.json
```

Valid only while the stored digests were written by the prompt/model you compare
against — keep `--thumbs-since` at or after the current briefer prompt's
go-live. Both runners print cost (USD), tokens and timing per call and in a
summary; `--reconstruct-missing` lets `run_digest_eval.py` rebuild contexts for
old packs (tagged `reconstructed`, no advisories when the pack kept none —
report them apart). `sonnet46` is the pre-#717 production config for baselines.

## Key files

| File | Purpose |
|------|---------|
| `scripts/run_digest_eval.py` | Replays the eval-set corpus through the LLM and scores against golden labels |
| `scripts/export_digest_replay.py` / `scripts/replay_prod_digests.py` | Prod-pack A/B export (droplet) / replay (local) |
| `configs/weather_digest/default.json` | Production config: model and briefer prompt (`prompts/briefer_v6.md` at the time of writing) |
| `src/weatherbrief/digest/prompt_builder.py` | Context string assembly |
| `src/weatherbrief/digest/llm_digest.py` | WeatherDigest model + LangGraph pipeline |

## Golden labels + the labelling workbench (#254)

The eval scores against **golden** labels (the SME's correct assessment), not
the model's own output. Label in the visual workbench:

- **Visual workbench** — render the real briefing view for a corpus
  of pulled prod packs and label in-view, blind-first. See
  `designs/eval-digest-workbench.md`. Flow: `scripts/export_eval_candidates.py`
  → copy to dev → `scripts/pull_eval_corpus.py` → `WEATHERBRIEF_EVAL_WORKBENCH=1`
  + `/devserver` → open `/eval.html`. Golden lives in each corpus pack's
  `label.json`.

`run_digest_eval.py --guidance <preset>` scores the model against the golden
label for that guidance (`resolve_expected` prefers the golden over the model's
original assessment).

## AMBER bias investigation

Known issue: ~70% of digests are AMBER. Suspects to check:
- Fixtures with **all-green advisories but AMBER assessment** — LLM is being overcautious
- Check if `assessment_reason` cites model uncertainty or minor concerns that shouldn't override green advisories
- The prompt calibration section in the production briefer prompt (named in `configs/weather_digest/default.json`) controls GREEN/AMBER/RED thresholds
