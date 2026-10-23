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
replacements, categorical radar/lightning values; §38, #683: pending SIGMETs
(§46, #686: a failed fetch is no fetch, a covered missing pending SIGMET is
"cancelled");
§39: en route, a station's CB/TCU is a highlight, TS/VCTS still alerts;
§40, #689: a SIGMET starting after arrival is a highlight, a plain reissue
of a briefed SIGMET is direction `updated`;
§41, #688: en-route convective alerts from radar storms, see below;
§45, #722: an airport row pings only when worse than the worst it already
alerted on this flight, a memory that survives a return to baseline;
§47, #754: the push path pushes a clear and re-arms the key, see below).
The per-role rules live in one table, `live_significance.AIRPORT_POLICY`.
A departure/destination with no METAR/TAF of its own is classified on its
proxy station (#767, meteorology-decisions §49): `_terminal_pair` compares the
station read now with the *same* station in the baseline (so a proxy switch is
never a row), messages read "EGTF (via EGLL) METAR: …", keys stay
`metar:EGTF`, and the station's own route rows are dropped while it serves.
The glance line carries `proxy_caption`, the ribbon station `via`.

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
├── live_meta.json            ← {pack_dir_name, pack_timestamp, live_updated_at, highlight_updated_at?}
├── live_history.jsonl        ← append-only timeline of the flight day (#643)
└── live_snapshots.jsonl.gz   ← per-tick display snapshot, for replay + recap (#711, debrief-recap.md)
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
- **Alert memory** (`LiveLayer.alerted`, `dict[str, str]`, reset with the
  layer on a new pack) decides `new_alert` (what the push consumes), never which rows
  show. Three families: airport keys (`metar:` / `taf:` / `conv:` / `wind:` /
  `wx:`) hold the worst value alerted (the phenomena alerted, for `wx:`) and
  are never forgotten during the flight (§45, `_airport_alert`); storm keys
  (`storm-alerted:` / `storm-span:`, §41); SIGMET and radar/lightning keys
  hold the last value and are dropped once the row is gone (unless pending, `pending_sigmet_key`). Changing
  a family's stored format must keep an existing `live.json` loadable without
  re-alerting a flight in the air.
- **`live_meta.json`** exists so list endpoints can report `live_updated_at`
  without parsing a payload that carries the full observed-conditions block.
- **Lifetime:** one file per flight, overwritten each tick. Removed with the flight
  (`storage/flights.py::_live_files` joins the deferred cleanup list; every
  site uses `live_layer.LIVE_FILES`). Retention runs once per flight
  (`retention._live_retention`, #711): `LIVE_FILES_T1` (`live.json`, the
  highlight log, `live_frozen`) at T1 (30 days post-departure); `LIVE_FILES_KEPT`
  (history, snapshots, `live_meta.json`) at `RETENTION_LIVE_DAYS` (90), never for
  a kept flight (`retention.kept_flight_ids`: debriefed). Pinned flights and
  flights with a PIREP-linked pack keep all of them. The purge no longer reads
  `live_meta.json` to find the owning pack: that check ran per pack and turned a
  later pass into a no-op once T1 had deleted the meta file. A flight move does
  not carry packs or live files: they are deleted with the old flight.
- **Reached only through pack rows (accepted).** Retention finds a flight's
  directory from its pack rows, so the live pass purges the kept files in the same
  run where T2 drops those rows (`_t2_due`, shared with the pack loop). Pack rows
  removed any other way (a manual DB delete) leave the live files unreachable;
  deleting a flight cleans them up through `_live_files`, so there is no orphan sweep.

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
| `estimate` | a storm with a closest-approach estimate, once per storm per cell frame (#688) | the estimate, plus `progress_source` (#760): the schedule its 4-D track came from |
| `progress` | the owner's `POST …/live/progress` (#760) or `POST …/live/position` (#761), not a tick; no `pack_timestamp` | `event` (`takeoff` / `landed` / `fix`), `at` (null = withdrawn), `source` (`manual` / `app`); a `fix` also `lat` / `lon`, the device's `alt_ft`, `gs_kt`, `track_deg`, `h_accuracy_m`, the server's projection on the route it was received against (`along_nm`, `cross_nm` unsigned, `course_deg`, `route_nm`) and the device's own (`client_along_nm`, `client_cross_nm`, cross-check only); `tick_at` = when the server learned it |

Choices:
- **Not full `live.json` snapshots** (60–400 KB × ~37 ticks, mostly the observed
  block). Replaying a morning's radar under other thresholds is out of scope.
  The *display* blocks per tick are kept since #711, in their own file
  (`live_snapshots.jsonl.gz`, see `debrief-recap.md`), not in the history.
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
  ≈ 50 KB, mostly METAR raw text; a test caps it at 100 KB. Kept 90 days, or
  forever for a kept flight (#711), with the snapshots.

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
   pack, not only auto-refresh flights. Only this writer pushes (below). The
   end follows the progress (`live_window_end`, #760, #812): the effective
   arrival + 1 h, never before the planned arrival + 1 h (an early Start must
   not close the window before the flight could have flown), or
   `LANDED_GRACE` (15 min) after a landing (entered, app-detected or inferred
   from a fix) became known (one tick commits the landed state, then the
   flight leaves). **Started flights tick every 5 min** (#812): half-way
   between two verification cycles the loop runs `LiveTick.run(…,
   started_only=True)` (`scheduler._run_started_tick_once`) over the flights
   `FlightProgress.started` holds for (Start pressed or a take-off known, not
   yet arrived). It has no verification fetch to share, so it fetches its own:
   one METAR/TAF batch and one SIGMET fetch, whatever the number of flights.
   Their `live_tick_timing` rows show the 5-min cadence.
3. **The Start update** (`live_tick.run_start_update`, #812): the first fix
   on the ground at the departure (the app posts one right after Start)
   schedules it from `POST …/live/position`, after the response. The ↻ path
   with a fresh fetch, then the highlight regenerated whatever the gate says,
   with `at_new_times` in its facts (below). Never pushes, like a ↻ press.
   `find_live_flights` picks candidates by the planned window widened by
   `MAX_TAKEOFF_DELAY_H` (6 h) and reads only their progress records.
   `push_expiry` uses the same end. The web briefing page mirrors it from
   `/live`'s `progress` (#877, `helpers/live-layer.ts::liveWindowEndMs`):
   `max(planned end, eta_destination + 1 h)`, capped at departure + 6 h +
   duration + 1 h. Past the planned window but before that cap, with no
   progress yet, it reads `/live` once before deciding to poll or open on the
   recap, so a late take-off keeps polling and never opens on the recap.

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
| `GET /api/flights/{id}/live` | `LiveLayerResponse`: the blocks + `*_updated_at` + `progress` (#760) + `changes` (with read-time `trail` / `recently_cleared`, #669) + `last_refresh_delta`. 200 with nulls when nothing live exists for the latest pack. Redacted for non-owners (position sharing, below). |
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
- `sigmets_at_briefing_unavailable: true` — only when the briefing's own
  SIGMET fetch failed (#734): the digest's silence on SIGMETs is "not checked".
  Not shown with `live: null` (no layer yet); the digest says it there.
- `sigmets_live_unchecked: true` — only while the layer has had no good
  SIGMET fetch (`route_sigmets` unset): `sigmets_total: 0` is "not checked".
- `airports` — departure, destination and the top alternates only (the
  classifier's `airport_roles`), when the corridor fetch has a METAR for them;
  a proxied terminal (#767) lists the station's report under the field's ICAO
  with `metar_station`, `proxy` and `proxy_caption`.
- `progress` — the layer's progress block (#760), redacted per the reader's
  position sharing; `LIVE_NOTE` says what `source` and the nulls mean.

Deliberately not in it: observed radar/lightning/tops arrays and
`cross_section.json` (never exposed, #278). The layer outlives the live window
(until retention T1), so an agent asked after the flight still sees the last
tick — the timestamps say how old it is.

Not overlaid (build-time, correctly the briefing's view): the LLM digest, the text
digest, alternate requirement.

## Flight progress (#759, #760, #761)

Every "where is the aircraft" question the live layer asks goes through one
object, `analysis/flight_progress.FlightProgress` (beside `RouteTrack`): distance
flown, departed / arrived, the ETA at a point along the route, the arrival time,
the position at a time. `live_layer.flight_progress(route, departure, now)` builds
it once per tick and every consumer reads it: the classifier's `departure_at` /
`arrival_at` / `flown_nm`, `build_storms` (abeam ETA, ahead, the estimate's 4-D
track), `build_glance` (ribbon ETAs, passed lines, the "ahead" segment), the
read-time trails (`progress.at(t)` rebuilds what a past tick measured against),
the ↻ path's fallback classification, and `replay_live_history.py score-estimates`.

- **Sources**: the plan (`source = "plan"`: on-time departure, constant speed
  over `flight_duration_hours`), corrected by the owner's manual take-off /
  landed entries (`plan+takeoff`, #760 PR 2) and by app GPS fixes (`app`,
  #761, below). A manual entry always overrides what fixes infer. The plan-only refactor (#760 PR 1)
  was checked byte-identical against the code it replaced (LELL→LEMI, with and
  without synthetic storms, every tick's `/live` body incl. trails); with no
  entries the arithmetic is still the same float-hours code, so a flight without
  entries is unchanged to the bit (the iOS tick fixtures only gained `progress`).
- **Manual entries** (`ProgressEvent`, history records of `type: progress`):
  a take-off replaces the planned departure (`departed_at`), a landing replaces
  the arrival, and with both the speed is the one flown. Within a kind the entry
  recorded last wins (an edit); `at: null` withdraws it. A landing not after the
  departure is ignored. **No "late = not departed" rule from silence**: without
  an entry or fix the plan says departed at the planned time, because a
  missing press is not evidence of being on the ground. Only an app fix on the
  ground at the departure moves it (#761, below).
- **As of a time**: an entry counts only from its `tick_at` (when the server
  learned it), so `progress.at(t)` (read-time trails) rebuilds exactly what the
  tick at `t` knew and an entry never rewrites what was already shown.
- **App GPS fixes (#761)**, rules in the `flight_progress` module docstring
  (constants beside them). **Premise: fixes are sparse corrections to the
  plan, not its replacement.** Pilots don't watch the app live; the common
  case is a few fixes when the app is opened to check the weather (the
  locate button, the fix taken on opening), then nothing for an hour. Each
  rule is chosen so a fix, however old or odd, never makes the progress
  worse than the plan. (The first cut extrapolated the freshest fix at its
  instantaneous GS: one climb fix put every ETA ~1 h late until the next
  fix, which may never come. Revised 2026-10-17, plan on #761.)
  - *What a fix shows*: from its height above the terrain under it when
    known (`terrain_ft`, recorded at receipt from the pack's elevation
    profile at the fix's along-route distance): airborne at ≥ 1000 ft,
    on the ground below unless ≥ 50 kt (fast and low is a car or the
    climb-out: shows nothing). Without a height, from its GS: airborne at
    ≥ 50 kt, on the ground under 40 kt. Else unknown (a one-shot fix often
    has no valid speed). A fix > 25 NM cross-track is not this flight (a
    weather check from home) and is ignored outright. **Only an airborne fix
    starts the flight**; once started, any fix places it.
  - *Anchor = a delay, not a speed*: the freshest fix after the departure;
    `flown(t) = along0 + v·(t − t0)`, capped at the route's end, with `v` the
    **planned speed**, so ETAs are the plan's shifted by the delay the fix
    shows. Instantaneous GS (a climb, a turn, a gust) is never extrapolated.
    `v` is scaled by the speed flown between the anchor and an earlier fix
    (or an entered take-off) ≥ 15 min and ≥ 20 NM apart, the ratio clamped
    0.7–1.4. A fix that reaches the server late (no data in flight) moves no
    anchor but still measures speed. `off_route` at > 10 NM cross-track
    (planned speed, the projection kept, no diversion detection). `estimated`
    once the fix is > 20 min old; it keeps going at the plan's speed.
  - *Never backwards*: fixes are replayed in the order the server learned
    them; one up to 10 NM behind what the previous anchor showed at that
    moment holds that position as a floor (the marker waits for the
    aircraft; a passed airport doesn't flap). Further behind, it is taken as
    it is when > 10 min after the previous fix (a new observation after a
    gap: with sparse fixes, a real correction), else only when the next fix
    agrees (jitter). ETAs inside the hold read as passed when the hold began.
    **The first fix always replaces the plan**, even behind it: a late
    departure being found.
  - *Take-off*: inferred from the first airborne fix after the last one on
    the ground, back-dated to along 0 at the planned speed, never before
    that ground fix; a later ground fix at the departure resets it. It is
    `departed_at` / `delay_min` like a manual one but **never observed**: the
    recap's `time_base` is `observed` only with an entered take-off and an
    entered (or app-detected) landing; anything a fix infers makes it
    `partial` ("partly observed", "flown 08:29"), never the plan's wording.
  - *On the ground at the departure (Start, #812)*: the newest telling fix
    on the ground within 3 NM of the departure, no take-off known:
    `departed_at = fix + TAXI_MARGIN` (10 min), **earlier than the plan
    too**. Pilots press Start early in the preflight for a last weather
    check (Start → take-off 10–30 min) and don't keep the planned time
    precise; taking the time then beats waiting for a better one, and the
    first airborne fix corrects it. With nothing newer the flight is taken
    to leave then and land the planned duration later (the common case:
    no fix after Start). Accepted cost: a check at the airfield an hour
    early moves every ETA an hour early until the next fix. (Was
    `max(planned, fix + 10 min)`, #761.) The server cannot see the button:
    the iOS app sends no fix for a flight before Start is pressed on it (the
    fix on opening, the PIREP form's and the map's locate button all wait
    for `grantConsent`), so the first ground fix is the press. Owner,
    2026-10-22: Start is pressed once in the aircraft, from boarding to just
    before take-off; the 3 h window start stays the only bound. An explicit
    `start` flag from the app is #813's.
  - *Started* (`FlightProgress.started`): a ground fix at the departure or
    a take-off known, not yet arrived; `start_fix` is the first ground fix
    at the departure, which stays after the take-off (push consent).
  - *Landing*: the app's auto-stop posts `landed` (`source: app`), timed at
    the batch's **first** fix on the ground at the destination
    (`first_landing_at`, #812; the newest fix's time when none is), not its
    newest fix (+8 min against +1.4 min in the test). A fix not airborne
    within 5 NM of the destination, once
    flying there at 1.4× the plan could have got there, infers the landing at
    the earlier of the fix and the expected arrival (the apron one-shot after
    landing). Entered and app landings win over it (`landing_event_at`); a
    manual landing wins over the app's but an app one counts after a manual
    clear.
  - *The next flight closes the previous one* (#812): the Start fix (the
    first on the ground at a flight's departure; later apron fixes would only
    repeat the lookup, which runs in the request) is also recorded on the
    owner's previous flight
    (departed in the 24 h before, newest first) when it is at that flight's
    destination, that flight is in its live window and has no landing. It
    is that flight's fix from then on (projected on its route, its terrain),
    so its own rule infers the landing, never later than the fix. Pilots
    rarely open the app after landing; the next leg's Start is often the
    first fix after it.
  - *Projection*: done once at receipt against the latest pack's route and
    stored; re-done here only when the route in hand differs in length by
    > 0.5 NM (a pack with another route). The live window and push expiry
    (`FlightProgress.schedule`, no route) read the stored projection, so a
    fix-found late departure keeps the window open to the real arrival.
  - Plan-only and manual-only flights are untouched: every fix rule is behind
    "an anchor / ground fix / inferred take-off / inferred landing exists".
- **`POST /flights/{id}/live/position`** `{fixes: [{at, lat, lon, alt_ft?,
  gs_kt?, track_deg?, h_accuracy_m, along_nm?, cross_track_nm?}] (≤ 5),
  landed?}`, owner only, appended under the commit lock
  (`record_progress_events`). Values are bounded (GS ≤ 600 kt, altitude
  −2,000…60,000 ft; else 422). Each fix is recorded, with the terrain under
  it, or listed as ignored with a reason: `future` (5 min skew),
  `outside_window` (before departure − 3 h or after the live window's end as
  of now), `accuracy` (> 1000 m, `MAX_FIX_ACCURACY_M`: iOS approximate
  location), `duplicate` (an app fix at the same time is recorded; the app
  resends unsent fixes), `too_close` (< 30 s from a recorded one: the
  server's own spacing, not trusting the client's) or `limit` (600 app fixes
  per flight). The response is the progress block as of now; the first
  ground fix at the departure also schedules the Start update (Writers,
  above), any other fix waits for the tick. A
  device along more than 5 NM off the server's is logged
  (`LIVE_POSITION_XCHECK`); each post logs `LIVE_POSITION` with the newest
  fix's age.
- **Retention / growth**: one record per accepted fix, ≤ ~12 an hour while the
  app is open in the window (Start Flight, foreground, 5-min poll), capped at
  600 a flight, ~400 bytes each; raw lat/lon lives only in the history, whose
  retention (90 days, #711) covers them; deleting the flight removes its live files with the packs (`storage/flights._live_files`), and deleting the account removes the user's whole `packs/` directory. Viewers below `full` sharing get the
  route position, never the fix's lat/lon.
- **Fix age at use**: each tick's `live_tick_timing` row records
  `progress_source` and `fix_at` (migration 106); the admin latency report's
  `fix_age` hop is `committed_at − fix_at` for `app` ticks. This is the data
  for the `UIBackgroundModes: location` decision (#759).
- **Reading entries cheaply**: `load_progress_events` pre-filters lines by the
  compact `"type":"progress"` spelling, built from `HISTORY_SEPARATORS`, the
  same constant `_append_history` writes with.
- **The debrief recap** (#711) builds its `FlightProgress` with the entries
  too (`live_recap.flight_progress_for`); `planned()` drops them for a
  cancelled flight; `time_base` is `partial` with one entry, `observed` with
  take-off and landing.
- **Who writes**: `POST /flights/{id}/live/progress` `{event, at?, clear?}`,
  owner only (there are no editors today), appended under the commit lock by
  `record_progress_event`. No refetch and no recompute; the next tick (10 min)
  measures from it; the response is the progress block as of now so the client
  can confirm. (App fixes and the app's landing have their own endpoint, below.) Validation, all against the **latest pack's** departure (what
  the tick measures from, not the flight row's, which may have been edited
  since): not in the future (5 min skew), a take-off within `departure − 3 h …
  + MAX_TAKEOFF_DELAY_H` (6 h; later = change the flight's departure time), a
  landing after the effective departure, and no take-off edit or clear that
  would leave a recorded landing before it (it would silently stop counting).
  An unreadable pack is a 409 before anything is written.
- **The `progress` block** (`LiveProgress`, on `LiveLayer`, `/live`, the agent
  block): `source`, `as_of`, `along_nm` / `route_nm`, `lat` / `lon` (the route
  position), `departed_at` / `landed_at` entered, app-detected or inferred from a fix,
  never from the plan alone (null on the plan, so no client shows "Landed"
  from a schedule), `eta_destination` (effective
  arrival), `delay_min` (take-off − planned). `fix_at` / `fix_age_min` /
  `off_route` describe the fix in use (#761), empty without one;
  `estimated` is true on the plan, manual entries and a fix > 20 min old.
- **Everything moves with it**: classifier cut-offs (`departure_at`,
  `arrival_at` = the landing), ribbon ETAs / `flown_nm` / `arrival_at`, glance
  passed flags, storm `abeam_eta` and estimates, trails, TAF-at-ETA (the ↻ path
  passes the effective departure as `run_route_weather`'s `target_time`, which
  only places airport ETAs; observation-vs-model comparison and SIGMET windows
  stay on the plan; since #812 each airport's TAF is read at
  `progress.eta(along)`, the ribbon's own ETA, not the effective departure
  plus a share of the planned duration), the live window and push expiry. A landing puts `flown_nm`
  at the route's end, so the highlight gate reads "arrived" and generates
  nothing. A shifted ETA can itself make a TAF-at-ETA row appear (the baseline
  was read at the planned ETA): that is the TAF at the actual ETA, intended.
- Consumers must not compute schedule arithmetic of their own; add what they
  need to `FlightProgress` (`FlightProgress.schedule` for times without a route).
  Inside the object, every computation (`timed`, `arrival`, `flown_at`, `eta`)
  measures from `departed_at`, the effective departure. Only `departed_at` reads
  `planned_departure`, so one source swap moves them all. The ribbon's
  `departure_at` deliberately stays the planned departure. An observed take-off
  goes in the progress block instead.
- `timed` = a departure and a duration (flown distance, departed, arrival).
  ETAs and positions also need a non-zero route: a local flight (same airport at
  both ends) has an arrival time but no ETAs, and `flown_nm` 0.
- Still on the plan: the flight-day email choice
  (`dispatch.flight_day_email_applies`, planned arrival + 1 h) and the refresh
  classification (`api/packs._classify_refresh_time`, 3 h grace); the ribbon's
  `departure_at` (deliberately). The highlight facts still word the phase as
  "arrival planned …" (the times are right, the word is not).

### Position sharing (#759, #760)

`flights.position_sharing` (`none` / `along_route` default / `full`, migration
104, owner-only `PATCH /flights/{id}/position-sharing`). The owner always gets
`full`; anyone else (a share link, a follower, a viewer emailing themselves the
flight-day brief, an agent reading a shared flight) gets the owner's setting
via `live_layer.sharing_for_viewer`. One helper per shape:
`redact_live` (`/live`, which the share pages read), `redact_summary` (the
agent block, `/live/summary`, the on-request flight-day email) and
`overlay_live(…, position_sharing)` (the snapshot / bundle overlay, whose
`live_changes` and `last_refresh_delta` messages carry the same times).
- `along_route`: drops the progress block's `lat` / `lon` only (the fix
  fields `fix_at` / `fix_age_min` / `off_route` stay: they place nothing). Position is
  still known to within the route's cross-track error: help text must say
  "progress along the route", not "private".
- `none`: no progress block; ribbon `flown_nm` / `arrival_at` / every ETA,
  storm `abeam_eta` / `minutes_to_abeam` / `estimate` nulled; the "~HH:MMZ"
  estimate times stripped from glance lines, change, cleared and delta messages
  (`strip_eta_text`; only ETAs use the "~" form). The highlight is dropped:
  it is model-written, so its times need not use the "~" form. Accepted
  leaks (#759): rows still drop passed airports and glance lines carry
  `passed`, so coarse progress is inferable.
- Redaction fails closed: `live_summary` returns null on any error.

## Radar storms (#688)

Rule in meteorology-decisions §41. Pieces:

- **Input**: `observed/storms.py::load_cell_frames(now)` reads the droplet's
  `DisplayStore` (the node's pushed display files, `cells-overlay.md`): the
  newest frame at or before `now`, plus ~3 earlier ones 10 min apart. Status
  `available` / `stale` (> `STALE_AFTER`, 25 min) / `disabled` (ingest off) /
  `unavailable`; never raises. `run_realtime_refresh` passes it to
  `commit_live_update(cells=…)`; `None` reads as a dark feed.
- **Suspect echoes** (#696, meteorology-decisions §42): every cell the droplet
  speaks about passes `storms.operational_cells`, the one gate shared by the
  storm rows, the §41 alerts that read them, the glance and the ribbon's
  weather bands — so the three cannot disagree about which echoes exist. It is
  a **no-op unless `WB_CELLS_CLUTTER_SUPPRESS` is set** (read per call, so
  flipping it is a web-app restart, not a deploy); with it on, cells the node
  marked `suspect` or `confirmed` are dropped before grouping, and from each
  storm's earlier-frame history too, so a suppressed cell cannot return through
  the off-track trail. A `core` band whose every member was suppressed is
  dropped rather than reported at the tier floor; a mixed band keeps the peak of
  what is left; a `rain20` ring the node marked as the echo's own skirt
  (`suspect_outlines`, #702) is skipped, so no band of any tier remains. Dropping a cell is **not** a claim the sky is clear there: the
  cell stays in the display file with its reasons and the overlay can draw it.
- **Geometry** (`build_storms`, droplet side, no analysis): cells grouped by
  the node's `within` (core41 → its core35; older files: nearest core35 within
  its equivalent radius + 3 NM); each storm projected on the route polyline
  (`analysis/route_geometry.RouteTrack`, local equirectangular per segment) at
  the member nearest the track: `along_nm`, `offtrack_nm`, signed `cross_nm`,
  `side`, or `end` + compass from the airport past the route's ends;
  `abeam_eta` from the flight's `FlightProgress` (the plan today, like
  `flown_nm`); `relative_motion` = the velocity's component toward the
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
  (`scripts/replay_live_history.py`, prod-briefings-review skill) joins them with later
  frames. Growth: ~10 storms × ~30 ticks ≈ 100 KB on a showery flight, gone
  with the history (90 days, never for a kept flight; #711).
- **Replay**: `scripts/replay_live_history.py replay … --cells DIR` (promoted
  from the skill) reads display files as of each tick by their mtime (= when
  the node wrote them; `on_mini.py cells` keeps it).

Gotchas: a storm's key is its core35 id, so a core35 that loses the dominant
link in a merge starts a new row (and can alert again). Coverage for the
station fallback is read at the route point nearest the airport along track,
not at the airport itself.

## Observed tab: glance, ribbon, focus (#690)

Plan in `designs/future/observed-tab-presentation.md` §3–5. Server, iOS (PR
#695) and web all landed. `tasks/live_glance.py::build_glance` runs in `commit_live_update` right
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
  of `{phase, text}`) and `highlight` (`{text, written_at}` or null, #697),
  word for word what the apps show; `LIVE_NOTE` and the MCP instructions say
  to quote the highlight with its written time.
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
  band on top; `RouteRibbonLegend` underneath. Without `weather` the radar
  strip hugs the line and storms are points. **Tapping a mark opens the
  inspector card under the ribbon (#747)**, never the map directly — see the
  bullet below. They replace `ObservedGlanceCard` only when `glance` is present,
  and only for the pack on screen (`BriefingViewModel.liveLayerForPack`). A ↻
  response has no glance: the last one for the same pack is kept until the
  next `/live`. Tap-to-map: `FocusIntent.mapFocus` → `RouteMapView` turns on the
  focus's cells/radar and passes `focusRegion` + a counter key to
  `RouteMapKitView`, which frames it once per key. The map draws no SIGMET
  polygons yet, so a SIGMET focus only frames its area; `metar` layers and the
  focus `time` are ignored too (newest frame). The applied focus is cleared
  (`onFocusApplied`) so a recreated map never re-frames a stale one.
  - **Inspector card (#747, iOS).** One tap gesture over the whole drawing;
    `RouteRibbonInspectorRules.hits` picks the marks within 22 pt (generous,
    for turbulence; no long-press): marks *under* the finger first, top paint
    layer winning (station > cell > SIGMET > core > rain > radar stretch), so
    a disc inside a rain area picks the disc; then the others nearest first.
    Two or more → chips at the card's top. A core with a `storm_id` whose
    cell is listed *is* that cell (as on the web); `bandAt` / `bandBinRects`
    are the same rects the Canvas fills. The card (`RibbonInspectorCard`)
    shows the web tooltip's rows from the mirrored rules, the raw METAR/TAF
    (airports joined by ICAO to `/live`'s `route_observations`, else the
    snapshot's), "Show on map" + a tappable ICAO (the only ways to the map).
    A rain area or core has no focus of its own: its "Show on map" frames
    the radar stretch under its middle (`bandFocus`, the last stretch past
    the end), as the pre-card weather-zone tap did.
    `StormDetailSheet` is gone: its extra sections (trend numbers, off-track
    history, backing stations, the labelled estimate + footnote) sit under
    the cell card's "More" (`stormMore`), keeping `stormDetail` /
    `stormEstimate` / `stormShowOnMap`. Selection is a `RibbonMarkKey`
    (ICAO + role, so a round trip's departure and destination stay two
    marks; storm id, band id, SIGMET id, segment index) kept across `/live`
    refreshes; `RibbonInspection.pruned` drops chips whose mark vanished and
    closes the card when the selected one did. ✕, the same mark again or bare
    ribbon closes it. Regular width: rows left, raw reports right. Each mark
    is an accessibility element (`ribbonStation-<icao>-<role>`, `ribbonStorm-<id>`,
    `ribbonBand-<id>`, `ribbonSigmet-<id>`, `ribbonSegment-<n>`) whose
    activation selects it; the card's rows (not the raw reports) are
    announced. The "core of a listed cell is that cell" rule lives once
    (`cellId`), and `availableKeys` reads the models, held equal to
    `targets` by a test. Choices: hover
    stays web-only and the web keeps its click-to-map; the card keeps
    "Cloud top: unavailable" (under More) that the tooltip omits.
- **Web**: two new collapsible sections, `observed-glance` ("At a glance") and
  `observed-ribbon` ("Along the route"), first in the sidebar's Observations
  group so the page reads in the iOS Observed tab's order: glance → ribbon →
  radar → METAR/TAF → SIGMET (`sidebar-layout.ts::NAV_GROUPS`, order pinned by
  `briefing-section-order.test.ts`). `ts/visualization/observed/`:
  `nutshell-view.ts` and `ribbon-view.ts` (an SVG re-laid out on resize, not
  scaled — the marks are px-sized, as `GeometryReader` gives the SwiftUI side),
  over the pure `ribbon-core.ts`. Rendered from `state.live` in
  `renderObservedLive`, on its own subscriber guard: the poll replaces the
  layer while the pack and snapshot sit unchanged for an hour.
  Tap-to-map goes through `RouteMapRenderer.focusBbox` (pads a degenerate box
  and caps `maxZoom`, so one cell does not zoom to the tile limit) after
  switching to the `split` layout and turning on the focus's layers; a cell
  opens `stormDetailHtml` in the shared info popup. `focusMapOn` honours the
  two layers the web map has a switch for — `cells`, and `radar` (only when
  the overlay is not already on a radar product, so a deliberate rain-rate
  choice survives). `route` is always drawn, `lightning` rides with the
  overlay, and `metar` / `sigmets` have no toggleable layer here.
  - **Hover tooltips (#742, web only):** with a mouse, each ribbon mark says
    what it is. An airport shows its name, role, METAR category now, TAF at
    ETA and the raw METAR / TAF, joined by ICAO to the same response's
    `route_observations.airports` (else the snapshot's). A cell shows
    strength, position, motion, lightning and top. The rain or core band
    under the pointer shows its span, side, distance and motion (a core with
    a `storm_id` shows its cell). SIGMETs and radar stretches are covered too.
    The content and `bandAt` (the inverse of `bandRects`) are pure in
    `ribbon-tooltip.ts`. Touch keeps its tap. The iOS tap card (#747) says
    the same, from the mirrored `RouteRibbonInspectorRules.swift` (SYNC
    header both sides; `ribbon-tooltip.test.ts` ↔
    `RouteRibbonInspectorRulesTests.swift`).
  - **Deliberate, not a gap:** like iOS, the web honours only part of the
    focus contract — it ignores `time` and draws no SIGMET polygons. There is
    no SIGMET polygon layer on the route map and no frame stepper for `time`
    to point at, so both would be new map features, not wiring. `time`
    becomes meaningful with the radar/satellite loop (#653). Don't re-open
    this as a bug against the client.
  - **Gotcha, cost a bug once:** `glance` / `ribbon` / `storms` ride **only**
    on the `/live` response — `overlay_live` deliberately leaves them off the
    snapshot. `briefing-store.loadLive` therefore stores the layer even when
    the snapshot patch is a no-op (same tick, no new trails); the earlier
    `if (!next) return` dropped it and left both sections permanently empty.
- **Keeping the two clients in sync.** The rules are one pair, diffed symbol by
  symbol: `web/ts/visualization/observed/ribbon-core.ts` ↔
  `app/.../Views/Briefing/RouteRibbonRules.swift`, each with the other's path
  in its header and a symbol map in the Swift one. Both are pure (no DOM, no
  View), and `tests/unit/ribbon-core.test.ts` ↔ `RouteRibbonRulesTests.swift`
  assert the same inputs against the same expected strings — change a rule on
  one platform and the other platform's test is what tells you. Two
  divergences are deliberate and documented in both headers: **MVFR is amber on
  web, blue on iOS** (the web badges MVFR amber in every table, so matching iOS
  would paint one airport two colours on one page), and the **dBZ hexes** come
  from the web's own VIP ramp while the *boundaries* (35 / 41 / 50, the cell
  tiers) are shared exactly. Registered in the `sync-ios-web` skill.
  Two traps the pair hit and now guards: JS `Math.round` breaks ties toward
  +∞ while Swift's `.rounded()` breaks them away from zero (reflectivity is
  the one mirrored input that can go negative), so every web label rounds
  through `roundHalfAway`; and Swift `compactMap` keeps `""` where the web's
  `filter(Boolean)` drops it, so the Swift joins go through `[String?].present`.
  Both are asserted on both sides.
  `web/tests/unit/observed-live-fixtures.test.ts` renders the **same** exported
  `/live` ticks the iOS UI test consumes
  (`flyfun-weatherUITests/LiveScenarios/`, written by
  `scripts/export_live_scenario_ios.py` and pinned to the server by
  `test_live_scenarios.py`), so a server-side shape change surfaces as a failing
  web test rather than an empty section. Those archived ticks have a dark cells
  feed, so they exercise the radar-strip fallback; the bands path and the
  interactions are covered by `web/tests/observed-ribbon.spec.ts`.
  - The web has **no standalone cells list** (iOS's `ObservedCellsSection`):
    cells live on the route map's Cells toggle and the maps page's "Now" tab,
    and the ribbon plus the cell detail now cover the route-relative reading.
    "Since this briefing" also stays a page-level banner on web
    (`refresh-delta-banner`) rather than a section in the group.

### Runway + wind dials (#758)

Departure + destination dials in the glance section: web `#observed-runway-wind`
inside `#observed-glance-wrapper` (shown when glance lines **or** a dial
exist), iOS `RunwayWindPairCard` above the ribbon (or above
`ObservedGlanceCard` in the fallback), spy anchor `winds`. Flight day only:
both require the live layer. Data is `AirportObservation.runway_wind` from the
live tick's airports (else the pack's), see
[metar-taf-route-weather.md](metar-taf-route-weather.md). The departure shows
the METAR; the destination adds the TAF at ETA as an outline "ghost" arrow and
a second line. No picture on either airport (older server) → no dials at all.

Pure rules mirrored `runway-wind-core.ts` ↔ `RunwayWindRules.swift`
(`/sync-ios-web` §5b); renderers `runway-wind-view.ts` (SVG, viewBox 0–100) and
`RunwayWindView.swift` (Canvas). Schematic, not a chart: north-up at true
heading, through the centre, parallels side by side by L/C/R, length ∝ runway
(floor 40 %, unknown = full). The arrow starts at the upwind rim and points
downwind; length on a fixed 35 kt scale with a floor. The crosswind figure is
toned by the server's advisory (amber/red); no new threshold. Sizes `compact`
/ `regular` / `inline` (inline unused in v1). The web re-renders only when the
HTML changed, so an open "All runways" survives an unchanged poll.

### Flight strip and destination at ETA (#816)

The Observed top reorganised around the pilot's in-flight questions (plan in
`future/observed-tab-presentation.md`). Two more glance blocks, built in the
same `build_glance` call, so they inherit its "never raises" guard:

- **`glance.flight`** (`LiveGlanceFlight`, `live_glance.flight_strip`): the
  `progress` block in words, null when the flight is untimed. `phase`:
  `planned` (nothing observed: "Planned · departure 09:00Z · ETA LPPT 10:30Z",
  then "Planned · 120 of 280 NM · …", then "Planned · arrival …", never
  "Landed" from a schedule), `ground` (an app fix on the ground at the
  departure before the take-off it implies: "On the ground at LPPR 08:52Z ·
  take-off ~09:02Z · ETA …"), `enroute` (take-off entered or seen: "120 of
  280 NM · ETA LPPT 10:32Z (+12 min vs plan) · position 4 min ago", ",
  estimated" past `STALE_FIX`, "estimated from take-off HH:MMZ" without a
  fix, "last fix on the ground HH:MMZ, estimated" once past a ground fix's
  take-off, "off route"), `landed` ("Landed LPPT 10:35Z", "~" when inferred).
  "vs plan" is the ETA (or landing) minus `FlightProgress.planned_arrival`,
  shown only off the plan. `marker_label` captions the ribbon's position mark
  ("You · 4 min ago" for a fresh app fix, "Estimated position", "Planned
  position", "Landed"). **Choice:** the server has no "Start Flight" event, so
  the issue's "Started HH:MM" is the ground fix's time.
- **`glance.destination`** (`LiveGlanceDestination`, `destination_block`):
  the arrival line split into fields (same helpers, so the same words): METAR
  now with the proxy caption, TAF at ETA with TEMPO/PROB, `wind_text` (TAF wind
  at ETA, else METAR wind now, with the best runway's crosswind and the
  server's existing `*_wind_advisory` tier; no new threshold), the cells
  within `TERMINAL_NM` each with its storm focus, lightning. Clients show it
  **in place of a non-alert arrival line** (an alert one stays in the alert
  group); the arrival line itself is unchanged for the agent and the highlight.
- **Redaction:** `position_sharing=none` drops `glance.flight` (it is the
  progress block in words), nulls `destination.eta` and runs every
  destination clause (METAR, TAF, wind, cells, lightning, each cell's text)
  through `strip_eta_text`. They carry no estimate today; the strip keeps a
  future wording from leaking one. `along_route` keeps both.
- **Agent:** `summarize_live` adds `glance.flight` `{phase, text}`.
- **Reading order, both clients:** strip → alert lines (and on iOS alert
  change rows) → highlight or headline → lines not yet `passed` → destination
  → (iOS) winds, ribbon, Details with the passed lines / (web) passed lines
  dimmed. **This reverses the 2026-10-09 "highlight above alerts" order**: the
  issue's target layout puts alerts second. iOS spy pills gain "Ahead"; the
  highlight caption adds its age, orange past `LiveTime.staleAfter` (30 min).
- **Not done in #816's first PR** (follow-ups): the iOS Cells list on `/live`
  storms, the offline banner, tappable change rows (`storm_ids` is decoded),
  changes collapsed to a count, phase emphasis beyond the strip, the XCUI
  before/en route/after-landing journey.

### Airport cards: departure block and fold (#880)

Server half of the departure / arrival airport cards (#737; web #881, iOS
#882, Android #883). Every rule the card needs is decided here so the three
clients only draw. Additive and optional throughout.

- **`glance.departure`** (`LiveGlanceDeparture`, `departure_block`): the
  destination block's twin read at the ETD. Both now come from one helper,
  `_terminal_block`, so the words match ("TAF at ETD …", "Wind at ETD …",
  "no TAF for ETD"); `etd` is `FlightProgress.departed_at`.
- **`airport`** on both blocks: the terminal's `effective()` row (its own, or
  with the proxy station's METAR/TAF laid in, #767). The card reads that one
  object, so no client picks the station row itself. ~3–5 KB per block in
  `live.json` and `/live`. Not stored in `live_snapshots.jsonl.gz` (it is a
  METAR/TAF row, like `route_observations`): `tick_view` re-attaches it from
  the rebuilt rows, so `/live/at` has it without the runway picture.
- **Fold rule** (`departure_fold`): by evidence, never the plan. A take-off
  event (`folded_source="takeoff"`), else a take-off inferred from fixes,
  else the Start fix `FOLD_AFTER_START` (15 min) old **and**
  `progress.departed` (past the take-off that fix implies; a newer ground fix
  moves it, so a pilot still parked stays open), or any landing (all
  `"fixes"`). A late flight with nothing observed stays open. `folded_at` is
  the take-off, else `departed_at`; `folded_text` is the whole folded line
  ("LFPN 290/15G25 · RWY 25R · departed ~10:09Z", `~` for an estimate; the
  category box is drawn from `metar_category`).
- **Redaction:** `none` nulls `etd`, strips every clause, and keeps a fold
  only when `folded_source == "takeoff"`; a fixes fold would say where the
  flight is. `redact_summary` applies the same rule to the agent block.
- **Agent:** `summarize_live` `glance.departure` `{icao, metar_text,
  taf_text, wind_text, folded, folded_source}` and `glance.destination`
  `{icao, metar_text, taf_text, wind_text, cells_text}` (the destination was
  not in the agent block before).
- The airport-level fields (`taf_hours`, `metar_trend`, meter, sky, weather
  words, short wind, crosswind) are in `metar-taf-route-weather.md`.

## Observed highlight (#697)

One or two sentences above the nutshell saying what deserves attention on the
route ahead, written by Claude Haiku 5.5 (#715; Haiku 4.5 before) from a facts
block the code computes.
**Code does the weather, the model phrases it**: no analysis by the model, so a
highlight can never say something the tick did not already know.

Written but hidden 2026-10-07 → 10-09 while the first flight days were
reviewed; **displayed since 2026-10-09** (owner: start showing and adjust, no
further calibration gate, no What's New entry).

### Display and feedback (#697 slices 1–3)

- **Reading order, both clients** (superseded by #816 above: strip and alert
  lines now come before the highlight): the highlight first, at body size, with
  one gray caption "Experimental, still being calibrated. Thanks for flagging
  issues. · written HH:MMZ" (the written time stays: a carried-forward line
  can be older than the layer) and 👍/👎; then the alert-tier nutshell lines
  (and on iOS the alert-tier change rows), never folded; then the ribbon. The
  owner moved the highlight above the alerts on 2026-10-09 after seeing it on
  real flights (the plan had alerts on top). No highlight (before the first
  generation, a rejected state, after arrival): the nutshell `headline` in its
  slot, no caption, no thumbs. Never styled as an alert.
- **iOS** (`ObservedHighlightView.swift`): a "Details" fold below the ribbon,
  collapsed by default and remembered (`@AppStorage("observedDetailsExpanded")`),
  holds the headline (when the highlight took its slot), the other nutshell
  lines, the map button and the other "Since this briefing" rows
  (`LiveChangesView(excludesAlerts:)`). A plain button, not `DisclosureGroup`,
  so the toggle exposes `expanded`/`collapsed` to the XCUI helper. UI-test
  launches clear the remembered state (`AppState.setupUITestSession`), and the
  helper `openObservedDetails` judges "open" by the fold's content
  (`observedNutshell`) or the toggle reading `expanded`, retapping only if the
  content is absent and the toggle still reads collapsed: a blind retap on a
  lagging value closed it again (#803).
- **Web** (`nutshellHtml`): the highlight (or the headline), alert lines, then
  the headline and the other lines **unfolded** (documented divergence: the
  desktop has room).
  Alert change rows stay in the page-level "Since this briefing" banner.
- **Rating:** `POST /api/feedback` with `target="live_highlight"`,
  `category="highlight_rating"`, and `context` = the four `/live` fields
  (`facts_hash`, `generated_at`, `model`, `text`) verbatim; the server keeps
  only those keys as bounded strings, in `feedback.context` (TEXT holding
  JSON, migration 100 — repo convention, not `sa.JSON`). The text is stored
  because the line is regenerated; `facts_hash` keys back into
  `live_highlights.jsonl`. Digest-rating limiters, no LangSmith mirror (no
  trace id). Admin `kind=ratings` covers both rating categories and shows the
  rated line; the admin email quotes it. Dedup is session-only, keyed
  `flight|facts_hash` on both clients.
- **Fixtures:** the suite has no API key, so the replayed ticks serve
  `highlight: null`. `IOS_HIGHLIGHT_TICKS` (tests/live_scenario_replay.py)
  exports one extra `<scenario>_<HHMM>_highlight.json` with a fixed,
  hand-written line that passes `check_grounding` against its tick (pinned);
  the drift test applies the same injection. Web vitest, Playwright and the
  XCUI journey render both the null fallback and the highlight.

### Model choice (#715)

**Haiku 5.5, thinking off, effort `low`, `MAX_WORDS` 60 (70 since 2026-10-09), `MAX_TOKENS` 300.**
Chosen on an A/B of the first flight day's 311 logged facts blocks
(2026-10-08, offline replay through this prompt and checker):

- **Better where 4.5 failed:** it led with three TS SIGMETs over 0–90 NM that
  4.5 omitted; read "130 NM along, 19 NM left of track" where 4.5 wrote
  "130 NM left"; didn't invent rain "from behind".
- **~7x cheaper per call** ($0.24 vs $1.75 per 1k) after its ~30 % larger
  tokenizer. p50/p95 0.85/1.2 s.
- **Wordier.** At the old 40-word ceiling it was rejected 26 % of the time,
  almost all 41–50-word lines; 50 and 60 rejected the same. The owner chose 60
  (prompt target 35).
- **Thinking off** because adaptive thinking at `low` added nothing visible and
  cost a 4 s p95, empty replies and `max_tokens` stops (thinking counts toward
  `max_tokens`). With thinking off the model can narrate its own drafting into
  the reply — see the drafting rule under Grounding check.

Final configuration measured at **~7 % rejected** (22/311; 4.5 was 6 %), half
of it "Watch LFAC…" caught as advice. Run-to-run variance is large: small
prompt edits moved it between 7 % and 16 %, and an extra "one paragraph, no
notes" output line made it *wordier* (median 38 → 45 words) — the checker
handles leaks instead. A refusal (`stop_reason: refusal`; 5.5 has safety
classifiers and no server-side fallback) is logged as a `rejected` attempt
with its category and cost, so it counts toward the retry cap.

### Off the critical path

`commit_live_update` is unchanged except for one pure call, and the model runs
*after* it returns:

1. **Commit** writes the layer as before, and `live_highlight.carry_forward`
   re-attaches the previous highlight unless the gate (below) sees a
   significant change
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

**The patch has its own stamp, `highlight_updated_at` (#815).** Clients fetch
`/live` only when a stamp on `/packs/latest` moved, and iOS `applyLive` / web
`loadLive` refused an equal `live_updated_at`, so a client that fetched in the
~50 s between commit and patch never saw that tick's highlight (in flight the
gate moves almost every tick, so nearly every one). The patch does **not** bump
`live_updated_at`: that is the tick's identity, read by `commit_live_update`'s
"a newer write landed first" check (a bumped stamp would drop the next tick),
the latency join (`live_delivery` → tick by version) and the snapshots. Instead
it sets `LiveLayer.highlight_updated_at` (reset to null by every commit) and
writes it into `live_meta.json`; `/packs/latest` and `/live` serve it, and both
clients fetch when it moves and take the same tick when only the highlight
differs (iOS `refreshesHighlight`, web `highlightChanged`). A moved stamp with
the same text counts as a change too: adopting it is what advances the held
stamp, otherwise every poll would refetch.

### Regenerated on Start (#812)

The Start update (Writers, 3.) calls `ensure_highlight(…, retimed_from=gate)`
with the gate state of the highlight the pilot had before (`{}` without one):
a new highlight is written even when the gate would carry the old one, since
Start is the moment the pilot is looking (in the test it was 23-37 min old).
The facts get `at_new_times`: `"no change"` or `"changed: <gate keys>"`
(`gate_changes` against that state), and one prompt rule makes the line open
with "No change at your new times." or what changed. Not in the gate: the
stored gate state is the new baseline as for any generation. While on the
ground after Start the phase reads "on the ground at the departure, times
estimated from the pilot's Start", with no take-off figure: it moves with
each ground fix and the gate ignores it (item 5 below).

### What counts as "changed" (the regeneration gate, #706)

The first real flight day (2026-10-08, 17 flights) made **311** calls where a
hash of the facts *strings* was the gate: every half-hourly METAR's time,
every ±100 ft of ceiling, every radar frame's cell figures and 5 NM rain
edges, storm change rows flickering in and out, and an airports-ahead count
that ticks down as each is passed. 19 calls reproduced the previous sentence.

Now `facts_and_gate(live)` builds the facts block **and a gate state** in one
pass, and `gate_changes(previous, current)` decides. The previous state is the
one **stored with the highlight** (`LiveHighlight.gate`), i.e. the state at the
last *generation*, not the previous tick: tick-to-tick lets slow drift through
(2400 → 2900 → 3400 ft one step at a time), and a fixed bucket compared with
the last generated state regenerates once on a crossing instead of flapping.
A carried highlight keeps its baseline. `gate_hash` (stored in `facts_hash`)
is the cheap first check, and also what the retry cap counts per.

What the gate holds, and so what the facts show:

| Part | Gated as | Not a change |
|---|---|---|
| Airport METAR | category + its driver word ("low ceiling"), convective level tags (TS/CB/TCU, as `convective_tags`), weather **families** (showers, rain, snow, fog, freezing, hail…, heavy marked), wind advisory band (or "gusts" ≥ 25 kt without runway data) | obs time, exact ceiling/vis/gust, runway id, `-SHRA` ↔ `SHRA`, mist/haze |
| Route airports | every notable one ahead, **uncapped**, by ICAO | passing one (`along < flown − 5`); the next sliding into the 12 cap |
| SIGMETs | exactly, **id included** (the highlight quotes ids, a reissue must regenerate) | — |
| Rain on track | stretches on a **25 NM** grid; 10 NM coverage as a band none / < 25 / < 50 / ≥ 50 % | edges within a bin; `main_rain_area` (shown, not gated) |
| Cells ahead | nearest band (≤ 3 / ≤ 10 / corridor NM), any lightning, any closing ≤ 10 NM, any developing ≤ 10 NM; since #790 each flagged cell's place **at anchor level** ("over the sea after coast-out (Middelburg)", "near Middelburg (EHMZ)", `_coarse_place`), so moving to another anchor regenerates | dBZ, along, side, abeam time, closing speed, the NM from the anchor |
| Change rows | METAR/TAF/SIGMET rows by identity `key|direction|to_value` | storm / radar / lightning rows (dropped: `cells_ahead` is the radar source); re-wording; improvement count |
| Phase | before departure / en route / arrived | the flown figure |

**The rule that ties the two halves (item 5): what the gate ignores, the
facts don't show.** Otherwise a carried-forward line quoting "ceiling 2400 ft"
or "42 dBZ at 102 NM" goes stale silently. So the facts lost every figure in
the table's right column, and `test_the_facts_never_show_a_figure_the_gate_ignores`
moves all of them at once and asserts the facts are identical except `now`,
the flown figure in `flight` and `main_rain_area` — the three deliberate
exceptions (the model may say where the aircraft is; the motion word is not a
figure). Cost of the rule: the model can no longer place a cell along the
route or quote its dBZ; if that turns out to matter, add a position to the
gate (e.g. thirds of the route) and to the facts together, never one alone.

**After arrival nothing is generated or carried** (item 6): 34 of the 311
calls came after the planned arrival, about "ahead" cells already abeam in the
past. The layer falls back to the nutshell headline. Since #760/#761 `flown`
follows `FlightProgress`, and since #815 `_phase` reads the layer's `progress`
block so the plan alone never arrives (two late legs lost the highlight before
take-off): a landing (entered, app, or inferred from a fix) is arrived at once;
an effective arrival from a take-off or fix is "arriving" for `ARRIVAL_GRACE`
(20 min) and then arrived; plan only (`source == "plan"`) past the planned
arrival is "position unknown" and the facts measure "ahead" from 0, so the
whole route (departure included) is shown until the live window closes. A
layer without a `progress` block keeps the old plan rule. The glance's own
"flight arrived at plan" nutshell line (`live_glance`) still reads the plan;
it is not gated by this.

`main_rain_area` reads only rain bands still ahead (it once said "moving
toward the route from behind" with no rain ahead). A highlight written before
#706 has no stored gate and regenerates once. Each regeneration logs its
reasons at INFO (`Live highlight regenerates for <id>: destination, cells`).
The gate is persisted in `live.json` (it must survive a JSON round trip
unchanged, pinned by `test_the_gate_survives_a_json_round_trip`) but `/live`
serves the highlight with `gate: null`: it is the server's baseline, uncapped
over the route, and no client reads it. An airport row that fails
`AirportObservation` validation reads "METAR unreadable" and is not notable on
its METAR alone; it does not drop the flight's facts.

The issue's offline replay of that corpus estimated **~188 calls (−39 %)** for
this gate; it is not reproduced in the repo (the corpus lives on prod).
Remaining known trigger on busy convective flights: rain edges crossing
25 NM bins (10–18 per flight in the replay) — #706 item 11, not done.

### Grounding check

`check_grounding` is the only thing between a model sentence and a cockpit
screen. It **rejects only on the shape of the reply** and falls back to the
nutshell `headline`; `review_flags` logs `flags` on the written attempt and
never blocks it.

History: on 2026-10-09 (owner) the meaning rules moved from rejecting to
flagging, because on the first Haiku 5.5 prod day 17 of 28 rejections were
them misreading a correct line, each costing the pilot that tick's highlight.
On 2026-10-10 (owner) an audit of 1,224 prod attempts (2026-10-08..10) scored
every rule against its facts, and the set was cut to the rules that pay off:

| Rule | Fired | Real | False |
|---|---|---|---|
| drafting text / line break (rejects) | 2 | 2 | 0 |
| length (rejects) | 23 | 5 drafting leaks | 0 (18 long but right) |
| ICAO not in facts (rejects) | 1 | 1 (EBRD for EHRD) | 0 |
| figure not in facts (was reject, now flag) | 10 | 1 (an invented time) | 7 |
| verdict word | 12 | 1 ("depart with caution") | 11 ("may go MVFR") |
| thunderstorm without lightning | 4 | 4 | 0 |
| thunderstorm at a position (dropped) | 16 | 0 | 16 |
| place binding (dropped) | 33 | 2 | 27 |

20 accepted unflagged lines sampled alongside: none clearly wrong.

**Rejects** (`check_grounding`):
- **Drafting text**: a line break or drafting words ("corrected",
  "instructions", "the facts", "wait", "highlight") (#715: with thinking off,
  Haiku 5.5 wrote "Wait, that contains 'watch'… Corrected highlight: …", and
  on the prod day "Highlight (word count under 35):"). Every leak seen had a
  blank line.
- **Length**: over `MAX_WORDS` (70; the prompt asks for 35). In practice it
  catches drafting leaks.
- **ICAOs**: every aerodrome code must appear in the facts. `\b[A-Z]{4}\b`
  matches `LIFR` and `TSRA` too, so `_NOT_ICAO` holds the colliding weather
  codes.
- Empty, truncated (`max_tokens`) and refused replies.

**Flags only** (`review_flags`, logged as `flags` and as `LIVE_HIGHLIGHT_FLAGGED`):
1. **Verdict words**: never go/no-go (`feedback_not_go_nogo`). Not "go"
   itself ("LFRG and LFRC go MVFR"). "monitor" flags under its own reason,
   `advice word`. **"watch" is allowed** (owner, 2026-10-08): "Watch LFAC,
   MVFR…" points at the airport rather than telling the pilot what to do.
   Naming it in the prompt made Haiku 5.5 write around it ("Watch-free
   note: …"), so advice words are never named there.
2. **"Thunderstorm" needs lightning**: §41, a radar core is a "cell". A
   stated absence ("no lightning", "without thunderstorms") does not count.
   The gate reads the whole facts blob, so any `TS` in it licenses the word.
3. **Figures**: every number should appear in the facts (keys included, so
   10 and 30 NM thresholds pass). It flags rather than rejects because the
   model derives span widths ("the final 3 NM" of 129–132 NM). The prompt
   still asks for every figure exactly as the facts give it.

**Dropped** (2026-10-10): place binding (a clause naming one airport had to
claim only that airport's conditions) and "thunderstorm at a position". They
misread spans ending at an airport ("rain from 125 NM to EGJJ"), lists read
respectively ("EGJA and EGJB are IFR and LIFR") and an airport's TAF TSRA in
a clause with a figure. While they rejected, they also pushed the model into
worse wording (a TAF's TSRA rewritten as "showers"). The category misses they
were meant to catch are a prompt line instead: give each airport's category
exactly as the facts give it, and keep "now" and "at ETA" apart.

### Retries on a rejection

The tick picks up any flight with no stored highlight, and a rejection stores
nothing, so uncapped a single bad facts block bought a rejected sentence every
tick for the whole live window — 33 billed calls on one flight. Zero retries
would be wrong the other way: the model is stochastic (the same tick comes back
worded differently run to run), so a transient bad line would cost that flight
its highlight until the weather moved.

`MAX_ATTEMPTS_PER_FACTS = 2`, counted per **gate state** (`gate_hash`) from the review log
(`rejected_attempts`) — no extra state to carry across ticks. That count runs
every tick for every flight without a highlight, so it rejects lines on raw
text before parsing any JSON; `skipped_rejected` deliberately does not satisfy
that filter, or the cap would tighten itself every tick. A bad draw gets a
second chance; a systematic failure costs twice, not 33 times. Only `rejected`
counts: a `written` one is carried forward anyway, and a `call_failed` one is a
timeout that cost nothing and is right to retry. When the gate state moves the hash
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

**Size.** One full record is ~1.8 kB (the facts block is most of it); a
`skipped_rejected` marker is 155 B. After the cost gate and the retry cap a
flight generates roughly one record per facts state — ~6 for a 1.5 h flight,
~10 for 3.5 h — so **11–18 kB per flight**, or 25–40 kB in the worst case where
every state is rejected twice and the rest of the window logs markers. Nothing
prunes it: it is **kept until the flight is deleted**, which `storage.flights._live_files`
handles. A `call_failed` record carries **no** facts block: there is no text to
judge against them, and a timeout retries every tick by design (it costs
nothing and is right to retry), so with the block attached a sustained outage
wrote ~1.8 kB per flight per tick for as long as it lasted. At these sizes a cap would be more machinery than it saves; revisit if
the facts block grows or the window lengthens.

**Measured rejection rate: 2 of 120 generations (1.7%)** across 8 facts shapes,
and both were correct catches — "proceed with caution" (a verdict) and "LFMC
now shows thunderstorm activity" when LFMC reports TCU. No false positives in
that run. Getting there took three fixes, each found by measuring rather than
reasoning: the figure rule's span arithmetic (prompt), the 41-word overruns
(the prompt now states the 40-word hard limit and says to drop the least
important item rather than shorten every clause), and the SIGMET-edge false
positive above.

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
- **Reviewing real layers locally**: `scripts/ops/import_flight.py --list`
  / `--live` / `FLIGHT…` copies prod flights (rows exported from the
  container, flight dir tar-streamed out of it) into the dev DB under the same id, owned by
  the dev-login user. The flight dir gets `live_frozen` (`LIVE_FROZEN_FILE`,
  in `LIVE_FILES`), which the tick skips, so a running devserver neither
  overwrites prod's layer nor pays for a local highlight; a ↻ press still
  refreshes it. Import **while the flight is live**: none is written after
  planned arrival and `live_history.jsonl` keeps reports, not layers
  (`--highlight-at HH:MMZ` patches an earlier written line onto a finished
  flight's final layer, text only). Web fetches `/live` only inside the window
  or for a `days_out == 0` pack; iOS has no window check.
- **Cost** ~$0.00024 per call on Haiku 5.5 (~$0.0016 on 4.5), through the
  shared ledger (`action=live_highlight`, priced by `compute_call_cost`, never
  the per-briefing `compute_cost`). Admins see it as "Other LLM spend" on the
  Cost tab and per-user cost page; pilot totals and per-briefing figures
  exclude it (#741, see `cost-attribution-design.md`). A `max_tokens` stop is rejected as
  `truncated`, not graded. Charged
  on the tick's own thread: a `Session` is not thread-safe, so
  `ensure_highlight` returns the usage and the caller charges it.
- Prompt, facts block and check live in `tasks/live_highlight.py` — the code
  the tick runs. `scripts/live_highlight_experiment.py` imports them, so the
  replay harness cannot drift from production.
- **Client parity**: `LiveHighlight` is mirrored in `web/ts/store/types.ts`
  and `Models/API/LiveGlance.swift` (optional; `gate`/`latency_ms` are not
  decoded). The highlight block is a `SYNC —` pair (`highlightHtml` ↔
  `ObservedHighlightCard`); the iOS Details fold is the documented divergence.
- One **process-wide Anthropic client**, built lazily under a lock
  (`_anthropic_client`). The fan-out opens up to `_HIGHLIGHT_WORKERS` threads
  and a client per call meant a new HTTP connection pool per flight per tick;
  the SDK client is safe to share, building it is what needs the lock.
- Prompt caching and streaming are both no-ops here and deliberately absent:
  the request was ~1.4 k tokens against Haiku 4.5's 4096-token minimum cacheable
  prefix (~1.7 k on 5.5's tokenizer; re-check its minimum before relying on
  caching if the prompt grows), and the client gets the text as one JSON field.

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
  drops pending SIGMETs beyond it for one tick. So a baseline SIGMET missing before its
  start is not "gone", and `pending_sigmet_key` keeps the alert memory of a missing
  SIGMET whose trace is still before its start. Both rely on the trace
  (`ClassifierMemory.sigmets`) holding `valid_from`.
- **Fetch status (#686, §46).** `RouteSigmets.fetch_ok` / `queried_at` come
  from euro_aip `fetch_isigmet_result`. A failed base query never reaches the
  layer: `run_realtime_refresh` turns it into `None` (stored SIGMETs kept),
  `SharedSigmetSource` raises `SigmetSourceUnavailable`, and
  `classify_changes` skips a `fetch_ok=False` block anyway.
- **Failed at build time (#734).** The pipeline still stores the empty
  `fetch_ok=False` block as the baseline (§46, "Not changed"); only what is
  shown changed. Web (`helpers/route-sigmets.ts::sigmetSectionState`), iOS
  (`RouteSigmets.unavailable` / `showsSection`, which also offers the Observed
  tab), the text digest and the HTML/PDF report show
  `SIGMETS_UNAVAILABLE_TEXT` (`models/observations.py`, three hand-kept copies:
  Python, web i18n `sigmets.unavailable`, iOS `RouteSigmets.unavailableText`,
  pinned equal by `tests/test_route_weather.py`);
  the LLM prompt gets a "NOT CHECKED" section; the agent `live` block carries
  `sigmets_at_briefing_unavailable: true`. The notice goes once a good fetch
  lands: the live tick / ↻ never store a failed block, and `overlay_live` (and
  the clients' patch) replace `route_sigmets` with the live one. The digest
  keeps saying "not checked" (build-time, not overlaid).
- **Cancelled (#686, §46).** `_cancelled_traces`: a trace last seen pending,
  missing, not superseded, and `isigmet_covers(latest.queried_at, …)` gets
  `cancelled_at` and a `sigmet_cancelled` highlight row; `pending_sigmet_key` then
  releases its memory. Briefing SIGMETs have no trace in `_trace_sigmets`
  once missing, so `_cancelled_traces` builds one (or takes it from `seen`)
  and keeps it in the memory while it is cancelled; `gone` skips them.
- `LiveChange.observed_at` for a pending SIGMET is in the future; clients
  show no age for it. For a cancelled row it is when the cancellation was
  seen.
- **After arrival (#689).** `classify_changes(arrival_at=…)` (the landing,
  `FlightProgress.arrival`, the planned one today; both writers pass it). A row whose
  SIGMETs all start after arrival + `SIGMET_AFTER_ARRIVAL_MARGIN` (30 min)
  is highlight and leaves `chain_alerted` false.

## Observed latency (#751)

How long a report takes from the time printed on it to a pilot's screen, per
hop, tracked over time. Code: `tasks/live_timing.py`; admin view: the
Performance tab (`GET /admin/live-latency`, `web/ts/admin-latency-view.ts`).

**Definitions (agreed on the issue).**
- *Report time* is the time on the report: METAR/SPECI obs, TAF issue, SIGMET
  valid-from, radar/cells frame valid time. When a provider published it is
  unknown and not estimated.
- *Fetched*: when the tick's shared fetch got that airport (`LiveTick._fetched_at`,
  stamped in `sink`/`_top_up`), not the block's `fetch_time`, which is when the
  refresh assembled the block from the cache, later. SIGMETs:
  `SharedSigmetSource.fetched_at`.
- *Available*: the tick that first carries the item committed `live.json`
  (`committed_at` = the `live_updated_at` it wrote). The highlight is its own
  hop after that.
- *Delivered*: the first time a client receives a `live_updated_at` at or after
  that tick. Today the next `/live` poll (iOS every 5 min while open).

**Two tables, no foreign keys** (migration 102, every datetime `DATETIME(6)`
because the delivery join is an equality on `live_updated_at`):
- `live_tick_timing`: one row per flight per tick, written after
  `_highlights` so the highlight columns (`gated` = carried forward, no call;
  else `HighlightOutcome.outcome`) land in the same insert. `commit_live_update`
  fills a `CommitTrace` (committed version, the history records it appended,
  the cells frame's built/ingested times); a refused (stale) commit writes no
  row. `new_items_json` lists what the tick showed first: each report from
  *this* tick's fetch (a pack switch also records the briefing's own reports,
  which are not new) and each alert-tier `appeared` event, anchored on its
  evidence time (one with none is counted as `end_to_end:alert:no_evidence_time`
  under "Not counted", not timed from its commit). Only the tick writes rows: a ↻ press commits versions no row
  describes (deliveries of those count as `untracked`). `progress_source` /
  `fix_at` (migration 106, #761) give the `fix_age` hop: how old the app fix a
  tick measured from was.
- `live_delivery`: one row per (flight, user, platform, version), on `/live`
  (platform from the User-Agent: `ios` / `web` / `other`), `/live/summary`
  (the MCP server) and ChatGPT `getBriefing` (both `agent`). iPadOS is not told
  apart (the app sends URLSession's default agent). Dedupe: an in-process cache
  of the newest version per key, falling back to the table, plus the unique
  constraint for a concurrent poll. The cache assumes the single uvicorn
  worker prod runs (refresh-durability.md); with several, each worker would
  pay its own table read, and the constraint would still hold one row. `delivered_via = push` and `push_sent_at`
  are written for each live-alert push sent (#754, below); a later poll of
  the same version is then not a second delivery.

**Derived, never stored.** `latency_report` computes per-day p50/p95/max per
hop in Python over the window (~700 tick rows a day): report → fetched,
fetched → available (both per kind), available → highlight (written only),
available → delivered (per platform), report → delivered (METAR, SIGMET,
alerts, per platform: the earliest delivery on that flight serving a version
at or after the tick), cells built → droplet, cells frame → available, tick
duration (once per tick). Negative spans are dropped and counted.
"Available → delivered" is labelled "(poll)": with a 5-min iOS poll it
measures mostly the poll cadence, not server work; push would shorten it.
The admin endpoint caps the window at 90 days (rows are aggregated in
Python).

**Cells times.** The node's build time is the display file's mtime, which
`rsync -t` (`cells/push.py`) carries to the inbox; ingest keeps it in memory
(`cells_display.computed_at`). It is deliberately not written into the display
file, which must stay byte-for-byte reproducible by a replay. After a restart
it is null until new frames arrive (the tick reads frames < 25 min old).

**Never fails the caller.** Rows are written in a savepoint and every writer
catches and logs. `charge_highlight` writes and commits each ledger row on its
own session on the tick's engine (#815): on the tick's session, open across the
whole tick, an earlier failure surfaced as a recurring "Live highlight cost
charge failed" MySQL traceback (the highlight itself written), and a failed
ledger flush left that session needing a rollback. A failure is now one warning
line naming the error, traceback at DEBUG. Before #751 nothing committed that
session after the tick, so tick-written highlight costs were flushed and
dropped; the tick still commits it before the latency insert. Retention: `LIVE_LATENCY_RETENTION_DAYS` (180), purged with the daily
analytics rollup. Account deletion removes the user's deliveries and their
flights' tick rows; the account export includes `live_deliveries`.

## Live-alert push (#754)

The tick's one consumer of `new_alert`: `notify/live_alerts.py`, called per
committed flight from `LiveTick._push_alerts`, after the commits. It runs on
its own thread and DB session (flight rows re-read there) alongside the
highlights: the text is deterministic so it does not wait on a model, and a
slow APNs host does not hold up the highlights. Joined before the tick's
commit.

- **Only a commit that wrote** pushes: `CommitTrace.layer` is the in-memory
  layer it committed (its changes still carry `evaluated`, never dumped). A
  refused commit has none; its stored `new_alert` belongs to the other writer.
  A ↻ press never pushes (the pilot is looking; #751 records the poll).
- **Memory**: `LiveLayer.push_state` (`LivePushState`): `active` (pushed, not
  yet cleared, with a sustain counter), `rearmed` (keys whose clear was
  pushed), and three counters for shadow review. Written by
  `live_layer.patch_push_state`, a small write under the commit lock that
  checks only the pack (a ↻ commit in between leaves it valid). Carried by
  `commit_live_update`'s copy of the prior layer, reset by a new pack.
- **Decision** (`decide` → `next_state`, pure): alerts = `new_alert` rows,
  plus rows for a re-armed key (§47), plus rows whose earlier push failed;
  clears = active keys with no alert-tier row for 2 evaluated ticks. Storms
  push, never clear. A skipped push (pref off, muted…) tracks nothing but
  still drops due clears, so unmuting does not release stale ones.
- **A failed push is retried.** One that raised or reached no device has
  spent its `new_alert`, so its keys go to `push_state.retry` (first failed
  attempt) and are re-sent on later ticks while still alert-tier rows, until
  `PUSH_TTL` (30 min) after that attempt, when the push would have expired
  anyway. A skipped push is not retried.
- **Shadow → sending.** Each `active` entry records whether it was written in
  shadow mode. Once sending is on, a due clear of a shadow entry is dropped
  (`LIVE_PUSH_CLEAR_DROPPED … reason=recorded_in_shadow`), never pushed: the
  pilot never had that alert. So `WB_LIVE_PUSH_SEND` can be flipped mid-flight.
- **Shadow mode** until `WB_LIVE_PUSH_SEND=1`: decisions logged
  (`LIVE_PUSH_WOULD_SEND` / `LIVE_PUSH_SKIPPED reason=…`), memory advanced as
  if sent. `LIVE_PUSH_SENT` and `LIVE_PUSH_OPENED` (the tap's
  `/live?source=push`) once sending.
- Eligibility, payload and APNs headers: ios-app-briefing-notifications.md
  → "Live-alert push (#754)".

## Route locator (#790)

`analysis/route_locator.py` turns a route position into pilot words, so the same
point reads the same way on the glance, change rows, highlight facts, ribbon,
Radar & lightning cards and the agent block. Built once per tick in
`commit_live_update` (`_build_locator`) and passed to `classify_changes`,
`build_glance` and `build_summary_entries`; `None` everywhere falls back to the
old "N NM along" wording (a layer or pack written before #790, a locator failure).

- **Anchors** (the ladder, `LiveRibbon.anchors`): named route waypoints (lat/lon
  fixes skipped; a doubled-back route anchors each pass, `EHMZ@234`), the route
  airports with a METAR/TAF that project inside the route **within 10 NM of the
  track** (`ANCHOR_OFFTRACK_NM`; the ribbon's stations), and coast-out / coast-in
  (`analysis/coastline.py`). Without the cap two airports 25 NM either side
  project to one point and read "Rotterdam near Antwerp" (EDLD→EGTF replay,
  2026-10-17). Stations 10–20 NM off (`ABEAM_OFFTRACK_NM`) are a fallback only
  where no anchor is within 20 NM, always "abeam": "5 NM before abeam Alicante
  (LEAL)"; they are not on the ladder, and also name coast points
  ("coast-in (Southend-on-Sea)"). Names (`display_names`): nav.db `municipality`
  (on `AirportObservation.municipality`) without its county; where several route
  airports share a town ("London") each reads by its short airport name
  ("Gatwick", "Biggin Hill", "London City"). The ribbon's `RibbonStation.name`
  is the same name.
- **Rules**: ≤ 5 NM "near X"; ≤ 20 NM "N NM after/before X" (nearest wins);
  over water "over the sea, N NM after coast-out (X)" / "mid-crossing"; on land
  with nothing within 20 NM "between A and B" (named anchors before coast points);
  off the ends "29 NM east of departure" / "15 NM past the destination". A
  position more than 10 NM off track reads "abeam X" rather than "near X", and
  over a crossing "N NM after coast-out (X)" without "over the sea" (an
  off-track airport is on the coast, not at sea). Lateral
  only past 3 NM, kept in `RoutePlace.lateral` so a line that states it (storm
  rows) does not repeat it. `text` names the town ("Middelburg (EHMZ)"), `short`
  the ICAO; change rows and cards use `text`, the glance uses `short`.
- **Hysteresis**: `locate(prior=)` keeps last tick's anchor while it is within
  3 NM of being the nearest, and "near" until 8 NM. Only storms carry it
  (`LiveStorm.place`, read back from the previous layer by lineage id in
  `_place_storms`); spans (SIGMETs, radar hits) are recomputed each tick.
- **Structured**: `RoutePlace` on `RibbonStation`, `LiveStorm`, storm/radar
  `LiveChange` rows; `RibbonSigmet.span_text`. The highlight facts are built from
  the serialized layer, so they rebuild the locator from the ladder
  (`RouteLocator.from_ladder`, along-track only); its prompt now says places "as
  the facts word them, never as a distance along the route", and `review_flags`
  flags an "N NM along" (non-blocking).
- **Time**: `when_text` ("~10:20Z" before departure, "in ~25 min" in flight) is
  used by the glance only. Change rows keep the clock: they are pushed and read
  later, where a relative time is stale (a departure from #790's ask).
- **True along-track**: airport positions come from euro_aip's signed
  projection (`along_track_nm` / `cross_track_nm` / `route_end`, rzflight, euro_aip
  ≥ 0.23) via `AirportObservation.along_nm`, falling back to the legacy
  `enroute_distance_nm` (closest segment start + straight distance: puts an airport
  behind the departure ahead of it) on older packs or an older euro_aip. ETA clamps
  a negative position to the departure; `airport_relevant` keeps such an airport
  until take-off. Ribbon stations can now sit at `along_nm < 0` or past `route_nm`.
- **Coast data**: Natural Earth 10m land clipped to Europe and simplified
  (`analysis/data/europe_land.json.gz`, 270 KB, rebuilt by
  `scripts/build_europe_land.py` with shapely as a build-time-only dependency).
  Pure-python even-odd ray cast over edges bucketed in 0.05° latitude bands;
  crossings cached per route. Water under 8 NM (estuaries) is no crossing, land
  under 3 NM inside one (islets) does not split it; outside the bbox is land.

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
- `analysis/flight_progress.py` — `FlightProgress`, `ProgressEvent`, `FixData`, `progress_events` (#759, #760, #761: fix fusion); `live_layer.flight_progress` builds it per tick, `progress_block` makes the `LiveProgress` block, `load_progress_events` / `record_progress_event` read and append the entries; `live_tick.live_window_end`; position sharing `live_layer.sharing_for_viewer` / `redact_live` / `redact_summary`; tests `tests/test_flight_progress.py`, `tests/test_live_progress.py` (incl. the LELL→LEMI replay with a take-off 40 min late), `tests/test_live_position.py` (the same morning with a fix stream and with occasional one-shot fixes), `tests/test_api.py::TestLiveProgress` / `TestLivePosition`
- `tasks/live_glance.py` — `build_glance`: the Observed tab's nutshell, ribbon and map focus (#690); `flight_strip`, `destination_block` (#816, tests `tests/test_live_glance_strip.py`), `departure_block` / `departure_fold` (#880, `tests/test_live_glance_departure.py`)
- `analysis/route_locator.py`: `RouteLocator` (`build`, `locate`, `locate_along`, `span`, `ladder`, `from_ladder`), `for_route`, `display_names`, `town_name`, `short_airport_name`, `when_text`; `analysis/coastline.py`: `is_land`, `coast_crossings` (#790); tests `tests/test_route_locator.py`, `tests/test_route_locator_wiring.py`
- `tasks/live_highlight.py` — `facts`, `facts_hash`, `check_grounding`, `generate`, `carry_forward`, `ensure_highlight`, `charge_highlight`: the model-written highlight (#697); `live_layer.patch_highlight` is its second write and `live_tick._highlights` its fan-out
- `api/packs.py` — `live_router` (`/flights/{id}/live`, `/flights/{id}/live/summary`, `POST /flights/{id}/live/progress`, `POST /flights/{id}/live/position`), overlay in snapshot/bundle; `api/flights.py` `PATCH /flights/{id}/position-sharing`
- `tasks/live_timing.py` — `build_tick_row`, `write_tick_rows`, `record_delivery`, `latency_report`, `purge_old` (#751); `live_layer.CommitTrace`
- `notify/live_alerts.py` — `notify_live_alerts`, `decide`, `next_state`, `skip_reason`, `build_payload`, `push_expiry` (#754); `live_layer.patch_push_state`; tests `tests/test_live_alert_push.py`
- Tests: `tests/test_live_layer.py`, `tests/test_live_significance.py`, `tests/test_live_tick.py`, `tests/test_api.py::TestLiveLayerEndpoint`, `tests/test_live_summary.py` (agent block, incl. the 08:30 LELL→LEMI tick), `tests/test_live_trail.py` (trail rules, LFBZ→LFMD day, LELL→LEMI replay), `tests/test_live_storms.py` (storm geometry, §41 tiers, backing/fallback, estimate log and scoring), `tests/test_live_glance.py` (nutshell, ribbon, focus; an LPPR→LPPT-like synthetic day), `tests/test_live_highlight.py` (grounding rules, carry-forward, refused patch, API failure), `tests/test_mcp_live.py`, `tests/test_agent_endpoints.py` (live block + `/live/summary`), `tests/test_live_timing.py` (#751 latency rows, dedupe, end-to-end join)

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
