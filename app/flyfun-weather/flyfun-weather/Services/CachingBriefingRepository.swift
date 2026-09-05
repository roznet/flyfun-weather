import Foundation
import OSLog
#if DEBUG
import FlyFunCommon  // only the DEBUG cache-testing factory below needs it
#endif

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
final class CachingBriefingRepository: BriefingRepository, CacheStatusReporting {
    private let client: APIClient
    // Protocol type (not the concrete `OnlineBriefingRepository`) so the online
    // layer can be faulted with a test double — the seam ServiceTests flagged as
    // a follow-up. Production still injects `OnlineBriefingRepository`.
    private let online: any BriefingRepository
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

    init(client: APIClient, online: any BriefingRepository, cache: BriefingCacheStore) {
        self.client = client
        self.online = online
        self.cache = cache
    }

    #if DEBUG
    /// Test-only factory: build a repository over an injected cache directory
    /// whose network layer is never exercised (the base URL is intentionally
    /// unreachable). Cache/eviction unit tests drive only the on-disk path
    /// (`pruneStalePacks`, download bookkeeping) and never make a request. This
    /// lives here rather than the test target because the `APIClient` auth types
    /// come from `FlyFunCommon`, which the test target doesn't link.
    static func makeForCacheTesting(cache: BriefingCacheStore) -> CachingBriefingRepository {
        let tokenStore = KeychainBearerTokenStore(service: "aero.flyfun.weather.tests")
        let rollingSession = RollingBearerSession(store: tokenStore, onUnauthorized: {})
        let client = APIClient(
            baseURL: URL(string: "https://tests.invalid")!,
            tokenStore: tokenStore,
            rollingSession: rollingSession
        )
        let online = OnlineBriefingRepository(client: client)
        return CachingBriefingRepository(client: client, online: online, cache: cache)
    }
    #endif

    // MARK: - Flight creation (pass-through, no caching)

    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse {
        try await online.createFlight(request)
    }

    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> UpdateFlightResponse {
        // Editing is online-only (mode-A default, §4.4).
        try await online.updateFlight(flightId: flightId, request: request)
    }

    /// Move server-side first; only once it confirms do we drop the source
    /// flight's local packs. The server deleted that flight, so its cached packs
    /// can never be refreshed or re-downloaded again — same reasoning (and same
    /// eviction order) as `deleteFlight` below.
    func moveFlight(flightId: String, request: MoveFlightRequest) async throws -> FlightResponse {
        let moved = try await online.moveFlight(flightId: flightId, request: request)
        await evictLocalCopies(of: [flightId])
        return moved
    }

    func triggerRefresh(flightId: String) async throws {
        // Online-only: queuing work on the server has no offline meaning.
        try await online.triggerRefresh(flightId: flightId)
    }

    /// Delete server-side first — only once the server confirms (204) do we drop
    /// the local copy, so a failed delete leaves a flight that still exists with
    /// its downloaded packs intact. After that the packs are unreachable (no
    /// refresh, no re-download), so evict them: index entries first, then the
    /// whole flight directory including the sidecars, matching the order the
    /// eviction sweep in `pruneStalePacks` uses.
    func deleteFlight(id: String) async throws {
        try await online.deleteFlight(id: id)
        await evictLocalCopies(of: [id])
    }

    /// Same contract as `deleteFlight`, for many ids at once: the server decides
    /// what actually went, and only then is the local copy dropped. Eviction is
    /// restricted to the ids the server confirmed in `deleted` — anything it put
    /// in `notFound` still exists (someone else's flight, or already gone), so
    /// its packs must not be touched.
    ///
    /// A long selection is split into chunks, and those chunks are not one
    /// transaction: if one fails, the earlier ones have still deleted flights
    /// server-side. That arrives as `BulkDeletePartialFailure`, whose confirmed
    /// ids are evicted before the error is rethrown — otherwise those packs would
    /// linger unreachable until the departure-date sweep reached them.
    func bulkDeleteFlights(ids: [String]) async throws -> BulkDeleteResponse {
        do {
            let response = try await online.bulkDeleteFlights(ids: ids)
            await evictLocalCopies(of: Set(response.deleted))
            return response
        } catch let failure as BulkDeletePartialFailure {
            await evictLocalCopies(of: Set(failure.partial.deleted))
            throw failure
        }
    }

