import Foundation
import OSLog

/// Local cache of the help catalog (metric + advisory (i)-popup content).
///
/// The web app is the single source of truth; this store downloads the catalog
/// when online, caches it to disk, and serves popups from that cache so help
/// renders offline. A bundled baseline (`metrics-catalog.json`) covers the very
/// first run before any sync.
///
/// Modeled on `PirepOfflineStore` (JSON file in the documents directory) but
/// `@MainActor @Observable` like `UserPreferencesStore` so SwiftUI views can read
/// the catalog synchronously. The raw server bytes are persisted verbatim (the
/// payload is ~100 KB+ — too large for `UserDefaults`); the `ETag` rides in
/// `UserDefaults` so the next launch can issue a conditional request.
@MainActor
@Observable
final class HelpCatalogStore {
    private static let etagKey = "helpCatalogETag"
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "HelpCatalog")

    /// Current catalog: disk cache, else bundled baseline, else nil.
    private(set) var catalog: HelpCatalogResponse?

    private let fileURL: URL
    private var etag: String?

    init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        fileURL = docs.appendingPathComponent("help-catalog.json")
        etag = UserDefaults.standard.string(forKey: Self.etagKey)
        catalog = Self.loadFromDisk(fileURL) ?? Self.loadBaseline()
    }

    // MARK: - Lookups (used by the (i) popups)

    func metric(_ id: String) -> MetricHelp? { catalog?.metrics[id] }

    func advisory(_ id: String) -> AdvisoryCatalogEntry? { catalog?.advisoriesById[id] }

    // MARK: - Sync

    /// Fetch the latest catalog if it changed, then persist + publish it.
    ///
    /// Non-blocking by design (call from a `Task`). A `304` keeps the cache; a
    /// transient network error is a silent no-op so the cached/baseline copy
    /// stays in place offline.
    func refresh(using client: APIClient) async {
        do {
            switch try await client.fetchHelpCatalog(ifNoneMatch: etag) {
            case .notModified:
                Self.logger.debug("Help catalog unchanged (304)")
            case .updated(let data, let newETag):
                let fresh = try HelpCatalogResponse.decode(from: data)
                catalog = fresh
                etag = newETag
                persist(data: data, etag: newETag)
                Self.logger.info("Help catalog updated (version \(fresh.version))")
            }
        } catch let error as APIError where error.isTransientNetwork {
            Self.logger.debug("Offline, keeping cached help catalog")
        } catch {
            Self.logger.warning("Failed to refresh help catalog: \(error.localizedDescription)")
        }
    }

    // MARK: - Persistence

    private func persist(data: Data, etag: String?) {
        do {
            try data.write(to: fileURL, options: .atomic)
        } catch {
            Self.logger.warning("Failed to write help catalog cache: \(error.localizedDescription)")
        }
        if let etag {
            UserDefaults.standard.set(etag, forKey: Self.etagKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.etagKey)
        }
    }

    private static func loadFromDisk(_ url: URL) -> HelpCatalogResponse? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        do {
            return try HelpCatalogResponse.decode(from: Data(contentsOf: url))
        } catch {
            logger.error("Failed to load cached help catalog: \(error.localizedDescription)")
            return nil
        }
    }

    /// Decode the bundled baseline metrics catalog (advisories empty until first
    /// sync — they arrive with the first online fetch).
    private static func loadBaseline() -> HelpCatalogResponse? {
        guard let url = Bundle.main.url(forResource: "metrics-catalog", withExtension: "json") else {
            logger.error("Bundled metrics-catalog.json missing from app resources")
            return nil
        }
        do {
            return try HelpCatalogResponse.decodeBaseline(from: Data(contentsOf: url))
        } catch {
            logger.error("Failed to decode bundled metrics-catalog.json: \(error.localizedDescription)")
            return nil
        }
    }
}
