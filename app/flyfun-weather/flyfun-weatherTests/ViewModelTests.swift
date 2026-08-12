//
//  ViewModelTests.swift
//  flyfun-weatherTests
//
//  Tier B (#314) — ViewModel logic via the injected `MockBriefingRepository`.
//  These cover the highest-risk-of-user-facing-bug logic: form validation,
//  change detection, and the list state machine — all without a network.
//

import Testing
import Foundation
import MapKit
@testable import flyfun_weather

// MARK: - AddFlightViewModel (form validation + change detection)

@MainActor
@Suite struct AddFlightViewModelTests {

    private func makeVM(flight: FlightResponse? = nil) -> AddFlightViewModel {
        AddFlightViewModel(repository: MockBriefingRepository(), flight: flight)
    }

    @Test func waypointsParseUppercaseSplitOnSpaceDashComma() {
        let vm = makeVM()
        vm.waypointsText = "lfmd-lfml, lfat  egtf"
        #expect(vm.waypoints == ["LFMD", "LFML", "LFAT", "EGTF"])
    }

    // #358 round-3 fix: editing an unrelated field on a day-scan flight that
    // carries a pinned "★ your alternate" must NOT clear that alt time. The PATCH
    // omits `altDepartureTime` (nil) rather than sending "".
    @Test func editingDayScanFlightPreservesPinnedAlternate() async throws {
        let flight = makeFlight(cruiseAltitudeFt: 8000, flexibility: .sameDay,
                                altDepartureTime: "2026-06-24T15:00:00Z")
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight))
        let vm = AddFlightViewModel(repository: repo, flight: flight)
        vm.cruiseAltitudeFt = 9000            // unrelated edit
        #expect(vm.canSubmit)
        _ = await vm.saveEditedFlight(regenerate: false)
        let req = try #require(repo.lastUpdateRequest)
        #expect(req.altDepartureTime == nil) // omitted → server keeps the pin
        #expect(req.flexibility == .sameDay)
    }

    // Leaving `.alternate` mode still clears the stored alt time (explicit "").
    @Test func leavingAlternateModeClearsAltTime() async throws {
        let flight = makeFlight(flexibility: .alternate,
                                altDepartureTime: "2026-06-24T15:00:00Z")
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight))
        let vm = AddFlightViewModel(repository: repo, flight: flight)
        vm.flexibility = .none                // switch away from alternate
        #expect(vm.canSubmit)
        _ = await vm.saveEditedFlight(regenerate: false)
        let req = try #require(repo.lastUpdateRequest)
        #expect(req.altDepartureTime == "")   // explicit clear
    }

    @Test func waypointsDropsEmptyTokens() {
        let vm = makeVM()
        vm.waypointsText = "  ,, lfmd  -  lfml , "
        #expect(vm.waypoints == ["LFMD", "LFML"])
    }

    @Test func canSubmitNeedsAtLeastTwoWaypointsWhenCreating() {
        let vm = makeVM()
        vm.waypointsText = "LFMD"
        #expect(vm.canSubmit == false)
        vm.waypointsText = "LFMD LFML"
        #expect(vm.canSubmit == true)
        vm.isSubmitting = true            // in-flight submit blocks re-submit
        #expect(vm.canSubmit == false)
    }

    @Test func canSubmitWhenEditingRequiresAChange() {
        let vm = makeVM(flight: makeFlight())
        // Loaded from the flight verbatim → no changes → cannot save.
        #expect(vm.isEditing)
        #expect(vm.canSubmit == false)
        vm.cruiseAltitudeFt += 1000
        #expect(vm.canSubmit == true)
    }

    @Test func hasChangesDetectsEachEditedField() {
        // Route
        var vm = makeVM(flight: makeFlight(waypoints: ["LFMD", "LFML"]))
        vm.waypointsText = "LFMD LFAT"
        #expect(vm.hasChanges)
        // Altitude
        vm = makeVM(flight: makeFlight(cruiseAltitudeFt: 8000))
        vm.cruiseAltitudeFt = 6000
        #expect(vm.hasChanges)
        // Duration
        vm = makeVM(flight: makeFlight(flightDurationHours: 2.0))
        vm.flightDurationHours = 3.0
        #expect(vm.hasChanges)
        // Aircraft
        vm = makeVM(flight: makeFlight(aircraftId: nil))
        vm.selectedAircraftId = 5
        #expect(vm.hasChanges)
        // Unchanged
        vm = makeVM(flight: makeFlight())
        #expect(vm.hasChanges == false)
    }

    @Test func aircraftOnlyEditIsNotForecastAffecting() {
        let vm = makeVM(flight: makeFlight(aircraftId: nil))
        vm.selectedAircraftId = 7
        #expect(vm.hasChanges)                       // it IS a change…
        #expect(vm.hasForecastAffectingChange == false)  // …but doesn't re-brief
        // A route change, by contrast, is forecast-affecting.
        vm.waypointsText = "LFMD LFAT"
        #expect(vm.hasForecastAffectingChange)
    }

    // MARK: Structural change detection (Move / Duplicate, #552)

    /// A time-only nudge that stays inside the same UTC day is an ordinary
    /// forecast-affecting edit — PATCH handles it, no Move/Duplicate prompt.
    @Test func timeOnlyEditInsideSameUtcDayIsNotStructural() {
        let vm = makeVM(flight: makeFlight(departureTime: "2026-06-24T12:00:00Z"))
        vm.departureTime.timeZoneId = "UTC"
        vm.departureTime.setHour(14)
        #expect(vm.hasChanges)
        #expect(vm.hasForecastAffectingChange)
        #expect(vm.hasStructuralChange == false)
    }

    /// The case the picker's own calendar day cannot see: a pilot on
    /// `Europe/Paris` nudges the *time* to 00:30 local, which is 22:30 UTC the
    /// day before. The local day never moved, but `target_date` did — so PATCH
    /// would 422 and this must route to Move/Duplicate.
    @Test func localTimeEditCrossingUtcMidnightIsStructural() {
        let vm = makeVM(flight: makeFlight(departureTime: "2026-06-24T01:00:00Z"))
        vm.departureTime.timeZoneId = "Europe/Paris"   // UTC+2 in June
        let localDayBefore = vm.departureTime.dateProxy
        vm.departureTime.setHour(0)
        vm.departureTime.setMinute(30)
        // The picker still shows the same local calendar day…
        #expect(Calendar.current.isDate(vm.departureTime.dateProxy,
                                        inSameDayAs: localDayBefore))
        // …but the UTC day it derives from moved back one.
        #expect(vm.departureDayChanged)
        #expect(vm.hasStructuralChange)
    }

    @Test func departureDateChangeIsStructural() {
        let vm = makeVM(flight: makeFlight(departureTime: "2026-06-24T12:00:00Z"))
        vm.departureDate = vm.departureDate.addingTimeInterval(24 * 3600)
        #expect(vm.departureDayChanged)
        #expect(vm.hasStructuralChange)
    }

    @Test func originOrDestinationChangeIsStructural() {
        var vm = makeVM(flight: makeFlight(waypoints: ["LFMD", "LFML"]))
        vm.waypointsText = "LFAT LFML"          // new origin
        #expect(vm.routeEndpointsChanged)
        #expect(vm.hasStructuralChange)

        vm = makeVM(flight: makeFlight(waypoints: ["LFMD", "LFML"]))
        vm.waypointsText = "LFMD LFAT"          // new destination
        #expect(vm.routeEndpointsChanged)
        #expect(vm.hasStructuralChange)
    }

    /// A mid-route waypoint insert keeps both endpoints, so the flight ID is
    /// unchanged and PATCH accepts it — it must NOT prompt for Move/Duplicate.
    @Test func midRouteWaypointInsertIsNotStructural() {
        let vm = makeVM(flight: makeFlight(waypoints: ["LFMD", "LFML"]))
        vm.waypointsText = "LFMD LFAT LFML"
        #expect(vm.hasChanges)
        #expect(vm.hasForecastAffectingChange)
        #expect(vm.routeEndpointsChanged == false)
        #expect(vm.hasStructuralChange == false)
    }

    /// The three-way branch the Save button takes: structural wins over the
    /// re-brief confirm, which wins over a silent save.
    @Test func saveBranchesStructuralThenRebriefThenSilent() {
        // Aircraft-only → silent save.
        var vm = makeVM(flight: makeFlight(aircraftId: nil))
        vm.selectedAircraftId = 7
        #expect(vm.hasStructuralChange == false)
        #expect(vm.hasForecastAffectingChange == false)
        // Altitude → re-brief confirm, still not structural.
        vm = makeVM(flight: makeFlight(cruiseAltitudeFt: 8000))
        vm.cruiseAltitudeFt = 9000
        #expect(vm.hasStructuralChange == false)
        #expect(vm.hasForecastAffectingChange)
        // Date → structural, even though it is also forecast-affecting.
        vm = makeVM(flight: makeFlight(departureTime: "2026-06-24T12:00:00Z"))
        vm.departureDate = vm.departureDate.addingTimeInterval(48 * 3600)
        #expect(vm.hasStructuralChange)
    }

    // MARK: Move / Duplicate submission

    @Test func moveRequestEncodesIso8601WithOffset() async throws {
        let repo = MockBriefingRepository()
        repo.moveFlightResult = .success(makeFlight(id: "flt-2"))
        let vm = AddFlightViewModel(
            repository: repo,
            flight: makeFlight(departureTime: "2026-06-24T12:00:00Z")
        )
        vm.departureDate = vm.departureDate.addingTimeInterval(24 * 3600)

        let moved = await vm.moveFlight()
        #expect(moved?.id == "flt-2")
        #expect(repo.lastMovedFlightId == "flt-1")

        let request = try #require(repo.lastMoveRequest)
        let encoded = try JSONEncoder.weatherBrief.encode(request)
        let json = try #require(
            try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        )
        let departure = try #require(json["departure_time"] as? String)
        #expect(departure == "2026-06-25T12:00:00Z")
        // The server rejects a naive datetime, so the offset must survive encoding.
        #expect(ISO8601DateFormatter().date(from: departure) != nil)
        #expect(json["waypoints"] as? [String] == ["LFMD", "LFML"])
        // Route untouched → no raw_route, so the server keeps the stored one.
        #expect(json["raw_route"] == nil)
    }

    /// Duplicate keeps the original: it goes through `createFlight` with the
    /// edited values and never touches move/delete.
    @Test func duplicateCreatesASecondFlight() async throws {
        let repo = MockBriefingRepository()
        repo.createFlightResult = .success(makeFlight(id: "flt-copy"))
        let vm = AddFlightViewModel(
            repository: repo,
            flight: makeFlight(departureTime: "2026-06-24T12:00:00Z", cruiseAltitudeFt: 8000)
        )
        vm.departureDate = vm.departureDate.addingTimeInterval(24 * 3600)
        vm.cruiseAltitudeFt = 9000

        let copy = await vm.duplicateFlight()
        #expect(copy?.id == "flt-copy")
        let request = try #require(repo.lastCreateRequest)
        #expect(request.departureTime == "2026-06-25T12:00:00Z")
        #expect(request.cruiseAltitudeFt == 9000)
        #expect(repo.lastMoveRequest == nil)
        #expect(repo.deletedFlightIds.isEmpty)
    }

    /// A 409 from the move endpoint reads as pilot-facing copy, not "Server error 409".
    @Test func moveCollisionSurfacesHumanCopy() async {
        let repo = MockBriefingRepository()
        repo.moveFlightResult = .failure(APIError.serverError(409, "A flight with ID 'x' already exists."))
        let vm = AddFlightViewModel(
            repository: repo,
            flight: makeFlight(departureTime: "2026-06-24T12:00:00Z")
        )
        vm.departureDate = vm.departureDate.addingTimeInterval(24 * 3600)

        #expect(await vm.moveFlight() == nil)
        #expect(vm.errorMessage?.contains("already have a flight on that date") == true)
    }

    /// The server's decoded `detail` reaches the pilot verbatim for the 422s that
    /// already read well (booking cap, rejected waypoints).
    @Test func serverDetailIsSurfacedVerbatim() {
        let capped = APIError.serverError(422, "That date is more than 180 days out\u{2026}")
        #expect(AddFlightViewModel.submitErrorMessage(capped) == "That date is more than 180 days out\u{2026}")
        let past = APIError.forbidden("Only admins can move a flight into the past")
        #expect(AddFlightViewModel.submitErrorMessage(past).contains("already in the past"))
    }

    // MARK: Dismiss-first regeneration

    /// The regression behind the second half of #544: a `refetch_needed` edit must
    /// *queue* the pipeline and return, not sit on the SSE stream for two minutes.
    @Test func refetchNeededEditQueuesRefreshInsteadOfStreaming() async throws {
        let flight = makeFlight()
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight,
                                                                  invalidation: .refetchNeeded))
        let vm = AddFlightViewModel(repository: repo, flight: flight)
        vm.flightDurationHours = 3.0

        let saved = await vm.saveEditedFlight(regenerate: true)
        #expect(saved?.id == flight.id)
        // `saveEditedFlight` returned without waiting; the queueing task is the
        // only thing still outstanding.
        await vm.pendingRefreshTask?.value
        #expect(repo.triggeredRefreshIds == [flight.id])
    }

    // MARK: Form parity with the web (#552 phase 4)

    /// Quarter-hour granularity, matching `web/ts/utils/duration.ts`: a 1h15
    /// flight must round-trip, and a still-air estimate rounds **up** so we never
    /// advertise a window shorter than the computed time.
    @Test func durationSplitsAndCombinesOnQuarterHours() {
        #expect(FlightDuration.split(1.25) == FlightDuration.Parts(hours: 1, minutes: 15))
        #expect(FlightDuration.split(0.75) == FlightDuration.Parts(hours: 0, minutes: 45))
        // 1h02 rounds up to 1h15, never down to 1h00.
        #expect(FlightDuration.split(62.0 / 60) == FlightDuration.Parts(hours: 1, minutes: 15))
        // Clamped to the 12h45 picker ceiling; non-positive input is 0h00.
        #expect(FlightDuration.split(99) == FlightDuration.Parts(hours: 12, minutes: 45))
        #expect(FlightDuration.split(-1) == FlightDuration.Parts(hours: 0, minutes: 0))
        #expect(FlightDuration.combine(hours: 1, minutes: 45) == 1.75)
        #expect(FlightDuration.label(1.25) == "1h15")
        #expect(FlightDuration.label(2.0) == "2h")
    }

    @Test func durationPickerBindingsPreserveTheOtherComponent() {
        let vm = makeVM(flight: makeFlight(flightDurationHours: 2.0))
        vm.durationMinutes = 15
        #expect(vm.flightDurationHours == 2.25)
        vm.durationHours = 3
        #expect(vm.flightDurationHours == 3.25)
        #expect(vm.hasChanges)
    }

    @Test func ceilingIsEditableAndCountsAsAForecastAffectingChange() async throws {
        let flight = makeFlight()                     // flightCeilingFt: 13000
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight))
        let vm = AddFlightViewModel(repository: repo, flight: flight)
        #expect(vm.flightCeilingFt == 13000)
        vm.flightCeilingFt = 16000
        #expect(vm.hasChanges)
        #expect(vm.hasForecastAffectingChange)

        _ = await vm.saveEditedFlight(regenerate: false)
        #expect(repo.lastUpdateRequest?.flightCeilingFt == 16000)
    }

    /// An untouched route must not carry `raw_route`: the server reads its
    /// presence as "here is a fresh Field-15 string" and re-stamps
    /// `parser_version` to the current euro_aip release, destroying the marker.
    @Test func untouchedRouteSendsNoRawRoute() async throws {
        let flight = makeFlight(waypoints: ["LFMD", "LFML"])
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight))
        repo.interpretRouteResult = .success(makeInterpretResponse(interpreted: ["LFMD", "LFML"]))
        let vm = AddFlightViewModel(repository: repo, flight: flight)
        vm.cruiseAltitudeFt = 9000                    // unrelated edit

        #expect(await vm.interpretRouteForSubmit() == .ready)
        _ = await vm.saveEditedFlight(regenerate: false)
        #expect(repo.lastUpdateRequest?.rawRoute == nil)
    }

    /// An edited route sends what the pilot *typed*, captured before the
    /// interpretation rewrites the field to the resolved waypoints. Omitting it
    /// on a changed route makes the server clear the annotation instead — which
    /// is what every iOS route edit used to do.
    @Test func editedRouteSendsTheTypedFieldFifteenText() async throws {
        let flight = makeFlight(waypoints: ["LFMD", "LFML"])
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight))
        repo.interpretRouteResult = .success(makeInterpretResponse(interpreted: ["LFMD", "LFAT", "LFML"]))
        let vm = AddFlightViewModel(repository: repo, flight: flight)
        vm.waypointsText = "LFMD DCT LFAT DCT LFML"

        #expect(await vm.interpretRouteForSubmit() == .ready)
        // The field is now the resolved route…
        #expect(vm.waypointsText == "LFMD LFAT LFML")
        // …but the captured raw route is still what the pilot typed.
        #expect(vm.editedRawRoute == "LFMD DCT LFAT DCT LFML")

        _ = await vm.saveEditedFlight(regenerate: false)
        #expect(repo.lastUpdateRequest?.rawRoute == "LFMD DCT LFAT DCT LFML")
    }

    /// `editedRawRoute` outlives the attempt that captured it (a failed interpret,
    /// or a confirm sheet the pilot cancels), so the payload must re-check that
    /// the route still differs. Otherwise a revert-then-save-something-else sends
    /// a route the pilot backed out of AND re-stamps `parser_version` for an
    /// unchanged one.
    @Test func revertedRouteDoesNotSendAStaleRawRoute() async throws {
        let flight = makeFlight(waypoints: ["LFMD", "LFML"])
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight))
        repo.interpretRouteResult = .failure(APIError.serverError(500, "interpret down"))
        let vm = AddFlightViewModel(repository: repo, flight: flight)

        // First attempt: a real route edit, captured, then interpretation fails.
        vm.waypointsText = "LFMD DCT LFAT DCT LFBD"
        #expect(await vm.interpretRouteForSubmit() == .failed)
        #expect(vm.editedRawRoute == "LFMD DCT LFAT DCT LFBD")

        // The pilot backs the route out and saves an unrelated change instead.
        vm.waypointsText = "LFMD LFML"
        vm.cruiseAltitudeFt = 9000
        _ = await vm.saveEditedFlight(regenerate: false)
        #expect(repo.lastUpdateRequest?.rawRoute == nil)
    }

    /// A refresh already running when the edit lands is computing the OLD
    /// parameters, so a 409 must be re-queued rather than dropped.
    @Test func queuedRefreshRetriesWhenOneIsAlreadyInProgress() async throws {
        let flight = makeFlight()
        let repo = MockBriefingRepository()
        repo.updateFlightResult = .success(try makeUpdateResponse(flight: flight,
                                                                  invalidation: .refetchNeeded))
        repo.triggerRefreshResults = [.failure(APIError.serverError(409, "Refresh already in progress"))]
        let previousDelay = AddFlightViewModel.queueRefreshRetryDelay
        AddFlightViewModel.queueRefreshRetryDelay = .milliseconds(1)
        defer { AddFlightViewModel.queueRefreshRetryDelay = previousDelay }

        let vm = AddFlightViewModel(repository: repo, flight: flight)
        vm.flightDurationHours = 3.0

        #expect(await vm.saveEditedFlight(regenerate: true) != nil)
        await vm.pendingRefreshTask?.value
        // Once on the 409, once on the retry that succeeds.
        #expect(repo.triggeredRefreshIds == [flight.id, flight.id])
    }

    /// The alternate departure is bound to the primary's UTC day — the picker
    /// used to offer days the server rejects outright.
    @Test func alternateDepartureIsBoundToTheDepartureDay() {
        let flight = makeFlight(departureTime: "2026-06-24T12:00:00Z",
                                flexibility: .alternate,
                                altDepartureTime: "2026-06-24T15:00:00Z")
        let vm = makeVM(flight: flight)
        // Move the departure two days out; the alternate follows onto that day,
        // keeping its 15:00 UTC time.
        vm.departureDate = vm.departureDate.addingTimeInterval(48 * 3600)
        let aligned = vm.alignedAltDepartureInstant
        #expect(AddFlightViewModel.utcDay(of: aligned)
                == AddFlightViewModel.utcDay(of: vm.departureDate))
        #expect(aligned == Date.parseISO8601("2026-06-26T15:00:00Z"))
        #expect(vm.altDepartureCollidesWithDeparture == false)
    }

    @Test func alternateEqualToDepartureIsFlaggedLocally() {
        let flight = makeFlight(departureTime: "2026-06-24T12:00:00Z",
                                flexibility: .alternate,
                                altDepartureTime: "2026-06-24T12:00:00Z")
        let vm = makeVM(flight: flight)
        #expect(vm.altDepartureCollidesWithDeparture)
    }

    @Test func canSaveAircraftReflectsIcaoTypeRegex() {
        let vm = makeVM()
        vm.newAircraftIcaoType = "C172"
        #expect(vm.canSaveAircraft)            // 1–4 alphanumerics
        vm.newAircraftIcaoType = "a1"          // lowercased → uppercased, valid
        #expect(vm.canSaveAircraft)
        vm.newAircraftIcaoType = "TOOLONG"     // > 4 chars
        #expect(vm.canSaveAircraft == false)
        vm.newAircraftIcaoType = "C-72"        // illegal character
        #expect(vm.canSaveAircraft == false)
        vm.newAircraftIcaoType = ""            // empty
        #expect(vm.canSaveAircraft == false)
    }
}

