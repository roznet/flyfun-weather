import Foundation
import Observation
import OSLog

/// The experimental radar-cell overlay for one briefing route (#661). Two
/// instances per briefing: the route map's (paired with the drawn radar frame)
/// and the Observed tab's (`BriefingViewModel.cellsModel`, newest frame), so
/// two polls with different stamps never overtake each other.
///
/// Port of the web's `CellsLayer.refresh` (cells-overlay.ts): the frame listing
/// is fetched at most once a minute (a new frame lands every 5), the display
/// file for the chosen stamp + route box is immutable so it is cached by path,
/// and nothing is drawn unless the match is current — never a stale picture.
/// Online-only — nothing is written to the briefing cache.
@Observable
final class RouteCellsModel {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "RouteCells")

    private(set) var match: CellsOverlay.Match?
    /// The overlay to draw, or nil (not loaded, unavailable, disabled).
    private(set) var display: CellDisplay?
    /// Path of `display` — the map rebuilds its overlays only when it changes.
    private(set) var displayPath: String?
    /// Bumped on every refresh so the badge re-reads the clock.
    private(set) var now = Date()
    /// True once a refresh finished (successfully or not), so a list can tell
    /// "loading" from "nothing here".
    private(set) var hasLoaded = false

    static let framesTTL: TimeInterval = 60
    static let pollInterval: Duration = .seconds(120)
    static let displayCacheSize = 6

    @ObservationIgnored private let repository: any BriefingRepository
    @ObservationIgnored private var frames: CellFramesResponse?
    @ObservationIgnored private var framesFetchedAt: Date?
    @ObservationIgnored private var displayCache: [(path: String, display: CellDisplay)] = []
    /// Drops the result of a refresh a newer one has overtaken.
    @ObservationIgnored private var token = 0

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    /// The badge line for what is (or is not) drawn.
    var badge: String? {
        match.map { CellsOverlay.badge($0, display: display, now: now) }
    }

    /// The overlay's chip in the map's summary line.
    var chip: ObservedMapImagery.SummaryChip? {
        match.map { CellsOverlay.chip($0, now: now) }
    }

    /// Keep the overlay fresh until the calling task is cancelled.
    func poll(radarStamp: @escaping () -> String?, box: ObservedMapImagery.LatLonBox?) async {
        while !Task.isCancelled {
            await refresh(radarStamp: radarStamp(), box: box)
            try? await Task.sleep(for: Self.pollInterval)
        }
    }

    /// Fetch the listing (TTL; `force` skips it — a pilot's pull-to-refresh)
    /// and the display file that pairs with `radarStamp`.
    func refresh(radarStamp: String?, box: ObservedMapImagery.LatLonBox?, at time: Date = Date(),
                 force: Bool = false) async {
        token += 1
        let mine = token
        now = time
        if force || frames == nil || time.timeIntervalSince(framesFetchedAt ?? .distantPast) >= Self.framesTTL {
            do {
                frames = try await repository.observedCellFrames()
            } catch {
                if Task.isCancelled || (error as? APIError)?.isCancellation == true { return }
                Self.logger.warning("cell frames failed: \(error.localizedDescription)")
                // Keep a held listing over a failed refresh; with none, the
                // match below reads "unavailable".
            }
            framesFetchedAt = time
        }
        guard mine == token else { return }

        let match = CellsOverlay.match(frames, radarStamp: radarStamp, now: time)
        guard case .ok(let frame) = match, let frames else {
            apply(match: match, display: nil, path: nil)
            return
        }
        // The newest revision's key (#666): an amended frame is a new path,
        // so the path-keyed cache below never serves a superseded revision.
        let path = CellsOverlay.displayPath(template: frames.urlTemplate, stamp: frame.displayKey, box: box)
        if let hit = displayCache.first(where: { $0.path == path }) {
            apply(match: match, display: hit.display, path: path)
            return
        }
        do {
            let display = try await repository.observedCellDisplay(path: path)
            guard mine == token else { return }
            displayCache.append((path, display))
            if displayCache.count > Self.displayCacheSize { displayCache.removeFirst() }
            apply(match: match, display: display, path: path)
        } catch {
            if Task.isCancelled || (error as? APIError)?.isCancellation == true { return }
            guard mine == token else { return }
            Self.logger.warning("cell overlay \(frame.stamp, privacy: .public) failed: \(error.localizedDescription)")
            // Purged (410) or unreadable: say unavailable since its own time.
            apply(match: .unavailable(since: frame.validTime), display: nil, path: nil)
        }
    }

    private func apply(match: CellsOverlay.Match, display: CellDisplay?, path: String?) {
        self.match = match
        self.display = display
        self.displayPath = path
        hasLoaded = true
    }
}
