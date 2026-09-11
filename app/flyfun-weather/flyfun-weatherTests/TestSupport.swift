//
//  TestSupport.swift
//  flyfun-weatherTests
//
//  Shared test doubles + fixture factories (#314).
//
//  `MockBriefingRepository` conforms to the full `BriefingRepository` protocol
//  but only the handful of calls a given ViewModel actually makes are stubbable;
//  everything else throws `notStubbed` so an unexpected call fails loudly rather
//  than returning silent garbage. ViewModels inject `any BriefingRepository`, so
//  this is the seam that lets the whole logic layer be tested with no network.
//

import Foundation
@testable import flyfun_weather

enum MockError: Error, CustomStringConvertible {
    case notStubbed(String)
    case injected(String)
    var description: String {
        switch self {
        case .notStubbed(let m): "MockBriefingRepository.\(m) was called but not stubbed"
        case .injected(let m): "injected failure: \(m)"
        }
    }
}

/// In-memory `BriefingRepository`. Configure the few results a test needs; the
/// rest throw `notStubbed`.
///
/// `@unchecked Sendable` is sound here because each test drives a single
/// instance sequentially — configure the result, then `await` one ViewModel
/// call, then read the counter; the `await` establishes the ordering. It stays a
/// `class` (not an `actor`) so tests keep the synchronous `repo.x = …` /
/// `repo.callCount` configuration API.
final class MockBriefingRepository: BriefingRepository, @unchecked Sendable {
    // Configurable results (set per-test).
    var flightsResult: Result<[FlightResponse], Error> = .success([])
    var aircraftResult: Result<[AircraftResponse], Error> = .success([])
    var createFlightResult: Result<FlightResponse, Error> = .failure(MockError.notStubbed("createFlight"))
    var updateFlightResult: Result<UpdateFlightResponse, Error> = .failure(MockError.notStubbed("updateFlight"))
    var moveFlightResult: Result<FlightResponse, Error> = .failure(MockError.notStubbed("moveFlight"))
    /// Queued outcomes for successive `triggerRefresh` calls; empty (the default)
    /// means every call succeeds.
    var triggerRefreshResults: [Result<Void, Error>] = []
    /// Pack history for the edited flight — the Move confirm's "discards N
    /// briefings" count reads this. Empty by default (the no-packs copy).
    var packsResult: Result<[PackMetaResponse], Error> = .success([])
    /// Delete succeeds by default (the common case under test is the happy path);
    /// set a `.failure` to drive the surfaced-error branch.
    var deleteFlightResult: Result<Void, Error> = .success(())
    /// Bulk delete: by default every requested id comes back confirmed-deleted.
    /// Set a closure to model a partial result (`not_found`) or a server failure.
    var bulkDeleteHandler: (@Sendable ([String]) throws -> BulkDeleteResponse)?
    var interpretRouteResult: Result<InterpretRouteResponse, Error> = .failure(MockError.notStubbed("interpretRoute"))
    var submitPirepsBatchResult: Result<[PirepResponse], Error> = .success([PirepResponse.offline])

    // --- Trips (#607). Stored here because a Swift extension can't declare
    // stored properties; the conformance itself is in the extension below.
    var tripsResult: Result<[TripResponse], Error> = .success([])
    var tripResult: Result<TripResponse, Error> = .failure(MockError.notStubbed("trip"))
    var createTripResult: Result<TripResponse, Error> = .failure(MockError.notStubbed("createTrip"))
    var addTripLegsResult: Result<TripResponse, Error> = .failure(MockError.notStubbed("addTripLegs"))
    var removeTripLegResult: Result<Void, Error> = .success(())
    var updateTripResult: Result<TripResponse, Error> = .failure(MockError.notStubbed("updateTrip"))
    var deleteTripResult: Result<Void, Error> = .success(())
    var refreshTripResult: Result<TripRefreshStatus, Error> = .failure(MockError.notStubbed("refreshTrip"))
    var tripAiSummaryResult: Result<TripAiSummaryResponse, Error> = .failure(MockError.notStubbed("tripAiSummary"))
    /// Successive `tripRefreshStatus` polls. The final element is returned
    /// repeatedly once the script runs out, so a poll loop settles instead of
    /// throwing when it outruns the test's expectations.
    var tripRefreshStatusQueue: [TripRefreshStatus] = []

