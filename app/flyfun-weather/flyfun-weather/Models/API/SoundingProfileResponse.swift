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
    let trackDeg: Double?
    let label: String?
    let indices: ThermodynamicIndices?
    let parcelPath: [ParcelPathPoint]?
    let cloudLayers: [EnhancedCloudLayer]?
    let nwpCloudLayers: [EnhancedCloudLayer]?
    let icingZones: [IcingZone]?
    let icingOgimetNwpZones: [IcingZone]?
    let sfipZones: [SfipZone]?
    let inversionLayers: [InversionLayer]?
    let convective: ConvectiveAssessment?
}

struct SoundingProfileLevel: Codable, Sendable {
    let pressureHpa: Int
    let altitudeFt: Double?
    let temperatureC: Double
    let dewpointC: Double?
    let windSpeedKt: Double?
    let windDirectionDeg: Double?
    let relativeHumidityPct: Double?
    let dewpointDepressionC: Double?
    let wetBulbC: Double?
    let thetaEK: Double?
    let lapseRateCPerKm: Double?
    let icingIndex: Double?
    let icingIndexNwp: Double?
    let sfip100: Double?
    let cloudLiquidWaterGM3: Double?
    let iceMixingRatioGKg: Double?
    let cloudAreaFractionPct: Double?
    let richardsonNumber: Double?
    let omegaPaS: Double?
    let wFpm: Double?
}

struct ParcelPathPoint: Codable, Sendable {
    let pressureHpa: Double
    let temperatureC: Double
}
