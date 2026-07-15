#if DEBUG
import Foundation

/// Deterministic, offline `BriefingRepository` for UI tests (`FLYFUN_MOCK=1`).
///
/// Serves canned fixtures so XCUITest journeys run with no backend, no network,
/// and no ~2-minute briefing wait. Endpoints a journey doesn't need yet throw
/// `FixtureError.notProvided` — wire them up as the journeys grow rather than
/// faking a whole briefing up front.
///
/// Flights use far-future departures so they land in the list's "Future" group
/// regardless of when the test runs.
///
/// A `@MainActor final class` (like the real `CachingBriefingRepository`, and
/// matching this project's default main-actor isolation) — the `createdFlights`
/// mutation is serialized by the main actor, and reads of the main-actor
/// `FixtureBriefingData` statics stay in-isolation. Every call arrives via
/// `await` through the `BriefingRepository` protocol.
///
/// `fixture-1` serves a full, coherent briefing (`FixtureBriefingData`) so the
/// advisory drill-down + cross-section journeys have real data; other flights'
/// briefing endpoints throw `notFound`/`notProvided` (they aren't opened by a
/// journey). In `offline` mode (`FLYFUN_MOCK_OFFLINE=1`) the repo reports itself
/// as serving a cached list (`CacheStatusReporting`), so the flight list shows
/// the offline banner + read-only behaviour without a real cache or network.
final class FixtureBriefingRepository: BriefingRepository, CacheStatusReporting {

    enum FixtureError: Error, CustomStringConvertible {
        case notProvided(String)
        var description: String { if case .notProvided(let m) = self { "fixture not provided: \(m)" } else { "" } }
    }

    /// Simulated offline state (`CacheStatusReporting`). `nonisolated let` so the
    /// flight list can read it synchronously, like `CachingBriefingRepository`.
    nonisolated let isServingCachedFlights: Bool

    init(offline: Bool = false) {
        self.isServingCachedFlights = offline
    }

    /// Offline, `fixture-1` is the one flight with a "downloaded" pack, so it
    /// stays tappable while the rest of the list is read-only. Empty when online.
    func offlineReadyFlightIds() async -> Set<String> {
        isServingCachedFlights ? [FixtureBriefingData.flightId] : []
    }

    private static func decodeFlights() -> [FlightResponse] {
        let json = """
        [
          {
            "id": "fixture-1", "user_id": "uitest", "route_name": "LFMD LFML",
            "waypoints": ["LFMD", "LFML"], "departure_time": "2099-07-01T09:00:00Z",
            "target_date": "2099-07-01", "target_time_utc": 900,
            "cruise_altitude_ft": 8000, "flight_ceiling_ft": 13000, "flight_duration_hours": 1.5,
            "private": false, "auto_refresh": false, "created_at": "2099-06-25T09:00:00Z"
          },
          {
            "id": "fixture-2", "user_id": "uitest", "route_name": "EGTF LFAT",
            "waypoints": ["EGTF", "LFAT"], "departure_time": "2099-07-03T13:00:00Z",
            "target_date": "2099-07-03", "target_time_utc": 1300,
            "cruise_altitude_ft": 6000, "flight_ceiling_ft": 11000, "flight_duration_hours": 2.0,
            "private": false, "auto_refresh": false, "created_at": "2099-06-25T09:00:00Z"
          }
        ]
        """
        // try! on purpose: this fixture must always decode. If a model change
        // breaks it, fail loudly here rather than silently emptying the list and
        // making every UI journey fail with a misleading "no flights" message.
        return try! JSONDecoder.weatherBrief.decode([FlightResponse].self, from: Data(json.utf8))
    }

    private let flightsFixture = decodeFlights()
    /// Flights created during a journey via `createFlight`, so the new flight
    /// shows up on the next `flights()` reload.
    private var createdFlights: [FlightResponse] = []

    // MARK: Served

