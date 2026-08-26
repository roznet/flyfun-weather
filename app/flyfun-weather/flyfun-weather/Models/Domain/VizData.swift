import SwiftUI

// MARK: - Route-level visualization data

struct VizRouteData {
    /// `var` because changing the observed corridor width re-resolves the discs
    /// and re-folds them onto these points in place — every sampled radius already
    /// shipped with the pack, so it must not cost a rebuild from the API response.
    var points: [VizPoint]
    let cruiseAltitudeFt: Double
    let ceilingAltitudeFt: Double
    let flightCeilingFt: Double  // Y-axis max = max(ceiling, cruise) + 5000
    let totalDistanceNm: Double
    let waypointMarkers: [WaypointMarker]
    let departureTime: String
    let flightDurationHours: Double
    let terrainProfile: [TerrainPoint]?
    /// D-0 observed conditions resolved to one sample per route point at the
    /// selected corridor width (#574). Built by `extractVizData` from the
    /// snapshot's `observedConditions`; nil on D-1+ packs, on a deployment with
    /// the collector switched off, and on any pack built before #574.
    var observed: VizObserved? = nil
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
    /// The observed sample matched to this point by along-route distance, so a
    /// hover can report everything measured here without re-deriving the match.
    /// nil when no observed station fell within `observedMatchToleranceNm`.
    var observed: VizObservedPoint? = nil
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

// MARK: - Observed conditions (#574)
//
// SYNC — mirrors the `VizObserved*` interfaces in
// web/ts/visualization/types.ts and the `buildObserved` resolver in
// web/ts/visualization/data-extract.ts. These are the *resolved* shapes: the
// API payload carries every station × every radius, and the resolver collapses
// it to one sample per route point at one corridor width, so switching the
// corridor is a client-side re-resolve with no request.

/// One populated flight-level band of the cloud-top histogram.
struct VizObservedTopBin {
    let label: String
    let loFt: Double
    let hiFt: Double
    /// Share of the disc's LOOKED-AT SKY with its top in this band, 0–1.
    ///
    /// Denominator is `validPx` — every pixel the retrieval could answer for,
    /// cloudy or clear — not the cloudy pixels alone. So a band reads directly
    /// as "this much of the sky around the point", the bands sum to how cloudy
    /// the disc was, and a thin deck in an otherwise clear sky can no longer
    /// draw as bright as a solid overcast simply for being most of the little
    /// cloud there was.
    ///
    /// It is also what the drawing floor is measured against, so "5% of the sky"
    /// means the same thing in the filter, in the colour ramp and in the legend.
    let fraction: Double
    /// Pixels in this band, so a hover can say "12 of 201" rather than only a
    /// percentage — 4% of 201 and 4% of 3 are very different evidence.
    let count: Int
}

/// Every observed quantity at one route point, for the selected corridor.
///
/// The three-state split survives the trip: `dbz == nil` with
/// `radarNoCoverage == false` means the radar looked and found no echo, while
/// `radarNoCoverage == true` means it does not see there at all. Collapsing the
/// two would paint about half the OPERA grid as clear sky.
struct VizObservedPoint {
    let distanceNm: Double
    /// Peak reflectivity (dBZ), or nil when nothing was detected.
    var dbz: Double? = nil
    /// True when the radar does not cover enough of this disc to say anything.
    /// Renderers MUST distinguish this from `dbz == nil`.
    var radarNoCoverage = false
    var rateMmH: Double? = nil
    var rateNoCoverage = false
    var flashCount = 0
    /// Flashes per 1000 km² per minute — comparable between corridor widths.
    var flashRate: Double? = nil
    /// Highest observed cloud top (ft), or nil when the disc was clear.
    var topsHighestFt: Double? = nil
    var topsBins: [VizObservedTopBin] = []
    /// Share of cloudy pixels the retrieval flagged multi-layer-suspect (qm 9).
    var topsMultiLayerFraction: Double = 0
    var topsNoCoverage = false
    /// Coldest top in the disc (°C). Deepest convection, not an average.
    var topsColdestC: Double? = nil
    /// Effective cloudiness at the highest top, 0–1. Separates a solid deck from
    /// wispy cirrus — height alone renders both identically.
    var topsHighestCloudiness: Double? = nil
    var topsMedianCloudiness: Double? = nil
    /// Pressure-based FL of the highest top, what an altimeter agrees with.
    /// Coarse (10 FL steps) and can diverge from the geometric `topsHighestFt`,
    /// so it is secondary to it, never a replacement.
    var topsHighestAviationFl: Double? = nil
}

/// Per-source identity and age. There is no combined timestamp on purpose —
/// the four streams are minutes apart and nothing here lets a client pretend
/// otherwise.
struct VizObservedSource {
    let source: String
    let label: String
    let validTime: String
    let ageMinutes: Double
    /// Width of the product's own accumulation / rolling-max window; 0 = instant.
    let windowMinutes: Double
    let attribution: String
}

struct VizObserved {
    /// All sampled radii — switching between them is a client-side pick.
    let radiiNm: [Double]
    /// The radius these `points` were resolved at.
    let radiusNm: Double
    let points: [VizObservedPoint]
    let reflectivity: VizObservedSource?
    let rainRate: VizObservedSource?
    let cloudTops: VizObservedSource?
    let lightning: VizObservedSource?
    let summaryLines: [String]
}
