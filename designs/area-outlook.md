# Area Outlook

> Indicative "is a flight even worth briefing?" read of the forecast map around a base or along a route, over several days (#836). Agent-only (MCP `area_outlook`, ChatGPT `getAreaOutlook`); no web/iOS UI yet.

## Intent

Pilots ask agents "is Saturday or Sunday better for Le Touquet?" or "which direction is flyable from my base this weekend?". Creating a flight per candidate is slow and expensive, and `get_airport_weather` by hand is one slot per call, ~2 KB per airport, no spatial selection. The outlook does the selection, grading and aggregation on the server so the LLM only narrates. It is an **attention-director, never a briefing**: the output says so and points to `create_flight` for the slot the pilot picks.

## Structure

```
MCP area_outlook ──► mcp/client.area_outlook ──► GET /api/maps/area-outlook ─┐
ChatGPT getAreaOutlook ─► GET /agent/v1/area-outlook ────────────────────────┤
                                     api/maps.area_outlook_response (400 on bad input)
                                                   │
                     tasks/area_outlook.build_area_outlook   (I/O shell)
                        ├── map_queries.watchlist_coords      (monitored airports)
                        ├── resolve_query → OutlookQuery       (selection)
                        ├── map_queries.load_forecast_slots    (bounded, all slots at once)
                        └── compute_outlook                    (pure: grade, aggregate, pair)
```

- `load_forecast_slot(db, day, hour, airports_db)` is the single-slot cache-first loader (`/maps/airport-weather`). The outlook uses `load_forecast_slots` instead (below). `forecast_grid.forecast_hour_for` is the one `(day, hour) → datetime`.
- `area_outlook_response` is a plain function both routes call, so no handler is used both as a route and called directly with `Depends` defaults.
- `forecast_grid.day_coverage_note()` builds the "which models on which day" text in the MCP and agent `day` descriptions from `MAP_FORECAST_DAYS`, so it moves with the grid.

## Database cost (review round 1)

One call may want up to 35 slots (7 days × 5 hours). Looping `load_forecast_slot` would be fine on cache hits but, on a miss, run one **full-map live query per slot**, and misses are common: `is_stale` marks every relative-day entry stale after **UTC midnight** until the next forecast cycle, hours later. `load_forecast_slots` bounds the work instead:

| Step | Query | Index |
|---|---|---|
| newest snapshot | `MAX(fetched_at)` | `ix_afs_fetched` (one index seek) |
| cache metadata | `cache_key IN (<the grid's ≤35 keys>)`, no blobs | `uq_vcache_key` |
| blobs not in the LRU | one `IN` read of `data_json` | `uq_vcache_key` |
| uncovered slots | ≤ `max_live` (1) `get_forecast_map_data` | `ix_afs_hour_model` |

- Entries are matched on the **absolute** hour they hold (`computed_at` date + relative day, confirmed by the payload's `forecast_time`), so after midnight yesterday's `day=4` serves today's D+3. Only the new horizon day is uncovered.
- An entry older than the newest snapshots is **served and reported** (`stale_slots`): the previous model run beats recomputation. Uncovered slots past the live budget go to `missing_slots`.
- Parsed blobs (~1.4 MB JSON each) are projected to what grading reads (`project_slot`) and kept in a per-process LRU of 20 slots keyed by `(projection version, cache_key, computed_at)`. A generation is immutable, so repeat calls read no blobs (2 queries). Bump `SLOT_PROJECTION_VERSION` when the projection changes.
- `tests/test_area_outlook.py` counts statements on SQLite: 3 for an all-cached call, 2 on a repeat.
- No new index needed. The live path's `MAX(model_init_time)` per model is the costliest step and is now capped at one slot per call.

## Selection

- Only **monitored** (watchlist) airports. A base/destination that isn't monitored is replaced by the nearest monitored one; `query.*.substituted_nm` reports it, and `unrepresentative` is set past 20 nm.
- **Radius** (default 60 nm): up to 16 airports, round-robin across N/E/S/W sectors (quadrants centred on the cardinals) nearest-first, so a dense quadrant can't crowd out the others.
- **Route** (default corridor 20 nm): the route string goes through the same Field-15 tokenizer + `resolve_waypoints` as `interpret_route`; up to 12 airports projected onto the track with `RouteTrack.project`; points beyond either end are skipped. Over the cap the route is cut into 12 along-track bins and the closest-to-track airport per bin wins first, so a long route keeps its middle.

## Grading (per airport, per slot)

Reads the map's baked `consensus` / `consensus_majority` (same mode the caller picks), so category, ceiling, visibility and crosswind are exactly what the forecast map shows. Gust and alternate-required aren't in the consensus block: gust is max (worst) / median (majority) of the models; alternate-required is any / strict majority.

| Axis | GO | MARGINAL | NO-GO |
|---|---|---|---|
| VFR ceiling | ≥ 1500 ft | 1000–1500 | < 1000 ft |
| VFR visibility | ≥ 5000 m | 3000–5000 | < 3000 m |
| IFR cloud | no alternate required | alternate required (FAA or EASA) | below the approach class's worst-case DH/vis (`APPROACH_CLASS_PROXY` hi ends; an IAP of unknown class gets the non-precision default, via `map_queries.approach_classes`) |
| Crosswind | < 15 kt | 15–25 | ≥ 25 kt |
| Gust | < 25 kt | 25–35 | ≥ 35 kt |
| Convective risk | none / marginal | low | moderate+ |
| Sun (VFR) | day / civil twilight | | night (< −6°) |

- Wind lines are the **Airport Wind advisory's catalog defaults**, read from `AirportWindEvaluator.catalog_entry()`: one source of truth with the briefing. (The issue first said 20 kt from `approach_feasibility`; that is the approach-alignment limit, not the wind-severity one.)
- IFR at an airport with no instrument approach uses the VFR cloud lines.
- Overrides (`min_ceiling_ft`, `min_visibility_m`, `max_crosswind_kt`, `max_gust_kt`) move the GO line (cloud) or the NO-GO line (wind); the other line clamps so the band never inverts. Not stored in the profile.
- **Confidence**: `low` if category or ceiling agreement is divergent (or < 2 models), `medium` if mixed or a two-model day (D+5/D+6), else `high`. Disagreement is reported, never averaged away.

## Aggregation

- **Radius**: per slot, base row + per-sector `go/marg/nogo`, min ceiling, max gust, worst-confidence, and `worst` only when it isn't GO.
- **Route**: per slot, departure + destination rows + a corridor summary. For legs the corridor counts as one point: NO-GO only when **at least half** its airports are NO-GO, MARGINAL for a minority (an airport can be 20 nm off track and a pilot can avoid one field).
- `best_slots` (top 3, never a NO-GO slot): radius ranks the base grade then the area GO fraction; route ranks the outbound leg.
- `trend` per day: GO fraction (GO 1, MARGINAL 0.5) of the first half vs last half of the day's slots, ±0.25 → improving / deteriorating.
- **Pairs** (destination known): leg time = great-circle / TAS (default 110 kt). Outbound graded at take-off slot for the departure end and corridor, at the slot nearest arrival for the landing end; return must start ≥ outbound slot + leg time, same day. `round_trip` per day: possible / outbound only / return only / no. Top 5 pairs, one per outbound slot.

## Map links

`map_url` (top level = best slot, plus each best slot and each pair's outbound slot) opens `/maps.html?tab=forecast&fc.day=&fc.hour=&fc.apt=&fc.view=` (`fc.model=majority` when asked): the forecast tab's URL contract (`web/ts/visualization/forecast-url.ts`), which the iOS app also opens natively. `fc.view` frames the radius, or the route's bounding box plus the corridor, for a ~700 px map (zoom 4..10); `fc.apt` opens the destination's (else the base's) summary card. Origin is `WEATHER_BASE_URL`, like the agent's briefing links. `fc.day` is **relative**, so a link opened on a later day shows a later date: the map has no absolute-date key yet.

## Output budget

Compact strings, not raw model output: one `GRADE CAT ceiling vis wind xw conf` row per airport per slot in `airports[].rows` (order = `slot_order`); focus airports appear only in `slots`. A worst-case weekend (16 airports, 2 days × 5 hours, destination, with map links) is ~14 KB (~3.5k tokens), asserted < 16 KB in `tests/test_area_outlook.py`.

## Gotchas

- Snapshots carry **no freezing level / cloud tops**: IFR output has `scope: terminal_only` and the note says en-route icing is not assessed. Adding a freezing-level column to the snapshot table is the way to lift that.
- Slots come from the map grid: out-of-grid hours snap to the nearest offered; D+6 is 06/12/18Z only. A slot with no data lands in `missing_slots` rather than failing the call. D+0 slots more than an hour gone are dropped (`past_slots_skipped`).
- With `rules=ifr`, `query.minima` moves the VFR cloud lines under `no_approach_airports_only`, so an LLM never quotes them as the IFR minima.
- Thresholds are presets for an indicative tool, not briefing meteorology; changing them doesn't touch any advisory (see `meteorology-decisions.md` §52).
