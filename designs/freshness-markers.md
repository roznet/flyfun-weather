# Freshness Markers

> Marker-based per-(model, source) decision system for "is this pack stale?" — replaces per-call Open-Meteo `meta.json` fan-out with an in-memory marker store gated by a hardcoded schedule registry.

## Intent

The old freshness path (`fetch/model_status.check_freshness`) called Open-Meteo's `meta.json` for **all 5 models on every freshness check**, and never considered direct-GRIB sources at all. Three concrete problems addressed by this redesign:

1. **Wasted HTTP** — N polling clients × M endpoints per freshness call.
2. **Wasted refreshes** — auto-refresh fired before our 00Z ECMWF GRIB had landed (ready ~06:40Z, fired at 07:07Z), refreshing against stale data.
3. **Direct ECMWF GRIB freshness was invisible** — packs recorded `grib_init_times["ecmwf"]` but `_build_data_status` only consulted `model_init_times` (Open-Meteo).

What should NOT change:
- The pure-compute property: `now < pack.next_expected_update` ⇒ fresh, no I/O. Most freshness HTTP calls are a dict lookup, not a network round-trip.
- The marker store stays **in-memory only**. No DB persistence — bootstrap from registry on restart (~2s of inline-fallback cost). Adding persistence would widen scope; the admin endpoint covers debugging.
- Per-(model, source) granularity. The same logical model can be sourced multiple ways (`ecmwf:direct` vs `ecmwf:openmeteo`), and which one a pack used is a per-pack property — recorded as `pack.model_sources`.

## Architecture

Three modules under `src/weatherbrief/fetch/freshness/`:

| Module | Purpose |
|---|---|
| `registry.py` | `SourceConfig` dataclass, `SOURCE_REGISTRY` dict (8 source/model pairs), pure functions: `next_run_after`, `next_cycle_after`, `run_horizon`, `cycle_init_for`, `expected_delivery_for_init`, `initial_marker_for`. |
| `markers.py` | `Marker` dataclass + `MarkerStore` (asyncio-locked, singleton via `get_store()`). Records `(cycle_init, arrival_wallclock)` observations in a maxlen=100 deque. |
| `sources.py` | Unified `check_source(source, model)` dispatch wrapping existing helpers (`grib_fetch.find_latest_run` for GFS/NOAA, `icon_eu_fetch.find_latest_icon_eu_run` for ICON-EU/DWD, `ecmwf_watcher.get_latest_ready` for ECMWF/direct, `model_status.fetch_model_metadata` for `*:openmeteo`). Each dispatch returns an `Observation(init, published_at)` — `published_at` is the provider's `Last-Modified` header for HTTP sources (NOAA, DWD), the OM `last_run_availability_time` for `*:openmeteo`, and the sentinel-file mtime for ECMWF direct (no central publish wallclock). |

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

## Key Choices

- **In-memory only.** Lost on restart, ~2s bootstrap cost. Admin endpoint provides debugging visibility; no DB persistence in scope.
- **Source key prefix → model name.** `("icon_eu:dwd", "icon_eu")` vs `("icon:openmeteo", "icon")` — the marker store key is `(source, source.split(":", 1)[0])`. Pack-side may map the same logical pack-model name (`"icon"`) to either source; `model_for_source()` strips the suffix to find the marker.
- **Min-rule for staleness.** If *any* source has new data covering the flight, the pack is stale. Avoids per-model weighting heuristics. If a refresh fires for a less-useful source, it just falls back to the previous source — no harm.
- **Horizon-aware.** Without this, a 78h intermediate ICON-EU run would wrongly invalidate a 120h main-run pack for long-haul flights.
- **Exponential slip backoff.** `slip_bump(n) = min(retry_interval × 2^(n-1), max_retry_interval)`. Default base 10min, cap 1h, 8 slips → ~6h before cycle-jump. Replaces uniform `retry_interval × 12 = 2h` cap that hammered slow-publishing OM endpoints.
- **`(cycle_init, arrival_wallclock)` observations.** Stored as tuples in a 100-entry deque per marker. Lets the admin endpoint compute per-cycle-hour median/p90 delay vs. registry expectation, surfacing drift without needing log retention or a DB table.
- **`Marker.published_at` distinct from `arrival_wallclock`.** `published_at` is the provider-reported time the run became downloadable (HTTP `Last-Modified` or OM availability field); `arrival_wallclock` is when *we* observed it. The frontend per-source freshness popover surfaces `published_at`, and the admin endpoint exposes both — the gap measures our polling lag, separately from provider drift.
- **Pack `model_sources` recorded at enrichment time, backfilled at read time.** Legacy packs missing the column infer source from `grib_init_times` presence (a model in `grib_init_times` → direct; otherwise OM).

## Patterns

When adding a new (source, model) pair:
1. Add a `SourceConfig` to `SOURCE_REGISTRY` with cycles, delivery_offset, horizon, readiness_check symbol.
2. Add a wrapper to `sources._DISPATCH` mapping the readiness_check symbol → callable returning `datetime | None`.
3. If the source produces a new model name not seen by `_finalize_refresh`, extend `_DIRECT_SOURCE_KEYS` in `api/packs.py` so `model_sources` records correctly.

When tuning registry offsets:
1. Wait several days, then `curl /api/admin/freshness/markers`.
2. Read `calibration[].drift_p90_vs_config_s` for each (source, cycle_hour). Negative → registry too generous, positive → too tight.
3. Update `delivery_offset` to `p90 + 30min` margin and redeploy. The deque auto-resets; the loop will recalibrate after a few cycles.

## Gotchas

- **Bootstrap is wall-clock dependent.** `initial_marker_for` picks the most-recent cycle whose expected delivery is at-or-before now, falling back one cycle if not yet due. Tests must inject `now=` explicitly to be deterministic.
- **`marker.is_stale(loop_interval)` is heartbeat, not data freshness.** Returns True if `last_check is None` or older than `2 × loop_interval`. Used to surface `marker_health="suspect"` and trigger inline-check fallback. Not a comment on the underlying data.
- **Open-Meteo ECMWF cycles are tracked as `(0, 12)` only.** OM publishes 06/18 as `bc-runs` with heavy lag (per issue #100); tracking them would cause the marker to bounce on bc-run shuffles.
- **OM ICON cycles are `(0, 6, 12, 18)` not 3-hourly.** Even though DWD itself runs ICON every 3h, OM's `meta.json` reports `update_interval_seconds: 21600` — OM only republishes the 6-hourly main runs.
- **The in-memory deque is lost on process restart.** Calibration data builds up over a few days then resets. If you need durable telemetry, add a SQL table (deferred — not in scope).
- **Sync `get_sync()` returns a snapshot copy** so callers can't accidentally mutate the live deque. Don't pass markers across coroutines and expect mutations to land — use the async `update`/`mark_check` API.

## References

- Issue #108 (design + acceptance criteria)
- Existing helpers wrapped: `fetch/model_status.py`, `fetch/grib/{grib_fetch,icon_eu_fetch,ecmwf_watcher}.py`
- Pack-side: `api/packs.py:_build_data_status`, `_backfill_sources`, `_finalize_refresh` (records `model_sources`)
- Loop wiring: `scheduler.py:run_freshness_loop`, `api/app.py:lifespan`
- Admin endpoint: `api/admin.py:freshness_markers`
- Storage: `BriefingPackMeta.model_sources`, `BriefingPackRow.model_sources_json` (Text JSON, alembic 050)
- Tests: `tests/test_freshness_{registry,markers,sources}.py`
