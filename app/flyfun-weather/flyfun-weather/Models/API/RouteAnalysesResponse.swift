import Foundation

/// Route analyses manifest — per-point sounding + wind data for all models.
struct RouteAnalysesResponse: Codable, Sendable {
    let routeName: String
    let targetDate: String
    let departureTime: String
    let flightDurationHours: Double
    let totalDistanceNm: Double
    let cruiseAltitudeFt: Int
    let models: [String]
    let analyses: [RoutePointAnalysis]
}

struct RoutePointAnalysis: Codable, Sendable {
    let pointIndex: Int
    let lat: Double
    let lon: Double
    let distanceFromOriginNm: Double
    let waypointIcao: String?
    let waypointName: String?
    let interpolatedTime: String
    let trackDeg: Double
    let windComponents: [String: WindComponent]
    let sounding: [String: SoundingAnalysis]
    let modelDivergence: [ModelDivergence]
}

/// Full sounding analysis for cross-section rendering.
struct SoundingAnalysis: Codable, Sendable {
    let indices: ThermodynamicIndices?
    let cloudLayers: [EnhancedCloudLayer]?
    /// Native NWP cloud envelope. nil = no NWP source for this model;
    /// empty array = model says clear sky. Non-empty = render layers.
    let nwpCloudLayers: [EnhancedCloudLayer]?
    let icingZones: [IcingZone]?
    let icingOgimetNwpZones: [IcingZone]?
    let sfipZones: [SfipZone]?
    let inversionLayers: [InversionLayer]?
    let convective: ConvectiveAssessment?
    let convectiveNwp: ConvectiveAssessment?
    let verticalMotion: VerticalMotionAssessment?
    let cloudCoverLowPct: Double?
    let cloudCoverMidPct: Double?
    let cloudCoverHighPct: Double?
    let nwpCloudDiagnostics: NWPCloudDiagnostics?
}

struct ThermodynamicIndices: Codable, Sendable {
    let lclPressureHpa: Double?
    let freezingLevelFt: Double?
    let minus10cLevelFt: Double?
    let minus20cLevelFt: Double?
    let lclAltitudeFt: Double?
    let lfcPressureHpa: Double?
    let lfcAltitudeFt: Double?
    let elPressureHpa: Double?
    let elAltitudeFt: Double?
    let capeSurfaceJkg: Double?
    let capeMostUnstableJkg: Double?
    let capeMixedLayerJkg: Double?
    let cinSurfaceJkg: Double?
    let liftedIndex: Double?
    let showalterIndex: Double?
    let kIndex: Double?
    let totalTotals: Double?
    let precipitableWaterMm: Double?
    let soundingCeilingFt: Double?
    let nwpCeilingFt: Double?
    let nwpCapeJkg: Double?
    let nwpCapeType: String?
    let nwpCinJkg: Double?
    let nwpLiftedIndex: Double?
    let nwpFreezingLevelFt: Double?
    let capeRawVsCalcDivergent: Bool?
}

struct EnhancedCloudLayer: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let coverage: String
    let meanDewpointDepressionC: Double?
}

struct IcingZone: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let risk: String
    let icingType: String
}

struct SfipZone: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let risk: String
    let icingType: String
    let meanSfip100: Double?
    let variant: String
}

struct InversionLayer: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let strengthC: Double
}

struct ConvectiveAssessment: Codable, Sendable {
    let riskLevel: String
    let baseFt: Double?
    let topFt: Double?
    let capeJkg: Double?
    let cinJkg: Double?
    let coverPct: Double?
    let method: String?
}

struct VerticalMotionAssessment: Codable, Sendable {
    let classification: String?
    let catRiskLayers: [CATRiskLayer]?
}

struct CATRiskLayer: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let risk: String
}

struct NWPCloudDiagnostics: Codable, Sendable {
    let low: NWPCloudLayerDiag
    let mid: NWPCloudLayerDiag
    let high: NWPCloudLayerDiag
    let ceilingFt: Double?
}

struct NWPCloudLayerDiag: Codable, Sendable {
    let coverPct: Double?
    let baseFt: Double?
    let topFt: Double?
}

struct ModelDivergence: Codable, Sendable {
    let variable: String
    let modelValues: [String: Double]
    let mean: Double
    let spread: Double
    let agreement: String
}
