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

/// The appearance every comparison resolves against. System colours like
/// `.blue` are *dynamic*: `UIColor(color).getRed(...)` resolves them against
/// whatever `UITraitCollection.current` happens to be on the calling thread,
/// and Swift Testing runs these tests in parallel. On 2026-10-07 CI that made
/// `sameColor(MapColors.flightCategory("MVFR") → blue, .blue → blue)` fail —
/// the same colour compared against itself, resolved once light and once dark.
/// Pinning the traits takes the ambient appearance out of the comparison.
private let comparisonTraits = UITraitCollection(userInterfaceStyle: .light)

/// Resolve a SwiftUI `Color` to sRGB components so two colours can be compared
/// for equality with a tolerance (SwiftUI `Color`'s own `==` is unreliable
/// across the semantic vs `.sRGB` providers we mix here).
private func rgba(_ color: Color) -> (r: Double, g: Double, b: Double, a: Double) {
    var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
    UIColor(color).resolvedColor(with: comparisonTraits).getRed(&r, green: &g, blue: &b, alpha: &a)
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

// MARK: - MapColors (flight-category palette, still used by the cross-section)

@Suite struct MapColorsTests {
    @Test func flightCategoryPalette() {
        #expect(sameColor(MapColors.flightCategory("VFR"), .green))
        #expect(sameColor(MapColors.flightCategory("MVFR"), .blue))
        #expect(sameColor(MapColors.flightCategory("IFR"), .red))
        #expect(sameColor(MapColors.flightCategory("LIFR"), .purple))
        #expect(sameColor(MapColors.flightCategory("???"), .gray))
    }
}

// MARK: - Route-map catalog (#998: served `route_maps`, bundled copy)

@Suite struct RouteMapCatalogTests {
    private var catalog: RouteMapCatalog { RouteMapCatalog.bundledBaseline! }
    private var metrics: MapMetrics { MapMetrics(catalog: catalog) }

    @Test func bundledCatalogDecodes() {
        #expect(RouteMapCatalog.bundledBaseline != nil, "route-map-metrics-catalog.json must be in the app bundle")
        #expect(catalog.defaultMetric == "cloud-cover-total")
        #expect(catalog.scales.bands["headwind_kt"]?.stops?.first?.at == 0)
    }

    @Test func everyCatalogMetricHasAnExtractor() {
        // A metric this build cannot read is skipped; the bundled copy must have none.
        #expect(metrics.all.map(\.id) == catalog.metrics.map(\.key))
        for spec in catalog.metrics { #expect(MapMetrics.extractors[spec.value] != nil, "\(spec.value)") }
    }

    @Test func groupsSplitAltitudeFromRoute() {
        #expect(metrics.groups.map(\.id) == ["at_altitude", "along_route"])
        #expect(metrics.groups[0].metrics.allSatisfy { $0.altitudeDependent })
        #expect(!metrics.groups[1].metrics.contains { $0.altitudeDependent })
    }

    @Test func unknownIdFallsBackToDefault() {
        #expect(metrics.resolve("flight-category")?.id == "cloud-cover-total")
        #expect(metrics.resolve(nil)?.id == "cloud-cover-total")
        #expect(metrics.resolve("cape")?.id == "cape")
        #expect(metrics.metric(byId: "flight-category") == nil)
    }

    @Test func ceilingBandsFlipAtCategoryBoundaries() {
        let m = metrics.metric(byId: "nwp-ceiling")!
        #expect(!sameColor(m.color(499), m.color(500)))
        #expect(!sameColor(m.color(999), m.color(1000)))
        #expect(!sameColor(m.color(2999), m.color(3000)))
        #expect(sameColor(m.color(3000), m.color(9000)))
        // Every colour the line can draw is in the legend (#735).
        let legend = m.legend.map(\.color)
        for ft in stride(from: 0.0, through: 10000, by: 250) {
            #expect(legend.contains { sameColor($0, m.color(ft)) })
        }
    }

    @Test func sfipUsesTheBackendTiers() {
        // 15 / 30 / 55, the backend sfip_to_risk() tiers, in the icing colours.
        let sfip = metrics.metric(byId: "sfip-at-level")!
        let icing = metrics.metric(byId: "icing-risk-at-level")!
        #expect(sameColor(sfip.color(14.9), icing.color(0)))
        #expect(sameColor(sfip.color(15), icing.color(1)))
        #expect(sameColor(sfip.color(30), icing.color(2)))
        #expect(sameColor(sfip.color(55), icing.color(3)))
    }

    @Test func linearScalesInterpolateAndClamp() {
        let head = metrics.metric(byId: "headwind")!
        #expect(sameColor(head.color(0), Color(.sRGB, red: 1, green: 1, blue: 1)))
        #expect(sameColor(head.color(-10), head.color(0)))
        #expect(sameColor(head.color(60), head.color(30)))
        #expect(!sameColor(head.color(15), head.color(0)))
        #expect(!sameColor(head.color(15), head.color(30)))
    }

    @Test func widthRunsThinToThickWithinRange() {
        let ceiling = metrics.metric(byId: "nwp-ceiling")!   // inverted: low ceiling thick
        #expect(abs(ceiling.width(0) - MapMetrics.maxWidth) < 1e-6)
        #expect(abs(ceiling.width(5000) - MapMetrics.minWidth) < 1e-6)
        #expect(abs(ceiling.width(20000) - MapMetrics.minWidth) < 1e-6)
        let cloud = metrics.metric(byId: "cloud-cover-total")!
        #expect(abs(cloud.width(50) - (MapMetrics.minWidth + MapMetrics.maxWidth) / 2) < 1e-6)
    }

    @Test func helpCatalogCarriesRouteMaps() throws {
        // Served under `route_maps`, decoded with the plain decoder (keys verbatim).
        let bundled = try Data(contentsOf: try #require(
            Bundle.main.url(forResource: "route-map-metrics-catalog", withExtension: "json")))
        let route = try #require(String(data: bundled, encoding: .utf8))
        let json = #"{"version": "v", "metrics": {}, "advisories": [], "route_maps": "# + route + "}"
        let decoded = try HelpCatalogResponse.decode(from: Data(json.utf8))
        #expect(decoded.routeMaps?.metrics.count == catalog.metrics.count)
        // A route catalog this build cannot read costs that section only.
        let broken = #"{"version": "v", "metrics": {}, "advisories": [], "route_maps": {"version": 1}}"#
        #expect(try HelpCatalogResponse.decode(from: Data(broken.utf8)).routeMaps == nil)
    }

    @Test func formatsFollowTheCatalog() {
        #expect(metrics.metric(byId: "icing-risk-at-level")!.format(2) == "moderate")
        #expect(metrics.metric(byId: "visibility")!.format(2500) == "2.5 km")
        #expect(metrics.metric(byId: "nwp-ceiling")!.format(800) == "800 ft")
    }
}

// MARK: - MapMetrics extractors (the altitude helpers, segment aggregation)

@Suite struct MapMetricsTests {

    private func metric(_ id: String) -> MapMetric {
        MapMetrics(catalog: RouteMapCatalog.bundledBaseline!).metric(byId: id)!
    }

    @Test func segmentValueAveragesContinuousFields() {
        let m = metric("cloud-cover-total")  // aggregation average
        let a = makeVizPoint(cloudCoverTotalPct: 20)
        let b = makeVizPoint(cloudCoverTotalPct: 60)
        #expect(m.segmentValue(a, b, altitudeFt: nil) == 40)
    }

    @Test func segmentValueTakesWorstForOrdinalRisk() {
        let m = metric("convective-risk")  // aggregation max, riskRank lookup
        let calm = makeVizPoint(convectiveRisk: "none")    // 0
        let bad = makeVizPoint(convectiveRisk: "severe")   // 3
        #expect(m.segmentValue(calm, bad, altitudeFt: nil) == 3)
    }

    @Test func segmentValueFallsBackWhenOneEndpointNil() {
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
        #expect(abs((m.getValue(p, 10000) ?? .nan) - 0) < 1e-6)
        #expect(abs((m.getValue(p, 15000) ?? .nan) - -5) < 1e-6)
        #expect(abs((m.getValue(p, 5000) ?? .nan) - 10) < 1e-6)
        #expect(abs((m.getValue(p, 35000) ?? .nan) - -30) < 1e-6)
        #expect(m.getValue(makeVizPoint(), 10000) == nil)
    }

    @Test func icingRiskReadsTheDiagnosticZonesLikeTheWeb() {
        // #998: one rule on every client, the web's (the OGIMET-NWP preference
        // was iOS-only).
        let m = metric("icing-risk-at-level")
        let diag = VizIcingZone(baseFt: 5000, topFt: 15000, risk: "moderate", type: "diag")  // rank 2
        let ogimet = VizIcingZone(baseFt: 0, topFt: 40000, risk: "severe", type: "nwp")       // rank 3
        let p = makeVizPoint(icingZones: [diag], icingOgimetNwpZones: [ogimet])
        #expect((m.getValue(p, 10000) ?? -1) == 2)
        #expect((m.getValue(p, 20000) ?? -1) == 0)   // above the zone → no risk
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
        let ovc = makeVizPoint(cloudLayers: [makeCloud(3000, 8000, "ovc")])
        #expect(m.getValue(ovc, 5000) == 100)
        let sct = makeVizPoint(cloudLayers: [makeCloud(3000, 8000, "sct")])
        #expect(abs((m.getValue(sct, 5000) ?? .nan) - 30) < 1e-6)
        let unknown = makeVizPoint(cloudLayers: [makeCloud(3000, 8000, "xyz")])
        #expect((m.getValue(unknown, 5000) ?? -1) == 50)
        #expect((m.getValue(ovc, 12000) ?? -1) == 0)
    }

    @Test func windSpeedAndVisibilityReadThePoint() {
        var p = makeVizPoint()
        p.windSpeedKt = 25
        p.visibilityM = 3000
        #expect(metric("wind-speed").getValue(p, nil) == 25)
        #expect(metric("visibility").getValue(p, nil) == 3000)
        // No model visibility (ICON) → no value, not a guess.
        #expect(metric("visibility").getValue(makeVizPoint(), nil) == nil)
    }
}

// MARK: - Route-map chrome helpers (#998)

@Suite struct RouteMapChromeTests {
    @Test func altitudeStepsSnapAndClamp() {
        #expect(RouteMapView.stepAltitude(8000, by: 1, ceilingFt: 12000) == 8500)
        #expect(RouteMapView.stepAltitude(8000, by: -1, ceilingFt: 12000) == 7500)
        #expect(RouteMapView.stepAltitude(0, by: -1, ceilingFt: 12000) == 0)
        #expect(RouteMapView.stepAltitude(12000, by: 1, ceilingFt: 12000) == 12000)
        #expect(RouteMapView.stepAltitude(8240, by: 1, ceilingFt: 12000) == 8500)
    }

    @Test func frontsArePlacedAlongTheRoute() {
        let points = [makeVizPoint(distanceNm: 0, lat: 50, lon: 0), makeVizPoint(distanceNm: 100, lat: 52, lon: 2)]
        let c = RouteFrontPlacement.coordinate(atDistanceNm: 50, along: points)
        #expect(abs((c?.lat ?? 0) - 51) < 1e-9)
        #expect(abs((c?.lon ?? 0) - 1) < 1e-9)
        #expect(RouteFrontPlacement.coordinate(atDistanceNm: 150, along: points) == nil)
    }
}

// MARK: - VizPoint stub factory (only the fields under test need overriding)

private func makeCloud(_ baseFt: Double, _ topFt: Double, _ coverage: String) -> VizCloudLayer {
    VizCloudLayer(baseFt: baseFt, topFt: topFt, coverage: coverage, meanDewpointDepressionC: nil, meanCloudCoverPct: nil)
}

private func makeVizPoint(
    distanceNm: Double = 0,
    lat: Double = 0,
    lon: Double = 0,
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
        lat: lat, lon: lon,
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
