import Foundation

nonisolated struct ElevationResponse: Codable, Sendable {
    let routeName: String
    let points: [ElevationPoint]
    let maxElevationFt: Double
    let totalDistanceNm: Double
}

nonisolated struct ElevationPoint: Codable, Sendable {
    let distanceNm: Double
    let elevationFt: Double
    let lat: Double
    let lon: Double
}
