import Foundation

// =============================================================================
// SYNC — mirrors `GET /api/observed/flashes` in src/weatherbrief/api/observed.py
// and the web's `ObservedFlashPoint` (web/ts/visualization/route-map/observed-overlay.ts).
//
// Online-only, like the imagery: the route map asks while it is on screen,
// nothing is cached to disk. Decoded with `JSONDecoder.weatherBrief`
// (`.convertFromSnakeCase`); no dictionaries, so the key-rewriting trap does
// not apply.
// =============================================================================

/// One lightning flash with its own time, so the map can fade it by age.
nonisolated struct ObservedFlash: Codable, Sendable, Equatable {
    let lat: Double
    let lon: Double
    /// ISO-8601 instant of the flash itself (not of the frame it came in).
    let time: String
}

/// Lightning inside a box: every retained flash (or the last `minutes`).
nonisolated struct ObservedFlashesResponse: Codable, Sendable, Equatable {
    let flashes: [ObservedFlash]
    let count: Int?
    /// The newest lightning frame's valid time; nil when no frame is held.
    let newestValidTime: String?
    /// The product's own accumulation window (10 min for MTG LI).
    let windowMinutes: Double?
    let retentionMinutes: Double?
    /// `{}` when no frame is held — every field optional.
    let attribution: ObservedImageryAttribution?
}
