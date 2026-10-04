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
  does not. `derived.observations` holds one entry per (METAR, TAF) so the replay
  serves TAF changes too (the LELL→LEMI fixture has no TAFs).
- `tests/test_live_history.py`: the history records, and the LELL→LEMI replay's
  history equals the pinned timeline.

## Storage: per flight, pack stays immutable

```
DATA_DIR/packs/{user}/{flight}/
├── {pack_ts}/briefing.json   ← immutable: what the assessment saw (the baseline)
├── live.json                 ← LiveLayer (models/live.py)
├── live_meta.json            ← {pack_dir_name, pack_timestamp, live_updated_at}
└── live_history.jsonl        ← append-only timeline of the flight day (#643)
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
  retention T1 (`retention._purge_live_layer`, 30 days post-departure). Every
  site uses `live_layer.LIVE_FILES`, so the history goes with them. A flight
  move does not carry packs or live files: they are deleted with the old flight.

## History (`live_history.jsonl`, #643)

`live.json` only holds the latest tick, so nothing recorded what the pilot was
shown, and when. `commit_live_update` appends to a per-flight JSON Lines file,
inside the flight lock, after `live.json`/meta are written (`_record_history`).
Every record has `type`, `tick_at`, `pack_timestamp`:

| type | when | carries |
|---|---|---|
| `pack` | first write, and whenever the layer's pack differs from the last `pack` record | `pack_dir_name`, `previous_pack_timestamp`, `has_observations`, corridor widths |
| `report` | a METAR/SPECI (ICAO + obs time + raw, so a COR at the same time is kept), TAF (ICAO + issue time) or SIGMET (FIR + raw text) the first time it is seen | `raw`; SIGMETs also the structured `SigmetAlongRoute` minus raw text (nothing parses raw SIGMET text back); `seen_at` only when ≠ `tick_at` |
| `event` | a change `appeared` / `cleared` vs what the history last recorded as shown, identity `change_identity` = (key, direction, to_value, tier) | the change (nulls dropped; `new_alert` on appear; a clear carries the last message shown) |
| `evidence` | right after a radar/lightning `appeared` event | the triggering route points (`LiveEvidencePoint`: station, along-route NM, inner ring, flash count or peak dBZ + valid/total px), frame time |

Choices:
- **Not full `live.json` snapshots** (60–400 KB × ~37 ticks, mostly the observed
  block). Replaying a morning's radar under other thresholds is out of scope.
- **One timeline across packs.** Events diff against the changes the history
  last recorded as on screen (appeared, not yet cleared; each in the stored
  layer's latest form and order) even when the pack switched, so a rebuild shows as clears/appears at
  that tick (the scenario replay does the same). The new pack's own
  observations/SIGMETs are recorded as reports with its `pack` record, so its
  baseline can be rebuilt.
- **Dedupe from the file**, not an index on the layer (the layer resets on a pack
  switch): each write reads the history (tens of KB) for seen report keys.
- **History starting mid-flight** (no file yet, layer exists): what is on screen
  is recorded as appearing at that tick.
- **Only reports the tick saw**: the latest METAR per airport per tick. A METAR
  superseded within one 10-min tick is not recorded (the pilot never saw it).
- **Evidence stays off the API**: `LiveChange.evidence` is `exclude=True`, so it
  is in memory for the writer only, never in `live.json`, `/live` or the overlay.
- **Failure isolation**: any history error is logged (`Live history write
  failed`) and the tick/↻ carries on. Because the diff runs against the history
  itself, a failed tick's events are not lost: they land on the next tick that
  writes, at that tick's time. A truncated last line is skipped on read
  and the next append starts on a fresh line.
- **Size**: LELL→LEMI (busiest flight of 2026-10-02, no TAFs in the replay)
  ≈ 50 KB, mostly METAR raw text; a test caps it at 100 KB. No retention beyond
  the live files' own (T1).

METAR report records carry `flight_category` since #669 (for the trail's
strip); records written before (2026-10-03/04 files) are re-parsed from `raw`
on read, the way `inputs_from_history` does.

### Trail (#640 option A, #669)

Each change row shows its recent history, so the pilot can tell building
from bouncing, and a change that cleared does not vanish without a trace.
`tasks/live_trail.py::change_trails(history, changes, now, route, departure)`
is one pure function; `trails_for_pack` wraps it (never raises: on error the
changes come back without trails). Display only: the classifier, tiers,
`new_alert`, alert memory and the counts are untouched; a flip-flop is shown,
never suppressed (§35).

- **Computed at read time** in `/live`, the realtime refresh result (so ↻ and
  the next poll agree) and `live_summary`. Never stored: `live.json` is
  written before the history, so a stored trail would lag a tick. `TRAIL_EXCLUDE`
  keeps `trail` / `cleared_at` / `recently_cleared` out of `live.json` and the
  snapshot overlay. The route/departure for the relevance check come from the
  pack's briefing, cached per pack dir (packs are immutable).
- **Model** (all optional): `LiveChange.trail` = `{spans: [{start, end|null}],
  times_today, reports (metar_category: [{at, category, report_type}], latest
  6 from the report that first showed the change), baseline_source}`;
  `LiveChanges.recently_cleared` (rows with `cleared_at`, newest first; None =
  not computed, [] = nothing recent). Span fields are `start`/`end`, not the
  issue's `from`/`to` (`from` is a Python keyword and plain dumps skip aliases).
- **Grouping is by key + direction**, not `change_identity`: a value/tier
  change (MVFR → IFR) continues the span; a direction flip is another row, so
  "New SIGMET X" then (after a rebuild absorbed it) "SIGMET X no longer
  active" is not a "2nd time today". `times_today` counts that group's spans
  over the whole history (one timeline across packs).
- **What ends a span, and whether it is a cleared row** (only `weather` is):
  - clear + appear of the same key/direction at one tick → continuous;
  - clear at a tick with a `pack` record and no re-appear → `pack`: the new
    briefing absorbed it;
  - airport change, airport no longer relevant at that tick
    (`live_significance.airport_relevant`: departure after take-off, en-route
    airport passed) → `dropout`;
  - airport change with no newer METAR (TAF for `taf_category`) for that ICAO
    recorded by then → `gap` (fetch missed the airport); a re-appear continues
    the span;
  - anything else, incl. SIGMET expiry and radar/lightning → `weather`.
- **Cleared rows**: last span ended `weather` within 60 min
  (`RECENTLY_CLEARED_MINUTES`, inclusive), key not on screen in any direction,
  and only the key's most recent span (a better reading replaced by a worse one
  that later dropped out must not resurface). `new_alert` forced false; tier
  kept as shown, clients style it plainly.
- **Clients** show the server's list as is (no client-side hour re-filter, so
  web and iOS always agree; each row says when it cleared). The trail line is
  shown only when it says more than the row (N ≥ 2, ≥ 2 spans, ≥ 2 reports,
  or a cleared row). Text: `web/ts/helpers/live-layer.ts::trailText` and iOS
  `LiveTrailText` (same strings, pinned by both test suites). Both clients
  accept a `/live` with the *same* `live_updated_at` once when it brings
  trails the snapshot overlay lacks (`addsTrails`), else the first poll after a
  pack load would be ignored.
- Agents get only `times_today` per change and `recently_cleared` (key,
  message, cleared_at; cap 6) — the fact, not the strip.
- Not done: radar/lightning clears are always `weather`, even when the echo
  was simply passed (the change's min along-track position cannot tell).

Readers: `live_layer.load_live_history(flight_dir)` (records oldest first) for
admin/debug and the trail (#669). `scripts/build_live_scenario.py
--from-history <flight_dir> <fixture>` turns a flight's history into a scenario
(`inputs_from_history`: raw METAR/TAF re-parsed with euro_aip anchored on the
report time, SIGMETs issued when first seen, packs active from their first
tick); with `AIRPORTS_DB` a test checks the round trip reproduces the pinned
timeline.

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
| `GET /api/flights/{id}/live` | `LiveLayerResponse`: the blocks + `*_updated_at` + `changes` (with read-time `trail` / `recently_cleared`, #669) + `last_refresh_delta`. 200 with nulls when nothing live exists for the latest pack. |
| Pack meta (`/packs`, `/packs/latest`, `/packs/{ts}`, SSE `complete.pack`) and flight list `latest_briefing` | `live_updated_at` — the sync signal. A realtime refresh keeps `fetch_timestamp`, so clients cannot rely on it alone. |
| `GET …/snapshot`, `/bundle`, HTML/PDF report | The pack's `briefing.json` **overlaid** with its live layer (`overlay_live`), plus `live_updated_at` / `live_changes` keys. Keeps older app versions on the newest data, as the in-place patch used to. |
| Realtime refresh responses | `live_updated_at` + `changes` (with trails) alongside the existing `observations`/`sigmets`/`delta`/`observed`. |
| Agents: MCP `get_briefing` and ChatGPT `getBriefing` (#641) | A compact `live` block (null when the latest pack has no layer), built by one helper, `live_layer.live_summary(pack_dir)`. The ChatGPT action calls it in-process; the MCP server (separate process, HTTP only) reads it from `GET /api/flights/{id}/live/summary?pack_timestamp=…`, pinned to the pack whose digest it returns (so `digest_written_at` matches even if a refresh lands mid-call). If that fetch fails, MCP returns `live: null` plus `live_unavailable: true`, so an agent can tell a failed fetch from "nothing live". |

### The agent `live` block (#641)

`summarize_live(layer, briefing_data)`: a `note` guardrail (`LIVE_NOTE`: lead
with alert-tier changes; the digest/advisories/grade were written at
`digest_written_at` = the pack time and are never re-graded), the `*_updated_at`
times, `baseline_at` / `baseline_source`, the three counts, then:

- `changes` — ordered alert → highlight, worse → better, destination → departure
  → alternate → route, newest evidence first; capped at 12 (`changes_total`
  keeps the count). Only tier/direction/role/kind/icao/message/observed_at,
  plus `times_today` (#669).
- `recently_cleared` — what cleared on the weather in the last hour (key,
  message, cleared_at; cap 6). `LIVE_NOTE` explains both.
- `sigmets` — every current route SIGMET (cap 20, `sigmets_total`), with the
  same `label` the change messages use ("LECB 3: EMBD TS") so an agent can tie
  them; no polygon, no raw text.
- `airports` — departure, destination and the top alternates only (the
  classifier's `airport_roles`), when the corridor fetch has a METAR for them.

Deliberately not in it: observed radar/lightning/tops arrays and
`cross_section.json` (never exposed, #278). The layer outlives the live window
(until retention T1), so an agent asked after the flight still sees the last
tick — the timestamps say how old it is.

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
- `tasks/live_layer.py` — store, `commit_live_update`, `overlay_live`, `live_updated_at_for_pack`, history (`load_live_history`, `change_identity`, `LIVE_FILES`)
- `tasks/live_significance.py` — `classify_changes`, `airport_roles`, `worsening_delta`
- `tasks/live_tick.py` — `LiveTick`, `find_live_flights`, shared sources
- `tasks/route_weather.py::run_realtime_refresh` — the seam (no longer patches the pack)
- `tasks/live_layer.py::live_summary` / `summarize_live` — the agent block (#641)
- `tasks/live_trail.py` — `change_trails`, `trails_for_pack` (#669)
- `api/packs.py` — `live_router` (`/flights/{id}/live`, `/flights/{id}/live/summary`), overlay in snapshot/bundle
- Tests: `tests/test_live_layer.py`, `tests/test_live_significance.py`, `tests/test_live_tick.py`, `tests/test_api.py::TestLiveLayerEndpoint`, `tests/test_live_summary.py` (agent block, incl. the 08:30 LELL→LEMI tick), `tests/test_live_trail.py` (trail rules, LFBZ→LFMD day, LELL→LEMI replay), `tests/test_mcp_live.py`, `tests/test_agent_endpoints.py` (live block + `/live/summary`)

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
- UI: "Observed as of HH:MMZ" row (orange past 30 min); the "Since this
  briefing" panel on the Observed tab (#661) with changed rows marked in its
  METAR/TAF and SIGMET tables; on the Advisory tab only the digest caveat and
  a teaser row that switches to Observed. Mock: `FLYFUN_MOCK_LIVE=1`.

**Web**: `store.loadLive` / `syncLatest`, 5-min poll in the live window (skipped
while hidden) + `visibilitychange` re-sync, the same panel / label / caveat /
row highlight (`helpers/live-layer.ts`, unit-tested).

Parity gap: a SIGMET with no sequence number keys on Python's `str(datetime)`;
the web reproduces it, iOS matches on FIR + hazard only.