// MARK: - FlightListViewModel (async load state machine)

@MainActor
@Suite struct FlightListViewModelTests {

    @Test func startsIdle() {
        let vm = FlightListViewModel(repository: MockBriefingRepository())
        guard case .idle = vm.state else {
            Issue.record("expected .idle, got \(vm.state)")
            return
        }
    }

    @Test func loadSuccessTransitionsToLoaded() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([makeFlight(id: "a"), makeFlight(id: "b")])
        let vm = FlightListViewModel(repository: repo)

        await vm.loadFlights()

        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded, got \(vm.state)")
            return
        }
        #expect(flights.count == 2)
        #expect(vm.isOffline == false)            // plain mock isn't a caching repo
        #expect(vm.cachedFlightIds.isEmpty)
        #expect(repo.flightsCallCount == 1)
    }

    @Test func loadFailureTransitionsToError() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .failure(MockError.injected("server down"))
        let vm = FlightListViewModel(repository: repo)

        await vm.loadFlights()

        guard case .error = vm.state else {
            Issue.record("expected .error, got \(vm.state)")
            return
        }
    }

    // MARK: Cache-first cold start (#359)

    /// Seed `flights.json` into a temp-dir cache, then drive a real
    /// `CachingBriefingRepository` whose online layer is a mock we can gate.
    private func seededCache(_ flights: [FlightResponse]) async throws -> BriefingCacheStore {
        let cache = BriefingCacheStore(cacheDir: makeTempDir())
        try await cache.writeMetadata(JSONEncoder.weatherBrief.encode(flights), name: "flights")
        return cache
    }

    /// Cold start with a cached list + a slow-but-successful fetch: paints the
    /// cached list immediately (never a full-screen spinner) and swaps in the
    /// fresh list once the network resolves.
    @Test func coldStartSeedsCachedListThenSwapsFresh() async throws {
        let cache = try await seededCache([makeFlight(id: "cached")])
        let online = MockBriefingRepository()
        online.flightsResult = .success([makeFlight(id: "fresh")])
        let gate = TestGate()
        online.beforeFlightsReturn = { await gate.wait() }
        let vm = FlightListViewModel(repository: CachingBriefingRepository.makeForTesting(online: online, cache: cache))

        let task = Task { await vm.loadFlights() }

        // Spin the main actor until the seed has painted the cached list. The
        // gated fetch keeps loadFlights suspended, so we observe the seed.
        var seeded = false
        for _ in 0..<200 where !seeded {
            await Task.yield()
            if case .loaded(let f) = vm.state, f.first?.id == "cached" { seeded = true }
        }
        #expect(seeded)                       // instant paint from cache…
        #expect(vm.isRefreshing)              // …with the subtle indicator, not a wheel
        if case .loading = vm.state { Issue.record("entered .loading despite cached list") }

        await gate.open()
        await task.value

        guard case .loaded(let fresh) = vm.state else {
            Issue.record("expected .loaded(fresh), got \(vm.state)")
            return
        }
        #expect(fresh.first?.id == "fresh")   // server stayed authoritative
        #expect(vm.isRefreshing == false)
        #expect(vm.isOffline == false)
    }

    /// True first run (no cached list): the full-screen spinner still shows.
    @Test func coldStartWithoutCacheShowsSpinnerThenLoads() async throws {
        let cache = BriefingCacheStore(cacheDir: makeTempDir())   // empty — no flights.json
        let online = MockBriefingRepository()
        online.flightsResult = .success([makeFlight(id: "fresh")])
        let gate = TestGate()
        online.beforeFlightsReturn = { await gate.wait() }
        let vm = FlightListViewModel(repository: CachingBriefingRepository.makeForTesting(online: online, cache: cache))

        let task = Task { await vm.loadFlights() }

        var sawLoading = false
        for _ in 0..<200 where !sawLoading {
            await Task.yield()
            if case .loading = vm.state { sawLoading = true }
        }
        #expect(sawLoading)                   // spinner on genuine first run
        #expect(vm.isRefreshing == false)

        await gate.open()
        await task.value

        guard case .loaded(let fresh) = vm.state else {
            Issue.record("expected .loaded(fresh), got \(vm.state)")
            return
        }
        #expect(fresh.first?.id == "fresh")
    }

    /// Cached list present but the network throws: the cached list stays on
    /// screen and the view model reports offline — never `.error`, never empty.
    @Test func coldStartCachePresentNetworkThrowsStaysOffline() async throws {
        let cache = try await seededCache([makeFlight(id: "cached")])
        let online = MockBriefingRepository()
        online.flightsResult = .failure(MockError.injected("offline"))
        let vm = FlightListViewModel(repository: CachingBriefingRepository.makeForTesting(online: online, cache: cache))

        await vm.loadFlights()

        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded (cached), got \(vm.state)")
            return
        }
        #expect(flights.first?.id == "cached")   // offline fallback served the cache
        #expect(vm.isOffline == true)
        #expect(vm.isRefreshing == false)
    }

    // MARK: Level-triggered reconcile (#426)

    /// A briefing that advanced on the server (new pack `fetchTimestamp`) repaints
    /// the list via the quiet reconcile — the completion path the active-refresh
    /// edge can miss when a refresh starts *and* finishes between two polls.
    @Test func reconcileRepaintsWhenPackTimestampAdvances() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN", fetchTimestamp: "2026-07-15T10:00:00Z")),
        ])
        let vm = FlightListViewModel(repository: repo)
        await vm.loadFlights()                 // baseline snapshot @ T1

        // Server now has a newer pack for the same flight.
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "RED", fetchTimestamp: "2026-07-15T10:05:00Z")),
        ])
        let repainted = await vm.reconcileLatestPacks()

        #expect(repainted)
        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded, got \(vm.state)")
            return
        }
        #expect(flights.first?.latestBriefing?.assessment == "RED")
    }

    /// No pack change → the reconcile is a no-op (one cheap fetch, no repaint), so
    /// it can run on a steady cadence without churning the list.
    @Test func reconcileIsNoopWhenPackUnchanged() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN", fetchTimestamp: "2026-07-15T10:00:00Z")),
        ])
        let vm = FlightListViewModel(repository: repo)
        await vm.loadFlights()

        let repainted = await vm.reconcileLatestPacks()   // same timestamp
        #expect(repainted == false)
    }

    /// A slow reconcile must not clobber a fresher `loadFlights()` that completed
    /// while the reconcile was suspended in its own fetch (#427 review). Both run
    /// on `@MainActor` but interleave across `await`, so the reconcile captures a
    /// load generation and drops its write when an authoritative load bumps it.
    @Test func reconcileDropsStaleWriteWhenLoadFlightsWins() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN", fetchTimestamp: "2026-07-15T10:00:00Z")),
        ])
        let vm = FlightListViewModel(repository: repo)
        await vm.loadFlights()                         // baseline (generation → 1)

        // Park the reconcile inside its `repository.flights()` fetch.
        let gate = TestGate()
        repo.beforeFlightsReturn = { await gate.wait() }
        let reconcileTask = Task { await vm.reconcileLatestPacks() }

        // Wait until the reconcile has entered flights() (2nd call) and is parked
        // on the gate — by then it has already captured the load generation.
        for _ in 0..<1000 where repo.flightsCallCount < 2 { await Task.yield() }
        #expect(repo.flightsCallCount == 2)

        // A pull-to-refresh lands a newer briefing while the reconcile is suspended.
        repo.beforeFlightsReturn = nil
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "RED", fetchTimestamp: "2026-07-15T10:05:00Z")),
        ])
        await vm.loadFlights()                         // wins (generation → 2), state = RED

        // Release the stale reconcile; it must detect it lost and drop its write.
        await gate.open()
        let repainted = await reconcileTask.value
        #expect(repainted == false)

        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded, got \(vm.state)")
            return
        }
        #expect(flights.first?.latestBriefing?.assessment == "RED")   // fresh load preserved
    }

    /// The mirror case (#427 review, round 2): a slow `loadFlights()` must not
    /// clobber a fresher `reconcileLatestPacks()` that committed while the load
    /// was suspended in its own fetch. Request ordering doesn't guarantee the
    /// load is fresher, so `loadFlights()` captures a generation and drops its
    /// write when a reconcile bumps it.
    @Test func loadFlightsDropsStaleWriteWhenReconcileWins() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN", fetchTimestamp: "2026-07-15T10:00:00Z")),
        ])
        let vm = FlightListViewModel(repository: repo)
        await vm.loadFlights()                         // baseline (generation → 1)

        // Park a `loadFlights()` inside its `repository.flights()` fetch.
        let gate = TestGate()
        repo.beforeFlightsReturn = { await gate.wait() }
        let loadTask = Task { await vm.loadFlights() }

        // Wait until that load has entered flights() (2nd call) and is parked on
        // the gate — by then it has already captured the load generation.
        for _ in 0..<1000 where repo.flightsCallCount < 2 { await Task.yield() }
        #expect(repo.flightsCallCount == 2)

        // A reconcile lands a newer briefing while the load is suspended.
        repo.beforeFlightsReturn = nil
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "RED", fetchTimestamp: "2026-07-15T10:05:00Z")),
        ])
        let repainted = await vm.reconcileLatestPacks()   // wins (generation → 2), state = RED
        #expect(repainted == true)

        // Restore the stale result the parked load will read when the gate opens
        // (the mock resolves `flightsResult` *after* the gate), so a missing guard
        // would revert the row to GREEN — that's what this test must catch.
        repo.flightsResult = .success([
            makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN", fetchTimestamp: "2026-07-15T10:00:00Z")),
        ])
        // Release the stale load; it must detect it lost and drop its write.
        await gate.open()
        await loadTask.value

        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded, got \(vm.state)")
            return
        }
        #expect(flights.first?.latestBriefing?.assessment == "RED")   // fresh reconcile preserved
    }

    // MARK: Delete an owned flight (swipe / context menu)

    /// A confirmed delete calls the repository once and re-syncs the list, so the
    /// row disappears immediately instead of at the next foreground refresh.
    @Test func deleteFlightCallsRepositoryThenReloads() async throws {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([makeFlight(id: "a"), makeFlight(id: "b")])
        let vm = FlightListViewModel(repository: repo)
        await vm.loadFlights()
        // Server truth after the delete.
        repo.flightsResult = .success([makeFlight(id: "b")])

        try await vm.deleteFlight(makeFlight(id: "a"))

        #expect(repo.deletedFlightIds == ["a"])
        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded, got \(vm.state)")
            return
        }
        #expect(flights.map(\.id) == ["b"])
    }

    /// A failed delete rethrows (the view surfaces it) and leaves the list exactly
    /// as it was — a row that couldn't be deleted must not vanish as if it had been.
    @Test func deleteFlightFailureRethrowsAndKeepsRow() async {
        let repo = MockBriefingRepository()
        repo.flightsResult = .success([makeFlight(id: "a")])
        repo.deleteFlightResult = .failure(MockError.injected("not owned"))
        let vm = FlightListViewModel(repository: repo)
        await vm.loadFlights()

        await #expect(throws: MockError.self) {
            try await vm.deleteFlight(makeFlight(id: "a"))
        }

        guard case .loaded(let flights) = vm.state else {
            Issue.record("expected .loaded, got \(vm.state)")
            return
        }
        #expect(flights.map(\.id) == ["a"])
        #expect(repo.flightsCallCount == 1)     // no reload on failure
    }
}