    private(set) var tripsCallCount = 0
    private(set) var tripRefreshStatusCallCount = 0
    private(set) var tripAiSummaryCallCount = 0
    private(set) var refreshedTripIds: [String] = []
    private(set) var deletedTripIds: [String] = []
    private(set) var removedLegs: [(tripId: String, flightId: String)] = []
    private(set) var addedLegs: [(tripId: String, flightIds: [String])] = []
    private(set) var createdTrips: [(flightIds: [String], name: String?)] = []
    private(set) var lastTripUpdate: (tripId: String, request: UpdateTripRequest)?

    /// Optional per-call overrides for the pack-data path (BriefingViewModel
    /// tests). Reassign between `await`s to vary behaviour across sequential loads
    /// — e.g. succeed on the first load, then throw on a quiet reload. Default:
    /// the `notStubbed` throw below.
    var latestPackHandler: (@Sendable () throws -> PackMetaResponse)?
    var advisoriesHandler: (@Sendable () throws -> AdvisoriesResponse)?

    // Call counters + captured args for behaviour assertions.
    private(set) var flightsCallCount = 0
    private(set) var submitPirepsBatchCallCount = 0
    private(set) var interpretRouteCallCount = 0
    /// One per `updateFlight` call — the post-move residual PATCH retries, so a
    /// test needs to see how many attempts a given failure bought.
    private(set) var updateFlightCallCount = 0
    /// One per `packs` call, so the pack-count load can be shown to de-duplicate
    /// across the editor's `.task` and the Save-time await.
    private(set) var packsCallCount = 0
    private(set) var lastUpdateRequest: UpdateFlightRequest?
    private(set) var lastMoveRequest: MoveFlightRequest?
    private(set) var lastMovedFlightId: String?
    private(set) var triggeredRefreshIds: [String] = []
    private(set) var deletedFlightIds: [String] = []
    /// One entry per `bulkDeleteFlights` call. NOT chunk boundaries: this mock
    /// stands in for the *online* layer, which is where chunking happens
    /// (`OnlineBriefingRepository`), so it only ever sees the full id list handed
    /// down by `CachingBriefingRepository`. Chunking is asserted against
    /// `BulkDeleteResponse.sendChunked` directly.
    private(set) var bulkDeleteRequests: [[String]] = []
    private(set) var lastCreateRequest: CreateFlightRequest?
    private(set) var lastInterpretRawRoute: String?

    /// Optional hook awaited *inside* `flights()` before it returns — lets a test
    /// gate the network so it can observe intermediate ViewModel state (e.g. the
    /// cache-seeded list painted before a slow fetch resolves, #359).
    var beforeFlightsReturn: (@Sendable () async -> Void)?

    func flights() async throws -> [FlightResponse] {
        flightsCallCount += 1
        if let hook = beforeFlightsReturn { await hook() }
        return try flightsResult.get()
    }
    func flight(id: String) async throws -> FlightResponse {
        guard let match = try flightsResult.get().first(where: { $0.id == id }) else {
            throw APIError.notFound
        }
        return match
    }
    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse {
        lastCreateRequest = request
        return try createFlightResult.get()
    }
    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> UpdateFlightResponse {
        updateFlightCallCount += 1
        lastUpdateRequest = request
        return try updateFlightResult.get()
    }
    func moveFlight(flightId: String, request: MoveFlightRequest) async throws -> FlightResponse {
        lastMovedFlightId = flightId
        lastMoveRequest = request
        return try moveFlightResult.get()
    }
    /// Awaited *inside* `triggerRefresh` before it returns — lets a test hold the
    /// queued refresh suspended and prove `saveEditedFlight` already returned.
    var beforeTriggerRefreshReturn: (@Sendable () async -> Void)?

