import Foundation
import OSLog

/// View model for creating or editing a flight (one form, two modes — §4.4).
@Observable
@MainActor
final class AddFlightViewModel {
    // Form fields
    var waypointsText: String = ""
    /// TZ-aware departure time. `departureDate` below bridges to its instant so
    /// the rest of the VM keeps working with a plain `Date`.
    let departureTime: DepartureTimeModel
    var cruiseAltitudeFt: Int = 5500
    var flightDurationHours: Double = 2.0
    var selectedAircraftId: Int?

    // MARK: Flexibility (timing scenarios, #357)

    /// Selected Flexibility mode. Seeded from the flight when editing. The view's
    /// picker calls `flexibilityPicked(_:)` on a user change so the explainer
    /// gate never fires from init-time seeding.
    var flexibility: FlexibilityMode = .none
    /// TZ-aware editor for the pinned alternate departure, shown only for
    /// `.alternate` mode (net-new on iOS). Shares the route's timezone options
    /// with the primary departure picker.
    let altDepartureTime: DepartureTimeModel
    /// Durable "has this pilot ever run a timing scan?" flag from `/usage` —
    /// gates the first-time explainer. Defaults to `false` so we err toward
    /// gently informing when it hasn't loaded.
    private(set) var timeScanUsed = false
    /// The view observes this to present the explainer sheet, then clears it.
    var showFlexibilityExplainer = false

    /// Session-scoped ack: once the explainer has fired (or we've learned the
    /// pilot already uses the feature) this app run, subsequent mode toggles and
    /// re-opened editors don't re-fire it. The durable `timeScanUsed` flag still
    /// governs across app launches. Mirrors the web `sessionStorage` ack.
    @MainActor private static var explainerAckedThisSession = false

    /// Flexibility options for the picker. `.alternate` needs an alt time set via
    /// PATCH, so it is offered only when editing (the server rejects it on create).
    var flexibilityOptions: [FlexibilityMode] {
        isEditing ? FlexibilityMode.allCases : FlexibilityMode.allCases.filter { $0 != .alternate }
    }

    /// Absolute departure instant — the single source of truth lives in
    /// `departureTime`; this is the bridge the existing create/edit code uses.
    var departureDate: Date {
        get { departureTime.instant }
        set { departureTime.instant = newValue }
    }

    // Route interpretation (#5) — also feeds the timezone dropdown (#4).
    private(set) var routeInterpretation: InterpretRouteResponse?
    private(set) var isInterpreting: Bool = false

