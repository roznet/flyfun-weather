# iOS Forecast Map

> Porting the pan-European forecast overview map (`maps.html`, forecast tab) to
> the iOS/iPad app — as a **pilot-framed** weather map, not a generic one.

**Status:** design, not built. Agreed in a design session 2026-07-14.
Related: [forecast-page.md](../forecast-page.md) (the web feature this ports),
[ios-app-ui.md](../ios-app-ui.md), [ios-app-architecture.md](../ios-app-architecture.md),
[ios-web-known-gaps.md](./ios-web-known-gaps.md).

---

## Why this map exists (and why it isn't Windy)

Windy renders raw fields better than we ever will, and it always will. The reason
this map is still worth building is that **every metric on it is a pilot's
question already answered**, where Windy makes you answer it yourself from
several layers:

| This map | What Windy makes you do |
|---|---|
| **Flight category** in the future (VFR/MVFR/IFR/LIFR) | Read a cloud layer and a visibility layer and combine them in your head |
| **Crosswind on the best runway** — `crosswind_kt` + `best_runway_id` | Read wind speed + direction, then recall the runway headings |
| **Aviation ceiling** — lowest BKN/OVC from the model sounding | Squint at cloud-cover %, which is not a ceiling |
| **Cross-model agreement ring** | Open three models in three tabs |

