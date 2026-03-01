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

    var id: String { model }
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
