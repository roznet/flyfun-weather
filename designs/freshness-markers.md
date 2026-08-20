# Freshness Markers

> Marker-based per-(model, source) decision system for "is this pack stale?" — replaces per-call Open-Meteo `meta.json` fan-out with an in-memory marker store gated by a hardcoded schedule registry.

## Intent

The old freshness path (`fetch/model_status.check_freshness`) called Open-Meteo's `meta.json` for **all 5 models on every freshness check**, and never considered direct-GRIB sources at all. Three concrete problems addressed by this redesign:

1. **Wasted HTTP** — N polling clients × M endpoints per freshness call.
2. **Wasted refreshes** — auto-refresh fired before our 00Z ECMWF GRIB had landed (ready ~06:40Z, fired at 07:07Z), refreshing against stale data.
3. **Direct ECMWF GRIB freshness was invisible** — packs recorded `grib_init_times["ecmwf"]` but `_build_data_status` only consulted `model_init_times` (Open-Meteo).

What should NOT change:
- The pure-compute property: `now < pack.next_expected_update` ⇒ fresh, no I/O. Most freshness HTTP calls are a dict lookup, not a network round-trip.
- The marker store stays **in-memory only**. No DB persistence — bootstrap from registry on restart (~2s of inline-fallback cost). (Issue #515 added a durable `model_delivery_log`, but that is an append-only *measurement* record, not marker state: restart behaviour is unchanged.)
- Per-(model, source) granularity. The same logical model can be sourced multiple ways (`ecmwf:direct` vs `ecmwf:openmeteo`), and which one a pack used is a per-pack property — recorded as `pack.model_sources`.

## Architecture

Four modules under `src/weatherbrief/fetch/freshness/`:

| Module | Purpose |
|---|---|
| `registry.py` | `SourceConfig` dataclass, `SOURCE_REGISTRY` dict (11 source/model pairs: `ecmwf:direct`, `gfs:noaa`, `hrrr:noaa`, `icon_eu:dwd`, `icon_d2:dwd`, and six `*:openmeteo`), pure functions: `next_run_after`, `next_cycle_init_after`, `run_horizon`, `cycle_init_for`, `expected_delivery_for_init`, `initial_marker_for`, plus horizon-query helpers `max_horizon`, `next_full_horizon_run`, `first_full_coverage` (used by the digest long-range gate to find the regime boundary and the date high-res GRIB first covers a flight — see [digest.md](./digest.md)). **Single source of truth** for descriptive fields too (`model_label`, `provider_label`, `provider_url`, `role`, `resolution`, `coverage`, `pressure_levels`, `description`) — consumed by the freshness popover, the public data-sources endpoint, and the help-page table. |
| `markers.py` | `Marker` dataclass + `MarkerStore` (asyncio-locked, singleton via `get_store()`). Records `(cycle_init, arrival_wallclock)` observations in a maxlen=100 deque. `Marker.last_probe_at` is the last *successful* dynamic check — distinct from the `last_check` heartbeat, which every tick bumps. |
| `sources.py` | Unified `check_source(source, model)` dispatch wrapping existing helpers (`grib_fetch.find_latest_run_with_response` for GFS/NOAA, `hrrr_fetch.find_latest_hrrr_run_with_response` for HRRR/NOAA, `icon_eu_fetch.find_latest_icon_eu_run_with_response` for both ICON-EU and ICON-D2 (`variant=ICON_D2`), `ecmwf_watcher.get_latest_ready_with_mtime` for ECMWF/direct, `model_status.fetch_model_metadata` for `*:openmeteo`). The `_with_response` variants exist so the dispatch can pull `published_at` from the same HEAD's `Last-Modified` header. Each dispatch returns an `Observation(init, published_at, data_end, observed_via)` — `published_at` is the provider's `Last-Modified` header for HTTP sources (NOAA, DWD), the OM `last_run_availability_time` for `*:openmeteo`, and the sentinel-file mtime for ECMWF direct (no central publish wallclock); `observed_via` names which of those three clocks it was. |
| `catalog.py` | Pure read-only merge of `SOURCE_REGISTRY` (static description + schedule) and `MarkerStore` (live `latest_init`, `published_at`, `next_expected`, `horizon_end`, `marker_health`). Backs `GET /api/data-sources` — the public catalog endpoint that drives the help-page Data Sources & Models table. |

Plus one storage module outside the package: `storage/model_delivery.py` — the durable delivery log (below).

The 5-min loop lives in `scheduler.run_freshness_loop` — bootstraps the store, then on each tick: for every `(source, model)` whose `next_expected` has passed, run `check_source` (offloaded to a thread), call `store.update`. Most ticks no-op.

The HTTP-side check is `api/packs._build_data_status(pack, flight)`:

```
for (model, source) in pack.model_sources or _backfill_sources(pack):
  marker = store.get_sync(source, model_for_source(source))
  if marker is None or marker.is_stale(loop_interval):
    marker_health = "suspect"
    marker = inline-fallback dynamic check
  pack_init = grib_init_times[m] if source ends in :direct/:noaa/:dwd else model_init_times[m]
  horizon = registry.run_horizon(source, marker.init)
  stale iff marker.init > pack_init AND (marker.init + horizon) >= flight_end
```

Horizon-awareness matters for ICON-EU: a new 15Z intermediate run (78h) won't replace a 12Z main run (120h) for a flight needing 100h coverage.

**A pack-model slot can be served by a substitute source.** `hrrr:noaa` serves the `gfs` slot when the whole route fits CONUS and the window is inside the run's horizon; `icon_d2:dwd` serves the `icon` slot on short central-European routes (#456/#457). So `pack.model_sources["gfs"]` may read `"hrrr:noaa"` — the marker is then keyed `("hrrr:noaa", "hrrr")` via `model_for_source`, and the pack init comes from `grib_init_times["gfs"]`. Freshness therefore compares the *substitute's* schedule/horizon against the slot's recorded run, which is what you want: a new HRRR extended cycle should invalidate an HRRR-enriched pack, not a GFS one.

**From-scratch packs.** A pack built when every model was out of horizon (pending-coverage future flights) records no model data at all, so `_backfill_sources` returns `{}` and there is nothing to gate against. `_build_data_status` then falls back to `_SCRATCH_CANDIDATES` — the longest-horizon source per model (`gfs:noaa` 16d, `ecmwf:openmeteo` 10d, `icon:openmeteo` 7d) — and marks a model `stale` as soon as a *covering* run exists, so the gate runs a full from-scratch refresh once the flight comes into range while still returning `none` (nothing eligible) before that.

## Usage Examples

```python
# Read marker state from anywhere (sync, no lock):
from weatherbrief.fetch.freshness.markers import get_store
m = get_store().get_sync("ecmwf:direct", "ecmwf")
if m.is_stale(loop_interval=timedelta(seconds=300)):
    ...  # heartbeat suspect — fall back to inline check

# Run freshness check for a pack (used by /packs/freshness + auto-refresh):
from weatherbrief.api.packs import _build_data_status
status = _build_data_status(pack, flight)  # DataStatus with per-model state
if not status.fresh:
    # at least one source has newer data covering the flight horizon
    schedule_refresh()

# Inspect calibration via admin endpoint:
# GET /api/admin/freshness/markers
# Returns per-marker observations (with delay_s) and per-cycle-hour
# calibration: count / median_delay_s / p90_delay_s / configured_offset_s /
# drift_p90_vs_config_s.
```

## Delivery Log (issue #515)

`model_delivery_log` is the **realised** counterpart to the registry's *expected* `delivery_offset`. One row per observed model run, appended by `scheduler._run_freshness_check_once` whenever a marker advances. **Collect-only — nothing reads it yet.**

| Column | Meaning |
|---|---|
| `source` / `model` | Registry key (`"ecmwf:direct"`) and its denormalised prefix (`"ecmwf"`), so "all GFS" is a `WHERE`, not a string split |
| `cycle_init` | The run being measured |
| `expected_at` | The registry's prediction, **frozen at observation time** |
| `published_at` | Provider-reported publish wallclock — the actual measurement (nullable) |
| `detected_at` | When our loop noticed |
| `last_absent_at` | Last dynamic check that did *not* yet see this run (nullable) |
| `observed_via` | `sentinel_mtime` \| `http_last_modified` \| `om_meta` |

Everything interesting is derived on read: `drift = published_at − expected_at`, `poll_lag = detected_at − published_at`, `cycle_hour = HOUR(cycle_init)`.

Why the schema looks like this — each of these is easy to "simplify" back into the bug it fixes:

- **`expected_at` is stored, not derived.** The point of the table is to *change* the offset constants. Recomputing expectation at read time would collapse every historical drift to ~0 the moment we recalibrated, erasing the record of what was wrong.
- **`published_at` is distinct from `detected_at`.** Conflating them is the pre-existing bug: `MarkerStore.update()`'s deque records `(observed_init, now)`, i.e. *our detection*, which the 300 s poll quantises. Prod showed three different sources sharing an identical `arrived_at` and `drift=+159s` — poll lag, not lateness. Worse, the loop skips the check while `now < next_expected`, so **early arrival is unobservable** in that record: 0 negative drifts in 7 days, while the timeline (reading the provider's own `published_at`) correctly showed ICON and UKMO ~60 min early.
- **`observed_via` is recorded, not assumed uniform.** A sentinel mtime is when *our* watcher finished writing after rsync; an OM `last_run_availability_time` is the provider's own record; an HTTP `Last-Modified` is the origin file's. Different systematic biases — pooling them silently would be sloppy. Each `sources._DISPATCH` wrapper declares its own on the returned `Observation`, so a new source can't inherit the wrong one.
- **`last_absent_at` brackets the truth.** `(last_absent_at, published_at]` bounds arrival when `published_at` is null, and cross-checks it when it isn't (`published_at < last_absent_at` means the provider's timestamp contradicts what we observed). It comes from `Marker.last_probe_at` **snapshotted before `update()`** — the heartbeat `last_check` is useless here, since the loop bumps it on every tick including no-I/O ones.

