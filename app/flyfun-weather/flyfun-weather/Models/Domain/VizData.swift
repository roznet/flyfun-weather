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

extension Array where Element == TerrainPoint {
    /// Terrain elevation (ft MSL) at an along-route distance, linearly
    /// interpolated between the two bracketing samples and clamped to the
    /// profile's ends. Port of web `interpolateTerrainElevation`
    /// (`web/ts/visualization/data-extract.ts`).
    ///
    /// Interpolating rather than snapping matters for the AGL ceiling metrics:
    /// route points and elevation samples are on different grids, so a snap can
    /// put a ceiling several hundred feet off over rising ground — enough to
    /// flip which side of a VFR minimum it reads as.
    func elevationFt(atDistanceNm distanceNm: Double) -> Double {
        guard let first, let last else { return 0 }
        if distanceNm <= first.distanceNm { return first.elevationFt }
        if distanceNm >= last.distanceNm { return last.elevationFt }
        for (a, b) in zip(self, dropFirst())
        where distanceNm >= a.distanceNm && distanceNm <= b.distanceNm {
            let span = b.distanceNm - a.distanceNm
            guard span > 0 else { return a.elevationFt }
            let t = (distanceNm - a.distanceNm) / span
            return a.elevationFt + t * (b.elevationFt - a.elevationFt)
        }
        return 0
    }
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
    /// Surface-based CIN (J/kg) — companion to `capeSurfaceJkg`, and like it read
    /// straight off the selected model's sounding indices rather than
    /// `model_divergence`. Convention-negative: energy that inhibits convection.
    ///
    /// `var` with a default, like the trailing fields below: the extractor always
    /// supplies a real value, but the test fixtures build points field-by-field
    /// and a `let` default would be dropped from the memberwise initializer.
    var cinSurfaceJkg: Double = 0
    /// Sounding-derived ceiling (ft MSL), nil when the point has no sounding.
    /// The route graph converts to AGL against `terrainElevationFt`.
    var soundingCeilingFt: Double? = nil
    /// Terrain elevation at this point (ft MSL), for AGL conversion. Linearly
    /// interpolated from the elevation profile, matching the web extractor —
    /// unlike the observed strip's deliberate nearest-neighbour snap, which
    /// wants the sampled ground it draws on, not a value between samples.
    var terrainElevationFt: Double = 0
    /// ISA deviation (°C) at the elected cruise level: actual − ISA standard.
    /// Positive = warmer than standard (higher density altitude, degraded
    /// TAS/climb). nil when the pack carries no cruise temperature.
    var isaDevC: Double? = nil
    /// QNH (hPa, canonical) from `model_divergence.pressure_msl_hpa`.
    var qnhHpa: Double? = nil
    /// The observed sample matched to this point by along-route distance, so a
    /// hover can report everything measured here without re-deriving the match.
    /// nil when no observed station fell within `observedMatchToleranceNm`.
    var observed: VizObservedPoint? = nil

    // MARK: Observed convenience accessors
    //
    // The web extractor mirrors the measured scalars onto VizPoint itself so a
    // metric getter is a one-liner; iOS keeps them nested under `observed`.
    // These bridge the two shapes so the route-graph metric table reads the same
    // on both platforms rather than reaching through an optional inline.

    /// Observed radar rain rate (mm/h). nil means EITHER the radar looked and
    /// found nothing OR it does not cover this point — `observedRadarNoCoverage`
    /// is what disambiguates, and a renderer must not collapse the two.
    var observedRateMmH: Double? { observed?.rateMmH }

    /// True when the radar does not cover enough of this disc to say anything.
    /// Either coverage flag alone is enough: if the composite does not reach here
    /// there is no rate to derive either, so both gate the rate metric — matching
    /// the web extractor's `radarNoCoverage || rateNoCoverage`.
    var observedRadarNoCoverage: Bool {
        guard let observed else { return false }
        return observed.radarNoCoverage || observed.rateNoCoverage
    }

    /// Flashes per 1000 km² per minute — comparable between corridor widths.
    var observedFlashRate: Double? { observed?.flashRate }
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
