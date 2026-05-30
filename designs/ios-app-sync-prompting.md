# iOS App — Sync & Prompting Engines

> Offline queue + WebSocket (Phase 3a) + forecast-driven prompt engine (Phase 3b)

## Sync Engine (Phase 3a — Detailed Spec)

Handles the core offline-first challenge:

```
Observation created
       │
       ▼
  ┌─────────┐    yes    ┌──────────┐
  │ Online? ├──────────▶│ POST to  │──▶ Mark .synced
  └────┬────┘           │  server  │
       │ no             └──────────┘
       ▼
  Save locally
  (.local status)
       │
       ▼
  Queue for sync
       │
  ─ ─ ─ ─ ─ ─  (connectivity restored)
       │
       ▼
  Batch POST
  queued observations
       │
       ▼
  Mark .synced
```

- **Queue** — observations persist in SwiftData with `syncStatus = .local`
- **Retry** — on connectivity change (`NWPathMonitor`), flush the queue
- **Conflict** — server is append-only for observations. No conflict resolution needed (each observation is immutable once created; amendments are new observations linked to the original)
- **Backpressure** — if queue grows large (extended offline), batch in chunks of 50

### Real-time sharing (Starlink-connected pilots)

- **WebSocket** connection kept alive during flight session
- Outbound: observations pushed immediately on creation
- Inbound: server relays nearby observations from other active flights
- Fallback to polling if WebSocket unavailable

### Current implementation note (Phase 3 M1)

`PirepOfflineStore` actor writes the queue to a JSON file (`pending_pireps.json`) in Documents (not SwiftData yet). Offline detection lives on `APIError.isTransientNetwork`: it unwraps `case .networkError(let inner)`, casts `inner as? URLError`, and only treats an allowlist of codes as offline (`.notConnectedToInternet`, `.timedOut`, `.networkConnectionLost`, `.cannotConnectToHost`) — `APIClient` wraps URL errors in `APIError.networkError`, so the raw `URLError.code` is on the inner error, not the top level. `PirepViewModel.submit` catches `where apiError.isTransientNetwork`, calls `offlineStore.enqueue(request)`, and shows a synthetic `PirepResponse.offline`. On a successful online submit it first calls `offlineStore.sync(using: repository)`, which drains the whole queue in one `submitPirepsBatch` call (no chunking yet — server dedups via `client_uuid`).

There is **no `NWPathMonitor`** yet. The queue is flushed on two triggers: (1) the successful-submit path above, and (2) `AppState.syncPendingPireps()` (calls `offlineStore.load()` then `sync(using:)`), invoked from `WeatherBriefApp` on `scenePhase == .active` — i.e. the queue retries when the app is foregrounded, not on a connectivity-restored event.

## Prompting Engine (Phase 3b — Detailed Spec)

> Status: design intent only — none of this is implemented yet. No `RouteProgressTracker`, trigger rules, priority queue, or `ForecastSummary`/`forecastAtCurrentPosition()` exist in the iOS code. The Swift below is illustrative of the intended shape, not real symbols.

The intelligence layer that makes the app "smart" — watches aircraft progress, reads ahead in the forecast, decides when and what to ask. Requires Phase 2 offline data (synced cross-section data at each route point).

### Route Progress Tracker

Continuously match aircraft GPS position to nearest route point and track progress as percentage / distance along route:
- **Current conditions** — what the forecast predicts at current position and altitude
- **Look-ahead** — what's coming in next 5–15 minutes of flight (configurable)
- **Transition detection** — when aircraft crosses from one forecast regime to another (e.g., "no icing" → "light icing")

```
Route points:   [0] ---- [1] ---- [2] ---- [3] ---- [4] ---- [5]
                                     ✈ (current)     ↓ look-ahead
Forecast:       CLR  CLR  SCT  BKN  BKN  OVC
Icing:          ---  ---  ---  LGT  LGT  MOD
                                    ^^^
                            Trigger: entering icing zone
```

### Trigger Rules

Each trigger has entry/exit logic + cooldown:

| Trigger | Entry | Exit / Reset | Cooldown |
|---------|-------|--------------|----------|
| Icing zone | Forecast icing ≥ Trace at current point | Icing = None for 2+ consecutive points | 10 min |
| IMC zone | Cloud base ≤ cruise altitude, BKN/OVC | CLR/SCT or base > cruise + 1000ft | 10 min |
| Convective | Convective risk ≥ MODERATE within look-ahead | Risk drops below MODERATE | 15 min |
| Turbulence | CAT or strong vertical motion at cruise | CAT clear, motion calm | 10 min |
| Cloud base transition | Altitude crosses predicted cloud base (±500ft) | Altitude moves >1000ft from base | 10 min |
| Wind shear | Predicted wind change >30° or >15kt between current and next segment | Next segment reached | Once per segment |
| Periodic | No prompt in last N min and no hazard active | After prompt | Configurable (default 15 min) |

### Priority Queue

When multiple triggers fire simultaneously (or within the rate limit window), queue by priority:

1. **Convective** (safety-critical, time-sensitive)
2. **Icing zone entry** (safety-critical)
3. **IMC entry** (significant)
4. **Turbulence** (significant)
5. **Cloud base transition** (moderate)
6. **Wind shear** (moderate)
7. **Periodic check-in** (low — always deferred if anything else is pending)

Only the highest-priority pending trigger fires. Lower-priority triggers are suppressed if a higher one covers the same conditions (e.g., entering IMC subsumes a cloud-base-transition prompt).

### Forecast Lookup

When a trigger fires, the engine reads synced cross-section data at current point and altitude to pre-populate the observation card:

```swift
func forecastAtCurrentPosition() -> ForecastSummary {
    let routeIdx = routeTracker.nearestPointIndex
    let altFt = locationManager.currentAltitudeFt

    let cs = briefingPayload.crossSection(for: selectedModel)
    let icing = cs.icingAt(pointIndex: routeIdx, altitudeFt: altFt)
    let cloud = cs.cloudAt(pointIndex: routeIdx, altitudeFt: altFt)
    let wind = cs.windAt(pointIndex: routeIdx, altitudeFt: altFt)

    return ForecastSummary(
        predictedIcing: icing,
        predictedCloudCoverage: cloud.coverage,
        predictedCloudBaseFt: cloud.baseFt,
        predictedWindDir: wind.direction,
        predictedWindSpeedKt: wind.speed,
        model: selectedModel
    )
}
```

This is why cross-section data is part of the lightweight sync payload — it's the source of truth for both visualization and prompting.

## References

- [Features](./ios-app-features.md) — trigger types and prompt UX
- [UI](./ios-app-ui.md) — prompted report card layout
- [Data Models](./ios-app-data-models.md) — `ForecastSummary`, `ObservationResponse`
