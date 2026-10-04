import Foundation

// =============================================================================
// SYNC — mirrors the tiled-imagery endpoints in src/weatherbrief/api/observed.py
// (#652) and the web's `ObservedFramesInfo` / `ObservedSourceStatus`
// (web/ts/visualization/route-map/observed-overlay*.ts).
//
// Imagery is never in the briefing payload: the route map asks for it while it
// is on screen, so none of this is cached to disk or part of the offline bundle.
// Decoded with `JSONDecoder.weatherBrief` (`.convertFromSnakeCase`); there are no
// dictionaries here, so the key-rewriting trap does not apply.
// =============================================================================

/// One frame of a tiled layer, as `/api/observed/frames/{source}` lists it.
nonisolated struct ObservedImageryFrame: Codable, Sendable, Equatable {
    /// Path segment that keys the tiles — the URL *is* the frame.
    let stamp: String
    /// The frame's own valid time. There is no shared "as of" across sources.
    let validTime: String
    /// Age when the server listed it. The badge recomputes from `validTime`.
    let ageMinutes: Double
}

/// Attribution text for a tiled layer. The radar's carries producer/licence too;
/// only the rendered text is needed here (unknown keys are ignored).
nonisolated struct ObservedImageryAttribution: Codable, Sendable, Equatable {
    let text: String?
}

/// `GET /api/observed/frames/{source}`: every retained frame, newest first —
/// the map draws `frames[0]`; a loop (#653) would step through the rest.
nonisolated struct ObservedFramesResponse: Codable, Sendable, Equatable {
    let source: String
    let label: String
    let frames: [ObservedImageryFrame]
    /// The newest frame is too old to pass for the present sky — don't draw it.
    let stale: Bool
    /// Rolling-max / accumulation window; 0 for an instant.
    let windowMinutes: Double?
    let attribution: ObservedImageryAttribution?
    /// `/api/observed/tiles/{source}/{stamp}/{z}/{x}/{y}.png`
    let tileUrlTemplate: String
    /// Zoom range the server renders; outside it the tile endpoint answers 404.
    let minZoom: Int
    let maxZoom: Int
}

/// One colour stop of a source's ramp, as the server renders it (`#rrggbb`).
nonisolated struct ObservedLegendStop: Codable, Sendable, Equatable {
    let value: Double
    let color: String
}

/// One source in `GET /api/observed/status` — only the legend fields the map
/// needs. From the server rather than a client copy of the ramps, so the legend
/// cannot drift from the paint (`legend_for`).
nonisolated struct ObservedImagerySourceStatus: Codable, Sendable, Equatable {
    let source: String
    let label: String
    let units: String?
    let legend: [ObservedLegendStop]?
}

nonisolated struct ObservedImageryStatusResponse: Codable, Sendable, Equatable {
    let sources: [ObservedImagerySourceStatus]
}
