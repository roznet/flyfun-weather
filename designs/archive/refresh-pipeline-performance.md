# Refresh pipeline performance investigation

> **Outcome (re-verified against code 2026-08-15):** Phases A, B-1',
> B-2 and B-3 all shipped to prod. B-3 evolved further into a
> priority-aware, fault-tolerant decode dispatcher (GH #171 / PR #172,
> shipped 2026-05-22) — see MEMORY notes
> `project_decode_priority_dispatcher.md` / `project_grib_process_pool.md`.
> Only **Phase C** (ICON memory streaming) and the **parking-lot items
> (O-1, O-3 dup, O-4, O-5)** remain unbuilt, all deliberately deferred
> (memory is not a constraint post-upgrade). O-4 confirmed still open:
> `pipeline.py` sections 5 (GRAMET) and 7 (digest) are still sequential.
> The active investigation is complete; **this doc is now a historical
> record of the measurements and reasoning, not a live plan.** The
> durable B-3 architecture lives in its own design doc
> (`designs/grib-decode-dispatcher.md`, in INDEX), so this plan is a
> candidate to archive.
>
> **What drifted since the briefs were written** (read the code, not the
> brief, for current shape):
> - **Line numbers in every brief below are as-of-May-2026 and are now
>   wrong.** `_decode_pressure_vars_from_datasets` is `decode.py:542`
>   (not 295), `decode_ecmwf_pressure_per_point` is `:2248` (not 1161),
>   `decode_ecmwf_surface_per_point` is `:2829` (not 1403).
> - **B-1' shipped as a numpy advanced-indexing bilinear gather**
>   (corner indices/weights computed once per dataset), *not* the
>   `scipy.ndimage.map_coordinates` the brief sketched. Don't grep for
>   `map_coordinates` — it isn't there.
> - **`_GribTimer` never moved to a `_grib_timer.py` module** (the B-3
>   brief speculated one); it stays in `fetch/grib/__init__.py:93`.
> - **The ICON cycle/horizon constants are no longer literals.**
>   `ICON_EU_MAIN_CYCLES` / `ICON_EU_MODEL_LEVEL_MAX_HOUR_SHORT` in
>   `icon_eu_fetch.py:361-363` now derive from the `ICON_EU` model
>   registry entry, so the "single-file two-line revert" note in
>   *Files touched* no longer describes the code.
> - **The Phase A smoke-test fix #2 shipped**: `is_cached(run_dir, name)`
>   exists in `fetch/grib/cache.py:236` and is what the prefetch/precache
>   paths call; only genuine reads use `get_cached`.
>
> **Still open, confirmed 2026-08-15** (low priority, pick up alongside
> the next touch of these files): `_stage_label` in
> `scripts/profile_refresh.py:71` is still dead (both call sites pass
> `None`), and `fetch_icon_eu_fields` (`icon_eu_fetch.py:955`) still
> logs INFO on full failure while the three sibling fetch helpers warn.

## Status (2026-05-02)

