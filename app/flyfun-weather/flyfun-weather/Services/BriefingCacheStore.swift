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
    /// Flight departure (ISO-8601) captured at download time so cache eviction
    /// can age packs out by flight date without a separate flight-metadata
    /// lookup. Optional for backward compatibility with index entries written
    /// before this field existed (they fall back to the cached flight record).
    var departureTime: String? = nil

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
    private var cacheRootEnsured = false

    /// All cache writes are at-rest encrypted (readable only after first unlock)
    /// so briefing routes, positions, and digest content aren't exposed on a
    /// locked or lost device. `.atomic` also guards against torn writes.
    private static let writeOptions: Data.WritingOptions =
        [.atomic, .completeFileProtectionUntilFirstUserAuthentication]

    /// Create the cache root (once) and exclude it from iCloud/iTunes backup —
    /// the contents are regenerable from the server and shouldn't inflate
    /// backups or migrate to other devices.
    private func ensureCacheRoot() throws {
        if !cacheRootEnsured {
            try FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
            var url = cacheDir
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? url.setResourceValues(values)
            cacheRootEnsured = true
        }
    }

    /// The cache root is injectable so tests can point it at a temp directory;
    /// production (`cacheDir: nil`) uses Application Support/BriefingCache.
    init(cacheDir: URL? = nil) {
        self.cacheDir = cacheDir ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("BriefingCache", isDirectory: true)
    }

    // MARK: - Read / Write

    func readData(flightId: String, timestamp: String, endpoint: String) -> Data? {
        let file = fileURL(flightId: flightId, timestamp: timestamp, endpoint: endpoint)
        return try? Data(contentsOf: file)
    }

    func writeData(_ data: Data, flightId: String, timestamp: String, endpoint: String) throws {
        try ensureCacheRoot()
        let dir = packDir(flightId: flightId, timestamp: timestamp)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("\(endpoint).json")
        try data.write(to: file, options: Self.writeOptions)
    }

    // MARK: - Metadata cache (for offline fallback)

    /// Write a metadata file at the cache root level (e.g. "flights.json").
    func writeMetadata(_ data: Data, name: String) throws {
        try ensureCacheRoot()
        try data.write(to: cacheDir.appendingPathComponent("\(name).json"), options: Self.writeOptions)
    }

    /// Read a metadata file from the cache root level.
    func readMetadata(name: String) -> Data? {
        try? Data(contentsOf: cacheDir.appendingPathComponent("\(name).json"))
    }

    /// Write per-flight metadata (e.g. "latest-pack" for a given flight).
    func writeFlightMetadata(_ data: Data, flightId: String, name: String) throws {
        try ensureCacheRoot()
        let dir = cacheDir.appendingPathComponent(flightId, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try data.write(to: dir.appendingPathComponent("\(name).json"), options: Self.writeOptions)
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
        totalBytes: Int64,
        departureTime: String? = nil
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
            totalBytes: totalBytes,
            departureTime: departureTime
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

    /// Whether any pack for this flight still has an index entry. The trailing
    /// slash keeps `f1` from matching `f10` when testing the `flightId/timestamp` key.
    func hasCachedPacks(flightId: String) -> Bool {
        ensureLoaded()
        let prefix = "\(flightId)/"
        return index.keys.contains { $0.hasPrefix(prefix) }
    }

    /// Top-level `<flightId>/` subdirectories present on disk (each may hold
    /// pack subdirs and/or flight-level sidecar metadata). Enumerates `cacheDir`'s
    /// immediate children and keeps only directories, so the root-level metadata
    /// files (`index.json`, `flights.json`) are skipped. Pack data lives nested at
    /// `<flightId>/<timestamp>/`, so the top level is only flight-id dirs. Lets
    /// eviction sweep viewed-but-never-downloaded flights that the index never saw.
    func flightDirectoryIDs() -> [String] {
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: cacheDir,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return entries.compactMap { url in
            let isDir = (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
            return isDir ? url.lastPathComponent : nil
        }
    }

    /// Remove a flight's entire cache directory, including flight-level sidecar
    /// metadata (flight.json, packs.json, latest-pack.json, pack-meta.json).
    /// Only safe once every pack for the flight has been deleted from the index
    /// — offline recovery walks index entries, so nothing reads these files once
    /// no entry points at the flight. No-op on the index itself (already clear).
    func removeFlightDirectory(flightId: String) {
        let dir = cacheDir.appendingPathComponent(flightId, isDirectory: true)
        try? FileManager.default.removeItem(at: dir)
        Self.logger.info("Removed orphaned flight cache dir \(flightId)")
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
            try ensureCacheRoot()
            let data = try JSONEncoder().encode(index)
            try data.write(to: cacheDir.appendingPathComponent("index.json"), options: Self.writeOptions)
        } catch {
            Self.logger.error("Failed to save cache index: \(error)")
        }
    }
}

// MARK: - Per-user scoping (paths, migration, cross-user eviction)

/// The on-disk tree is scoped per signed-in user: `<base>/BriefingCache/users/<scope>/`.
/// Without this the cache dir was shared, so after user A logged out and user B
/// logged in, B's cold-start seed would read A's `flights.json` (and A's cached
/// briefings sat readable on disk). Scoping gives physical isolation instead of
/// relying on a clear-on-logout wipe, and lets a returning user keep their offline
/// downloads. These are pure filesystem helpers (no actor state), taking an
/// explicit `base` so tests drive them against a temp directory.
extension BriefingCacheStore {
    /// Application Support base that holds the whole cache tree.
    static func defaultBase() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
    }

    /// Per-user cache directory handed to `init(cacheDir:)`.
    static func scopedCacheDir(base: URL, scope: String) -> URL {
        base.appendingPathComponent("BriefingCache", isDirectory: true)
            .appendingPathComponent("users", isDirectory: true)
            .appendingPathComponent(scope, isDirectory: true)
    }

    /// One-time migration off the pre-scoping layout, where `flights.json`,
    /// `index.json`, and `<flightId>/` dirs sat directly under `BriefingCache/`.
    /// Those are unreachable once reads are user-scoped, and eviction only walks
    /// the active scope — so they'd leak forever. Remove every root child except
    /// the new `users/` subtree. Idempotent (a no-op once only `users/` remains),
    /// so it's safe to call on every sign-in.
    static func migrateLegacyLayout(base: URL) {
        let root = base.appendingPathComponent("BriefingCache", isDirectory: true)
        guard let children = try? FileManager.default.contentsOfDirectory(
            at: root, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]
        ) else { return }
        for child in children where child.lastPathComponent != "users" {
            try? FileManager.default.removeItem(at: child)
        }
    }

    /// Coarse cross-user janitor. Per-user scoping means an abandoned account's dir
    /// is never swept by `pruneStalePacks` (which only runs on the active user), so
    /// it would accumulate stale packs forever. Delete any *other* user's scoped
    /// dir whose `flights.json` — rewritten on every logged-in `flights()` load, so
    /// a good "last active" marker — is older than the cutoff, falling back to the
    /// dir's own mtime when the marker is absent. The active scope is always kept
    /// (and swept precisely elsewhere); a dir with no determinable timestamp is
    /// kept (never delete blind).
    static func evictInactiveUsers(base: URL, keeping scope: String, olderThanDays days: Int) {
        let usersRoot = base.appendingPathComponent("BriefingCache", isDirectory: true)
            .appendingPathComponent("users", isDirectory: true)
        let cutoff = Date().addingTimeInterval(-Double(days) * 86_400)
        guard let dirs = try? FileManager.default.contentsOfDirectory(
            at: usersRoot, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]
        ) else { return }
        for dir in dirs where dir.lastPathComponent != scope {
            let marker = dir.appendingPathComponent("flights.json")
            let mtime = (try? marker.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
                ?? (try? dir.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            guard let mtime, mtime < cutoff else { continue }
            try? FileManager.default.removeItem(at: dir)
            Self.logger.info("Evicted inactive user cache dir \(dir.lastPathComponent)")
        }
    }
}
