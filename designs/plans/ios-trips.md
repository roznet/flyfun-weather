# iOS trips — porting the conjunctive chain to a native shape

> Status: **plan**, not built. Scope is "replicate the web app's trip
> functionality with native iOS UX". The feature itself is defined in
> [`designs/flight-trips.md`](../flight-trips.md) (#602) — read that first; this
> doc only says how iOS should surface it.

## What is being ported, and the two rules that constrain every screen

A trip is a **conjunctive feasibility chain with a shrinking scope**: it happens
only if all its *remaining* legs work. The product value is that the return leg
is the binding constraint and per-flight briefings structurally hide it.

Two corollaries from the design doc are load-bearing and every iOS decision
below is downstream of them:

1. **The trip never gets a verdict.** No colour, no badge, no "trip is AMBER".
   The screen ranks and directs attention: *which leg decides this, and when
   does it become decidable*. Any iOS affordance that reduces a trip to one
   traffic light is wrong even if it looks better.
2. **The binding leg is computed exactly once, on the server**
   (`weatherbrief.trips._pick_binding_leg`). iOS reads `summary.binding_leg_id`,
   `summary.headline`, `summary.chain_status`. It must never re-derive them,
   never `max()` over leg assessments, and never fold a long-range `outlook`
   into a traffic light — those two ladders are mutually exclusive by design.

A third, practical one: **refresh is server-driven and serial.** There are two
executor slots for the whole process and `MAX_PER_USER = 2`, so a client-side
fan-out over legs fails outright. iOS presses one button and polls.

## Where iOS is today

Phase 1 (shipped, #602) is exactly one thing: `FlightResponse.trip`
(`TripLegRef`) decoded, and a `TripBadge` on `FlightCardView` reading
"2/3" with the accessibility label "Leg 2 of 3 in trip <name>". No trip screen,
no grouping, no trip refresh, no trip notification routing.

### Gaps found while reviewing, in priority order

These are not "phase 2 nice-to-haves" — the first three are places where the
*server already ships trip behaviour that iOS silently drops on the floor*.

| # | Gap | Where | Impact |
|---|---|---|---|
| 1 | **Trip push taps go nowhere useful.** `send_trip_push` puts `trip_id` (deliberately, not `flight_id`) in the payload; `PushSupport.pendingNavigation(from:)` only reads `flight_id` and returns `nil`. | `Services/PushNotifications.swift` vs `notify/push.py:403` | The coalesced trip notification is live today. Tapping it opens the flight list with no indication of which trip fired. |
| 2 | **`/trip.html` is not in the AASA.** Whitelist is `["/auth/callback","/briefing.html","/maps.html","/s/*"]`. | `deploy/weather.flyfun.aero.caddy:17` | A trip link from the trip email or a shared URL opens Safari, not the app. AASA is cached per-install, so **this must deploy before the build that handles it ships**. |
| 3 | **`TripLegRef` is missing `auto_refresh`.** The server added it so a member leg's briefing page can show the trip's switch (disabled, not hidden). | `Models/API/FlightResponse.swift:385` | iOS can't reproduce the "whether is the trip's, when is the leg's" split. Decode-safe (Swift ignores unknown keys), so it is a divergence, not a crash. |
| 4 | **A per-flight refresh on a claimed leg 409s with specific copy iOS doesn't surface.** `leg_is_claimed` guards both the queued endpoint (409 "…part of a trip refresh that is already running") and the SSE path (an `error` event with the same text). | `api/packs.py:2283`, `:2549` | The briefing screen's ↻ shows a generic failure. The pilot has no idea a trip run owns the leg. |
| 5 | **Move/Duplicate trip inheritance is invisible.** The web renders an explicit checkbox; iOS relies on the server defaults (`keep_in_trip` defaults true on move, `trip_id` absent on duplicate). | `Views/Flights/AddFlightView.swift:102`, `api/flights.py:1540` | Behaviour is *correct*, but silent. A pilot duplicating "same route, different weekend" can't tell the copy left the trip. |

Two structural facts that make this cheaper than it looks:

- The Xcode project uses **file-system synchronized groups**, so new Swift files
  under `flyfun-weather/` need no `project.pbxproj` edit.
- `GET /api/flights` already embeds `trip` on every member flight
  (`bulk_trip_refs`), and `GET /api/trips` returns each trip's full `summary`
  computed from one DB query over the latest pack per leg. The client needs **no
  new server work at all** for the core screens.

## Proposed native shape

### 1. The flights list — a trip is one row that pushes, not a collapsible card

The web renders a trip as a collapsible card placed in the section of its
earliest un-flown leg, collapsed by default, carrying the binding-leg chip.

**Recommendation: keep the placement rule, drop the inline expansion.** The trip
becomes a single `NavigationLink` row in the section of its earliest un-flown
leg, showing chain label, date range, "2 of 3 legs ahead", and the binding chip.

Why not a `DisclosureGroup` mirroring the web:

- `FlightListView`'s `List(selection: $selection)` *is* the iPad detail driver.
  Nesting expandable rows that themselves contain `NavigationLink(value:)` rows
  inside a `NavigationSplitView` sidebar re-opens exactly the class of problem
  `FlightSelectionView`'s doc comment already documents for `editMode`.
- Everything that justifies expansion — the callout, the AI paragraph, refresh,
  per-leg `days_out` — lives on the trip screen anyway. An inline expansion would
  be a second, weaker rendering of the same thing.

**The honest cost:** getting from the list to Sunday's briefing becomes two taps
instead of one. Mitigations, in order of preference: (a) the row's context menu
lists the legs as direct destinations; (b) the collapsed row already carries the
binding chip, which is the decision-relevant bit; (c) on iPad, `SidebarSelection`
gains a `.trip(String)` case so the trip screen fills the detail pane — the same
shape `.forecastMap` already uses — and a leg tap swaps the detail to the
briefing, which is one tap from there.

Past legs keep rendering individually with the existing `TripBadge`, matching
web: the Past section is server-paginated, so pulling a past leg into the trip
row would make it vanish or duplicate depending on the page loaded.

Offline: see the open question below.

### 2. The trip screen — a vertical timeline, not the web's horizontal strip

`TripDetailView`, reached from the list row, a push notification, or a
`/trip.html?id=` Universal Link.

```
┌──────────────────────────────────────────┐
│ ‹ Flights          Alps weekend      ↻   │  navigationTitle = trip name
│                    EGTF → LSGS → EGTF    │  .navigationSubtitle = chain_label
├──────────────────────────────────────────┤
│ ┏━━ DECIDES THIS TRIP ━━━━━━━━━━━━━━━━┓  │  accented hero, the thing the
│ ┃ Sunday's LSGS → EGTF decides this   ┃  │  eye lands on. Text is
│ ┃ trip. It is AMBER at D-5. Not       ┃  │  `summary.headline`, verbatim,
│ ┃ decidable on high-resolution        ┃  │  server-computed. Never
│ ┃ guidance until Thu 18 Sep.          ┃  │  reconstructed client-side.
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                          │
│  ○ Fri 12 Sep 08:00Z  EGTF → LSGS        │  timeline: one row per leg,
│  │  GREEN · D-7 · 2.1 h                  │  flown legs dimmed, the binding
│  ┆  2 nights on the ground               │  leg carrying an accent rail and
│  ●  Sun 14 Sep 14:00Z  LSGS → EGTF       │  a "decides this trip" label
│  │  AMBER · D-5 · 2.3 h                  │  (NOT a trip-level colour)
│  │  ⚠ Icing  ⚠ Cloud base                │
│                                          │
│  Trip summary (AI)                       │  secondary: .secondary tint,
│  Your Friday outbound looks settled…     │  smaller, below the fold
│                                          │
│  ⚠ LSGS arrival ≠ LFAT departure         │  soft continuity warnings
└──────────────────────────────────────────┘
```

Design notes, each tied to a rule above:

- **Vertical timeline, not the web's horizontal strip.** The strip is horizontal
  because a desktop has width; a phone does not, and `MAX_TRIP_LEGS = 12` means
  a horizontal strip becomes a scroll-to-discover on the one screen whose entire
  job is "see the whole chain at once". A vertical timeline also gives the ground
  gaps a natural home (the connector between rows), which is where
  `gap_hours_before` / `same_sortie_as_previous` (`SORTIE_GAP_HOURS = 4 h`)
  belong: a sub-4 h gap renders as a thin "fuel stop" connector, a longer one as
  a labelled "2 nights on the ground".
- **The binding leg is marked by rail + label, never by making the trip header
  amber.** This is rule 1 in pixels.
- **`days_out` sits beside every badge.** A D-7 amber and a D-1 amber are not the
  same claim, and on a phone there is no hover to recover it.
- **Beyond-horizon legs get the soft outlook badge**, visually distinct from a
  traffic light (outline vs filled), reusing the web's `outlookClass` semantics.
  Four non-gradeable states, not three: beyond-horizon, pending-coverage,
  needs-briefing, and briefed-but-ungradeable each get their own neutral badge.
  Collapsing "unavailable" into "needs a briefing" tells the pilot something
  false.
- **AI paragraph is visually secondary** and shows the `ai_disabled` reason
  out loud when a member leg has AI off — silently omitting it looks like a bug.
- **Refresh is a single toolbar `↻`**, disabled with inline progress
  ("Leg 2 of 3…") while `refresh.active`. Deliberately **no per-leg refresh
  button**, matching web: the gate already skips legs with no new model run.
- **Leg actions**: tap → briefing. Swipe → Remove from trip (an unlink, and the
  confirmation must say the flight and its briefings survive). Overflow menu:
  Rename, Auto-refresh toggle, Delete trip (confirmation spells out that the
  legs survive).

### 3. Creating and editing membership

Web reuses its multi-select selection bar. iOS's nearest equivalent is
`FlightSelectionView` — today a delete-only sheet reached from
"Select & Delete Flights…".

**Recommendation: two entry points, because the single-leg case and the
multi-leg case are genuinely different gestures on a phone.**

- **Single leg** — a context-menu / swipe action "Add to Trip…" on a flight row,
  opening a small sheet listing the pilot's existing trips plus "New trip". This
  is the common case (book the outbound, add the return when it comes into
  range) and should not require entering a selection mode.
- **Multi-leg** — generalise `FlightSelectionView` from "Select & Delete" to
  "Select Flights", with an action bar whose trip actions come from a **Swift
  port of `web/ts/helpers/trip-selection.ts`** (`buildTripSelection`): zero
  distinct trips touched → *Group as Trip*; exactly one → *Add to "…"*; more than
  one → neither, because merging trips is a decision the bar cannot ask about.
  *Remove from Trip* appears whenever any pick is in one, labelled as an unlink.
  Delete stays where it is.

The sheet's existing `isEditable` filter is already the right gate for trips too
(a subscriber can't group someone else's flight). The menu item label changes.

For **Move / Duplicate** (gap 5): don't add a checkbox; iOS already forces the
choice through two distinct buttons. Add a caption to each — "Stays in *Alps
weekend*" under Move, "Won't be added to *Alps weekend*" under Duplicate —
rendered only when the source is in a trip. Same information, no new control.

### 4. Refresh and progress

- `POST /trips/{id}/refresh` once, then poll `GET /trips/{id}/refresh/status`
  every **5 s** while `active` (the web's cadence; a leg takes minutes, and the
  chain survives the app being backgrounded because it is server-driven).
- Reuse the existing `activeRefreshes()` poll in `FlightListViewModel` for
  per-leg "Updating…" state on the timeline: a leg can be refreshed from its own
  briefing, Siri, MCP or the scheduler without any trip run existing, and the web
  learned this the hard way (`trip-refresh-indicator.ts`).
- Surface `message` verbatim ("2 of 3 legs had new data"). Without that line, a
  trip refresh that legitimately did almost nothing reads as one that failed.
- **Never fan out.** One `POST`, no per-leg `triggerRefresh`. `triggered_by="trip"`
  is not in `UNCAPPED_TRIGGERS` and must not be worked around client-side.
- Handle gap 4: on 409 from `triggerRefresh`, and on the SSE `error` event,
  show the server's sentence rather than a generic failure, with a "Open trip"
  action.

### 5. Notifications and deep links

- `PendingNavigation` gains `.trip(id: String)`.
- `PushSupport.pendingNavigation(from:)` reads `trip_id` before `flight_id`
  (a trip push carries only `trip_id`). Pure function, already unit-tested in
  `PushNotificationsTests` — extend there.
- `AppState.navigationTarget(for:)` maps `/trip.html?id=<id>` → `.trip`. Pure
  and `nonisolated`; extend `UniversalLinkRoutingTests`.
- Ship the AASA path change **first** (gap 2), then the build.
- Per-flight notify override precedence is already server-side
  (flight → trip → account); iOS needs no logic, but the briefing screen's
  notification control should say when the trip's override is what applies.

## Layering plan

| Layer | Work |
|---|---|
| `Models/API/TripResponse.swift` (new) | `TripResponse`, `TripSummary`, `TripLeg`, `ContinuityWarning`, `TripRefreshStatus`, `TripAiSummaryResponse`, `LegState` / `GradeKind` / `BindingBasis` as `String`-backed enums with an unknown fallback. Add `autoRefresh` to the existing `TripLegRef` (gap 3). |
| `Services/TripRepository.swift` (new protocol) | `trips()`, `trip(id:)`, `createTrip(flightIds:name:)`, `addLegs(tripId:flightIds:)`, `removeLeg(tripId:flightId:)`, `updateTrip(id:patch:)`, `deleteTrip(id:)`, `refreshTrip(id:)`, `tripRefreshStatus(id:)`, `tripAiSummary(id:)`. |
| `Services/*Repository.swift` | `OnlineBriefingRepository` conforms; `CachingBriefingRepository` forwards (online-only, modulo the cache decision below); a `FixtureTripRepository` backs the XCUI journey. |
| `ViewModels/TripDetailViewModel.swift` (new) | Load, poll refresh status, poll active refreshes, mutate membership, request the AI paragraph on open-when-stale. |
| `ViewModels/FlightListViewModel.swift` | Load `trips()` alongside `flights()`; expose a grouped model. |
| `Views/Trips/` (new) | `TripDetailView`, `TripTimelineView`, `TripLegRow`, `TripBindingCallout`, `TripRowView` (list row), `AddToTripSheet`. |
| `Views/Flights/` | `SidebarSelection.trip`, trip row in `groupedFlights`, generalised `FlightSelectionView`, Move/Duplicate captions. |
| `Utilities/TripSelection.swift` (new) | Swift port of `buildTripSelection`, pure and unit-tested. |

**On `BriefingRepository`:** the architecture doc already warns that widening it
means touching every conformer, and it is ~40 methods. Trips get their **own
protocol** rather than nine more methods on that one. `AppState` exposes
`tripRepository` separately; views that need trips take it explicitly.

## Milestones

**M1 — close the silent drops (small, ship independently of any UI).**
Gaps 1–4: `trip_id` push routing, AASA + `/trip.html` link routing,
`TripLegRef.autoRefresh`, claimed-leg 409 copy. M1 makes the trip features that
are *already live server-side* stop failing quietly on iOS. Note the AASA deploy
ordering.

**M2 — read-only trip screen.** DTOs, `TripRepository`, `TripDetailViewModel`,
`TripDetailView` (timeline + callout + AI paragraph + continuity), list row +
`SidebarSelection.trip`. No mutation. This is the bulk of the product value:
"which leg decides this trip" becomes reachable on a phone.

**M3 — trip actions.** Refresh + progress polling, auto-refresh toggle, rename,
delete, remove leg.

**M4 — membership.** `AddToTripSheet`, generalised `FlightSelectionView` with
the ported selection rule, Move/Duplicate captions.

**M5 — polish.** Offline caching of the `TripResponse` with the calendar-staleness
rule (see below), an XCUI journey, App Intents (`TripEntity`, "what decides my
next trip?") if it earns its place.

M1 and M2 are independently shippable and in that order. M3 before M4
deliberately: a pilot who can read the chain but not refresh it is better served
than one who can build trips but can't see what they decide.

## Decisions to make, and my recommendation

**Offline: cache the `TripResponse`, but the headline has a second clock on
it.** iOS caches `flights.json`, so an offline list can show trip *badges* but
not trip *rows*. Cache the last `GET /api/trips` payload the same way — it
carries `summary` and `ai_summary` inline, so one cached document covers the
list row, the timeline and the paragraph.

Re-downloading it whenever a leg refreshes is the right trigger and iOS already
has the signal (the coalesced trip push, and the `externalSync` nudge the list
already listens to). **But refresh is not the only thing that invalidates a
cached summary, and the other cause fires no push at all:**

| What changes | Fires a push? | What goes stale |
|---|---|---|
| A leg refreshes (new `fetch_timestamp`) | yes | assessments, chips, freshness, the AI paragraph |
| A **departure passes** (`remaining` → `flown`, from the clock alone) | **no** | the binding leg can change identity with zero new data — only remaining legs can bind |
| The **UTC date rolls over** | **no** | every `D-N` in the headline, `decision_ripeness_days`, `decidable_from` |

So a download-on-refresh cache fixes *data* staleness and not *calendar*
staleness. A headline cached on Monday saying "Sunday's LSGS → EGTF decides this
trip. It is AMBER at D-5" is simply false on Wednesday, with nothing having
happened.

Recommendation — split the cached document by how it decays:

- **Per-leg rows are cacheable without caveat.** They are pack facts (assessment,
  chips, `fetch_timestamp`) and stay true until the leg refreshes. `D-N` is
  re-derivable from `departure_time` and the current clock, which is arithmetic,
  not a binding-leg derivation.
- **The headline and `decidable_from` get an explicit `as of <time>` on the
  callout itself**, not in a corner.
- **Suppress the headline entirely** when the cache is calendar-stale: it was
  written on an earlier UTC day, or any cached leg's `departure_time` has since
  passed. Fall back to the timeline plus "Connect to see which leg decides this
  trip."

That last check is a clock comparison on cached data — it says *don't trust
this*, never *here is the new answer* — so it stays on the right side of rule 2.
Re-deriving the binding leg client-side would not.

**AI paragraph on open — correcting an earlier framing of mine.** It is *not*
regenerated per open. `ensure_trip_ai_summary` is keyed on the per-leg
`(fetch_timestamp, debrief_decision, derived state)` tuples and returns the
stored text on a key hit with **no model call and no charge**
(`digest/trip_summary.py:396`). It regenerates when a leg refreshes, when a
debrief decision changes, or when a departure passes and flips a leg's state —
not on a view appearing, and notably **not** as `days_out` ticks down, since
`days_out` is not in the key.

So no debounce is needed. Better still, iOS should not `POST` at all on the
normal path: `GET /trips/{id}` already returns `ai_summary` plus
`ai_summary_stale` (`api/trips.py:286`), with the consent gate re-checked on
read. Render the stored paragraph from the GET, and `POST /ai-summary` only when
`ai_summary_stale` is true. That is one fewer write-shaped call than the web page
makes, and it means an offline cached `TripResponse` carries the paragraph with
no extra plumbing.

**`/api/trips` fetch cadence — measure before deciding.** `list_trips` builds a
full `TripResponse` per trip: a packs query, a debriefs query, and
`legs_allow_ai`, which calls an **uncached** `load_profile_settings` per member
leg (`api/profiles.py:421`). The web pays this once per page load.
`FlightListViewModel.loadFlights()` fires on cold start, every
`scenePhase == .active`, every return from a briefing, every `externalSync` push
and pull-to-refresh — a much higher cadence against the same endpoint.

Not measured, so not yet a problem. But do measure it with a realistic trip count
before wiring `trips()` into `loadFlights()` unconditionally. If it bites, the
cheap client-side fix is a longer TTL on trips than on flights: the flights
payload already carries `trip` refs (id, name, position, total), which is enough
to render a trip row's *identity* — only the binding chip needs the summary. The
server-side fix (a lighter list shape that skips `legs_allow_ai`, which the list
row never needs) is out of scope for an iOS issue.

**`chain_status` on the list row.** Tempting, and wrong — see rule 1. The row
carries the binding-leg chip (leg label + that *leg's* badge + `days_out`),
which is what `bindingChip` does on web. Don't let it collapse into a trip dot.

## Open questions

- Does the trip screen need the pack-history (D-N) picker the briefing screen
  has? Probably not — the chain is about *now* — but a pilot comparing "what did
  this look like yesterday" for a trip is a real question the web doesn't answer
  either.
- Should a trip appear in Spotlight / `FlightEntity`-style App Intents? "What
  decides my Alps weekend?" is a good Siri phrase, and the deterministic headline
  is a ready-made spoken answer. Deferred to M5 rather than designed here.
- Trip sharing (`share_code`) is reserved server-side but unbuilt; iOS should
  not design around it yet.

## Testing

Pure-logic (`flyfun-weatherTests`, CI-gated):

- Trip placement in `groupedFlights` — the trip lands in the section of its
  earliest un-flown leg, and a past member still renders individually.
- The `buildTripSelection` port: 0 / 1 / >1 distinct trips → the right actions.
- `PushSupport.pendingNavigation` for a `trip_id` payload, and for a payload
  carrying neither.
- `AppState.navigationTarget(for:)` on `/trip.html?id=…`, including a missing id.
- Badge selection per `grade_kind` — in particular that `unavailable` and
  `needs_briefing` produce different labels, and that an `outlook` leg never
  produces a traffic light.
- Cache calendar-staleness: a cached `TripResponse` written on an earlier UTC
  day, or before a leg's `departure_time` passed, suppresses the headline while
  still rendering the timeline.

XCUI (`flyfun-weatherUITests`, **not** CI-gated — run
`-only-testing:flyfun-weatherUITests` locally before merging): list → trip row →
trip screen → leg → briefing, against `FixtureTripRepository`.

After implementation, run the `sync-ios-web` skill: the badge vocabulary and the
selection rule are exactly the kind of hand-copied surface it exists to police.

## References

- [`designs/flight-trips.md`](../flight-trips.md) — the feature, and every
  decision the above depends on
- [`designs/ios-app-architecture.md`](../ios-app-architecture.md) — repository
  pattern, `PendingNavigation`, AASA
- [`designs/ios-app-ui.md`](../ios-app-ui.md) — cockpit constraints
- [`designs/future/ios-web-known-gaps.md`](../future/ios-web-known-gaps.md) —
  where "full iOS trip UI is v2" should move once this lands
