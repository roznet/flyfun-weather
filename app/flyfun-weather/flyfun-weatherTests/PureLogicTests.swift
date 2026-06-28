//
//  PureLogicTests.swift
//  flyfun-weatherTests
//
//  Tier A (#314) — pure-function unit tests for the math/colour/geometry that
//  draws every chart. These are the silent-regression class: wrong colours or
//  positions never crash, so we pin the boundaries here.
//
//  Style note: colour-threshold logic is tested by *bucket identity* — values
//  inside one bucket resolve to the same colour, values across a boundary do
//  not — so the tests survive palette tweaks and only fail if the *thresholds*
//  move. The continuous-scale clamps are tested the same way (out-of-range
//  inputs collapse to the endpoint colour).
//

import Testing
import SwiftUI
import UIKit
@testable import flyfun_weather

// MARK: - Colour helpers

/// Resolve a SwiftUI `Color` to sRGB components so two colours can be compared
/// for equality with a tolerance (SwiftUI `Color`'s own `==` is unreliable
/// across the semantic vs `.sRGB` providers we mix here).
private func rgba(_ color: Color) -> (r: Double, g: Double, b: Double, a: Double) {
    var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
    UIColor(color).getRed(&r, green: &g, blue: &b, alpha: &a)
    return (Double(r), Double(g), Double(b), Double(a))
}

private func sameColor(_ a: Color, _ b: Color, tol: Double = 0.001) -> Bool {
    let x = rgba(a), y = rgba(b)
    return abs(x.r - y.r) < tol && abs(x.g - y.g) < tol && abs(x.b - y.b) < tol && abs(x.a - y.a) < tol
}

// MARK: - CoordTransform (cross-section pixel mapping)

@Suite struct CoordTransformTests {
    // 400×200 plot, margins L50/R40/T20/B30 → plotArea left50 top20 width310 height150.
    private let t = CoordTransform(size: CGSize(width: 400, height: 200), maxDistanceNm: 100, maxAltitudeFt: 10000)

    @Test func distanceMapsLeftToRight() {
        #expect(abs(t.distanceToX(0) - 50) < 1e-6)        // origin at plot left
        #expect(abs(t.distanceToX(100) - 360) < 1e-6)     // max distance at plot right (50+310)
        #expect(abs(t.distanceToX(50) - 205) < 1e-6)      // midpoint
    }

    @Test func altitudeAxisIsInverted() {
        // High altitude = top (small y), ground = bottom (large y).
        #expect(abs(t.altitudeToY(10000) - 20) < 1e-6)    // ceiling at plot top
        #expect(abs(t.altitudeToY(0) - 170) < 1e-6)       // ground at plot bottom (20+150)
        #expect(t.altitudeToY(10000) < t.altitudeToY(0))
    }

    @Test func roundTripsBothAxes() {
        for d in [0.0, 12.5, 47.3, 100.0] {
            #expect(abs(t.xToDistance(t.distanceToX(d)) - d) < 1e-6)
        }
        for a in [0.0, 1234.0, 8000.0, 10000.0] {
            #expect(abs(t.yToAltitude(t.altitudeToY(a)) - a) < 1e-6)
        }
    }
}

// MARK: - Assessment / severityRank

@Suite struct AssessmentTests {
    @Test func severityOrdersRedWorstThenAmberThenGreen() {
        #expect(severityRank("red") == 3)
        #expect(severityRank("amber") == 2)
        #expect(severityRank("green") == 1)
        #expect(severityRank("unavailable") == 0)
        #expect(severityRank("") == 0)
        #expect(severityRank("red") > severityRank("amber"))
        #expect(severityRank("amber") > severityRank("green"))
        #expect(severityRank("green") > severityRank("bogus"))
    }

    @Test func colourMapping() {
        #expect(sameColor(Assessment.green.color, .green))
        #expect(sameColor(Assessment.amber.color, .orange))
        #expect(sameColor(Assessment.red.color, .red))
        #expect(sameColor(Assessment.unavailable.color, .gray))
    }

    @Test func labelIsUppercased() {
        #expect(Assessment.green.label == "GREEN")
        #expect(Assessment.unavailable.label == "UNAVAILABLE")
    }
}

// MARK: - Extensions