Decisions worth not relitigating:

- **Written from the scheduler, not `MarkerStore.update()`.** The marker modules are deliberately I/O-free; the scheduler already holds the pre-update snapshot, the registry expectation, and the instrument. The insert is offloaded to a thread and is best-effort — a DB hiccup costs one calibration sample, never a freshness tick.
- **Missed runs are reconstructed, not stored.** A skipped cycle never advances the marker, so no row appears. Find the hole by joining against the expected cycle grid. *Store facts about observations, derive facts about the schedule* — a synthetic "missing" row would also assert a schedule we might later stop believing.
- **No backfill.** The `Marker advanced` journald lines carry detection time only; seeding from them would pollute the calibration set with a lesser measurement.
- **No retention policy, no aggregation.** ~50 rows/day ≈ 18k/yr ≈ 2–3 MB/yr, three orders of magnitude below `verification_scores`. Aggregating would destroy the primary use case: "what time is safe on the bad days" is a P95/P99 question, and percentiles cannot be reconstructed from stored min/max/median. Revisit only if we ever log every *check* rather than every *advance* (60× the volume).
- **`UNIQUE (source, cycle_init)`, first write wins.** A restart re-bootstraps markers, so the same run can legitimately be advanced onto twice; the first observation has the tighter bracket.

Not in v1, in rough order: the timeline reading the log (draw an observed-lag band per source, gated on having enough points — a band over 3 samples is worse than the current dot); the registry consuming the measured distribution as per-cycle-hour offsets (`_OM_GFS_OFFSET` is one number but GFS 00Z and 06Z differ by ~80 min — and if it ever *auto*-consumes, `expected_at` will need to record which calibration produced it); recalibrating the current constants once a few weeks of signed drift exist.