// MARK: - FlightCardView content equality (#426)

/// The row must diff on the briefing content it draws, not `FlightResponse`'s
/// id-only identity — otherwise a same-id/new-briefing update would look
/// unchanged and the card would keep its stale badge until app relaunch.
@MainActor
@Suite struct FlightCardEquatableTests {

    @Test func sameIdDifferentBriefingIsNotEqual() {
        let green = makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN", fetchTimestamp: "T1"))
        let red = makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "RED", fetchTimestamp: "T2"))

        // The underlying model is *equal* by its id-only identity conformance…
        #expect(green == red)
        // …but the cards must compare *unequal* so SwiftUI repaints the row.
        #expect(FlightCardView(flight: green) != FlightCardView(flight: red))
    }

    @Test func identicalContentIsEqual() {
        let a = makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "AMBER", fetchTimestamp: "T1"))
        let b = makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "AMBER", fetchTimestamp: "T1"))
        #expect(FlightCardView(flight: a) == FlightCardView(flight: b))
    }

    @Test func refreshingFlagBreaksEquality() {
        let a = makeFlight(id: "a", latestBriefing: makeBriefingStatus(assessment: "GREEN"))
        #expect(FlightCardView(flight: a, isRefreshing: false) != FlightCardView(flight: a, isRefreshing: true))
    }
}

