import Foundation
import OSLog

/// View model for creating or editing a flight (one form, two modes — §4.4).
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

    // Aircraft picker + inline create
    private(set) var aircraftOptions: [AircraftResponse] = []
    private(set) var isLoadingAircraft: Bool = false
    var newAircraftIcaoType: String = ""
    var newAircraftTailNumber: String = ""
    var newAircraftNickname: String = ""
    var newAircraftCruiseSpeedKt: String = ""
    var newAircraftCeilingFt: String = ""
    var newAircraftIsIfr: Bool = false
    var newAircraftIsFiki: Bool = false
    var newAircraftIsDefault: Bool = false
    private(set) var selectedAircraftType: AircraftTypeResponse?
    private(set) var aircraftTypeSuggestions: [AircraftTypeResponse] = []
    private(set) var isSearchingAircraftTypes: Bool = false
    private(set) var isSavingAircraft: Bool = false
    var aircraftFormError: String?

    // Submission
    var isSubmitting: Bool = false
    var errorMessage: String?
    /// Streamed progress message shown while regenerating the briefing (§4.4).
    var statusMessage: String?

    private let repository: any BriefingRepository
    private let editingFlight: FlightResponse?
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AddFlight")

    init(repository: any BriefingRepository, flight: FlightResponse? = nil) {
        self.repository = repository
        self.editingFlight = flight
        if let flight {
            waypointsText = flight.waypoints.joined(separator: " ")
            if let date = flight.departureDate { departureDate = date }
            cruiseAltitudeFt = flight.cruiseAltitudeFt
            flightDurationHours = flight.flightDurationHours
            selectedAircraftId = flight.aircraftId
        }
    }

    var isEditing: Bool { editingFlight != nil }

    var navigationTitle: String { isEditing ? "Edit Flight" : "New Flight" }

    var submitTitle: String { isEditing ? "Save" : "Create" }

    /// Parsed waypoints from the text field.
    var waypoints: [String] {
        waypointsText
            .uppercased()
            .split(whereSeparator: { " -,".contains($0) })
            .map(String.init)
            .filter { !$0.isEmpty }
    }

    var canSubmit: Bool {
        waypoints.count >= 2 && !isSubmitting && (!isEditing || hasChanges)
    }

    /// Any edited field differs from the original (gates the Save button).
    var hasChanges: Bool {
        guard let original = editingFlight else { return true }
        if waypoints != original.waypoints.map({ $0.uppercased() }) { return true }
        if cruiseAltitudeFt != original.cruiseAltitudeFt { return true }
        if abs(flightDurationHours - original.flightDurationHours) > 0.01 { return true }
        if selectedAircraftId != original.aircraftId { return true }
        guard let originalDate = original.departureDate else { return true }
        return abs(departureDate.timeIntervalSince(originalDate)) > 1
    }

    /// Whether the edit changes a forecast-affecting field (route/time/FL/duration).
    /// Aircraft-only edits are excluded — they don't regenerate the briefing, so
    /// they save without the re-briefing confirm (§4.4).
    var hasForecastAffectingChange: Bool {
        guard let original = editingFlight else { return false }
        if waypoints != original.waypoints.map({ $0.uppercased() }) { return true }
        if cruiseAltitudeFt != original.cruiseAltitudeFt { return true }
        if abs(flightDurationHours - original.flightDurationHours) > 0.01 { return true }
        guard let originalDate = original.departureDate else { return true }
        return abs(departureDate.timeIntervalSince(originalDate)) > 1
    }

    // MARK: - Aircraft picker

    var selectedAircraft: AircraftResponse? {
        guard let selectedAircraftId else { return nil }
        return aircraftOptions.first { $0.id == selectedAircraftId }
    }

    var canSaveAircraft: Bool {
        resolvedNewAircraftIcaoType != nil && !isSavingAircraft
    }

    func loadAircraft() async {
        guard !isLoadingAircraft else { return }
        isLoadingAircraft = true
        defer { isLoadingAircraft = false }

        do {
            let aircraft = try await repository.aircraft()
            aircraftOptions = aircraft.sortedForPicker()
            // Default-select the user's default aircraft only when creating.
            if !isEditing, selectedAircraftId == nil {
                selectedAircraftId = aircraft.first(where: \.isDefault)?.id
            }
        } catch {
            // Non-fatal: the form still works without saved aircraft.
            Self.logger.debug("Aircraft list unavailable: \(error)")
        }
    }

    func prepareNewAircraftForm() {
        newAircraftIcaoType = ""
        newAircraftTailNumber = ""
        newAircraftNickname = ""
        newAircraftCruiseSpeedKt = ""
        newAircraftCeilingFt = ""
        newAircraftIsIfr = false
        newAircraftIsFiki = false
        newAircraftIsDefault = aircraftOptions.isEmpty
        selectedAircraftType = nil
        aircraftTypeSuggestions = []
        aircraftFormError = nil
    }

    /// Search aircraft types as the user types. The caller (the form's `.task(id:)`)
    /// debounces and cancels superseded searches; we also bail out if the task was
    /// cancelled before the network call so a stale query never overwrites results.
    func searchAircraftTypes() async {
        let query = newAircraftIcaoType.trimmingCharacters(in: .whitespacesAndNewlines)
        // Clear a previously-picked type once the text diverges from it.
        if let selectedAircraftType, query.uppercased() != selectedAircraftType.icao {
            self.selectedAircraftType = nil
        }
        if let selectedAircraftType, query.uppercased() == selectedAircraftType.icao {
            aircraftTypeSuggestions = []
            isSearchingAircraftTypes = false
            return
        }
        guard !query.isEmpty else {
            aircraftTypeSuggestions = []
            isSearchingAircraftTypes = false
            return
        }
        guard query.count <= 20 else { return }

        // Defensive cancellation guard: even though the debounce in the view
        // cancels superseded `.task(id:)` runs, never fire a stale search.
        guard !Task.isCancelled else { return }

        isSearchingAircraftTypes = true
        defer { isSearchingAircraftTypes = false }
        do {
            let results = try await repository.searchAircraftTypes(query)
            // The query may have been superseded while the request was in flight.
            guard !Task.isCancelled else { return }
            aircraftTypeSuggestions = results
        } catch {
            aircraftTypeSuggestions = []
            Self.logger.debug("Aircraft type search unavailable: \(error)")
        }
    }

    func selectAircraftType(_ type: AircraftTypeResponse) {
        selectedAircraftType = type
        newAircraftIcaoType = type.icao
        aircraftTypeSuggestions = []
        aircraftFormError = nil
    }

    func createAircraft() async -> Bool {
        guard let icaoType = resolvedNewAircraftIcaoType else {
            aircraftFormError = "Enter a valid ICAO aircraft type, for example C172."
            return false
        }
        aircraftFormError = nil
        let cruiseSpeed = optionalPositiveInt(newAircraftCruiseSpeedKt, fieldName: "Cruise speed")
        guard aircraftFormError == nil else { return false }
        let ceiling = optionalPositiveInt(newAircraftCeilingFt, fieldName: "Ceiling")
        guard aircraftFormError == nil else { return false }

        isSavingAircraft = true
        defer { isSavingAircraft = false }

        let request = CreateAircraftRequest(
            icaoType: icaoType,
            tailNumber: optionalText(newAircraftTailNumber)?.uppercased(),
            nickname: optionalText(newAircraftNickname),
            isIfr: newAircraftIsIfr,
            isFiki: newAircraftIsFiki,
            cruiseSpeedKt: cruiseSpeed,
            ceilingFt: ceiling,
            isDefault: newAircraftIsDefault
        )

        do {
            let aircraft = try await repository.createAircraft(request)
            aircraftOptions.removeAll { $0.id == aircraft.id }
            aircraftOptions.append(aircraft)
            aircraftOptions = aircraftOptions.sortedForPicker()
            selectedAircraftId = aircraft.id
            prepareNewAircraftForm()
            return true
        } catch {
            aircraftFormError = error.localizedDescription
            Self.logger.error("Create aircraft failed: \(error)")
            return false
        }
    }

    // MARK: - FPL parse

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

    // MARK: - Save

    /// Create the flight on the server. Returns the created flight on success.
    func createFlight() async -> FlightResponse? {
        guard canSubmit else { return nil }

        isSubmitting = true
        errorMessage = nil
        statusMessage = "Creating flight\u{2026}"
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

    /// Save edits, then run the invalidation-aware regeneration when requested.
    /// `regenerate` is set by the view after the user confirms the re-briefing
    /// cost dialog; the server's `invalidation` hint decides how much work is done.
    func saveEditedFlight(regenerate: Bool) async -> FlightResponse? {
        guard canSubmit, let editingFlight else { return nil }

        isSubmitting = true
        errorMessage = nil
        statusMessage = "Saving flight\u{2026}"
        defer {
            isSubmitting = false
            statusMessage = nil
        }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        // `0` is the server's "detach aircraft" sentinel: send it only when the
        // flight had an aircraft and the user cleared the picker.
        let aircraftId = selectedAircraftId ?? (editingFlight.aircraftId == nil ? nil : 0)
        let request = UpdateFlightRequest(
            aircraftId: aircraftId,
            waypoints: waypoints,
            departureTime: formatter.string(from: departureDate),
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightDurationHours: flightDurationHours
        )

        do {
            let response = try await repository.updateFlight(flightId: editingFlight.id, request: request)
            if regenerate, response.invalidation.needsRegeneration {
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

    /// Regenerate the briefing according to the invalidation hint:
    /// `advisoriesOnly` recomputes advisories in place, `refetchNeeded` runs the
    /// full streamed refresh.
    private func regenerateBriefing(for flight: FlightResponse, invalidation: FlightInvalidation) async throws {
        switch invalidation {
        case .none:
            return
        case .advisoriesOnly:
            statusMessage = "Updating advisories\u{2026}"
            do {
                let pack = try await repository.latestPack(flightId: flight.id)
                try await repository.recalculateAdvisories(
                    flightId: flight.id,
                    timestamp: pack.fetchTimestamp,
                    cruiseAltitudeFt: flight.cruiseAltitudeFt
                )
            } catch APIError.notFound {
                // No existing pack to recompute against — fall back to a full refresh.
                try await refreshBriefing(flightId: flight.id)
            }
        case .refetchNeeded:
            try await refreshBriefing(flightId: flight.id)
        }
    }

    private func refreshBriefing(flightId: String) async throws {
        statusMessage = "Regenerating briefing\u{2026}"
        let stream = await repository.refreshStream(flightId: flightId)
        var completed = false
        for try await event in stream {
            switch event.type {
            case "progress":
                statusMessage = event.label ?? event.stage ?? "Regenerating briefing\u{2026}"
            case "briefing_ready":
                statusMessage = "Briefing ready\u{2026}"
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

    // MARK: - Aircraft form helpers

    private var resolvedNewAircraftIcaoType: String? {
        let value = newAircraftIcaoType
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .uppercased()
        if let selectedAircraftType, value == selectedAircraftType.icao {
            return selectedAircraftType.icao
        }
        guard value.range(of: #"^[A-Z0-9]{1,4}$"#, options: .regularExpression) != nil else {
            return nil
        }
        return value
    }

    private func optionalText(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func optionalPositiveInt(_ value: String, fieldName: String) -> Int? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard let intValue = Int(trimmed), intValue > 0 else {
            aircraftFormError = "\(fieldName) must be a positive number."
            return nil
        }
        return intValue
    }
}

private extension [AircraftResponse] {
    func sortedForPicker() -> [AircraftResponse] {
        sorted { lhs, rhs in
            if lhs.isDefault != rhs.isDefault {
                return lhs.isDefault && !rhs.isDefault
            }
            return lhs.pickerTitle.localizedCaseInsensitiveCompare(rhs.pickerTitle) == .orderedAscending
        }
    }
}
