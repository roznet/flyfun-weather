# Area Outlook

> Indicative "is a flight even worth briefing?" read of the forecast map around a base or along a route, over several days (#836). Agent-only (MCP `area_outlook`, ChatGPT `getAreaOutlook`); no web/iOS UI yet.

## Intent

Pilots ask agents "is Saturday or Sunday better for Le Touquet?" or "which direction is flyable from my base this weekend?". Creating a flight per candidate is slow and expensive, and `get_airport_weather` by hand is one slot per call, ~2 KB per airport, no spatial selection. The outlook does the selection, grading and aggregation on the server so the LLM only narrates. It is an **attention-director, never a briefing**: the output says so and points to `create_flight` for the slot the pilot picks.

### Wording: guidance, not a verdict (#851)

Grades are **PROMISING / MARGINAL / UNLIKELY**, plus **NIGHT** for VFR after civil twilight. The first shipped words were GO / MARGINAL / NO-GO, and agents relayed them as decisions ("the only slot FlyFun grades GO"). FlyFun informs judgement and this tool can't support a decision anyway (point forecasts, no en-route, no operational checks), so the words answer "is this slot worth briefing?". Rejected: "possible / difficult" (a 400 ft / 35 kt slot isn't "difficult", it isn't VFR weather) and GREEN / AMBER / RED (collides with the briefing's grades for the same slot). NIGHT is a rules matter, not weather, so it gets its own label but ranks, scores and pairs exactly like UNLIKELY. The framing is in three places so it survives whichever part an agent quotes: a top-level `guidance` string (first key of the response), the MCP / ChatGPT tool descriptions, and the MCP server instructions' outlook paragraph. Internal `Minima` field names (`ceiling_go_ft` …) kept the old words; the output keys don't.

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
- Parsed blobs (~1.4 MB JSON each) are projected to what grading reads (`project_slot`) and kept in a per-process LRU sized to the map grid (`len(day_hour_pairs())`, so a full-horizon call never evicts itself), keyed by `(projection version, cache_key, computed_at)` and guarded by a lock (sync endpoints share it across the threadpool). A generation is immutable, so repeat calls read no blobs (2 queries). Entries are shared between requests: `compute_outlook` treats `slot_data` as read-only, pinned by a test. Bump `SLOT_PROJECTION_VERSION` when the projection changes.
- `tests/test_area_outlook.py` counts statements on SQLite: 3 for an all-cached call, 2 on a repeat.
- No new index needed. The live path's `MAX(model_init_time)` per model is the costliest step and is now capped at one slot per call.

## Selection

- Only **monitored** (watchlist) airports. A base/destination that isn't monitored is replaced by the nearest monitored one; `query.*.substituted_nm` reports it, and `unrepresentative` is set past 20 nm.
- **Radius** (default 60 nm): up to 16 airports, round-robin across N/E/S/W sectors (quadrants centred on the cardinals) nearest-first, so a dense quadrant can't crowd out the others.
- **Route** (default corridor 20 nm): the route string goes through the same Field-15 tokenizer + `resolve_waypoints` as `interpret_route`; up to 12 airports projected onto the track with `RouteTrack.project`; points beyond either end are skipped. Over the cap the route is cut into 12 along-track bins and the closest-to-track airport per bin wins first, so a long route keeps its middle.

## Grading (per airport, per slot)

Reads the map's baked `consensus` / `consensus_majority` (same mode the caller picks), so category, ceiling, visibility and crosswind are exactly what the forecast map shows. Gust and alternate-required aren't in the consensus block: gust is max (worst) / median (majority) of the models; alternate-required is any / strict majority.

| Axis | PROMISING | MARGINAL | UNLIKELY |
|---|---|---|---|
| VFR ceiling | ≥ 1500 ft | 1000–1500 | < 1000 ft |
| VFR visibility | ≥ 5000 m | 3000–5000 | < 3000 m |
| IFR cloud | no alternate required | alternate required (FAA or EASA) | below the approach class's worst-case DH/vis (`APPROACH_CLASS_PROXY` hi ends; an IAP of unknown class gets the non-precision default, via `map_queries.approach_classes`) |
| Crosswind | < 15 kt | 15–25 | ≥ 25 kt |
| Gust | < 25 kt | 25–35 | ≥ 35 kt |
| Convective risk | none / marginal | low | moderate+ |
| Sun (VFR) | day / civil twilight | | night (< −6°) → labelled `NIGHT`, overriding the weather grade (weather reasons still listed) |