// MARK: - BriefingStatusInfo wire decoding (#426)

@Suite struct BriefingStatusDecodeTests {

    /// The server's `latest_briefing.fetch_timestamp` decodes onto
    /// `fetchTimestamp` via the shared snake-case decoder.
    @Test func decodesFetchTimestamp() throws {
        let json = Data("""
        {"assessment": "GREEN", "fetch_timestamp": "2026-07-15T10:00:00Z"}
        """.utf8)
        let status = try JSONDecoder.weatherBrief.decode(BriefingStatusInfo.self, from: json)
        #expect(status.assessment == "GREEN")
        #expect(status.fetchTimestamp == "2026-07-15T10:00:00Z")
    }

    /// A legacy payload without the field decodes with `fetchTimestamp == nil`
    /// (optional `let`, no default → `decodeIfPresent`).
    @Test func toleratesMissingFetchTimestamp() throws {
        let json = Data("""
        {"assessment": "AMBER"}
        """.utf8)
        let status = try JSONDecoder.weatherBrief.decode(BriefingStatusInfo.self, from: json)
        #expect(status.fetchTimestamp == nil)
    }
}

// MARK: - RouteMapViewModel (waypoint extraction + fit-region math)

@MainActor
@Suite struct RouteMapViewModelTests {

    /// Minimal snapshot with three waypoints at clean lat/lon for exact math.
    private func snapshot() throws -> SnapshotResponse {
        let json = """
        {
          "route": {
            "name": "TEST",
            "waypoints": [
              {"icao": "AAAA", "name": "Alpha", "lat": 40.0, "lon": 2.0},
              {"icao": "BBBB", "name": "Bravo", "lat": 42.0, "lon": 4.0},
              {"icao": "CCCC", "name": "Charlie", "lat": 44.0, "lon": 6.0}
            ],
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 13000,
            "flight_duration_hours": 2.0
          },
          "target_date": "2026-06-24",
          "days_out": 1
        }
        """
        return try JSONDecoder.weatherBrief.decode(SnapshotResponse.self, from: Data(json.utf8))
    }