    func triggerRefresh(flightId: String) async throws {
        triggeredRefreshIds.append(flightId)
        if let hook = beforeTriggerRefreshReturn { await hook() }
        // Results are consumed in order so a test can drive the 409-then-succeed
        // retry; once exhausted, every further call succeeds.
        if !triggerRefreshResults.isEmpty {
            try triggerRefreshResults.removeFirst().get()
        }
    }
    func deleteFlight(id: String) async throws {
        deletedFlightIds.append(id)
        try deleteFlightResult.get()
    }
    func bulkDeleteFlights(ids: [String]) async throws -> BulkDeleteResponse {
        bulkDeleteRequests.append(ids)
        if let handler = bulkDeleteHandler { return try handler(ids) }
        return BulkDeleteResponse(deleted: ids, notFound: [])
    }
    func aircraft() async throws -> [AircraftResponse] { try aircraftResult.get() }
    func profiles() async throws -> [ProfileResponse] { [] }
    func usageSummary() async throws -> UsageSummaryResponse {
        try JSONDecoder.weatherBrief.decode(UsageSummaryResponse.self, from: Data("{}".utf8))
    }
    func interpretRoute(rawRoute: String) async throws -> InterpretRouteResponse {
        interpretRouteCallCount += 1
        lastInterpretRawRoute = rawRoute
        return try interpretRouteResult.get()
    }
    func routeDistance(waypoints: [String]) async throws -> RouteDistanceResponse { throw MockError.notStubbed("routeDistance") }
    func autorouterRoutes(limit: Int) async throws -> [AutorouterRoute] { [] }
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] { throw MockError.notStubbed("searchAircraftTypes") }
    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse { throw MockError.notStubbed("createAircraft") }
    func parseFpl(_ text: String) async throws -> ParseFplResponse { throw MockError.notStubbed("parseFpl") }
    /// Awaited *inside* `packs` before it returns — lets a test hold the pack
    /// count suspended and observe the copy the editor shows meanwhile.
    var beforePacksReturn: (@Sendable () async -> Void)?

    func packs(flightId: String) async throws -> [PackMetaResponse] {
        packsCallCount += 1
        if let hook = beforePacksReturn { await hook() }
        return try packsResult.get()
    }
    func flightByShareCode(_ code: String) async throws -> FlightResponse { throw MockError.notStubbed("flightByShareCode") }
    func subscribeFlight(id: String) async throws { throw MockError.notStubbed("subscribeFlight") }
    func unsubscribeFlight(id: String) async throws { throw MockError.notStubbed("unsubscribeFlight") }
    func latestPack(flightId: String) async throws -> PackMetaResponse {
        if let h = latestPackHandler { return try h() }
        throw MockError.notStubbed("latestPack")
    }
    func airportWeather(icao: String, day: Int, hour: Int) async throws -> AirportWeatherResponse { throw MockError.notStubbed("airportWeather") }
    func forecastMap(day: Int, hour: Int) async throws -> ForecastMapResponse { throw MockError.notStubbed("forecastMap") }
    func forecastDays() async throws -> ForecastDaysResponse { throw MockError.notStubbed("forecastDays") }
    func frequentAirports() async throws -> FrequentAirportsResponse { throw MockError.notStubbed("frequentAirports") }
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        if let h = advisoriesHandler { return try h() }
        throw MockError.notStubbed("advisories")
    }
    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse { throw MockError.notStubbed("advisoryDetail") }
    func recalculateAdvisories(flightId: String, timestamp: String, cruiseAltitudeFt: Int?) async throws { throw MockError.notStubbed("recalculateAdvisories") }
    func timeOptions(flightId: String, timestamp: String) async throws -> TimeOptionsResponse { throw MockError.notStubbed("timeOptions") }
    func confirmTimeOption(flightId: String, timestamp: String, departureTime: String) async throws { throw MockError.notStubbed("confirmTimeOption") }
    func rescanTimeOptions(flightId: String, timestamp: String) async throws { throw MockError.notStubbed("rescanTimeOptions") }
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse { throw MockError.notStubbed("digest") }
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse { throw MockError.notStubbed("snapshot") }
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse { throw MockError.notStubbed("routeAnalyses") }
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse { throw MockError.notStubbed("elevation") }
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data { throw MockError.notStubbed("skewtImage") }
    func grametImage(flightId: String, timestamp: String) async throws -> Data { throw MockError.notStubbed("grametImage") }
    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse { throw MockError.notStubbed("soundingProfile") }
    func refreshStream(flightId: String, source: RefreshSource) async -> AsyncThrowingStream<RefreshEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }
    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse { throw MockError.notStubbed("refreshStatus") }
    func activeRefreshes() async throws -> [ActiveRefreshResponse] { [] }
    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse { throw MockError.notStubbed("submitPirep") }
    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse] {
        submitPirepsBatchCallCount += 1
        return try submitPirepsBatchResult.get()
    }
    func fetchPireps(flightId: String) async throws -> PirepListResponse { throw MockError.notStubbed("fetchPireps") }
    func fetchDebrief(flightId: String) async throws -> DebriefResponse { throw MockError.notStubbed("fetchDebrief") }
    func upsertDebrief(flightId: String, request: DebriefRequest) async throws -> DebriefResponse { throw MockError.notStubbed("upsertDebrief") }
    func deleteDebrief(flightId: String) async throws { throw MockError.notStubbed("deleteDebrief") }
    func submitDigestFeedback(_ request: DigestFeedbackRequest) async throws { throw MockError.notStubbed("submitDigestFeedback") }
    func submitGeneralFeedback(_ request: GeneralFeedbackRequest) async throws { throw MockError.notStubbed("submitGeneralFeedback") }
}