## Tiered Refresh Gate (issue #167)

`_build_data_status` answers "is anything stale?" with a **min-rule** — one newer covering run flips `fresh=False`. That's the right signal for the auto-refresh *scheduler's* freshness loop, but it makes the **manual refresh button** wasteful: any single model tick triggers a full token+compute+disk refresh, even when one model update can't move a multi-day-out picture. `decide_refresh` (in `api/packs.py`) layers a lead-time-aware gate on top of the same per-model states.

It is a **pure function of the `DataStatus`** that `_build_data_status` already computes (no extra I/O):

```python
from weatherbrief.api.packs import gated_data_status
status = gated_data_status(latest_pack, flight)   # the only entry point — see below
decision = status.refresh_decision
# decision.mode ∈ {"full", "realtime", "none"}; .reason; .eta_useful; .needed/.n_eligible/.n_updated
```

`decide_refresh` itself stays public and is the pure layer described here, but
**production code must not call it directly** — `gated_data_status` is the
boundary that also applies the parameter-change override below.

- `n_eligible` = models whose **latest available run covers the flight horizon** (`ModelStatus.covers_horizon`, set from the same `(init + run_horizon) >= flight_end` test used for staleness).
- `n_updated` = eligible models with a newer-than-pack covering run (== `len(stale_models)`; `state == "stale"`).
- `needed = min(threshold[days_out], n_eligible)` with **threshold `{>=2: 3, 1: 2, 0: 1}`** (module constants `_REFRESH_THRESHOLD_*`, not env-tunable for v1).
- mode: `full` if `n_updated >= needed`; `realtime` if `days_out == 0` and not full (a D-0 press always at least pulls fresh METAR/TAF); else `none`.
- `eta_useful` (for `none`/`realtime`) = the `(needed − n_updated)`-th soonest `next_expected` among not-yet-updated eligible models — i.e. when the threshold will next be crossed.
- `pending_models` = the not-yet-updated eligible models, soonest-first — the runs the user is waiting on. Drives the freshness bar's "awaiting {models}" hint.

