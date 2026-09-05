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
    /// Flights removed during a journey, filtered out of subsequent loads. Two
    /// producers, same need: bulk delete (whose whole assertion is that the
    /// selected rows disappear after the confirmation, so it is served for real
    /// rather than `notProvided`) and `moveFlight`, where the source flight stops
    /// being listed exactly as it does server-side — a move is create-new +
    /// delete-old in one transaction.
    private var deletedFlightIds: Set<String> = []

    // MARK: Served

    func flights() async throws -> [FlightResponse] { allFlights }

    /// Every fixture + journey-created flight, minus anything deleted or moved.
    private var allFlights: [FlightResponse] {
        (flightsFixture + createdFlights).filter { !deletedFlightIds.contains($0.id) }
    }

    func flight(id: String) async throws -> FlightResponse {
        guard let match = allFlights.first(where: { $0.id == id }) else {
            throw APIError.notFound
        }
        return match
    }

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
            flexibility: request.flexibility, altDepartureTime: nil,
            // Stored like the server does, so a create-then-edit journey sees the
            // Field-15 string the edit form now seeds its route input from.
            rawRoute: request.rawRoute
        )
        createdFlights.append(flight)
        return flight
    }
    /// Served (not `notProvided`): the multi-select journey's whole assertion is
    /// that the selected rows are gone after the confirmation, so the fixture has
    /// to actually forget them. Unknown ids come back in `notFound`, like the
    /// server's owner-scoped behaviour.
    func bulkDeleteFlights(ids: [String]) async throws -> BulkDeleteResponse {
        let known = Set(allFlights.map(\.id))
        let deleted = ids.filter { known.contains($0) }
        deletedFlightIds.formUnion(deleted)
        return BulkDeleteResponse(deleted: deleted, notFound: ids.filter { !known.contains($0) })
    }

    /// Mirror the server's move semantics well enough for the structural-edit
    /// journey (#552): the source flight disappears and a *new* flight takes its
    /// place, so the list ends up with the same number of rows and the pilot lands
    /// on the new one. Duplicate needs nothing extra — it goes through
    /// `createFlight`, which already leaves the original in place.
    func moveFlight(flightId: String, request: MoveFlightRequest) async throws -> FlightResponse {
        guard let source = allFlights.first(where: { $0.id == flightId }) else {
            throw APIError.notFound
        }
        let movedWaypoints = request.waypoints ?? source.waypoints
        // The server's three-way `raw_route` rule (`api/flights.py::move_flight`),
        // which the rest of this synthesis already mirrors field for field:
        //   • sent in the body        → store it
        //   • omitted, route changed  → clear it (the old string would lie)
        //   • omitted, route unchanged → keep the source's
        // Hard-coding nil here would give a future raw-route-across-move test a
        // false pass from the fixture rather than exercising the client.
        let movedRawRoute: String?
        if let requested = request.rawRoute {
            movedRawRoute = requested
        } else if movedWaypoints != source.waypoints {
            movedRawRoute = nil
        } else {
            movedRawRoute = source.rawRoute
        }
        let moved = FlightResponse(
            id: "moved-\(flightId)",
            userId: source.userId, profileId: source.profileId,
            aircraftId: source.aircraftId, aircraft: source.aircraft,
            routeName: movedWaypoints.joined(separator: " "),
            waypoints: movedWaypoints,
            departureTime: request.departureTime ?? source.departureTime,
            targetDate: String((request.departureTime ?? source.departureTime).prefix(10)),
            targetTimeUtc: source.targetTimeUtc,
            cruiseAltitudeFt: request.cruiseAltitudeFt ?? source.cruiseAltitudeFt,
            flightCeilingFt: request.flightCeilingFt ?? source.flightCeilingFt,
            flightDurationHours: request.flightDurationHours ?? source.flightDurationHours,
            private: source.private, autoRefresh: source.autoRefresh,
            autoRefreshHour: source.autoRefreshHour,
            createdAt: source.createdAt, latestBriefing: nil, coverage: nil, role: source.role,
            flexibility: source.flexibility, altDepartureTime: source.altDepartureTime,
            rawRoute: movedRawRoute
        )
        deletedFlightIds.insert(flightId)
        createdFlights.append(moved)
        return moved
    }

    func triggerRefresh(flightId: String) async throws {}

    func aircraft() async throws -> [AircraftResponse] { [] }
    func profiles() async throws -> [ProfileResponse] { [] }
    func usageSummary() async throws -> UsageSummaryResponse {
        // No usage in fixtures — report "never scanned" so the explainer shows.
        try JSONDecoder.weatherBrief.decode(UsageSummaryResponse.self, from: Data("{}".utf8))
    }
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] { [] }
    func fetchPireps(flightId: String) async throws -> PirepListResponse { PirepListResponse(items: [], count: 0) }
    func fetchDebrief(flightId: String) async throws -> DebriefResponse { throw APIError.notFound }
    func upsertDebrief(flightId: String, request: DebriefRequest) async throws -> DebriefResponse {
        throw FixtureError.notProvided("upsertDebrief")
    }
    func deleteDebrief(flightId: String) async throws {}
    func submitDigestFeedback(_ request: DigestFeedbackRequest) async throws {}
    func submitGeneralFeedback(_ request: GeneralFeedbackRequest) async throws {}
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
        return FixtureBriefingData.snapshotWithObservedMotion()
    }
    /// Mirror the production lifecycle boundary for the DEBUG UI fixture: this
    /// is an existing-snapshot read carrying a fresh explicit capability value,
    /// not a fabricated motion computation or persisted authorization.
    func freshSnapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse {
        guard isBriefed(flightId) else { throw APIError.notFound }
        return FixtureBriefingData.snapshotWithObservedMotion()
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
    func deleteFlight(id: String) async throws { throw FixtureError.notProvided("deleteFlight") }
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
            RouteWaypointInfo(icao: $0, name: $0, lat: 0, lon: 0, timezone: Self.fixtureTimeZone(for: $0))
        }
        return InterpretRouteResponse(
            originalTokens: tokens,
            interpreted: tokens,
            skipped: [],
            offRoute: [],
            waypoints: waypoints
        )
    }
    /// Plausible IANA zone for a fixture waypoint, by ICAO prefix. The real
    /// resolver returns the airport's own zone; a flat "UTC" would leave the
    /// departure timezone picker with a single option and so make the
    /// local-day-vs-UTC-day distinction (#552) untestable in a journey.
    private static func fixtureTimeZone(for icao: String) -> String {
        switch icao.prefix(2) {
        case "LF": return "Europe/Paris"
        case "EG": return "Europe/London"
        case "ED": return "Europe/Berlin"
        default: return "UTC"
        }
    }

    func flightByShareCode(_ code: String) async throws -> FlightResponse { throw FixtureError.notProvided("flightByShareCode") }
    func subscribeFlight(id: String) async throws { throw FixtureError.notProvided("subscribeFlight") }
    func unsubscribeFlight(id: String) async throws { throw FixtureError.notProvided("unsubscribeFlight") }
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