// MARK: - Fixture factories

func makeFlight(
    id: String = "flt-1",
    aircraftId: Int? = nil,
    waypoints: [String] = ["LFMD", "LFML"],
    departureTime: String = "2026-06-24T12:00:00Z",
    cruiseAltitudeFt: Int = 8000,
    flightDurationHours: Double = 2.0,
    private: Bool = false,
    role: FlightRole? = nil,
    shareCode: String? = nil,
    ownerDisplayName: String? = nil,
    isSubscribed: Bool? = nil,
    flexibility: FlexibilityMode? = nil,
    altDepartureTime: String? = nil,
    rawRoute: String? = nil,
    latestBriefing: BriefingStatusInfo? = nil
) -> FlightResponse {
    FlightResponse(
        id: id,
        userId: "user-1",
        profileId: nil,
        aircraftId: aircraftId,
        aircraft: nil,
        routeName: waypoints.joined(separator: " "),
        waypoints: waypoints,
        departureTime: departureTime,
        targetDate: "2026-06-24",
        targetTimeUtc: 1200,
        cruiseAltitudeFt: cruiseAltitudeFt,
        flightCeilingFt: 13000,
        flightDurationHours: flightDurationHours,
        private: `private`,
        autoRefresh: false,
        autoRefreshHour: nil,
        createdAt: "2026-06-20T09:00:00Z",
        latestBriefing: latestBriefing,
        coverage: nil,
        role: role,
        flexibility: flexibility,
        altDepartureTime: altDepartureTime,
        shareCode: shareCode,
        ownerDisplayName: ownerDisplayName,
        rawRoute: rawRoute,
        isSubscribed: isSubscribed
    )
}

/// Build a `BriefingStatusInfo` fixture — the latest-pack summary inlined into a
/// flight-list row. Centralizes construction so adding wire fields (e.g.
/// `fetchTimestamp`) touches one call site, not every test.
func makeBriefingStatus(
    assessment: String? = nil,
    assessmentReason: String? = nil,
    outlook: String? = nil,
    outlookReason: String? = nil,
    hasAdvisories: Bool? = nil,
    advisorySummary: AdvisorySummary? = nil,
    fetchTimestamp: String? = nil,
    unseen: Bool? = nil
) -> BriefingStatusInfo {
    BriefingStatusInfo(
        assessment: assessment,
        assessmentReason: assessmentReason,
        outlook: outlook,
        outlookReason: outlookReason,
        hasAdvisories: hasAdvisories,
        advisorySummary: advisorySummary,
        fetchTimestamp: fetchTimestamp,
        unseen: unseen
    )
}

/// Build a minimal `PackMetaResponse` fixture. Decoded from JSON rather than
/// constructed, because the type has no memberwise init in scope and this keeps
/// it honest about the wire shape (snake_case keys, optionals absent).
func makePackMeta(
    flightId: String = "flt-1",
    fetchTimestamp: String = "2026-06-24T09:00:00Z",
    daysOut: Int = 3
) throws -> PackMetaResponse {
    let json = """
    {
      "flight_id": "\(flightId)",
      "fetch_timestamp": "\(fetchTimestamp)",
      "days_out": \(daysOut),
      "is_historical": false,
      "has_gramet": true, "has_skewt": true, "has_digest": true, "has_advisories": true,
      "model_init_times": {}, "grib_init_times": {}, "models_skipped_region": []
    }
    """
    return try JSONDecoder.weatherBrief.decode(PackMetaResponse.self, from: Data(json.utf8))
}

/// Build an `UpdateFlightResponse` fixture (Decodable-only, so assembled via
/// JSON) for stubbing `MockBriefingRepository.updateFlightResult`.
func makeUpdateResponse(
    flight: FlightResponse = makeFlight(),
    invalidation: FlightInvalidation = .none
) throws -> UpdateFlightResponse {
    let flightData = try JSONEncoder.weatherBrief.encode(flight)
    var obj = try JSONSerialization.jsonObject(with: flightData) as! [String: Any]
    obj["invalidation"] = invalidation.rawValue
    let data = try JSONSerialization.data(withJSONObject: obj)
    return try JSONDecoder.weatherBrief.decode(UpdateFlightResponse.self, from: data)
}

