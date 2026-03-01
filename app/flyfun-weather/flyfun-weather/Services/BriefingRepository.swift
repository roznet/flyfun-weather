import Foundation

/// Abstraction over briefing data access. Phase 1 = online only; Phase 2 adds caching.
protocol BriefingRepository: Sendable {
    func flights() async throws -> [FlightResponse]
    func latestPack(flightId: String) async throws -> PackMetaResponse
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data
    func grametImage(flightId: String, timestamp: String) async throws -> Data
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
}
