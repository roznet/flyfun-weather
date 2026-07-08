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

`FlightEntity` is resolved by an `EntityStringQuery` over a **tiny, closed set** — the
user's handful of upcoming flights. That makes a *deterministic* resolver the primary
path and an on-device model a *fallback*, not the first resort. Two distinct "models"
matter here:

- **Siri's own resolution** (system-side, Gemini-backed at WWDC26): flattens/cleans the
  phrase and, with `IndexedEntity`'s semantic index, does more matching before it even
  calls our query. We benefit passively; it improves each OS release.
- **Foundation Models framework** (a model *we* call): the on-device ~3B Apple
  Intelligence LLM, available since iOS 26. With **guided generation** it turns a free
  string into a typed `{place, when}` — ideal for messy phrasing.

**Tiered resolution:**

| Tier | Handles | Cost / availability |
|---|---|---|
| 1. Deterministic — `AirportDatabase`/`RZFlight` place↔ICAO + relative-date keywords | "Fairoaks", "EGTF", "Le Touquet", "tomorrow", weekday names | free, instant, offline, **every device** |
| 2. Foundation Models guided generation → `{place, when}`, then re-run tier-1 match | loose phrasing: "my trip to the coast next weekend" | on-device, needs Apple-Intelligence-capable device + enabled |
| 3. Siri disambiguation UI | still ≥2 matches after tiers 1–2 | system-provided |

Tier 1 is the **floor** and must always exist: Foundation Models requires an
Apple-Intelligence-capable device (iPhone 15 Pro / M-series+), the feature enabled, and
the model present — so it can never be a hard dependency. Gate tier 2 on
`SystemLanguageModel.default.availability`; if unavailable, fall straight to tier 3.

```swift
struct FlightEntityQuery: EntityStringQuery {
    func entities(matching string: String) async throws -> [FlightEntity] {
        let flights = try await repository.flights()             // cache-first
        // Tier 1 — deterministic (authoritative)
        var hits = flights.filter {
            matchesDestination($0, string) ||                   // AirportDatabase place↔ICAO
            matchesRelativeDate($0, string)                     // "tomorrow", weekday — parsed in-app
        }
        // Tier 2 — on-device LLM fallback, only when tier 1 is empty AND the model is available
        if hits.isEmpty, case .available = SystemLanguageModel.default.availability {
            let q = try await LanguageModelSession()
                .respond(to: string, generating: FlightQuery.self)
            hits = flights.filter { matches($0, place: q.place, when: q.when) }
        }
        return hits.map(FlightEntity.init)                      // Siri disambiguates if >1
    }
    func suggestedEntities() async throws -> [FlightEntity] { /* upcoming flights */ }
    func entities(for ids: [String]) async throws -> [FlightEntity] { /* by id */ }
}

@Generable struct FlightQuery {
    @Guide(description: "airport or city name mentioned, e.g. Fairoaks")
    var place: String?
    @Guide(description: "when: tomorrow, Saturday, next week")
    var when: String?
}
```