@Suite struct ExtensionsTests {
    @Test func shortModelName() {
        #expect("meteofrance".shortModelName == "MF")
        #expect("best_match".shortModelName == "BEST")
        #expect("ecmwf".shortModelName == "ECMWF")
        #expect("ECMWF".shortModelName == "ECMWF")   // case-insensitive
        #expect("gfs".shortModelName == "GFS")        // default → uppercased
        #expect("icon".shortModelName == "ICON")
    }

    @Test func queryParamExtraction() {
        let url = URL(string: "https://weather.flyfun.aero/brief?flight=42&model=ecmwf")!
        #expect(url.queryParam("flight") == "42")
        #expect(url.queryParam("model") == "ecmwf")
        #expect(url.queryParam("missing") == nil)
    }
}

// MARK: - MapColors (segment colour scales — threshold buckets)

@Suite struct MapColorsTests {
    @Test func ceilingMatchesFlightCategoryBoundaries() {
        #expect(sameColor(MapColors.ceiling(200), MapColors.flightCategory("LIFR")))
        #expect(sameColor(MapColors.ceiling(700), MapColors.flightCategory("IFR")))
        #expect(sameColor(MapColors.ceiling(2000), MapColors.flightCategory("MVFR")))
        #expect(sameColor(MapColors.ceiling(5000), MapColors.flightCategory("VFR")))
        // Boundaries flip the category exactly at 500 / 1000 / 3000.
        #expect(!sameColor(MapColors.ceiling(499), MapColors.ceiling(500)))
        #expect(!sameColor(MapColors.ceiling(999), MapColors.ceiling(1000)))
        #expect(!sameColor(MapColors.ceiling(2999), MapColors.ceiling(3000)))
    }

    @Test func flightCategoryPalette() {
        #expect(sameColor(MapColors.flightCategory("VFR"), .green))
        #expect(sameColor(MapColors.flightCategory("MVFR"), .blue))
        #expect(sameColor(MapColors.flightCategory("IFR"), .red))
        #expect(sameColor(MapColors.flightCategory("LIFR"), .purple))
        #expect(sameColor(MapColors.flightCategory("???"), .gray))
    }

    @Test func sfipBucketBoundariesAt20_50_80() {
        // Within a bucket → same colour; across the 20/50/80 lines → different.
        #expect(sameColor(MapColors.sfip(0), MapColors.sfip(20)))
        #expect(!sameColor(MapColors.sfip(20), MapColors.sfip(20.1)))
        #expect(sameColor(MapColors.sfip(21), MapColors.sfip(50)))
        #expect(!sameColor(MapColors.sfip(50), MapColors.sfip(50.1)))
        #expect(sameColor(MapColors.sfip(51), MapColors.sfip(80)))
        #expect(!sameColor(MapColors.sfip(80), MapColors.sfip(80.1)))
    }

    @Test func riskRanksAreFiveDistinctColours() {
        let colours = [MapColors.risk(0), MapColors.risk(1), MapColors.risk(2), MapColors.risk(3), MapColors.risk(4)]
        for i in colours.indices {
            for j in colours.indices where j > i {
                #expect(!sameColor(colours[i], colours[j]))
            }
        }
        // Rank is rounded: 0.4 → 0 (none), 0.6 → 1 (light).
        #expect(sameColor(MapColors.risk(0.4), MapColors.risk(0)))
        #expect(sameColor(MapColors.risk(0.6), MapColors.risk(1)))
    }

    @Test func headwindSignSplitsTailwindFromHeadwind() {
        // All tailwind/calm (kt ≤ 0) is one constant green; headwind scales away from it.
        #expect(sameColor(MapColors.headwind(0), MapColors.headwind(-30)))
        #expect(!sameColor(MapColors.headwind(0), MapColors.headwind(15)))
    }

    @Test func continuousScalesClampAtEnds() {
        // CAPE / temperature / cloud-cover saturate outside their domain.
        #expect(sameColor(MapColors.cape(-100), MapColors.cape(0)))
        #expect(sameColor(MapColors.cape(3000), MapColors.cape(2000)))
        #expect(sameColor(MapColors.temperature(-30), MapColors.temperature(-20)))
        #expect(sameColor(MapColors.temperature(50), MapColors.temperature(40)))
        #expect(sameColor(MapColors.cloudCover(-10), MapColors.cloudCover(0)))
        #expect(sameColor(MapColors.cloudCover(150), MapColors.cloudCover(100)))
        // …but the endpoints themselves are visibly different.
        #expect(!sameColor(MapColors.cape(0), MapColors.cape(2000)))
        #expect(!sameColor(MapColors.temperature(-20), MapColors.temperature(40)))
        #expect(!sameColor(MapColors.cloudCover(0), MapColors.cloudCover(100)))
    }
}

