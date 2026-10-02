# Live Observation Layer

> Issue #637. On flight day, an open briefing always shows the newest observed
> conditions, and highlights only the changes that matter. The briefing's grade
> is never touched: the live layer annotates, it does not re-grade.

## Two mechanisms, kept apart

| | Display (live layer) | Significance (changes) |
|---|---|---|
| Rule | Always the newest data, no thresholds | Only changes that matter |
| Hysteresis | None | None since §35 (a crossing counts on its first report) |
| Baseline | None | The briefing (the pack's own observations), or the layer's own starting point when the pack has none + last-alerted state |
| Where | `live.json` blocks → `/live` → clients | `tasks/live_significance.py` (one server classifier, so web / iOS / push agree) |

Meteorology choices (what counts as significant, tiers) are in
[meteorology-decisions.md §34](meteorology-decisions.md), amended by §35–36.
The per-role rules live in one table, `live_significance.AIRPORT_POLICY`.

## Testing

- `tests/test_live_airport_rules.py` pins every policy cell, trigger and timing
  rule; `tests/test_live_significance.py` the SIGMET/observed/memory rules.
- **Scenarios** (`tests/fixtures/live_scenarios/*.json`): a real flight morning
  frozen as public METARs/SIGMETs plus the route. `scripts/build_live_scenario.py`
  runs corridor discovery and the wind advisory (needs `AIRPORTS_DB`) and stores
  the result as `derived`; `tests/test_live_scenarios.py` replays every tick
  through the production `commit_live_update` and pins the full timeline. A rule
  change shows up as a timeline diff to review. With `AIRPORTS_DB` set, a test
  also checks `derived` still matches a fresh build — rerun the script when it
  does not.

## Storage: per flight, pack stays immutable

```
DATA_DIR/packs/{user}/{flight}/
├── {pack_ts}/briefing.json   ← immutable: what the assessment saw (the baseline)
├── live.json                 ← LiveLayer (models/live.py)
└── live_meta.json            ← {pack_dir_name, pack_timestamp, live_updated_at}
```

- **Why not keep patching `briefing.json`** (the pre-#637 behaviour): the patch
  destroyed the baseline the significance classifier needs, and an iOS pack cache
  keyed by pack timestamp can never see an in-place change.
- **The layer records its pack** (`pack_dir_name`, `pack_timestamp`). Readers
  ignore a layer whose pack is not the one they show — that is how a full refresh
  resets it. `commit_live_update` refuses a write for an *older* pack than the one
  stored, and a write whose computation started before another commit landed, so
  the baseline switch is atomic from a reader's point of view.
- **One lock per flight** around read → classify → write (the alert memory is
  read-modify-write); writes are temp file + `os.replace`. Single uvicorn worker,
  so a `threading.Lock` suffices.
- **`live_meta.json`** exists so list endpoints can report `live_updated_at`
  without parsing a payload that carries the full observed-conditions block.
- **Lifetime:** one file per flight, overwritten each tick. Removed with the flight
  (`storage/flights.py::_live_files` joins the deferred cleanup list) and at
  retention T1 (`retention._purge_live_layer`, 30 days post-departure).

## Writers

Both go through `run_realtime_refresh(pack_dir, …, persist=…)` →
`live_layer.commit_live_update`:

1. **A D-0 ↻ press** (tiered gate `realtime` mode, both refresh endpoints, and
   `POST …/observations/refresh`). The latter persists only when the pack is the
   flight's latest; an older pack gets fresh data back but its layer is not
   written.
2. **The live-window tick** (`tasks/live_tick.py`), every verification cycle
   (10 min), for flights in `departure − 3 h … departure + duration + 1 h`
   (`WB_LIVE_WINDOW_BEFORE_H` / `WB_LIVE_WINDOW_AFTER_H`). Every flight with a
   pack, not only auto-refresh flights.

`None` blocks keep the stored value (a SIGMET fetch failing must not blank the
SIGMETs); each block has its own `*_updated_at`.

### Fetch once (the tick)

The tick makes no per-flight network call. Verification's own METAR/TAF fetch
hands raw reports to `LiveTick.sink` (what verification stores is unchanged).
The tick resolves each flight's corridor airports with euro_aip's own
`fetch_route_weather` against a *recording* source (so aliases and route airports
match the network path exactly), tops up only what verification did not cover in
one batch, and serves every flight through `SharedReportSource`. SIGMETs: one
`fetch_isigmet` per tick (`SharedSigmetSource`); a failure is logged once and
cached, and every flight then gets `SigmetSourceUnavailable`, which
`run_realtime_refresh` skips quietly (stored SIGMETs kept). Worst case per tick: two
METAR/TAF batches and one SIGMET call, whatever the flight count. Observed
radar/lightning/tops are re-sampled from local frames (no network).

The verification window (dep − 1 h) was deliberately **not** widened to dep − 3 h:
that would change what verification stores and scores. The top-up covers the gap.

Kill switch: `DISABLE_LIVE_LAYER=1`. The tick rides the verification loop, so
`DISABLE_VERIFICATION=1` also stops it. A failing verification pass still runs
the tick; a failing tick never fails the cycle.

## Readers

| Surface | What it gets |
|---|---|
| `GET /api/flights/{id}/live` | `LiveLayerResponse`: the blocks + `*_updated_at` + `changes` + `last_refresh_delta`. 200 with nulls when nothing live exists for the latest pack. |
| Pack meta (`/packs`, `/packs/latest`, `/packs/{ts}`, SSE `complete.pack`) and flight list `latest_briefing` | `live_updated_at` — the sync signal. A realtime refresh keeps `fetch_timestamp`, so clients cannot rely on it alone. |
| `GET …/snapshot`, `/bundle`, HTML/PDF report | The pack's `briefing.json` **overlaid** with its live layer (`overlay_live`), plus `live_updated_at` / `live_changes` keys. Keeps older app versions on the newest data, as the in-place patch used to. |
| Realtime refresh responses | `live_updated_at` + `changes` alongside the existing `observations`/`sigmets`/`delta`/`observed`. |

Not overlaid (build-time, correctly the briefing's view): the LLM digest, the text
digest, alternate requirement.

## Gotchas

- **Packs written before #637** were patched in place, so their "baseline" is
  whatever the last ↻ wrote. Harmless; new packs are clean.
- **A D-1 pack as the latest on flight day** has no `route_observations` /
  `route_sigmets` (D-0 only). The first live write keeps that fetch on the layer
  (`seeded_observations` / `seeded_sigmets` / `seeded_observed`, `seeded_at`) and
  changes are measured from it: `changes.baseline_source = "live_start"`,
  `baseline_at = seeded_at`, and the web panel reads "Since live tracking began".
  The pack is never touched; a new pack resets the seed with the rest of the layer.
  iOS still labels it "Since this briefing" (time is the seed time) — follow-up.
- **Alternates outside the 30 NM corridor are not fetched**, so their changes
  never show (they are highlight tier anyway since §35). Follow-up.
- `run_realtime_refresh` used `latest.artifact_path` raw (not re-rooted) before
  #637 too; the tick and `/live` resolve through `_resolve_artifact_path`.

## Key code

- `models/live.py` — `LiveLayer`, `LiveChanges`, `LiveChange`, `LiveLayerResponse`
- `tasks/live_layer.py` — store, `commit_live_update`, `overlay_live`, `live_updated_at_for_pack`
- `tasks/live_significance.py` — `classify_changes`, `airport_roles`, `worsening_delta`
- `tasks/live_tick.py` — `LiveTick`, `find_live_flights`, shared sources
- `tasks/route_weather.py::run_realtime_refresh` — the seam (no longer patches the pack)
- `api/packs.py` — `live_router` (`/flights/{id}/live`), overlay in snapshot/bundle
- Tests: `tests/test_live_layer.py`, `tests/test_live_significance.py`, `tests/test_live_tick.py`, `tests/test_api.py::TestLiveLayerEndpoint`

## Clients

**iOS** (written without Xcode in the authoring session — verify on a Mac):
- `LiveLayerResponse` / `LiveChanges` / `LiveChange` DTOs decode tolerantly (enums
  kept as strings). `BriefingRepository.liveLayer(flightId:)` on all conformers.
- **Per-flight live cache** `<flightId>/live.json` in `BriefingCacheStore`, separate
  from the immutable pack bundle; newest wins (checked inside the actor); survives
  relaunch and serves offline (any error but 401/403/404 — an auth failure or a
  deleted flight is rethrown, not masked by stale observations). Encoded with plain `JSONEncoder`, not
  `.weatherBrief` — snake-casing would corrupt `ObservedConditions` dictionary keys
  like `"FL000-050"`. Removed with the flight directory.
- `BriefingViewModel.applyLive(_:to:packTimestamp:)` (pure): applies only when the
  layer's pack is the same *instant* as the pack on screen and it is newer. Cached
  layer applied first, then fetched. `syncLatestPack` fetches when
  `latest.liveUpdatedAt` moved; pull-to-refresh always fetches; a 300 s poll runs
  while the briefing is visible and in the live window.
- UI: "Observed as of HH:MMZ" row (orange past 30 min), "Since this briefing"
  panel + digest caveat on the Advisory tab, changed rows marked in the
  Observations / SIGMET tables. Mock: `FLYFUN_MOCK_LIVE=1`.

**Web**: `store.loadLive` / `syncLatest`, 5-min poll in the live window (skipped
while hidden) + `visibilitychange` re-sync, the same panel / label / caveat /
row highlight (`helpers/live-layer.ts`, unit-tested).

Parity gap: a SIGMET with no sequence number keys on Python's `str(datetime)`;
the web reproduces it, iOS matches on FIR + hazard only.
