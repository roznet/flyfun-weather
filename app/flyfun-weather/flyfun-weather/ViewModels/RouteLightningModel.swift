import Foundation
import Observation
import OSLog

/// Lightning flashes for the route map's corridor box (#665), polled while the
/// map is on screen. Port of the web route map's `fetchObservedFlashes`, with
/// a badge the web route map doesn't carry yet. Online-only — nothing is
/// written to the briefing cache.
@Observable
final class RouteLightningModel {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "RouteLightning")

    private(set) var state: ObservedLightning.State = .loading
    /// Flashes to draw now, faded at `now`.
    private(set) var drawable: [ObservedLightning.DrawableFlash] = []
    /// Bumped on every refresh: the badge re-reads the clock and the map
    /// replaces its overlay (the fade moved on).
    private(set) var now = Date()
    private(set) var revision = 0

    /// MTG LI lands every 10 min; a 2-minute poll keeps the fade and the
    /// badge's age honest without hammering the endpoint.
    static let pollInterval: Duration = .seconds(120)

    @ObservationIgnored private let repository: any BriefingRepository
    /// Drops the result of a refresh a newer one has overtaken.
    @ObservationIgnored private var token = 0

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    var badge: String? { ObservedLightning.badge(state, now: now) }
    var chip: ObservedMapImagery.SummaryChip? { ObservedLightning.chip(state, now: now) }

    /// Keep the flashes fresh until the calling task is cancelled.
    func poll(box: ObservedMapImagery.LatLonBox) async {
        while !Task.isCancelled {
            await refresh(box: box)
            try? await Task.sleep(for: Self.pollInterval)
        }
    }

    func refresh(box: ObservedMapImagery.LatLonBox, at time: Date = Date()) async {
        token += 1
        let mine = token
        do {
            let response = try await repository.observedFlashes(path: ObservedLightning.flashesPath(box: box))
            guard mine == token else { return }
            apply(.loaded(response), at: time)
        } catch {
            if Task.isCancelled || (error as? APIError)?.isCancellation == true { return }
            guard mine == token else { return }
            Self.logger.warning("flashes failed: \(error.localizedDescription)")
            // Say so on the map rather than drawing nothing silently.
            apply(.unavailable, at: time)
        }
    }

    private func apply(_ state: ObservedLightning.State, at time: Date) {
        self.state = state
        now = time
        if case .loaded(let response) = state {
            drawable = ObservedLightning.drawable(response.flashes, now: time)
        } else {
            drawable = []
        }
        revision += 1
    }
}
