import Foundation
import OSLog

/// View model for creating or editing a flight.
@Observable
@MainActor
final class AddFlightViewModel {
    // Form fields
    var waypointsText: String = ""
    var departureDate: Date = Calendar.current.date(byAdding: .hour, value: 1, to: Date()) ?? Date()
    var cruiseAltitudeFt: Int = 5500
    var flightDurationHours: Double = 2.0
    var selectedAircraftId: Int?

    // FPL paste
    var fplText: String = ""
    var isParsing: Bool = false
    var parseError: String?

    // Aircraft
    private(set) var aircraftOptions: [AircraftResponse] = []
    private(set) var isLoadingAircraft: Bool = false

    // Submission
    var isSubmitting: Bool = false
    var errorMessage: String?
    var statusMessage: String?

    private let repository: any BriefingRepository
    private let editingFlight: FlightResponse?
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AddFlight")

    init(repository: any BriefingRepository, editing flight: FlightResponse? = nil) {
        self.repository = repository
        self.editingFlight = flight

        if let flight {
            waypointsText = flight.waypoints.joined(separator: " ")
            departureDate = flight.departureDate ?? departureDate
            cruiseAltitudeFt = flight.cruiseAltitudeFt
            flightDurationHours = flight.flightDurationHours
            selectedAircraftId = flight.aircraftId
        }
    }

    /// Parsed waypoints from the text field.
    var waypoints: [String] {
        waypointsText
            .uppercased()
            .split(whereSeparator: { " -,".contains($0) })
            .map(String.init)
            .filter { !$0.isEmpty }
    }

    var isEditing: Bool {
        editingFlight != nil
    }

    var navigationTitle: String {
        isEditing ? "Edit Flight" : "New Flight"
    }

    var submitTitle: String {
        isEditing ? "Save" : "Create"
    }

    var canSubmit: Bool {
        waypoints.count >= 2 && !isSubmitting && (!isEditing || hasChanges)
    }

    var requiresRebriefConfirmation: Bool {
        isEditing && hasChanges
    }

    var departureRange: ClosedRange<Date>? {
        guard let original = editingFlight?.departureDate else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0) ?? TimeZone.current
        let start = calendar.startOfDay(for: original)
        guard let end = calendar.date(byAdding: DateComponents(day: 1, second: -1), to: start) else {
            return nil
        }
        return start...end
    }

    var hasChanges: Bool {
        guard let editingFlight else { return true }
        if waypoints != editingFlight.waypoints.map({ $0.uppercased() }) { return true }
        if cruiseAltitudeFt != editingFlight.cruiseAltitudeFt { return true }
        if abs(flightDurationHours - editingFlight.flightDurationHours) > 0.01 { return true }
        if selectedAircraftId != editingFlight.aircraftId { return true }
        guard let originalDate = editingFlight.departureDate else { return true }
        return abs(departureDate.timeIntervalSince(originalDate)) > 1
    }

    func loadAircraft() async {
        guard !isLoadingAircraft else { return }
        isLoadingAircraft = true
        defer { isLoadingAircraft = false }

        do {
            let aircraft = try await repository.aircraft()
            aircraftOptions = aircraft
            if !isEditing, selectedAircraftId == nil {
                selectedAircraftId = aircraft.first(where: \.isDefault)?.id
            }
        } catch {
            Self.logger.debug("Aircraft list unavailable: \(error)")
        }
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
        statusMessage = "Creating flight…"
        defer {
            isSubmitting = false
            statusMessage = nil
        }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]

        let request = CreateFlightRequest(
            waypoints: waypoints,
            departureTime: formatter.string(from: departureDate),
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightDurationHours: flightDurationHours,
            aircraftId: selectedAircraftId
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

    /// Save edits and run the follow-up regeneration path when requested.
    func saveEditedFlight(regenerate: Bool) async -> FlightResponse? {
        guard canSubmit, let editingFlight else { return nil }

        isSubmitting = true
        errorMessage = nil
        statusMessage = "Saving flight…"
        defer {
            isSubmitting = false
            statusMessage = nil
        }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let aircraftId = selectedAircraftId ?? (editingFlight.aircraftId == nil ? nil : 0)
        let request = UpdateFlightRequest(
            aircraftId: aircraftId,
            departureTime: formatter.string(from: departureDate),
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightDurationHours: flightDurationHours,
            waypoints: waypoints
        )

        do {
            let response = try await repository.updateFlight(flightId: editingFlight.id, request)
            if regenerate && response.invalidation.needsRegeneration {
                try await regenerateBriefing(for: response.flight, invalidation: response.invalidation)
            }
            Self.logger.info("Updated flight \(response.flight.id): invalidation=\(response.invalidation.rawValue)")
            return response.flight
        } catch {
            errorMessage = error.localizedDescription
            Self.logger.error("Edit flight failed: \(error)")
            return nil
        }
    }

    private func regenerateBriefing(for flight: FlightResponse, invalidation: FlightInvalidation) async throws {
        switch invalidation {
        case .none:
            return
        case .advisoriesOnly:
            statusMessage = "Updating advisories…"
            do {
                let pack = try await repository.latestPack(flightId: flight.id)
                try await repository.recalculateAdvisories(
                    flightId: flight.id,
                    timestamp: pack.fetchTimestamp,
                    cruiseAltitudeFt: flight.cruiseAltitudeFt
                )
            } catch APIError.notFound {
                try await refreshBriefing(flightId: flight.id)
            }
        case .refetchNeeded:
            try await refreshBriefing(flightId: flight.id)
        }
    }

    private func refreshBriefing(flightId: String) async throws {
        statusMessage = "Regenerating briefing…"
        let stream = await repository.refreshStream(flightId: flightId)
        var completed = false
        for try await event in stream {
            switch event.type {
            case "progress":
                statusMessage = event.label ?? event.stage ?? "Regenerating briefing…"
            case "briefing_ready":
                statusMessage = "Briefing ready…"
            case "complete":
                completed = true
                statusMessage = "Briefing regenerated"
            case "error":
                throw APIError.serverError(500, event.message ?? "Briefing regeneration failed")
            default:
                break
            }
        }
        if !completed {
            Self.logger.warning("Refresh stream ended before a complete event while editing \(flightId)")
        }
    }
}