- ✅ **Phase A** — instrumentation, ICON cycle/horizon fix, hardware
  upgrade. All shipped. See [Plan](#plan).
- ❌ **Phase B-1 (ThreadPool variant)** — abandoned after local profile
  showed no wall-clock improvement. Root cause turned out to be
  different than the brief assumed; details in
  [Phase B-1 — investigation outcome](#phase-b-1--investigation-outcome).
- ✅ **Phase B-1' — vectorise ECMWF interp loop** — merged 2026-05-02
  (PR #105, on main at `d45801bb`). Deploy pending. See
  [brief](#phase-b-1--vectorise-ecmwf-interp-loop-replaces-threadpool-variant)
  for the original goals against which to evaluate prod numbers
  post-deploy.
- ✅ **Phase B-2** — MetPy vectorisation. Deployed 2026-05-02 (PR #103,
  commits a1eacab4 + 3afde9b9). First post-deploy refresh of the
  canonical test flight: `analyze` 51.81s → 35.37s (–31.7%) under
  concurrent-refresh contention. See [GIL evidence](#gil-bound-decode--prod-evidence-2026-05-02).
- ✅ **Phase B-3 — out-of-process GRIB decode pool** — SHIPPED (PR
  #104, `800bbeed`), then extended into a priority-aware,
  fault-tolerant decode dispatcher (PR #172 / `14cf4d96`, shipped
  2026-05-22). `ProcessPoolExecutor`
  (spawn, default workers=2, override via `GRIB_DECODE_WORKERS`,
  per-call timeout via `GRIB_DECODE_TIMEOUT_S` default 300 s) wraps the
  six decode entry points called from `enrich_forecasts`. Worker
  module reads bytes from disk inside the worker so the parent never
  pickles ~70 MB blobs. `_dispatch_decode` auto-resets the pool on
  `BrokenProcessPool` (worker SIGKILL/OOM) or `TimeoutError` (worker
  hang), so one bad worker doesn't poison subsequent requests. New
  `tests/test_grib_pool.py` covers cold start, parallel dispatch,
  error propagation, crash + hang recovery. Pool shutdown wired into
  FastAPI lifespan via `asyncio.to_thread`. Local A/B on the canonical
  test flight: pipeline 219.47 s → 180.79 s (–17.6%), parent peak RSS
  1553 MB → 528 MB (–66%), no numerical drift. Awaiting prod rollout
  to validate the predicted concurrency win under forecast-cycle
  contention.
  See [brief](#phase-b-3--out-of-process-grib-decode-pool).
- ⏸️ **Phase C** — ICON memory streaming. Deferred (memory not a
  constraint post-upgrade).

**B-1' and B-3 share `fetch/grib/decode.py`** but at different layers:
B-1' rewrote the inner `_decode_pressure_vars_from_datasets` loop
(now in main); B-3 wraps the outer `decode_ecmwf_*_per_point` entry
points with a process-pool dispatcher. They were designed to
compose, with B-1' scheduled to land first so the pickle/IPC cost
ratio of B-3 stays favourable. **B-3's agent should rebase onto
post-B-1' main and re-baseline its measurements** (per-step decode
is now ~halved, so the IPC overhead ratio shifts).

## How to brief a fresh agent for Phase B-3

> "Read `designs/plans/refresh-pipeline-performance.md`, find
> Phase B-3, and verify against the acceptance criteria there.
> The Status section at the top tells you where we are. Don't
> worry about Phases A, B-1', or B-2 — those are done."

Each brief is self-contained: goal, files, approach, expected impact,
acceptance criteria, risk notes.

## Why

Briefing refreshes take ~2 minutes (sometimes longer). User wants to be sure
there are no easy optimisations missed before treating the latency as
intrinsic. This document captures the full investigation, numbers measured,
issues uncovered, the planned fixes, and what's still open.

## Driver: how we measure

Script: `scripts/profile_refresh.py`

- Loads a flight from the local DB by id (uses `_load_flight_or_404` to
  hydrate the `Flight` model with parsed waypoints).
- Mirrors `_prepare_refresh()` for option construction so the run matches
  the production refresh path (GRAMET on, LLM digest on, GRIB enrichment
  on, Skew-T off — that's the API default).
- Wraps `execute_briefing()` in:
  - a `progress_callback` that records per-stage wall-clock from existing
    `_notify()` boundaries
  - a `pyinstrument.Profiler(interval=0.01, async_mode='disabled')`
- Writes pack to `profiles/_packs/{flight_id}/{ts}/` (kept separate from
  real `data/packs/`).
- Outputs:
  - per-stage timing summary to stdout
  - HTML flamegraph: `profiles/{flight_id}_{ts}.html`
  - text flamegraph (top 1% frames): `profiles/{flight_id}_{ts}.txt`

Flags: `--clear-grib-cache`, `--force-live` (override historical mode),
`--no-llm`, `--no-gramet`.

Test flight used throughout: `lsgs_lsgl_lfqb_lfqa_egkb_egtf-2026-05-04-55bf`
(LSGS → LSGL → LFQB → LFQA → EGKB → EGTF, 6 waypoints, FL160, 3h, D+3
European multi-leg). Pipeline interpolates this to 50 route points along
~370 nm.

## Instrumentation added during this work

Surgical, zero-behavior-change additions inside `fetch/grib/__init__.py`:

- `_grib_time_reset()` / `_grib_time(label)` context manager — accumulates
  per-label wall-clock across an `enrich_forecasts` call; thread-safe with
  a lock so the in-Phase-1 ThreadPoolExecutor can record its own legs.
- `_grib_gc()` — wraps `gc.collect()` to track cumulative GC time and call
  count without changing memory behaviour. **gc.collect() stays — it's
  load-bearing for memory, not just defensive.**
- `_grib_time_summary()` — single INFO log line at end of enrichment:
  ```
  GRIB timing: phase1_parallel=… ecmwf_total=… icon_prefetch=… gc=…s/N
  ```
- Improved 404 logging in `icon_eu_fetch.py` — `_format_failure_summary`
  appends `(404=N,network=M)` to per-fhour aggregate lines so failure
  modes are visible in prod logs without raising the log level.

These additions are safe to deploy as part of Phase A (see plan below).

## Profile runs and headline numbers

Three runs on the same test flight, all D+3 European, all warm Open-Meteo
(paid API key, fast network), with different GRIB cache states:

| Run | Cache state | ICON state | Total | GRIB | Peak RSS |
|---|---|---|---|---|---|
| 1 — warm | populated | broken (404s) | 92.3s | 32.0s | ~924 MB |
| 2 — cold | cleared | broken (404s) | 88.2s | 33.9s | similar |
| 3 — ICON fixed | populated | **working fully** | 208.2s | **144.4s** | **3317 MB** |

The cold/warm delta in runs 1+2 is within noise (4s) — confirms **GRIB
enrichment is decode-bound, not download-bound**. Cache barely matters.

Run 3 is the eye-opener: **fixing ICON downloads more than doubles the
pipeline (96s → 208s) and triples peak memory (924 MB → 3.3 GB)**. That
finding drove the staged plan in this document.

## Detailed stage breakdown — Run 1 (broken ICON, 96s baseline)

```
Stage                 Sec   %     Notes
──────────────────────────────────────────────────────────────
route_interpolation   0.00  —
elevation_profile     0.13  0.1%
fetch_forecasts       3.14  3.4%  3 models (ecmwf/gfs/icon)
grib_enrichment      32.43 33.7%  ┐
  ecmwf_a2_decode    27.22         │ 10 steps × 2.72s, sequential
  ecmwf_a1_decode     4.66         │ 10 steps × 0.47s, sequential
  gfs_total           7.91         │ overlapped with ECMWF
    gfs_clwmr_decode 5.98         │ 4 fhours
    gfs_cloud_diag   4.30         │ 4 fhours
  icon_prefetch      4.74         │ failed fast (404s)
  gc                 1.36 / 8     │ ⟵ NOT load-bearing for perf
route_analysis      18.85 19.6%   ┐
  metpy.xarray wrapper 8.43       │ pure overhead
  compute_derived_levels 10.99
  compute_indices     4.67
  compute_stability   1.56
waypoint_analysis     0.02  —
route_advisories      0.27  0.3%
save_snapshot         0.00  —
fetch_gramet          3.36  3.5%  HTTPS wait
llm_digest           38.73 40.2%  Anthropic API HTTP wait
──────────────────────────────────────────────────────────────
Total                96.26 100%
```

Open-Meteo serial fetches are 3.1s total, not the bottleneck I initially
suspected. Paid API key + warm DNS + 3 active models = cheap.

## Detailed stage breakdown — Run 3 (ICON working, 208s)

```
GRIB timing:
  phase1_parallel    111.88s
    icon_prefetch    111.87s   ⟵ NEW BOTTLENECK
      icon_prefetch_var  108.98s / 36 calls (~3.0s × 36 var-fhours)
      icon_prefetch_cloud_diag  2.49s / 4
    ecmwf_total      35.59s    (overlapped, finished at ~35s)
      ecmwf_a2_decode 30.18s / 10
      ecmwf_a1_decode  5.28s / 10
    gfs_total         8.21s    (overlapped, finished at ~8s)
      gfs_clwmr_decode  5.99s
      gfs_cloud_diag_decode  4.13s
  phase2_icon_decode 31.94s    ⟵ 2nd new cost
    icon_chunked_decode 28.91s / 4 fhours (~7.2s/fhour)
    icon_cloud_diag_decode 0.44s / 4
  propagate_all       0.05s
  gc                  3.47s / 20

route_analysis       20.05s
fetch_gramet          3.63s
llm_digest           36.88s
──────────────────────────
Total               208.24s
```

Key shape: with ICON working, **Phase 1 is dominated entirely by ICON
download**. ECMWF (35s) and GFS (8s) become "free" within the overlap.
Means **ECMWF parallelisation saves nothing** as long as ICON download is
the long pole.

## The ICON cycle/horizon bug (discovered during profiling)

Two wrong constants in `fetch/grib/icon_eu_fetch.py` (verified empirically
by curl-ing DWD opendata directory listings):

| Constant | Code value | Reality (DWD) |
|---|---|---|
| `ICON_EU_MAIN_CYCLES` | `{0, 12}` | `{0, 6, 12, 18}` |
| `ICON_EU_MODEL_LEVEL_MAX_HOUR_SHORT` | `78` | `48` (hourly to ~30h, then 6-hourly to 48h) |

Behaviour today (with bug):
- For a flight ≥ 49h out, code may pick a short cycle (03/09/15/21z),
  thinking the horizon is 78h. Real horizon is 48h.
- Code then requests f049+ files that don't exist → all 40 levels return 404
  per variable → variable enrichment fails for those fhours.
- Some hours within real horizon still succeed, so prod ICON enrichment
  is **partial**, not entirely broken.

Fix is two-line constant correction. **Held back until Step 2(b) is done**
because activating it on prod increases memory pressure (see below).

The improved 404 logging is independent of the constant change and ships
in Phase A.

## Phase A smoke-test findings (worth recording)

After deploying the instrumentation locally and running the test flight,
two things surfaced that update our model of the memory peak:

1. **The memory peak is in Phase 1 prefetch, not Phase 2 decode.** With
   12z (a main cycle even in buggy code) freshly published, ICON
   downloaded all 36 variable-fhours sequentially. RSS climbed from
   510 MB baseline to **3118 MB at end of Phase 1**, then *dropped* to
   3053 MB by the end of Phase 2 decode. So Step 2(b) ("stream var_bytes
   one at a time during decode") will help, but **most of the bloat is
   already there before decode starts**. The leak is in the prefetch
   loop itself, where 36 sequential `bz2.decompress` + bytearray
   accumulations cause glibc/macOS malloc fragmentation. Linux glibc may
   reclaim more aggressively, which would explain prod's ~2.2 GB peak
   vs our ~3.1 GB on macOS.

2. **`get_cached(run_dir, filename)` reads the entire file just to
   check existence.** It's used as `if get_cached(...) is not None:
   continue` to skip already-cached variables. On a warm-cache refresh
   this reads ~70 MB × 36 var-fhours = 2.5 GB into briefly-held bytes
   objects, only to discard them. Even if they free promptly, the
   `ru_maxrss` peak still reflects the high-water mark.

   Fix: add `is_cached(run_dir, filename) -> bool` that just calls
   `path.exists()` + TTL check, and route the prefetch loop to it.
   Zero behaviour change, removes a per-refresh memory spike on warm
   cache. Worth doing as part of Phase A or as a tiny standalone PR
   ahead of Phase B.

These updates change the Step 2(b) design slightly: streaming is still
worth doing, but **the bigger memory win is fixing the prefetch loop**
to either (a) call `bz2.decompress` once and write to disk before
moving to the next variable rather than buffering the whole result,
or (b) explicitly free each `data`/`per_var` reference plus call
gc.collect between variables (matching the decode loop pattern), or
(c) avoid the bytearray buffer growth by writing each level's
decompressed bytes to disk as it arrives rather than accumulating.

## Prod observations (post Phase A deploy, 2026-05-01)

Phase A landed on prod and we triggered one user refresh to capture the new
log lines. The data substantially shifts our model of where prod time is
spent.

**Test refresh**: D+3 European flight (LSGS→…→EGTF, FL160). Phase 1
picked ICON-EU `20260501 15z` — a short cycle the buggy code thinks goes
to 78h but really only goes to 48h. fhours 64+ all 404'd (the
`(404=40)` annotation made this immediately visible in prod logs).

### Prod vs local timing (warm cache, paid Open-Meteo, full enrichment)

| Stage | Local (Mac) | Prod (DO 2 vCPU) | Ratio |
|---|---|---|---|
| `phase1_parallel` | 32s | **150.5s** | 4.7× |
| `ecmwf_total` | 32s | **150.5s** | 4.7× |
| `ecmwf_a2_decode` | 27.30s / 10 | 123.15s / 11 (≈11.2s/step) | 4.1× |
| `ecmwf_a1_decode` | 4.78s / 10 | 25.17s / 11 | 4.7× |
| `gfs_total` | 8s | 42.22s | 5.3× |
| `gfs_clwmr_decode` | 5.42s / 4 | 31.64s / 5 | 5.0× |
| `gfs_cloud_diag_decode` | 3.66s / 4 | 24.58s / 5 | 5.5× |
| `icon_prefetch_var` (failed-fast) | 4.65s / 36 | 8.68s / 45 | 1.9× |
| `ecmwf_scan` | 0.02s | 1.88s | 90× |

ECMWF a2 + GFS decode are CPU-bound (cfgrib + xarray + numpy interp).
Prod's 2 vCPUs vs the local 8+ cores explains the ~4–5× slowdown
cleanly. Network-bound stages (icon_prefetch_var even on failure) scale
much less. The `ecmwf_scan` outlier is because prod has many delivery
files in `/data/ecmwf` from days of ECPDS pushes; small absolute cost.

### Prod vs local memory (same refresh)

| Checkpoint | Local (Mac) | Prod (Linux) |
|---|---|---|
| baseline | 510 MB | 630 MB |
| `after_phase1` | +1100 MB | **+336 MB** |
| `icon_fhour_decoded` peak | +1145 MB | +336 MB (unchanged — see below) |
| Total enrichment peak | 1656 MB | **966 MB** |

Prod's much smaller `after_phase1` delta reflects two things:
1. Linux glibc malloc returns arena pages more aggressively than
   macOS — confirmed empirically.
2. ICON downloads failed on prod (15z short-cycle), so `_decode_fhour`
   returned None for every fhour and the `icon_fhour_decoded` mark
   never measured an actual decode peak.

The macOS-vs-Linux delta on identical workload (fully successful
ICON) we don't have yet. Best estimate: prod with full ICON working
would peak at **~1.5–2.2 GB**, well under the 3 GB Docker cap.
But this is an **estimate**, not a measurement — see "Should we
trigger a deliberate ICON-decode test?" below.

### Implications for Phase B priorities

Phase A's design doc assumed **ICON download was the long pole** (because
that's what dominated my local profile when 12z published mid-test).
On prod, with ICON failing 404, **ECMWF a2 decode is the long pole at
123s — 80% of Phase 1**. Even fixing ICON's cycle bug won't change
that until ECMWF a2 is parallelised:

- Today on prod: 11 ECMWF a2 steps × ~11.2s, sequential = 123s wall
- With workers=2 inside `_enrich_ecmwf_inner`: ~67s wall (–56s)
- Phase 1 wall would drop from 150s → ~70s
- Memory cost: each cfgrib decode peaks ~270 MB, so workers=2 → ~540 MB
  peak. Even on local-Mac scale that's safe; on prod with allocator
  reclaim it's even safer.

**ECMWF a2 parallelism is now the highest-value Phase B target.** Bigger
prod win than ICON streaming, no memory risk under 2 GB, and unrelated
to the ICON cycle bug — can ship independently.

### Should we trigger a deliberate ICON-decode test on prod?

Currently the prod `icon_fhour_decoded` checkpoint is uninformative
because ICON downloads fail (cycle bug + 15z short cycle) and the
decode loop becomes a no-op. We don't have prod numbers for ICON
decode time or memory peak.

We could collect them without any code change by **triggering a
refresh on a flight ≤48h out** while a short cycle is the most
recent. The current 15z covers f000–f048 — a flight 24h out
gets f024 successfully, ICON downloads + decodes on prod hardware,
we measure. No deploy required, no behavioural change.

This is a cheap experiment we should do before designing
Phase B/C: it gives us the ICON decode time scaling factor on prod,
which we currently only have for ECMWF/GFS (~4–5×). If ICON decode
also scales 4–5×, prod ICON decode would be ~120–150s for 4 fhours
— meaningful added cost that should inform whether we
do streaming refactor before or after the cycle/horizon fix.

## Post-upgrade prod observations (2026-05-01 ~20:15 UTC)

Two changes shipped on 2026-05-01 after Phase A's instrumentation
landed:

1. **ICON cycle/horizon fix** (commit `d53b97cc` direct on main) —
   `ICON_EU_MAIN_CYCLES = {0, 6, 12, 18}`, `ICON_EU_MODEL_LEVEL_MAX_HOUR_SHORT = 48`.
   Verified via prod logs that the picker now selects 12z (real main)
   instead of 15z (real short) for our test flight, and `(404=N)`
   warnings disappear.

2. **Droplet upgrade**: DO-Regular Basic 2 vCPU shared / 3.8 GB RAM
   → **DO-Premium-AMD 4 vCPU dedicated / 7.8 GB RAM**, NVMe SSD,
   ~$56/mo. Swap pressure went from 1.9 GB used to **0**.

### Post-upgrade refresh numbers (same test flight)

| Metric | Pre-upgrade (Basic 2 vCPU, broken ICON) | Pre-upgrade (Basic 2 vCPU, full ICON) | **Post-upgrade (Premium 4 vCPU, full ICON)** |
|---|---|---|---|
| `phase1_parallel` | 150s | 320s | **92s** |
| `ecmwf_a2_decode` (per step) | 11.2s | 11.2s (contended) | **6.9s** |
| `gfs_clwmr_decode` | 31.6s | 82s | **16s** |
| `gfs_total` | 42s | 87s | **20s** |
| `phase2_icon_decode` | 0 | 118s | **95s** |
| Total enrichment | 150s | 438s | **187s** |
| `route_analysis` | not measured | not measured | **~106s** ⟵ surprise! |
| Total pipeline | "~2 min" (user report) | est. ~7 min | **5:51 (351s)** |
| Peak RSS (in enrichment) | not measured | 1028 MB | **1027 MB** (unchanged) |
| Pipeline peak RSS | 1.8–2.2 GB | 1.6 GB | **1.6 GB** |

### Headline takeaways

1. **Hardware upgrade alone gave ~3.5× Phase 1 speedup** (320s → 92s).
   Two compounding effects: GFS/ECMWF/ICON workers now have dedicated
   cores instead of competing for 2 shared vCPUs (~2× from contention
   removal), AND Premium AMD is ~30–40% faster per core than DO-Regular
   shared (~1.6× clock).

2. **`route_analysis` is 106s on prod** — that's much more than I'd
   estimated from local (19s × 4–5× scaling = ~95s, close). It's
   single-threaded and benefits only from clock improvements, so the
   4-core upgrade barely helped. **MetPy is now the biggest per-stage
   prod cost outside ECMWF.** Vectorising it (was O-3, now Phase B-2)
   could roughly halve it.

3. **Memory is not the constraint anywhere** — peak 1027 MB during
   enrichment, 1.6 GB pipeline-wide, well under the 3 GB Docker cap.
   ICON memory streaming (was Phase C key concern) is now a
   nice-to-have, not a correctness/safety requirement.

4. **ECMWF a2 step parallelism** is now safe and high-value with 4
   dedicated cores (was counter-productive on 2 vCPU contended). On
   prod this drops `ecmwf_a2_decode` from 76s sequential → ~40s with
   workers=2.

5. **We accepted a wall-time regression** (~2 min → ~5:51) for ICON
   correctness. Of those 4 extra minutes, ~3 are inherent (full ICON
   fhours download + decode), ~1 is recoverable via the Phase B
   optimisations below.

## Memory analysis

Prod baseline today (`docker logs weatherbrief --since 24h | grep peak=`):
- peak ranges 1.8 GB → 2.2 GB
- worst single request growth: +1363 MB
- most requests are small (+0–250 MB) — cache hits dominate

Prod is **already** hitting the ICON cycle bug right now (logs show
"Found ICON-EU run: 20260501 09z (horizon 78h)" followed by f031+ all-failed
warnings). So prod memory baseline is **with partially-failing ICON**.

Local test with the cycle fix (full ICON working) hit **3317 MB peak on
macOS**. Linux's glibc allocator releases pages more aggressively than
macOS, so prod might land 2.0–2.5 GB. But the 3 GB Docker cap is in play.

Plausible peak attribution:
- Per-fhour ICON decode holds `var_bytes` dict with 9 variables × ~70 MB
  decompressed = ~630 MB.
- Plus xarray decode workspace (cfgrib opens temp files, allocates
  numpy arrays per pressure level).
- Plus existing pipeline working set (~1.0–1.5 GB).
- Held simultaneously across `_decode_and_merge_icon_eu`'s per-fhour loop.

## Plan

### Phase A — instrument and observe ✅ DONE

Shipped via PR #102 (merged 2026-05-01, commits `bd5a841f`, `d73a607d`,
`cb801732`):

- ✅ Sub-stage GRIB timing (`_GribTimer` class, ContextVar-isolated
  per-call, propagated to ThreadPool workers via `_submit_with_context`)
- ✅ Memory RSS checkpoints (`enrich_start`, `after_phase1`,
  `icon_fhour_pre/decoded/post_gc`, `after_phase2`, `enrich_end`)
- ✅ `_grib_gc()` wrapper around the 5 existing `gc.collect()` calls
- ✅ Improved 404 logging in `icon_eu_fetch.py` (`_format_failure_summary`,
  HTTP status codes in aggregate log lines)
- ✅ `scripts/profile_refresh.py` driver
- ✅ `weatherbrief/process_rss.py` shared RSS helper

Plus shipped separately on 2026-05-01:
- ✅ Cycle/horizon fix (`d53b97cc`, direct commit on main)
- ✅ Hardware upgrade DO-Regular 2 vCPU → DO-Premium-AMD 4 vCPU 8 GB

Two pending nits from the bot review on PR #102 (low priority, pick up
alongside the next code change):
- `_stage_label` is dead code in `scripts/profile_refresh.py:43-`. All
  call sites pass `detail=None`, so the `if detail:` branch is unreachable.
  Remove the function and inline.
- `fetch_icon_eu_fields` in `icon_eu_fetch.py` logs at INFO even when
  all files fail. Other two fetch helpers (`fetch_icon_eu_per_variable`,
  `fetch_icon_eu_single_level`) were upgraded to WARNING on full failure —
  apply the same pattern here for consistency.

---

### Phase B — two parallel optimisation tracks

**Designed for parallel implementation.** B-1 lives entirely in
`fetch/grib/__init__.py` and `fetch/grib/decode.py`. B-2 lives entirely
in `analysis/sounding/thermodynamics.py` (and possibly its callers in
`analysis/sounding/__init__.py`). **Zero file overlap.** Two fresh agents
can work on these in parallel branches without merge conflicts.

Each track is independently shippable. Together they push prod pipeline
from 5:51 → ~4:20 (about a third of what we lost when ICON started
working correctly).

#### Phase B-1 — investigation outcome

**TL;DR**: The ThreadPool variant doesn't work. Local profile showed
no wall-clock improvement; an isolated experiment localised the cause
to xarray/Python interp, not cfgrib. Branch
`perf-ecmwf-parallel` was discarded. New direction: vectorise the
per-point interp loop (Phase B-1' below).

**What we tried**: Implemented the brief literally — wrapped the per-
step `decode_ecmwf_pressure_per_point` / `decode_ecmwf_surface_per_point`
calls in `ThreadPoolExecutor(max_workers=2)` via `_submit_with_context`,
kept merge sequential. All 197 GRIB tests passed. RSS unchanged.

**What we observed** (local, same test flight, warm cache):

| Metric | Sequential baseline | workers=2 |
|---|---|---|
| `phase1_parallel` | 32.58s | 32.98s |
| `ecmwf_total` | 32.57s | 32.98s |
| `ecmwf_a2_decode` (sum/10) | 27.60s | **56.17s** |
| `ecmwf_a1_decode` (sum/10) | 4.87s | 9.46s |
| Peak RSS | 1603 MB | 1600 MB |

Per-step decode time *doubled* under workers=2 — classic GIL/mutex
contention signature — and wall-clock barely changed.

**Where the lock actually lives** (isolated experiment, 5 a2 files):

| Phase | workers=1 | workers=2 | speedup |
|---|---|---|---|
| `cfgrib.open_datasets` only | 2.50s | 0.53s | **4.73×** ✅ |
| xarray `.sel` + per-point interp (datasets pre-opened) | 7.28s | 7.15s | **1.02×** ❌ |
| Combined (production path) | 7.45s | 7.49s | 0.99× |

cfgrib metadata-parsing parallelises fine — superlinear, in fact,
from page-cache warming. The bottleneck is
`_decode_pressure_vars_from_datasets` in `decode.py:295`: nested
Python loops doing `xr_var.sel(pressure_coord=p_val)` per level
followed by `_interpolate_per_point` per route-point, ~50 points × 25
levels × 7 variables × 10 steps. xarray's `.sel()` returns a Python
DataArray and the per-point loop holds the GIL through dict
construction, attribute access, and result accumulation. Two threads
running this loop concurrently spin the GIL against each other.

**Why the brief's prod estimate was off**: It assumed the cfgrib decode
was CPU-bound work that would parallelise on dedicated cores. The
actual hot path is the interp loop, which is GIL-bound regardless of
how many cores are available. The Phase 1 outer pool (GFS + ECMWF +
ICON) overlaps successfully because each branch holds different Python
objects; two ECMWF interp loops don't have that property.

**What would actually work**:

1. **Vectorise the interp loop** (preferred — see Phase B-1' below).
   Replace per-(point, level) `.sel + interpolate` with a single
   batched scipy `map_coordinates` call across all 50 points × all
   levels at once. Releases the GIL once during the C call, and the
   underlying numpy/scipy work itself is faster. Closer in spirit to
   Phase B-2's approach to MetPy.

2. **ProcessPoolExecutor** would parallelise the interp loop, but the
   pickling cost of shipping xarray datasets across processes is
   high relative to ~2.7s/step of work. Not the right trade-off.

3. **Cython/numba on the interp loop** — overkill for the ~5–10s
   prod savings available here.

#### Phase B-1' — vectorise ECMWF interp loop (replaces ThreadPool variant)

**Goal**: replace the per-(level, point) Python loop in
`_decode_pressure_vars_from_datasets` with a single vectorised
interpolation across all route points and all pressure levels at
once. Same outputs, less GIL-held Python work.

**Files**:
- `src/weatherbrief/fetch/grib/decode.py` — `_decode_pressure_vars_from_datasets`
  (line 295) and its helper `_interpolate_per_point`. May also touch
  `decode_ecmwf_pressure_per_point` (line 1161) and
  `decode_ecmwf_surface_per_point` (line 1403) if their data shape
  needs adjusting.

**Implementation approach** (sketch — adapt to actual code):
1. For each variable in each dataset, materialise the full
   `(level, lat, lon)` numpy array once with `xr_var.values`.
2. Build route-point fractional grid coordinates once
   (`(lat_idx, lon_idx)` arrays of shape `(n_points,)`).
3. Call `scipy.ndimage.map_coordinates(values, [lat_idxs, lon_idxs],
   order=1)` per level, OR stack levels into a `(n_levels, n_points)`
   call. scipy's C implementation releases the GIL.
4. Reshape results back into the existing `[{p_hpa: {field: val}}]`
   per-point structure for backward compatibility, OR refactor
   downstream consumers to take batched arrays (bigger change, more
   wins).

**Acceptance criteria**:
- All 197 GRIB tests pass (`pytest tests/test_grib.py
  tests/test_grib_fill.py tests/test_ecmwf_sample.py tests/test_ecmwf_sounding.py`).
- Numerical equivalence: local diff of decoded values vs main on the
  test flight matches to within `np.allclose(rtol=1e-6)` for all
  pressure-level fields. (Add a temporary comparison test during dev.)
- Local profile via `scripts/profile_refresh.py`:
  - `ecmwf_a2_decode` total **drops measurably** (target: ~halve from
    ~27s → ~14s on local; on prod ~76s → ~40s).
  - `phase1_parallel` drops correspondingly.
  - RSS unchanged.
- Same approach should be applied to ECMWF a1 (surface) and may also
  help GFS `_decode_pressure_vars_from_datasets` callers — but ship
  ECMWF first, then assess whether GFS is worth touching.

**Risk**: medium. Edge cases:
- Multi-grid ECMWF files (Europe + Nordic sub-grids in the same .grib).
  Current code handles this via `first_wins=True`; vectorised version
  must preserve that semantics.
- Out-of-domain points (NaN handling) — scipy's `map_coordinates` with
  `mode='constant', cval=np.nan` and a coverage check matches the
  current per-point None-on-out-of-bounds.
- Different grid conventions (-180/+180 vs 0–360) — already handled
  before the interp call; vectorisation shouldn't change that.

**Expected prod impact**: ECMWF decode ~76s → ~40s, Phase 1 ~92s →
~55s. Same order of magnitude the brief originally targeted, achieved
via vectorisation rather than threads.

**Out of scope**: ICON and GFS interp loops use the same helper. They
*could* be vectorised in the same PR, but defer to keep the change
focused; revisit after measuring impact on ECMWF.

#### Phase B-2 — vectorise MetPy in thermodynamics

**Goal**: hoist the `metpy.xarray.py:1285` per-call wrapper overhead out
of the per-(route_point, model) loop in `analyze_sounding`. Currently
analyses run MetPy functions one waypoint at a time, each call paying
~8.4s of pure decorator/conversion overhead across the whole pipeline.
Vectorise inputs into a leading "point" dimension so MetPy operates on
batched arrays — the documented efficient pattern.

**Why this is "just reorganising calls"**: Same MetPy, same physics
(CAPE, lifted index, total totals, K index, etc.), same numerical
results. The change is `for point in points: metpy_func(point.data)`
→ `metpy_func(stacked_data)`. MetPy is built on numpy + xarray and
supports batch inputs natively. The per-call wrapper exists to handle
scalar inputs gracefully — it's pure overhead when we have 50 route
points × 3 models worth of data we could pass at once.

**Files**:
- `src/weatherbrief/analysis/sounding/thermodynamics.py` — the
  `compute_derived_levels_extended` function (around line 319) and
  `compute_indices_extended` (around line 162). Maybe
  `compute_indices_core` (around line 69) for symmetry.
- Possibly `src/weatherbrief/analysis/sounding/__init__.py` — caller
  shape changes if we lift batching one level up. Aim to keep it local
  to thermodynamics.py first.

**Where time is spent (from Phase A pyinstrument data, local warm)**:
```
17.28  _analyze_sounding_heavy
├─ 10.99  compute_derived_levels_extended
│   └─  8.43  metpy/xarray.py:1285  (the @parse_grid_arguments wrapper!)
├─  4.67  compute_indices_extended
│   └─  4.33  metpy/xarray.py:1285
└─  1.56  compute_stability_indicators
   └─  1.00  metpy/xarray.py:1285
```

The wrapper overhead is ~70% of the call time. Nearly all of that is
saveable.

**Implementation approach**:
1. Read `compute_derived_levels_extended` and `compute_indices_extended`
   carefully. They're called from `_analyze_sounding_heavy` in
   `analysis/sounding/__init__.py`.
2. Identify the inputs (pressure, temperature, dewpoint, wind, etc.)
   and outputs.
3. The MetPy functions inside (`mpcalc.k_index`, `mpcalc.total_totals_index`,
   `mpcalc.surface_based_cape_cin`, etc.) accept arrays — verify by
   reading MetPy docs / source.
4. Build the inputs once per (model, time) batch as pint Quantity arrays
   with shape `(point, level)` and call MetPy once. Return arrays of
   the same leading shape; index back per-point in the caller.
5. Where existing per-point inputs differ in length (some points may
   have fewer pressure levels), stack with NaN padding and let MetPy's
   own NaN handling apply, or batch only same-shape soundings together.

**Acceptance criteria**:
- All existing sounding tests pass: `pytest tests/test_clouds.py
  tests/test_convective.py tests/test_comparison.py
  tests/test_ecmwf_sounding.py tests/test_grib.py tests/test_grib_fill.py
  -q`
- **Numerical equivalence**: write a small ad-hoc comparison test that
  runs both old + new on the test flight's data and asserts each
  per-point output matches to within `np.allclose(rtol=1e-6, atol=1e-9)`
  for every sounding metric. Add this as a temporary tests/ file
  during development; can be removed before merge once you trust the
  result.
- Local profile via `scripts/profile_refresh.py`: `route_analysis`
  stage drops by ~50% (was 18.85s local; should be ~10s after).
- No new warnings/errors in the briefing pipeline.

**Risk**: medium. Edge cases to verify:
- Single-point routes (only one waypoint analysed)
- Models with fewer pressure levels (some Open-Meteo models give 11
  levels, others 25 or 28 — see fetch.md table)
- NaN handling for missing data
- ECMWF post-fix gets full GRIB sounding replacement (different shape
  than DD path)

**Expected prod impact**: `route_analysis` 106s → ~55s (–51s).

---

#### Phase B-3 — out-of-process GRIB decode pool

**Goal**: move GRIB decode (cfgrib + xarray + numpy interp) out of the
main uvicorn process so that the **standalone forecast cycle and a user
auto-refresh can decode concurrently without sharing a GIL**. Each decode
runs in a worker process; the uvicorn process orchestrates and consumes
results.

##### Why now (evidence as of 2026-05-02)

Today's 7Z auto-refresh window had three flights queued behind the
forecast cycle's ECMWF GRIB phase. Per-step `ecmwf_a2_decode` blew up to
21–43s vs the clean baseline of 6.9s/step (3–6× slowdown). The
`droplet-metrics` skill (`~/.claude/skills/droplet-metrics/`) pulled
the corresponding window:

```
CPU %:    peak= 45.6%  avg= 32.2%      ← ~2.7 of 4 cores idle
Load 1m:  peak=2.56  avg=1.37          ← well below saturation (4)
Memory:   peak_used= 4367MB (53%)      ← not pressured
Net in:   peak= 31.86 Mbps  avg= 8.57  ← nowhere near link rate
```

Nothing in the system metrics shows saturation. The slowdown can only be
intra-process serialisation — the **Python GIL**. Phase B-1's local
investigation already isolated the per-(point, level) Python loop in
`_decode_pressure_vars_from_datasets` as GIL-bound; today's prod data
confirms it at the system level.

Phase B-1' vectorises the inner loop (releases the GIL during the C
call), which removes most of the contention but **not all** — cfgrib's
metadata parse and xarray's per-message dataset construction also hold
the GIL. Two simultaneous decodes still share one interpreter. The
durable fix is to give each decode its own interpreter.

Disk I/O is **unmeasured** today (DO API doesn't expose `disk_read`/`disk_write`).
Run an `iostat -xm 5 360` capture during a future contention window
before claiming GIL is the only co-factor; if `await` or `%util` is
high, the design below needs to also address disk concurrency
(e.g. process affinity per device, or a single shared reader).

##### Files

- `src/weatherbrief/fetch/grib/__init__.py` — orchestration. Replaces
  the in-process call to `decode_ecmwf_pressure_per_point` /
  `decode_icon_chunked` / `_decode_pressure_vars_from_datasets` with a
  pool dispatcher.
- `src/weatherbrief/fetch/grib/decode.py` — entry points
  `decode_ecmwf_pressure_per_point` (line ~1161),
  `decode_ecmwf_surface_per_point` (line ~1403), and the ICON/GFS
  counterparts. These need to be **callable in a fresh interpreter**
  (no module-level state, no shared singletons).
- New module: `src/weatherbrief/fetch/grib/decode_worker.py` — the
  worker entry point + pool factory.
- `src/weatherbrief/fetch/grib/_grib_timer.py` (or wherever
  `_GribTimer` lives) — timing snapshots must survive the process
  boundary; serialise to dict, return alongside the decode result,
  merge in the parent.

##### Design choices

| Question | Options | Recommended | Rationale |
|---|---|---|---|
| Pool model | `ProcessPoolExecutor` inside uvicorn / `multiprocessing.Pool` / separate microservice | **`ProcessPoolExecutor`** with `initializer=` to import cfgrib once per worker | Simplest, persistent workers (no per-decode spawn cost), trivially testable |
| Workers (`max_workers`) | 1 / 2 / `os.cpu_count() - 1` | **2** to start | Matches today's typical concurrent job count (forecast cycle + 1 user); doesn't double RSS |
| What crosses the boundary | Raw GRIB bytes / decoded dict / numpy arrays | Path strings + decode params **in**, decoded result dict **out** | Workers read GRIB from disk themselves (page cache hits), avoid ferrying ~70MB bytes through pickle |
| Worker state | Stateless / cached cfgrib indexes / shared memory | **Stateless**, revisit only if profiling shows index rebuild is hot | `ProcessPoolExecutor` lifecycle does the right thing for free |
| Error propagation | Re-raise / sentinel return / log+None | **Re-raise via futures** | `concurrent.futures.Future.result()` already propagates; existing error handling unchanged |
| Timing/memory observability | Re-implement / pipe back / lose | **Pipe back as part of result dict** | `_GribTimer.snapshot() → dict` then `_GribTimer.merge(snapshot)` in parent. Keeps existing INFO log lines intact |
| GC inside workers | Manual / let process exit reclaim | **Manual `gc.collect()` matching today's pattern** | Workers are long-lived; same memory hygiene as in-process today |

##### Implementation approach

1. **Audit `decode.py` for module-level state.** Anything that breaks
   in a fresh interpreter (e.g. cfgrib lazy index registration, env
   var reads, logging config) needs to either be re-initialised in the
   worker `initializer` or made deterministic.
2. **Refactor `decode_ecmwf_pressure_per_point` and friends** to accept
   serialisable args only (no `xarray.Dataset` instances passed in;
   build them inside). Most should already be close to this shape.
3. **Create `decode_worker.py`** exposing:
   ```python
   def decode_ecmwf_pressure(grib_path: str, params: dict) -> dict: ...
   def decode_ecmwf_surface(grib_path: str, params: dict) -> dict: ...
   def decode_icon_chunked(...): ...
   ```
   Each function imports its real implementation lazily, calls it,
   and returns `{"result": <dict>, "timings": <dict>, "rss_peak_mb": <float>}`.
4. **Build the pool** with `initializer=_worker_init` setting up
   logging + ContextVar defaults + cfgrib import. Singleton in
   `fetch/grib/__init__.py`, lazy-created on first use, lifecycle
   tied to FastAPI app shutdown.
5. **Replace decode call-sites** in `enrich_forecasts` /
   `_enrich_ecmwf_inner` / `_decode_and_merge_icon_eu` with
   `pool.submit(...)` returning a Future. Keep the outer
   ThreadPool that overlaps GFS+ECMWF+ICON branches — that part
   stays in-process and just dispatches futures.
6. **Wire timing/RSS merge**: after each Future resolves, call
   `_grib_timer.merge(future.result()["timings"])` so the existing
   `GRIB timing: ...` line still has the right numbers. Same for
   memory checkpoints.
7. **Worker pool tests** in a new `tests/test_grib_pool.py`:
   deliberately submit two concurrent decodes, assert per-step
   times stay near baseline (no contention).

##### Acceptance criteria

- All existing GRIB tests pass: `pytest tests/test_grib.py
  tests/test_grib_fill.py tests/test_ecmwf_sample.py
  tests/test_ecmwf_sounding.py -q` (the slow `-m slow` ECMWF
  end-to-end class must also pass).
- New `tests/test_grib_pool.py` covers: cold start, two concurrent
  submits, error propagation, worker recycling on crash.
- Numerical equivalence: temporary comparison test asserts each
  decoded result matches the in-process version to within
  `np.allclose(rtol=1e-6)` for all field/point combinations on the
  test flight. Remove before merge.
- **Concurrency win**: a deliberate test that submits the standalone
  forecast cycle's ECMWF decode + a user-refresh ECMWF decode in
  parallel must show per-step `ecmwf_a2_decode` ≤ 1.3× the clean
  single-decode baseline (vs today's 3–6×).
- Existing prod log lines (`GRIB timing: ...`, `GRIB RSS: ...`,
  `Pipeline timing: ...`) continue to emit with reasonable values —
  no regression in observability.
- Memory peak (pipeline-wide) stays under 2.5 GB on the test flight.
  Today's peak with 2 concurrent decodes was 2.6 GB; expectation is
  that pool-internal decode peak is similar to today's in-process
  peak (~270 MB per active decode), and the parent process' working
  set drops because raw GRIB bytes never live in the parent.

##### Expected prod impact

- **No change for single-refresh latency** — a quiet refresh with no
  concurrent forecast cycle sees identical timings (small IPC cost
  offset by no longer holding decoded bytes in two places).
- **Removes the 3–6× per-step inflation under concurrency.** The 7Z
  window stops being the worst case; queued auto-refreshes complete
  ~3× faster when the forecast cycle is running. Headline: the
  worst-case `egjb_lfbp_leal` morning refresh (467s today) drops to
  ~250–280s.
- **No change in p50, big drop in p99** — this is a tail-latency fix.

##### Risk

**High.** Multiprocessing is full of foot-guns:

- **Pickling errors**: cfgrib datasets, lazy file handles, custom
  exceptions — anything that escapes the worker boundary needs to
  pickle cleanly. Audit during step 1.
- **Worker lifecycle**: zombie processes on uvicorn reload, leaked
  file handles on worker crash, deadlocks on shutdown. Use
  `concurrent.futures.ProcessPoolExecutor` (not raw `multiprocessing`)
  and explicitly call `pool.shutdown(wait=True)` in the FastAPI
  lifespan handler.
- **Logging context**: workers don't inherit ContextVar values
  (request_id, user_id). Pass relevant context as part of the
  decode params and re-set in the worker `initializer`.
- **Memory accounting**: each worker has its own RSS. The
  `_peak_rss_mb()` line currently reports just the parent — needs
  to either aggregate worker RSS or be relabelled "parent RSS".
- **macOS dev environment**: `fork` start method is unsafe with
  some libraries (libdispatch); use `spawn` everywhere for parity
  with Linux prod.
- **Order with B-1'**: B-1' shrinks per-step decode from ~7s to
  ~3.5s. With B-3's pickle/IPC cost being a fixed ~50-150ms, the
  overhead ratio rises from 2% (today) to 3-4% (post-B-1') — still
  fine but worth measuring. **Land B-1' first**, then this.

##### Out of scope

- ICON download parallelisation across variables (`O-1` in parking
  lot) — separate concern, not a GIL issue, can ship independently.
- Forecast-cycle scheduling fixes (move to 7:30Z, sentinel-wait,
  process mutex) — those are short-term mitigations for the same
  pathology this brief structurally fixes; they remain valuable as
  defence-in-depth and ship on their own track.
- Disk I/O optimisation — gated on the iostat measurement above.

---

#### GIL-bound decode — prod evidence (2026-05-02)

This section captures the empirical case that motivates Phase B-3,
preserved here so future agents can verify the model.

**Setup**: 7Z auto-refresh window. Standalone forecast cycle started
at 07:00:11 UTC fetching forecasts for 619 airports across 3 models
(GFS + ICON + ECMWF). Auto-refresh scheduler picked up 3 due flights
at 07:07:43 UTC. The forecast cycle's ECMWF GRIB phase
(~07:16 → 07:35 UTC) overlapped fully with all three user refresh
ECMWF a2 decodes.

**Per-step ECMWF a2 decode times during overlap**:

| Refresh | `ecmwf_a2_decode` per step | vs clean baseline (6.9s) |
|---|---|---|
| Flight #1 (during heaviest overlap) | 42.7s | 6.2× |
| Flight #2 | 20.5s | 3.0× |
| Flight #3 | 21.2s | 3.1× |

**Droplet metrics during the same window** (via `~/.claude/skills/droplet-metrics/`):

```
CPU %:    peak= 45.6%  avg= 32.2%   (4 vCPUs → ~2.7 cores idle)
Load 1m:  peak=2.56  avg=1.37        (sat threshold = 4)
Memory:   peak_used= 4367MB (53%)
FS /mnt/flyfun_data    used_peak= 64.4%  size= 148.6GB
Net in:   peak= 31.86 Mbps  avg= 8.57 Mbps
```

**Inference**: with 2.7 cores idle, load 1m at 1.37, no memory
pressure, and modest network — there is no system-level resource
the decode could be waiting on. The slowdown must come from
intra-process serialisation. Python's GIL is the only mechanism
that produces this signature on a CPython interpreter running
multiple cfgrib + numpy decode loops concurrently.

This finding has been saved to memory at
`feedback_grib_decode_gil_bound.md` so future debugging starts
from this insight rather than rediscovering it.

**Disk I/O note**: `disk_read`/`disk_write` aren't exposed via DO's
v2 monitoring API (404). To rule out disk as a co-factor, run
`iostat -xm 5 360` on the droplet during the next 7Z window
(start at ~06:25 UTC, runs 30 min) and inspect `await`, `r/s`,
`%util` per device. If they look idle, GIL is the only story; if
high, B-3's design needs to consider per-device dispatch as well.

---

### Phase C — deferred ICON memory streaming

After Phase B-1 + B-2 land. Memory peak is comfortably 1.6 GB on prod
post-upgrade, so this is no longer a correctness/safety issue — pure
speed/headroom optimisation. Deferring until we see if a future
hardware regression or workload change makes it necessary again.

If/when needed:
1. Refactor `_decode_and_merge_icon_eu` so it loads `var_bytes[var]`
   from disk one variable at a time into the chunked decoder, frees
   it, moves on.
2. Possibly stream per-fhour to ensure decoded points from fhour N
   are released before fhour N+1 starts loading.

### Combined success criteria after Phase B-1 + B-2

- All existing tests pass on each branch independently.
- Local profile shows `phase1_parallel` lower (B-1) and
  `route_analysis` lower (B-2), additively.
- Prod refresh of a long-range European flight under 4:30 total
  (was 5:51).
- Memory peak unchanged (no regression).
- Both branches mergeable to main in either order without conflict.

## Other optimisation candidates (parking lot)

Captured here so we don't lose them. Sequenced **after ICON Phase C** —
some only become worth doing once ICON download stops being the long pole.

### O-1. Parallelise ICON download across variables (–60 to –80s, after Phase C only)

`fetch_icon_eu_per_variable` loops variables sequentially; each variable
does 40 parallel downloads then waits before the next variable starts.
A 360-file ThreadPoolExecutor (max_workers ~16–24) would shrink ICON
prefetch from ~110s to ~30s — **but** memory peak goes up if not paired
with the streaming decode from Step 2(b). This is the natural follow-on
once 2(b) lands.

Risk: DWD rate limiting. Test with 16 workers first.

### O-2. ~~Parallelise ECMWF a2 decode steps~~ → promoted to Phase B

Was parked here under the assumption that ICON download was the long
pole (true on local macOS, false on prod). Prod data showed ECMWF a2
takes 123s on prod, dominating Phase 1. Promoted out of parking lot,
see Phase B above.

### O-3. ~~Vectorise MetPy~~ → promoted to Phase B-2

Local data understated the prod impact. After Phase A measured
`route_analysis = 106s` on prod (vs 19s local), MetPy vectorisation
became the second-biggest single optimisation available. Promoted to
parallel implementation track in Phase B above.

### O-3 (original, kept for reference)

`analysis/sounding/thermodynamics.py:319` (and `:162`). 8.43s of self-time
in `metpy.xarray.py:1285` — that's the `@parse_grid_arguments` decorator
converting Python lists → pint Quantity → xarray on every call. Hoist
that conversion outside the per-(point, model) loop and call MetPy once
per batch.

Risk: medium. Need to verify thermodynamic outputs match exactly.
Existing tests in `test_clouds.py`, `test_convective.py`, `test_comparison.py`
should catch regressions; a numerical-equivalence test would help.

Independent of all GRIB work — can land in parallel with ICON Phase C
if convenient.

### O-4. Overlap GRAMET + LLM digest (–3s)

`pipeline.py` sections 5 (GRAMET), 7 (LLM digest) are sequential and
share no state. Both are HTTP I/O. ThreadPoolExecutor or asyncio.gather
saves the GRAMET 3s by overlapping it with the LLM 38s.

Trivial change. Ship anytime.

### O-5. LLM digest streaming (deferred, UX change)

LLM digest is 30–40s of HTTP wait at the very end. Anthropic API supports
streaming — could begin rendering partial digest as tokens arrive. Changes
UX semantics (digest text shown progressively) and would need frontend
work for SSE. Defer until everything else is exhausted.

## Things to investigate later (not yet ranked)

These came up during the read-through but aren't load-bearing right now:

- **Why ECMWF a2 decode takes 2.7s per step (10 steps × 25 levels each).**
  Most of the time is presumably cfgrib opening the file + reading the
  multi-grid (Europe + Nordic) sub-datasets. Worth checking if a single
  open + multi-grid extraction would be faster than the current
  per-grid-loop pattern.

- **`gc.collect()` real cost** — measured at 1.36s in Run 1 (8 calls)
  and 3.47s in Run 3 (20 calls). Modest but not zero. The user
  confirmed they're load-bearing for memory; we keep them. Worth
  remembering in any memory-related work.

- **Spatial interpolation cost** — `analysis/spatial_interpolation.py`
  was logged but not timed: "filled 300 (point, level) CLW/ICMR gaps,
  filled 15 cloud diagnostics gaps". Probably small; should be timed
  inside route_analysis.

- **Cross-section .json size on disk** — separate from speed, but worth
  checking how big `cross_section.json` lands as briefing.json+forecasts.json
  scale up. May tie into account-deletion tests.

- **Open-Meteo per-model parallelisation** — 3.1s today, not worth
  parallelising (the savings would be ~2s). But if more models are
  added (e.g. UKMO once it's stable), revisit.

- **Profile pyinstrument with `async_mode='enabled'`** — current runs use
  `disabled` so we only see main-thread frames. The GRIB worker threads
  are reduced to `lock.acquire`. The new sub-stage timing data already
  covers this gap, but a thread-aware profile would be useful if we
  hit something unexpected during Phase B.

## Open questions before Phase A deploy

1. The Phase A instrumentation adds ~10 INFO log lines per refresh (memory
   checkpoints + GRIB timing summary). Acceptable for prod? They're
   bounded, not per-route-point.

2. Confirm the 4 cycle/horizon constants live in `icon_eu_fetch.py`
   only — yes (confirmed; no other module re-exports them). Reverting
   them is a single-file diff.

## Verification plan (across phases)

- Phase A: deploy. Verify GRIB timing log appears, memory checkpoints
  produce sensible numbers, no behaviour change vs prior. ≥10 prod
  refreshes captured before next change.
- Phase B: local profile shows peak ≤ 1.5 GB on macOS for the test flight,
  total time within 10% of pre-change Run 3 (we expect ~210s with same
  ICON download cost; 2(b) is memory-only, not speed).
- Phase C: deploy. First 5 prod refreshes:
  - peak RSS ≤ 2 GB
  - ICON enrichment hourly count climbs (no fhour-level all-failed
    warnings except where ICON-EU domain genuinely doesn't cover)
  - GRIB timing summary shows expected shape (icon_prefetch and
    phase2_icon_decode both populated; no `404=` failures except
    network blips)

## Files touched (running list)

- `src/weatherbrief/fetch/grib/__init__.py` — instrumentation already in.
- `src/weatherbrief/fetch/grib/icon_eu_fetch.py` — improved 404 logging
  in. Cycle/horizon constants change **needs to be reverted for Phase A**.
- `scripts/profile_refresh.py` — driver script; new file, kept.
- `designs/plans/refresh-pipeline-performance.md` — this document.
