# Debrief recap and single-tick replay (server, #711)

Slice A1 of the flight debrief (umbrella #710; plan and later slices in
`future/flight-debrief-observed.md`). Server only: the web UI is #769, iOS later.
Answers two questions after (or during) a flight, from the live layer alone:

1. **What did the Observed section show at HH:MM?** (single-tick replay)
2. **What was observed along the route at the time the aircraft was there, and
   what did the forecast say?** (the recap)

## Pieces

| File | Role |
|---|---|
| `tasks/live_snapshots.py` | Writes/reads `live_snapshots.jsonl.gz`; `observations_as_of`, `find_tick`, `tick_view`, `tick_index` |
| `tasks/live_recap.py` | `build_recap` (pure), forecast lane (`forecast_lane.json`), `recap_for_flight`, `summarize_recap` / `recap_summary` (agents) |
| `tasks/live_layer.py` | `commit_live_update` appends the snapshot; `patch_highlight` appends the highlight record |
| `tasks/artifacts.py::save_advisory_artifacts` | Writes `forecast_lane.json` beside `route_advisories.json` |
| `analysis/flight_progress.py` | `time_base` (`planned`/`partial`/`observed`), `planned()` |
| `api/packs.py` (live router) | `GET /flights/{id}/live/ticks`, `/live/at?tick=`, `/recap[?pack=]`, `/recap/summary` |
| `tasks/retention.py` | `_live_retention`, `kept_flight_ids` |

Agents: `get_briefing` carries `recap` next to `live` (ChatGPT in-process via
`recap_summary`; MCP via `/recap/summary`). Null before departure and when
nothing was recorded.

## Snapshots (`live_snapshots.jsonl.gz`)

One line per `commit_live_update` (server tick and ↻ press), in the flight dir.
Blocks: `glance` (highlight without its gate), `ribbon` (with `weather`),
`storms`, `changes` (no trails), `last_refresh_delta`, `route_sigmets`,
`observed_conditions` without per-station samples (each source keeps valid
time, window, attribution; `stations: []`), plus `tick_at`, pack, `radar_time`
(the ribbon's radar frame) and the `FlightProgress` state (`source`,
`flown_nm`, departed/arrival).

Choices:
- **One gzip member per append**, decompressed member by member on read. A
  failed write is truncated back; a write cut short by a crash leaves a partial
  member that the next append lands right after, so the reader resyncs on the
  next gzip header (`_GZIP_HEADER`) and loses only that member. Reading
  `gzip.open` in one go would lose everything after it *and* raise.
- **Highlight as a second record.** The Haiku line is generated after the
  commit (`patch_highlight`); a `{"type": "highlight", "tick_at"}` line is
  merged into that tick's glance on read. Until then the tick carries the
  carried-forward line, which is what the page showed.
- **`route_observations` is not stored** (spec). The METAR/TAF rows are rebuilt
  from the history `report` records seen by the tick (`observations_as_of`),
  parsed with euro_aip, TAF read at each airport's ETA. The report records carry
  no geometry, so each snapshot keeps a small `observations.airports` list
  (ICAO, name, distances, nearest waypoint, lat/lon; ~0.6 KB gzipped). Not
  rebuilt: the runway/wind picture and wind advisories (need the airports DB;
  the dials are hidden, #758).
- **Size**: the LELL→LEMI replay (busy SIGMET morning, no radar) is ~4.3 KB a
  tick, 158 KB for 37 ticks; owner measured ~6 KB a tick with radar and cells.
  `SNAPSHOT_MAX_BYTES` = 1.5 MB stops appends (logged `LIVE_SNAPSHOTS_CAPPED`);
  a test keeps the replay under 6 KB a tick.
- **Not backfilled.** Flights flown before #711 read `not_recorded`.
  `scripts/replay_live_history.py` stays a dev tool.

## Replay (`/live/at`)

`find_tick` serves the tick at `tick`, else the last one before it (a client
maps a route point to a time). Response is `LiveTickResponse` (the `/live`
shape + `tick_at`, `radar_time`, `progress`); change trails are computed as of
the tick (`trails_for_pack(now=tick_at)`). Round trip pinned in
`tests/test_live_recap.py` against `live_scenario_replay.live_response` for
every block except `route_observations`, which matches on raw text, time,
category and TAF per airport.

## Recap (`/recap`)

Computed per request from the snapshots, the history and one pack's lane:
nothing stored, no table, no migration, so a corrected take-off (#760) applies
on the next read.

- **Diagonal.** Base grid = the newest snapshot's ribbon segments. For each,
  the aircraft time at the midpoint is `progress.eta(mid)`; the snapshot with
  the nearest **radar frame time** wins (tick time only for a snapshot without
  radar). The radar lags the tick by 6-15 min, more than a segment.
- **Outside the recording** (aircraft time > newest frame + 20 min, or < oldest
  − 20 min, `MATCH_SLACK`): `not_reached` / `before_recording`, never the
  nearest tick's echo. Mid-flight this is the not-yet-flown part.
- **Coverage words pass through**: `radar_status` `no_coverage` / `no_sample`
  and `lightning: null` are kept as is; the agent summary counts them as
  "radar unavailable", never clear.
- **Time base** only through `FlightProgress` (`eta`, `flown_at`,
  `departed_at`, `time_base`). The recap's progress is built from the newest
  readable pack's route and departure. Cancelled (debrief `cancelled`):
  `progress.planned()`, labelled `planned`.
- **Status**: `ok`, `no_live_window` (no snapshots, no history: never live, or
  purged by retention; the message does not guess which), `not_recorded`
  (history but no snapshots: flown before #711), `untimed`.
- **Alert markers**: every history `event` record (`raised` / `cleared`, tier,
  direction, message), placed with `progress.flown_at(tick_at)`. No push record.
- **Forecast lane**: `forecast_lane.json` per pack = each advisory's aggregate
  and per-model `highlights.ribbon` bands `[from, to, severity]` (a few KB; all
  advisories and models until #769 decides what to show). Packs written before
  #711 fall back to `route_advisories.json`. Default pack: newest fetched before
  `progress.departed_at`; `?pack=<ts>` picks another (404 if unknown). The
  block carries that pack's `planned_departure` so the UI can show the gap.

## Retention

`LIVE_FILES_T1` at T1; `LIVE_FILES_KEPT` (history, snapshots, meta) at 90 days
unless kept (debriefed; #712 adds "has a report" in `kept_flight_ids`), or
earlier when the flight's packs reach a T2 shorter than 90 days (the pack rows
are the only route back to the flight dir). Pinned flights and flights with a
PIREP-linked pack skip it all. `forecast_lane.json` lives in the pack dir and
follows the pack (T1 keeps it; T2 never touches a debriefed flight's packs).

## Gotchas

- `route_advisories.json` already survives T1 (only heavy files are stripped),
  so the lane file's job is speed (read per pack-picker click), not survival.
- A re-planned route between packs: segments come from the newest snapshot's
  grid, values from whichever snapshot's segment covers the midpoint.
- `tick_view` needs the tick's pack dir for the route (TAF at ETA, trails); a
  pack deleted at T2 still replays, without TAF-at-ETA and trail relevance.
