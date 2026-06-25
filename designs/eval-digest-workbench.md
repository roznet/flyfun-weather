# Digest Eval Labelling Workbench

> Dev-only golden-labelling for the LLM digest eval — render the real briefing
> view for a curated corpus of pulled prod packs, and let an SME record golden
> GREEN/AMBER/RED labels (per guidance) in-view. Issue #254 (parent #252).

**Status (2026-06, later update):** runtime-verified and substantially extended
beyond this doc. Now in place: **two areas** (`staging` scratch + committed
`corpus`) with promote; a separate **eval-set git repo**
(`flyfun-weather-evalset`) as the corpus home; **Option B storage** (committed
`cross_section.json.gz` master, gitignored+regenerated derived tier);
**curation priority (1–4)**; **prod re-pull** of full packs + **pilot debrief**
ground truth (decision / cancel-reasons / per-category outcomes); and
**non-destructive advisory re-run + diff** (shallow re-grade or `--deep` sounding
recompute). For the operational recipes (commands for all of the above) see the
**`eval-workbench` skill** — that is the source of truth for *how to run* these;
this doc covers the architecture. (The original status note + verification
checklist below are historical.)

## Why

The eval (see the `eval-digest` skill) scores the LLM against *golden* labels —
the correct assessment an SME assigns, not what the model produced. Hand-
labelling from a terminal context dump is slow and anchors the labeller on the
model's text. This workbench labels **in the same visual briefing the pilot
sees**, blind-first, directing effort at coverage gaps and the AMBER-bias
suspects.

## Shape

```
PROD  /mnt/flyfun_data/weather/data/packs/<user>/<flight>/<ts>/   (full packs; debriefed flights kept past T1)
   │  pull_prod_complete_packs.py (by age or --tails-file)  /  sync_eval_from_prod.py (fill missing heavy files)
   │  pull_debrief_data.py  (attach pilot ground truth from flight_debriefs)
   ▼
EVAL-SET REPO  ~/Developer/public/flyfun-weather-evalset/
   ├─ staging/<corpus_id>/      ── gitignored scratch (triage/label here); EVAL_STAGING_DIR
   │     ├─ cross_section.json.gz  forecasts.json  route_advisories.json  digest.json   (committed tier)
   │     ├─ corpus_meta.json  label.json  debrief.json
   │     └─ route_analyses.json  elevation_profile.json  skewt/  …  (GITIGNORED, regenerable cache)
   │        promote (move, gated on golden label, auto-gzip)
   ▼
   └─ corpus/<corpus_id>/       ── committed curated set; EVAL_CORPUS_DIR points here
   │  /eval.html  →  [Staging|Corpus] toggle → /briefing.html?flight=eval-<corpus_id>  (standard view + panel)
   ▼
run_digest_eval.py (LLM replay, eval-digest skill)   +   rerun_advisories_diff.py (advisory regression, --deep)
```

Local `data/packs` may also seed the corpus (`export_eval_candidates.py` → `pull_eval_corpus.py`),
but it is often a *partial* sync (no heavy files) — prod is the authoritative source for full packs.

## Locations: staging vs corpus vs prod

| Where | Role | Git |
|-------|------|-----|
| **prod** `/mnt/flyfun_data/weather/data/packs` | authoritative full packs; debriefed flights survive T1 retention | — |
| **eval-repo `staging/`** | scratch triage: new pulls land here, label/prioritize, then promote | gitignored (whole dir) |
| **eval-repo `corpus/`** | curated, committed set (`EVAL_CORPUS_DIR`) | committed (see storage tiers) |

**Storage tiers (Option B).** Committed: `cross_section.json.gz` (the raw master, gzipped ~17×; read
transparently via `_read_json_or_gz`), `forecasts.json`, `route_advisories.json`, `digest.json`,
`digest_context.txt`, `corpus_meta.json`, `label.json`, `debrief.json`. Gitignored + regenerated:
`route_analyses.json`, `elevation_profile.json`, `route_points.json`, `sounding_profiles.json.gz`,
`skewt/`, plain `cross_section.json`. `cross_section.json` is the irreplaceable master — it holds the
per-interpolated-point soundings (forecasts.json is waypoints-only), so it cannot be regenerated from
forecasts; `route_analyses.json` IS regenerable from it (`rehydrate_eval_cache.py`, ~3.5s/pack).

