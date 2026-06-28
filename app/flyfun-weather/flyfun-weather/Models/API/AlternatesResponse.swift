import Foundation

/// Weather-based alternate airports for a route's destination (D-2 inward).
/// Swift mirror of `models/alternates.py:RouteAlternates`; decoded as a nested
/// field on `SnapshotResponse`. We decode the display subset — the regulatory
/// `faa`/`easa` qual is reduced to verdict + reason (the full criterion bands
/// stay web-only). See `designs/future/alternates.md`.
struct RouteAlternates: Codable, Sendable {
    let destinationIcao: String
    let destinationCategory: String
    let destinationCrosswindKt: Double?
    let corridorNm: Double?
    let radiusNm: Double?
    /// Destination itself is MVFR/IFR/LIFR (informational).
    let requireApproach: Bool?
    /// Per-candidate IAP gate was skipped (no procedure data present).
    let approachFilterRelaxed: Bool?
    let candidatesEvaluated: Int?
    /// Ranked closest-first.
    let alternates: [AlternateAirport]
    let nearestImproving: [AlternateAxisPick]
    /// Destination "is a filed alternate required?" (FAA + EASA). Filled by the
    /// regulatory post-step; nil on older packs.
    let alternateRequirement: AlternateRequirement?
}

struct AlternateAirport: Codable, Identifiable, Sendable {
    let icao: String
    let name: String?

    // Geometry relative to route + destination.
    let distanceFromDestNm: Double
    let position: String          // "before" | "after"
    let detourEarlyNm: Double?
    let detourLateNm: Double?

    // Consensus assessment (same shape as the forecast-map consensus).
    let flightCategory: String
    let windSpeedKt: Double?
    let crosswindKt: Double?
    let headwindKt: Double?
    let bestRunwayId: String?
    let ceilingFt: Double?
    let visibilityM: Double?
    let agreement: [String: String]?

    // Suitability.
    let hasInstrumentApproach: Bool?
    let bestApproachType: String?   // ILS/RNP/RNAV/... precision tier (minima proxy)
    let isMajor: Bool?              // type == large_airport — hidden by default

    // vs-destination flags.
    let betterCategory: Bool?
    let betterWind: Bool?
    let betterCrosswind: Bool?
    let dominatesDestination: Bool?  // better-or-equal on all axes (Pareto) → "Better"

    // Regulatory alternate-minima qualification (display subset).
    let faa: AlternateQual?
    let easa: AlternateQual?

    var id: String { icao }

    /// "+8 nm early / +47 nm late" — the early-vs-late divert cost pair.
    var detourLabel: String? {
        let parts: [String] = [
            detourEarlyNm.map { "+\(Int($0.rounded())) nm early" },
            detourLateNm.map { "+\(Int($0.rounded())) nm late" },
        ].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " / ")
    }
}

/// The nearest improving alternate for one deficient axis.
struct AlternateAxisPick: Codable, Identifiable, Sendable {
    let axis: String              // "category" | "wind" | "crosswind"
    let icao: String?
    let distanceFromDestNm: Double?
    let position: String?

    var id: String { axis }

    /// Human label for the axis, matching `ALT_AXIS_LABELS` on the server.
    var axisLabel: String {
        switch axis {
        case "category": "better category"
        case "wind": "lower wind"
        case "crosswind": "lower crosswind"
        default: axis
        }
    }
}

/// Per-candidate alternate-minima qualification (display subset of
/// `models/alternate_requirement.py:AlternateQual`).
struct AlternateQual: Codable, Sendable {
    let regime: String            // "faa" | "easa"
    let verdict: String           // likely | marginal | unlikely | not_required | required
    let reason: String?
}

/// Destination "is a filed alternate required?" (display subset of
/// `AlternateRequirement` + its per-regime `RegAlternateTrigger`).
struct AlternateRequirement: Codable, Sendable {
    let destinationIcao: String?
    let faa: RegAlternateTrigger?
    let easa: RegAlternateTrigger?
}

struct RegAlternateTrigger: Codable, Sendable {
    let regime: String            // "faa" | "easa"
    let status: String            // not_required | required | likely | marginal | unlikely
    let reason: String?
}
