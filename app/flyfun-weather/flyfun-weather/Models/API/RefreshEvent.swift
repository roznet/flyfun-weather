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

    // Freshly fetched D-0 data on the `realtime` gate path, mirroring the
    // non-streaming `RefreshAccepted` so a consumer needs no follow-up reload.
    // That is not a convenience here, it is the fix: a gated realtime refresh
    // leaves the pack timestamp alone, and `CachingBriefingRepository` is
    // cache-first, so re-reading the snapshot of a DOWNLOADED pack would hand
    // back exactly the stale copy the user just pressed ↻ to replace.
    // All nil on the `none` path and on full-pipeline completes (which do mint a
    // new pack, and reload normally).
    let observations: RouteObservations?
    let sigmets: RouteSigmets?
    /// Re-sampled radar / lightning / satellite cloud tops (#574). Costs the
    /// server no network I/O — the collector has been writing frames all along —
    /// which is why it rides on the cheap path.
    let observed: ObservedConditions?
    var observedMotion: RawObservedMotion? = nil
    var observedMotionCapability: ObservedMotionCapability? = nil
    var observedMotionCapabilitySequence: Int? = nil

    // Error fields
    let message: String?

    enum CodingKeys: String, CodingKey {
        case type, stage, detail, label, progress, pack, elapsedSeconds
        case refreshDecision, observations, sigmets, observed, message
    }

    static func decodePreservingObservedMotion(
        from data: Data, capability: ObservedMotionCapability? = nil, capabilitySequence: Int? = nil
    ) throws -> RefreshEvent {
        let raw = RawJSONDocument(data).member(named: "observed_motion")
        let withoutMotion = try RawJSONDocument(data).replacingMember(named: "observed_motion", with: Data("null".utf8))
        var event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: withoutMotion)
        if let raw, String(decoding: raw, as: UTF8.self) != "null" {
            event.observedMotion = RawObservedMotion(rawJSON: raw)
        }
        event.observedMotionCapability = capability
        event.observedMotionCapabilitySequence = capabilitySequence
        return event
    }
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
    /// Stage-derived completion fraction (0...1), mirroring the SSE stream's
    /// `progress`. Lets the poll path drive the progress bar after the user
    /// navigates away and back. Absent on older servers → treat as 0.
    let progress: Double?
}
