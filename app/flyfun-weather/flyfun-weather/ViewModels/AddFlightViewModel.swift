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

    /// Non-nil when editing an existing flight (the create form, pre-filled —
    /// one form, two modes, §4.4).
    let editingFlight: FlightResponse?

    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AddFlight")

    init(repository: any BriefingRepository, flight: FlightResponse? = nil) {
        self.repository = repository
        self.editingFlight = flight
        if let flight {
            waypointsText = flight.waypoints.joined(separator: " ")
            if let date = flight.departureDate { departureDate = date }
            cruiseAltitudeFt = flight.cruiseAltitudeFt
            flightDurationHours = flight.flightDurationHours
        }
    }

    var isEditing: Bool { editingFlight != nil }

    /// Whether the edited fields differ from the original in a way that affects
    /// the forecast (route/time/FL/duration) — all of them do, so any change
    /// triggers the re-briefing cost confirm (§4.4).
    var hasForecastAffectingChange: Bool {
        guard let original = editingFlight else { return false }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return waypoints != original.waypoints.map { $0.uppercased() }
            || formatter.string(from: departureDate) != (original.departureDate.map { formatter.string(from: $0) } ?? "")
            || cruiseAltitudeFt != original.cruiseAltitudeFt
            || abs(flightDurationHours - original.flightDurationHours) > 0.01
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

    /// Dispatch to create or update depending on the mode.
    func save() async -> FlightResponse? {
        isEditing ? await updateFlight() : await createFlight()
    }

    /// Update an existing flight. The server regenerates the briefing for the
    /// changed parameters; the caller confirms the cost first (§4.4).
    func updateFlight() async -> FlightResponse? {
        guard let editingFlight, canSubmit else { return nil }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let request = UpdateFlightRequest(
            waypoints: waypoints,
            departureTime: formatter.string(from: departureDate),
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightDurationHours: flightDurationHours
        )
        do {
            let flight = try await repository.updateFlight(flightId: editingFlight.id, request: request)
            Self.logger.info("Updated flight \(flight.id): \(flight.shortTitle)")
            return flight
        } catch {
            errorMessage = error.localizedDescription
            Self.logger.error("Update flight failed: \(error)")
            return nil
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