The `min(…, n_eligible)` cap handles small selections: 2 models selected → D-2/D-1 both need both; 1 model → every press runs. (Default selection is the 3 mains, so "count ≥ 3" == "all mains" for nearly everyone.)

**Wiring:**
- Both manual-refresh endpoints (`refresh_briefing`, `refresh_briefing_stream`): `full` → run the pipeline (unchanged); `realtime` → run `tasks/route_weather.run_realtime_refresh` and return updated observations; `none` → 200 / SSE-complete no-op carrying `reason` + `eta_useful`.
- Auto-refresh scheduler (`scheduler._auto_refresh_one`): same **full/none** policy, but **no** realtime fallback — live METAR/TAF is the verification loop's job. So a non-`full` decision means skip.
- `force=true` (admin) still bypasses the gate entirely.

**The gate knows about model runs, not about the flight (issue #552).** `decide_refresh` is a pure function of *model-run* freshness, so it cannot see that the flight's own parameters changed. Right after a departure-time, duration, altitude or route edit no new run exists → `n_updated == 0` → `mode != "full"` → both endpoints return the **old pack** as `complete`, while the `PATCH` that preceded it has just answered `refetch_needed`. The client then reports "Briefing regenerated" over a briefing computed for the previous departure time (and `force=true` is admin-gated, so nothing could work around it).

`briefing_packs.flight_params_hash` closes that. Every pack is stamped at persist time (`_build_pack_meta` → `storage.flights.compute_flight_params_hash`) with a hash of the flight's **route + departure instant + altitude + ceiling + duration**; `apply_params_change_override` wraps the `decide_refresh` call on both endpoints and forces `mode="full"` when the stamp differs from the flight's current hash.

- Stateless and self-healing — no edit bookkeeping, and an edit made on another device or via MCP is caught just the same.
- Broader than `_compute_flight_id`'s tuple on purpose: that one hashes the time-of-day and the route *slug*, so a mid-route waypoint insert and a same-date time nudge both keep the flight ID while changing the forecast.
- Deliberately excludes profile / aircraft / Flexibility: they don't change the weather that is fetched, and a profile change already invalidates via `advisories_only`.

**One entry point: `gated_data_status` (issue #558).** The override was originally *opt-in* — every caller had to remember to wrap `decide_refresh` in it — and three of the six call sites only became correct via follow-up commits after the fact. The failure mode is silent (a pack built for the previous departure time, returned as up to date, no error anywhere), so nothing catches it but a pilot noticing. `api/packs.py:gated_data_status(latest, flight, *, now=None, params_override=True)` now bundles `_build_data_status` + `decide_refresh` + the override and returns the `DataStatus` with `refresh_decision` populated. All six sites go through it:

| Caller | Purpose |
| --- | --- |
| `GET /packs/freshness` | what the freshness bar shows |
| `refresh_briefing` / `refresh_briefing_stream` | the manual refresh button |
| `api/agent.py:_freshness_dict` | in-process mirror of `/packs/freshness` |
| `tasks/refresh_resume.py:decide_resume` | is an interrupted job worth resuming |
| `scheduler._auto_refresh_one` | the scheduler's pre-execution re-check |

`params_override=False` is the single deliberate opt-out, taken by the **routine** scheduler cycle only (`triggered_by != "resume"`): an edit there is already followed by a client-driven refresh, so forcing a full run per edited flight would just buy a second one. Making it an argument keeps that exclusion visible at the one site that wants it rather than an invisible omission at the five that don't. `tests/test_refresh_gate_boundary.py` holds an AST-based guard test that fails if any module outside `api/packs.py` calls `decide_refresh` or `apply_params_change_override` directly.
- **NULL means "don't know", not "changed"** — packs predating migration 088 have no stamp, and forcing a full refresh for each of them on its owner's next press would be a stampede for no benefit. The model-freshness decision stands for those.
- The auto-refresh scheduler is deliberately *not* wired to this: its question is "has new model data landed?", and a parameter edit is already followed by a client-driven refresh.

**Freshness UI agrees with the button.** `GET /packs/freshness` attaches the decision as `DataStatus.refresh_decision` (and the gated SSE complete carries it on the returned pack's `data_status`), so the client never re-derives gate logic. The freshness bar (`web/ts/managers/briefing-ui.ts:renderFreshnessBar`) renders by mode:
- `realtime` (D-0) → "day of flight — refresh updates live METAR/TAF" (a press is always useful).
- `none` → **one consistent "Up to date" line at every stage**: "Up to date · next full refresh in ~{eta_useful} (awaiting {pending_models})". It answers *when* a full refresh becomes worthwhile (the run that crosses the threshold) and *what* we're waiting on (the not-yet-updated models), so the wording doesn't lurch as runs trickle in — the `awaiting` list just shrinks. Deliberately **not** keyed on `n_updated`: whether 0 or 2 of 3 models have ticked, the message and styling stay the same; only reaching the threshold flips it.
- `full` → falls through to the red "updates available" stale wording — the one actionable state where a press runs the pipeline.

Without this, the bar's raw min-rule `fresh` flag disagreed with the button (showed "updates available" while a press did nothing), and an earlier iteration that switched to a separate "minor updates (n/needed)" line read as a jarring state change versus the plain "next update in ~X" — replaced by the single `awaiting` framing above.

## Key Choices

- **Marker state stays in-memory only.** Lost on restart, ~2s bootstrap cost. Admin endpoint provides debugging visibility. The one durable thing is the delivery log above — an append-only measurement record, not marker state, so a restart still re-bootstraps from the registry exactly as before.
- **`marker_health="ok"` requires an actual observation.** `is_stale` measures time since the last check *attempt*, and the loop bumps that on ticks that do no I/O — so a source whose dynamic check has always failed used to report a green `"ok"` while serving nothing but registry guesses (`gem:openmeteo` in prod: `latest_init` ~36h stale, `published_at` null, health `"ok"`). `Marker.last_probe_at` records when `check_source` last *succeeded*; the catalog reports `"unobserved"` until it has.
- **Source key prefix → model name.** `("icon_eu:dwd", "icon_eu")` vs `("icon:openmeteo", "icon")` — the marker store key is `(source, source.split(":", 1)[0])`. Pack-side may map the same logical pack-model name (`"icon"`) to either source; `model_for_source()` strips the suffix to find the marker.
- **Min-rule for staleness.** If *any* source has new data covering the flight, the pack is stale. Avoids per-model weighting heuristics. If a refresh fires for a less-useful source, it just falls back to the previous source — no harm.
- **Horizon-aware.** Without this, a 78h intermediate ICON-EU run would wrongly invalidate a 120h main-run pack for long-haul flights.
- **Exponential slip backoff.** `slip_bump(n) = min(retry_interval × 2^(n-1), max_retry_interval)`. Default base 10min, cap 1h, 8 slips → ~6h before cycle-jump. Replaces uniform `retry_interval × 12 = 2h` cap that hammered slow-publishing OM endpoints.
- **`(cycle_init, arrival_wallclock)` observations.** Stored as tuples in a 100-entry deque per marker. Lets the admin endpoint compute per-cycle-hour median/p90 delay vs. registry expectation, surfacing drift without needing log retention or a DB table.
- **`Marker.published_at` distinct from `arrival_wallclock`.** `published_at` is the provider-reported time the run became downloadable (HTTP `Last-Modified` or OM availability field); `arrival_wallclock` is when *we* observed it. The frontend per-source freshness popover surfaces `published_at`, and the admin endpoint exposes both — the gap measures our polling lag, separately from provider drift.
- **Pack `model_sources` recorded at enrichment time, backfilled at read time.** Legacy packs missing the column infer source from `grib_init_times` presence (a model in `grib_init_times` → direct; otherwise OM).

## Patterns

When adding a new (source, model) pair:
1. Add a `SourceConfig` to `SOURCE_REGISTRY` with **both** schedule fields (`cycles`, `delivery_offset`, `horizon`, `readiness_check`) **and** descriptive fields (`model_label`, `provider_label`, `provider_url`, `role`, `resolution`, `coverage`, `pressure_levels`, `description`). The catalog test (`test_data_sources_catalog.py`) enforces that descriptive fields are populated.
2. Add a wrapper to `sources._DISPATCH` mapping the readiness_check symbol → callable returning an `Observation`. Set its `observed_via` to the `VIA_*` constant naming the clock the `published_at` actually came from — the delivery log keeps those instruments apart, and a default would silently pool incomparable measurements.
3. Make the enrichment path report the key it actually used: `fetch/grib/__init__.py:enrich_forecasts` returns `grib_sources` (pack-model → source key), which `_finalize_refresh` prefers when stamping `model_sources`. `_DIRECT_SOURCE_KEYS` in `api/packs.py` is only the static fallback for legacy results and for `_backfill_sources` on pre-#108 packs — extend it too if the new source is the *default* for a pack-model, but a substitute variant (HRRR, ICON-D2) belongs in `grib_sources`, not there.

The help-page table and the freshness popover both render from the same registry — no separate copy to keep in sync.

## Public Data-Sources Endpoint

`GET /api/data-sources` returns the full catalog as JSON. Response shape:

```jsonc
{
  "sources": [
    {
      "key": "ecmwf:direct",
      "model": "ecmwf",
      "model_label": "ECMWF IFS",
      "provider_label": "ECMWF",
      "provider_url": "https://www.ecmwf.int/",
      "role": "primary-sounding",
      "resolution": "0.25° (~25 km)",
      "coverage": "Europe + US",
      "pressure_levels": 25,
      "description": "...",
      "cycles": [0, 6, 12, 18],
      "horizon_hours": {"0": 168.0, "6": 144.0, "12": 168.0, "18": 144.0},
      "delivery_offset_hours": {"0": 6.67, ...},
      "latest_init": "2026-05-11T06:00:00+00:00",     // null if marker unset
      "published_at": "2026-05-11T12:38:00+00:00",     // null until first observed
      "next_expected": "2026-05-11T18:40:00+00:00",
      "horizon_end": "2026-05-15T00:00:00+00:00",
      "marker_health": "ok"   // "ok" | "suspect" | "unobserved" | "unknown"
    },
    ...
  ],
  "generated_at": "2026-05-11T14:23:11+00:00"
}
```

Public (no auth) and inexpensive (pure dict + marker-store read, no I/O) — safe to reuse in other UI surfaces (admin pages, mobile clients, etc.). Optional `?model=ecmwf` filter narrows to one pack-model.

When tuning registry offsets:
1. Wait several days, then `curl /api/admin/freshness/markers`.
2. Read `calibration[].drift_p90_vs_config_s` for each (source, cycle_hour). Negative → registry too generous, positive → too tight.
3. Update `delivery_offset` to `p90 + 30min` margin and redeploy. The deque auto-resets; the loop will recalibrate after a few cycles.

## Gotchas

- **Bootstrap is wall-clock dependent.** `initial_marker_for` picks the most-recent cycle whose expected delivery is at-or-before now, falling back one cycle if not yet due. Tests must inject `now=` explicitly to be deterministic.
- **`marker.is_stale(loop_interval)` is heartbeat, not data freshness.** Returns True if `last_check is None` or older than `2 × loop_interval`. Used to surface `marker_health="suspect"` and trigger inline-check fallback. Not a comment on the underlying data.
- **Open-Meteo ECMWF cycles are tracked as `(0, 12)` only.** OM publishes 06/18 as `bc-runs` with heavy lag (per issue #100); tracking them would cause the marker to bounce on bc-run shuffles.
- **OM ICON cycles are `(0, 6, 12, 18)` not 3-hourly.** Even though DWD itself runs ICON every 3h, OM's `meta.json` reports `update_interval_seconds: 21600` — OM only republishes the 6-hourly main runs.
- **The in-memory deque is lost on process restart.** The admin endpoint's calibration view builds up over a few days then resets. Durable telemetry lives in `model_delivery_log` instead — and note the two disagree by construction: the deque records *detection* time (poll-quantised, never early), the table records the provider's *publish* time. Prefer the table.
- **Sync `get_sync()` returns a snapshot copy** so callers can't accidentally mutate the live deque. Don't pass markers across coroutines and expect mutations to land — use the async `update`/`mark_check` API.

## References

- Issue #108 (design + acceptance criteria); issue #167 (tiered refresh gate); issue #192 (model-update-aware auto-refresh email timing — `scheduler._defer_regular_for_model_update` consumes the store + `registry.next_full_horizon_run`/`max_horizon`)
- Existing helpers wrapped: `fetch/model_status.py`, `fetch/grib/{grib_fetch,icon_eu_fetch,ecmwf_watcher}.py`
- Pack-side: `api/packs.py:_build_data_status`, `_backfill_sources`, `_SCRATCH_CANDIDATES`/`_DIRECT_SOURCE_KEYS`, `_pack_init_for_source`, `_finalize_refresh` (records `model_sources` from `result.grib_sources`), `_provider_label` (reads `registry.SOURCE_REGISTRY.provider_label`)
- Tiered gate: `api/packs.py:decide_refresh` + `_refresh_threshold`/`_days_out_now`/`RefreshDecision`, `ModelStatus.covers_horizon`; wired into `refresh_briefing`, `refresh_briefing_stream`, `scheduler._auto_refresh_one`; realtime seam `tasks/route_weather.run_realtime_refresh` (see [metar-taf-route-weather.md](metar-taf-route-weather.md))
- Parameter-change override (#552): `api/packs.py:apply_params_change_override`, `storage/flights.py:compute_flight_params_hash`, `BriefingPackMeta.flight_params_hash`, migration `088`
- Single entry point (#558): `api/packs.py:gated_data_status` — the only thing production code should call; guard test `tests/test_refresh_gate_boundary.py`
- Loop wiring: `scheduler.py:run_freshness_loop`, `api/app.py:lifespan`
- Admin endpoint: `api/admin.py:freshness_markers`
- Public catalog: `fetch/freshness/catalog.py`, `api/data_sources.py` (GET `/api/data-sources`)
- Frontend consumers: `web/help.html` + `web/ts/data-sources-table.ts` (data-driven help table), `web/ts/managers/briefing-ui.ts:renderSourcesPopupContent` (per-flight popover)
- Storage: `BriefingPackMeta.model_sources`, `BriefingPackRow.model_sources_json` (Text JSON, alembic 050)
- Delivery log (issue #515): `db/models.py:ModelDeliveryLogRow`, `storage/model_delivery.py`, alembic 084; written from `scheduler._run_freshness_check_once`
- Tests: `tests/test_freshness_{registry,markers,sources,admin}.py`, `tests/test_data_sources_catalog.py`, `tests/test_packs.py::TestDecideRefresh`, `tests/test_model_delivery_log.py`