    // Flight profile picker
    private(set) var profileOptions: [ProfileResponse] = []
    private(set) var isLoadingProfiles: Bool = false
    var selectedProfileId: Int?

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
        let defaultInstant = Calendar.current.date(byAdding: .hour, value: 1, to: Date()) ?? Date()
        let departureInstant = flight?.departureDate ?? defaultInstant
        self.departureTime = DepartureTimeModel(instant: departureInstant)
        // Seed the alt-departure editor from the flight's stored alt time when
        // present, else from the primary departure (a sensible starting point).
        let altInstant = flight?.altDepartureTime.flatMap { Date.parseISO8601($0) } ?? departureInstant
        self.altDepartureTime = DepartureTimeModel(instant: altInstant)
        if let flight {
            waypointsText = flight.waypoints.joined(separator: " ")
            cruiseAltitudeFt = flight.cruiseAltitudeFt
            flightDurationHours = flight.flightDurationHours
            selectedAircraftId = flight.aircraftId
            selectedProfileId = flight.profileId
            flexibility = flight.effectiveFlexibility
        }
    }

    var isEditing: Bool { editingFlight != nil }

    var navigationTitle: String { isEditing ? "Edit Flight" : "New Flight" }

    var submitTitle: String { isEditing ? "Save" : "Create" }

    /// Replace the trailing (still-being-typed) token with a chosen ICAO and add
    /// a trailing space so the user can keep typing the next waypoint. Preserves
    /// whatever separator the user was using before the last token.
    func completeLastToken(with icao: String) {
        let separators: Set<Character> = [" ", "-", ","]
        if let lastSep = waypointsText.lastIndex(where: { separators.contains($0) }) {
            let prefix = waypointsText[...lastSep]
            waypointsText = String(prefix) + icao + " "
        } else {
            waypointsText = icao + " "
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
        if selectedProfileId != original.profileId { return true }
        if flexibility != original.effectiveFlexibility { return true }
        // An alternate-time change (in `.alternate` mode) is a real edit too.
        if flexibility == .alternate, altDepartureChanged(from: original) { return true }
        guard let originalDate = original.departureDate else { return true }
        return abs(departureDate.timeIntervalSince(originalDate)) > 1
    }

    /// Whether the edited alt-departure instant differs from the flight's stored
    /// one (or newly sets one). Used only in `.alternate` mode.
    private func altDepartureChanged(from original: FlightResponse) -> Bool {
        guard let originalAlt = original.altDepartureTime.flatMap({ Date.parseISO8601($0) }) else {
            return true   // no stored alt time yet → setting one is a change
        }
        return abs(altDepartureTime.instant.timeIntervalSince(originalAlt)) > 1
    }

    /// Whether the edit changes a forecast-affecting field (route/time/FL/duration).
    /// Aircraft-only edits are excluded — they don't regenerate the briefing, so
    /// they save without the re-briefing confirm (§4.4).
    var hasForecastAffectingChange: Bool {
        guard let original = editingFlight else { return false }
        if waypoints != original.waypoints.map({ $0.uppercased() }) { return true }
        if cruiseAltitudeFt != original.cruiseAltitudeFt { return true }
        if abs(flightDurationHours - original.flightDurationHours) > 0.01 { return true }
        // A profile carries model/method choices, so changing it can change the
        // forecast — treat it like a forecast-affecting field (rebrief confirm).
        if selectedProfileId != original.profileId { return true }
        guard let originalDate = original.departureDate else { return true }
        return abs(departureDate.timeIntervalSince(originalDate)) > 1
    }

    // MARK: - Route interpretation + timezone resolution

    /// Whether the latest interpretation dropped any tokens (skipped / off-route),
    /// so the save flow should confirm before committing (mirrors the web).
    var routeHasDroppedTokens: Bool {
        guard let interpretation = routeInterpretation else { return false }
        return !interpretation.isClean
    }

    /// Resolve the typed route on the server: validates/normalises waypoints,
    /// returns what was understood / skipped / off-route, and per-waypoint
    /// timezones. Feeds both the interpret popup (#5) and the TZ dropdown (#4).
    /// Debounced by the caller; non-fatal on failure (e.g. a half-typed route).
    func resolveRoute() async {
        let route = waypointsText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard waypoints.count >= 2 else {
            routeInterpretation = nil
            return
        }
        isInterpreting = true
        defer { isInterpreting = false }
        do {
            let result = try await repository.interpretRoute(rawRoute: route)
            routeInterpretation = result
            applyRouteTimeZones(from: result.waypoints)
        } catch {
            // Half-typed or out-of-coverage route — keep the field usable, just
            // don't offer interpretation/timezones yet.
            Self.logger.debug("Route interpret unavailable: \(error)")
        }
    }

    /// Populate the timezone dropdown from resolved waypoints and default the
    /// display zone to the departure airport's timezone (first waypoint).
    private func applyRouteTimeZones(from waypoints: [RouteWaypointInfo]) {
        let zones = waypoints.compactMap(\.timezone)
        departureTime.setRouteTimeZones(zones, preferred: waypoints.first?.timezone)
        // The alt-departure picker shares the same route timezone options.
        altDepartureTime.setRouteTimeZones(zones, preferred: waypoints.first?.timezone)
    }

    // MARK: - Flexibility explainer gate (#357)

    /// Load the durable timing-scan usage flag that gates the first-time
    /// explainer. Non-fatal on failure — we keep `timeScanUsed = false` so the
    /// explainer still shows (erring toward informing).
    func loadUsage() async {
        do {
            timeScanUsed = try await repository.usageSummary().timeScanUsed
        } catch {
            Self.logger.debug("Usage summary unavailable: \(error)")
        }
    }

    /// Called by the view when the pilot changes the Flexibility picker. Fires
    /// the first-time explainer gate on any non-`none` selection.
    func flexibilityPicked(_ mode: FlexibilityMode) {
        guard mode != .none else { return }
        Task { await maybeShowFlexibilityExplainer() }
    }

    /// Resolve the first-time explainer gate exactly once per session. Set the
    /// session ack up front (before any await) so rapid re-entry short-circuits;
    /// then show the sheet only when the pilot has never run a scan.
    private func maybeShowFlexibilityExplainer() async {
        guard !Self.explainerAckedThisSession else { return }
        Self.explainerAckedThisSession = true
        if timeScanUsed { return }   // already loaded and used → never show
        // The gate may fire before `/usage` has loaded; fetch it now so an
        // established user isn't shown the first-time modal.
        await loadUsage()
        if !timeScanUsed {
            showFlexibilityExplainer = true
        }
    }

    // MARK: - Autorouter import + recent routes

    private(set) var autorouterRoutes: [AutorouterRoute] = []
    private(set) var isLoadingAutorouter: Bool = false
    /// User-facing message when Autorouter import can't proceed (not linked, empty,
    /// or unreachable). Nil when routes loaded successfully.
    var autorouterError: String?

    /// Recently-flown routes, derived client-side from the flight list (most recent
    /// distinct waypoint sequences) — same source as the web's recent-route dropdown.
    private(set) var recentRoutes: [[String]] = []

    func loadAutorouterRoutes() async {
        isLoadingAutorouter = true
        autorouterError = nil
        defer { isLoadingAutorouter = false }
        do {
            autorouterRoutes = try await repository.autorouterRoutes(limit: 25)
            if autorouterRoutes.isEmpty {
                autorouterError = "No recent routes found in your Autorouter account."
            }
        } catch let APIError.serverError(code, message)
            where code == 409 && (message?.contains("autorouter_not_linked") ?? false) {
            // Specifically the "not linked" 409 — other 409s fall through to the
            // generic handler so we don't mislabel an unrelated conflict.
            autorouterRoutes = []
            autorouterError = "Link your Autorouter account on the web app to import routes here."
        } catch {
            autorouterRoutes = []
            autorouterError = "Could not load Autorouter routes: \(error.localizedDescription)"
            Self.logger.debug("Autorouter routes unavailable: \(error)")
        }
    }

    /// Import a selected Autorouter route by running its ICAO flight plan through
    /// the same parse→fill path as "Paste Flight Plan".
    func importAutorouterRoute(_ route: AutorouterRoute) async {
        fplText = route.fplan
        await parseFpl()
    }

    func loadRecentRoutes() async {
        do {
            let flights = try await repository.flights()
            // `createdAt` is an ISO-8601 string, so a lexicographic sort orders by
            // recency. Keep up to 8 distinct waypoint sequences.
            let sorted = flights.sorted { $0.createdAt > $1.createdAt }
            var seen = Set<String>()
            var result: [[String]] = []
            for flight in sorted {
                let wps = flight.waypoints.map { $0.uppercased() }
                guard wps.count >= 2 else { continue }
                if seen.insert(wps.joined(separator: " ")).inserted {
                    result.append(wps)
                    if result.count >= 8 { break }
                }
            }
            recentRoutes = result
        } catch {
            Self.logger.debug("Recent routes unavailable: \(error)")
        }
    }

    func applyRecentRoute(_ waypoints: [String]) {
        waypointsText = waypoints.joined(separator: " ")
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

    // MARK: - Profile picker

    var selectedProfile: ProfileResponse? {
        guard let selectedProfileId else { return nil }
        return profileOptions.first { $0.id == selectedProfileId }
    }

    func loadProfiles() async {
        guard !isLoadingProfiles else { return }
        isLoadingProfiles = true
        defer { isLoadingProfiles = false }
        do {
            let profiles = try await repository.profiles()
            profileOptions = profiles.sortedForPicker()
            // Ensure the picker always reflects a valid selection. If none is set
            // (new flight, or an older flight saved before profiles existed), fall
            // back to the account default. Apply the preset's altitude only when
            // creating — on edit we must not silently rewrite the flight's values.
            let known = selectedProfileId.flatMap { id in profiles.contains { $0.id == id } } ?? false
            if !known {
                selectedProfileId = profiles.first(where: \.isDefault)?.id ?? profiles.first?.id
                if !isEditing, let id = selectedProfileId { applyProfile(id) }
            }
        } catch {
            // Non-fatal: the form still works without a profile (server uses the
            // account default).
            Self.logger.debug("Profile list unavailable: \(error)")
        }
    }

    /// Apply a selected profile's preset flight parameters to the form. Mirrors
    /// the web: choosing a profile fills cruise altitude (and the server fills
    /// ceiling/speed from the same profile on save).
    func applyProfile(_ id: Int?) {
        selectedProfileId = id
        guard let profile = profileOptions.first(where: { $0.id == id }) else { return }
        if let alt = profile.settings.cruiseAltitudeFt { cruiseAltitudeFt = alt }
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
            aircraftId: selectedAircraftId,
            profileId: selectedProfileId,
            // `.alternate` is edit-only (needs an alt time via PATCH); the create
            // picker never offers it, so only the day modes reach here.
            flexibility: flexibility == .none ? nil : flexibility
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
            flightDurationHours: flightDurationHours,
            profileId: selectedProfileId,
            flexibility: flexibility,
            // Alt time, three cases (the backend only clears `alt_departure_time`
            // on an explicit "" — omitting `nil` is a no-op):
            //  • `.alternate` mode          → send the pinned time.
            //  • *leaving* `.alternate`     → send "" to clear the stale alt.
            //  • already a day/`none` mode  → omit (`nil`). A day-scan flight can
            //    carry a pinned "★ your alternate" set via "Set as alternate"
            //    while its mode stays a day mode, so an unrelated edit (e.g.
            //    cruise altitude) must NOT wipe it by unconditionally sending "".
            altDepartureTime: altDepartureTimePayload
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

    /// The `alt_departure_time` value a PATCH should send — see the three cases
    /// documented at the call site in `saveEditedFlight`. `nil` means "omit the
    /// key" (leave the server's stored value untouched), which is what protects a
    /// day-scan flight's pinned "★ your alternate" from an unrelated edit.
    private var altDepartureTimePayload: String? {
        if flexibility == .alternate {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime]
            return formatter.string(from: altDepartureTime.instant)
        }
        // Only clear when the edit is actually *leaving* `.alternate` mode.
        if editingFlight?.effectiveFlexibility == .alternate { return "" }
        return nil
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
