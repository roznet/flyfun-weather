import CoreLocation
import Foundation
import OSLog

/// Form field identifiers for smart ordering.
enum PirepField: String, CaseIterable {
    case icing
    case turbulence
    case inCloud
    case cloudTops
    case ceiling
    case wind
    case temperature
    case remarks
}

/// View model for the PIREP reporting card.
@Observable
@MainActor
final class PirepViewModel {
    let flight: FlightResponse
    private let repository: any BriefingRepository

    // Form fields — no defaults selected (avoids confirmation bias)
    var reportedAltitudeFt: Int?
    var icingIntensity: String?
    var icingType: String?
    var turbulenceIntensity: String?
    var inCloud: Bool?
    var ceilingMslFt: Int?
    var topsMslFt: Int?
    var topsBasis: String?
    var tempC: Double?
    var windDir: Int?
    var windSpeedKt: Int?
    var remarks: String = ""

    // Smart field ordering
    var fieldOrder: [PirepField] = PirepField.allCases
    var useSmartOrdering: Bool = true

    // Submission state
    var submitState: LoadingState<PirepResponse> = .idle
    var errorMessage: String?
    var queuedOffline = false

    private let offlineStore: PirepOfflineStore
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "PirepVM")

    init(flight: FlightResponse, repository: any BriefingRepository, offlineStore: PirepOfflineStore) {
        self.flight = flight
        self.repository = repository
        self.offlineStore = offlineStore
    }

    /// Build the request and submit. Location is passed from the view which reads it
    /// from the tracking service via @Observable (same pattern as CrossSectionView).
    func submit(location: CLLocation?) async {
        guard let loc = location else {
            errorMessage = "No GPS position yet — wait a moment for a location fix, or enable location access in Settings"
            Self.logger.warning("PIREP submit blocked: no GPS location")
            return
        }

        let gpsAlt: Int? = loc.verticalAccuracy >= 0
            ? Int(loc.altitude * 3.28084) : nil

        Self.logger.info("Submitting PIREP at \(loc.coordinate.latitude), \(loc.coordinate.longitude) gpsAlt=\(gpsAlt.map(String.init) ?? "nil")")
        submitState = .loading

        let request = SubmitPirepRequest(
            clientUuid: UUID().uuidString.lowercased(),
            observedAt: {
                let fmt = ISO8601DateFormatter()
                fmt.formatOptions = [.withInternetDateTime]
                fmt.timeZone = TimeZone(identifier: "UTC")
                return fmt.string(from: Date())
            }(),
            latitude: loc.coordinate.latitude,
            longitude: loc.coordinate.longitude,
            gpsAltitudeFt: gpsAlt,
            reportedAltitudeFt: reportedAltitudeFt,
            inCloud: inCloud,
            icingIntensity: icingIntensity,
            icingType: icingType,
            turbulenceIntensity: turbulenceIntensity,
            ceilingMslFt: ceilingMslFt,
            topsMslFt: topsMslFt,
            topsBasis: topsBasis,
            tempC: tempC,
            windDir: windDir,
            windSpeedKt: windSpeedKt,
            remarks: remarks.isEmpty ? nil : remarks,
            source: "inflight"
        )

        do {
            let response = try await repository.submitPirep(request)
            submitState = .loaded(response)
            Self.logger.info("PIREP submitted: id=\(response.id)")
            // Connectivity confirmed — flush any queued PIREPs
            let synced = await offlineStore.sync(using: repository)
            if synced > 0 {
                Self.logger.info("Flushed \(synced) queued PIREPs after successful submit")
            }
        } catch let apiError as APIError where apiError.isTransientNetwork {
            await offlineStore.enqueue(request)
            let pending = await offlineStore.pendingCount
            queuedOffline = true
            submitState = .loaded(PirepResponse.offline)
            Self.logger.info("PIREP queued offline (pending: \(pending))")
        } catch {
            submitState = .error(error)
            errorMessage = error.localizedDescription
            Self.logger.error("PIREP submit failed: \(error)")
        }
    }

    /// Reset form for another report.
    func resetForm() {
        icingIntensity = nil
        icingType = nil
        turbulenceIntensity = nil
        inCloud = nil
        ceilingMslFt = nil
        topsMslFt = nil
        topsBasis = nil
        tempC = nil
        windDir = nil
        windSpeedKt = nil
        remarks = ""
        submitState = .idle
        errorMessage = nil
    }
}
