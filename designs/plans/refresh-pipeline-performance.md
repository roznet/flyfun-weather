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

### Phase B — ECMWF a2 decode parallelism (highest-value prod win)

**Reordered after Phase A prod data**: ECMWF a2 decode is 123s on prod
(80% of Phase 1) and parallelisable with negligible memory risk. Lands
ahead of ICON streaming because:
- Bigger prod win (~–56s on phase 1 wall vs ICON streaming's ~–500 MB peak)
- Independent of the ICON cycle bug — can ship without touching ICON
- Memory cost is bounded (workers=2 → ~540 MB peak, well under 3 GB cap
  even on macOS where Linux glibc reclaim doesn't help us)
- Existing instrumentation already measures the relevant metrics — we
  ship this and immediately see the wall-time delta in prod logs

Concrete changes in `_enrich_ecmwf_inner`:
1. Replace the `for step_hours, parts in sorted(files_by_step.items())`
   loop body with `ThreadPoolExecutor(max_workers=2)` submissions of
   per-step work.
2. **Decode** in parallel; **merge** (`_replace_pressure_levels_from_grib`,
   `_apply_ecmwf_surface_to_hourly`, surface step-difference state)
   stays single-threaded — those mutate shared cross-section state.
3. Use `_submit_with_context` so per-step `_grib_time(...)` calls land
   on the parent's timer.
4. Verify prod numbers: target `phase1_parallel ≤ 80s` (was 150s).

### Phase C — ICON memory streaming + cycle/horizon fix

After Phase B's prod data confirms the new baseline, attack ICON.

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

Ship together with:
- Cycle/horizon fix (`{0, 6, 12, 18}`, `48`)
- Phase A + B instrumentation still in place to verify prod memory
  stays under 2 GB peak with full ICON working

Watch first few refreshes carefully. Rollback path: revert cycle fix
(line-level revert restores pre-fix behaviour).

### Phase C success criteria

- ICON enrichment fully complete on long-range flights (no `_failed`
  warnings for fhours within the real horizon).
- Prod peak RSS stays ≤2 GB on a D+3 European refresh.
- Pipeline total ≤ ~120s on a D+3 refresh (Phase B should already
  put us at ~150s; Phase C trades that for full ICON enrichment
  without further regression).

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
