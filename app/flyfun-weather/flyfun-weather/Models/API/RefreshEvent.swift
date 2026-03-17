import Foundation

/// SSE event from the refresh stream endpoint.
struct RefreshEvent: Codable, Sendable {
    let type: String // "progress", "complete", "error"

    // Progress fields
    let stage: String?
    let detail: String?
    let label: String?
    let progress: Double?

    // Complete fields
    let pack: PackMetaResponse?
    let elapsedSeconds: Double?

    // Error fields
    let message: String?
}

/// Status response from the refresh status endpoint.
struct RefreshStatusResponse: Codable, Sendable {
    let active: Bool
    let status: String?
    let stage: String?
    let label: String?
    let detail: String?
}
