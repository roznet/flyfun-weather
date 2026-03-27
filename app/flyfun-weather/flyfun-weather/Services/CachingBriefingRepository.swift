import Foundation
import OSLog

/// Download state for a pack.
enum DownloadState: Equatable {
    case notDownloaded
    case downloading(progress: Double)
    case downloaded
    case error(String)
}

/// Repository that serves cached data when available, falls back to network.
/// Caching is explicit — only packs downloaded via `downloadPack()` are cached.
final class CachingBriefingRepository: BriefingRepository {
    private let client: APIClient
    private let online: OnlineBriefingRepository
    let cache: BriefingCacheStore

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "CachingRepo")

    /// Endpoint suffixes used in API paths and cache keys.
    private static let endpointPaths: [String: String] = [
        "advisories": "advisories",
        "digest": "digest/json",
        "snapshot": "snapshot",
        "route-analyses": "route-analyses",
        "elevation": "elevation",
    ]

    init(client: APIClient, online: OnlineBriefingRepository, cache: BriefingCacheStore) {
        self.client = client
        self.online = online
        self.cache = cache
    }

    // MARK: - Always online (metadata)

    func flights() async throws -> [FlightResponse] {
        try await online.flights()
    }

    func packs(flightId: String) async throws -> [PackMetaResponse] {
        try await online.packs(flightId: flightId)
    }

    func latestPack(flightId: String) async throws -> PackMetaResponse {
        try await online.latestPack(flightId: flightId)
    }

    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error> {
        await online.refreshStream(flightId: flightId)
    }

    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse {
        try await online.refreshStatus(flightId: flightId)
    }

    // MARK: - Cache-aware data endpoints

    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "advisories")
    }

    func digest(flightId: String, timestamp: String) async throws -> DigestResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "digest")
    }

    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "snapshot")
    }

    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "route-analyses")
    }

    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "elevation")
    }

    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse {
        try await cachedOrFetch(
            flightId: flightId, timestamp: timestamp,
            endpoint: "sounding-\(pointIndex)-\(model)",
            pathSuffix: "sounding-profile/\(pointIndex)/\(model)",
            writeThrough: true
        )
    }

    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data {
        try await online.skewtImage(flightId: flightId, timestamp: timestamp, icao: icao, model: model)
    }

    func grametImage(flightId: String, timestamp: String) async throws -> Data {
        try await online.grametImage(flightId: flightId, timestamp: timestamp)
    }

    // MARK: - Explicit download / delete

    /// Download all pack data to disk for offline access via a single bundle request.
    func downloadPack(
        flightId: String,
        timestamp: String,
        flightTitle: String,
        assessment: String?,
        progress: @Sendable @escaping (Double) -> Void
    ) async throws {
        progress(0.1)

        // Single request fetches everything (gzip-compressed by server)
        let path = "/api/flights/\(flightId)/packs/\(timestamp)/bundle"
        let data = try await client.requestData(path)
        progress(0.7)

        // Parse the bundle: { "endpoint-name": { ... }, ... }
        guard let bundle = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.decodingError(NSError(domain: "BundleDownload", code: 0, userInfo: [NSLocalizedDescriptionKey: "Invalid bundle format"]))
        }

        var totalBytes: Int64 = 0
        var downloaded: Set<String> = []

        for (i, (endpoint, value)) in bundle.enumerated() {
            do {
                let entryData = try JSONSerialization.data(withJSONObject: value)
                try await cache.writeData(entryData, flightId: flightId, timestamp: timestamp, endpoint: endpoint)
                totalBytes += Int64(entryData.count)
                downloaded.insert(endpoint)
            } catch {
                Self.logger.warning("Failed to cache \(endpoint): \(error)")
            }
            progress(0.7 + 0.3 * Double(i + 1) / Double(bundle.count))
        }

        await cache.registerDownload(
            flightId: flightId,
            timestamp: timestamp,
            flightTitle: flightTitle,
            assessment: assessment,
            endpoints: downloaded,
            totalBytes: totalBytes
        )
        Self.logger.info("Downloaded pack \(flightId)/\(timestamp): \(downloaded.count) endpoints, \(totalBytes) bytes")
    }

    func deletePack(flightId: String, timestamp: String) async {
        await cache.deletePack(flightId: flightId, timestamp: timestamp)
    }

    func isPackCached(flightId: String, timestamp: String) async -> Bool {
        await cache.isPackCached(flightId: flightId, timestamp: timestamp)
    }

    func cachedPacks() async -> [CachedPackEntry] {
        await cache.cachedPacks()
    }

    // MARK: - Private

    private func cachedOrFetch<T: Decodable>(flightId: String, timestamp: String, endpoint: String) async throws -> T {
        let pathSuffix = Self.endpointPaths[endpoint]!
        return try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: endpoint, pathSuffix: pathSuffix)
    }

    private func cachedOrFetch<T: Decodable>(
        flightId: String, timestamp: String, endpoint: String, pathSuffix: String, writeThrough: Bool = false
    ) async throws -> T {
        // Check cache first
        if let data = await cache.readData(flightId: flightId, timestamp: timestamp, endpoint: endpoint) {
            do {
                return try JSONDecoder.weatherBrief.decode(T.self, from: data)
            } catch {
                Self.logger.warning("Cache decode failed for \(endpoint), fetching online: \(error)")
            }
        }

        // Fall back to network
        let path = "/api/flights/\(flightId)/packs/\(timestamp)/\(pathSuffix)"
        let data = try await client.requestData(path)
        if writeThrough {
            try? await cache.writeData(data, flightId: flightId, timestamp: timestamp, endpoint: endpoint)
        }
        return try JSONDecoder.weatherBrief.decode(T.self, from: data)
    }
}