- Wind lines are the **Airport Wind advisory's catalog defaults**, read from `AirportWindEvaluator.catalog_entry()`: one source of truth with the briefing. (The issue first said 20 kt from `approach_feasibility`; that is the approach-alignment limit, not the wind-severity one.)
- IFR at an airport with no instrument approach uses the VFR cloud lines.
- Overrides (`min_ceiling_ft`, `min_visibility_m`, `max_crosswind_kt`, `max_gust_kt`) move the PROMISING line (cloud) or the UNLIKELY line (wind); the other line clamps so the band never inverts. Not stored in the profile.
- `query.minima` keys: `ceiling_promising_ft`, `ceiling_unlikely_below_ft`, `vis_promising_m`, `vis_unlikely_below_m`, `crosswind_marginal_kt`, `crosswind_unlikely_kt`, `gust_marginal_kt`, `gust_unlikely_kt`.
- **Freezing level (IFR only, informative, #841)**: each airport-slot gets `fzl_ft`, the map consensus `freezing_level_ft` (worst = lowest model, majority = median of the winning pool, so it matches the map), or `SFC` when the 2 m temperature is ≤ 0 °C (worst: coldest model, majority: median of all models). `SFC` wins because the snapshot value is the sounding's *first* 0 °C crossing going up: under a sub-zero surface that is the base of a warm layer aloft, or missing when the whole column is sub-zero. It never changes a grade (option (a) of #841, owner's choice); grading icing from a point freezing level was rejected (no cloud tops, no en-route data; the briefing's icing advisories do it properly).
- **Confidence**: `low` if category or ceiling agreement is divergent (or < 2 models), `medium` if mixed or a two-model day (D+5/D+6), else `high`. Disagreement is reported, never averaged away.

## Aggregation

- **Radius**: per slot, base row + per-sector `promising/marginal/unlikely` counts (plus `night: n` when any airport is NIGHT), min ceiling, max gust, worst-confidence, and `worst` only when it isn't PROMISING.
- **IFR freezing level**: each sector / corridor summary carries `min_fzl_ft`, and each slot row a `min_fzl_ft` over every airport in it (focus airports included, since their compact rows don't print it). Rounded to 100 ft, `SFC` lowest. Not in the per-airport compact rows: it would add ~1 KB to a weekend for little gain. Absent for VFR.
- **Route**: per slot, departure + destination rows + a corridor summary. For legs the corridor counts as one point: UNLIKELY only when **at least half** its airports are UNLIKELY or NIGHT, MARGINAL for a minority (an airport can be 20 nm off track and a pilot can avoid one field).
- `best_slots` (top 3, never an UNLIKELY / NIGHT slot): radius ranks the base grade (the worse of base and destination when a destination is given, so an UNLIKELY destination never ranks) then `area_promising_score` (PROMISING 1, MARGINAL 0.5, else 0); route ranks the outbound leg.
- `best_slots` confidence comes from the airports the pilot would use (base; base + destination; route ends + corridor), not the whole area: one divergent far-off field doesn't make a solid slot read "low". The area score only breaks ties.
- `trend` per day: the same score of the first half vs last half of the day's slots, ±0.25 → improving / deteriorating, over **daylight grades only**. VFR makes every airport NIGHT after dark, so dark 06Z/18Z slots in winter would otherwise read as a trend on a flat day.
- **Pairs** (destination known): leg time = great-circle / TAS (default 110 kt). Outbound graded at take-off slot for the departure end and corridor, at the slot nearest arrival for the landing end; return must start ≥ outbound slot + leg time, same day. `round_trip` per day: possible / outbound only / return only / no. Top 5 pairs, one per outbound slot.

## Map links

`map_url` (top level = best slot, plus each best slot and each pair's outbound slot) opens `/maps.html?tab=forecast&fc.day=&fc.hour=&fc.apt=&fc.view=` (`fc.model=majority` when asked): the forecast tab's URL contract (`web/ts/visualization/forecast-url.ts`), which the iOS app also opens natively. `fc.view` frames the radius (widened to include a destination outside it), or the route's bounding box plus the corridor, for a ~700 px map (zoom 4..10); `fc.apt` opens the destination's (else the base's) summary card. Origin is `WEATHER_BASE_URL`, like the agent's briefing links. `fc.day` is **relative**, so a link opened on a later day shows a later date: the map has no absolute-date key yet.

## Output budget

Compact strings, not raw model output: one `GRADE CAT ceiling vis wind xw conf` row per airport per slot in `airports[].rows` (order = `slot_order`); focus airports appear only in `slots`. A worst-case weekend (16 airports, 2 days × 5 hours, destination, with map links) is ~17 KB (~4.2k tokens), asserted < 18 KB in `tests/test_area_outlook.py`; the IFR case with the freezing level is ~18.2 KB, asserted < 19 KB. #851's longer grade words (PROMISING vs GO in every row) and the `guidance` line cost ~2.3 KB over the original ~14.7 KB; kept, since short codes would get relayed verbatim.

## Gotchas

- Snapshots carry a freezing level (`freezing_level_ft`, ft MSL, from the sounding) but **no cloud tops**: IFR output has `scope: terminal_only`, shows the freezing level as information and the note says icing is not graded. The map payload has carried it since forecast-map cache `v4`; the outlook's projection keeps it plus each model's `temperature_c` (`SLOT_PROJECTION_VERSION` `v2`).
- Slots come from the map grid: out-of-grid hours snap to the nearest offered; D+6 is 06/12/18Z only. A slot with no data lands in `missing_slots` rather than failing the call. D+0 slots more than an hour gone are dropped (`past_slots_skipped`); if nothing is left the call is a 400.
- Great-circle maths goes through `great_circle` → euro_aip `NavPoint.haversine_distance`, the same source `tasks/alternates` uses.
- With `rules=ifr`, `query.minima` moves the VFR cloud lines under `no_approach_airports_only`, so an LLM never quotes them as the IFR minima.
- Thresholds are presets for an indicative tool, not briefing meteorology; changing them doesn't touch any advisory (see `meteorology-decisions.md` §52).
