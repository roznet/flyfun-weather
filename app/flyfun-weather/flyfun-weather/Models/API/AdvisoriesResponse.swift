import Foundation

struct AdvisoriesResponse: Codable, Sendable {
    let advisories: [RouteAdvisoryResult]
    let catalog: [AdvisoryCatalogEntry]
    let routeName: String
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
    let totalDistanceNm: Double
    let models: [String]
    let aggregation: String
    let airportConditions: AirportConditions?
}

struct RouteAdvisoryResult: Codable, Identifiable, Sendable {
    let advisoryId: String
    let aggregateStatus: String
    let aggregateDetail: String
    let perModel: [ModelAdvisoryResult]
    let parametersUsed: [String: Double]
    /// Advice-only mitigations (representative-model policy). A mitigation NEVER
    /// changes `aggregateStatus` — same "informs but doesn't grade" contract as
    /// `crossCheck`. Optional so old packs decode cleanly.
    let aggregateMitigations: [Mitigation]?

    var id: String { advisoryId }
}

struct ModelAdvisoryResult: Codable, Identifiable, Sendable {
    let model: String
    let status: String
    let detail: String
    let affectedPoints: Int
    let totalPoints: Int
    let affectedPct: Double
    let affectedNm: Double
    let totalNm: Double
    /// The extent's own denominator in nm (#571). Equals `totalNm` for every
    /// advisory whose domain is the whole route. Optional so old packs decode.
    let domainNm: Double?
    /// Names a denominator that is NOT the whole route ("of high terrain"); nil
    /// means the route. Any UI showing `affectedPct` MUST qualify it with this
    /// — Mountain Wind measures coverage over the route's mountain points, and
    /// an unqualified "93% affected" read as route coverage for a 132 nm
    /// footprint on a 582 nm route.
    let affectedDomain: String?
    /// Display-only context explaining a grade (e.g. high CAPE while NWP scheme is
    /// quiet). Present mainly for convective; nil otherwise. Per `CROSS_CHECK_NOTE`
    /// this is an EXPLAINER, never an alert — render in neutral chrome, never amber/red.
    let crossCheck: String?
    /// Per-model advice-only mitigations. Never alters `status`. Optional so old
    /// packs decode cleanly.
    let mitigations: [Mitigation]?
    /// Cross-section highlight geometry (scrim regions + verdict ribbon, #374).
    /// Absent/null on old packs and for evaluators that don't emit it — the
    /// feature is then silently absent (chip behaves as before highlights).
    let highlights: AdvisoryHighlights?

    var id: String { model }
}

/// Cross-section highlight geometry for one advisory × one model (#374).
/// The backend owns the geometry; the client only renders it — so any advisory
/// that gains an emitter server-side lights up here with no app change.
struct AdvisoryHighlights: Codable, Sendable {
    /// 1-D route verdict. Segments tile `[0, total_nm]` exactly (gapless).
    let ribbon: [RibbonSegment]
    /// 2-D scrim cutouts — where the hazard physically is (flagged areas only).
    let regions: [HighlightRegion]
    /// Distance of the advisory's peak (worst point) along the route, if any.
    let peakDistNm: Double?
}

/// One run of the 1-D route verdict strip.
struct RibbonSegment: Codable, Sendable {
    let distFromNm: Double
    let distToNm: Double
    let severity: String  // "green" | "amber" | "red" | "unavailable"
}

/// One scrim cutout. `baseFt`/`topFt` both nil = full column (terrain-to-top).
struct HighlightRegion: Codable, Sendable {
    let distFromNm: Double
    let distToNm: Double
    let baseFt: Double?
    let topFt: Double?
    let kind: String
    let severity: String
}

/// A decision that could improve a flagged sub-issue — advice only.
///
/// A mitigation NEVER changes an advisory's grade (same contract as
/// `crossCheck`). `mitigatedStatus` is the status of the ADDRESSED sub-issue if
/// applied — NOT the advisory overall. `addresses` is a stable English machine
/// tag (never displayed raw); `detail` is already localized server-side.
/// Keys arrive snake_case and map via the decoder's `.convertFromSnakeCase`
/// strategy (`mitigated_status`, `altitude_ft`, `distance_nm`).
struct Mitigation: Codable, Identifiable, Sendable {
    let kind: String            // "altitude" | "route_position" | "timing"
    let addresses: String       // machine tag, e.g. "cruise_imc" — not for display
    let detail: String          // localized human phrasing
    let mitigatedStatus: String // status of the addressed sub-issue if applied
    let altitudeFt: Int?
    let distanceNm: Double?
    let reference: String?      // "departure" / "arrival"

    var id: String { "\(kind)-\(addresses)-\(detail)" }
}

struct AdvisoryCatalogEntry: Codable, Identifiable, Sendable {
    let id: String
    let name: String
    let shortDescription: String
    let description: String
    let category: String
    let defaultEnabled: Bool
    let altitudeDependent: Bool
    let parameters: [AdvisoryParameterDef]
}

struct AdvisoryParameterDef: Codable, Sendable {
    let key: String
    let label: String
    let description: String
    let type: String
    let unit: String
    let `default`: Double
    let min: Double?
    let max: Double?
    let step: Double?
}

struct AirportConditions: Codable, Sendable {
    let departure: AirportConditionsSummary
    let arrival: AirportConditionsSummary
}

struct AirportConditionsSummary: Codable, Sendable {
    let icao: String
    let name: String
    let runwayEnds: [RunwayEnd]
    let conditions: [AirportModelCondition]
}

struct RunwayEnd: Codable, Sendable {
    let id: String
    let headingDeg: Double
}

struct AirportModelCondition: Codable, Identifiable, Sendable {
    let model: String
    let flightCategory: String
    let ceilingFt: Int?
    let visibilitySm: Double?
    let windSpeedKt: Double?
    let windDirectionDeg: Double?
    let windGustKt: Double?
    let bestRunway: RunwayWind?
    let allRunways: [RunwayWind]

    var id: String { model }
}

struct RunwayWind: Codable, Sendable {
    let runwayId: String
    let headingDeg: Double
    let crosswindKt: Double
    let headwindKt: Double
}
