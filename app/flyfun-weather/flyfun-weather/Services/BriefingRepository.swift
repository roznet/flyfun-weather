import Foundation

/// Abstraction over briefing data access.
protocol BriefingRepository: Sendable {
    func flights() async throws -> [FlightResponse]
    func packs(flightId: String) async throws -> [PackMetaResponse]
    func latestPack(flightId: String) async throws -> PackMetaResponse
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data
    func grametImage(flightId: String, timestamp: String) async throws -> Data
    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse
    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error>
    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse
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

    func packs(flightId: String) async throws -> [PackMetaResponse] {
        try await client.request("/api/flights/\(flightId)/packs")
    }

    func latestPack(flightId: String) async throws -> PackMetaResponse {
        try await client.request("/api/flights/\(flightId)/packs/latest")
    }

    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/advisories")
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
}