## Key decisions

- **File-based corpus, rendered via a virtual-flight resolver** (not a per-
  endpoint `/eval` mirror, not synthetic DB rows). A corpus pack is opened as
  flight id `eval-<corpus_id>`; three core read paths resolve it from disk, so
  **every existing briefing endpoint serves it unchanged** with no DB rows.
- **Runtime-gated, never in prod.** The API router is mounted only when
  `WEATHERBRIEF_EVAL_WORKBENCH` is set (`api/app.py`), and the resolver guards
  are dead code when the flag is off. Endpoints also require `require_admin`.
- **Separate eval-set git repo, two areas.** The corpus lives in its own repo
  (`flyfun-weather-evalset`), not the code repo, so large packs don't bloat it.
  `staging/` (gitignored scratch) holds new pulls; `promote` moves a labelled
  pack into committed `corpus/`. Git commit stays a manual curator step.
- **Option B storage (committed master gzipped, derived regenerated).** Commit
  `cross_section.json.gz` + the small text tier; gitignore + regenerate the
  derived tier (see Storage tiers above). `git add -f` is no longer used.
- **Prod is the source for full packs; debrief = ground truth.** Pull full packs
  from prod (local `data/packs` is a partial sync); attach pilot `flight_debriefs`
  (decision / cancel-reasons / per-category `consistent|better|worse` outcomes)
  as the strongest eval ground truth.
- **Blind-first labelling.** The panel hides the model's assessment + synopsis
  (`#latest-assessment`, `[data-section="synopsis"]`) until you toggle blind off.

## The virtual-flight resolver (the linchpin)

`eval-<corpus_id>` flight ids short-circuit three chokepoints — **only** when
`eval_workbench_enabled() and is_eval_flight_id(flight_id)`:

| Hook | File | Returns |
|------|------|---------|
| `_load_flight_or_404` | `api/flights.py` | `resolve_eval_flight` → synthesized `Flight` |
| `load_pack_meta` | `storage/flights.py` | `resolve_eval_pack_meta` → `BriefingPackMeta` |
| `list_packs` | `storage/flights.py` | `resolve_eval_pack_list` → one-element list |

`_get_pack_dir` (`api/packs.py`) then resolves the directory from the synthesized
meta's `artifact_path`, which points at `$EVAL_CORPUS_DIR/<corpus_id>/`. The
`Flight`/`BriefingPackMeta` are built purely from `corpus_meta.json` — no DB.

`fetch_timestamp` in `corpus_meta.json` is date-precision and synthetic: the
corpus is keyed by `corpus_id` (one pack each), and the resolver ignores the ts
for disambiguation, so the value only needs to be a stable opaque identity that
the frontend echoes back in the pack URL.

## Module map

`src/weatherbrief/eval_workbench/` (import-light; resolver/config touch no heavy deps):

| Module | Role |
|--------|------|
| `config.py` | `eval_workbench_enabled()`, `eval_corpus_dir()`, `eval_staging_dir()`, `area_root()`, `AREAS`, the `eval-` namespace helpers |
| `situations.py` | shared pack-load / advisory-summary / `classify_situations` + `SITUATION_VOCAB` (also imported by `scripts/extract_digest_eval.py`) |
| `corpus.py` | `CorpusMeta` (+ debrief/priority fields), `CorpusLabel` (+ `priority`), `CorpusPack` (+ `area`); area-threaded list/load/save; `find_pack` (search both areas), `promote`, `coverage_report` |
| `resolver.py` | `synthesize_flight` / `synthesize_pack_meta(meta, area)`; hooks resolve via `find_pack` (serves staging or corpus) |
| `ingest.py` | `build_corpus_meta`, `ingest_pack(area=…)` (gzips cross_section via `compact_corpus_pack`, writes `_source.json`), `load_pack_context` |
| `candidates.py` | `candidate_reasons`, `base_score`, `select_candidates` (coverage-aware greedy) |
| `rerun.py` | non-destructive advisory re-run + diff vs saved: `rerun_diff(deep=…)`, `rerun_manifest[_deep]`, `diff_manifests` |

