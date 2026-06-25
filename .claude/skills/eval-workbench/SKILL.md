---
name: eval-workbench
description: Build & maintain the LLM-digest eval SET (corpus) — pull prod briefings, attach pilot debriefs, re-run advisories against saved baselines, promote staging→corpus. Distinct from the `eval-digest` skill (which replays contexts through the LLM).
---

# Eval Workbench operations

Operational recipes for the dev-only eval-set workbench (#252/#254/#255). For the
architecture see `designs/eval-digest-workbench.md`; for the LLM replay eval see the
`eval-digest` skill. **All of this is dev-only** — gated by `WEATHERBRIEF_EVAL_WORKBENCH`,
never in prod.

## Model (read first)

- **Two areas**, both under the separate **eval-set git repo** `~/Developer/public/flyfun-weather-evalset`:
  - `corpus/` — committed, curated. `EVAL_CORPUS_DIR` points HERE.
  - `staging/` — gitignored scratch where new briefings land; triage/label, then **promote** to corpus.
  - `.env` (dev) has `EVAL_CORPUS_DIR=.../flyfun-weather-evalset/corpus` + `WEATHERBRIEF_EVAL_WORKBENCH=1`. Staging derives as the `staging` sibling (or `EVAL_STAGING_DIR`).
- **Pack shape (Option B):** the committed master is `cross_section.json.gz` (gzip ~17×; read transparently). The **derived tier is gitignored + regenerated**: `route_analyses.json`, `elevation_profile.json`, `route_points.json`, `sounding_profiles.json.gz`, `skewt/`, plain `cross_section.json`.
- **`cross_section.json` is the irreplaceable RAW master** — it holds the per-interpolated-point soundings (~5× more points than `forecasts.json`, which is waypoints-only). It CANNOT be regenerated from forecasts, and a past forecast can't be re-fetched. `route_analyses.json` IS regenerable from it (`run_analysis`, ~3.5s/pack).
- **`full_fidelity`** = `route_analyses.json` present (cross-section view + rerun work). Else "label-only" (judge from advisories+digest only).
- UI: `/eval.html` (gated, admin) lists packs with `[Staging|Corpus]` toggle, priority, flight date, debrief badge; the label panel docks on `/briefing.html?flight=eval-<id>` with golden G/A/R + priority + Promote + Re-run advisories + Debrief toggle.

```bash
source venv/bin/activate   # always, from main/
```

## Recipe: pull complete packs from prod

Local `data/packs` is often a partial sync (no heavy files); **prod keeps full packs**. Prod: `ssh brice@161.35.35.15`, data at `/mnt/flyfun_data/weather/data/packs`.

```bash
# Complete packs older than N days (prod keeps these past T1 only for DEBRIEFED flights):
python scripts/pull_prod_complete_packs.py --min-age-days 30 [--dry-run]
# Or an explicit list of prod tails (<user>/<flight>/<ts>), e.g. the debriefed set:
python scripts/pull_prod_complete_packs.py --tails-file tails.txt
# Re-pull only the MISSING heavy artifacts for packs already in staging:
python scripts/sync_eval_from_prod.py --area staging [--manifest eval_candidates.json] [--dry-run]
```
Both rsync the heavy tier and gzip the cross-section on ingest. Pack→source mapping is the gitignored `_source.json` breadcrumb (flight id = its flight-dir segment).

## Recipe: attach pilot debriefs (ground truth)

Prod `flight_debriefs`: `decision` (flown|cancelled|monitoring), `reasons_json` (cancel tags), `outcomes_json` (per-category `consistent|better|worse` — the forecast-vs-reality verdict), `note`.

```bash
python scripts/pull_debrief_data.py          # ssh-dumps prod debriefs, attaches debrief.json + flags
```
Sets `corpus_meta.debriefed`/`debrief_decision`/`debrief_graded` (graded = has reasons or outcomes).
Dump prod debriefs manually (engine raw SQL — SessionLocal needs binding):
```bash
ssh brice@161.35.35.15 'cd flyfun-weather && docker compose exec -T weatherbrief python -c "
import json; from flyfun_common.db import get_engine; from sqlalchemy import text
print(json.dumps([dict(r) for r in get_engine().connect().execute(text(\"SELECT * FROM flight_debriefs\")).mappings()]))"'
```

## Recipe: re-run advisories vs the saved baseline (NON-destructive)

Confirm a code change's effect on real briefings. Reads saved data, recomputes, diffs vs saved `route_advisories.json`; **never overwrites** (`persist=False`).

```bash
# Shallow — picks up ADVISORY-EVALUATOR changes (analysis/advisories/*.py):
python scripts/rerun_advisories_diff.py --area staging [--id <corpus_id>]
# DEEP — also recomputes SOUNDINGS first; REQUIRED for sounding/analysis-layer
# changes (e.g. assess_convective_thermo). A shallow re-grade reads the SAVED
# (old) route_analyses and shows NO diff for these.
python scripts/rerun_advisories_diff.py --area staging --deep
# Target a debrief cohort (e.g. the convective over-warn set):
python scripts/rerun_advisories_diff.py --area staging --deep --debrief-outcome TS=better
```
Read the `convective` / `convective_character` rows. Caveat: the diff is a change-detector (uses default params, skips DB airport-conditions recompute → those rows are noise/flagged); the saved-vs-rerun *detail headline* can also drift from unrelated code evolution — the aggregate-status diff is the clean signal. Needs `cross_section` (+ `route_points` for `--deep`); stale label-only packs error cleanly.

## Recipe: regenerate the derived cache (after a fresh clone)

The committed corpus has only `cross_section.json.gz`; rebuild the gitignored derived tier:
```bash
python scripts/rehydrate_eval_cache.py --area corpus     # run_analysis from cross_section, ~3.5s/pack
```

## Recipe: build/refresh corpus from local packs; promote

```bash
# Select coverage-diverse candidates → manifest → ingest into staging (default area):
python scripts/export_eval_candidates.py --from data/packs --limit 50
python scripts/pull_eval_corpus.py --manifest eval_candidates.json   # --area staging default
```
**Promote** staging→corpus: the `/eval.html` Promote button (or `corpus.promote(id)`) — a directory MOVE, gated on a golden label, auto-gzips the cross-section. **git stays manual**: commit in the eval-set repo when the shape is settled (`git add corpus/ && commit && push`; LFS handles the `.gz`).

## Gotchas

- Restart the dev server after `.env` changes (it `load_dotenv()`s at startup; `--reload` only catches `.py`).
- Editable-install: the venv imports `weatherbrief` from `main/src` — verify with `python -c "import weatherbrief,inspect; print(inspect.getfile(weatherbrief))"` if a change "isn't taking".
- Debriefed flights are the high-value ground truth (`outcomes` = per-category forecast-vs-reality). Prod retention is NOT a clean debriefed proxy — use the `flight_debriefs` table.