    @Test func extractsWaypointsAndRouteLine() throws {
        let vm = RouteMapViewModel()
        vm.update(from: try snapshot())
        #expect(vm.waypoints.count == 3)
        #expect(vm.routeCoordinates.count == 3)
        #expect(vm.waypoints.first?.id == "AAAA")
        #expect(vm.waypoints.first?.name == "Alpha")
    }

    @Test func fitRegionCentersAndPadsBy1_4Plus0_5() throws {
        let vm = RouteMapViewModel()
        vm.update(from: try snapshot())
        // center = midpoint of the bounding box
        #expect(abs(vm.mapRegion.center.latitude - 42.0) < 1e-6)
        #expect(abs(vm.mapRegion.center.longitude - 4.0) < 1e-6)
        // span = range * 1.4 + 0.5 padding
        #expect(abs(vm.mapRegion.span.latitudeDelta - ((44.0 - 40.0) * 1.4 + 0.5)) < 1e-6)
        #expect(abs(vm.mapRegion.span.longitudeDelta - ((6.0 - 2.0) * 1.4 + 0.5)) < 1e-6)
    }
}

// MARK: - BriefingViewModel (pack history labels)

@MainActor
@Suite struct BriefingViewModelTests {

    private func vm() -> BriefingViewModel {
        BriefingViewModel(flight: makeFlight(), repository: MockBriefingRepository())
    }

