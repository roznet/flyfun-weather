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
[meteorology-decisions.md §34](meteorology-decisions.md), amended by §35–41
(§37, #682: CB/TCU read off the observed part only, SIGMET reissues as
replacements, categorical radar/lightning values; §38, #683: pending SIGMETs;
§39: en route, a station's CB/TCU is a highlight, TS/VCTS still alerts;
§40, #689: a SIGMET starting after arrival is a highlight, a plain reissue
of a briefed SIGMET is direction `updated`;
§41, #688: en-route convective alerts from radar storms, see below).
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
| `evidence` | right after a radar/lightning `appeared` event, and again while it lasts when the peak moves ≥ 5 dBZ or a span end ≥ 10 NM (`EVIDENCE_PEAK_DBZ` / `EVIDENCE_SPAN_NM`, #682) | the triggering route points (`LiveEvidencePoint`: station, along-route NM, inner ring, flash count or peak dBZ + valid/total px), frame time |

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

Radar/lightning `to_value` is categorical since #682 (`heavy` / `present`;
the peak and span are in the message), so the identity holds while the echo
lasts. Records written before carry `"51 dBZ"` / a hit count: the first tick
after the change clears and re-shows the row at one tick, which the trail
reads as one span.

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
    the span, and so does a row back on screen before the history records the
    re-appear (one tick of lag), so neither reads "2nd time";
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
- **SIGMET reissues (#682)** group on their chain's first SIGMET
  (`_trail_key`: the key's part before "+", when `replaces` is set), and a
  reissue appearing within `SIGMET_REISSUE_WINDOW` (60 min) of its row's
  `weather` clear continues the span (`_reissue_resumes`).
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
`fetch_isigmet` per tick (`SharedSigmetSource`, cached per argument set incl.
`lookahead`); a failure is logged once and
cached, and every flight then gets `SigmetSourceUnavailable`, which
`run_realtime_refresh` skips quietly (stored SIGMETs kept). Worst case per tick: two
METAR/TAF batches and one SIGMET fetch (9 isigmet requests with the 4 h
lookahead, see below), whatever the flight count. Observed
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
  them, and `new_since_briefing` when known (#689); no polygon, no raw text.
- `airports` — departure, destination and the top alternates only (the
  classifier's `airport_roles`), when the corridor fetch has a METAR for them.

Deliberately not in it: observed radar/lightning/tops arrays and
`cross_section.json` (never exposed, #278). The layer outlives the live window
(until retention T1), so an agent asked after the flight still sees the last
tick — the timestamps say how old it is.

Not overlaid (build-time, correctly the briefing's view): the LLM digest, the text
digest, alternate requirement.

## Radar storms (#688)

Rule in meteorology-decisions §41. Pieces:

- **Input**: `observed/storms.py::load_cell_frames(now)` reads the droplet's
  `DisplayStore` (the node's pushed display files, `cells-overlay.md`): the
  newest frame at or before `now`, plus ~3 earlier ones 10 min apart. Status
  `available` / `stale` (> `STALE_AFTER`, 25 min) / `disabled` (ingest off) /
  `unavailable`; never raises. `run_realtime_refresh` passes it to
  `commit_live_update(cells=…)`; `None` reads as a dark feed.
- **Geometry** (`build_storms`, droplet side, no analysis): cells grouped by
  the node's `within` (core41 → its core35; older files: nearest core35 within
  its equivalent radius + 3 NM); each storm projected on the route polyline
  (`analysis/route_geometry.RouteTrack`, local equirectangular per segment) at
  the member nearest the track: `along_nm`, `offtrack_nm`, signed `cross_nm`,
  `side`, or `end` + compass from the airport past the route's ends;
  `abeam_eta` from the planned schedule (on-time departure, constant speed,
  like `flown_nm`); `relative_motion` = the velocity's component toward the
  nearest track point (`PARALLEL_KT` 3 kt; `stationary` under 1 kt;
  `unknown` unless motion is `available`); `history` = off-track at the
  earlier frames; `estimate` = closest approach to the 4-D track at current
  motion (1-min steps to arrival, ≤ 3 h), for storms still ahead only. Listed within 30 NM
  (`STORM_CORRIDOR_NM`), behind included, nearest along-track first.
- **Stored** on `LiveLayer.storms` every tick and served on `/live`
  (`LiveLayerResponse.storms`); not in the snapshot overlay or the agent block
  (the rows are). Clients need no change for the rows: a storm row has no
  `icao`, so web/iOS treat it as an area row and render `message`. The storm
  list is for the Observed tab work (#690).
- **Rows** (`live_significance._storm_changes`): one per cluster of storms
  within 25 NM along (`storm_clusters`, keyed on the oldest lineage, members in
  `LiveChange.storm_ids`) / `storms:later`; alert once per storm and per
  stretch (`storm-alerted:` / `storm-span:` keys in the alert memory, §41),
  kind `storm`, source `RADAR`. While the feed is `available` they replace the
  `radar:route` / `lightning:route` ring rows; otherwise the ring rows and the
  station CB/TCU alert are the fallback (`_station_convective`). Station
  backing needs the airport position: `AirportObservation.lat/lon`, filled at
  fetch time from the airports DB (every tick re-fetches, so only an airport
  the DB cannot place has none). Only a storm with its own row
  (`_storm_has_row`) takes backing; `_station_convective` returns it and
  `classify_changes` attaches it to `LiveLayer.storms` in one place.
- **History**: one `estimate` record per storm per cell frame (a ↻ on the same
  frame adds none; ≤ 25 per tick, ~300 B each): storm id and cell ids, frame
  time, position, motion, geometry and the estimate. `score-estimates`
  (`scripts/replay_live_history.py`, live-review skill) joins them with later
  frames. Growth: ~10 storms × ~30 ticks ≈ 100 KB on a showery flight, gone
  with the live files (T1, 30 days).
- **Replay**: `scripts/replay_live_history.py replay … --cells DIR` (promoted
  from the skill) reads display files as of each tick by their mtime (= when
  the node wrote them; `on_mini.py cells` keeps it).

Gotchas: a storm's key is its core35 id, so a core35 that loses the dominant
link in a merge starts a new row (and can alert again). Coverage for the
station fallback is read at the route point nearest the airport along track,
not at the airport itself.

## Observed tab: glance, ribbon, focus (#690)

Plan in `designs/future/observed-tab-presentation.md` §3–5. Server and iOS
landed (PR #695); web is still to do. `tasks/live_glance.py::build_glance` runs in `commit_live_update` right
after the classifier and stores `LiveLayer.glance` / `LiveLayer.ribbon`, and
sets `LiveStorm.focus` on every storm. Served on `/live` and in the iOS UI-test
fixtures; not in the snapshot overlay or the history. Never raises: a failure
logs `Live glance failed` and leaves both null for that tick.

- **Glance**: `as_of` (the tick), `headline` ("Observed 14:29Z · as briefed,
  departure improving", from the change rows' directions by phase; "as briefed"
  only with a briefing baseline, `updated` rows read "N SIGMETs reissued"), then
  one line per phase, departure → en route → arrival. Each line: `text`,
  `alert` (an alert-tier row of that phase, styling only), `passed` (at plan),
  `unavailable` (metar / taf / storms / lightning / sigmets), `sources`, `focus`.
- **Rules**: observed motion only, never `estimate`; a missing source says
  "unavailable", never "clear"/"no cell"; counts are storms, worded "cell"
  (§41). Terminal clauses read storms and lightning within `TERMINAL_NM` (20 NM,
  the widest sampler ring) of the airport, by haversine from it, and also name
  a storm before the route's start / past its end out to the corridor ("no cell
  within 20 NM now (1 at 25 NM E)") — the en-route line counts only storms
  beside the route, so these would otherwise be on no line; en route counts
  storms still ahead outside both terminal discs. Storm motion is the component toward/away
  from the *track* (as on the storm rows), so near an airport it can read
  lower than the storm's own speed. A block older than 30 min at the tick and
  a METAR older than 75 min are flagged inline with their time.
- **Ribbon**: equal segments of ~10 NM (≤ 30) with planned ETAs; per segment
  the max dBZ in the 10 NM ring of the observed route points (`radar_status`
  measured / no_coverage / no_sample, so "nothing detected" ≠ "not seen"),
  lightning in the same ring, overlapping SIGMET ids and storm ids. Station
  lane: every corridor airport with a METAR/TAF, signed `cross_nm`, METAR now,
  TAF at ETA (prevailing + temporary, for hatching). SIGMET band: span, new
  (from `new_sigmets`), pending, and MOV against the route (toward / away /
  parallel / stationary, from the area centre). The storm lane is
  `LiveLayer.storms` itself, not copied.
- **Weather bands** (`observed/route_bands.py::build_weather_bands`, the
  symbolic map's rain and cells): built from the cells feed's newest display
  file while `storms.status == "available"` (`weather_status` carries the feed
  state; clients fall back to the radar segments otherwise). One
  `RibbonWeather` per `rain20` / `core35` **outline** within the storm corridor
  (30 NM). Outlines are not linked to cells, so the shape comes from the
  polygon (densified to ~1 NM so long straight edges still bound every bin)
  and the cells whose centre lies inside lend it peak, flashes, motion and the
  storm id; with none inside, the tier floor (20 / 35 dBZ). `profile`: per
  `weather_bin_nm` (5 NM) of route the signed off-track range covered; where
  the route point is inside the outline the track is covered and a side with
  no boundary in that bin runs out to the corridor; an outline enclosing the
  route with no boundary in the corridor at all is the full width wherever it
  encloses it (route bins tested inside the ring, bbox first). `motion_rel_deg` is the
  member's `toward_deg` against the course of the segment abeam (`+` toward
  the right of track). ≤ 80 per tier, nearest first. ~8 KB on a 380 NM route
  through a squall line (LFBH→LFMD 2026-10-06).
- **Focus** (`LiveFocus`): `{kind, id, bbox (min_lon, min_lat, max_lon,
  max_lat), layers, time}` on storms (storm + the track abeam), ribbon SIGMETs
  (their area; a pending one opens at its start), stations, segments, and each
  glance line. Change rows carry none: clients map a row to its item through
  `storm_ids`, the SIGMET key or `icao` (keeps `live.json`'s change rows and the
  history unchanged).
- **Agents**: `summarize_live` adds `glance` (`as_of`, `headline`, `lines`
  of `{phase, text}`), word for word what the apps show; `LIVE_NOTE` says so.
- **Size**: ~17 KB per `live.json` on a 280 NM route (LELL→LEMI), overwritten
  each tick.
- **iOS** (built and UI-tested on a Mac, PR #695): DTOs in
  `Models/API/LiveGlance.swift` (`storms`, `glance`, `ribbon` are defaulted vars on
  `LiveLayerResponse`, so memberwise inits and older servers still work).
  `Views/Briefing/ObservedNutshellView.swift`: `ObservedNutshellCard` (server
  text as is; red bar for `alert`, dimmed when `passed`), `RouteRibbonCard` /
  `RouteRibbonView` — a symbolic map: the route as a straight line to scale,
  departure / destination circles on its ends, the planned position on it;
  left of course above, right below: an airport row each side (fill = METAR
  now, ring = TAF at ETA, dashed for PROB/TEMPO), and between rows and line
  the weather bands at their off-track distance (rain pale, cores by peak)
  with one arrow per moving band rotated by `motion_rel_deg`; SIGMETs a thin
  band on top; `RouteRibbonLegend` underneath. A storm's core is its tap
  target (`ribbonStorm-<id>`, the sheet); elsewhere a tap frames the map on
  that segment. Without `weather` the radar strip hugs the line and storms
  are points, `StormDetailSheet` (observed facts, 30-min trend and
  off-track history, the estimate in its own "Estimate at current motion"
  section). They replace `ObservedGlanceCard` only when `glance` is present,
  and only for the pack on screen (`BriefingViewModel.liveLayerForPack`). A ↻
  response has no glance: the last one for the same pack is kept until the
  next `/live`. Tap-to-map: `FocusIntent.mapFocus` → `RouteMapView` turns on the
  focus's cells/radar and passes `focusRegion` + a counter key to
  `RouteMapKitView`, which frames it once per key. The map draws no SIGMET
  polygons yet, so a SIGMET focus only frames its area; `metar` layers and the
  focus `time` are ignored too (newest frame). The applied focus is cleared
  (`onFocusApplied`) so a recreated map never re-frames a stale one. The web
  slice should implement the full contract, not copy iOS's subset.
- **Web**: not done yet.

## Observed highlight (#697) — written, not displayed

One or two sentences above the nutshell saying what deserves attention on the
route ahead, written by Claude Haiku 4.5 from a facts block the code computes.
**Code does the weather, the model phrases it**: no analysis by the model, so a
highlight can never say something the tick did not already know.

**Not shown anywhere yet** (owner, 2026-10-07). It is generated for every live
flight and logged so real flight days can be reviewed and the prompt
calibrated before any client renders it. Deliberately also out of the agent
`live` block — `summarize_live` names the glance fields it exposes, and a
pinned test keeps it that way, because an agent quoting it would be a
user-facing surface by the back door.

### Off the critical path

`commit_live_update` is unchanged except for one pure call, and the model runs
*after* it returns:

1. **Commit** writes the layer as before, and `live_highlight.carry_forward`
   re-attaches the previous highlight when this tick's facts hash is unchanged
   (the glance is rebuilt wholesale each tick, so without this every tick loses
   the highlight and pays for a new one). Measured cost on a real 69 KB layer:
   **0.29 ms**.
2. **`live_tick._highlights`** then generates for the flights that still have
   none, fanned out over a thread pool, and `live_layer.patch_highlight` writes
   `glance.highlight` in a second small write under the same lock.

So the deterministic blocks are servable ~1 s earlier, a model failure or
timeout cannot roll a tick back, and the ↻ press never waits on a model: its
own commit either carries the previous highlight forward (unchanged facts) or
leaves it null for the next tick, which is the contract iOS already has ("a ↻
response has no glance: the last one for the same pack is kept until the next
`/live`"). Measured on prod 2026-10-07, Haiku on the busiest real tick: **p50
1.04 s, p95 1.19 s, max 1.21 s over 25 calls**, against a 3–5 s ↻ refresh and a
10–25 min observation→screen pipeline. 1, 4 and 10 concurrent calls all return
in ~1.2 s, so the tick grows by one call's latency, not by the flight count.

`patch_highlight` refuses rather than overwrite when the layer moved on (a
different pack, or a newer `glance.as_of`): the text was written from *those*
facts, and the next tick generates its own.

### What counts as "changed" (the cost gate)

`facts_hash` hashes a **`_hash_view`** of the facts, not the facts, because
three things move on the wall clock alone with nothing a pilot would read
having changed — and hashing them meant paying for an identical highlight
every tick of every flight:

- `now`;
- the flown figure in `flight` — interpolated from departure, so on a
  276 NM / 1.5 h plan it advances ~30 NM per 10-minute tick;
- the "% of the route ahead" in `rain_ahead`, whose denominator is
  `route_nm - flown`.

Measured on the real LELL→LEMI 08:30 tick replayed 10 min later with the
weather byte-identical: the hash changed, so the gate was dead for the whole
airborne phase — the part that matters. Dropping the figure loses nothing,
because everything progress actually decides is captured exactly elsewhere and
still hashed: which cells are `ahead`, which SIGMET spans are still in front,
which airports remain, the rain stretches. The **coarse phase** is kept, so
take-off and landing still regenerate.

Replaying all 33 ticks of that flight's window with the weather held
identical: **10 billed calls before the fix, 6 after** (−40%), and all 6 are
real — take-off, landing, and four airports dropping out of "ahead" as the
flight passes them. The facts the model sees keep the exact figure; only
change detection is coarse.

Cost: **~$0.0016 a call** measured (1050–1509 input tokens, 21–96 output;
input is ~90% of it and nearly constant because the facts block is capped).
A 2 h flight sits in the window for 36 ticks. Note the admin cost views filter
on `category == "briefing"`, so these rows are recorded and queryable but **not
shown** anywhere — which is why `call_cost` puts the USD of every attempt into
the review log, rejected ones included.

### Grounding check

`check_grounding` is the only thing between a model sentence and a cockpit
screen, so it rejects on five rules and falls back to the nutshell `headline`:

1. **ICAOs** — every aerodrome code must appear in the facts.
2. **Figures** — every number must appear in the facts. The prompt therefore
   forbids the model working out spans of its own: it first wrote "the last 41
   NM" for a SIGMET covering 235–276 NM, which is true but unverifiable, and
   the rule rejected it. One prompt line ("give every figure exactly as the
   facts give it") took the replay set from 2 rejections in 5 to 0 in 10.
3. **Place binding** — in a clause naming exactly one airport, every *airport
   condition* claimed must be one the facts give for that airport. This is the
   rule a plain "appears somewhere in the facts" check misses: moving LECH's
   LIFR onto LEMI passes rules 1 and 2 and fails here. Conditions are matched
   through a surface-form map, so the facts' `TSRA` supports the model's
   "thunderstorm".
4. **Verdict words** — never go/no-go (`feedback_not_go_nogo`).
5. **"Thunderstorm" needs lightning** — §41: a radar core is a "cell".

Gotcha that cost a test: `\b[A-Z]{4}\b` matches `LIFR` and `TSRA` as if they
were ICAO codes. Unfiltered, the binding rule saw two "ICAOs" in "LEMI
reporting LIFR" and skipped the clause — the exact misattribution it exists to
catch. `_NOT_ICAO` holds the colliding weather codes.

Accepted limits: a clause naming two airports is not bound (ambiguous
attribution), and a clause hedged with "no"/"better" is skipped (the rule
cannot read a negation). Both let a wrong line through rather than reject a
right one; the replay set and the review log are the backstop.

### Retries on a rejection

The tick picks up any flight with no stored highlight, and a rejection stores
nothing, so uncapped a single bad facts block bought a rejected sentence every
tick for the whole live window — 33 billed calls on one flight. Zero retries
would be wrong the other way: the model is stochastic (the same tick comes back
worded differently run to run), so a transient bad line would cost that flight
its highlight until the weather moved.

`MAX_ATTEMPTS_PER_FACTS = 2`, counted per **facts state** from the review log
(`rejected_attempts`) — no extra state to carry across ticks. A bad draw gets a
second chance; a systematic failure costs twice, not 33 times. Only `rejected`
counts: a `written` one is carried forward anyway, and a `call_failed` one is a
timeout that cost nothing and is right to retry. When the weather moves the hash
changes and the flight gets a fresh go. Further ticks log a one-line
`skipped_rejected` marker **without** the facts block, so the frequency stays
visible for review without repeating 1.5 kB every tick.

Measured rejection rate after the figure-rule prompt fix: **0 of 74
generations** across 8 distinct facts shapes (SIGMET/METAR-heavy LELL→LEMI
ticks and the cell-heavy LFBH→LFMD squall line, which exercises dBZ, abeam
times, closing speeds and the lightning rule). Narrow — 8 shapes — so the
calibration logs are what will actually establish the rate.

### Review log

`live_highlights.jsonl`, append-only per flight, one record per attempt
(`written` / `reused` / `rejected` / `call_failed` / `superseded`) with the
**facts block alongside the text**. `live.json` holds only the newest
highlight, so this is the only place a flight day can be read back from — and
without the facts, a line that reads wrong is ambiguous between the model's
phrasing and the block feeding it. A rejection keeps the rejected text too, so
a false rejection is visible rather than silent. Listed in `LIVE_FILES`, so a
flight delete takes it with the layer.

### Operational

- **Needs #695 deployed.** The facts come from `glance` + `ribbon`; on a
  pre-#695 layer `facts` degenerates to "DEP to DEST, 0 NM". `ensure_highlight`
  skips a layer with no ribbon rather than pay for that call. As of 2026-10-07
  the droplet image predates #695 — the highlight stays dark until they deploy
  together.
- **Depends on #696 for trust.** A phantom clutter cell reaches the highlight
  too, and the highlight *promotes* it from one row in a list to the one
  sentence at the top. Reviewing before displaying is what covers this.
- Kill switch `DISABLE_LIVE_HIGHLIGHT=1`; dark with no `ANTHROPIC_API_KEY`
  (which is also how the test suite runs the whole tick without calling
  anything — `conftest` deletes the key so a developer's shell cannot bill the
  suite).
- **Cost** ~$0.0016 per call through the shared ledger (`action=live_highlight`,
  priced by `compute_call_cost`, never the per-briefing `compute_cost`). Charged
  on the tick's own thread: a `Session` is not thread-safe, so
  `ensure_highlight` returns the usage and the caller charges it.
- Prompt, facts block and check live in `tasks/live_highlight.py` — the code
  the tick runs. `scripts/live_highlight_experiment.py` imports them, so the
  replay harness cannot drift from production.
- Prompt caching and streaming are both no-ops here and deliberately absent:
  the request is ~1.4 k tokens against Haiku 4.5's 4096-token minimum cacheable
  prefix, and the client gets the text as one JSON field.

## SIGMET reissues (#682)

`live_significance._sigmet_changes` matches each SIGMET it sees for the first
time against the baseline's and the recently seen ones
(`ClassifierMemory.sigmets`, persisted as `LiveLayer.sigmet_traces`, reset
with the alert memory on a new pack; a trace lives until 60 min after its
validity ends). The rule and tiers are in meteorology-decisions §37. What the
code relies on:

- A SIGMET keeps the trace it got when first seen (`_trace_sigmets`), so a row
  never flips between "new" and "replaces".
- The row key is `<chain's first SIGMET>+<this SIGMET>` (`reissue_key` /
  `reissue_chain`, the only places that build or parse it; the trail groups
  on `reissue_chain`); `LiveChange.replaces` holds the predecessor's label
  ("LFMM T01"). Clients need no change: they split keys on "+" to mark
  listed SIGMETs (iOS `issuedSigmetKeys`, web `live-layer.ts`), and render
  `message`. Keep "+" out of SIGMET keys.
- A SIGMET still listed keeps its trace however old it is; the 60-min window
  only limits which traces can be a *predecessor*.
- `classify_changes` gets a `quiet` key set from the SIGMET stage: those rows
  set the alert memory without `new_alert`.
- No geometry or validity on either side → never a reissue (the louder
  "New SIGMET" reading).
- **Direction (#689, §40).** A reissue of a chain the baseline had is
  `updated` unless it reaches the destination or `LiveSigmetTrace.reissue_worse`
  (area now crosses the route, or levels now meet the flight's band; decided
  once, from the predecessor's trace, which now keeps `base_ft` / `top_ft` /
  `min_distance_nm`; traces stored before #689 lack them, so at deploy an
  in-flight layer's reissue reads `updated` for those two cases, once). `updated` is not in `worsened_count` nor in
  `last_refresh_delta`; sort order worse → updated → better. The trail groups
  by key + direction, so a chain whose row was "worse" before a pack switch
  and "updated" after reads as two rows.
- **NEW (#689).** `LiveChanges.new_sigmets` lists the listed SIGMETs whose
  chain is not `chain_in_baseline`; None when SIGMETs were not evaluated.
  iOS badges NEW from it (fallback: the old change-row keys); the agent block
  gives `new_since_briefing` per SIGMET.

## Pending SIGMETs (#683)

The SIGMET fetch looks ahead (`route_weather.SIGMET_LOOKAHEAD`, 4 h), so the
list holds SIGMETs issued but not yet valid. Rule in meteorology-decisions §38.
What the code relies on:

- `_pending(s, now)` (`valid_from > now`) adds " from HH:MMZ" to a "New
  SIGMET" or reissue row while every SIGMET of the row is pending. Only the
  message changes when it starts: `change_identity` ignores the message, so
  no second alert and no new trail event.
- A failed lookahead query (euro_aip stops the lookahead and keeps the rest)
  drops pending SIGMETs for one tick. So a baseline SIGMET missing before its
  start is not "gone", and `_pending_key` keeps the alert memory of a missing
  SIGMET whose trace is still before its start. Both rely on the trace
  (`ClassifierMemory.sigmets`) holding `valid_from`.
- `LiveChange.observed_at` for a pending SIGMET is in the future; clients
  show no age for it.
- **After arrival (#689).** `classify_changes(arrival_at=…)` (the planned
  landing, `live_layer.planned_arrival`; both writers pass it). A row whose
  SIGMETs all start after arrival + `SIGMET_AFTER_ARRIVAL_MARGIN` (30 min)
  is highlight and leaves `chain_alerted` false.

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
- `observed/storms.py` — `load_cell_frames`, `build_storms`, `group_storms`, `estimate` (#688); `analysis/route_geometry.RouteTrack`
- `tasks/live_glance.py` — `build_glance`: the Observed tab's nutshell, ribbon and map focus (#690)
- `tasks/live_highlight.py` — `facts`, `facts_hash`, `check_grounding`, `generate`, `carry_forward`, `ensure_highlight`, `charge_highlight`: the model-written highlight (#697); `live_layer.patch_highlight` is its second write and `live_tick._highlights` its fan-out
- `api/packs.py` — `live_router` (`/flights/{id}/live`, `/flights/{id}/live/summary`), overlay in snapshot/bundle
- Tests: `tests/test_live_layer.py`, `tests/test_live_significance.py`, `tests/test_live_tick.py`, `tests/test_api.py::TestLiveLayerEndpoint`, `tests/test_live_summary.py` (agent block, incl. the 08:30 LELL→LEMI tick), `tests/test_live_trail.py` (trail rules, LFBZ→LFMD day, LELL→LEMI replay), `tests/test_live_storms.py` (storm geometry, §41 tiers, backing/fallback, estimate log and scoring), `tests/test_live_glance.py` (nutshell, ribbon, focus; an LPPR→LPPT-like synthetic day), `tests/test_live_highlight.py` (grounding rules, carry-forward, refused patch, API failure), `tests/test_mcp_live.py`, `tests/test_agent_endpoints.py` (live block + `/live/summary`)

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
