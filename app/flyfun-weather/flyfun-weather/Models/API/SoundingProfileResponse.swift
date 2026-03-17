import Foundation

/// Raw sounding profile from the API for client-side Skew-T rendering.
struct SoundingProfileResponse: Codable, Sendable {
    let pointIndex: Int
    let lat: Double
    let lon: Double
    let distanceFromOriginNm: Double
    let waypointIcao: String?
    let model: String
    let time: String
    let levels: [SoundingProfileLevel]
    let cruiseAltitudeFt: Int?
    let indices: ThermodynamicIndices?
    let cloudLayers: [EnhancedCloudLayer]?
    let icingZones: [IcingZone]?
    let inversionLayers: [InversionLayer]?
}

struct SoundingProfileLevel: Codable, Sendable {
    let pressureHpa: Int
    let altitudeFt: Double?
    let temperatureC: Double
    let dewpointC: Double?
    let windSpeedKt: Double?
    let windDirectionDeg: Double?
}