API: `src/weatherbrief/api/eval_workbench.py` — `GET /api/eval/packs?area=`,
`/coverage?area=`, `/packs/{id}`, `/packs/{id}/debrief`; `POST /packs/{id}/label`,
`/packs/{id}/promote`, `/packs/{id}/recalc-diff`. Mounted conditionally in `api/app.py`.
`/auth/me` carries `eval_workbench_enabled` so Settings shows a dev-only link.

Frontend: `web/eval.html` + `web/ts/eval/eval-main.ts` (list + coverage + `[Staging|Corpus]`
toggle, priority/flight-date/debrief columns, Promote button); `web/ts/eval/label-panel.ts`
(in-view panel: golden G/A/R, priority, Promote, Re-run advisories, Debrief toggle; lazy-imported by
`web/ts/briefing-main.ts` for `eval-` flights). esbuild `build:eval`/`dev:eval`.

Scripts (commands → **`eval-workbench` skill**): `export_eval_candidates.py`, `pull_eval_corpus.py`
(seed from local packs), `pull_prod_complete_packs.py` / `sync_eval_from_prod.py` (pull from prod),
`pull_debrief_data.py` (attach debriefs), `rerun_advisories_diff.py` (advisory regression, `--deep`),
`rehydrate_eval_cache.py` (rebuild derived cache).

## Scoring tie-in + bug fixed

`run_digest_eval.py` now resolves the expected label via `resolve_expected(meta)`
which prefers `meta["golden"]["assessments"]` (written by the interactive
`label_digest_eval.py`) and falls back to the legacy top-level
`expected_assessments` (synthetic fixtures). **Before this fix the runner only
read `expected_assessments`, so every label produced by the labelling CLI was
silently ignored at scoring time.**

For the corpus, golden lives in `label.json` (`CorpusLabel.assessments`). The
intended end-state is for `extract_digest_eval.py` to derive its compact
fixtures *from* the labelled corpus so the full-pack corpus is the single source
of truth — **not yet wired** (see next stages).

## Verification checklist (next agent)

Run in a worktree with a real `DATA_DIR` and a dev server:

1. `python3 -m venv venv && source venv/bin/activate && pip install -e ".[dev]"`.
2. `pytest tests/eval_workbench/ -q` — should pass (13 tests; pure logic).
3. Build a tiny corpus: `python scripts/pull_eval_corpus.py --from data/packs`
   (or via `export_eval_candidates.py` → manifest). Confirm
   `$EVAL_CORPUS_DIR/<id>/corpus_meta.json` written, payloads copied.
4. `WEATHERBRIEF_EVAL_WORKBENCH=1` + `/devserver`; open `/eval.html` — list +
   coverage render. Open a pack → `/briefing.html?flight=eval-<id>` should render
   the **full standard briefing** (cross-section, advisories, digest, skew-T) and
   the labelling panel should dock bottom-right, blind by default.
5. Save a label → confirm `label.json` written; reload shows it pre-selected.
6. `python scripts/run_digest_eval.py --guidance balanced --dry-run` picks up the
   golden (once the corpus→fixture wiring lands, step below).

## Open items / risks to validate

- **Edge endpoints on eval flights.** The 3 hooks cover the auto-load path
  (flight + packs + snapshot/advisories/digest). Endpoints doing extra DB work
  (`audit_pack_access` in `api/packs.py`, observations/advisory *recalc*,
  email/PDF) were not exercised; the digest/skew-T/chart GETs should be fine via
  `artifact_path`. Harden any that 500 on an `eval-` flight during step 4.
- **Core pack endpoints aren't admin-gated** (only `/api/eval/*` is). Acceptable
  because the whole feature is dev-only (flag off in prod), but note it.
- **Corpus→fixture wiring** (`extract_digest_eval.py` reading `label.json`) is
  the last milestone — make the labelled full-pack corpus the source of truth
  for the compact eval fixtures, retiring the separate `label_digest_eval.py`
  flow (or pointing it at the corpus).
- **`trend` coverage** (#254 acceptance): pull a few packs carrying a
  `previous_digest` (the `previous_digest` situation tag) so `trend` is
  exercised, and label them.

## References

- Eval running + AMBER-bias notes: `eval-digest` skill (`.claude/skills/eval-digest/`)
- Digest pipeline + guidance presets: [digest.md](./digest.md)
- Pack storage / serving: `storage/flights.py`, `api/packs.py`
- Issue #254 (Phase 2 — situation-coverage sampling + golden labels)
