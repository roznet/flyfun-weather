import Foundation

/// Per-advisory drill-down for the "why it's RED" ladder (§4.6). Mirrors the
/// shaping in `connectors/views.py` exposed over REST in Phase 5 — the same
/// data the MCP/ChatGPT connectors consume. The convective advisory carries
/// the richest fill (CAPE-vs-cover reconciliation).
struct AdvisoryDetailResponse: Codable, Sendable {
    let advisoryId: String
    let aggregateStatus: String
    let aggregateDetail: String
    let perModel: [ModelAdvisoryDetail]
    let parametersUsed: [String: Double]
    /// CARDINAL RULE: this is an EXPLAINER, never an alert — render in neutral
    /// chrome, never amber/red. Optional/defensive: when present it's shown as
    /// the explainer; absence simply omits it (decode stays resilient).
    let crossCheckNote: String?
    let name: String?
    let category: String?
    let description: String?
    /// Per-model convective reconciliation, keyed by model name. Present only
    /// for the convective advisory.
    let convective: [String: ConvectiveModelDetail]?
    let convectiveNote: String?
}

struct ModelAdvisoryDetail: Codable, Identifiable, Sendable {
    let model: String
    let status: String
    let detail: String
    let affectedPct: Double?
    let affectedNm: Double?
    let totalNm: Double?
    /// The extent's own denominator in nm (#571); nil on old packs.
    let domainNm: Double?
    /// Names a non-route denominator ("of high terrain"); nil means the route.
    /// Required to qualify `affectedPct` — see `ModelAdvisoryResult`.
    let affectedDomain: String?
    /// Display-only context; render in neutral/info chrome (see CARDINAL RULE).
    let crossCheck: String?

    var id: String { model }
}

struct ConvectiveModelDetail: Codable, Sendable {
    /// "thermo" or "nwp" — which derivation graded the route, or nil.
    let assessmentMethod: String?
    let methodCounts: [String: Int]?
    let thermo: ConvectiveThermo?
    let nwp: ConvectiveNwp?
}

struct ConvectiveThermo: Codable, Sendable {
    /// [min, max] CAPE across the route.
    let capeRangeJkg: [Double]?
    let peak: ConvectivePeak?
}

struct ConvectivePeak: Codable, Sendable {
    let capeJkg: Double?
    /// Parcel equilibrium level — the "convective tops" the digest narrates
    /// (NOT the model's convective cloud field).
    let elTopFt: Double?
    let riskLevel: String?
    let distanceNm: Double?
    let waypointIcao: String?
    let validTime: String?
    let eta: String?
}

struct ConvectiveNwp: Codable, Sendable {
    /// The model's own convective cover — ~0 means "blue sky".
    let maxCoverPct: Double?
    let peakTopFt: Double?
    let note: String?
}
