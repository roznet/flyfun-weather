import SwiftUI

// MARK: - Route-level visualization data

struct VizRouteData {
    let points: [VizPoint]
    let cruiseAltitudeFt: Double
    let ceilingAltitudeFt: Double
    let flightCeilingFt: Double  // Y-axis max = max(ceiling, cruise) + 5000
    /// Top of the fetched wind/cloud column (ft) for the rendered model when a
    /// ceiling-limited fetch (#469/#474) truncated it (ICON-D2). Above it the
    /// cross-section has no wind / NWP-cloud data for this model, so a labelled
    /// line marks it as "not modeled" rather than an unexplained blank strip.
    /// nil = full column fetched.
    var fetchedColumnTopFt: Double? = nil
    let totalDistanceNm: Double
    let waypointMarkers: [WaypointMarker]
    let departureTime: String
    let flightDurationHours: Double
    let terrainProfile: [TerrainPoint]?
    /// Advisory highlight geometry (scrim + verdict ribbon, #374) for the
    /// tracked advisory × the rendered model. Unlike the fields above it is NOT
    /// built by `extractVizData` (the geometry lives in the advisories manifest,
    /// a separate artifact) — the cross-section scene attaches the derived value
    /// just before rendering, mirroring web `briefing-main`. nil → the highlight
    /// layer no-ops.
    var advisoryHighlights: VizAdvisoryHighlights? = nil
}

/// Domain mirror of the API `AdvisoryHighlights` (#374). `Equatable` because the
/// static cross-section scene's redraw gate compares it by value — the geometry
/// is re-derived on every body evaluation, so identity can't be used.
struct VizAdvisoryHighlights: Equatable {
    struct Segment: Equatable {
        let distFromNm: Double
        let distToNm: Double
        let severity: String
    }

    /// `baseFt`/`topFt` both nil = full column (terrain-to-top).
    struct Region: Equatable {
        let distFromNm: Double
        let distToNm: Double
        let baseFt: Double?
        let topFt: Double?
        let kind: String
        let severity: String
    }

    let ribbon: [Segment]
    let regions: [Region]
    let peakDistNm: Double?

    init(ribbon: [Segment], regions: [Region], peakDistNm: Double?) {
        self.ribbon = ribbon
        self.regions = regions
        self.peakDistNm = peakDistNm
    }

    init(from api: AdvisoryHighlights) {
        ribbon = api.ribbon.map {
            Segment(distFromNm: $0.distFromNm, distToNm: $0.distToNm, severity: $0.severity)
        }
        regions = api.regions.map {
            Region(distFromNm: $0.distFromNm, distToNm: $0.distToNm,
                   baseFt: $0.baseFt, topFt: $0.topFt, kind: $0.kind, severity: $0.severity)
        }
        peakDistNm = api.peakDistNm
    }
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
    /// Native NWP cloud envelope (GRIB or model-percentage-derived).
    /// nil = no NWP source for this point; [] = clear sky.
    let nwpCloudLayers: [VizCloudLayer]?
    let icingZones: [VizIcingZone]
    let icingOgimetNwpZones: [VizIcingZone]
    let sfipZones: [VizSfipZone]
    let catLayers: [VizCATLayer]
    let inversions: [VizInversionLayer]
    let convectiveRisk: String
    let convectiveBaseFt: Double?
    let convectiveTopFt: Double?
    let nwpConvectiveRisk: String
    let nwpConvectiveBaseFt: Double?
    let nwpConvectiveTopFt: Double?
    let nwpConvectiveCoverPct: Double?
    let nwpConvectiveMethod: String?
    let hasNwpConvective: Bool
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
    /// Granular model cloud fraction (NWP source); nil for DD layers. When
    /// present the natural/square NWP colour uses it directly instead of the
    /// 4-bucket coverage category — matches the web factory.
    let meanCloudCoverPct: Double?
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