    func flights() async throws -> [FlightResponse] { flightsFixture + createdFlights }

    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse {
        let flight = FlightResponse(
            id: "created-\(createdFlights.count + 1)",
            userId: "uitest", profileId: nil, aircraftId: request.aircraftId, aircraft: nil,
            routeName: request.waypoints.joined(separator: " "),
            waypoints: request.waypoints,
            departureTime: request.departureTime,
            targetDate: "2099-07-05", targetTimeUtc: 1000,
            cruiseAltitudeFt: request.cruiseAltitudeFt ?? 8000,
            flightCeilingFt: request.flightCeilingFt ?? 13000,
            flightDurationHours: request.flightDurationHours ?? 2.0,
            private: false, autoRefresh: false, autoRefreshHour: nil,
            createdAt: "2099-06-25T09:00:00Z", latestBriefing: nil, coverage: nil, role: nil,
            flexibility: request.flexibility, altDepartureTime: nil
        )
        createdFlights.append(flight)
        return flight
    }
    func aircraft() async throws -> [AircraftResponse] { [] }
    func profiles() async throws -> [ProfileResponse] { [] }
    func usageSummary() async throws -> UsageSummaryResponse {
        // No usage in fixtures — report "never scanned" so the explainer shows.
        try JSONDecoder.weatherBrief.decode(UsageSummaryResponse.self, from: Data("{}".utf8))
    }
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] { [] }
    func fetchPireps(flightId: String) async throws -> PirepListResponse { PirepListResponse(items: [], count: 0) }
    func refreshStream(flightId: String, source: RefreshSource) async -> AsyncThrowingStream<RefreshEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    // MARK: Briefing pack — served for `fixture-1` (#318)

    /// The one fixture flight that carries a full briefing. Endpoints below serve
    /// `FixtureBriefingData` for it and throw `notFound` for anything else — a
    /// created/other flight then follows the normal "no pack yet" path rather
    /// than rendering fixture-1's data under the wrong route.
    private func isBriefed(_ flightId: String) -> Bool { flightId == FixtureBriefingData.flightId }

    func latestPack(flightId: String) async throws -> PackMetaResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.pack
    }
    func packs(flightId: String) async throws -> [PackMetaResponse] {
        isBriefed(flightId) ? FixtureBriefingData.packs : []
    }
    func airportWeather(icao: String, day: Int, hour: Int) async throws -> AirportWeatherResponse {
        throw FixtureError.notProvided("airportWeather")
    }
    func forecastMap(day: Int, hour: Int) async throws -> ForecastMapResponse {
        throw FixtureError.notProvided("forecastMap")
    }
    func forecastDays() async throws -> ForecastDaysResponse {
        throw FixtureError.notProvided("forecastDays")
    }
    func frequentAirports() async throws -> FrequentAirportsResponse {
        throw FixtureError.notProvided("frequentAirports")
    }
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.advisories
    }
    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse {
        guard isBriefed(flightId), advisoryId == "convective" else { throw FixtureError.notProvided("advisoryDetail(\(advisoryId))") }
        return FixtureBriefingData.convectiveDetail
    }
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.digest
    }
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.snapshot
    }
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.routeAnalyses
    }
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.elevation
    }

    // MARK: Not yet needed by a journey

    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> UpdateFlightResponse { throw FixtureError.notProvided("updateFlight") }
    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse { throw FixtureError.notProvided("createAircraft") }
    func parseFpl(_ text: String) async throws -> ParseFplResponse { throw FixtureError.notProvided("parseFpl") }
    /// Echo the typed tokens back as a clean interpretation. The create/edit flow
    /// now interprets the route on the server *at submit time* before it will
    /// create (`AddFlightViewModel.interpretRouteForSubmit`), so a fixture that
    /// throws here strands the add-flight journey on the form. Fixtures have no
    /// server to resolve Field-15 syntax, and the journey types plain ICAO idents,
    /// so a verbatim echo (nothing skipped / off-route → `isClean`) resolves to
    /// `.ready` and lets `performCreate` proceed.
    func interpretRoute(rawRoute: String) async throws -> InterpretRouteResponse {
        let tokens = rawRoute
            .split(whereSeparator: { $0 == " " || $0 == "\n" })
            .map { $0.uppercased() }
        let waypoints = tokens.map {
            RouteWaypointInfo(icao: $0, name: $0, lat: 0, lon: 0, timezone: "UTC")
        }
        return InterpretRouteResponse(
            originalTokens: tokens,
            interpreted: tokens,
            skipped: [],
            offRoute: [],
            waypoints: waypoints
        )
    }
    func routeDistance(waypoints: [String]) async throws -> RouteDistanceResponse { throw FixtureError.notProvided("routeDistance") }
    func autorouterRoutes(limit: Int) async throws -> [AutorouterRoute] { [] }
    func recalculateAdvisories(flightId: String, timestamp: String, cruiseAltitudeFt: Int?) async throws { throw FixtureError.notProvided("recalculateAdvisories") }
    func timeOptions(flightId: String, timestamp: String) async throws -> TimeOptionsResponse { throw FixtureError.notProvided("timeOptions") }
    func confirmTimeOption(flightId: String, timestamp: String, departureTime: String) async throws { throw FixtureError.notProvided("confirmTimeOption") }
    func rescanTimeOptions(flightId: String, timestamp: String) async throws { throw FixtureError.notProvided("rescanTimeOptions") }
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data { throw FixtureError.notProvided("skewtImage") }
    func grametImage(flightId: String, timestamp: String) async throws -> Data { throw FixtureError.notProvided("grametImage") }
    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse { throw FixtureError.notProvided("soundingProfile") }
    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse { throw FixtureError.notProvided("refreshStatus") }
    func activeRefreshes() async throws -> [ActiveRefreshResponse] { [] }
    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse { throw FixtureError.notProvided("submitPirep") }
    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse] { throw FixtureError.notProvided("submitPirepsBatch") }
}
#endif
