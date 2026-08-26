# iOS App — App Intents, Siri & Apple Intelligence

> Expose FlyFun briefings to Siri, Shortcuts, and Spotlight via App Intents.
> The MCP tool catalog (`src/weatherbrief/mcp/server.py`) is the reference for
> what capabilities to surface — this doc is its on-device sibling.

**Status: PHASE 1 IMPLEMENTED (#364, resolver #367).** The Tier-1 App Intents
surface ships in `app/flyfun-weather/flyfun-weather/AppIntents/` (17 files):
`FlightEntity` (`AppEntity` + `IndexedEntity`) + `AirportEntity`, the six intents
(open-list / open-briefing / check / overview / refresh / airport-weather), all
six registered in `FlyFunShortcuts`, the full tiered resolver (deterministic +
Foundation Models grounded selection over the candidate flights), the spoken-line
builders in `IntentDialogs`, the typed `PendingNavigation` seam, and Spotlight
reconciliation on flight-list load. Unit coverage:
`flyfun-weatherTests/AppIntentsResolverTests.swift`.
The push prerequisite (Issue 3) has **also landed** (#366 server + #371 prefs) —
see [Briefing Refresh Notifications](./ios-app-briefing-notifications.md). Phase 2
(View Annotations, App Intents Testing framework) remains proposed. It has **no
SiriKit**, so there was nothing to migrate — a clean start on the modern App
Intents path.

> **No App Group, deliberately** — intents live in the main app target and run
> in-process. See Decision 1 for the reasoning and the migration task.

## Related Docs

- [Overview](./ios-app-overview.md) — current implementation status
- [Architecture](./ios-app-architecture.md) — MVVM + Repository, auth, deep links
- [Briefing Refresh Notifications](./ios-app-briefing-notifications.md) — APNs push on refresh-complete; **prerequisite** for the refresh intent to close its loop
- [Roadmap](./ios-app-roadmap.md) — phase context (this is a Phase-1/2 cross-cut, adjacent to the Phase-3a voice-PIREP work)
- Server sibling: `src/weatherbrief/mcp/server.py` — the MCP tools these intents mirror

## Why App Intents (and the one hard constraint)

App Intents is the single framework through which Siri and Apple Intelligence
call into a third-party app. Our capabilities already exist as thin, offline-capable
methods on `BriefingRepository`; the MCP server wraps them for Claude, and App
Intents wraps the *same* methods for Siri. Intents should stay as thin as the MCP
tools — no new networking, reuse `CachingBriefingRepository`.

**The hard constraint:** there is **no weather/aviation App Schema** (WWDC26
assistant schemas cover only messages, mail, photos, contacts, calendar, media).
So every intent here is a *custom* App Intent. Consequences:

- Custom intents are first-class in **Shortcuts, Spotlight, Widgets, Action Button,
  Control Center**, and are Siri-reachable — but the trigger phrase must contain
  the app name (e.g. "…my **FlyFun** briefing…"). A bare "refresh my weather
  briefing" only routes to us if the user renames the shortcut in the Shortcuts app.
- Siri does **not** parse "tomorrow to Fairoaks" into a flight for us. Natural-language
  parameter resolution is *our* code, via an `EntityStringQuery` (see Resolver Design).
  WWDC26's semantic index (`IndexedEntity`) improves match quality but the resolver is ours.

Everything degrades gracefully across iOS 26 → 27: Phase 1 works today; Phase 2
layers on WWDC26-only APIs where present.

---

## Phase 1 — App Intents + App Shortcuts (ships on current iOS 26)

Target utterances (all require the "FlyFun" token in the phrase):

- "Hey Siri, **show me my FlyFun briefings**" → opens the flight list
- "Hey Siri, **open the FlyFun briefing for my next flight**" → opens that briefing
- "Hey Siri, **refresh my FlyFun briefing for the flight tomorrow to Fairoaks**"
- "Hey Siri, **what's my FlyFun assessment for Cannes?**" (spoken, no app open)

### Entities

| Entity | Backed by | Notes |
|---|---|---|
| `FlightEntity` | `FlightResponse` (`repository.flights()`) | `AppEntity` + **`IndexedEntity`** for Spotlight + Siri resolution. Display: `shortTitle` ("ORIGIN → DEST"), subtitle = departure date + assessment. `id` = flight id. |
| `AirportEntity` | `AirportDatabase` (existing service) + `RZFlight` `KnownAirports` | ICAO + name; used by the airport-weather intent and to expand "Fairoaks" → `EGTF`. `AirportEntityQuery.suggestedEntities()` offers only the user's own flights' endpoints, not the ~1000-airport DB, so the Shortcuts picker stays usable. |

### Intents

| Intent | Foreground? | Maps to MCP tool | Behaviour |
|---|---|---|---|
| `OpenFlightListIntent` | opens app | `list_flights` | Navigate to the flight list page. No parameter. |
| `OpenBriefingIntent` | opens app | `get_briefing` | `@Parameter var flight: FlightEntity?`. If nil → compute the **next** upcoming flight and open its briefing. |
| `CheckBriefingIntent` | background (spoken) | `get_briefing` | `@Parameter var flight: FlightEntity`. Speaks assessment (GREEN/AMBER/RED) + the top one or two advisories. Reads from cache when offline. |
| `FlightsOverviewIntent` | background (spoken) | `list_flights` | No parameter. Speaks a one-line-per-flight traffic-light summary of upcoming flights. |
| `RefreshBriefingIntent` | background | `refresh_briefing` | `@Parameter var flight: FlightEntity`. `RefreshDriver` returns on the **first** SSE signal, speaking one of `alreadyFresh` / `started` / `completed` / `alreadyInProgress` / `rateLimited` / `failed`; a detached drain keeps the server run alive past our early return. See loop-closure note. |
| `AirportWeatherIntent` | background (spoken) | `get_airport_weather` | `@Parameter var airport: AirportEntity`, `@Parameter var day: AirportWeatherDay` (`@AppEnum` today/tomorrow/in-2/in-3 → the server's 0…3; hour pinned to 12). Spoken consensus category + wind, plus the latest observation and a "snapped to nearest monitored airport" note. |

Notes:
- All six speak through `IntentDialogs` (pure, `FlightResponse`-driven, unit-tested), so the
  voice phrasing — including the empty-state and `UNAVAILABLE`-is-not-a-verdict lines
  (#392) — lives in one file. Background intents return `ProvidesDialog` only; no
  `ShowsSnippetView` yet.
- **`RefreshBriefingIntent` closes its loop via** [Briefing Refresh Notifications](./ios-app-briefing-notifications.md):
  a refresh is ~2 min server-side and a background intent can't wait, so `RefreshDriver`
  reports the early gate outcome and returns. It refreshes with `source: .siri`, which the
  server treats as non-present → push/email on completion. The spoken line is still the
  interim "open FlyFun shortly"; upgrading it to promise the notification is a copy change.

### App Shortcuts (zero-setup Siri phrases)

`FlyFunShortcuts` (an `AppShortcutsProvider`) registers **all six** intents, two phrases
each, every one carrying `\(.applicationName)` — e.g. `"Refresh my \(.applicationName)
briefing for \(\.$flight)"`. English v1 (Decision 7); ~10-shortcut soft limit, so adding a
seventh intent means budgeting phrases, not just appending.

### Display ordering vs. "my next flight" (#536)

`FlightResolver` has two ordering entry points and they answer different
questions:

- `orderedForSuggestions(_:order:now:)` — the **display** list backing
  `FlightEntityQuery.suggestedEntities()`. Its caller passes the account's
  `flight_order` preference so the Shortcuts picker matches what the flight list
  shows; only the upcoming half flips, past stays most-recent-first.
- `nextFlight(in:now:)` — deliberately **not** preference-driven. "My next
  flight" means the soonest departure however the list happens to be drawn;
  following a display preference here would return the furthest-away flight.
- `IntentDialogs.overviewSummary` — calls `orderedForSuggestions` but pins
  `.soonestFirst`, for that reason plus a second one: it truncates to five, so a
  furthest-first preference would make it read out the five most *distant*
  flights and silently omit the one departing tomorrow.

The rule: **entity pickers follow the display preference; spoken
"next/upcoming" answers stay chronological.**

All three share one duration-aware `FlightResolver.isUpcoming(_:now:)`, so they
agree with `FlightResponse.resolvedSection`, `FlightListView.groupedFlights` and
the server's `_flight_has_ended` about when a flight stops being upcoming.
Gating them on bare `departureDate >= now` would make Siri report no upcoming
flights while the list showed an in-progress one at the top of Future — and
`nextFlight` skip the flight you are actually on. A flight whose `departureTime`
won't parse counts as upcoming, matching the list.

`order` is a parameter, not a read of global state: the ordering helpers stay `nonisolated`
and pure (callable from the non-MainActor test target) while `UserPreferencesStore` is
`@MainActor`. Callers pass `UserPreferencesStore.cachedFlightOrder()`, a `nonisolated`
accessor over the same `cachedUserPreferences` blob. Shortcuts caches suggested entities,
so a preference flip may not reorder its picker until iOS refreshes them; in-app is immediate.

`overviewSummary` also re-`filter`s by `isUpcoming` after ordering — not redundant:
`orderedForSuggestions` returns `upcoming + past` (the picker deliberately includes
history), and without the filter the overview would announce past flights as upcoming.

### Resolver Design — "the flight tomorrow to Fairoaks"

`FlightEntity` is resolved by an `EntityStringQuery` over a **tiny, closed set** — the
user's handful of upcoming flights. That makes a *deterministic* resolver the primary
path and an on-device model a *fallback*, not the first resort. Two distinct "models"
matter here:

- **Siri's own resolution** (system-side, Gemini-backed at WWDC26): flattens/cleans the
  phrase and, with `IndexedEntity`'s semantic index, does more matching before it even
  calls our query. We benefit passively; it improves each OS release.
- **Foundation Models framework** (a model *we* call): the on-device ~3B Apple
  Intelligence LLM, available since iOS 26. With **guided generation** it selects, from a
  provided candidate list, *which* upcoming flight the phrase means — ideal for messy or
  world-knowledge phrasing ("my beach trip").

**Tiered resolution:**

| Tier | Handles | Cost / availability |
|---|---|---|
| 1. Deterministic — `AirportDatabase`/`RZFlight` place↔ICAO + relative-date keywords | "Fairoaks", "EGTF", "Le Touquet", "tomorrow", weekday names | free, instant, offline, **every device** |
| 2. Foundation Models **grounded selection** over the today-and-future flights (route + airport names + date) → a validated flight index | vague/world-knowledge phrasing: "my beach trip", "the France run", "my Geneva flight" | on-device, needs Apple-Intelligence-capable device + enabled |
| 3. Siri disambiguation UI | still ≥2 matches after tiers 1–2 | system-provided |

Tier 1 is the **floor** and must always exist: Foundation Models requires an
Apple-Intelligence-capable device (iPhone 15 Pro / M-series+), the feature enabled, and
the model present — so it can never be a hard dependency. Gate tier 2 on
`SystemLanguageModel.default.availability`; if unavailable, fall straight to tier 3.

**Tier 2 = grounded selection, not blind extraction.** Rather than extracting `{place,
when}` from the phrase in isolation (which can't bridge "the coast" → *Nice*), the resolver
hands the model the actual candidate flights — **today-and-future only** — each as a line
of ICAO + airport name + date, and asks which number matches. The model returns a **1-based
index** (0 = none); the resolver range-checks it and maps back to a real flight id.

A candidate line is `FlightResolver.candidateLine`: `"EGKB (London Biggin Hill, London, GB)
→ EGTF (Fairoaks, GB), 9 Jul 2026"` — airport name + city + country are what let the model
bridge world knowledge. The `@Generable FlightChoice { choice: Int }` return is a small
bounded integer on purpose: easier and safer for a 3B model to emit than an opaque id.

Tier 1 itself is two passes: flights matching **both** a place and a date first (narrowing
"the flight tomorrow to Fairoaks"), then either signal alone. Place matching needs a
≥4-char non-stopword word shared between the query and the airport name.

**Division of authority:** the model only *selects from a closed set we provide*; the
returned index is range-checked and the id re-validated against the real flights — so the
model can never surface a flight that doesn't exist (a *safer* guarantee than token
re-matching, and a task on-device models are more reliable at). Selection sits behind a
`FlightPhraseResolving` protocol so the tier is unit-testable with an injected fake.

**Two different "upcoming" tests, deliberately.** Free-phrase resolution scopes to
`FlightResolver.upcoming(_:)` — *calendar-day* today-or-later, so a flight earlier today
is still offerable — while the display/next-flight helpers use the duration-aware
`isUpcoming(_:now:)` (below). Don't collapse them: the resolver wants a slightly wider net
than "hasn't ended". Explicit id lookups (`entities(for:)`) and `suggestedEntities()` see
full history; only free-phrase resolution is scoped.

Reuse:
- **`Services/AirportDatabase.swift`** + `RZFlight` `KnownAirports` for place↔ICAO — do
  not hand-roll name matching. `IntentSupport.ensureAirportDatabase()` **awaits** the load
  first: a freshly-spawned Siri process would otherwise match against an empty DB.
- **`CachingBriefingRepository`** for `flights()` / `latestPack()` / `refreshBriefing`
  so intents work from cache in the cockpit.

*Naming note: the resolver's tiers 1–3 are internal to this algorithm and unrelated to
the product **Tier 1 / Tier 2** issue split below.*

### Navigation from an intent (built, #364)

Foregrounding intents set a pending navigation target the UI consumes. Implemented as a
typed `PendingNavigation` enum — App Intents use `.flightList` / `.briefing(flightId:)`;
the other consumers have since added `.forecastMap(MapDeepLink)` (#420) and
`.share(code:)` (#446) to the same enum — with **two backings**:

- `PendingNavigationStore` (UserDefaults.standard) is the **cold-launch-safe** hand-off — a
  foregrounding intent may run *before* `AppState` exists, so it writes there and returns
  with `openAppWhenRun`. `OpenBriefingIntent` / `OpenFlightListIntent` call `…Store.set(...)`.
  The store is a struct over an **injectable `UserDefaults`** (`init(defaults: = .standard)`),
  with the static `set`/`take` the app calls forwarding to the standard suite. Two reasons:
  it is the one place that moves to a shared App Group when an out-of-process consumer lands
  (Decision 1), and its single key plus destructive `take()` made two swift-testing suites
  driving `.standard` in parallel steal each other's target — tests now use their own suite
  (`PendingNavigationStore.testStore()`, #578).
- `AppState.consumePendingNavigation()` runs on every scene `.active` (covers cold launch +
  warm foreground), `take()`s from the store into the observable `pendingNavigation`
  property, and `FlightListView` routes then calls `clearPendingNavigation()`.

This is now the **unified navigation seam**: App Intents, push-notification taps
(`PushNotifications.swift`), and Universal Links (`handleUniversalLink`) all write to the same
`PendingNavigationStore` rather than parsing `flyfunweather://` URLs.

### Auth & process model

- Intents run **in-process**, reusing the Keychain JWT via `APIClient` /
  FlyFunCommon `RollingBearerSession` — no OAuth in the intent path (see Decision 4
  for the expired-token fallback).
- `IntentSupport.makeRepository()` rebuilds the same cache-first stack as
  `AppState.setupClient` (same Keychain `service:`, same `cacheScope(forToken:)`), calling
  those static helpers rather than re-deriving. If you change cache scoping or the
  Keychain service in `AppState`, the intents follow only because they reuse those — a
  divergence here silently gives Siri a *different user's* cache directory.
- No App Group (Decision 1); the `PendingNavigation` hand-off uses `UserDefaults.standard`.
- **Resolution must never throw.** `FlightEntityQuery` swallows load failures into an empty
  match, because resolution runs *before* `perform()` and a thrown error there pre-empts the
  intent's own signed-out/error dialog. A signed-out user with a cached list still resolves,
  so `perform()` runs and speaks the sign-in line.

### Spotlight

`FlightEntity: IndexedEntity` (donated via `CSSearchableIndex`) makes flights
searchable in Spotlight and improves Siri's resolution of "my flight to Cannes".

`SpotlightDonator.reindex(_:)` **reconciles the whole set** on each flight-list load
(delete-all, then `indexAppEntities`) rather than donating/removing per flight — the
default index is app-scoped, so delete-all purges server-deleted flights for free
(Decision 8). Overlapping calls chain through a `pending` `Task` so a second delete-all
can't interleave with the first insert and transiently empty the index. UI-test fixtures
are never donated (`AppState.isUITesting` guard).

---

## Phase 2 — On-screen context (iOS 27 / WWDC26)

Goal: act on **what's on screen** conversationally. Phase 2 is scoped strictly to what
genuinely needs the next iOS release — the **View Annotations API** and the **App Intents
Testing framework**. (Foundation Models is *not* here: it ships in iOS 26 and lives in
Phase 1 — the resolver fallback, and the optional on-device narration below.)

### View Annotations ("explain this", "show me the cross-section")

The View Annotations API tags on-screen SwiftUI views with App Entities so Siri
knows the referent. Surface, smallest-first:

| Utterance (while viewing a briefing) | Annotate | Intent | Maps to |
|---|---|---|---|
| "Explain this" (on an advisory row) | `AdvisoryEntity` on each advisory row | `ExplainAdvisoryIntent(advisory:)` | `get_advisory_detail` |
| "Show me the cross-section" | `FlightEntity` on the briefing container | `ShowCrossSectionIntent` | in-app nav (Cross-Section tab) |
| "Is this still green / what changed?" | `FlightEntity` on the container | `CheckBriefingIntent` (reused) | `get_briefing` |
| "Explain the icing at waypoint 3" / "Skew-T here" | per-point annotations | (deferred — large surface, low marginal value) | — |

`ExplainAdvisoryIntent` resolves the on-screen `AdvisoryEntity` → `repository.advisoryDetail(...)`
→ speaks/shows the "why" (the same per-model, cross-check reasoning the MCP
`get_advisory_detail` returns).

### On-device narration (Foundation Models — iOS 26, so Tier-1-capable)

Not an OS constraint, a packaging choice: the on-device model can narrate our **cached**
`AdvisoryDetailResponse` into a natural sentence, fully offline in the cockpit, today. It
rides with Phase 2 only because the on-screen "explain this" is its natural trigger; a
parameterized `ExplainAdvisoryIntent(advisory:)` could ship now. Split of authority: the
authoritative digest stays server-side (LangGraph) and the on-device narration only fills
the offline gap — it never replaces it.

Optional: use the model to improve voice-PIREP parsing beyond the regex `PIREPParser`
(cross-ref Phase 3a in the roadmap).

### Testing

Adopt the **App Intents Testing framework** (WWDC26) to validate Siri / Shortcuts /
Spotlight through real system pathways without UI automation, alongside the existing
XCTest suite.

---

## MCP ⇆ App Intents parity

The MCP tools and the App Intents are two front doors over one repository. Keep them
in sync the way `sync-ios-web` guards web↔iOS drift: when an MCP tool is added or its
shape changes, check the mirroring intent. Current mapping:

| MCP tool | Intent(s) |
|---|---|
| `list_flights` | `OpenFlightListIntent`, `FlightsOverviewIntent` |
| `get_briefing` | `OpenBriefingIntent`, `CheckBriefingIntent` |
| `refresh_briefing` | `RefreshBriefingIntent` |
| `get_airport_weather` | `AirportWeatherIntent` |
| `get_advisory_detail` | `ExplainAdvisoryIntent` (Phase 2) |
| `create_flight` | *(kept in-app / Shortcuts-only — route+time too complex for voice)* |
| `get_alternates`, `get_digest_context` | *(not surfaced initially — deep/niche)* |

## Implementation issues

Tiering is by **OS availability**: Tier 1 = everything implementable on **today's iOS 26**
(including Foundation Models); Tier 2 = only what needs the **next release (iOS 27)**.

1. **Tier 1 — App Intents, iOS 26. ✅ SHIPPED (#364, resolver #367).** Entities, the six
   intents, `FlyFunShortcuts`, the full tiered resolver, the `PendingNavigation` seam,
   Spotlight reconciliation, the expired-token fallback (Decision 4). App Group
   provisioning (Decision 1) was **deferred** — see the status block up top.
2. **Tier 2 — iOS 27 / WWDC26 only. Not started.** The **View Annotations API**
   (on-screen "explain this" / "show the cross-section") and the **App Intents Testing
   framework**. By packaging choice, `ExplainAdvisoryIntent` + on-device advisory
   narration ride along here (their natural UX is the on-screen trigger) — though both
   are iOS-26-capable and could move to Tier 1 (see Phase 2 note).
3. **Notification — briefing-refresh push. ✅ SHIPPED (#366, #371).** See
   [ios-app-briefing-notifications.md](./ios-app-briefing-notifications.md). The refresh
   intent's loop now closes through it (`source: .siri` → non-present → push/email); only
   the spoken interim wording still predates it.

## Decisions

Locked (★) decisions first, then defaults still open to revision.

1. **★ DECIDED (revised in #364) — App Group deferred to the first out-of-process
   consumer, NOT provisioned in Phase 1.** The original plan was to provision it up
   front. Implementation revised that: Phase-1 intents are defined in the **main app
   target**, so they run in-process and share the Keychain JWT + `BriefingCacheStore`
   directly — no shared container is needed for any Tier-1 path. Provisioning the
   entitlement now would also require an Apple Developer-portal registration that
   otherwise fails code-signing on every build, for zero Tier-1 benefit. *Task (when
   Widgets / Live Activities / Control Center land): define the App Group id, move the
   `KeychainBearerTokenStore` access-group + the `BriefingCacheStore` /
   Application-Support path into the shared container, with a one-time migration.*
2. **★ DECIDED — Refresh via Siri: freshness gate only, no confirmation.** A voice
   "refresh" triggers a real, **billed** pipeline run (see
   [cost-attribution](./cost-attribution-design.md)), but the server's `already_fresh`
   gate prevents redundant spend and it is rate-limited (409/429). No extra Siri
   confirmation step (friction in a hands-busy flow). *Built (#364) — see the
   `RefreshBriefingIntent` row above for the outcome set.*
3. **★ DECIDED — Full tiered resolver ships in Tier 1 (today).** The Foundation Models
   framework is available on **iOS 26**, so the on-device LLM tier is implementable now and
   belongs in the Tier-1 issue — Tier 2 is reserved for what genuinely needs the next iOS
   release (View Annotations, App Intents Testing). Resolver = deterministic (mandatory
   floor) → Foundation Models **grounded selection** (the model picks from the provided
   today-and-future candidates; the returned index is validated back to a real flight) →
   Siri disambiguation, with the LLM step gated on `SystemLanguageModel.default.availability`
   so non-AI devices degrade to deterministic + disambiguation. *(As built in #367 the LLM
   tier does grounded selection over real candidates — see the Resolver Design section —
   rather than the originally-sketched blind `{place, when}` extraction, which couldn't
   bridge world-knowledge references and gave a weaker "never invent a flight" guarantee.)*
4. **★ DECIDED — Signed-out / expired-token behaviour.** Attempt a silent token refresh
   (`RollingBearerSession`) first; if it fails, foreground intents throw
   `needsToContinueInForegroundError` ("Open FlyFun to sign in") and background intents
   speak "Please open FlyFun to sign in first."
5. **Empty-state / no-match dialog per intent.** *Built:* every spoken line lives in
   `IntentDialogs` + the intents' `catch` arms — no-upcoming-flights, not-yet-briefed,
   out-of-forecast-range (uses `coverage.availableDay`), `UNAVAILABLE` phrased as "I
   couldn't assess…" not a verdict (#392), and the signed-out line. Change copy there,
   not inline in an intent.
6. **★ Navigation seam — typed `PendingNavigation`, built.** Not `flyfunweather://` URL
   parsing. Cold launch and warm foreground share one path (store + `.active` consume);
   push taps and Universal Links were migrated onto the same seam.
7. **AppShortcut phrase set.** ~10-shortcut soft limit, every phrase needs "FlyFun".
   **English phrases v1 shipped**; FR/DE localization is still a fast-follow (place
   resolution is already language-agnostic via ICAO).
8. **★ Spotlight donation lifecycle — full reconcile, built.** Not per-flight
   donate/remove: `SpotlightDonator` delete-alls and re-indexes the current set on each
   flight-list load, which is what keeps server-deleted flights from lingering.
9. **★ DECIDED — Privacy / prediction surfacing: discoverable.** Flight routes are not
   especially sensitive and surfacing requires the user's own unlocked device, so intents
   and `FlightEntity` are discoverable in Shortcuts/Spotlight and Siri may predict them.

## Open Questions

Resolved and kept only as pointers: the **day parameter** is now the `AirportWeatherDay`
`@AppEnum`; **"next flight"** is `FlightResolver.nextFlight` (soonest by departure,
duration-aware, briefing-agnostic — see the #536 section); **refresh feedback** is the
push (#366), leaving only the interim spoken wording.

Still open:

- **Phrase discoverability** — how much to lean on App Shortcuts phrases (need "FlyFun")
  vs. teaching users to build their own Shortcuts (free phrasing). Onboarding tip?
- **Refresh spoken line** — now that the push exists, "I'll let you know when it's ready"
  is honest; the intent still says "open FlyFun shortly". Worth gating on whether push is
  actually enabled for that user/device before promising it.
- **iPhone vs iPad** — intents are universal; confirm navigation targets exist on both size classes.
- **Siri output shape** — everything speaks via `ProvidesDialog` today; whether
  `CheckBriefingIntent` should also return a `ShowsSnippetView` result card is undecided.
- **Resolver tier-1 recall** — place matching needs a ≥4-char non-stopword token overlap,
  so short/awkward names ("Nice", "Ronaldsway") fall through to the model tier. Fine, but
  it means devices without Apple Intelligence lose those phrases entirely.

## References

- Implementation: `app/flyfun-weather/flyfun-weather/AppIntents/` (entities, intents,
  `FlightResolver`, `IntentDialogs`, `IntentSupport`, `RefreshDriver`, `SpotlightDonator`,
  `PendingNavigation`)
- Tests: `app/flyfun-weather/flyfun-weatherTests/AppIntentsResolverTests.swift`
- MCP server (parity source): `src/weatherbrief/mcp/server.py`
- Repository surface: `app/flyfun-weather/flyfun-weather/Services/BriefingRepository.swift`
- Airport name↔ICAO: `app/flyfun-weather/flyfun-weather/Services/AirportDatabase.swift`
- Notifications prerequisite: [ios-app-briefing-notifications.md](./ios-app-briefing-notifications.md)
