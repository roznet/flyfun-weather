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

struct AdvisoryDetailResponse: Codable, Sendable {
    let advisoryId: String?
    let aggregateStatus: String?
    let aggregateDetail: String?
    let perModel: [AdvisoryDetailModelResult]
    let parametersUsed: [String: Double]
    let crossCheckNote: String?
    let name: String?
    let category: String?
    let description: String?
    let flightId: String?
    let briefingTimestamp: String?
    let routeName: String?
    let cruiseAltitudeFt: Int?
    let flightCeilingFt: Int?
    let totalDistanceNm: Double?
    let models: [String]?
    let aggregation: String?
    let convective: [String: AdvisoryConvectiveDetail]?
    let convectiveNote: String?

    var isEmpty: Bool {
        perModel.isEmpty
            && (aggregateDetail?.isEmpty ?? true)
            && (description?.isEmpty ?? true)
    }
}

struct AdvisoryDetailModelResult: Codable, Identifiable, Sendable {
    let model: String?
    let status: String?
    let detail: String?
    let affectedPct: Double?
    let affectedNm: Double?
    let totalNm: Double?
    let crossCheck: String?

    var id: String { model ?? UUID().uuidString }

    enum CodingKeys: String, CodingKey {
        case model
        case status
        case detail
        case affectedPct
        case affectedNm
        case totalNm
        case crossCheck
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        detail = try container.decodeIfPresent(String.self, forKey: .detail)
        affectedPct = try container.decodeIfPresent(Double.self, forKey: .affectedPct)
        affectedNm = try container.decodeIfPresent(Double.self, forKey: .affectedNm)
        totalNm = try container.decodeIfPresent(Double.self, forKey: .totalNm)
        crossCheck = Self.decodeFlexibleText(from: container, forKey: .crossCheck)
    }

    private static func decodeFlexibleText(
        from container: KeyedDecodingContainer<CodingKeys>,
        forKey key: CodingKeys
    ) -> String? {
        if let value = try? container.decodeIfPresent(String.self, forKey: key) {
            return value
        }
        if let value = try? container.decodeIfPresent(Double.self, forKey: key) {
            return String(format: "%.1f", value)
        }
        if let value = try? container.decodeIfPresent(Int.self, forKey: key) {
            return String(value)
        }
        if let value = try? container.decodeIfPresent(Bool.self, forKey: key) {
            return value ? "true" : "false"
        }
        if let dict = try? container.decodeIfPresent([String: String].self, forKey: key) {
            return dict.sorted { $0.key < $1.key }
                .map { "\($0.key): \($0.value)" }
                .joined(separator: " · ")
        }
        if let dict = try? container.decodeIfPresent([String: Double].self, forKey: key) {
            return dict.sorted { $0.key < $1.key }
                .map { "\($0.key): \(String(format: "%.1f", $0.value))" }
                .joined(separator: " · ")
        }
        return nil
    }
}

struct AdvisoryConvectiveDetail: Codable, Sendable {
    let assessmentMethod: String?
    let methodCounts: [String: Int]
    let thermo: AdvisoryThermoDetail?
    let nwp: AdvisoryNwpDetail?
}

struct AdvisoryThermoDetail: Codable, Sendable {
    let capeRangeJkg: [Double]?
    let peak: AdvisoryThermoPeak?
}

struct AdvisoryThermoPeak: Codable, Sendable {
    let capeJkg: Double?
    let elTopFt: Double?
    let riskLevel: String?
    let distanceNm: Double?
    let waypointIcao: String?
    let validTime: Int?
    let eta: String?
}

struct AdvisoryNwpDetail: Codable, Sendable {
    let maxCoverPct: Double?
    let peakTopFt: Double?
    let note: String?
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
    let crossCheck: String?

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
