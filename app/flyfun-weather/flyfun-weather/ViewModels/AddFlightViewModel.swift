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
    /// Highest usable level for the altitude advisory sweep — the web's
    /// `flight_ceiling_ft`. Seeded from the flight when editing, from the
    /// selected profile when creating.
    var flightCeilingFt: Int = 13000
    var flightDurationHours: Double = 2.0
    var selectedAircraftId: Int?

    /// Duration as the hour + quarter-hour pair the pickers edit. Both clients
    /// present the stored decimal hours this way (see `FlightDuration`), so a
    /// 1h15 flight round-trips instead of being coerced to the nearest half hour.
    var durationHours: Int {
        get { FlightDuration.split(flightDurationHours).hours }
        set { flightDurationHours = FlightDuration.combine(hours: newValue, minutes: durationMinutes) }
    }

    var durationMinutes: Int {
        get { FlightDuration.split(flightDurationHours).minutes }
        set { flightDurationHours = FlightDuration.combine(hours: durationHours, minutes: newValue) }
    }

    /// The Field-15 text the pilot actually typed, captured *before*
    /// `applyInterpretedRoute()` normalises the field to the resolved waypoints.
    ///
    /// nil until the pilot edits the route — which is exactly the web's rule for
    /// when `raw_route` may be sent. An untouched route must keep the flight's
    /// stored annotation *and* its `parser_version`: re-sending the stored value
    /// would push the server into its "new raw route" branch and re-stamp the
    /// version to the current euro_aip release, destroying its meaning as a
    /// re-derive marker.
    private(set) var editedRawRoute: String?

    /// The `raw_route` a request may carry — `editedRawRoute`, but only while the
    /// route still differs from the flight's.
    ///
    /// `editedRawRoute` is captured at interpret time and outlives the attempt
    /// that produced it: an interpret that fails, or a confirm sheet the pilot
    /// cancels, leaves it set. Reverting the route and saving something else
    /// would then send a route the pilot backed out of *and* re-stamp
    /// `parser_version` for an unchanged one. Re-checking here also suppresses
    /// the send when interpretation normalises a cosmetic edit (`LFMD DCT LFML`)
    /// straight back to the stored route.
    private var rawRoutePayload: String? {
        routeChangedFromOriginal ? editedRawRoute : nil
    }

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

    /// The fire-and-forget refresh queued by `queueRefresh`, retained ONLY so
    /// tests can await it. Nothing in the app waits on it — that is the whole
    /// point: the editor dismisses while the server keeps working.
    private(set) var pendingRefreshTask: Task<Void, Never>?

    /// How many briefing packs the edited flight already has — the "discards N
    /// briefing(s)" count in the structural note and the Move confirm. Loaded
    /// when the editor opens; stays 0 (the no-packs copy) if it can't be read.
    private(set) var existingPackCount = 0

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
    /// True from the moment the pilot taps Create/Save until the whole submit
    /// flow (interpret → optional confirm sheet → create/save) settles. Unlike
    /// `isSubmitting`, this covers the new pre-submit interpret round trip, whose
    /// `await` would otherwise leave the button tappable long enough for a
    /// double-tap to launch a second, independent create. The view sets it
    /// synchronously in `submit()` (before any suspension) and disables the
    /// button on it, so the second tap is rejected before it can spawn work.
    var isPreparingSubmit: Bool = false
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
            flightCeilingFt = flight.flightCeilingFt
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

    /// Whether the typed route differs from the flight being edited (case- and
    /// spacing-insensitive). Single source for the three change gates below.
    private func routeDiffers(from original: FlightResponse) -> Bool {
        waypoints != original.waypoints.map { $0.uppercased() }
    }

    /// Any edited field differs from the original (gates the Save button).
    var hasChanges: Bool {
        guard let original = editingFlight else { return true }
        if routeDiffers(from: original) { return true }
        if cruiseAltitudeFt != original.cruiseAltitudeFt { return true }
        if flightCeilingFt != original.flightCeilingFt { return true }
        if abs(flightDurationHours - original.flightDurationHours) > 0.01 { return true }
        if selectedAircraftId != original.aircraftId { return true }
        if selectedProfileId != original.profileId { return true }
        if flexibility != original.effectiveFlexibility { return true }
        // An alternate-time change (in `.alternate` mode) is a real edit too.
        if flexibility == .alternate, altDepartureChanged(from: original) { return true }
        guard let originalDate = original.departureDate else { return true }
        return abs(departureDate.timeIntervalSince(originalDate)) > 1
    }

    /// Whether the typed route differs from the flight being edited (case- and
    /// spacing-insensitive). Gates submit-time interpretation on edit — an
    /// untouched route is already clean, so it needs no round-trip. On create
    /// there is no baseline, so this is always `true`.
    var routeChangedFromOriginal: Bool {
        guard let original = editingFlight else { return true }
        return routeDiffers(from: original)
    }

    /// Whether the edited alt-departure instant differs from the flight's stored
    /// one (or newly sets one). Used only in `.alternate` mode.
    private func altDepartureChanged(from original: FlightResponse) -> Bool {
        guard let originalAlt = original.altDepartureTime.flatMap({ Date.parseISO8601($0) }) else {
            return true   // no stored alt time yet → setting one is a change
        }
        return abs(alignedAltDepartureInstant.timeIntervalSince(originalAlt)) > 1
    }

    // MARK: - Structural change (Move / Duplicate, #552)

    /// Whether the edited departure falls on a **different UTC calendar day**
    /// than the flight's.
    ///
    /// UTC, deliberately: the flight's server-side `target_date` (and therefore
    /// its ID) is derived from the UTC instant, while `DepartureTimeModel`
    /// edits the wall-clock of the *selected* timezone. A pilot on
    /// `Europe/Paris` picking 00:30 local is 22:30 UTC the previous day — a date
    /// change the picker's own calendar day would report as "same day", so PATCH
    /// would 422 and the pilot would be told nothing. Comparing UTC days also
    /// catches the case the web can't express at all: nudging only the *time*
    /// across UTC midnight (the web pins `flight.target_date` and rewrites only
    /// the time-of-day, `flight-main.ts:313`).
    var departureDayChanged: Bool {
        guard let original = editingFlight?.departureDate else { return false }
        return Self.utcDay(of: departureDate) != Self.utcDay(of: original)
    }

    /// Whether the edited route changes the **origin or destination**. A
    /// mid-route waypoint insert/removal is NOT structural — the flight ID only
    /// encodes the endpoints, so PATCH handles it (mirrors the web's
    /// `detectStructuralChange`).
    var routeEndpointsChanged: Bool {
        guard let original = editingFlight, routeDiffers(from: original) else { return false }
        let originalWaypoints = original.waypoints.map { $0.uppercased() }
        guard let oldOrigin = originalWaypoints.first, let oldDest = originalWaypoints.last,
              let newOrigin = waypoints.first, let newDest = waypoints.last else { return false }
        return newOrigin != oldOrigin || newDest != oldDest
    }

    /// True when the edit changes something the flight ID is built from and PATCH
    /// therefore refuses. The Save button branches into the Move / Duplicate
    /// choice instead of issuing a PATCH that would 422.
    var hasStructuralChange: Bool { departureDayChanged || routeEndpointsChanged }

    /// Inline note under the Departure section once the date change is detected.
    /// Web copy verbatim (`flightDetail.dateChangedNote`).
    var departureChangeNote: String? {
        guard departureDayChanged else { return nil }
        return "Date changed. Move replaces this flight (discards \(existingPackCount) existing briefing(s) "
            + "for the old date) \u{2014} or Duplicate keeps both."
    }

    /// Inline note under the Route section. Web copy verbatim
    /// (`flightDetail.routeChangedNote`).
    var routeChangeNote: String? {
        guard routeEndpointsChanged else { return nil }
        return "Origin or destination changed. Move replaces this flight (discards \(existingPackCount) "
            + "existing briefing(s) for the old route) \u{2014} or Duplicate keeps both."
    }

    /// Confirm-dialog body for Move. Web copy verbatim
    /// (`flightDetail.moveConfirmWithPacks` / `\u{2026}NoPacks`).
    var moveConfirmMessage: String {
        existingPackCount > 0
            ? "Move this flight? \(existingPackCount) existing briefing(s) for the old values will be discarded."
            : "Move this flight? The new flight will start with no briefings."
    }

    /// UTC calendar day of an instant — the unit `target_date` is derived in.
    static func utcDay(of date: Date) -> DateComponents {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        return calendar.dateComponents([.year, .month, .day], from: date)
    }

    // MARK: - Alternate departure day binding

    /// The pinned alternate re-anchored onto the departure's UTC day, keeping its
    /// time of day.
    ///
    /// The server requires the two to be on the same day and compares them as
    /// stored — i.e. in UTC (`update_flight`: "Alt departure time must be on the
    /// same day as the primary departure"). The form used to offer a free "Alt
    /// date" picker, so any day but the departure's produced a 422 the pilot had
    /// no way to predict. Binding the day the way the web does (`flight-main.ts`
    /// pins `flight.target_date`) removes the failure instead of reporting it.
    var alignedAltDepartureInstant: Date {
        Self.alignUTCDay(of: altDepartureTime.instant, to: departureDate)
    }

    /// True when the alternate collapses onto the primary departure once bound to
    /// its day — the server's other alternate rule ("must differ from the primary
    /// departure time"). Surfaced inline rather than left to a 422.
    var altDepartureCollidesWithDeparture: Bool {
        flexibility == .alternate && alignedAltDepartureInstant == departureDate
    }

    /// Move `instant` onto `reference`'s UTC calendar day, preserving its UTC
    /// time of day.
    static func alignUTCDay(of instant: Date, to reference: Date) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        let day = calendar.dateComponents([.year, .month, .day], from: reference)
        var components = calendar.dateComponents([.hour, .minute], from: instant)
        components.year = day.year
        components.month = day.month
        components.day = day.day
        components.second = 0
        return calendar.date(from: components) ?? instant
    }

    /// Whether the edit changes a forecast-affecting field (route/time/FL/duration).
    /// Aircraft-only edits are excluded — they don't regenerate the briefing, so
    /// they save without the re-briefing confirm (§4.4).
    var hasForecastAffectingChange: Bool {
        guard let original = editingFlight else { return false }
        if routeDiffers(from: original) { return true }
        if cruiseAltitudeFt != original.cruiseAltitudeFt { return true }
        // The ceiling bounds the altitude-advisory sweep, so it re-grades the
        // briefing exactly as the cruise altitude does.
        if flightCeilingFt != original.flightCeilingFt { return true }
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

    /// Replace the typed route with the server's *understood* waypoints, so the
    /// create request carries the same resolved list the interpret popup showed —
    /// airways (Q230), SIDs (BEBEX7W) and speed/level tokens (WRB/N0174F090) are
    /// dropped rather than sent verbatim. Mirrors the web, which writes the
    /// interpreted route back into the field before saving. Without this the raw
    /// Field-15 tokens are submitted, which both carries garbage waypoints and
    /// can trip the server's 20-waypoint cap on routes that actually resolve to
    /// far fewer. No-op unless the interpretation resolved at least two points.
    func applyInterpretedRoute() {
        guard let interpreted = routeInterpretation?.interpreted, interpreted.count >= 2 else { return }
        waypointsText = interpreted.joined(separator: " ")
    }

    /// How a submit-time route interpretation resolved (mirrors the web's
    /// `interpretAndConfirmRoute` save gate).
    enum RouteSubmitInterpretation: Equatable {
        /// Route resolved cleanly (nothing skipped / off-route). The interpreted
        /// waypoints have already been written back to the field — safe to submit.
        case ready
        /// The resolver dropped tokens the pilot typed. The caller should present
        /// the interpret sheet so the pilot can confirm before submitting.
        case needsConfirmation
        /// Interpretation failed, or resolved to fewer than two usable waypoints.
        /// `errorMessage` is set; the caller must abort rather than submit raw tokens.
        case failed
    }

    /// Interpret the typed route on the server *at submit time* and decide how the
    /// create/save should proceed. Always awaited before the request goes out, so
    /// raw ICAO Field-15 syntax — speed/level groups like `N0180VFR`, airway labels
    /// (`Q230`), SIDs (`BEBEX7W`), `DCT` — is resolved to clean waypoints and never
    /// sent verbatim (the server rejects those as "must be 2-5 alphanumeric").
    ///
    /// This mirrors the web save flow, which awaits `interpretAndConfirmRoute` and
    /// aborts on failure rather than falling back to the raw input. Relying on the
    /// debounced `resolveRoute()` alone is racy: a pilot who pastes a route and taps
    /// Create before the debounce fires (or whose interpret call failed silently)
    /// would otherwise submit the raw tokens.
    func interpretRouteForSubmit() async -> RouteSubmitInterpretation {
        // Clear any banner from a prior failed attempt: only createFlight()/
        // saveEditedFlight() reset it, and the `.needsConfirmation` path reaches
        // neither, so a now-successful interpret would otherwise leave a stale
        // "Couldn't interpret…" error showing behind the confirm sheet.
        errorMessage = nil
        let route = waypointsText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard waypoints.count >= 2 else {
            errorMessage = "Enter at least two waypoints."
            return .failed
        }
        // Capture the pilot's Field-15 text *before* `applyInterpretedRoute()`
        // rewrites the field to the resolved waypoints, and only when they
        // actually edited it — that is what makes it safe to send as `raw_route`.
        if routeChangedFromOriginal { editedRawRoute = route }
        isInterpreting = true
        defer { isInterpreting = false }
        do {
            let result = try await repository.interpretRoute(rawRoute: route)
            routeInterpretation = result
            applyRouteTimeZones(from: result.waypoints)
            guard result.interpreted.count >= 2 else {
                errorMessage = "Couldn't resolve a route from \u{201C}\(route)\u{201D}. Check the waypoints and try again."
                return .failed
            }
            if result.isClean {
                applyInterpretedRoute()
                return .ready
            }
            return .needsConfirmation
        } catch {
            errorMessage = "Couldn't interpret the route: \(error.localizedDescription)"
            Self.logger.error("Route interpret failed at submit: \(error)")
            return .failed
        }
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

    /// Load the edited flight's pack count for the Move copy. Non-fatal: the
    /// count only sharpens the wording, so a failure just leaves the no-packs
    /// variant rather than blocking the editor.
    func loadPackCount() async {
        guard let editingFlight else { return }
        do {
            existingPackCount = try await repository.packs(flightId: editingFlight.id).count
        } catch {
            Self.logger.debug("Pack count unavailable: \(error)")
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
        // The ceiling too: the form now always sends `flight_ceiling_ft`, and the
        // server only fills it from the profile when the request omits it. Without
        // this, picking a profile would apply its altitude but silently keep the
        // old ceiling.
        if let ceiling = profile.settings.flightCeilingFt { flightCeilingFt = ceiling }
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

        let request = CreateFlightRequest(
            waypoints: waypoints,
            departureTime: Self.iso8601(departureDate),
            rawRoute: rawRoutePayload,
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightCeilingFt: flightCeilingFt,
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
            errorMessage = Self.submitErrorMessage(error)
            Self.logger.error("Create flight failed: \(error)")
            return nil
        }
    }

    // MARK: - Move / Duplicate (structural edits, #552)

    /// Replace the flight with one carrying the edited structural values.
    ///
    /// The old flight (and every briefing it accumulated) is discarded
    /// server-side in the same transaction, which is why the view confirms
    /// first. The move request carries only what its body accepts — see
    /// `applyResidualEdits(to:)` for the rest.
    func moveFlight() async -> FlightResponse? {
        guard canSubmit, let editingFlight else { return nil }

        isSubmitting = true
        errorMessage = nil
        statusMessage = "Moving flight\u{2026}"
        defer {
            isSubmitting = false
            statusMessage = nil
        }

        let request = MoveFlightRequest(
            departureTime: Self.iso8601(departureDate),
            waypoints: waypoints,
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightCeilingFt: flightCeilingFt,
            flightDurationHours: flightDurationHours,
            // Only when the pilot actually retyped the route: an untouched route
            // must keep the source flight's stored Field-15 annotation and its
            // `parser_version` re-derive marker (mirrors the web `moveBtn`).
            rawRoute: rawRoutePayload
        )

        do {
            let moved = try await repository.moveFlight(flightId: editingFlight.id, request: request)
            Self.logger.info("Moved flight \(editingFlight.id) \u{2192} \(moved.id)")
            return await applyResidualEdits(to: moved)
        } catch {
            errorMessage = Self.submitErrorMessage(error)
            Self.logger.error("Move flight failed: \(error)")
            return nil
        }
    }

    /// Create a *second* flight from the edited values, leaving the original (and
    /// its briefings) untouched — the non-destructive half of the structural
    /// choice. Reuses `createFlight` with merged values, like the web's
    /// Duplicate button.
    func duplicateFlight() async -> FlightResponse? {
        guard canSubmit, let editingFlight else { return nil }

        isSubmitting = true
        errorMessage = nil
        statusMessage = "Duplicating flight\u{2026}"
        defer {
            isSubmitting = false
            statusMessage = nil
        }

        let request = CreateFlightRequest(
            waypoints: waypoints,
            departureTime: Self.iso8601(departureDate),
            rawRoute: rawRoutePayload,
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightCeilingFt: flightCeilingFt,
            flightDurationHours: flightDurationHours,
            aircraftId: selectedAircraftId,
            profileId: selectedProfileId,
            // The create endpoint rejects `.alternate` (it needs an alt time,
            // which only PATCH can set), so the copy starts without it; the day
            // modes carry over unchanged.
            flexibility: (flexibility == .none || flexibility == .alternate) ? nil : flexibility
        )

        do {
            let created = try await repository.createFlight(request)
            Self.logger.info("Duplicated flight \(editingFlight.id) \u{2192} \(created.id)")
            return created
        } catch {
            errorMessage = Self.submitErrorMessage(error)
            Self.logger.error("Duplicate flight failed: \(error)")
            return nil
        }
    }

    /// Apply the edits `POST /move` has no request field for.
    ///
    /// `MoveFlightRequest` carries only the structural + numeric fields; the
    /// aircraft, the profile and Flexibility are inherited from the source
    /// flight. iOS shows a single **Save** button (the web swaps in separate
    /// Move/Duplicate buttons), so one edit can legitimately change the date
    /// *and* the aircraft — without this follow-up PATCH half of it would vanish
    /// with no message. Best-effort: the move itself already succeeded and the
    /// pilot is about to land on the new flight, so a failure here is logged
    /// rather than discarding a completed move.
    private func applyResidualEdits(to moved: FlightResponse) async -> FlightResponse {
        guard let original = editingFlight else { return moved }
        var request = UpdateFlightRequest()
        var hasResidual = false

        if selectedAircraftId != original.aircraftId {
            // `0` is the server's "detach aircraft" sentinel.
            request.aircraftId = selectedAircraftId ?? 0
            hasResidual = true
        }
        if let selectedProfileId, selectedProfileId != original.profileId {
            request.profileId = selectedProfileId
            hasResidual = true
        }
        // Send an alt time only when the mode changed (entering `.alternate`
        // needs one, leaving it clears the stale value) or the pilot edited it —
        // otherwise the server's own carry-over stays authoritative.
        if flexibility != original.effectiveFlexibility {
            request.flexibility = flexibility
            request.altDepartureTime = altDepartureTimePayload
            hasResidual = true
        } else if flexibility == .alternate, altDepartureChanged(from: original) {
            request.altDepartureTime = altDepartureTimePayload
            hasResidual = true
        }

        guard hasResidual else { return moved }
        do {
            return try await repository.updateFlight(flightId: moved.id, request: request).flight
        } catch {
            Self.logger.error("Post-move residual edit failed for \(moved.id): \(error)")
            return moved
        }
    }

    /// Pilot-facing copy for a create/move/duplicate failure.
    ///
    /// `APIClient` already decodes the server's `detail`, so the 422s (bad
    /// waypoints, the 180-day booking cap) read well verbatim. The two that
    /// don't are rewritten here: a 403 whose detail talks about admins, and the
    /// bare 409 that only names the colliding ID.
    static func submitErrorMessage(_ error: Error) -> String {
        switch error {
        case APIError.forbidden(let detail) where detail.localizedCaseInsensitiveContains("past"):
            return "That departure is already in the past. Pick a future date and time."
        case APIError.serverError(409, _):
            return "You already have a flight on that date for this route. "
                + "Pick a different time, or edit the existing flight."
        case APIError.serverError(_, let detail?) where !detail.isEmpty:
            return detail
        default:
            return error.localizedDescription
        }
    }

    /// ISO-8601 with an explicit offset. Both `create` and `move` reject a naive
    /// datetime server-side, so every departure string the app sends is built here.
    static func iso8601(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
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

        // `0` is the server's "detach aircraft" sentinel: send it only when the
        // flight had an aircraft and the user cleared the picker.
        let aircraftId = selectedAircraftId ?? (editingFlight.aircraftId == nil ? nil : 0)
        let request = UpdateFlightRequest(
            aircraftId: aircraftId,
            waypoints: waypoints,
            // Only what the pilot actually typed. Omitted on an untouched route,
            // which the server reads as "still valid, leave it alone"; on an
            // edited route omitting it instead would CLEAR the flight's Field-15
            // annotation, which is what every iOS route edit used to do.
            rawRoute: rawRoutePayload,
            departureTime: Self.iso8601(departureDate),
            cruiseAltitudeFt: cruiseAltitudeFt,
            flightCeilingFt: flightCeilingFt,
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
            errorMessage = Self.submitErrorMessage(error)
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
            // Bound to the departure's UTC day — see `alignedAltDepartureInstant`.
            return Self.iso8601(alignedAltDepartureInstant)
        }
        // Only clear when the edit is actually *leaving* `.alternate` mode.
        if editingFlight?.effectiveFlexibility == .alternate { return "" }
        return nil
    }

    /// Kick off the regeneration an edit made necessary — **without** holding the
    /// editor open for it.
    ///
    /// `advisoriesOnly` is a fast in-place recompute, so it is awaited; the pilot
    /// is back on the list within a second either way. `refetchNeeded` is the
    /// ~2-minute pipeline: it is *queued* (202) and deliberately not watched, so
    /// the sheet dismisses immediately. Awaiting the SSE stream here was the
    /// second half of #544 — "Regenerating briefing…" sitting on the Edit form
    /// for two minutes reads exactly like "Save didn't exit".
    ///
    /// Dropping the stream costs nothing: the pipeline runs in a server-side
    /// executor independent of it (`api/packs.py`,
    /// `loop.run_in_executor(_refresh_executor, run_pipeline)`), the flight list
    /// polls `/api/refresh/active` every 5 s and repaints on completion, and an
    /// APNs push is the second signal.
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
                queueRefresh(flightId: flight.id)
            }
        case .refetchNeeded:
            queueRefresh(flightId: flight.id)
        }
    }

    /// Queue a full refresh from a task the dismissed view doesn't own.
    ///
    /// The `Task` is unstructured (not a `.task` modifier), so it is not
    /// cancelled when the sheet goes away; it captures only the repository, not
    /// `self`. Failures are logged rather than surfaced — by the time this runs
    /// the editor is gone, and the flight list's active-refresh poll plus the
    /// pilot's own Refresh button are the recovery path.
    private func queueRefresh(flightId: String) {
        pendingRefreshTask = Task { [repository] in
            for attempt in 0..<Self.queueRefreshAttempts {
                do {
                    try await repository.triggerRefresh(flightId: flightId)
                    return
                } catch APIError.serverError(409, _) {
                    // A refresh was already running when the edit landed. It is
                    // computing the OLD parameters, so letting it stand would
                    // leave the pilot looking at a briefing for values they just
                    // changed. Wait for the registry to clear and re-queue. The
                    // params-hash gate would eventually self-heal this, but only
                    // on the pilot's next manual press.
                    Self.logger.info(
                        "Refresh already in progress for \(flightId) — retrying (\(attempt + 1))"
                    )
                    try? await Task.sleep(for: Self.queueRefreshRetryDelay)
                } catch {
                    Self.logger.error("Queueing refresh after edit failed for \(flightId): \(error)")
                    return
                }
            }
            Self.logger.error(
                "Gave up queueing a post-edit refresh for \(flightId): still in progress"
            )
        }
    }

    /// How many times `queueRefresh` re-tries a 409, and how long it waits
    /// between attempts. Sized to outlast a typical pipeline run (~2 min) without
    /// spinning: the pilot's own Refresh button and the params-hash gate are the
    /// backstop if it never clears.
    ///
    /// The delay is a `var` purely so tests don't sleep for half a minute —
    /// MainActor-isolated like `explainerAckedThisSession`, and only the retry
    /// test writes it.
    private static let queueRefreshAttempts = 6
    @MainActor static var queueRefreshRetryDelay: Duration = .seconds(30)

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