// MARK: - MapMetrics registry (exercises the private altitude helpers + linearWidth)

@Suite struct MapMetricsTests {

    private func metric(_ id: String) -> MapMetric {
        MapMetrics.metric(byId: id)!
    }

    @Test func linearWidthClampsAndScales() {
        // cloud-cover width = linearWidth(value, 100, 3, 18).
        let w = metric("cloud-cover-total").width
        #expect(abs(w(0) - 3) < 1e-6)       // min width
        #expect(abs(w(50) - 10.5) < 1e-6)   // midpoint
        #expect(abs(w(100) - 18) < 1e-6)    // max width
        #expect(abs(w(200) - 18) < 1e-6)    // clamped above max
        #expect(abs(w(-50) - 10.5) < 1e-6)  // uses |value|
    }

    @Test func segmentValueAveragesContinuousFields() {
        let m = metric("cloud-cover-total")  // aggregation .average
        let a = makeVizPoint(cloudCoverTotalPct: 20)
        let b = makeVizPoint(cloudCoverTotalPct: 60)
        #expect(m.segmentValue(a, b, altitudeFt: nil) == 40)
    }

    @Test func segmentValueTakesWorstForOrdinalRisk() {
        let m = metric("convective-risk")  // aggregation .worst, riskRank lookup
        let calm = makeVizPoint(convectiveRisk: "none")    // 0
        let bad = makeVizPoint(convectiveRisk: "severe")   // 3
        #expect(m.segmentValue(calm, bad, altitudeFt: nil) == 3)
    }

    @Test func segmentValueFallsBackWhenOneEndpointNil() {
        // temp-at-level returns nil without a freezing level; the segment should
        // then report the other endpoint's value, not nil.
        let m = metric("temp-at-level")
        let known = makeVizPoint(freezingLevelFt: 10000)   // 0°C at 10000 ft
        let unknown = makeVizPoint()                        // no freezing level → nil
        let v = m.segmentValue(known, unknown, altitudeFt: 10000)
        #expect(v != nil)
        #expect(abs((v ?? .nan) - 0) < 1e-6)
    }

    @Test func tempAtAltitudeInterpolatesAndExtrapolates() {
        let m = metric("temp-at-level")
        let p = makeVizPoint(freezingLevelFt: 10000, minus10cLevelFt: 20000, minus20cLevelFt: 30000)
        #expect(abs((m.getValue(p, 10000) ?? .nan) - 0) < 1e-6)     // at freezing level
        #expect(abs((m.getValue(p, 15000) ?? .nan) - -5) < 1e-6)    // half-way 0 → -10
        #expect(abs((m.getValue(p, 5000) ?? .nan) - 10) < 1e-6)     // below: +2°C/1000ft warmer
        #expect(abs((m.getValue(p, 35000) ?? .nan) - -30) < 1e-6)   // above: extrapolated colder
        #expect(m.getValue(makeVizPoint(), 10000) == nil)           // no freezing level → nil
    }

    @Test func icingRiskPrefersOgimetEnvelopeAndRespectsAltitudeSpan() {
        let m = metric("icing-risk-at-level")
        let diag = VizIcingZone(baseFt: 5000, topFt: 15000, risk: "moderate", type: "diag")  // rank 2
        let ogimet = VizIcingZone(baseFt: 0, topFt: 40000, risk: "severe", type: "nwp")       // rank 3
        // When OGIMET-NWP zones are present they win over the diagnostic zones.
        let p = makeVizPoint(icingZones: [diag], icingOgimetNwpZones: [ogimet])
        #expect((m.getValue(p, 10000) ?? -1) == 3)
        // Fall back to diagnostic zones when no OGIMET envelope.
        let pDiag = makeVizPoint(icingZones: [diag])
        #expect((m.getValue(pDiag, 10000) ?? -1) == 2)
        #expect((m.getValue(pDiag, 20000) ?? -1) == 0)   // above the zone → no risk
    }

