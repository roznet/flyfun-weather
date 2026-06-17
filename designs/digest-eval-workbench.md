# Digest Eval Labelling Workbench

> Dev-only golden-labelling for the LLM digest eval — render the real briefing
> view for a curated corpus of pulled prod packs, and let an SME record golden
> GREEN/AMBER/RED labels (per guidance) in-view. Issue #254 (parent #252).

**Status (2026-06):** backend + scripts + unit tests **implemented and tested**
in a minimal venv; frontend **written and esbuild-bundle-verified but not yet
runtime-tested** (the build container had no venv/`.env`/packs). See
[Verification checklist](#verification-checklist-next-agent) — finish in a
worktree with a real `DATA_DIR`.

## Why

The eval (see the `digest-eval` skill) scores the LLM against *golden* labels —
the correct assessment an SME assigns, not what the model produced. Hand-
labelling from a terminal context dump is slow and anchors the labeller on the
model's text. This workbench labels **in the same visual briefing the pilot
sees**, blind-first, directing effort at coverage gaps and the AMBER-bias
suspects.

## Shape

```
prod packs (DATA_DIR/packs)
   │  scripts/export_eval_candidates.py   ── coverage-aware candidate manifest
   ▼
eval_candidates.json  +  referenced pack dirs   ── copied to dev (rsync/scp)
   │  scripts/pull_eval_corpus.py
   ▼
$EVAL_CORPUS_DIR/<corpus_id>/                    ── corpus pack (payload gitignored)
   ├─ briefing.json, forecasts.json, route_advisories.json, digest.json, …
   ├─ corpus_meta.json   (committed — anonymized descriptor)
   └─ label.json         (committed — the SME's golden label)
   │  /eval.html  →  /flight.html?id=eval-<corpus_id>  (standard view + panel)
   ▼
run_digest_eval.py --guidance <preset>           ── scores model vs golden
```

## Key decisions

- **File-based corpus, rendered via a virtual-flight resolver** (not a per-
  endpoint `/eval` mirror, not synthetic DB rows). A corpus pack is opened as
  flight id `eval-<corpus_id>`; three core read paths resolve it from disk, so
  **every existing briefing endpoint serves it unchanged** with no DB rows.
- **Runtime-gated, never in prod.** The API router is mounted only when
  `WEATHERBRIEF_EVAL_WORKBENCH` is set (`api/app.py`), and the resolver guards
  are dead code when the flag is off. Endpoints also require `require_admin`.
- **Labels-only in git.** `eval_data/` is globally gitignored; pack payloads are
  reproducible from prod. Only `corpus_meta.json` + `label.json` are committed
  (force-added — `pull_eval_corpus.py` prints the `git add -f` line).
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
| `config.py` | `eval_workbench_enabled()`, `eval_corpus_dir()`, the `eval-` namespace helpers |
| `situations.py` | shared pack-load / advisory-summary / `classify_situations` + `SITUATION_VOCAB` (also imported by `scripts/extract_digest_eval.py`) |
| `corpus.py` | `CorpusMeta`, `CorpusLabel`, `CorpusPack`; list/load/`save_label`; `coverage_report` |
| `resolver.py` | `synthesize_flight` / `synthesize_pack_meta`; the three hook entry points |
| `ingest.py` | build a `CorpusMeta` from a pack dir + copy artifacts (`build_corpus_meta`, `ingest_pack`, `load_pack_context`); script-side, lazy heavy imports |
| `candidates.py` | `candidate_reasons`, `base_score`, `select_candidates` (coverage-aware greedy) |

API: `src/weatherbrief/api/eval_workbench.py` — `GET /api/eval/packs`,
`GET /api/eval/coverage`, `GET /api/eval/packs/{id}`,
`POST /api/eval/packs/{id}/label`. Mounted conditionally in `api/app.py`.

Frontend: `web/eval.html` + `web/ts/eval/eval-main.ts` (list + coverage grid);
`web/ts/eval/label-panel.ts` (in-view panel, lazy-imported by
`web/ts/flight-main.ts` when the flight id starts with `eval-`). esbuild entry
`build:eval`/`dev:eval` in `web/package.json`.

Scripts: `scripts/export_eval_candidates.py` (select → manifest),
`scripts/pull_eval_corpus.py` (manifest/dir → corpus).

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
2. `pytest tests/eval_workbench/ -q` — should pass (11 tests; pure logic).
3. Build a tiny corpus: `python scripts/pull_eval_corpus.py --from data/packs`
   (or via `export_eval_candidates.py` → manifest). Confirm
   `$EVAL_CORPUS_DIR/<id>/corpus_meta.json` written, payloads copied.
4. `WEATHERBRIEF_EVAL_WORKBENCH=1` + `/devserver`; open `/eval.html` — list +
   coverage render. Open a pack → `/flight.html?id=eval-<id>` should render the
   **full standard briefing** (cross-section, advisories, digest, skew-T) and
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

- Eval running + AMBER-bias notes: `digest-eval` skill (`.claude/skills/digest-eval/`)
- Digest pipeline + guidance presets: [digest.md](./digest.md)
- Pack storage / serving: `storage/flights.py`, `api/packs.py`
- Issue #254 (Phase 2 — situation-coverage sampling + golden labels)