    private func pack(daysOut: Int, timestamp: String = "2026-06-24T09:00:00Z") throws -> PackMetaResponse {
        let json = """
        {
          "flight_id": "flt-1",
          "fetch_timestamp": "\(timestamp)",
          "days_out": \(daysOut),
          "is_historical": false,
          "has_gramet": true, "has_skewt": true, "has_digest": true, "has_advisories": true,
          "model_init_times": {}, "grib_init_times": {}, "models_skipped_region": []
        }
        """
        return try JSONDecoder.weatherBrief.decode(PackMetaResponse.self, from: Data(json.utf8))
    }

    @Test func dayLabelSignsForecastVsHistorical() throws {
        let m = vm()
        #expect(m.packDayLabel(for: try pack(daysOut: 3)) == "D-3")
        #expect(m.packDayLabel(for: try pack(daysOut: 0)) == "D-0")
        #expect(m.packDayLabel(for: try pack(daysOut: -2)) == "D+2")
    }

    @Test func packLabelFormatsUtcDateTime() throws {
        let m = vm()
        let label = m.packLabel(for: try pack(daysOut: 1, timestamp: "2026-06-24T09:00:00Z"))
        #expect(label == "D-1 · Jun 24 09:00 UTC")
    }

    @Test func autoSyncOnlyWhenViewingLatestPack() throws {
        let d0 = try pack(daysOut: 0, timestamp: "2026-06-24T12:00:00Z")   // newest
        let d1 = try pack(daysOut: 1, timestamp: "2026-06-23T12:00:00Z")   // older
        let history = [d0, d1]
        // Viewing the newest pack → auto-sync allowed.
        #expect(BriefingViewModel.isViewingLatestPack(current: d0.fetchTimestamp, history: history))
        // Viewing an older pack picked from history → auto-sync suppressed.
        #expect(!BriefingViewModel.isViewingLatestPack(current: d1.fetchTimestamp, history: history))
        // History not loaded yet → don't block the first sync.
        #expect(BriefingViewModel.isViewingLatestPack(current: d0.fetchTimestamp, history: []))
        // No current pack → allowed.
        #expect(BriefingViewModel.isViewingLatestPack(current: nil, history: history))
        // Fractional seconds must not misorder vs a whole-second sibling: a viewer
        // on the fractional (later) run still counts as latest.
        let dFrac = try pack(daysOut: 0, timestamp: "2026-06-24T12:00:00.500Z")
        #expect(BriefingViewModel.isViewingLatestPack(current: dFrac.fetchTimestamp, history: [dFrac, d0]))
    }

