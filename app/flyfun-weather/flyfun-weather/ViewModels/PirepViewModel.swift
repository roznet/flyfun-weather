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

    // GPS state
    var currentLocation: CLLocation?

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

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "PirepVM")

    init(flight: FlightResponse, repository: any BriefingRepository) {
        self.flight = flight
        self.repository = repository
    }

    /// Pre-fill altitude from GPS.
    func updateLocation(_ location: CLLocation) {
        currentLocation = location
        if reportedAltitudeFt == nil {
            // CLLocation.altitude is meters MSL
            reportedAltitudeFt = Int(location.altitude * 3.28084)
        }
    }

    var gpsAltitudeFt: Int? {
        guard let loc = currentLocation else { return nil }
        return Int(loc.altitude * 3.28084)
    }

    /// Build the request and submit.
    func submit() async {
        guard let loc = currentLocation else {
            errorMessage = "No GPS position available"
            return
        }

        submitState = .loading

        let request = SubmitPirepRequest(
            clientUuid: UUID().uuidString.lowercased(),
            observedAt: ISO8601DateFormatter().string(from: Date()),
            latitude: loc.coordinate.latitude,
            longitude: loc.coordinate.longitude,
            gpsAltitudeFt: gpsAltitudeFt,
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
        // Keep altitude from GPS
        if let loc = currentLocation {
            reportedAltitudeFt = Int(loc.altitude * 3.28084)
        }
    }
}