That framing is the product. It should be visible in the UI, not just implicit in
the data — see [Metric picker](#metric-picker-is-a-list-of-questions).

**Primary use case:** *"Where can I fly this weekend?"* — discovery, no flight
planned, starting from the pilot's usual departure area.
**Secondary:** *"I have a flight — what's the wider geographical situation?"* —
context around a route already being planned.

---

## Scope

### v1 — port, with parity

Functional parity with the web forecast tab: map + markers, day/hour grid, 10
metrics, model/consensus modes, agreement ring, tap → airport summary card,
row-tap → switch map metric. iPad and iPhone both, from the start.

### Deferred (deliberate, with reasons)

- **Cross-section + Skew-T drill-down.** The web panel has Card / Cross-section /
  Skew-T. v1 ships **Card only**. The card is a pure function of data the map
  already holds (zero network); the other two need the SSE (see
  [The drill-down is expensive](#the-drill-down-is-expensive-when-we-get-there)).
  For the primary "where can I fly" use case the card answers the question — the
  vertical profile is a flight-planning tool, and flight planning already has one.
- **Flyability bars / day-comparison strip.** Taken on as a **separate feature**,
  not a slice of this one. Sketch retained at the bottom
  ([Deferred: the flyability strip](#deferred-the-flyability-strip)) because it is
  the thing that would actually *answer* the primary use case rather than support
  it — but it needs its own design pass (which region does it summarise? what
  makes a day "flyable"? which hour?) and shouldn't hold up the port.
- **D-0 METAR/TAF on the card.** The D-0 payload already carries an `observation`
  block that neither client renders (see [Free data already on the wire](#free-data-already-on-the-wire)).
  Worth doing — but on **both** platforms, as a parity-preserving follow-up.

---

## Design principle: the clients render, the server decides

The two clients must not hand-copy meteorology or thresholds. Anything that is a
*judgement* moves server-side; the clients do layout and colour-lookup only. This
is the same principle `api/help.py` already implements for (i)-popup content (one
JSON file, imported by the web bundle **and** served to iOS, ETag-cacheable).

Already server-side and reusable as-is: flight-category derivation, best-runway
crosswind/headwind, alternate-required (FAA/EASA), per-field agreement, and the
day/hour/model grid (`/maps/forecast/days` — the pickers are **drawn from the
response**, never hardcoded; the grid is deliberately ragged, see
`tasks/forecast_grid.py`).

Three things move server-side as part of this work:

### B1 — Bake `consensus_majority` into the forecast payload

Today the server bakes only the **worst** consensus; the web client recomputes
worst *and* majority itself (`weather-map-consensus.ts::computeConsensus`).
Porting that to Swift would mean re-implementing: ordinal max for categories,
per-field worst-is-min-or-max rules, **median within the winning-category pool**
for majority numerics, and **circular mean** for wind direction — while keeping it
behaviourally identical to `analysis/airport_consensus.py`. That is exactly the
class of silently-drifting hand-copy the `sync-ios-web` skill exists to police.

Instead: bake a second `consensus_majority` block next to `consensus` in the same
`forecast_map:{day}:{hour}` cache entry. Cost is one extra dict per airport in a
payload that is already several hundred KB. Both clients then carry **zero**
consensus logic. The web's `computeConsensus` is retired in the same PR (otherwise
we've added a third implementation rather than removing one).

### B2 — Map-metrics catalog (thresholds + colours) as served data

`weather-map-format.ts` hardcodes every colour ramp and threshold (wind bands,
crosswind bands, ceiling bands, CAPE bands, the category hexes, the agreement-ring
hexes). Duplicating that table in Swift means a threshold change needs a web
change **plus** a Swift change **plus** an App Store release, and any miss shows
the same weather in two different colours on two devices.

Follow the `help.py` pattern exactly: extract the table to
`web/ts/data/map-metrics-catalog.json`, have `weather-map-format.ts` import it
directly (so the web bundle has one copy, not a second), and serve it versioned +
ETag-cacheable for iOS to fetch and cache. A threshold change becomes a one-line
JSON edit that both clients pick up.

The catalog also carries each metric's **label, sub-label and legend bands**,
which is what powers the picker below.

### B3 — Frequent airports endpoint

Discovery needs an origin, and there is **no `home_base` concept anywhere in the
codebase** (no preference, no column, no field — checked). Rather than add a
setting and an onboarding step, derive it: the user's most-used airports are
already implicit in their flight history.

New endpoint returning, for the current user, the **top 5 departure** and **top 5
destination** airports (ICAO + count). Used here to centre the map on cold open;
immediately reusable by the **web app** (map centring, Add-Flight prefill).

### W1 — Web change: dates, not `D-N`

The day picker currently reads `D-0 … D-6`. "D+4" is a data-grid concept; a pilot
planning a weekend thinks *"Saturday"*. Relabel to weekday + date
(`Today · Thu 16 · Fri 17 · Sat 18 …`) on **web and iOS** so the two stay
consistent.

Note: the URL-state key `fc.day` stays a **relative integer** — only the label
changes — so existing share links keep resolving.

---

## API contract (all existing, all `Depends(current_user_id)`)

| Endpoint | Use |
|---|---|
| `GET /api/maps/forecast/days` | Build the day + hour pickers. Never hardcode the grid. |
| `GET /api/maps/forecast?day=&hour=` | The map payload: every watchlist airport (~619), per-model + baked consensus. **The only call that hits the network on a day/hour change.** |
| `GET /api/maps/forecast/outlook` | *(B-series, flyability feature — not v1)* |
| `GET /api/help` | Metric (i)-popup content; iOS already consumes this. |

Payload shape (`tasks/map_queries.py`):

```
{ forecast_time, model_init_times: {gfs|icon|ecmwf: ISO},
  airports: [{
    icao, lat, lon, approach_type,
    models: { <model>: { ceiling_ft, visibility_m, wind_speed_kt, wind_dir_deg,
                         wind_gust_kt, crosswind_kt, headwind_kt, best_runway_id,
                         cloud_cover_pct, cape_jkg, convective_risk,
                         temperature_c, flight_category, alt_required? } },
    consensus: { flight_category, agreement: {<field>: consistent|mixed|divergent}, ... },
    consensus_majority: { ... },              // B1, new
    observation?: { metar_raw, ... }          // D-0 only; not rendered in v1
  }] }
```

`models` is **sparse** — ICON is absent on D+5/D+6 (its cloud-diag GRIB stops at
120 h). Never assume three models.

**DTO note:** `Models/API/AirportWeatherResponse.swift` already decodes this exact
airport shape (a trimmed subset, for the Siri intent). The new
`ForecastMapResponse` is a superset — widen, don't reinvent.

---

## iOS structure

### Navigation (iPad done from the start)

The app root is a `NavigationSplitView` with **no `NavigationStack` anywhere**;
every secondary screen is a `.sheet`. Doing iPad later would mean retrofitting the
sidebar, so it's done up front.

- `FlightListView`'s `selectedFlight` becomes a `SidebarSelection` enum:
  `.forecastMap | .flight(id)`.
- **Toolbar** gains a Map button between `+` and `⋯` (`FlightListView.swift:115`),
  as agreed. It sets `.forecastMap`.
- **Regular width (iPad):** detail pane = `ForecastMapView`. Airport card = a
  trailing **`.inspector()`** column (~360 pt) — *not* a sheet.
- **Compact (iPhone):** `ForecastMapView` in a `.fullScreenCover` (the app's
  first). Airport card = bottom sheet,
  `.presentationDetents([.fraction(0.45), .large])` +
  **`.presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.45)))`** —
  that last modifier is what keeps the map pannable and live-recolouring under the
  sheet, and the whole interaction depends on it.
- Both containers share one `@Observable ForecastMapViewModel` (day, hour, metric,
  model mode, selection, payload LRU). The map view itself is container-agnostic.

### Controls: "when" on top, "what" at the bottom

The bottom belongs to the airport sheet, and a sheet covers whatever is under it.
So the time controls go **on top**, or they vanish exactly when you want to step
the hour while looking at an airport.

- **Nav title / subtitle:** `Sat 18 Jul 2026` / `09Z · Worst of 3 models`.
- **Top overlay capsules** (`ultraThinMaterial`, matching `RouteMapView`'s existing
  chrome): `[Sat 18 ▾]` and `‹ [09Z ▾] ›`. With only ~5 sample hours per day, the
  `‹ ›` steppers beat opening a picker and give a scrub feel for free.
- **The hour stepper is duplicated in the airport card's header.** This is the
  interaction that makes the screen sing: card at half-height, tap `›`, and the
  map above *and* the card below update together.
- **Bottom-right, above the sheet:** locate-me, metric menu, legend capsule.

### Metric picker is a list of questions

Not a list of variable names. Labels + sub-labels come from the B2 catalog:

| Label | Sub-label | Field |
|---|---|---|
| Can I get in? | Flight category | `flight_category` *(default)* |
| Can I land? | Crosswind, best runway | `crosswind_kt` (+ `best_runway_id`) |
| Will I see the ground? | Ceiling · Visibility | `ceiling_ft` / `visibility_m` |
| Will it be rough? | Convective risk · CAPE | `convective_risk` / `cape_jkg` |
| Will it take forever? | Headwind | `headwind_kt` |
| Do I need an alternate? | FAA / EASA | `alt_required` |

Same ten metrics as web, zero extra data — but the screen now *says* what makes it
different from Windy. The crosswind readout must always name the runway
("18 kt on RWY 06", not "18 kt").

### Markers

**`MKMapView` via `UIViewRepresentable`, no clustering.** 619 annotations is
comfortable for MapKit with reusable views; 619 SwiftUI `Annotation`s is a real
jank risk, and culling doesn't help at Europe zoom where everything is visible.

Clustering is rejected on *semantics*, not performance: for "where can I fly" a
cluster should show the **best** airport in it, for "what's dangerous" the
**worst**. Rather than pick wrong, show every airport, always.

- Fill = active metric colour (from the B2 catalog). Radius scales with zoom
  (5 px at z≤4 → 11 px at z>7), as web.
- **Agreement ring** = border colour, `weight: 2`, in consensus modes only.
  Crucially it is **per-active-metric** — `weather-map.ts:304` calls
  `getAgreementForMetric(consensus, metric)`, so the ring means *"the models
  disagree about the thing you are currently looking at"*. That is well-defined;
  a single global "do the models agree" colour would not be, which is why there
  is no agreement *metric*. Category colour stays the primary read; agreement is
  secondary.
- Selected airport: amber ring, `weight: 4`, brought to front.
- Missing data: `#888`.

**Metric and model switches are a pure recolour** — no refetch, and ideally no
annotation rebuild. Only day/hour hits the network.

### Airport card

Content mirrors `airport-summary-card.ts`: verdict header (consensus category +
ICAO + valid time + agreement chip), alternate-required row, then the **metric ×
model matrix** (rows = metrics, columns = present models + a consensus column,
every cell background-coloured with the same scale as the markers, divergent rows
flagged), init-times footnote.

**Tapping a metric row switches the map's colour metric** — already the shipped
web behaviour (`airport-summary-card.ts:268`), not a new idea. Mark the active row
with a "shown on map" check so the interaction teaches itself.

On selection, **nudge the map camera** so the tapped airport doesn't end up under
the sheet (bias centre latitude by ~25 % of the span), as Apple Maps does.

### Deep links

The web has full deep-link state (`fc.day`, `fc.hour`, `fc.model`, `fc.metric`,
`fc.apt`, `fc.apView`) and a share button. Registering those as iOS universal
links means **a map link shared from the desktop opens the phone in exactly that
state**. Cheap — the state schema already exists.

---

## Gotchas

- **Never hardcode the day/hour/model grid.** It is ragged by design: D+0…D+6;
  sample hours `06/09/12/15/18Z` on D+0–D+5 but only `06/12/18Z` on D+6 (ECMWF
  goes 6-hourly past 144 h); ICON absent on D+5/D+6. Draw the pickers from
  `/maps/forecast/days`, and port the web's *"greyed but still tappable, explains
  itself"* affordance — a model that can't reach the selected day must never read
  as agreement.
- **Payload is several hundred KB per (day, hour).** Confirm gzip is on before
  designing around it. Keep an in-memory LRU keyed `(day, hour)` and prefetch the
  adjacent hours of the current day (they're server-cached, so it's cheap) — that's
  what makes `‹ ›` stepping feel instant.
- **`models` is sparse.** Two-model days are normal, not an error state.
- **Colour scales must not be re-typed in Swift.** That's what B2 is for.

## The drill-down is expensive (when we get there)

For whenever the cross-section/Skew-T slice is picked up:
`GET /api/maps/airport-profile` is **not** a cache read — it runs an on-demand GRIB
decode, and `api/airport_profile.py` limits it to **3 concurrent streams per user,
20 globally**, then returns 429. The web panel fires it on a 400 ms hover-dwell;
iOS must **not** — no hover, and it would spend cellular, battery and server CPU on
every marker brushed past. Explicit tap only, cancel the in-flight stream on any
airport/model change, keep the web's LRU keyed `icao|startHour|model`.

The rendering itself is *cheaper than it looks*: iOS already has a SwiftUI Canvas
`CrossSectionRenderer` with its layer stack, and RZSkewT is already a dependency.
The real work is that the airport profile is a **time-axis** cross-section (4
forecast hours over one airport) while the iOS renderer is **distance-axis** (points
along a route) — so it's "generalise the ordinate + write an SSE adapter", not
"port thirty canvas layers".

## Free data already on the wire

The **D-0** `/maps/forecast` payload already includes a per-airport `observation`
block (raw METAR, actual category, actual wind, TAF) — added at cache-build time
(`cache_builder.py:250`) and rendered by **neither** client; it isn't even declared
in the web's TypeScript interface.

Surfacing it on the card gives *"models say MVFR, the field is reporting VFR"* for
free, with no new endpoint. Do it on **both** platforms to preserve parity.

## Deferred: the flyability strip

Taken on as a separate feature. Sketch, so the idea isn't lost:

Under each day pill, a thin stacked bar showing the mix of flight categories across
**the airports currently visible on the map**, so the day strip answers *"which
day"* at a glance without flipping between seven maps:

```
   Today      Thu 16     Fri 17     Sat 18     Sun 19     Mon 20
  ▇▇▇▇▇▇░░   ▇▇▇▇░░░░   ▇▇░░░░░░   ▇▇░░▒▒▒▒   ▇▇▇▇▇▇▇░   ▇▇▇▇▇░░░
```

It cannot be computed from the map payload (that's one `(day, hour)` slot), so it
needs a compact `GET /api/maps/forecast/outlook` → `{icao → category}` for every
slot in the grid (~15 KB per slot, ~500 KB for all 33, cacheable next to the
existing `forecast_map:*` entries). The same data would give the card a **7-day ×
5-hour heat grid for one airport** — *"when should I go to Cannes"* in one glance.

**Open questions that make it its own design pass:** which region does it
summarise (visible bbox? range from home base?); what makes a day "flyable"
(share of VFR? worst-in-window? weighted by distance?); which hour does each bar
represent (the selected one? the best one? a daylight roll-up?).

---

## Slices — two issues

Strictly ordered: **backend → frontend**. The iOS client reads its colours *and*
its majority consensus from what the backend issue builds, so it cannot start
first.

The frontend issue is one unit of **tracking** but lands as two PRs — the map,
then the card. Issue ≠ PR: one thing to follow, two reviewable diffs.

### Issue 1 (#419) — Move the judgement server-side *(server + web)*

B1 + B2 + B3 + W1. Grouped because they share one acceptance criterion: **the web
forecast map behaves and looks exactly as it does today.** No iOS client involved,
nothing to demo, and each piece alone is too small for its own issue — but together
they are what makes the iOS client logic-free.

- B1 — bake `consensus_majority`; **retire `computeConsensus` from the web** (else
  we've added a third implementation instead of removing one).
- B2 — extract colour bands/hexes/labels/legends to
  `web/ts/data/map-metrics-catalog.json`; web imports it, server serves it
  ETag-cacheable (`help.py` pattern).
- B3 — frequent departures/destinations endpoint (web can adopt for Add-Flight
  prefill immediately).
- W1 — weekday+date day labels; `fc.day` stays a relative integer so share links
  still resolve.

### Issue 2 (#420) — iOS: the forecast map *(two PRs)*

**PR 1 — the map.** Sidebar-selection enum + toolbar Map button (iPad detail pane /
iPhone `fullScreenCover`); `MKMapView` representable with 619 markers coloured from
the **served** catalog + per-active-metric agreement ring + legend; day/hour pickers
drawn from `/forecast/days` (incl. the greyed-but-explains-itself affordance);
payload LRU + adjacent-hour prefetch; cold-open centring from B3.

Demoable on device on its own — open, scrub day/hour, switch metric/model, watch
Europe recolour. Genuinely useful before the card exists.

**PR 2 — the card.** Tap → card (bottom sheet on iPhone, `.inspector()` on iPad);
verdict header, alternate row, metric × model matrix; **row-tap → switch map
metric**; hour stepper in the card header; camera nudge on selection. Plus
universal-link handling (below).

**Universal links — in scope, and small.** Universal links are already live and
validated for this app: the entitlement (`applinks:weather.flyfun.aero`) ships in
`flyfun_weather.entitlements`, and the site serves a working
`/.well-known/apple-app-site-association` (curl it to see the current contract).
Its `paths` today cover only `/auth/callback` and `/briefing.html`, so enabling
map links is a **one-line change**: add `/maps.html` to that path list. The app
then reuses its existing `/briefing.html` deep-link routing, and the web's `fc.*`
URL-state schema already defines the payload — so a map link shared from desktop
opens the phone on the same day/hour/metric/airport.

Caveats: the AASA is served by the Caddy config, which lives in the **private
deploy repo** and is rolled out manually (`caddy validate` →
`systemctl reload caddy`) — see the deployment notes. Apple's CDN caches AASA, so
propagation after the reload is not instant.

## Follow-ups (not v1 issues)

- **D-0 METAR/TAF on the card** — see [Free data already on the wire](#free-data-already-on-the-wire).
  Do it on **both** clients to preserve parity.
- **Cross-section + Skew-T drill-down** — see
  [The drill-down is expensive](#the-drill-down-is-expensive-when-we-get-there).
- **The flyability feature** — see [Deferred: the flyability strip](#deferred-the-flyability-strip).
