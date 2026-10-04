import Foundation

// =============================================================================
// SYNC — mirrors src/weatherbrief/observed/cells/display.py (DISPLAY_SCHEMA
// `observed-cells-display/1`) and observed/cells_display.py::frames_status, the
// same contract the web's `cells-overlay-core.ts` types describe (#656 → #661).
//
// Decoded with the shared `JSONDecoder.weatherBrief` (`.convertFromSnakeCase`),
// which also rewrites DICTIONARY keys. `outlines` is the one dictionary here and
// its keys are tier names (`rain20`, `core35`, `core41`) — no underscore, so the
// strategy leaves them untouched. A tier name with an underscore would need
// explicit `CodingKeys`.
//
// Every number is optional `Double`: the node writes rounded floats and nulls
// freely, and a missing value must read as "–", never as zero.
// =============================================================================

/// `/api/observed/cells/frames` — overlay stamps newest first plus the stale state.
nonisolated struct CellFramesResponse: Codable, Sendable, Equatable {
    /// False when the server runs without `WB_CELLS_INGEST_ENABLED` — an answer
    /// ("not available on this server"), not an error.
    let enabled: Bool
    let frames: [CellFrame]
    let newest: CellFrame?
    let stale: Bool
    /// Shipped by the server so the client never hard-codes the threshold.
    let staleAfterMinutes: Double
    let unavailableSince: String?
    /// `/api/observed/cells/{stamp}.json`
    let urlTemplate: String
}

nonisolated struct CellFrame: Codable, Sendable, Equatable {
    /// `YYYYMMDDTHHMM` — string order is time order.
    let stamp: String
    let validTime: String
    let receivedAt: String?
    let ageMinutes: Double?
}

/// One display file: one radar frame's cells, outlines and the frame's own times.
nonisolated struct CellDisplay: Codable, Sendable {
    let schema: String
    let policyVersion: String
    let codeRevision: String?
    let validTime: String
    let windowMinutes: Double?
    let times: CellDisplayTimes?
    let unavailable: [CellUnavailable]?
    let rainMinAreaKm2: Double?
    let arrowMinutes: Double?
    /// Tier → polylines, each `[[lat, lon], …]`.
    let outlines: [String: [[[Double]]]]?
    let cells: [DisplayCell]
}

nonisolated struct CellDisplayTimes: Codable, Sendable {
    let radar: String?
    let rate: String?
    let lightning: String?
    let cloudTop: String?
}

nonisolated struct CellUnavailable: Codable, Sendable {
    let what: String
    let reason: String?
}

nonisolated struct DisplayCell: Codable, Sendable, Identifiable {
    let id: String
    /// `rain20` | `core35` | `core41`
    let tier: String
    let lat: Double
    let lon: Double
    let areaKm2: Double?
    let peakDbz: Double?
    let ratePeakMmH: Double?
    let flashes: Double?
    let topFl: Double?
    let truncated: Bool?
    let ageMin: Double?
    let event: String?
    let trend: CellTrend?
    let motion: CellMotion?
    /// `[lat, lon]` after `arrow_minutes` at the current motion; nil unless the
    /// motion is `available`.
    let arrow: [Double]?

    var isCore: Bool { tier != "rain20" }
}

nonisolated struct CellTrend: Codable, Sendable {
    /// developing | decaying | steady | mixed | new (others possible)
    let state: String?
    let windowMin: Double?
    let dPeakDb: Double?
    let areaRatio: Double?
    let dFlashes: Double?
}

nonisolated struct CellMotion: Codable, Sendable {
    /// available | withheld | unsupported | no_pair (others possible)
    let status: String?
    let reason: String?
    let speedKt: Double?
    let towardDeg: Double?
}
