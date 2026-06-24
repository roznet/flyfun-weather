import Foundation
import OSLog

/// Download state for a pack.
/// `totalBytes <= 0` means the size is unknown (server didn't send the length header).
enum DownloadState: Equatable {
    case notDownloaded
    case downloading(progress: Double, receivedBytes: Int64, totalBytes: Int64)
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

    /// Whether the last `flights()` call was served from cache (offline).
    private(set) var isServingCachedFlights = false

    init(client: APIClient, online: OnlineBriefingRepository, cache: BriefingCacheStore) {
        self.client = client
        self.online = online
        self.cache = cache
    }

    // MARK: - Flight creation (pass-through, no caching)

    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse {
        try await online.createFlight(request)
    }

    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> FlightResponse {
        // Editing is online-only (mode-A default, §4.4).
        try await online.updateFlight(flightId: flightId, request: request)
    }

    func parseFpl(_ text: String) async throws -> ParseFplResponse {
        try await online.parseFpl(text)
    }

    // MARK: - Metadata with offline fallback

    func flights() async throws -> [FlightResponse] {
        do {
            let flights = try await online.flights()
            isServingCachedFlights = false
            // Cache for offline use
            if let data = try? JSONEncoder.weatherBrief.encode(flights) {
                try? await cache.writeMetadata(data, name: "flights")
            }
            return flights
        } catch {
            Self.logger.warning("Online flights() failed: \(error)")
            isServingCachedFlights = true
            // Fallback 1: cached flights.json
            if let data = await cache.readMetadata(name: "flights"),
               let cached = try? JSONDecoder.weatherBrief.decode([FlightResponse].self, from: data) {
                Self.logger.info("Serving \(cached.count) flights from cache (offline)")
                return cached
            }
            // Fallback 2: recover from per-flight cached data (downloaded packs)
            let recovered = await recoverFlightsFromCache()
            if !recovered.isEmpty {
                Self.logger.info("Recovered \(recovered.count) flights from download cache")
                return recovered
            }
            throw error
        }
    }

    func packs(flightId: String) async throws -> [PackMetaResponse] {
        do {
            let packs = try await online.packs(flightId: flightId)
            if let data = try? JSONEncoder.weatherBrief.encode(packs) {
                try? await cache.writeFlightMetadata(data, flightId: flightId, name: "packs")
            }
            return packs
        } catch {
            if let data = await cache.readFlightMetadata(flightId: flightId, name: "packs"),
               let cached = try? JSONDecoder.weatherBrief.decode([PackMetaResponse].self, from: data) {
                Self.logger.info("Serving pack history from cache for \(flightId) (offline)")
                return cached
            }
            throw error
        }
    }

