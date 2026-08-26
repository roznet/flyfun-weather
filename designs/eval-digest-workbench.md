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
this doc covers the architecture. Since then `run_digest_eval.py` was also
rebased onto the corpus itself (see Scoring tie-in), retiring the compact-fixture
eval set.

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
- **Blind-first labelling.** The panel hides the model's verdict + synopsis
  (`#assessment-banner`, `#alt-assessment-banner`, legacy `#latest-assessment`,
  `[data-section="synopsis"]`) until you toggle blind off. Keep `BLIND_SELECTORS`
  in `label-panel.ts` in step with any rename of the briefing's verdict nodes —
  a stale selector leaks the answer with no visible failure.

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

`fetch_timestamp` in `corpus_meta.json` is the pack's **real** fetch time (the
earliest `forecasts[].fetched_at`, via `pack_fetch_datetime`; midnight only as a
fallback). The resolver ignores it for identity — there is exactly one pack per
`corpus_id` — but it is what tells two same-day briefings of one flight apart.

**Id collisions (`disambiguated_corpus_id`).** `corpus_id` is keyed on (route,
target date, days-out, fetch *day*) so a re-pull of the same scenario rebuilds
`corpus_meta.json` while preserving `label.json` (`_SKIP_ON_COPY`). That key
collided for genuinely *different* briefings of one flight on one day (an 07:01
LIFR-departure brief and its 09:38 re-brief graded RED and AMBER) — the second
ingest silently overwrote the first. Now: identical content hash keeps the bare
id and overwrites itself; a different briefing gets a `_t<HHMM>` suffix, and the
incumbent keeps the bare id so committed corpus dirs and their labels stay valid.
Ids are checked across **both** areas, since `find_pack` searches staging first
and would otherwise shadow an already-promoted pack.

**Cruise/ceiling come from the advisory baseline, not the snapshot.** The
snapshot doesn't carry them; without `route_advisories.json` the meta would
default to 8000/18000 and the resolver would synthesize the eval `Flight` at a
bogus cruise, drawing the cross-section cruise line inconsistent with the saved
advisories.

## Module map

`src/weatherbrief/eval_workbench/` (import-light; resolver/config touch no heavy deps):

| Module | Role |
|--------|------|
| `config.py` | `eval_workbench_enabled()`, `eval_corpus_dir()`, `eval_staging_dir()`, `area_root()`, `AREAS`, the `eval-` namespace helpers |
| `situations.py` | shared pack-load / advisory-summary / `classify_situations` + `SITUATION_VOCAB` (also imported by `scripts/extract_digest_eval.py`) |
| `corpus.py` | `CorpusMeta` (+ debrief/priority fields), `CorpusLabel` (+ `priority`), `CorpusPack` (+ `area`); area-threaded list/load/save; `find_pack` (search both areas), `promote`, `coverage_report` |
| `resolver.py` | `synthesize_flight` / `synthesize_pack_meta(meta, area)`; hooks resolve via `find_pack` (serves staging or corpus) |
| `ingest.py` | `build_corpus_meta`, `ingest_pack(area=…)` (gzips cross_section via `compact_corpus_pack`, writes gitignored `_source.json` breadcrumb for later re-sync), `load_pack_context`, `disambiguated_corpus_id` / `pack_fetch_datetime` (collision handling above) |
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
`pull_debrief_data.py` (attach debriefs), `rerun_advisories_diff.py` (advisory regression, `--deep`,
`--check-invariants`, `--altitude-profile`),
`rehydrate_eval_cache.py` (rebuild derived cache).

## What a clean replay does not cover

