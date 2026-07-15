import Foundation
import OSLog

/// Owns the airport-forecast overlay data for the briefing route map (#428): the
/// server day/hour grid and the per-slot snapshot for the flight's nearest
/// forecast time. `@Observable`, so mutating `days` / `payload` / `payloadRevision`
/// re-renders the map view, which recolours from the already-fetched payload.
///
/// Only a new day/hour slice hits the network (`repository.forecastMap`); the
/// payload holds every model per airport, so switching the briefing model or the
/// overlay metric is a pure client recolour — mirroring the forecast map and the
/// web overlay (`designs/forecast-page.md`).
@Observable
@MainActor
final class RouteForecastOverlayModel {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "RouteForecastOverlay")

    /// The server day/hour/model grid (`/maps/forecast/days`), or nil until it
    /// loads. Nil ⇒ the overlay is simply not offered yet.
    private(set) var days: [ForecastDay]?
    /// The snapshot for `loadedSlotKey`, or nil while none is loaded for the
    /// current slot.
    private(set) var payload: ForecastMapResponse?
    /// Bumped on every payload change so the map rebuilds its airport markers.
    private(set) var payloadRevision = 0

    private let repository: any BriefingRepository
    private var daysLoading = false
    private var sliceLoading = false
    /// Slot key the current `payload` belongs to (guards against showing a stale
    /// slice for a different slot).
    private var loadedSlotKey: String?
    /// After a failure, back off rather than refetching on every re-render.
    private var retryAt = Date.distantPast
    private static let retryBackoff: TimeInterval = 30

    init(repository: any BriefingRepository) { self.repository = repository }

    /// The overlay slot for a flight's departure time against the loaded grid, or
    /// nil when the grid hasn't loaded or the flight is outside the horizon.
    func slot(departureTime: String?) -> RouteForecastSlot? {
        guard let days else { return nil }
        return RouteForecastOverlay.resolveSlot(departureIso: departureTime, days: days, now: Date())
    }

    /// Airports to draw for `slot`, or nil when the loaded payload is for a
    /// different slot (so stale markers never linger through a slot change).
    func airports(for slot: RouteForecastSlot) -> [ForecastAirport]? {
        loadedSlotKey == slot.key ? payload?.airports : nil
    }

    /// Lazy-load the day grid once. A failure leaves `days` nil (overlay not
    /// offered) and is retried on a later appear.
    func loadDaysIfNeeded() async {
        guard days == nil, !daysLoading else { return }
        daysLoading = true
        defer { daysLoading = false }
        do {
            days = try await repository.forecastDays().days
        } catch {
            Self.logger.warning("forecast days failed: \(error.localizedDescription)")
        }
    }

    /// Ensure the snapshot for `slot` is loaded (cached by slot key). Cheap no-op
    /// when it's already current. Honours a short failure backoff.
    func ensureSlice(_ slot: RouteForecastSlot) async {
        if loadedSlotKey == slot.key, payload != nil { return }
        guard !sliceLoading, Date() >= retryAt else { return }
        sliceLoading = true
        defer { sliceLoading = false }
        do {
            let resp = try await repository.forecastMap(day: slot.day, hour: slot.hour)
            payload = resp
            loadedSlotKey = slot.key
            payloadRevision &+= 1
        } catch let error as APIError where error.isCancellation {
            // Superseded by a newer slot (task cancellation) — benign, no backoff.
        } catch is CancellationError {
            // SwiftUI cancelled the `.task(id:)` on a slot change — benign.
        } catch {
            Self.logger.warning("forecast map \(slot.day)/\(slot.hour) failed: \(error.localizedDescription)")
            retryAt = Date().addingTimeInterval(Self.retryBackoff)
        }
    }

    /// The valid-time label of the loaded snapshot ("Wed 12Z"), empty until it
    /// loads.
    var forecastTimeLabel: String {
        RouteForecastOverlay.formatForecastTime(payload?.forecastTime)
    }
}
