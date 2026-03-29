import Foundation
import OSLog

/// View model for creating a new flight.
@Observable
@MainActor
final class AddFlightViewModel {
    // Form fields
    var waypointsText: String = ""
    var departureDate: Date = Calendar.current.date(byAdding: .hour, value: 1, to: Date()) ?? Date()
    var cruiseAltitudeFt: Int = 5500
    var flightDurationHours: Double = 2.0

    // FPL paste
    var fplText: String = ""
    var isParsing: Bool = false
    var parseError: String?

    // Submission
    var isSubmitting: Bool = false
    var errorMessage: String?

    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AddFlight")

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    /// Parsed waypoints from the text field.
    var waypoints: [String] {
        waypointsText
            .uppercased()
            .split(whereSeparator: { " -,".contains($0) })
            .map(String.init)
            .filter { !$0.isEmpty }
    }

    var canSubmit: Bool {
        waypoints.count >= 2 && !isSubmitting
    }

    /// Parse an ICAO FPL string and populate form fields.
    func parseFpl() async {
        let text = fplText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        isParsing = true
        parseError = nil
        defer { isParsing = false }

        do {
            let result = try await repository.parseFpl(text)
            if let error = result.error {
                parseError = error
                return
            }

            // Populate form from parsed FPL
            if !result.waypoints.isEmpty {
                waypointsText = result.waypoints.joined(separator: " ")
            }
            if let alt = result.altitudeFt {
                cruiseAltitudeFt = alt
            }
            if let duration = result.durationHours {
                flightDurationHours = duration
            }

            // Build departure date from parsed date + time
            if let dateStr = result.date, let timeStr = result.timeUtc {
                let isoString = "\(dateStr)T\(timeStr):00Z"
                if let date = ISO8601DateFormatter().date(from: isoString) {
                    departureDate = date
                }
            } else if let dateStr = result.date {
                // Date without time — use noon UTC as default
                let isoString = "\(dateStr)T12:00:00Z"
                if let date = ISO8601DateFormatter().date(from: isoString) {
                    departureDate = date
                }
            }

            fplText = ""
            Self.logger.info("Parsed FPL: \(result.waypoints.count) waypoints")
        } catch {
            parseError = "Failed to parse: \(error.localizedDescription)"
            Self.logger.error("FPL parse error: \(error)")
        }
    }

    /// Create the flight on the server. Returns the created flight on success.
    func createFlight() async -> FlightResponse? {
        guard canSubmit else { return nil }

        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]

        let request = CreateFlightRequest(
            waypoints: waypoints,
            departureTime: formatter.string(from: departureDate),
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightDurationHours: flightDurationHours
        )

        do {
            let flight = try await repository.createFlight(request)
            Self.logger.info("Created flight \(flight.id): \(flight.shortTitle)")
            return flight
        } catch {
            errorMessage = error.localizedDescription
            Self.logger.error("Create flight failed: \(error)")
            return nil
        }
    }
}
