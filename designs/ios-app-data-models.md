# iOS App Data Models

> The model layer as built: `Codable` API structs + Domain "Viz" structs. NO SwiftData.

## Architecture (as built)

**No `@Model` classes / SwiftData exist anywhere** (grep `import SwiftData` is empty). The
model layer is three plain-struct tiers, all under
`app/flyfun-weather/flyfun-weather/Models/` (+ persistence stores under `Services/`):

1. **API tier** (`Models/API/*.swift`) — `Codable, Sendable` structs that mirror the server JSON
   1:1. Decoded straight from HTTP responses. This is the source of truth.
2. **Domain tier** (`Models/Domain/*.swift`) — `Viz*` structs the cross-section / route-graph
   renderers consume, plus the `Assessment` enum and the `FlightDuration` helper. Mapped from the
   API tier by ViewModels.
3. **Persistence** — JSON files on disk (+ UserDefaults for small flags). No SwiftData, no Core
   Data. The single SQLite file (`airports.db`) is downloaded read-only reference data, not a
   model store — see `AirportDatabase` below.

`CLLocationCoordinate2D` (`Codable` caveat from the old doc) is irrelevant here — coordinates are
always stored as separate `lat`/`lon` `Double`s in the API/Viz structs.

## API tier — key structs (`Models/API/`)

One file per server response. Names below are the actual Swift type names.

- `FlightResponse` (`FlightResponse.swift`) — a flight. Core fields: `id` (slug), `userId`,
  `profileId?`, `aircraftId?`/`aircraft` (`AircraftInfo?`), `routeName`, `waypoints: [String]`,
  `departureTime` (ISO string), `targetDate`, `targetTimeUtc`, `cruiseAltitudeFt`, `flightCeilingFt`,
  `flightDurationHours`, `` `private` ``, `autoRefresh`, `autoRefreshHour?`, `createdAt`. Plus
  cross-feature fields: `latestBriefing` (`BriefingStatusInfo?`, inlined per-flight assessment),
  `coverage` (`CoveragePending?`), `role`/`flexibility`/`altDepartureTime` (sharing + timing-
  flexibility), `notifyOverride`, `section` (server logbook bucket), `debrief` (`DebriefResponse?`,
  inlined for owned past flights), `shareCode`/`ownerDisplayName`/`isSubscribed` (share links),
  `rawRoute` (the typed Field-15 text). Computed `departureDate`, `shortTitle`, `isEditable`,
  `effectiveFlexibility`, `notifyOverrideMode`, `hasDebrief`, `isShareable`, `hasSubscribed`,
  `flightSection`/`resolvedSection(now:)`. **`isPast` is duration-aware** — it delegates to
  `hasEnded(now:)` (departure + duration), mirroring the server's `_flight_has_ended` and web's
  `isFlightPast`; a flight that departed 30 min ago on a 3 h trip is still in progress. Every
  late-added field is `var … = nil` so the synthesized memberwise init keeps old call sites
  compiling — follow that when adding more. Enums/nested types in the same file: `FlightRole`,
  `FlightSection`, `FlightNotifyOverride`, `FlexibilityMode`, `CoveragePending`, `AircraftInfo`,
  `BriefingStatusInfo`, `AdvisorySummary`, `AdvisoryChip`.
- `CreateFlightRequest` / `ParseFplRequest` / `ParseFplResponse` (`CreateFlightRequest.swift`) —
  create-flight body and ICAO-FPL parse round-trip.
- `PackMetaResponse` + `DataStatus` (`PackMetaResponse.swift`) — one briefing pack's metadata.
  `fetchTimestamp` is the unique id; `modelInitTimes`/`gribInitTimes` are `[String: Int]` (epoch s),
  plus `modelsSkippedRegion`, `dataStatus` (freshness: `fresh`, `staleModels`, `nextExpected*`),
  `assessment`/`assessmentReason` **or** `outlook`/`outlookReason` (mutually exclusive — long-range
  packs beyond the GRIB horizon carry the outlook tendency instead of a verdict), and `flexibility`
  injected fresh from the flight row at serve time (not baked into the stored pack).