A replay's "N packs, no change" is only as broad as the packs in it, and two
gaps are structural rather than incidental (#578). Both were found while
validating changes the replay had blessed.

**The corpus is a low-level corpus.** Cruise altitudes in the staging set:

```
    0- 2000 ft :   1
 2000- 4000 ft :  27
 4000- 6000 ft :  31
 6000- 8000 ft :  37
 8000-10000 ft :  49        <- 142 of 205 (71%) below 10,000 ft
10000-12000 ft :  28
12000-14000 ft :  16
16000-18000 ft :   8
18000-20000 ft :   6
26000-28000 ft :   2        <- 15 packs at/above 16,000 ft
```

Two consequences for any altitude-scaled change (turbulence/CAT tiers, icing
aloft, cloud tops). Below 10,000 ft, a law like #539's `clamp(Δz/1000, 1.0, 2.0)`
is ≥1.0 by definition, so for 71% of the corpus the comparison is one-way *by
construction*. And where tightening bites hardest there is nothing to tighten:
of the 15 packs at/above 16,000 ft, 11 already read "Smooth ride expected" in the
baseline. #539 accordingly replayed as 8 escalations and 1 de-escalation — which
reads as "this change only ever raises risk" and is an artifact of the sample,
not a property of the change (a dev pack at FL180 goes from 9 severe-at-cruise
layers to 0).

`rerun_advisories_diff.py` therefore **ends every run with the cruise-altitude
profile of the packs it replayed** (`corpus.altitude_profile` /
`format_altitude_profile`, `--altitude-profile` to print it alone), including how
many of the high packs carry a *flagged* turbulence baseline — the only ones on
which a de-escalation has anything to measure. Growing the corpus at FL150–FL200
with packs that carry active turbulence is the standing gap; the profile is what
keeps it visible.

**A check that reports success without running.** `rerun_manifest_deep` called
`run_analysis_from_pack` and discarded its result — the function returns a
manifest and writes nothing, while the `run_advisories_from_pack` beside it
re-reads `route_analyses.json` off disk. `--deep` therefore re-graded the
*pre-change* soundings and reported "no change" for exactly the sounding-layer
changes it exists to validate. Fixed by persisting the recomputed manifest
(`tasks/artifacts.save_route_analyses`, split out of `save_analysis_artifacts`
for this) and pinned by `tests/eval_workbench/test_rerun_deep.py`, which
perturbs the sounding layer and requires a diff.

**Invariants, not just diffs.** `--check-invariants` runs the published-extent
invariants (`analysis/advisories/invariants.py`) over each re-run manifest and
counts aggregates that read calmer than a flagged model. A diff answers "did this
change move a rating?"; the invariants answer "is what we publish self-consistent
at all?", and only real packs carry the geometry that breaks it. The same
predicates run in CI over synthetic contexts, and over a handful of corpus packs
when `EVAL_CORPUS_DIR` is present
(`tests/analysis/advisories/test_extent_invariants_on_corpus.py`).

## Scoring tie-in — the corpus IS the eval set

**The old compact-fixture flow is superseded.** `run_digest_eval.py` no longer
reads `tests/eval_data/digests/` fixtures or a `resolve_expected` /
`expected_assessments` shim; it loads `CorpusPack`s directly (`list_corpus(area)`)
and scores each pack's saved `digest_context.txt` replay against
`pack.label.assessments[guidance]`. The workbench corpus is the single source of
truth for golden labels, so the once-planned "corpus → fixture wiring" is closed
in the other direction: nothing derives fixtures from the corpus.

`scripts/extract_digest_eval.py` and `scripts/label_digest_eval.py` (fixture
extraction + terminal labelling into `meta.json["golden"]`) are the legacy
pre-workbench path. They still import `eval_workbench.situations`, but nothing
downstream scores their output — don't reach for them when adding eval entries.

The runner **excludes rather than silently skips** three classes, and reports the
tally: long-range packs (`days_out > ecmwf_grib_horizon_days()`, whose
production output is `LongRangeDigest.outlook`, a different scale that
`CorpusLabel` has no vocabulary for — the old fixture set wrongly replayed 74 of
114 through the short-range prompt), packs with no `digest_context.txt` (context
is gitignored, re-pull it), and unlabelled packs (scoring against the model's own
prior output measures self-consistency). Long-range has its own harness,
`scripts/run_longrange_eval.py` (haiku vs sonnet on the outlook scale).

## Smoke check

`pytest tests/eval_workbench/ -q` (26 tests, pure logic — no server needed).
Runtime: `WEATHERBRIEF_EVAL_WORKBENCH=1` + `/devserver`, open `/eval.html`, then
a pack → `/briefing.html?flight=eval-<id>` renders the **full standard briefing**
with the panel docked bottom-right, blind by default. `run_digest_eval.py
--dry-run` lists what would run plus the exclusion tally, with no LLM calls.

## Open items / risks

- **Edge endpoints on eval flights.** The 3 hooks cover the auto-load path
  (flight + packs + snapshot/advisories/digest). Endpoints doing extra DB work
  (`audit_pack_access` — defined in `api/security.py`, called from `api/packs.py`
  on `get_pack`/`get_snapshot` — plus observations/advisory *recalc*, email/PDF)
  are not exercised by the labelling flow; the digest/skew-T/chart GETs resolve
  fine via `artifact_path`. Harden any that 500 on an `eval-` flight.
- **Core pack endpoints aren't admin-gated** (only `/api/eval/*` carries
  `require_admin` + `require_workbench`). Acceptable because the whole feature is
  dev-only (flag off in prod), but note it.
- **`trend` coverage** (#254 acceptance): pull a few packs carrying a
  `previous_digest` (the `previous_digest` situation tag) so `trend` is
  exercised, and label them.

## References

- Eval running + AMBER-bias notes: `eval-digest` skill (`.claude/skills/eval-digest/`)
- Digest pipeline + guidance presets: [digest.md](./digest.md)
- Pack storage / serving: `storage/flights.py`, `api/packs.py`
- Issue #254 (Phase 2 — situation-coverage sampling + golden labels)
