import SwiftUI

// MARK: - Route-level visualization data

struct VizRouteData {
    let points: [VizPoint]
    let cruiseAltitudeFt: Double
    let ceilingAltitudeFt: Double
    let flightCeilingFt: Double  // Y-axis max = max(ceiling, cruise) + 5000
    let totalDistanceNm: Double
    let waypointMarkers: [WaypointMarker]
    let departureTime: String
    let flightDurationHours: Double
    let terrainProfile: [TerrainPoint]?
}

struct WaypointMarker {
    let distanceNm: Double
    let icao: String
    let lat: Double
    let lon: Double
}

struct TerrainPoint {
    let distanceNm: Double
    let elevationFt: Double
}

// MARK: - Per-point data

struct VizPoint {
    let distanceNm: Double
    let lat: Double
    let lon: Double
    let time: String
    let altitudeLines: AltitudeLines
    let cloudLayers: [VizCloudLayer]
    let icingZones: [VizIcingZone]
    let icingOgimetNwpZones: [VizIcingZone]
    let sfipZones: [VizSfipZone]
    let catLayers: [VizCATLayer]
    let inversions: [VizInversionLayer]
    let convectiveRisk: String
    let cloudCoverTotalPct: Double
    let cloudCoverLowPct: Double
    let cloudCoverMidPct: Double
    let headwindKt: Double
    let crosswindKt: Double
    let capeSurfaceJkg: Double
    let worstModelAgreement: String
    let nwpCloudDiag: VizCloudDiag?
    let temperatureC: Double?
    let precipitationMm: Double?
}

struct AltitudeLines {
    let freezingLevelFt: Double?
    let minus10cLevelFt: Double?
    let minus20cLevelFt: Double?
    let lclAltitudeFt: Double?
    let lfcAltitudeFt: Double?
    let elAltitudeFt: Double?
}

// MARK: - Layer data

struct VizCloudLayer {
    let baseFt: Double
    let topFt: Double
    let coverage: String
    let meanDewpointDepressionC: Double?
}

struct VizIcingZone {
    let baseFt: Double
    let topFt: Double
    let risk: String
    let type: String
}

struct VizSfipZone {
    let baseFt: Double
    let topFt: Double
    let risk: String
    let type: String
    let meanSfip100: Double?
    let variant: String
}

struct VizCATLayer {
    let baseFt: Double
    let topFt: Double
    let risk: String
}

struct VizInversionLayer {
    let baseFt: Double
    let topFt: Double
    let strengthC: Double
}

struct VizCloudDiag {
    let low: VizCloudDiagBand
    let mid: VizCloudDiagBand
    let high: VizCloudDiagBand
    let ceilingFt: Double?
}

struct VizCloudDiagBand {
    let coverPct: Double?
    let baseFt: Double?
    let topFt: Double?
}
