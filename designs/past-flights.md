# Past flights finder

Server side of #1030 (#1031): one small durable record per flight and one
archive endpoint that every client filters, counts and maps through. The
clients (#1032) render what it returns and compute nothing.

## Why a separate record

T2 retention (`tasks/retention.py`, 180 days, 90 for inactive users) deletes
an undebriefed, unpinned flight's whole pack folder and its `BriefingPackRow`,
taking `advisory_summary_json` and `route_advisories.json` with it. The flight
row stays. `flight_facts` (`FlightFactsRow`) is a 1:1 sidecar retention never
touches, so "had red icing" survives the packs.

## What it holds

| Field | Source |
|---|---|
| departure / destination ICAO, lat/lon, `route_line_json`, `distance_nm` | newest readable pack's `briefing.json` route; else `resolve_waypoints` with `AIRPORTS_DB`; else the codes only |
| `forecast_tags_json`, `forecast_pack_ts`, `tags_recorded` | the pack `default_forecast_pack` (`tasks/live_recap.py`) picks, as the debrief report picks it (effective departure, planned when cancelled) |
| `forecast_frozen_at` | set at window close, by retention before T2, by the backfill, or on any refresh once the flight ended > 24 h ago; never before the departure |
| `reported_tags_json` | cancel reasons (cancelled only) + answers: icing light or worse → ICE, turbulence moderate or worse → TURB, cloud in cloud / in and out → IMC, showers build-ups or CB → TS |
| `decision`, `debriefed`, `interesting`, `differs_from_forecast` | `flight_debriefs` and `flight_debrief_reports` |

Forecast tags: worst aggregate status per tag over the chosen pack's
advisories, amber or worse only. `ARCHIVE_TAG_MAP` = `ADVISORY_TAG_MAP`
(debrief taxonomy) plus `freezing_precip` → FRZ and `enroute_precip` → VIS,
which the debrief form has no outcome row for; kept separate so the debrief
form and its TS / iOS copies do not change. `model`, `fronts`, sun etc. map to
nothing. `tags_recorded = False` means no pack could be read (gone before the
backfill, or no advisories): clients show "hazards not recorded", never "none".

## Write paths (all idempotent, `storage/flight_facts.py`)

- **Pack finalize** (`api/packs._persist_pack_finalize`): full refresh in a
  savepoint. Before departure the default pack is the newest, so tags follow
  every refresh.
- **Debrief PUT / DELETE / answers PUT** (`api/debriefs.py`):
  `update_flight_outcome`, no pack read.
- **Window close** (`tasks/debrief_close.close_flight`): full refresh with
  `freeze=True` after the report row is committed.
- **Retention**: `_ensure_facts_before_t2` writes and freezes every flight
  with a T2-due pack whose record is not frozen; a flight whose write fails
  keeps its packs this run. Dry runs write nothing.
- **Backfill**: `python -m weatherbrief.tasks.flight_facts_backfill
  [--dry-run]` in the container, wrapped by
  `scripts/ops/backfill_flight_facts.py` (ssh, or `--local`).

A frozen record never changes its forecast tags again, even if a pack is
re-read; route and outcome still refresh.

## `GET /api/flights/archive` (`api/flight_archive.py`)

Owned flights only, Past section only (`_classify_section`, so the Recent
nudge flights are not repeated). Filters combine with AND:
`q` (`api/flight_search` unchanged), `tags` (OR, in `source=forecast|reported`),
`outcome` (OR of flown, cancelled, monitoring, debriefed, differs), `hide`
(same keys, excluded; one tap = `hide=cancelled,monitoring`), `from` / `to`
(UTC departure date). Unknown tag or outcome is 422.

Views: `list` (rows, `limit` 1-200 default 20, `offset`), `calendar&year=`
(per day count and worst forecast status), `map` (airports with counts,
routes bundled by unordered ICAO pair with count, worst status and the newest
line oriented from the alphabetically first end, `unplaced` = flights without
coordinates).

Facets on every view: per tag, matches of every filter except `tags`; per
outcome, every filter except `outcome`; `not_recorded` within the current
filter. A chip's count is what turning it on alone would give.

`GET /api/flights` is unchanged.

## Clients (#1032)

Same chips, same order, same labels on every client; each keeps its own
copy, so change all three together: web `web/ts/helpers/past-archive.ts`,
iOS `PastChips` / `PastFilter` (`Models/API/PastArchiveResponse.swift`),
Android `PastChips` / `PastFilter` (`model/PastArchiveModels.kt`). The filter
to query mapping is the only client logic; rows, counts and grouping by
month come from the server's order.

- Web: the Past section of `flights.html` reads the archive (list view)
  instead of the `past` slice of `GET /api/flights` (which it now asks with
  `past_limit=0`). Search box + chips drive the query; paging is "Load more".
  Subscribed past flights no longer appear there.
- iOS: the list keeps a teaser of the latest 3 Past flights plus "See all",
  which pushes `PastFlightsView` (`.searchable`, chips, month sections).
  iPad adds saved filters (All past, With icing, With convection, Cancelled,
  Debriefed) under the teaser. A row opens the briefing through the list's
  selection (fetched by id if it is not loaded).
- Android: same teaser + "See all" to `PastFlightsScreen`. The list's
  in-app search no longer filters Past; with a search on, the button reads
  "Search past" and opens the screen with that query. Back from a briefing
  opened there returns to the Past screen (phone).

## Gotchas

- Cost: the archive loads all owned flights and their facts per request
  (no SQL-side filtering). Fine at hundreds of flights per user; move the
  filters into SQL if one account gets into the thousands.
- No growth concern: one row per flight, deleted with it.
- Wording: no "go/no-go" or "verdict" anywhere in this surface.
