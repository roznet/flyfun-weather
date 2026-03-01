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
