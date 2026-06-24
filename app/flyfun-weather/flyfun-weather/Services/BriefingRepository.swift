import Foundation

/// Abstraction over briefing data access.
protocol BriefingRepository: Sendable {
    func flights() async throws -> [FlightResponse]
    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse
    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> FlightResponse
    func parseFpl(_ text: String) async throws -> ParseFplResponse
    func packs(flightId: String) async throws -> [PackMetaResponse]
    func latestPack(flightId: String) async throws -> PackMetaResponse
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse
    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data
    func grametImage(flightId: String, timestamp: String) async throws -> Data
    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse
    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error>
    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse

    // PIREP
    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse
    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse]
    func fetchPireps(flightId: String) async throws -> PirepListResponse
}

/// Online-only implementation — every call hits the API.
final class OnlineBriefingRepository: BriefingRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func flights() async throws -> [FlightResponse] {
        try await client.request("/api/flights")
    }

    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/flights", method: "POST", body: body)
    }

    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> FlightResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        // Server returns UpdateFlightResponse (FlightResponse + invalidation hint);
        // the extra key is ignored when decoding into FlightResponse.
        return try await client.request("/api/flights/\(flightId)", method: "PATCH", body: body)
    }

    func parseFpl(_ text: String) async throws -> ParseFplResponse {
        let body = try JSONEncoder.weatherBrief.encode(ParseFplRequest(fplText: text))
        return try await client.request("/api/flights/parse-fpl", method: "POST", body: body)
    }

    func packs(flightId: String) async throws -> [PackMetaResponse] {
        try await client.request("/api/flights/\(flightId)/packs")
    }

    func latestPack(flightId: String) async throws -> PackMetaResponse {
        try await client.request("/api/flights/\(flightId)/packs/latest")
    }

    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/advisories")
    }

    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/advisories/\(advisoryId)/detail")
    }

    func digest(flightId: String, timestamp: String) async throws -> DigestResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/digest/json")
    }

    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/snapshot")
    }

    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/route-analyses")
    }

    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/elevation")
    }

    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data {
        try await client.requestData("/api/flights/\(flightId)/packs/\(timestamp)/skewt/\(icao)/\(model)")
    }

    func grametImage(flightId: String, timestamp: String) async throws -> Data {
        try await client.requestData("/api/flights/\(flightId)/packs/\(timestamp)/gramet.png")
    }

    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/sounding-profile/\(pointIndex)/\(model)")
    }

    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error> {
        await client.streamSSE("/api/flights/\(flightId)/packs/refresh/stream")
    }

    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse {
        try await client.request("/api/flights/\(flightId)/packs/refresh/status")
    }

    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/pireps", method: "POST", body: body)
    }

    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse] {
        let body = try JSONEncoder.weatherBrief.encode(requests)
        return try await client.request("/api/pireps/batch", method: "POST", body: body)
    }

    func fetchPireps(flightId: String) async throws -> PirepListResponse {
        let encoded = flightId.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? flightId
        return try await client.requestURL("/api/pireps?flight_id=\(encoded)")
    }
}
