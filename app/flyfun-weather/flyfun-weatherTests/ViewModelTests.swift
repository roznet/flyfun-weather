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

        gate.open()
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

        gate.open()
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
}
