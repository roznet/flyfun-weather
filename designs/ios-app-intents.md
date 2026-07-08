# iOS App — App Intents, Siri & Apple Intelligence

> Expose FlyFun briefings to Siri, Shortcuts, and Spotlight via App Intents.
> The MCP tool catalog (`src/weatherbrief/mcp/server.py`) is the reference for
> what capabilities to surface — this doc is its on-device sibling.

**Status: PROPOSED — nothing built yet.** The app today has zero App Intents,
zero App Shortcuts, no Widgets, and no Siri surface (grep for `AppIntent` /
`AppShortcut` / `INIntent` returns nothing). It also has **no SiriKit**, so
there is nothing to migrate — we start clean on the modern App Intents path
(SiriKit was formally deprecated at WWDC 2026).

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
| `AirportEntity` | `AirportDatabase` (existing service) + `RZFlight` `KnownAirports` | ICAO + name; used by the airport-weather intent and to expand "Fairoaks" → `EGTF`. |

### Intents

| Intent | Foreground? | Maps to MCP tool | Behaviour |
|---|---|---|---|
| `OpenFlightListIntent` | opens app | `list_flights` | Navigate to the flight list page. No parameter. |
| `OpenBriefingIntent` | opens app | `get_briefing` | `@Parameter var flight: FlightEntity?`. If nil → compute the **next** upcoming flight and open its briefing. |
| `CheckBriefingIntent` | background (spoken) | `get_briefing` | `@Parameter var flight: FlightEntity`. Returns a spoken/snippet summary: assessment (GREEN/AMBER/RED) + the top one or two advisories. Reads from cache when offline. |
| `FlightsOverviewIntent` | background (spoken) | `list_flights` | No parameter. Speaks a one-line-per-flight traffic-light summary of upcoming flights. |
| `RefreshBriefingIntent` | background | `refresh_briefing` | `@Parameter var flight: FlightEntity`. Triggers refresh, returns **immediately** ("Refreshing your Fairoaks briefing — I'll let you know when it's ready"). See loop-closure note. |
| `AirportWeatherIntent` | background (spoken) | `get_airport_weather` | `@Parameter var airport: AirportEntity`, `@Parameter var day: Int = 0`. Spoken consensus category + wind for the airport. |

Notes:
- **`OpenBriefingIntent` / `OpenFlightListIntent` ship first** — they need no parameter
  resolution and no push, so they carry no resolver risk and prove the navigation plumbing.
- **`RefreshBriefingIntent` depends on** [Briefing Refresh Notifications](./ios-app-briefing-notifications.md):
  a refresh is ~2 min server-side and a background intent can't wait. Without the
  "briefing ready" push, Siri can only say "started, open the app shortly." Sequence
  the push work before (or alongside) this intent.

### App Shortcuts (zero-setup Siri phrases)

```swift
struct FlyFunShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: OpenFlightListIntent(),
            phrases: ["Show my \(.applicationName) briefings",
                      "Open \(.applicationName)"],
            shortTitle: "My Briefings", systemImageName: "list.bullet.rectangle")
        AppShortcut(intent: OpenBriefingIntent(),
            phrases: ["Open my next \(.applicationName) briefing",
                      "Show my next flight in \(.applicationName)"],
            shortTitle: "Next Briefing", systemImageName: "airplane")
        AppShortcut(intent: RefreshBriefingIntent(),
            phrases: ["Refresh my \(.applicationName) briefing",
                      "Refresh my \(.applicationName) briefing for \(\.$flight)"],
            shortTitle: "Refresh Briefing", systemImageName: "arrow.clockwise")
        // + CheckBriefingIntent, AirportWeatherIntent
    }
}
```

### Resolver Design — "the flight tomorrow to Fairoaks"

`FlightEntity` is resolved by an `EntityStringQuery`. Siri hands the extracted
string to our code; we match it against upcoming flights and let Siri disambiguate
when >1 matches:

```swift
struct FlightEntityQuery: EntityStringQuery {
    func entities(matching string: String) async throws -> [FlightEntity] {
        let flights = try await repository.flights()          // cache-first
        return flights.filter { flight in
            // destination match: expand "fairoaks" ↔ EGTF via AirportDatabase
            matchesDestination(flight, string) ||
            // relative-date match: "tomorrow", "today", weekday — parsed in-app
            matchesRelativeDate(flight, string)
        }.map(FlightEntity.init)
    }
    func suggestedEntities() async throws -> [FlightEntity] { /* upcoming flights */ }
    func entities(for ids: [String]) async throws -> [FlightEntity] { /* by id */ }
}
```

Reuse, per the CLAUDE.md "reuse the library" principle:
- **`Services/AirportDatabase.swift`** (and `RZFlight` `KnownAirports`) to map spoken
  place names ↔ ICAO — do not hand-roll name matching.
- **`CachingBriefingRepository`** for `flights()` / `latestPack()` / `refreshBriefing`
  so intents work from cache in the cockpit.

### Navigation from an intent

Foregrounding intents set a pending navigation target that the UI consumes, reusing
the pattern already established for `onOpenURL`:

- Add a `PendingNavigation` value on `AppState` (e.g. `.flightList`, `.briefing(flightId:)`).
- `OpenBriefingIntent` / `OpenFlightListIntent` set it, return with `openAppWhenRun`.
- `WeatherBriefApp` / `FlightListView` read it on `.active` and route — the same seam
  `onOpenURL` already uses. Alternatively an intent can emit the existing
  `flyfunweather://` deep link, but a typed `PendingNavigation` avoids URL parsing.

### Auth & process model (Phase 1 has a low bar)

- Intents run **in-process**, reusing the Keychain JWT via `APIClient` /
  FlyFunCommon `RollingBearerSession` — no extra auth work.
- **No App Group needed** for these intents (that's only for Widgets / Live Activities
  / a separate extension process — out of scope here, tracked under Tier 2).

### Spotlight

`FlightEntity: IndexedEntity` (donated via `CSSearchableIndex`) makes flights
searchable in Spotlight and improves Siri's resolution of "my flight to Cannes".
Donate on flight-list load and on create; remove on delete.

---

## Phase 2 — View Annotations + Foundation Models (WWDC26 / iOS 27)

Goal: act on **what's on screen** conversationally, and narrate cached data offline.

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

### Foundation Models (on-device narration)

The on-device model narrates our **cached** `AdvisoryDetailResponse` into a natural
sentence for "explain this" — working **fully offline in the cockpit**. Split of
authority:

- **On-device / offline** — phrase cached advisory data ("convective is amber because
  CAPE peaks near waypoint 4 while cloud cover stays low").
- **Server / online** — the authoritative digest stays server-side (LangGraph); the
  on-device narration never replaces it, only fills the offline gap.

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

## Open Questions

- **Phrase discoverability** — how much to lean on App Shortcuts phrases (need "FlyFun")
  vs. teaching users to build their own Shortcuts (free phrasing). Onboarding tip?
- **Refresh feedback without push** — interim UX if the notification work lands later:
  local notification on next foreground? Poll `refreshStatus` in a short-lived background task?
- **"Next flight" definition** — soonest by departure, or soonest that is today-or-later
  and has a briefing? Align with `FlightListViewModel` ordering.
- **iPhone vs iPad** — intents are universal; confirm navigation targets exist on both size classes.
- **Spotlight donation lifecycle** — where to hook create/update/delete donations cleanly.

## References

- MCP server (parity source): `src/weatherbrief/mcp/server.py`
- Repository surface: `app/flyfun-weather/flyfun-weather/Services/BriefingRepository.swift`
- Airport name↔ICAO: `app/flyfun-weather/flyfun-weather/Services/AirportDatabase.swift`
- Notifications prerequisite: [ios-app-briefing-notifications.md](./ios-app-briefing-notifications.md)
</content>
</invoke>
