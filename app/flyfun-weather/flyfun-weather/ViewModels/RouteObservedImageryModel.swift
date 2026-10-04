import Foundation
import Observation

/// Frame listings + legends for the route map's observed imagery (#654).
///
/// Port of the web's `refreshObservedFrames` (briefing-main.ts): a listing per
/// tiled source, refreshed at most once a minute, and polled every two minutes
/// while the map is on screen so a new radar frame (every 5 min) appears without
/// waiting for some other re-render. The last listing received keeps drawing
/// over a failed refresh; a source is `failed` only when nothing is held for it.
/// Online-only — nothing is written to the briefing cache.
@Observable
final class RouteObservedImageryModel {
    private(set) var frames: [String: ObservedFramesResponse] = [:]
    private(set) var failed: Set<String> = []
    private(set) var legends: [String: ObservedImagerySourceStatus] = [:]
    /// Bumped on every poll so the age badges re-read the clock.
    private(set) var now = Date()

    static let refreshInterval: TimeInterval = 60
    static let pollInterval: Duration = .seconds(120)

    @ObservationIgnored private let repository: any BriefingRepository
    @ObservationIgnored private var fetchedAt: [String: Date] = [:]
    @ObservationIgnored private var pending: Set<String> = []
    @ObservationIgnored private var legendsRequested = false

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    /// Bytes of one tile, through the API client (bearer auth). Handed to the
    /// overlays, which call it off the main thread.
    var tileFetcher: @Sendable (String) async throws -> Data {
        let repository = self.repository
        return { path in try await repository.observedTile(path: path) }
    }

    /// Keep `sources` fresh until the calling task is cancelled (the map leaves
    /// the screen, or the wanted set changes and `.task(id:)` restarts).
    func poll(_ sources: [String]) async {
        while !Task.isCancelled {
            now = Date()
            await refresh(sources)
            try? await Task.sleep(for: Self.pollInterval)
        }
    }

    /// Fetch every source whose listing is missing or older than the TTL.
    func refresh(_ sources: [String], at time: Date = Date()) async {
        let due = sources.filter { source in
            !pending.contains(source)
                && (frames[source] == nil
                    || time.timeIntervalSince(fetchedAt[source] ?? .distantPast) >= Self.refreshInterval)
        }
        for source in due {
            pending.insert(source)
            defer { pending.remove(source) }
            do {
                let info = try await repository.observedFrames(source: source)
                frames[source] = info
                failed.remove(source)
            } catch {
                if Task.isCancelled || (error as? APIError)?.isCancellation == true { return }
                // Keep the last listing over a failed refresh; with none held,
                // the badge says the imagery is unavailable.
                if frames[source] == nil { failed.insert(source) }
            }
            fetchedAt[source] = time
        }
    }

    /// Legends from `/api/observed/status`, once. A missing legend costs the
    /// scale, not the overlay, so a failure is silent and retried next time.
    func loadLegendsIfNeeded() async {
        guard !legendsRequested else { return }
        legendsRequested = true
        do {
            let status = try await repository.observedImageryStatus()
            legends = Dictionary(status.sources.map { ($0.source, $0) }, uniquingKeysWith: { a, _ in a })
        } catch {
            legendsRequested = false
        }
    }
}