    /// Drop every local trace of flights the server has confirmed deleted: index
    /// entries first, then each flight directory including its sidecars — the
    /// order the `pruneStalePacks` eviction sweep uses. One `cachedPacks()` read
    /// covers the whole batch.
    private func evictLocalCopies(of ids: Set<String>) async {
        guard !ids.isEmpty else { return }
        for entry in await cache.cachedPacks() where ids.contains(entry.flightId) {
            await cache.deletePack(flightId: entry.flightId, timestamp: entry.timestamp)
        }
        for id in ids {
            await cache.removeFlightDirectory(flightId: id)
        }
    }

    func aircraft() async throws -> [AircraftResponse] {
        try await online.aircraft()
    }

    func profiles() async throws -> [ProfileResponse] {
        try await online.profiles()
    }

    func usageSummary() async throws -> UsageSummaryResponse {
        // Online-only — usage is a live account query, never cached.
        try await online.usageSummary()
    }

    func interpretRoute(rawRoute: String) async throws -> InterpretRouteResponse {
        try await online.interpretRoute(rawRoute: rawRoute)
    }

    func routeDistance(waypoints: [String]) async throws -> RouteDistanceResponse {
        try await online.routeDistance(waypoints: waypoints)
    }

    func autorouterRoutes(limit: Int) async throws -> [AutorouterRoute] {
        try await online.autorouterRoutes(limit: limit)
    }

    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] {
        try await online.searchAircraftTypes(query)
    }

    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse {
        try await online.createAircraft(request)
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
            if let cached = await readCachedFlightsFromDisk() {
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

    /// Online-only single-flight fetch (Universal Link fallback for flights not
    /// in the list).
    ///
    /// Deliberately has no cached fallback. The sole caller reaches here exactly
    /// when the flight is absent from the loaded list — and offline, that list
    /// *is* the cached one — so searching `flights.json` could only ever miss.
    /// Worse, swallowing the error there would report a transient network failure
    /// as "private, deleted, or on a different account". Letting the error through
    /// lets the caller tell a 404 from an offline tap.
    func flight(id: String) async throws -> FlightResponse {
        try await online.flight(id: id)
    }

    /// Cached flight list from `flights.json`, if present. No network — lets the
    /// flight list paint instantly on cold start and revalidate in the
    /// background (#359), instead of only serving as an offline fallback.
    func cachedFlights() async -> [FlightResponse]? {
        await readCachedFlightsFromDisk()
    }

    /// Read + decode `flights.json` from disk. No network. Shared by the
    /// cold-start seed (`cachedFlights()`) and the offline fallback in
    /// `flights()`. Returns nil when absent or undecodable.
    private func readCachedFlightsFromDisk() async -> [FlightResponse]? {
        guard let data = await cache.readMetadata(name: "flights"),
              let cached = try? JSONDecoder.weatherBrief.decode([FlightResponse].self, from: data)
        else { return nil }
        return cached
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

    func airportWeather(icao: String, day: Int, hour: Int) async throws -> AirportWeatherResponse {
        // Online-only — airport weather isn't part of the offline pack bundle.
        try await online.airportWeather(icao: icao, day: day, hour: hour)
    }

    // Forecast map (#420) — all online-only; the map VM keeps its own in-memory
    // (day, hour) LRU, so there's nothing for the disk cache to add here.
    func forecastMap(day: Int, hour: Int) async throws -> ForecastMapResponse {
        try await online.forecastMap(day: day, hour: hour)
    }

    func forecastDays() async throws -> ForecastDaysResponse {
        try await online.forecastDays()
    }

    func frequentAirports() async throws -> FrequentAirportsResponse {
        try await online.frequentAirports()
    }

    // Flight sharing (#446) — always online: resolving a code, subscribing, and
    // unsubscribing are live account actions, never part of the offline bundle.
    func flightByShareCode(_ code: String) async throws -> FlightResponse {
        try await online.flightByShareCode(code)
    }

    func subscribeFlight(id: String) async throws {
        try await online.subscribeFlight(id: id)
    }

    func unsubscribeFlight(id: String) async throws {
        try await online.unsubscribeFlight(id: id)
    }

    func refreshStream(flightId: String, source: RefreshSource) async -> AsyncThrowingStream<RefreshEvent, Error> {
        await online.refreshStream(flightId: flightId, source: source)
    }

    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse {
        try await online.refreshStatus(flightId: flightId)
    }

    func activeRefreshes() async throws -> [ActiveRefreshResponse] {
        // Online-only — a live "which briefings are refreshing" query is never cached.
        try await online.activeRefreshes()
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

    // Debrief + digest feedback are always online (post-flight, on-the-ground
    // actions — not part of the offline briefing bundle).
    func fetchDebrief(flightId: String) async throws -> DebriefResponse {
        try await online.fetchDebrief(flightId: flightId)
    }

    func upsertDebrief(flightId: String, request: DebriefRequest) async throws -> DebriefResponse {
        try await online.upsertDebrief(flightId: flightId, request: request)
    }

    func deleteDebrief(flightId: String) async throws {
        try await online.deleteDebrief(flightId: flightId)
    }

    func submitDigestFeedback(_ request: DigestFeedbackRequest) async throws {
        try await online.submitDigestFeedback(request)
    }

    func submitGeneralFeedback(_ request: GeneralFeedbackRequest) async throws {
        try await online.submitGeneralFeedback(request)
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

    func recalculateAdvisories(flightId: String, timestamp: String, cruiseAltitudeFt: Int?) async throws {
        try await online.recalculateAdvisories(
            flightId: flightId,
            timestamp: timestamp,
            cruiseAltitudeFt: cruiseAltitudeFt
        )
    }

    // Timing scenarios (#357) are online-only (v1): the timing artifact is not
    // part of the offline bundle, so these always hit the network.
    func timeOptions(flightId: String, timestamp: String) async throws -> TimeOptionsResponse {
        try await online.timeOptions(flightId: flightId, timestamp: timestamp)
    }

    func confirmTimeOption(flightId: String, timestamp: String, departureTime: String) async throws {
        try await online.confirmTimeOption(flightId: flightId, timestamp: timestamp, departureTime: departureTime)
    }

    func rescanTimeOptions(flightId: String, timestamp: String) async throws {
        try await online.rescanTimeOptions(flightId: flightId, timestamp: timestamp)
    }

    func digest(flightId: String, timestamp: String) async throws -> DigestResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "digest")
    }

    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse {
        try await cachedOrFetch(flightId: flightId, timestamp: timestamp, endpoint: "snapshot")
    }

    /// Same-timestamp realtime refreshes must survive reopening offline. No
    /// endpoint or full-pack re-download: update the snapshot already on disk.
    func persistRealtimeRefresh(_ event: RefreshEvent, flightId: String, timestamp: String) async throws {
        try await cache.patchRealtimeSnapshot(event, flightId: flightId, timestamp: timestamp)
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
        departureTime: String? = nil,
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
            totalBytes: diskBytes,
            departureTime: departureTime
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

    /// `CacheStatusReporting`: a flight is offline-ready once it has a downloaded
    /// pack in the on-disk index.
    func offlineReadyFlightIds() async -> Set<String> {
        Set(await cachedPacks().map(\.flightId))
    }

    // MARK: - Cache eviction

    /// Evict cached packs whose flight departed more than `days` ago, then sweep
    /// any leftover flight-level sidecar directories on the same cutoff.
    ///
    /// Departure date comes from the value captured at download time, falling
    /// back to the cached flight record for legacy entries. Packs whose
    /// departure can't be determined are kept (conservative — we never delete
    /// data we can't prove is stale). The trailing directory sweep reclaims
    /// sidecars written by *viewing* a flight (which never enter the pack index),
    /// applying the identical departure-based cutoff and safety invariants.
    /// Returns the number of packs removed (the sidecar sweep is logged separately).
    @discardableResult
    func pruneStalePacks(olderThanDays days: Int) async -> Int {
        let cutoff = Date().addingTimeInterval(-Double(days) * 86_400)
        var removed = 0
        var touchedFlights: Set<String> = []
        for entry in await cache.cachedPacks() {
            let fallback = await cachedFlightDeparture(forFlightId: entry.flightId)
            guard let stale = Self.isPackStale(
                entryDepartureTime: entry.departureTime,
                fallbackDeparture: fallback,
                cutoff: cutoff
            ), stale else { continue }
            await cache.deletePack(flightId: entry.flightId, timestamp: entry.timestamp)
            touchedFlights.insert(entry.flightId)
            removed += 1
        }
        // A flight's departure is shared across all its packs, so evicting any
        // one aged-out pack evicts them all in the same run. Once none remain,
        // the flight-level sidecar metadata (flight.json, packs.json,
        // latest-pack.json, pack-meta.json) is orphaned — offline recovery only
        // walks index entries, so nothing references it — so drop the directory
        // instead of leaking it forever.
        for flightId in touchedFlights where await !cache.hasCachedPacks(flightId: flightId) {
            await cache.removeFlightDirectory(flightId: flightId)
        }
        // Second sweep: flight-level sidecars are written just by *viewing* a
        // flight online (flight.json, packs.json, latest-pack.json), independent
        // of any download. Those flights never enter the index, so the pack loop
        // above never reaches them and their directories would leak forever. Walk
        // the cache directory on disk and age out any flight with no live packs on
        // the same departure-based cutoff. Same safety invariants as pack eviction:
        // today/future flights survive; an indeterminate departure (no readable
        // flight.json) is kept — never delete blind.
        var sweptDirs = 0
        for flightId in await cache.flightDirectoryIDs() {
            if await cache.hasCachedPacks(flightId: flightId) { continue }
            guard let departure = await cachedFlightDeparture(forFlightId: flightId) else { continue }
            if departure < cutoff {
                await cache.removeFlightDirectory(flightId: flightId)
                sweptDirs += 1
            }
        }
        if removed > 0 || sweptDirs > 0 {
            Self.logger.info("Pruned \(removed) stale pack(s) and \(sweptDirs) viewed-only flight dir(s) older than \(days)d")
        }
        return removed
    }

    /// Resolve a cached pack's departure and decide whether it's older than the
    /// cutoff. Pure (no I/O) so the cutoff comparison and the legacy fallback
    /// from `entry.departureTime` to the cached flight record are unit-testable.
    /// Returns nil when no departure can be determined — the caller keeps the
    /// pack (conservative: never delete data we can't prove is stale).
    static func isPackStale(entryDepartureTime: String?, fallbackDeparture: Date?, cutoff: Date) -> Bool? {
        let departure: Date?
        if let ts = entryDepartureTime, let parsed = Date.parseISO8601(ts) {
            departure = parsed
        } else {
            departure = fallbackDeparture
        }
        guard let departure else { return nil }
        return departure < cutoff
    }

    /// The cached flight record's departure, read from `flight.json`. Used both
    /// as the legacy fallback for index entries written before
    /// `CachedPackEntry.departureTime` existed, and to age out viewed-only flights
    /// that have a `flight.json` sidecar but no index entry at all.
    private func cachedFlightDeparture(forFlightId flightId: String) async -> Date? {
        guard let data = await cache.readFlightMetadata(flightId: flightId, name: "flight"),
              let flight = try? JSONDecoder.weatherBrief.decode(FlightResponse.self, from: data) else {
            return nil
        }
        // Parse via the shared fractional-tolerant helper rather than
        // FlightResponse.departureDate (plain ISO8601DateFormatter, rejects
        // fractional seconds) so this legacy fallback stays consistent with
        // every other timestamp parse in the eviction path.
        return Date.parseISO8601(flight.departureTime)
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
