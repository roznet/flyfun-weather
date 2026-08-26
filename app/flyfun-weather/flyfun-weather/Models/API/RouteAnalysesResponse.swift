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
    let freezingLevelFt: Double?
    let minus10cLevelFt: Double?
    let minus20cLevelFt: Double?
    let lclAltitudeFt: Double?
    let lfcAltitudeFt: Double?
    let elAltitudeFt: Double?
    let capeSurfaceJkg: Double?
    let soundingCeilingFt: Double?
    let nwpCeilingFt: Double?

    // Phase 4 wiring — server already sends these; the Skew-T package renders
    // LCL/LFC/EL markers + the lifted index / CIN from them. Pressures let the
    // markers sit at the exact level without an altitude→pressure conversion.
    let lclPressureHpa: Double?
    let lfcPressureHpa: Double?
    let elPressureHpa: Double?
    let cinSurfaceJkg: Double?
    let liftedIndex: Double?
}

struct EnhancedCloudLayer: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let coverage: String
    let meanDewpointDepressionC: Double?
    /// `mean_cloud_cover_pct` — model cloud fraction for NWP layers (band
    /// cover% for GRIB, mean CAF for nwp_3d); nil for DD layers.
    let meanCloudCoverPct: Double?
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
    /// Top of the detected surface well-mixed layer (#533); CAT layers below
    /// it are suppressed server-side. Optional — absent on pre-#534 packs.
    let mixedLayerTopFt: Double?
    /// The model's own ground in the column's height datum (#541), resolved
    /// from its surface pressure. The AGL datum for `boundaryLayer`, and the
    /// cut below which levels are sub-surface extrapolation. Optional —
    /// absent on pre-#541 packs.
    let modelSurfaceAltitudeFt: Double?
}

struct CATRiskLayer: Codable, Sendable {
    let baseFt: Double
    let topFt: Double
    let risk: String
    /// Layer lies wholly inside the boundary layer (#533): a SEVERE one is
    /// coverage-gated rather than forcing RED, and reads as "low-level wind
    /// shear" rather than CAT. Optional — absent on pre-#534 packs.
    let boundaryLayer: Bool?
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
