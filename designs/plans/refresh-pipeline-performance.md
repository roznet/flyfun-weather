# Refresh pipeline performance investigation

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

### Phase A — instrument and observe (zero behaviour change)

Goal: measure prod memory and timing in detail before making any change
that affects what data flows through the pipeline.

Ship as one PR/deploy:

1. ✅ Sub-stage timing (`_grib_time` + `_grib_time_summary`) — already in
   local tree.
2. ✅ Improved 404 logging (`_format_failure_summary`) — already in local
   tree.
3. ⏳ Add memory checkpoints in `fetch/grib/__init__.py`:
   - start/end of `enrich_forecasts`
   - start/end of `_prefetch_icon_eu_data` (per fhour aggregate)
   - inside `_decode_and_merge_icon_eu` (per fhour, before/after decode)
   - inside `decode_icon_eu_per_point_chunked` (per variable)
4. ⏳ **Revert the cycle/horizon constants** in `icon_eu_fetch.py` for
   this phase. Prod continues to silently 404 on long-range short cycles,
   but we get visibility before behaviour changes.

Deploy. Watch ~24h of prod refreshes. Capture:
- per-stage timings under prod network conditions (DWD will be faster)
- exact per-fhour memory delta during ICON decode
- 404 mix (`404=` vs `network=`) to see what actually fails today

### Phase B — design + verify Step 2(b) locally

Once Phase A data is in:

1. Refactor `_decode_and_merge_icon_eu` so it loads `var_bytes[var]`
   from disk **one variable at a time** into the chunked decoder, frees
   it, moves on. The compressed bytes are already cached on disk — we're
   just changing the in-memory shape from "load-all-9-variables-then-decode"
   to "load-one-decode-free-loop".
2. May need `decode_icon_eu_per_point_chunked` to accept a callable or
   generator instead of a dict.
3. Local profile run with cycle fix + streaming. Confirm peak RSS drops
   from 3.3 GB to ≤1.5 GB on macOS. (Linux will be lower.)
4. If still too high, also stream per-fhour to ensure decoded points
   from fhour N are released before fhour N+1 starts loading.

### Phase C — combined deploy

Ship together:
- Cycle/horizon fix (`{0, 6, 12, 18}`, `48`)
- Step 2(b) memory streaming refactor
- Phase A instrumentation still in place to verify prod memory stays
  under 2 GB peak with full ICON working

Watch first few refreshes carefully. Rollback path: revert cycle fix
(line-level revert restores Phase A behaviour).

### Step 2(b) success criteria

- ICON enrichment fully complete on long-range flights (no `_failed`
  warnings for fhours within the real horizon).
- Prod peak RSS stays ≤2 GB on a D+3 European refresh.
- Pipeline total ≤ ~150s on a D+3 refresh (we accept the ICON cost
  is real and unavoidable; targeting under that with later phases).

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

### O-2. Parallelise ECMWF a2 decode steps (–15s, after ICON optimised)

`_enrich_ecmwf_inner` decodes 10 steps sequentially (~3s each).
ThreadPoolExecutor with workers=2 would halve that. Worth nothing right
now because ICON download (~110s) is the long pole; ECMWF (~35s) hides
inside that overlap. Becomes worthwhile once ICON drops below ~30s.

Memory: each cfgrib decode peaks ~270 MB → workers=2 means ~540 MB peak.
OK with current 3 GB cap. workers=4 would breach.

### O-3. Vectorise MetPy in `compute_derived_levels_extended` (–8 to –12s)

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