    /// A *quiet* reload (seamless sync) that fails must keep the previously-loaded
    /// section on screen, not blow it away with an error — the silent-data-loss
    /// guard the review flagged. Drives the real `loadBriefing` → `syncLatestPack`
    /// path so the `quiet` + stale-timestamp handling is exercised end-to-end.
    @Test func quietReloadKeepsDataWhenSectionFetchFails() async throws {
        let mock = MockBriefingRepository()
        let p1 = try pack(daysOut: 0, timestamp: "2026-06-24T09:00:00Z")
        let p2 = try pack(daysOut: 0, timestamp: "2026-06-24T12:00:00Z")   // newer → adopt
        let advisories = try JSONDecoder.weatherBrief.decode(
            AdvisoriesResponse.self,
            from: Data("""
            {"advisories": [], "catalog": [], "route_name": "R", "cruise_altitude_ft": 8000,
             "flight_ceiling_ft": 13000, "total_distance_nm": 100.0, "models": [], "aggregation": "worst"}
            """.utf8)
        )

        // First load: pack p1 + advisories succeed (other sections stay notStubbed —
        // we only assert on advisories).
        mock.latestPackHandler = { p1 }
        mock.advisoriesHandler = { advisories }
        let vm = BriefingViewModel(flight: makeFlight(), repository: mock)
        await vm.loadBriefing()
        #expect(vm.advisoriesState.hasData)
        #expect(vm.pack?.fetchTimestamp == p1.fetchTimestamp)

        // A newer pack appears, but the advisories fetch now fails transiently.
        mock.latestPackHandler = { p2 }
        mock.advisoriesHandler = { throw MockError.injected("transient") }
        await vm.syncLatestPack()

        // The pack advanced, but the quiet reload swallowed the error and kept the
        // previously-loaded advisories rather than showing an error wall.
        #expect(vm.pack?.fetchTimestamp == p2.fetchTimestamp)
        #expect(vm.advisoriesState.hasData)
    }
}
