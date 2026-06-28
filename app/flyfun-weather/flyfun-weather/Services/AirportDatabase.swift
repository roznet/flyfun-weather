import Foundation
import OSLog
import RZFlight
import FMDB

/// Local airport database for fast, offline ICAO autocomplete.
///
/// The slim `airports.db` is **not** bundled — it churns every AIRAC cycle, so
/// instead the app downloads it from the server (`GET /api/nav/airports-db`),
/// caches it in Application Support, and refreshes it via ETag when the server's
/// copy changes. With no cached copy yet (first launch / offline) `search()`
/// simply returns nothing — autocomplete is empty, never broken.
///
/// Wraps RZFlight's `KnownAirports` (`rankedSearch` is an in-memory tiered
/// ICAO/name search), so lookups never touch the network or block typing.
@Observable
final class AirportDatabase: @unchecked Sendable {
    static let shared = AirportDatabase()

    /// True once a usable database is open. Drives whether autocomplete can offer
    /// anything; the UI degrades gracefully to "no suggestions" when false.
    private(set) var isLoaded: Bool = false

    private var knownAirports: KnownAirports?
    private var db: FMDatabase?
    private var loadTask: Task<Void, Never>?

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AirportDatabase")
    private static let etagKey = "airportsDB.etag"

    private init() {}

    /// On-disk cache location (persisted, not purgeable like Caches).
    private static var cacheURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("airports.db")
    }

    /// Open the locally-cached DB if one was previously downloaded. Cheap, runs
    /// off the main thread, and is safe to call at launch. No-op when there's no
    /// cache yet.
    func loadCached() {
        guard !isLoaded, loadTask == nil else { return }
        loadTask = Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            let url = Self.cacheURL
            guard FileManager.default.fileExists(atPath: url.path) else {
                Self.logger.debug("No cached airports DB yet")
                return
            }
            let database = FMDatabase(path: url.path)
            guard database.open() else {
                Self.logger.error("Failed to open cached airports DB")
                return
            }
            let known = KnownAirports(db: database)
            await MainActor.run {
                self.db = database
                self.knownAirports = known
                self.isLoaded = true
            }
        }
        Task { await loadTask?.value }
    }

    /// Conditionally download the latest airports DB and swap it in. Uses
    /// ETag/`If-None-Match`, so an unchanged DB costs only a 304. Failures
    /// (offline, server down) are non-fatal — whatever is already loaded stays.
    func refresh(using client: APIClient) async {
        let hasCache = FileManager.default.fileExists(atPath: Self.cacheURL.path)
        let etagToSend = hasCache ? UserDefaults.standard.string(forKey: Self.etagKey) : nil
        do {
            let result = try await client.fetchAirportsDB(ifNoneMatch: etagToSend)
            switch result {
            case .notModified:
                Self.logger.debug("Airports DB already current")
                if !isLoaded { loadCached() }
            case .updated(let data, let etag):
                await install(data: data, etag: etag)
                Self.logger.info("Airports DB updated (\(data.count) bytes)")
            }
        } catch {
            Self.logger.debug("Airports DB refresh skipped: \(error.localizedDescription)")
        }
    }

    /// Ranked ICAO/name search. Empty until a DB is loaded.
    func search(needle: String, limit: Int = 12) -> [Airport] {
        guard let knownAirports, !needle.isEmpty else { return [] }
        return knownAirports.rankedSearch(needle: needle, limit: limit)
    }

    /// Look up a single airport by ICAO (no runway hydration — autocomplete only
    /// needs identity + coordinates).
    func airport(icao: String) -> Airport? {
        knownAirports?.airport(icao: icao, ensureRunway: false)
    }

    // MARK: - Private

    /// Write the downloaded bytes to the cache path and open the new DB, all off
    /// the main thread; assign the handles on the main actor.
    private func install(data: Data, etag: String?) async {
        let url = Self.cacheURL
        await Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let dir = url.deletingLastPathComponent()
                try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                let tmp = url.appendingPathExtension("download")
                try? FileManager.default.removeItem(at: tmp)
                try data.write(to: tmp, options: .atomic)
                if FileManager.default.fileExists(atPath: url.path) {
                    try FileManager.default.removeItem(at: url)
                }
                try FileManager.default.moveItem(at: tmp, to: url)
            } catch {
                Self.logger.error("Failed to persist airports DB: \(error.localizedDescription)")
                return
            }
            let database = FMDatabase(path: url.path)
            guard database.open() else {
                Self.logger.error("Failed to open downloaded airports DB")
                return
            }
            let known = KnownAirports(db: database)
            await MainActor.run {
                self.db?.close()
                self.db = database
                self.knownAirports = known
                self.isLoaded = true
                if let etag { UserDefaults.standard.set(etag, forKey: Self.etagKey) }
            }
        }.value
    }
}
