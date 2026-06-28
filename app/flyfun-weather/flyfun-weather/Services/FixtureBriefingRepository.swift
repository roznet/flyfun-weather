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
final class FixtureBriefingRepository: BriefingRepository, @unchecked Sendable {

    enum FixtureError: Error, CustomStringConvertible {
        case notProvided(String)
        var description: String { if case .notProvided(let m) = self { "fixture not provided: \(m)" } else { "" } }
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
        return (try? JSONDecoder.weatherBrief.decode([FlightResponse].self, from: Data(json.utf8))) ?? []
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
            createdAt: "2099-06-25T09:00:00Z", role: nil
        )
        createdFlights.append(flight)
        return flight
    }
    func aircraft() async throws -> [AircraftResponse] { [] }
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] { [] }
    func packs(flightId: String) async throws -> [PackMetaResponse] { [] }
    func fetchPireps(flightId: String) async throws -> PirepListResponse { PirepListResponse(items: [], count: 0) }
    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    // MARK: Not yet needed by a journey

    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> UpdateFlightResponse { throw FixtureError.notProvided("updateFlight") }
    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse { throw FixtureError.notProvided("createAircraft") }
    func parseFpl(_ text: String) async throws -> ParseFplResponse { throw FixtureError.notProvided("parseFpl") }
    func latestPack(flightId: String) async throws -> PackMetaResponse { throw FixtureError.notProvided("latestPack") }
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse { throw FixtureError.notProvided("advisories") }
    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse { throw FixtureError.notProvided("advisoryDetail") }
    func recalculateAdvisories(flightId: String, timestamp: String, cruiseAltitudeFt: Int?) async throws { throw FixtureError.notProvided("recalculateAdvisories") }
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse { throw FixtureError.notProvided("digest") }
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse { throw FixtureError.notProvided("snapshot") }
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse { throw FixtureError.notProvided("routeAnalyses") }
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse { throw FixtureError.notProvided("elevation") }
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data { throw FixtureError.notProvided("skewtImage") }
    func grametImage(flightId: String, timestamp: String) async throws -> Data { throw FixtureError.notProvided("grametImage") }
    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse { throw FixtureError.notProvided("soundingProfile") }
    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse { throw FixtureError.notProvided("refreshStatus") }
    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse { throw FixtureError.notProvided("submitPirep") }
    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse] { throw FixtureError.notProvided("submitPirepsBatch") }
}
#endif
