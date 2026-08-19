# iOS Forecast Map

> The pan-European forecast overview map (`maps.html`, forecast tab) on
> iOS/iPad — as a **pilot-framed** weather map, not a generic one.

**Status: SHIPPED — this is as-built, not a proposal.** Backend (#419) landed in
PR #422; the iOS client (#420) is live under
`app/flyfun-weather/flyfun-weather/Views/ForecastMap/`. Verified against code
2026-08-15. It sits in `designs/future/` for historical reasons only — it
documents current behaviour and belongs in `designs/` with an INDEX entry.
Related: [forecast-page.md](../forecast-page.md) (the web feature this ports),
[ios-app-ui.md](../ios-app-ui.md), [ios-app-architecture.md](../ios-app-architecture.md),
[ios-web-known-gaps.md](./ios-web-known-gaps.md).

## Why this map exists (and why it isn't Windy)

Windy renders raw fields better than we ever will. The reason this map is worth
having is that **every metric on it is a pilot's question already answered**,
where Windy makes you answer it yourself from several layers:

| This map | What Windy makes you do |
|---|---|
| **Flight category** in the future (VFR/MVFR/IFR/LIFR) | Read a cloud layer and a visibility layer and combine them in your head |
| **Crosswind on the best runway** — `crosswind_kt` + `best_runway_id` | Read wind speed + direction, then recall the runway headings |
| **Aviation ceiling** — lowest BKN/OVC from the model sounding | Squint at cloud-cover %, which is not a ceiling |
| **Cross-model agreement ring** | Open three models in three tabs |

That framing is the product, and it is visible in the UI — see
[Metric picker](#metric-picker-is-a-list-of-questions). Don't flatten the picker
back into a list of variable names.

**Primary use case:** *"Where can I fly this weekend?"* — discovery, no flight
planned, from the pilot's usual departure area. **Secondary:** *"I have a flight
— what's the wider geographical situation?"*

## Design principle: the clients render, the server decides

The two clients must not hand-copy meteorology or thresholds. Anything that is a
*judgement* lives server-side; the clients do layout and colour-lookup only. Same
principle `api/help.py` implements for (i)-popup content.

Server-side and shared: flight-category derivation, best-runway
crosswind/headwind, alternate-required (FAA/EASA), per-field agreement, and the
day/hour/model grid (`/maps/forecast/days` — pickers are **drawn from the
response**, never hardcoded; the grid is deliberately ragged, see
`tasks/forecast_grid.py`).

Three things moved server-side as part of this work (all shipped):

### B1 — `consensus_majority` baked into the payload

The server used to bake only the **worst** consensus, with the web client
recomputing worst *and* majority itself. Porting that to Swift would have meant
re-implementing ordinal max for categories, per-field worst-is-min-or-max rules,
median-within-the-winning-pool for majority numerics and circular mean for wind
direction — the exact class of silently-drifting hand-copy `sync-ios-web` exists
to police. Now a `consensus_majority` block sits next to `consensus` in the same
`forecast_map:{day}:{hour}` cache entry (`tasks/cache_builder.py`,
`tasks/map_queries.py`). Both clients carry **zero** consensus logic, and the
web's `computeConsensus` was retired in the same PR.
`web/ts/visualization/weather-map-consensus.ts` still exists but now holds only
shared ordering/formatting helpers (`CAT_ORDER`, `isConsensusMode`, `median`,
`circularMean`, `ordinalConsensus`) — **not** the old hand-copy; don't "clean it
up" thinking it is dead consensus code.

### B2 — Map-metrics catalog (thresholds + colours) as served data

Colour ramps and thresholds used to be hardcoded in `weather-map-format.ts`.
Duplicating that table in Swift would mean a threshold change needs a web change
**plus** a Swift change **plus** an App Store release, with the same weather
showing in two colours on two devices meanwhile. As built, following the
`help.py` pattern: the table lives in
`web/ts/data/map-metrics-catalog.json` (`version`, `scales`, `metrics`),
`weather-map-format.ts` imports it directly, and `api/help.py`
(`_load_map_metrics_catalog`) serves it as the **`maps` section of `/api/help`** —
no separate endpoint. iOS decodes it via `HelpCatalogResponse` and interprets it
in `Views/ForecastMap/MapMetricsCatalog.swift` (`bandColor`, categorical,
gray-ramp, `m_to_sm`, `alternate_needed`, `agreementKey`), with
`Resources/map-metrics-catalog.json` bundled as an offline/first-run baseline.
A threshold change is a one-line JSON edit both clients pick up.

**Note:** the catalog carries each metric's `label` and colour bands — **not**
sub-labels. The pilot-question framing in the iOS picker is client-side copy
(`ForecastMapView+Support.swift`); if that becomes a nuisance, add sub-labels to
the catalog rather than duplicating the table.

### B3 — Frequent airports endpoint

Discovery needs an origin, and there is **no `home_base` concept anywhere in the
codebase**. Rather than add a setting and an onboarding step, it's derived from
flight history: `GET /api/flights/frequent-airports` (under **flights**, not maps
— `api/flights.py::compute_frequent_airports`) returns the user's top departure
and destination airports. Cold-open map centring; reusable for Add-Flight
prefill.

### W1 — Web change: dates, not `D-N`

The day picker reads weekday + date (`Today · Thu 16 · Fri 17 · Sat 18 …`) on
**both** web (`maps-main.ts`) and iOS (`ForecastMapView+Support.swift`), because a
pilot planning a weekend thinks *"Saturday"*, not *"D+4"*. The URL-state key
`fc.day` stays a **relative integer** — only the label changed — so existing share
links keep resolving.

## API contract (all `Depends(current_user_id)`)

| Endpoint | Use |
|---|---|
| `GET /api/maps/forecast/days` | Build the day + hour pickers. Never hardcode the grid. |
| `GET /api/maps/forecast?day=&hour=` | The map payload: every watchlist airport (~619), per-model + baked consensus. **The only call that hits the network on a day/hour change.** |
| `GET /api/help` | (i)-popup content **and** the `maps` metrics catalog (B2). |
| `GET /api/flights/frequent-airports` | Cold-open centring (B3). |

`GET /api/maps/forecast/outlook` is referenced by the deferred flyability feature
below and **does not exist** — don't code against it. Payload shape
(`tasks/map_queries.py`):

```
{ forecast_time, model_init_times: {gfs|icon|ecmwf: ISO},
  airports: [{
    icao, lat, lon, approach_type,
    models: { <model>: { ceiling_ft, visibility_m, wind_speed_kt, wind_dir_deg,
                         wind_gust_kt, crosswind_kt, headwind_kt, best_runway_id,
                         cloud_cover_pct, cape_jkg, convective_risk,
                         temperature_c, flight_category, alt_required? } },
    consensus: { flight_category, agreement: {<field>: consistent|mixed|divergent}, ... },
    consensus_majority: { ... },
    observation?: { metar_raw, ... }          // D-0 only; decoded, not rendered
  }] }
```

`models` is **sparse** — ICON is absent on D+5/D+6 (its cloud-diag GRIB stops at
120 h). Never assume three models.

**DTO note:** `Models/API/AirportWeatherResponse.swift` decodes a trimmed subset
of this same shape for the Siri intent; `ForecastMapResponse.swift` is the
superset — widen, don't reinvent. It uses a plain decoder with verbatim keys so
the `agreement` map survives, and falls back to `consensus` when
`consensus_majority` is key-absent.

## iOS structure

### Navigation

The app root is a `NavigationSplitView` with **no `NavigationStack` anywhere**;
every secondary screen is a `.sheet`. iPad was done up front, not retrofitted.

- `Views/Flights/FlightListView.swift` — `selectedFlight` is a `SidebarSelection`
  enum (`.forecastMap | .flight(id)`); the toolbar carries a Map button between
  `+` and `⋯` (`accessibilityIdentifier("forecastMapButton")`, with a
  `ForecastMapTip` popover).
- **Regular width (iPad):** detail pane = `ForecastMapView`. Airport card = a
  trailing **`.inspector()`** column (320/360/440 pt) — *not* a sheet.
- **Compact (iPhone):** `ForecastMapView` in a `.fullScreenCover`. Airport card =
  bottom sheet, `.presentationDetents([.fraction(0.45), .large])` +
  **`.presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.45)))`** —
  that last modifier is what keeps the map pannable and live-recolouring under the
  sheet. The whole interaction depends on it; don't drop it.
- Both containers share one `@Observable ForecastMapViewModel` (day, hour, metric,
  model mode, selection, payload LRU). The map view is container-agnostic; the
  presentation split lives in `ForecastMapView+Support.swift`.

### Controls: "when" on top, "what" at the bottom

The bottom belongs to the airport sheet, and a sheet covers whatever is under it.
So time controls go **on top**, or they vanish exactly when you want to step the
hour while looking at an airport.

- **Top overlay capsules** (`ultraThinMaterial`, matching `RouteMapView`'s chrome):
  `[Sat 18 ▾]` and `‹ [09Z ▾] ›`. With ~5 sample hours per day the steppers beat
  opening a picker and give a scrub feel for free.
- **The hour stepper is duplicated in the airport card's header**
  (`ForecastAirportCard.swift`) — the interaction that makes the screen sing: card
  at half-height, tap `›`, map above *and* card below update together.
- **Bottom-right, above the sheet:** locate-me, metric menu, mode menu, legend.

### Metric picker is a list of questions

The **section title** is the pilot's question; the **item** is the metric
(`ForecastMapView+Support.swift::metricSections`). Metric ids must match the
served catalog — items whose metric the catalog doesn't define are filtered out
at render time.

| Section (the question) | Items |
|---|---|
| Can I get in? | Flight category — `flight_category` *(default)* |
| Can I land? | Crosswind (best runway) — `crosswind_kt` |
| Will I see the ground? | Ceiling — `ceiling_ft` · Visibility — `visibility_m` |
| Will it be rough? | Convective risk — `convective_risk` · CAPE — `cape_jkg` |
| Will it take forever? | Headwind — `headwind_kt` |
| Do I need an alternate? | FAA / EASA — `alternate_needed` |
| More | Wind — `wind_speed_kt` · Cloud cover — `cloud_cover_pct` |

Ten metrics, same as web, zero extra data — but the screen now *says* what makes
it different from Windy. Note the catalog metric id is **`alternate_needed`**
while the per-model payload field is **`alt_required`**; they are not the same
string. The crosswind readout must always name the runway ("18 kt on RWY 06").

### Markers — `ForecastMapKitView.swift`

**`MKMapView` via `UIViewRepresentable`, no clustering.** 619 reused
`MKAnnotationView`s are comfortable; 619 SwiftUI `Annotation`s are a real jank
risk, and culling doesn't help at Europe zoom where everything is visible.
Clustering is rejected on *semantics*, not performance: for "where can I fly" a
cluster should show the **best** airport in it, for "what's dangerous" the
**worst**. Rather than pick wrong, show every airport, always.

- Fill = active metric colour (B2 catalog); radius scales with zoom
  (`diameter(for:)`), as web. Selected: amber ring, brought to front. Missing
  data: gray.
- **Agreement ring** = border colour, consensus modes only, and crucially
  **per-active-metric** (`agreementKey`) — the ring means *"the models disagree
  about the thing you are currently looking at"*. That is well-defined; a single
  global "do the models agree" colour would not be, which is why there is no
  agreement *metric*. Category colour stays the primary read.
- **Metric and model switches are a pure recolour** (`recolorVisible`) — no
  refetch, no annotation rebuild. Only day/hour hits the network.

### Airport card — `ForecastAirportCard.swift`

Mirrors `airport-summary-card.ts`: verdict header (consensus category + ICAO +
valid time + agreement chip), alternate-required row, then the **metric × model
matrix** (rows = metrics, columns = present models + a consensus column, cells
background-coloured with the same scale as the markers, divergent rows flagged),
init-times footnote. **Tapping a metric row switches the map's colour metric** —
shipped web behaviour, not a new idea; the active row is marked so the
interaction teaches itself. On selection the map camera is **nudged**
(`FocusRequest.biasForSheet`, compact only) so the tapped airport doesn't end up
under the sheet.

### Deep links

`/maps.html?fc.*` is a registered universal link: the entitlement
(`applinks:weather.flyfun.aero`) is in `flyfun_weather.entitlements`, and the AASA
served from `deploy/weather.flyfun.aero.caddy` lists `/auth/callback`,
`/briefing.html`, `/maps.html`, `/s/*`. `AppState` parses the `fc.*` keys into
`PendingNavigation.forecastMap(MapDeepLink)`, so a map link shared from desktop
opens the phone on the same day/hour/metric/airport. Caveat: that Caddy config
lives in the **private deploy repo**, is rolled out manually (`caddy validate` →
`systemctl reload caddy`), and Apple's CDN caches AASA — propagation is not
instant.

Tests: `flyfun-weatherTests/ForecastMapTests.swift`.

## Gotchas

- **Never hardcode the day/hour/model grid.** It is ragged by design: D+0…D+6;
  sample hours `06/09/12/15/18Z` on D+0–D+5 but only `06/12/18Z` on D+6 (ECMWF
  goes 6-hourly past 144 h); ICON absent on D+5/D+6. Draw the pickers from
  `/maps/forecast/days`, and keep the *"greyed but still tappable, explains
  itself"* affordance — a model that can't reach the selected day must never read
  as agreement.
- **Payload: ~1.5 MB raw, ~190 KB gzipped, per (day, hour)** (measured 2026-07-14
  on the real 619-airport payload: D+0 1,585 KB → 186 KB; D+6 1,058 KB → 127 KB).
  Compression does the heavy lifting — 12 % of raw. **Dev gotcha:** production
  compresses at the edge (Caddy `encode zstd gzip`), but the local HTTPS dev
  server is uvicorn serving TLS directly with **no Caddy and no compression
  middleware** — an iOS client on `localhost.ro-z.me:8443` pulls the full 1.5 MB
  and feels far slower than production. Don't tune fetch/prefetch against dev
  numbers. The `(day, hour)` LRU + adjacent-hour prefetch in
  `ForecastMapViewModel` is what makes `‹ ›` stepping feel instant.
- **`models` is sparse.** Two-model days are normal, not an error state.
- **Colour scales must not be re-typed in Swift.** That's what B2 is for.
- **The map payload has a second iOS consumer.** `RouteForecastOverlayModel` /
  `Views/Map/RouteForecastOverlay.swift` (#428, port of #424/#425) reads the same
  `repository.forecastMap(day:hour:)` for the briefing route-map overlay, and
  builds `fc.*` deep links back into this map. Changing the repository shape or
  the deep-link schema touches both.

## Not built (deliberate)

- **D-0 METAR/TAF on the card.** The D-0 payload carries a per-airport
  `observation` block (raw METAR, actual category, actual wind, TAF) added at
  cache-build time (`cache_builder.py`). iOS **decodes** it
  (`ForecastMapResponse.ForecastObservation`) but neither client **renders** it —
  the web's TypeScript interface doesn't even declare it. Surfacing it gives
  *"models say MVFR, the field is reporting VFR"* for free, no new endpoint. Do it
  on **both** platforms to preserve parity.
- **Cross-section + Skew-T drill-down.** The web panel has Card / Cross-section /
  Skew-T; iOS ships Card only. `GET /api/maps/airport-profile` is **not** a cache
  read — it runs an on-demand GRIB decode, and `api/airport_profile.py` limits it
  to **3 concurrent streams per user, 20 globally**, then 429s. The web panel
  fires it on a 400 ms hover-dwell; iOS must **not** — no hover, and it would
  spend cellular, battery and server CPU on every marker brushed past. Explicit
  tap only, cancel the in-flight stream on any airport/model change, keep the
  web's LRU keyed `icao|startHour|model`. Rendering is cheaper than it looks (iOS
  has the Canvas `CrossSectionRenderer`, RZSkewT is already a dependency); the
  real work is that the airport profile is **time-axis** (4 forecast hours over
  one airport) while the iOS renderer is **distance-axis** (points along a route)
  — "generalise the ordinate + write an SSE adapter", not "port thirty layers".
- **Flyability bars / day-comparison strip.** Its own feature, its own design
  pass. Sketch: under each day pill, a thin stacked bar showing the mix of flight
  categories across the airports currently visible, so the day strip answers
  *"which day"* without flipping between seven maps. It can't come from the map
  payload (one `(day, hour)` slot), so it needs a compact
  `GET /api/maps/forecast/outlook` → `{icao → category}` per slot (~15 KB each,
  ~500 KB for all 33, cacheable next to `forecast_map:*`); the same data gives the
  card a 7-day × 5-hour heat grid for one airport. **Open questions that make it
  its own pass:** which region does it summarise (visible bbox? range from home
  base?); what makes a day "flyable" (share of VFR? worst-in-window?); which hour
  does each bar represent? `designs/future/us-expansion-plan.md` asks the same.