**Division of authority:** the LLM only flattens *language* into `{place, when}`; the
deterministic matcher against real flights stays the authority — so the model can never
invent a flight that doesn't exist. This keeps correctness in well-tested code and uses
the LLM only for input variety (per the CLAUDE.md "push complexity into well-tested
code" principle).

Reuse:
- **`Services/AirportDatabase.swift`** + `RZFlight` `KnownAirports` for place↔ICAO — do
  not hand-roll name matching.
- **`CachingBriefingRepository`** for `flights()` / `latestPack()` / `refreshBriefing`
  so intents work from cache in the cockpit.

**Scope:** all three tiers ship in v1 (decided). Tier 1 is the mandatory floor; tier 2 is
gated on `SystemLanguageModel.default.availability`, so devices without Apple Intelligence
degrade to tier 1 + Siri disambiguation with no loss of the core paths. *(see Decisions)*

### Navigation from an intent

Foregrounding intents set a pending navigation target that the UI consumes, reusing
the pattern already established for `onOpenURL`:

- Add a `PendingNavigation` value on `AppState` (e.g. `.flightList`, `.briefing(flightId:)`).
- `OpenBriefingIntent` / `OpenFlightListIntent` set it, return with `openAppWhenRun`.
- `WeatherBriefApp` / `FlightListView` read it on `.active` and route — the same seam
  `onOpenURL` already uses. Alternatively an intent can emit the existing
  `flyfunweather://` deep link, but a typed `PendingNavigation` avoids URL parsing.

### Auth & process model

- Intents run **in-process**, reusing the Keychain JWT via `APIClient` /
  FlyFunCommon `RollingBearerSession` — no OAuth in the intent path (see Decision 4
  for the expired-token fallback).
- **App Group is provisioned in Phase 1** (Decision 1). The intents themselves don't
  strictly require it, but we move the Keychain access-group + `BriefingCacheStore`
  into the shared container now, so the later Widgets / Live Activities / Control Center
  work needs no Keychain/cache migration.

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

## Implementation issues

This doc breaks into three **independently-shippable** GitHub issues:

1. **Tier 1 — App Intents (iOS 26, ships today).** Entities (`FlightEntity` +
   `IndexedEntity`, `AirportEntity`), the open / check / overview / refresh / airport
   intents, `FlyFunShortcuts`, the **deterministic** resolver + Siri disambiguation
   (tiers 1 & 3), the `PendingNavigation` seam, Spotlight donation, App Group
   provisioning (Decision 1), and the expired-token fallback (Decision 4). Functional on
   every device.
2. **Tier 2 — Apple Intelligence (WWDC26 / iOS 27).** The Foundation Models resolver
   fallback (resolver tier 2), View Annotations ("explain this" / "show the
   cross-section"), on-device narration of cached advisories, and the App Intents Testing
   framework. Purely additive on top of Issue 1; gated on model availability.
3. **Notification — briefing-refresh push.** See
   [ios-app-briefing-notifications.md](./ios-app-briefing-notifications.md).

Independence: all three can proceed in parallel. The only soft coupling is that
`RefreshBriefingIntent` (Issue 1) gives its best "…I'll let you know when it's ready" UX
once Issue 3 lands; until then it speaks the interim "started — open FlyFun shortly"
(Open Questions → refresh feedback without push). Issue 1 does not *block* on Issue 3.

## Decisions

Locked (★) decisions first, then defaults still open to revision.

1. **★ DECIDED — App Group provisioned now.** Even though Phase-1 intents run in-process
   and don't consume it, we provision the shared App Group and place the Keychain
   access-group + briefing cache in it from the start, so Widgets / Live Activities /
   Control Center (broader-plan Tier 2) add no later Keychain/cache migration. *Task:
   define the App Group id, move `KeychainBearerTokenStore` access-group + the
   `BriefingCacheStore` / Application-Support path into the shared container in Phase 1.*
2. **★ DECIDED — Refresh via Siri: freshness gate only, no confirmation.** A voice
   "refresh" triggers a real, **billed** pipeline run (see
   [cost-attribution](./cost-attribution-design.md)), but the server's `already_fresh`
   gate prevents redundant spend and it is rate-limited (409/429). No extra Siri
   confirmation step (friction in a hands-busy flow). *Task: define spoken responses for
   `queued` / `already_fresh` / `already_in_progress` / `rate_limited`.*
3. **★ DECIDED — On-device LLM resolver fallback is committed, delivered in the Tier-2
   Apple-Intelligence issue.** The full tiered resolver is the target: deterministic
   (Tier-1 issue, mandatory floor) → Foundation Models `{place, when}` → Siri
   disambiguation. The LLM fallback is gated on `SystemLanguageModel.default.availability`,
   so the Tier-1 intent ships and works fully without it and tier 2 is purely additive.
   *Task (Tier-2 issue): `@Generable FlightQuery` + availability gate + guided-generation call.*
4. **★ DECIDED — Signed-out / expired-token behaviour.** Attempt a silent token refresh
   (`RollingBearerSession`) first; if it fails, foreground intents throw
   `needsToContinueInForegroundError` ("Open FlyFun to sign in") and background intents
   speak "Please open FlyFun to sign in first."
5. **Empty-state / no-match dialog per intent.** "No upcoming flights", "I couldn't find a
   flight to X", "You're offline and that briefing isn't downloaded." These spoken lines
   *are* the voice UX — decide them explicitly.
6. **Navigation seam.** Typed `PendingNavigation` on `AppState` (proposed) vs reuse
   `flyfunweather://`. **Recommend: typed `PendingNavigation`**; must handle cold-launch
   (set before window) and warm (`.active`).
7. **AppShortcut phrase set.** ~10-shortcut soft limit, every phrase needs "FlyFun".
   **Recommend: English phrases v1**, FR/DE localization as fast-follow (place resolution
   is already language-agnostic via ICAO).
8. **Spotlight donation lifecycle.** Donate `FlightEntity` on list-load + create, remove on
   delete; avoid stale donations for server-deleted flights.
9. **★ DECIDED — Privacy / prediction surfacing: discoverable.** Flight routes are not
   especially sensitive and surfacing requires the user's own unlocked device, so intents
   and `FlightEntity` are discoverable in Shortcuts/Spotlight and Siri may predict them.

## Open Questions

- **Phrase discoverability** — how much to lean on App Shortcuts phrases (need "FlyFun")
  vs. teaching users to build their own Shortcuts (free phrasing). Onboarding tip?
- **Refresh feedback without push** — interim UX if the notification work lands later:
  local notification on next foreground? Poll `refreshStatus` in a short-lived background task?
- **"Next flight" definition** — soonest by departure, or soonest that is today-or-later
  and has a briefing? Align with `FlightListViewModel` ordering.
- **iPhone vs iPad** — intents are universal; confirm navigation targets exist on both size classes.
- **`AirportWeatherIntent` day parameter** — `Int` (0–3) is unfriendly for voice; make it
  an `@AppEnum` ("today"/"tomorrow"/…) so Siri and Shortcuts read naturally.
- **Siri output shape** — which intents use `ProvidesDialog` (spoken) vs `ShowsSnippetView`
  (a small result card); e.g. `CheckBriefingIntent` likely wants both.

## References

- MCP server (parity source): `src/weatherbrief/mcp/server.py`
- Repository surface: `app/flyfun-weather/flyfun-weather/Services/BriefingRepository.swift`
- Airport name↔ICAO: `app/flyfun-weather/flyfun-weather/Services/AirportDatabase.swift`
- Notifications prerequisite: [ios-app-briefing-notifications.md](./ios-app-briefing-notifications.md)
</content>
</invoke>
