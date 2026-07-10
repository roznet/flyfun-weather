import Foundation

struct PackMetaResponse: Codable, Sendable {
    let flightId: String
    let fetchTimestamp: String
    let daysOut: Int
    let isHistorical: Bool
    let hasGramet: Bool
    let hasSkewt: Bool
    let hasDigest: Bool
    let hasAdvisories: Bool
    let assessment: String?
    let assessmentReason: String?
    /// Long-range early outlook (beyond the GRIB horizon), mutually exclusive
    /// with `assessment` — a tendency ("what to expect"), not a verdict. Present
    /// on far-out packs where no short-range assessment exists yet; the Advisory
    /// hero falls back to it so a long-range briefing still shows a summary.
    let outlook: String?
    let outlookReason: String?
    let modelInitTimes: [String: Int]
    let gribInitTimes: [String: Int]
    let modelsSkippedRegion: [String]
    let dataStatus: DataStatus?
}

struct DataStatus: Codable, Sendable {
    let fresh: Bool
    let staleModels: [String]
    let modelInitTimes: [String: Int]
    let nextExpectedUpdate: String?
    let nextExpectedModel: String?
}