- `SnapshotResponse` (`SnapshotResponse.swift`) — route + per-waypoint analysis + `routeObservations`
  (METAR/TAF airports) + `routeSigmets`. Nested: `RouteConfig`, `Waypoint`, `WaypointAnalysis`,
  `WindComponent`, `SoundingAnalysisSummary`, `ThermodynamicIndicesSummary`, `RouteObservations`,
  `AirportObservation`, `ObservationComparison`, `RouteSigmets`, `SigmetAlongRoute`. It also
  extracts the optional raw `observed_motion` member separately from ordinary `Codable` decoding,
  preserving unknown JSON keys and carrying the serve-time `X-Observed-Motion-Enabled` capability
  as transient network metadata.
- `RawObservedMotion` and typed observed-motion view (`ObservedMotion.swift`) — raw JSON wrapper plus
  conservative schema-1 accessors for the experimental motion explorer. The raw bytes are the cache
  source of truth; known fields drive rendering only after version, revision, source/frame,
  geometry, timing, accepted-motion and projection-authority checks pass. Unsupported/malformed
  motion disables that explorer without failing the surrounding briefing.
- `RouteAnalysesResponse` (`RouteAnalysesResponse.swift`) — the big one: per-point full sounding for
  every model, drives the cross-section. Nested `RoutePointAnalysis` (per point: `windComponents`,
  `sounding: [String: SoundingAnalysis]`, `modelDivergence: [ModelDivergence]`) → `SoundingAnalysis`
  with `indices`, `cloudLayers`, `nwpCloudLayers`, `icingZones`, `icingOgimetNwpZones`, `sfipZones`,
  `inversionLayers`, `convective`/`convectiveNwp`, `verticalMotion`, cloud-cover pcts,
  `nwpCloudDiagnostics`. Supporting types: `ThermodynamicIndices`,
  `EnhancedCloudLayer`, `IcingZone`, `SfipZone`, `InversionLayer`, `ConvectiveAssessment`,
  `VerticalMotionAssessment`, `CATRiskLayer`, `NWPCloudDiagnostics`, `NWPCloudLayerDiag`,
  `ModelDivergence`.
- `AdvisoriesResponse` (`AdvisoriesResponse.swift`) — route advisories + catalog. Nested
  `RouteAdvisoryResult` (aggregate + `perModel: [ModelAdvisoryResult]`), `Mitigation` (advice only —
  never changes the grade), `AdvisoryHighlights` + `RibbonSegment` + `HighlightRegion` (the #374
  cross-section scrim/verdict-ribbon geometry), `AdvisoryCatalogEntry`, `AdvisoryParameterDef`, and
  airport-condition types (`AirportConditions`, `AirportConditionsSummary`, `RunwayEnd`,
  `AirportModelCondition`, `RunwayWind`).
- `PirepResponse` + `PirepListResponse` + `SubmitPirepRequest` (`PirepResponse.swift`) — see PIREPs.
- Other response files (one struct family each): `DigestResponse`, `ElevationResponse`,
  `PreferencesResponse`, `RefreshEvent` (SSE, including optional raw `observed_motion` and transient
  motion capability) + `ActiveRefreshResponse`, `SoundingProfileResponse`,
  `AdvisoryDetailResponse`, `AirportWeatherResponse`, `AlternatesResponse`, `AutorouterRoute`,
  `BulkDeleteResponse`, `DebriefResponse`, `DebriefTaxonomy`, `FeedbackRequest`,
  `ForecastDaysResponse`, `ForecastMapResponse`, `FrequentAirportsResponse`, `HelpCatalogResponse`,
  `NotificationModels`, `ProfileResponse`, `RouteInterpretation`, `SystemMessageResponse`,
  `TimeOptionsResponse`, `UsageSummaryResponse`. (Full list = `ls Models/API/`.)

## Domain tier — Viz structs (`Models/Domain/`)

`VizData.swift` defines what the SwiftUI `Canvas` cross-section and Swift Charts route graph render.
Built by ViewModels from `RouteAnalysesResponse` so the renderers never touch raw API shapes:

- `VizRouteData` → `[VizPoint]` (+ `WaypointMarker`, `TerrainPoint`), plus
  `advisoryHighlights: VizAdvisoryHighlights?`. That last one is the exception to the rule: it is
  **not** built by `extractVizData` — the geometry lives in the advisories manifest, so the
  cross-section scene attaches it just before rendering (mirroring web `briefing-main`). It's
  `Equatable` because the static scene's redraw gate compares it by value.
- `VizPoint` carries per-distance state: `altitudeLines` (`AltitudeLines`), `cloudLayers` /
  `nwpCloudLayers?` (`[VizCloudLayer]`), `icingZones` / `icingOgimetNwpZones` (`VizIcingZone`),
  `sfipZones` (`VizSfipZone`), `catLayers` (`VizCATLayer`), `inversions` (`VizInversionLayer`),
  convective + NWP-convective fields, cloud-cover pcts, wind components, `nwpCloudDiag`
  (`VizCloudDiag`/`VizCloudDiagBand`), temperature, precip.

`Assessment.swift` — `enum Assessment: String, Codable, CaseIterable { green, amber, red,
unavailable }` with SwiftUI `color` + `label`. This is the GREEN/AMBER/RED route severity.

`FlightDuration.swift` — quarter-hour split of `flight_duration_hours` for the hour/minute pickers
(rounds **up**, clamps at 12h45). Hand-copied mirror of `web/ts/utils/duration.ts`; `/sync-ios-web`
exists to catch drift between the two.

## Persistence — JSON files (`Services/`)

Two `actor`s hold the model-bearing state:

- `BriefingCacheStore` (`BriefingCacheStore.swift`) — `actor`. On-disk cache of pack endpoints under
  Application Support, scoped per signed-in user
  (`BriefingCache/users/<scope>/<flightId>/<timestamp>/<endpoint>.json`); writes are at-rest
  encrypted (`.completeFileProtectionUntilFirstUserAuthentication`, `.atomic`) and excluded from
  iCloud backup. Maintains an
  `index.json` of `CachedPackEntry` (`flightId`, `timestamp`, `flightTitle`, `assessment`,
  `downloadedAt`, `endpoints: Set<String>`, `totalBytes`, `departureTime?`). `requiredEndpoints` =
  advisories, digest, snapshot, route-analyses, elevation — a pack `isComplete` when all are present
  (a downloaded pack also holds `sounding-{pt}-{model}` profiles from the bundle). `departureTime`
  (optional, captured at download) lets the eviction sweep age packs out by flight date. Also stores
  root/per-flight metadata files for offline list fallback. **The sweep itself
  (`pruneStalePacks(olderThanDays:)`) lives on `CachingBriefingRepository`, not on the store** —
  it's a cache-only path that never makes a request; `AppState.pruneStaleCache()` drives it on
  foreground.
- `PirepOfflineStore` (`PirepOfflineStore.swift`) — `actor`. JSON-file queue of unsent
  `SubmitPirepRequest`s at `Documents/pending_pireps.json`. `enqueue` on failure, `sync(using:)`
  flushes via a batch submit. Server dedups on `clientUuid`.

`CachingBriefingRepository` wraps the network `BriefingRepository` + `BriefingCacheStore` to make
the data layer offline-capable (see `ios-app-architecture.md`).

The remaining stores are `@MainActor final class`es, not actors, and hold ancillary state rather
than briefing models: `HelpCatalogStore` (`Documents/help-catalog.json` + ETag in UserDefaults —
the catalog is ~100 KB+, too big for UserDefaults), `WhatsNewStore`
(`Documents/whats-new.json`; the stream is global so it needs no per-user scoping, and the unseen
count is deliberately NOT persisted), `UserPreferencesStore` + `AppSettingsStore` (UserDefaults),
and `AirportDatabase` (downloaded `airports.db` in Application Support, opened via FMDB and wrapped
by RZFlight's `KnownAirports` for offline ICAO autocomplete — read-only reference data, ETag-
refreshed; no cached copy simply means empty suggestions, never a broken search).

