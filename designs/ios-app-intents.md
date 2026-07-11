# iOS App — App Intents, Siri & Apple Intelligence

> Expose FlyFun briefings to Siri, Shortcuts, and Spotlight via App Intents.
> The MCP tool catalog (`src/weatherbrief/mcp/server.py`) is the reference for
> what capabilities to surface — this doc is its on-device sibling.

**Status: PHASE 1 IMPLEMENTED (#364).** The Tier-1 App Intents surface now
ships in `app/flyfun-weather/flyfun-weather/AppIntents/`: `FlightEntity`
(`AppEntity` + `IndexedEntity`) + `AirportEntity`, the six intents
(open-list / open-briefing / check / overview / refresh / airport-weather),
`FlyFunShortcuts`, the full tiered resolver (deterministic + Foundation Models
grounded selection over the candidate flights), the typed `PendingNavigation` seam on `AppState`, and
Spotlight reconciliation on flight-list load. Phase 2 (View Annotations, App
Intents Testing framework) remains proposed. It has **no SiriKit**, so there was
nothing to migrate — a clean start on the modern App Intents path.

> **Deviation from Decision 1 (App Group), deliberate.** Phase-1 intents are
> defined in the **main app target**, so they run **in-process** and share the
> Keychain JWT + `BriefingCacheStore` directly — no App Group is needed for any
> Tier-1 path (the `PendingNavigation` hand-off uses `UserDefaults.standard`).
> The entitlement was **not** provisioned here because it requires an Apple
> Developer-portal registration that would otherwise fail code-signing on every
> build, for zero Tier-1 benefit. Provisioning the App Group + moving the
> Keychain access-group / cache into the shared container should land with the
> first out-of-process consumer (Widgets / Live Activities / Control Center),
> which is when the migration actually pays off.

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

```swift
// FlightResolver.resolve, tier 2 (parser is injectable for tests)
let candidates = upcoming(flights, now: now).map {          // today-and-future, soonest first
    FlightCandidate(id: $0.id, line: candidateLine($0))     // "EGKB (London Biggin Hill, London, GB) → EGTF (Fairoaks, GB), 9 Jul 2026"
}
if let id = await parser.pick(phrase: raw, today: today, candidates: candidates),
   let picked = flights.first(where: { $0.id == id }) {     // validate: id ∈ real flights
    return [picked]
}

@Generable struct FlightChoice {
    @Guide(description: "The number of the matching flight from the list, or 0 if none match.")
    var choice: Int
}
```

**Division of authority:** the model only *selects from a closed set we provide*; the
returned index is range-checked and the id re-validated against the real flights — so the
model can never surface a flight that doesn't exist (a *safer* guarantee than token
re-matching, and a task on-device models are more reliable at). Selection sits behind a
`FlightPhraseResolving` protocol so the tier is unit-testable with an injected fake.

Reuse:
- **`Services/AirportDatabase.swift`** + `RZFlight` `KnownAirports` for place↔ICAO — do
  not hand-roll name matching.
- **`CachingBriefingRepository`** for `flights()` / `latestPack()` / `refreshBriefing`
  so intents work from cache in the cockpit.

**Scope:** the whole resolver is part of the **Tier-1 / today** issue — the Foundation
Models fallback is iOS 26, not a next-release feature. Resolver tier 1 is the mandatory
floor; the LLM tier is gated on `SystemLanguageModel.default.availability`, so devices
without Apple Intelligence degrade to deterministic + Siri disambiguation with no loss of
core paths. (Naming note: *resolver* tiers 1–3 above are internal to this algorithm and
distinct from the product **Tier 1 / Tier 2** issue split.) *(see Decisions)*

### Navigation from an intent (built, #364)

Foregrounding intents set a pending navigation target the UI consumes. Implemented as a
typed `PendingNavigation` enum (`.flightList` / `.briefing(flightId:)`) with **two backings**:

- `PendingNavigationStore` (UserDefaults.standard) is the **cold-launch-safe** hand-off — a
  foregrounding intent may run *before* `AppState` exists, so it writes there and returns
  with `openAppWhenRun`. `OpenBriefingIntent` / `OpenFlightListIntent` call `…Store.set(...)`.
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
- **App Group: deferred, not provisioned in Phase 1** (revised — see the status
  block at the top of this doc and Decision 1). Phase-1 intents run in-process and
  share the Keychain JWT + `BriefingCacheStore` directly, so no shared container is
  needed today; the `PendingNavigation` hand-off uses `UserDefaults.standard`.
  Moving the Keychain access-group + cache into a shared App Group container lands
  with the first out-of-process consumer (Widgets / Live Activities / Control Center).

### Spotlight

`FlightEntity: IndexedEntity` (donated via `CSSearchableIndex`) makes flights
searchable in Spotlight and improves Siri's resolution of "my flight to Cannes".
Donate on flight-list load and on create; remove on delete.

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

This capability is **iOS 26** and therefore not gated on the next release: the on-device
model narrates our **cached** `AdvisoryDetailResponse` into a natural sentence for
"explain this", **fully offline in the cockpit**. It can ship in the Tier-1 issue via a
*parameterized* `ExplainAdvisoryIntent(advisory:)` (pick the flight+advisory in Shortcuts).
What Phase 2 adds is only the **on-screen trigger** — View Annotations let the user say
"explain **this**" while looking at the row, instead of naming it. Split of authority:

- **On-device / offline** — phrase cached advisory data ("convective is amber because
  CAPE peaks near waypoint 4 while cloud cover stays low").
- **Server / online** — the authoritative digest stays server-side (LangGraph); the
  on-device narration never replaces it, only fills the offline gap.

Recommendation: build `ExplainAdvisoryIntent` + narration **alongside** the View
Annotations in the Tier-2 issue, since the on-screen "explain this" is its natural UX —
but note this is a *packaging choice*, not an OS constraint (the intent + narration are
iOS-26-capable and could land in Tier 1 if wanted).

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

Tiering is by **OS availability**: Tier 1 = everything implementable on **today's iOS 26**
(including Foundation Models); Tier 2 = only what needs the **next release (iOS 27)**.

1. **Tier 1 — App Intents, iOS 26 (everything doable today).** Entities (`FlightEntity` +
   `IndexedEntity`, `AirportEntity`), the open / check / overview / refresh / airport
   intents, `FlyFunShortcuts`, the **full tiered resolver** — deterministic + **Foundation
   Models grounded selection over the candidate flights** (iOS 26) + Siri disambiguation —
   the `PendingNavigation` seam, Spotlight donation, and the expired-token fallback
   (Decision 4). App Group provisioning (Decision 1) was **deferred** — see the status
   block up top. Core paths work on every device; the LLM tier lights up on
   Apple-Intelligence-capable devices.
2. **Tier 2 — iOS 27 / WWDC26 only.** The **View Annotations API** (on-screen "explain
   this" / "show the cross-section") and the **App Intents Testing framework**. By
   packaging choice, `ExplainAdvisoryIntent` + on-device advisory narration ride along
   here (their natural UX is the on-screen trigger) — though both are iOS-26-capable and
   could move to Tier 1 (see Phase 2 note).
3. **Notification — briefing-refresh push.** See
   [ios-app-briefing-notifications.md](./ios-app-briefing-notifications.md).

Independence: all three can proceed in parallel. The only soft coupling is that
`RefreshBriefingIntent` (Issue 1) gives its best "…I'll let you know when it's ready" UX
once Issue 3 lands; until then it speaks the interim "started — open FlyFun shortly"
(Open Questions → refresh feedback without push). Issue 1 does not *block* on Issue 3.

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
   confirmation step (friction in a hands-busy flow). *Built (#364): `RefreshDriver.Outcome`
   + `RefreshBriefingIntent` speak `alreadyFresh` / `started` / `completed` / `alreadyInProgress`
   / `rateLimited` / `failed`. A started run resumes early (interim "open FlyFun shortly")
   while a detached SSE drain keeps the server run alive.*
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
