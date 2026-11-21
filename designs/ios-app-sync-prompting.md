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

- **Queue** — observations persist in SwiftData with `syncStatus = .local` (aspirational: the app imports SwiftData nowhere and has no observation queue; see the implementation note below)
- **Retry** — on connectivity change (`NWPathMonitor`), flush the queue
- **Conflict** — server is append-only for observations. No conflict resolution needed (each observation is immutable once created; amendments are new observations linked to the original)
- **Backpressure** — if queue grows large (extended offline), batch in chunks of 50

### Real-time sharing (Starlink-connected pilots)

> Status: spec only (Phase 3c). Neither the app nor the server has any WebSocket code — grep for `WebSocket` returns nothing in `app/` or `src/weatherbrief/`. The server's PIREP API is HTTP-only (`POST /api/pireps`, `POST /api/pireps/batch`, bounds/airport queries) and has no client since #1024.

- **WebSocket** connection kept alive during flight session
- Outbound: observations pushed immediately on creation
- Inbound: server relays nearby observations from other active flights
- Fallback to polling if WebSocket unavailable

### Current implementation note

None of the sync engine is built. The one queue that existed, the iOS PIREP offline queue (`PirepOfflineStore`, flushed by `AppState.syncPendingPireps`), was removed with the PIREP client UI in #1024; the server's `POST /api/pireps/batch` is kept. On sign-in `AppState.discardPirepQueues` deletes any leftover `PendingPireps/` directory and legacy `pending_pireps.json` from Documents. Offline detection for other calls lives on `APIError.isTransientNetwork` (unwraps `.networkError(let inner)` and allowlists `URLError` codes `.notConnectedToInternet`, `.timedOut`, `.networkConnectionLost`, `.cannotConnectToHost`). `NetworkMonitor` (wraps `NWPathMonitor`, on `AppState`) gates Wi-Fi-only auto-download and drives offline UI hints; nothing uses it to flush a queue.

## Prompting Engine (Phase 3b — Detailed Spec)

> Status: the *prompting* half is design intent only — no trigger rules, priority queue, cooldowns, or `ForecastSummary`/`forecastAtCurrentPosition()` exist in Swift. The Swift below is illustrative of the intended shape, not real symbols.
>
> What *does* exist is the position half of the tracker, under a different name: `Services/FlightTrackingService.swift` ("Start Flight", shipped) wraps `CLLocationManager` and publishes `ProjectedPosition` — along-route distance (nm), GPS altitude MSL, cross-track distance, `isOnRoute` (<10 nm), heading — projected onto `[TrackingRoutePoint]` via RZFlight's `RouteGeometry`. It has **no look-ahead and no forecast coupling**: it never reads cross-section data, computes no next-N-minutes window, and emits no transition events. So the section below is really "what is still missing on top of `FlightTrackingService`" — build the engine against that service rather than introducing a second `RouteProgressTracker`.

The intelligence layer that makes the app "smart" — watches aircraft progress, reads ahead in the forecast, decides when and what to ask. Requires Phase 2 offline data (synced cross-section data at each route point).

### Route Progress Tracker (partly shipped as `FlightTrackingService`)

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
- [Data Models](./ios-app-data-models.md) — `ForecastSummary`/`ObservationResponse` were never built; live tracking state (`ProjectedPosition`, `TrackingRoutePoint`) is in-memory only, nothing persisted
- Key code paths: `app/flyfun-weather/flyfun-weather/Services/{NetworkMonitor,FlightTrackingService}.swift`, `App/AppState.swift` (`discardPirepQueues`)
