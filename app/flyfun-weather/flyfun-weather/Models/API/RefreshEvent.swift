import Foundation

/// SSE event from the refresh stream endpoint.
struct RefreshEvent: Codable, Sendable {
    let type: String // "progress", "briefing_ready", "complete", "error"

    // Progress fields
    let stage: String?
    let detail: String?
    let label: String?
    let progress: Double?

    // Complete fields
    let pack: PackMetaResponse?
    let elapsedSeconds: Double?

    // Present on the `complete` event when the tiered refresh gate returned a
    // no-op (`mode == "none"`) or a real-time-only refresh instead of running a
    // full pipeline. Carries the human-readable reason we surface to the user.
    let refreshDecision: RefreshDecision?

    // Error fields
    let message: String?
}

/// Outcome of the server-side refresh gate, attached to the `complete` event.
struct RefreshDecision: Codable, Sendable {
    let mode: String // "full" | "realtime" | "none"
    let reason: String?
    let etaUseful: String?
    let pendingModels: [String]?
}

/// Status response from the refresh status endpoint.
struct RefreshStatusResponse: Codable, Sendable {
    let active: Bool
    let status: String?
    let stage: String?
    let label: String?
    let detail: String?
}