    func latestPack(flightId: String) async throws -> PackMetaResponse {
        do {
            let pack = try await online.latestPack(flightId: flightId)
            if let data = try? JSONEncoder.weatherBrief.encode(pack) {
                try? await cache.writeFlightMetadata(data, flightId: flightId, name: "latest-pack")
            }
            return pack
        } catch {
            // Fallback 1: cached latest-pack metadata
            if let data = await cache.readFlightMetadata(flightId: flightId, name: "latest-pack"),
               let cached = try? JSONDecoder.weatherBrief.decode(PackMetaResponse.self, from: data) {
                Self.logger.info("Serving latest pack from cache for \(flightId) (offline)")
                return cached
            }
            // Fallback 2: pack metadata saved during download
            if let data = await cache.readFlightMetadata(flightId: flightId, name: "pack-meta"),
               let cached = try? JSONDecoder.weatherBrief.decode(PackMetaResponse.self, from: data) {
                Self.logger.info("Serving pack meta from download cache for \(flightId)")
                return cached
            }
            throw error
        }
    }

    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error> {
        await online.refreshStream(flightId: flightId)
    }

    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse {
        try await online.refreshStatus(flightId: flightId)
    }

    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse {
        try await online.submitPirep(request)
    }

    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse] {
        try await online.submitPirepsBatch(requests)
    }

    func fetchPireps(flightId: String) async throws -> PirepListResponse {
        // Always online — PIREPs are live community data, not cached
        try await online.fetchPireps(flightId: flightId)
    }

    // MARK: - Cache-aware data endpoints

    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "advisories")
    }

    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse {
        try await cachedOrFetch(
            flightId: flightId, timestamp: timestamp,
            endpoint: "advisory-detail-\(advisoryId)",
            pathSuffix: "advisories/\(advisoryId)/detail",
            writeThrough: true
        )
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

    // MARK: - Offline recovery

    /// Cache flight data for offline recovery (called when navigating to a flight).
    func cacheFlightData(_ flight: FlightResponse) async {
        if let data = try? JSONEncoder.weatherBrief.encode(flight) {
            try? await cache.writeFlightMetadata(data, flightId: flight.id, name: "flight")
        }
    }

    // MARK: - Explicit download / delete

    /// Download all pack data to disk for offline access via a single bundle request.
    func downloadPack(
        flightId: String,
        timestamp: String,
        flightTitle: String,
        assessment: String?,
        packMeta: PackMetaResponse? = nil,
        progress: @Sendable @escaping (_ fraction: Double, _ receivedBytes: Int64, _ totalBytes: Int64) -> Void
    ) async throws {
        progress(0, 0, 0)

        // Single streaming request fetches everything (gzip-compressed by server).
        // The network transfer is the slow part on poor connections; the percentage
        // tracks bytes received. The local disk writes below are near-instant.
        let path = "/api/flights/\(flightId)/packs/\(timestamp)/bundle"
        let data = try await client.requestDataStreaming(path) { received, total in
            let fraction = total > 0 ? min(Double(received) / Double(total), 1.0) : 0
            progress(fraction, received, total)
        }

        // Parse the bundle: { "endpoint-name": { ... }, ... }
        guard let bundle = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.decodingError(NSError(domain: "BundleDownload", code: 0, userInfo: [NSLocalizedDescriptionKey: "Invalid bundle format"]))
        }

        var diskBytes: Int64 = 0
        var downloaded: Set<String> = []

        for (endpoint, value) in bundle {
            do {
                let entryData = try JSONSerialization.data(withJSONObject: value)
                try await cache.writeData(entryData, flightId: flightId, timestamp: timestamp, endpoint: endpoint)
                diskBytes += Int64(entryData.count)
                downloaded.insert(endpoint)
            } catch {
                Self.logger.warning("Failed to cache \(endpoint): \(error)")
            }
        }

        await cache.registerDownload(
            flightId: flightId,
            timestamp: timestamp,
            flightTitle: flightTitle,
            assessment: assessment,
            endpoints: downloaded,
            totalBytes: diskBytes
        )
        // Cache pack metadata for offline latestPack() recovery
        if let packMeta, let metaData = try? JSONEncoder.weatherBrief.encode(packMeta) {
            try? await cache.writeFlightMetadata(metaData, flightId: flightId, name: "pack-meta")
        }
        Self.logger.info("Downloaded pack \(flightId)/\(timestamp): \(downloaded.count) endpoints, \(diskBytes) bytes")
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

    /// Recover flight list from per-flight cached data when flights.json is unavailable.
    private func recoverFlightsFromCache() async -> [FlightResponse] {
        let entries = await cache.cachedPacks()
        let flightIds = Set(entries.map(\.flightId))
        var flights: [FlightResponse] = []
        for id in flightIds {
            if let data = await cache.readFlightMetadata(flightId: id, name: "flight"),
               let flight = try? JSONDecoder.weatherBrief.decode(FlightResponse.self, from: data) {
                flights.append(flight)
            }
        }
        return flights
    }

    private func cachedOrFetch<T: Decodable>(flightId: String, timestamp: String, endpoint: String) async throws -> T {
        let pathSuffix = Self.endpointPaths[endpoint]!
        return try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: endpoint, pathSuffix: pathSuffix)
    }

    private func cachedOrFetch<T: Decodable>(
        flightId: String, timestamp: String, endpoint: String, pathSuffix: String, writeThrough: Bool = false
    ) async throws -> T {
        // Check cache first (exact timestamp)
        if let data = await cache.readData(flightId: flightId, timestamp: timestamp, endpoint: endpoint) {
            do {
                return try JSONDecoder.weatherBrief.decode(T.self, from: data)
            } catch {
                Self.logger.warning("Cache decode failed for \(endpoint), fetching online: \(error)")
            }
        }

        // Try network
        do {
            let path = "/api/flights/\(flightId)/packs/\(timestamp)/\(pathSuffix)"
            let data = try await client.requestData(path)
            if writeThrough {
                try? await cache.writeData(data, flightId: flightId, timestamp: timestamp, endpoint: endpoint)
            }
            return try JSONDecoder.weatherBrief.decode(T.self, from: data)
        } catch {
            // Network failed — try any other cached timestamp for this flight
            let entries = await cache.cachedPacks()
            for entry in entries where entry.flightId == flightId && entry.timestamp != timestamp {
                if let data = await cache.readData(flightId: entry.flightId, timestamp: entry.timestamp, endpoint: endpoint),
                   let decoded = try? JSONDecoder.weatherBrief.decode(T.self, from: data) {
                    Self.logger.info("Serving \(endpoint) from cached pack \(entry.timestamp) (offline fallback)")
                    return decoded
                }
            }
            throw error
        }
    }
}