`BriefingCacheStore` also patches the raw `observed_motion` member through the
same snapshot/bundle/realtime cache boundary. Newer unavailable/disabled motion
replaces older ready motion; older bundle/realtime responses cannot roll it
back; same-revision conflicts disable motion presentation instead of selecting a
nicer-looking envelope. Pack deletion fencing prevents late direct writes from
recreating a removed snapshot. Motion capability is process/session authority and
is not persisted as an enabled flag.

`ObservedMotionState` is intentionally not a persistence model. It holds the
in-memory mode flag, family/time/feature selections, request generation,
capability state, expiry/clock status and stored-only fallback for the
experimental map explorer. Active projection styling requires a current enabled
capability observation from the existing snapshot/refresh network boundary;
offline or unknown authority can inspect stored analysis only.

## PIREPs — flat model, not first-class "Observations"

The old design's `Observation` / `FlightSession` / `TrackPoint` SwiftData entities and their enums
(`ObservationSource`, `PromptTrigger`, `ForecastSummary`, etc.) were **never built**. PIREPs today
are a single flat API struct:

- `PirepResponse` — server PIREP. Key fields: `id: Int`, `clientUuid: String?`, `submittedAt`,
  `observedAt`, `latitude`/`longitude`, `gpsAltitudeFt?`, `reportedAltitudeFt?`, `inCloud?`,
  `icingIntensity?`/`icingType?`, `turbulenceIntensity?`, `ceilingMslFt?`, `topsMslFt?`/`topsBasis?`,
  `tempC?`, `windDir?`/`windSpeedKt?`, `remarks?`, `aircraftType?`, `packId?`, `source`, `isOwn`.
  Computed `altitude` (reported ?? GPS), `observedDate`, `maxSeverity`; `.offline` sentinel.
- `SubmitPirepRequest` — submit body; `clientUuid` is client-generated for idempotent offline sync
  (the one surviving idea from the old "client UUIDs" choice). `source` defaults to `"inflight"`.
- `PirepListResponse` — `items` + `count`.

There is no client-side session/track-point persistence model. `FlightTrackingService`
(`Services/`) holds live GPS state (`ProjectedPosition`, `TrackingRoutePoint`) in memory, not as a
persisted entity.

## Key Choices

- **No SwiftData** — persistence is hand-rolled JSON-on-disk (`BriefingCacheStore`,
  `PirepOfflineStore`, `HelpCatalogStore`, `WhatsNewStore`) plus UserDefaults for small flags.
  Chosen over a DB for the cache-the-pack model: what's cached is whole server responses, keyed by
  flight+timestamp, so a file tree is the natural shape and there is nothing to query.
- **API structs are the source of truth** — Domain `Viz*` structs are derived per-render; nothing is
  persisted in Domain shape.
- **Client UUIDs for PIREPs** — `SubmitPirepRequest.clientUuid` lets the offline queue retry without
  duplicating server-side.
- **Pack completeness gate** — a cached pack only counts as offline-ready once all five
  `requiredEndpoints` are on disk.
- **Observed-motion raw cache** — `observed_motion` is preserved as raw JSON with revision-aware
  merge semantics. The typed Swift view is deliberately lossy and conservative; it never reconstructs
  cache bytes from today's known fields and never authorizes active prediction from persisted
  capability.

## Experimental Observed Motion Status

Backend, web and native software paths are implemented for the schema-1
`observed_motion` envelope, including radar/high-top feature records, route
rows, projection controls and stored/active authority states. Native execution
is still not claimed: Swift unit/UI tests were authored and statically reviewed,
and fixture literals were validated with the Python producer model, but no
`xcodebuild`, simulator or device run was performed.

Source readiness is narrower than the UI surface. Radar can publish accepted
experimental motion from retained OPERA DBZH local files. CTTH motion/projection
and quantitative cloud association remain source-gated on real product/domain
geolocation evidence. No regional replay has established predictive usefulness
or an operational horizon.

## References

- [Architecture](./ios-app-architecture.md) — MVVM + Repository, the caching repo, layer split.
  Its persistence row agrees: file-based JSON + UserDefaults, no SwiftData.
- [Server API](./ios-app-server-api.md) — server-side shapes these structs decode.
- [Sync & Prompting](./ios-app-sync-prompting.md) — PIREP offline-sync flow.
