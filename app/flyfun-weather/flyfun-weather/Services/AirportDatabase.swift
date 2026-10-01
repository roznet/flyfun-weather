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
///
/// `@MainActor`-isolated: all access to the stored DB handles happens on the main
/// actor (the only off-main work — file IO + opening the SQLite — runs inside
/// `Task.detached` blocks that hop back via `MainActor.run` before assigning), so
/// there's no data race on `knownAirports`/`isLoaded`.
@Observable
@MainActor
final class AirportDatabase {
    static let shared = AirportDatabase()

    /// True once a usable database is open. Drives whether autocomplete can offer
    /// anything; the UI degrades gracefully to "no suggestions" when false.
    private(set) var isLoaded: Bool = false

    private var knownAirports: KnownAirports?
    private var db: FMDatabase?
    private var loadTask: Task<Void, Never>?

    private nonisolated static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AirportDatabase")
    private nonisolated static let etagKey = "airportsDB.etag"

    /// On-disk cache location. A `let` so the off-main `Task.detached` blocks can
    /// read it; tests point it at a temp directory.
    nonisolated let cacheURL: URL

    init(cacheURL: URL = AirportDatabase.defaultCacheURL) {
        self.cacheURL = cacheURL
    }

    /// Boxes the non-`Sendable` SQLite handle + index so the off-main builder can
    /// hand them to the main actor as a single `Sendable` value. Safe because the
    /// objects are fully constructed off-main and then *exclusively* owned by the
    /// main actor — a one-way ownership hand-off with no concurrent access. The
    /// explicit box keeps this correct under Swift 6 strict concurrency too.
    private struct OpenedAirportDB: @unchecked Sendable {
        let db: FMDatabase
        let known: KnownAirports
    }

    /// Default cache location (persisted, not purgeable like Caches).
    nonisolated static var defaultCacheURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("airports.db")
    }

    /// Open the locally-cached DB if one was previously downloaded. Cheap, runs
    /// off the main thread, and is safe to call at launch. No-op when there's no
    /// cache yet.
    func loadCached() {
        guard !isLoaded, loadTask == nil else { return }
        let url = cacheURL
        loadTask = Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            guard FileManager.default.fileExists(atPath: url.path) else {
                Self.logger.debug("No cached airports DB yet")
                return
            }
            let database = FMDatabase(path: url.path)
            guard database.open() else {
                Self.logger.error("Failed to open cached airports DB")
                return
            }
            let opened = OpenedAirportDB(db: database, known: KnownAirports(db: database))
            await MainActor.run {
                self.db = opened.db
                self.knownAirports = opened.known
                self.isLoaded = true
            }
        }
    }

    /// Conditionally download the latest airports DB and swap it in. Uses
    /// ETag/`If-None-Match`, so an unchanged DB costs only a 304. Failures
    /// (offline, server down) are non-fatal — whatever is already loaded stays.
    func refresh(using client: APIClient) async {
        // Read the cache state off the main actor (sync FileManager/UserDefaults
        // calls); only the resulting String? (Sendable) crosses back.
        let cachePath = cacheURL.path
        let etagToSend = await Task.detached(priority: .userInitiated) { () -> String? in
            let hasCache = FileManager.default.fileExists(atPath: cachePath)
            return hasCache ? UserDefaults.standard.string(forKey: Self.etagKey) : nil
        }.value
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

    /// Open the cached DB and **await** the load, so a caller that searches
    /// immediately afterward doesn't race the detached `loadCached()` task. This
    /// matters for App Intents: a background Siri intent is often served by a
    /// freshly-spawned process where `loadCached()` + `search()` run back-to-back
    /// with no scheduling gap, leaving `knownAirports` nil on that first call.
    /// No-op once loaded; a no-cache-yet device still returns quickly (empty DB).
    func ensureLoaded() async {
        loadCached()
        await loadTask?.value
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

    /// Swap a downloaded DB in for the cached one. Internal for tests.
    ///
    /// The old handle is closed **before** its file is replaced: unlinking a file
    /// SQLite still has open is an API violation ("vnode unlinked while in use")
    /// that fails any read on that handle with a disk I/O error. Search returns
    /// nothing for the moment in between, never an error. A launch-time
    /// `loadCached()` still opening the old file is awaited first for the same
    /// reason — at launch both start together (`AppState`).
    func install(data: Data, etag: String?) async {
        let url = cacheURL
        await loadTask?.value

        // Stage the download next to the cache, off-main, while the old DB
        // keeps serving.
        let staged = await Task.detached(priority: .userInitiated) { () -> URL? in
            do {
                try FileManager.default.createDirectory(
                    at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                let tmp = url.appendingPathExtension("download")
                try? FileManager.default.removeItem(at: tmp)
                try data.write(to: tmp, options: .atomic)
                return tmp
            } catch {
                Self.logger.error("Failed to persist airports DB: \(error.localizedDescription)")
                return nil
            }
        }.value
        guard let staged else { return }

        knownAirports = nil
        db?.close()
        db = nil
        isLoaded = false

        // Swap, then open whatever is on disk — the new DB, or the old one if
        // the swap failed (so a failed update never leaves autocomplete empty).
        let (opened, swapped) = await Task.detached(priority: .userInitiated) { () -> (OpenedAirportDB?, Bool) in
            var swapped = false
            do {
                if FileManager.default.fileExists(atPath: url.path) {
                    try FileManager.default.removeItem(at: url)
                }
                try FileManager.default.moveItem(at: staged, to: url)
                swapped = true
            } catch {
                Self.logger.error("Failed to swap in airports DB: \(error.localizedDescription)")
            }
            guard FileManager.default.fileExists(atPath: url.path) else { return (nil, swapped) }
            let database = FMDatabase(path: url.path)
            guard database.open() else {
                Self.logger.error("Failed to open airports DB after update")
                return (nil, swapped)
            }
            return (OpenedAirportDB(db: database, known: KnownAirports(db: database)), swapped)
        }.value
        guard let opened else { return }
        db = opened.db
        knownAirports = opened.known
        isLoaded = true
        if swapped, let etag { UserDefaults.standard.set(etag, forKey: Self.etagKey) }
    }
}
