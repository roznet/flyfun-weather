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
    /// `/api/observed/cells/{stamp}.json` — `{stamp}` takes a frame's `displayKey`.
    let urlTemplate: String
}

nonisolated struct CellFrame: Codable, Sendable, Equatable {
    /// `YYYYMMDDTHHMM` — string order is time order.
    let stamp: String
    let validTime: String
    let receivedAt: String?
    let ageMinutes: Double?
    /// `<stamp>.r<n>`: the frame's newest revision (#666 — a frame is re-issued
    /// once its lightning lands). Nil from a server older than revisions.
    let key: String?
    let revision: Int?

    /// What goes into `urlTemplate`: the newest revision, immutable, so the
    /// display cache keyed by path can never hold an older one. The bare
    /// stamp on an older server.
    var displayKey: String { key ?? stamp }
}

/// One display file: one radar frame's cells, outlines and the frame's own times.
///
/// Decoded leniently: the envelope's identity fields are optional and `cells`
/// is decoded element by element — a cell missing a required field is
/// dropped, not the whole overlay (a node-side schema slip must not blank the
/// map for every cell).
nonisolated struct CellDisplay: Codable, Sendable {
    let schema: String?
    let policyVersion: String?
    let codeRevision: String?
    let validTime: String?
    let windowMinutes: Double?
    let times: CellDisplayTimes?
    let unavailable: [CellUnavailable]?
    let rainMinAreaKm2: Double?
    let arrowMinutes: Double?
    /// Tier → polylines, each `[[lat, lon], …]`.
    let outlines: [String: [[[Double]]]]?
    let cells: [DisplayCell]

    private enum CodingKeys: String, CodingKey {
        case schema, policyVersion, codeRevision, validTime, windowMinutes, times
        case unavailable, rainMinAreaKm2, arrowMinutes, outlines, cells
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schema = try? c.decodeIfPresent(String.self, forKey: .schema)
        policyVersion = try? c.decodeIfPresent(String.self, forKey: .policyVersion)
        codeRevision = try? c.decodeIfPresent(String.self, forKey: .codeRevision)
        validTime = try? c.decodeIfPresent(String.self, forKey: .validTime)
        windowMinutes = try? c.decodeIfPresent(Double.self, forKey: .windowMinutes)
        times = try? c.decodeIfPresent(CellDisplayTimes.self, forKey: .times)
        unavailable = try? c.decodeIfPresent([CellUnavailable].self, forKey: .unavailable)
        rainMinAreaKm2 = try? c.decodeIfPresent(Double.self, forKey: .rainMinAreaKm2)
        arrowMinutes = try? c.decodeIfPresent(Double.self, forKey: .arrowMinutes)
        outlines = try? c.decodeIfPresent([String: [[[Double]]]].self, forKey: .outlines)
        cells = (try? c.decodeIfPresent(LossyCells.self, forKey: .cells))?.elements ?? []
    }
}

/// `[DisplayCell]` that skips the elements it cannot decode.
private nonisolated struct LossyCells: Decodable {
    let elements: [DisplayCell]

    private struct Skip: Decodable {
        init(from decoder: Decoder) throws {}
    }

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var out: [DisplayCell] = []
        while !container.isAtEnd {
            if let cell = try? container.decode(DisplayCell.self) {
                out.append(cell)
            } else {
                _ = try? container.decode(Skip.self)  // advance past the bad element
            }
        }
        elements = out
    }
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
