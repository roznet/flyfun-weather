# iOS App Data Models

> The model layer as built: `Codable` API structs + Domain "Viz" structs. NO SwiftData.

## Architecture (as built)

**No `@Model` classes / SwiftData exist anywhere** (grep `import SwiftData` is empty). The
model layer is three plain-struct tiers, all under
`app/flyfun-weather/flyfun-weather/Models/` (+ two persistence actors under `Services/`):

1. **API tier** (`Models/API/*.swift`) — `Codable, Sendable` structs that mirror the server JSON
   1:1. Decoded straight from HTTP responses. This is the source of truth.
2. **Domain tier** (`Models/Domain/*.swift`) — `Viz*` structs the cross-section / route-graph
   renderers consume, plus the `Assessment` enum. Mapped from the API tier by ViewModels.
3. **Persistence** — two `actor`s that read/write JSON files on disk. No SwiftData, no Core Data.

`CLLocationCoordinate2D` (`Codable` caveat from the old doc) is irrelevant here — coordinates are
always stored as separate `lat`/`lon` `Double`s in the API/Viz structs.

## API tier — key structs (`Models/API/`)

One file per server response. Names below are the actual Swift type names.

- `FlightResponse` (`FlightResponse.swift`) — a flight. Fields: `id` (slug), `userId`,
  `profileId?`, `routeName`, `waypoints: [String]`, `departureTime` (ISO string), `targetDate`,
  `targetTimeUtc`, `cruiseAltitudeFt`, `flightCeilingFt`, `flightDurationHours`, `` `private` ``,
  `autoRefresh`, `autoRefreshHour?`, `createdAt`. Computed `departureDate`, `shortTitle`.
- `CreateFlightRequest` / `ParseFplRequest` / `ParseFplResponse` (`CreateFlightRequest.swift`) —
  create-flight body and ICAO-FPL parse round-trip.
- `PackMetaResponse` + `DataStatus` (`PackMetaResponse.swift`) — one briefing pack's metadata.
  `fetchTimestamp` is the unique id; `modelInitTimes`/`gribInitTimes` are `[String: Int]` (epoch s),
  plus `modelsSkippedRegion`, `dataStatus` (freshness: `fresh`, `staleModels`, `nextExpected*`).
- `SnapshotResponse` (`SnapshotResponse.swift`) — route + per-waypoint analysis + `routeObservations`
  (METAR/TAF airports). Nested: `RouteConfig`, `Waypoint`, `WaypointAnalysis`, `WindComponent`,
  `SoundingAnalysisSummary`, `ThermodynamicIndicesSummary`, `RouteObservations`, `AirportObservation`.
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
  `RouteAdvisoryResult` (aggregate + `perModel: [ModelAdvisoryResult]`), `AdvisoryCatalogEntry`,
  `AdvisoryParameterDef`, and airport-condition types (`AirportConditions`,
  `AirportConditionsSummary`, `RunwayEnd`, `AirportModelCondition`, `RunwayWind`).
- `PirepResponse` + `PirepListResponse` + `SubmitPirepRequest` (`PirepResponse.swift`) — see PIREPs.
- Other response files: `DigestResponse`, `ElevationResponse`, `PreferencesResponse`,
  `RefreshEvent` (SSE), `SoundingProfileResponse`.

## Domain tier — Viz structs (`Models/Domain/`)

`VizData.swift` defines what the SwiftUI `Canvas` cross-section and Swift Charts route graph render.
Built by ViewModels from `RouteAnalysesResponse` so the renderers never touch raw API shapes:

- `VizRouteData` → `[VizPoint]` (+ `WaypointMarker`, `TerrainPoint`).
- `VizPoint` carries per-distance state: `altitudeLines` (`AltitudeLines`), `cloudLayers` /
  `nwpCloudLayers?` (`[VizCloudLayer]`), `icingZones` / `icingOgimetNwpZones` (`VizIcingZone`),
  `sfipZones` (`VizSfipZone`), `catLayers` (`VizCATLayer`), `inversions` (`VizInversionLayer`),
  convective + NWP-convective fields, cloud-cover pcts, wind components, `nwpCloudDiag`
  (`VizCloudDiag`/`VizCloudDiagBand`), temperature, precip.

`Assessment.swift` — `enum Assessment: String, Codable, CaseIterable { green, amber, red,
unavailable }` with SwiftUI `color` + `label`. This is the GREEN/AMBER/RED route severity.

## Persistence — JSON files, two actors (`Services/`)

- `BriefingCacheStore` (`BriefingCacheStore.swift`) — `actor`. On-disk cache of pack endpoints under
  Application Support (`BriefingCache/<flightId>/<timestamp>/<endpoint>.json`). Maintains an
  `index.json` of `CachedPackEntry` (`flightId`, `timestamp`, `flightTitle`, `assessment`,
  `downloadedAt`, `endpoints: Set<String>`, `totalBytes`). `requiredEndpoints` = advisories, digest,
  snapshot, route-analyses, elevation — a pack `isComplete` when all are present. Also stores
  root/per-flight metadata files for offline list fallback.
- `PirepOfflineStore` (`PirepOfflineStore.swift`) — `actor`. JSON-file queue of unsent
  `SubmitPirepRequest`s at `Documents/pending_pireps.json`. `enqueue` on failure, `sync(using:)`
  flushes via a batch submit. Server dedups on `clientUuid`.

`CachingBriefingRepository` wraps the network `BriefingRepository` + `BriefingCacheStore` to make
the data layer offline-capable (see `ios-app-architecture.md`).

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

- **No SwiftData** — persistence is hand-rolled JSON-on-disk via two actors (`BriefingCacheStore`,
  `PirepOfflineStore`) plus `UserPreferencesStore` (UserDefaults). Chosen over a DB for the
  cache-the-pack model.
- **API structs are the source of truth** — Domain `Viz*` structs are derived per-render; nothing is
  persisted in Domain shape.
- **Client UUIDs for PIREPs** — `SubmitPirepRequest.clientUuid` lets the offline queue retry without
  duplicating server-side.
- **Pack completeness gate** — a cached pack only counts as offline-ready once all five
  `requiredEndpoints` are on disk.

## References

- [Architecture](./ios-app-architecture.md) — MVVM + Repository, the caching repo, layer split.
  Its persistence row agrees: file-based JSON + UserDefaults, no SwiftData.
- [Server API](./ios-app-server-api.md) — server-side shapes these structs decode.
- [Sync & Prompting](./ios-app-sync-prompting.md) — PIREP offline-sync flow.