/// Build an `InterpretRouteResponse` fixture for stubbing
/// `MockBriefingRepository.interpretRouteResult`. Defaults to a clean 2-point
/// route (nothing skipped / off-route); pass `skipped` / `offRoute` to model a
/// route the resolver dropped tokens from.
func makeInterpretResponse(
    interpreted: [String] = ["LFMD", "LFML"],
    skipped: [String] = [],
    offRoute: [String] = [],
    originalTokens: [String]? = nil
) -> InterpretRouteResponse {
    let waypoints = interpreted.enumerated().map { idx, icao in
        RouteWaypointInfo(
            icao: icao,
            name: icao,
            lat: 43.0 + Double(idx),
            lon: 6.0 + Double(idx),
            timezone: "Europe/Paris"
        )
    }
    return InterpretRouteResponse(
        originalTokens: originalTokens ?? (interpreted + skipped + offRoute),
        interpreted: interpreted,
        skipped: skipped,
        offRoute: offRoute,
        waypoints: waypoints
    )
}

func makePirepRequest(remarks: String = "test") -> SubmitPirepRequest {
    // Base is a fixed, safe JSON literal; set caller-controlled `remarks` on the
    // decoded value so a string with quotes/backslashes can't break the JSON.
    let json = """
    {"observed_at": "2026-06-24T12:00:00Z", "latitude": 43.5, "longitude": 6.95, "source": "inflight"}
    """
    var request = try! JSONDecoder.weatherBrief.decode(SubmitPirepRequest.self, from: Data(json.utf8))
    request.remarks = remarks
    return request
}

/// A one-shot async gate: `wait()` suspends until another task calls `open()`.
/// Lets a test hold a mocked network call suspended while it inspects the
/// ViewModel state that was painted before the call resolved.
actor TestGate {
    private var continuations: [CheckedContinuation<Void, Never>] = []
    private var isOpen = false

    func wait() async {
        if isOpen { return }
        await withCheckedContinuation { continuations.append($0) }
    }

    func open() {
        isOpen = true
        let pending = continuations
        continuations.removeAll()
        for c in pending { c.resume() }
    }
}

/// A unique temp directory for a test, auto-created. Caller removes it.
func makeTempDir() -> URL {
    let dir = FileManager.default.temporaryDirectory
        .appendingPathComponent("wbtests-\(UUID().uuidString)", isDirectory: true)
    // try! on purpose: if the temp dir can't be created, crash here rather than
    // returning a non-existent URL that makes every downstream write fail with a
    // misleading "no such file" error.
    try! FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}

// MARK: - Trips (#607)

/// `TripRepository` conformance for the shared mock.
///
/// Kept in its own extension so the trip surface is visible as an addition
/// rather than buried in the ~40-method flight mock. Only `trips()` and
/// `refreshTrip` are stubbable; the rest throw `notStubbed`, so a test that
/// reaches an unexpected trip call fails loudly instead of seeing empty data.
extension MockBriefingRepository: TripRepository {
    func trips() async throws -> [TripResponse] {
        tripsCallCount += 1
        return try tripsResult.get()
    }

    func trip(id: String) async throws -> TripResponse {
        try tripResult.get()
    }

    func createTrip(flightIds: [String], name: String?) async throws -> TripResponse {
        createdTrips.append((flightIds, name))
        return try createTripResult.get()
    }

    func addTripLegs(tripId: String, flightIds: [String]) async throws -> TripResponse {
        addedLegs.append((tripId, flightIds))
        return try addTripLegsResult.get()
    }

    func removeTripLeg(tripId: String, flightId: String) async throws {
        removedLegs.append((tripId, flightId))
        try removeTripLegResult.get()
    }

    func updateTrip(tripId: String, request: UpdateTripRequest) async throws -> TripResponse {
        lastTripUpdate = (tripId, request)
        return try updateTripResult.get()
    }

    func deleteTrip(tripId: String) async throws {
        deletedTripIds.append(tripId)
        try deleteTripResult.get()
    }

    func refreshTrip(tripId: String) async throws -> TripRefreshStatus {
        refreshedTripIds.append(tripId)
        return try refreshTripResult.get()
    }

    func tripRefreshStatus(tripId: String) async throws -> TripRefreshStatus {
        tripRefreshStatusCallCount += 1
        // A queue lets a test walk a chain forwards (leg 1 → leg 2 → done);
        // falling back to the last element means a poll that outruns the script
        // keeps seeing the terminal state rather than throwing.
        if !tripRefreshStatusQueue.isEmpty {
            return tripRefreshStatusQueue.count == 1
                ? tripRefreshStatusQueue[0]
                : tripRefreshStatusQueue.removeFirst()
        }
        return try refreshTripResult.get()
    }

    func tripAiSummary(tripId: String) async throws -> TripAiSummaryResponse {
        tripAiSummaryCallCount += 1
        return try tripAiSummaryResult.get()
    }
}