    @Test func sfipPeaksWithinSpanAndIsNilOutside() {
        let m = metric("sfip-at-level")
        let zone = VizSfipZone(baseFt: 5000, topFt: 15000, risk: "moderate", type: "nwp", meanSfip100: 65, variant: "x")
        let p = makeVizPoint(sfipZones: [zone])
        #expect(m.getValue(p, 10000) == 65)
        #expect(m.getValue(p, 20000) == nil)   // outside the zone
        #expect(m.getValue(p, nil) == nil)     // no altitude
    }

    @Test func cloudAtAltitudeUsesCoverageWeight() {
        let m = metric("cloud-at-level")
        let ovc = makeVizPoint(cloudLayers: [makeCloud(3000, 8000, "ovc")])  // weight 1.0 → 100
        #expect(m.getValue(ovc, 5000) == 100)
        let sct = makeVizPoint(cloudLayers: [makeCloud(3000, 8000, "sct")])  // weight 0.3 → 30
        #expect(abs((sct.cloudLayers.isEmpty ? .nan : (m.getValue(sct, 5000) ?? .nan)) - 30) < 1e-6)
        let unknown = makeVizPoint(cloudLayers: [makeCloud(3000, 8000, "xyz")])  // default 0.5 → 50
        #expect((m.getValue(unknown, 5000) ?? -1) == 50)
        #expect((m.getValue(ovc, 12000) ?? -1) == 0)   // above the layer → clear
    }
}

// MARK: - VizPoint stub factory (only the fields under test need overriding)

private func makeCloud(_ baseFt: Double, _ topFt: Double, _ coverage: String) -> VizCloudLayer {
    VizCloudLayer(baseFt: baseFt, topFt: topFt, coverage: coverage, meanDewpointDepressionC: nil, meanCloudCoverPct: nil)
}

private func makeVizPoint(
    distanceNm: Double = 0,
    freezingLevelFt: Double? = nil,
    minus10cLevelFt: Double? = nil,
    minus20cLevelFt: Double? = nil,
    cloudLayers: [VizCloudLayer] = [],
    icingZones: [VizIcingZone] = [],
    icingOgimetNwpZones: [VizIcingZone] = [],
    sfipZones: [VizSfipZone] = [],
    catLayers: [VizCATLayer] = [],
    convectiveRisk: String = "none",
    cloudCoverTotalPct: Double = 0,
    cloudCoverLowPct: Double = 0,
    headwindKt: Double = 0,
    capeSurfaceJkg: Double = 0,
    worstModelAgreement: String = "good"
) -> VizPoint {
    VizPoint(
        distanceNm: distanceNm,
        lat: 0, lon: 0,
        time: "2026-06-24T12:00:00Z",
        altitudeLines: AltitudeLines(
            freezingLevelFt: freezingLevelFt,
            minus10cLevelFt: minus10cLevelFt,
            minus20cLevelFt: minus20cLevelFt,
            lclAltitudeFt: nil, lfcAltitudeFt: nil, elAltitudeFt: nil
        ),
        cloudLayers: cloudLayers,
        nwpCloudLayers: nil,
        icingZones: icingZones,
        icingOgimetNwpZones: icingOgimetNwpZones,
        sfipZones: sfipZones,
        catLayers: catLayers,
        inversions: [],
        convectiveRisk: convectiveRisk,
        convectiveBaseFt: nil, convectiveTopFt: nil,
        nwpConvectiveRisk: "none",
        nwpConvectiveBaseFt: nil, nwpConvectiveTopFt: nil,
        nwpConvectiveCoverPct: nil, nwpConvectiveMethod: nil,
        hasNwpConvective: false,
        cloudCoverTotalPct: cloudCoverTotalPct,
        cloudCoverLowPct: cloudCoverLowPct,
        cloudCoverMidPct: 0,
        headwindKt: headwindKt,
        crosswindKt: 0,
        capeSurfaceJkg: capeSurfaceJkg,
        worstModelAgreement: worstModelAgreement,
        nwpCloudDiag: nil,
        temperatureC: nil,
        precipitationMm: nil
    )
}
