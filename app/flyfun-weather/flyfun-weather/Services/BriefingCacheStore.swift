import Foundation
import OSLog

/// Tracks a downloaded pack in the cache index.
struct CachedPackEntry: Codable, Sendable, Identifiable {
    let flightId: String
    let timestamp: String
    let flightTitle: String
    let assessment: String?
    let downloadedAt: Date
    var endpoints: Set<String>
    var totalBytes: Int64

    var id: String { "\(flightId)/\(timestamp)" }

    var isComplete: Bool {
        endpoints.isSuperset(of: Self.requiredEndpoints)
    }

    static let requiredEndpoints: Set<String> = [
        "advisories", "digest", "snapshot", "route-analyses", "elevation"
    ]
}

/// Manages on-disk cache of briefing pack data.
actor BriefingCacheStore {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Cache")

    private let cacheDir: URL
    private var index: [String: CachedPackEntry] = [:] // keyed by "flightId/timestamp"
    private var loaded = false

    /// Designated initializer — the cache root is injectable so tests can point
    /// it at a temp directory instead of the shared Application Support container.
    init(cacheDir: URL) {
        self.cacheDir = cacheDir
    }

    /// Production initializer — Application Support/BriefingCache.
    convenience init() {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        self.init(cacheDir: appSupport.appendingPathComponent("BriefingCache", isDirectory: true))
    }

    // MARK: - Read / Write

    func readData(flightId: String, timestamp: String, endpoint: String) -> Data? {
        let file = fileURL(flightId: flightId, timestamp: timestamp, endpoint: endpoint)
        return try? Data(contentsOf: file)
    }

    func writeData(_ data: Data, flightId: String, timestamp: String, endpoint: String) throws {
        let dir = packDir(flightId: flightId, timestamp: timestamp)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("\(endpoint).json")
        try data.write(to: file)
    }

    // MARK: - Metadata cache (for offline fallback)

    /// Write a metadata file at the cache root level (e.g. "flights.json").
    func writeMetadata(_ data: Data, name: String) throws {
        try FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
        try data.write(to: cacheDir.appendingPathComponent("\(name).json"))
    }

    /// Read a metadata file from the cache root level.
    func readMetadata(name: String) -> Data? {
        try? Data(contentsOf: cacheDir.appendingPathComponent("\(name).json"))
    }

    /// Write per-flight metadata (e.g. "latest-pack" for a given flight).
    func writeFlightMetadata(_ data: Data, flightId: String, name: String) throws {
        let dir = cacheDir.appendingPathComponent(flightId, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try data.write(to: dir.appendingPathComponent("\(name).json"))
    }

    /// Read per-flight metadata.
    func readFlightMetadata(flightId: String, name: String) -> Data? {
        let file = cacheDir.appendingPathComponent(flightId, isDirectory: true)
            .appendingPathComponent("\(name).json")
        return try? Data(contentsOf: file)
    }

    // MARK: - Index management

    func isPackCached(flightId: String, timestamp: String) -> Bool {
        ensureLoaded()
        let key = "\(flightId)/\(timestamp)"
        return index[key]?.isComplete ?? false
    }

    func cachedPacks() -> [CachedPackEntry] {
        ensureLoaded()
        return Array(index.values).sorted { $0.downloadedAt > $1.downloadedAt }
    }

    func registerDownload(
        flightId: String,
        timestamp: String,
        flightTitle: String,
        assessment: String?,
        endpoints: Set<String>,
        totalBytes: Int64
    ) {
        ensureLoaded()
        let key = "\(flightId)/\(timestamp)"
        index[key] = CachedPackEntry(
            flightId: flightId,
            timestamp: timestamp,
            flightTitle: flightTitle,
            assessment: assessment,
            downloadedAt: Date(),
            endpoints: endpoints,
            totalBytes: totalBytes
        )
        saveIndex()
    }

    func deletePack(flightId: String, timestamp: String) {
        ensureLoaded()
        let key = "\(flightId)/\(timestamp)"
        index.removeValue(forKey: key)
        saveIndex()
        let dir = packDir(flightId: flightId, timestamp: timestamp)
        try? FileManager.default.removeItem(at: dir)
        Self.logger.info("Deleted cached pack \(key)")
    }

    func totalCacheSize() -> Int64 {
        ensureLoaded()
        return index.values.reduce(0) { $0 + $1.totalBytes }
    }

    func clearAll() {
        index.removeAll()
        saveIndex()
        try? FileManager.default.removeItem(at: cacheDir)
        Self.logger.info("Cleared all cached packs")
    }

    // MARK: - Private

    private func packDir(flightId: String, timestamp: String) -> URL {
        // Sanitize timestamp for filesystem (replace colons)
        let safeTimestamp = timestamp.replacingOccurrences(of: ":", with: "-")
        return cacheDir.appendingPathComponent(flightId, isDirectory: true)
            .appendingPathComponent(safeTimestamp, isDirectory: true)
    }

    private func fileURL(flightId: String, timestamp: String, endpoint: String) -> URL {
        packDir(flightId: flightId, timestamp: timestamp)
            .appendingPathComponent("\(endpoint).json")
    }

    private func ensureLoaded() {
        guard !loaded else { return }
        loaded = true
        let indexFile = cacheDir.appendingPathComponent("index.json")
        guard let data = try? Data(contentsOf: indexFile) else { return }
        if let entries = try? JSONDecoder().decode([String: CachedPackEntry].self, from: data) {
            index = entries
            Self.logger.info("Loaded cache index: \(entries.count) packs")
        }
    }

    private func saveIndex() {
        do {
            try FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(index)
            try data.write(to: cacheDir.appendingPathComponent("index.json"))
        } catch {
            Self.logger.error("Failed to save cache index: \(error)")
        }
    }
}
