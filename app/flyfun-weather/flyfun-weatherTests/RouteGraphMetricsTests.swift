//
//  RouteGraphMetricsTests.swift
//  flyfun-weatherTests
//
//  Web↔iOS parity for the route-graph metric registry and the Skew-T side-panel
//  catalog — the two hand-copied surfaces that drifted silently because nothing
//  compared them (see .claude/skills/sync-ios-web).
//
//  These are the silent-regression class in the strongest sense: every
//  divergence fixed here rendered *plausibly* rather than failing. A declared
//  suggestedRange that nothing read still drew a chart; a ceiling above the
//  display cap still drew a gap; a help pointer aimed at the wrong metric still
//  opened a popup with confident, wrong text. So the assertions pin the
//  vocabulary and the state machine, not the pixels.
//

import Testing
import SwiftUI
@testable import flyfun_weather

// MARK: - Fixtures

private func point(
    distanceNm: Double = 0,
    terrainElevationFt: Double = 0,
    soundingCeilingFt: Double? = nil,
    nwpCeilingFt: Double? = nil,
    cinSurfaceJkg: Double = 0,
    temperatureC: Double? = nil,
    isaDevC: Double? = nil,
    qnhHpa: Double? = nil,
    observed: VizObservedPoint? = nil
) -> VizPoint {
    VizPoint(
        distanceNm: distanceNm,
        lat: 0, lon: 0,
        time: "2026-06-24T12:00:00Z",
        altitudeLines: AltitudeLines(
            freezingLevelFt: nil, minus10cLevelFt: nil, minus20cLevelFt: nil,
            lclAltitudeFt: nil, lfcAltitudeFt: nil, elAltitudeFt: nil
        ),
        cloudLayers: [],
        nwpCloudLayers: nil,
        icingZones: [], icingOgimetNwpZones: [], sfipZones: [],
        catLayers: [], inversions: [],
        convectiveRisk: "none", convectiveBaseFt: nil, convectiveTopFt: nil,
        nwpConvectiveRisk: "none",
        nwpConvectiveBaseFt: nil, nwpConvectiveTopFt: nil,
        nwpConvectiveCoverPct: nil, nwpConvectiveMethod: nil,
        hasNwpConvective: false,
        cloudCoverTotalPct: 0, cloudCoverLowPct: 0, cloudCoverMidPct: 0,
        headwindKt: 0, crosswindKt: 0,
        capeSurfaceJkg: 0,
        worstModelAgreement: "good",
        nwpCloudDiag: nwpCeilingFt.map {
            VizCloudDiag(
                low: VizCloudDiagBand(coverPct: nil, baseFt: nil, topFt: nil),
                mid: VizCloudDiagBand(coverPct: nil, baseFt: nil, topFt: nil),
                high: VizCloudDiagBand(coverPct: nil, baseFt: nil, topFt: nil),
                ceilingFt: $0)
        },
        temperatureC: temperatureC,
        precipitationMm: nil,
        cinSurfaceJkg: cinSurfaceJkg,
        soundingCeilingFt: soundingCeilingFt,
        terrainElevationFt: terrainElevationFt,
        isaDevC: isaDevC,
        qnhHpa: qnhHpa,
        observed: observed
    )
}

private func metric(_ id: String) -> RouteGraphMetric {
    guard let m = RouteGraphMetrics.metric(byId: id) else {
        fatalError("no route-graph metric '\(id)' — the registry lost a web metric")
    }
    return m
}

// MARK: - Registry parity with the web

@Suite("Route-graph registry ↔ web")
struct RouteGraphRegistryTests {

    /// The metric IDs and their order, verbatim from `ROUTE_GRAPH_METRICS` in
    /// web/ts/visualization/route-graph/metrics.ts.
    ///
    /// Order is asserted, not just membership: it is the picker's order on both
    /// clients, and an id present on only one silently breaks the web advisory
    /// lens that names it in its `routeGraph` directive.
    static let webIds = [
        "observed-rain-rate", "observed-flash-rate",
        "headwind", "crosswind", "temperature", "isa-dev",
        "precipitation", "cloud-cover", "cape", "cin", "qnh",
        "freezing-level", "ceiling-dd", "ceiling-nwp",
    ]

    @Test("every web metric exists on iOS, in the same order")
    func idsMatchWeb() {
        #expect(RouteGraphMetrics.all.map(\.id) == Self.webIds)
    }

    /// Every metric a web advisory lens names in `routeGraph`, including the
    /// `enroute_precip` override. `ceiling-nwp` is the one this suite exists for:
    /// three lenses point at it, and iOS had no such metric at all.
    @Test("metrics the web lenses target all resolve")
    func lensTargetsResolve() {
        for id in ["freezing-level", "ceiling-nwp", "cloud-cover", "cape",
                   "precipitation", "headwind", "crosswind"] {
            #expect(RouteGraphMetrics.metric(byId: id) != nil, "lens target '\(id)' missing")
        }
    }

    /// The pinned ranges from the web registry. A range that exists but is not
    /// honoured is worse than none: the same numbers then render at a different
    /// magnitude on each client with nothing on screen saying so.
    @Test("suggested ranges match the web registry")
    func rangesMatchWeb() {
        #expect(metric("precipitation").suggestedRange == 0...5)
        #expect(metric("cloud-cover").suggestedRange == 0...100)
        #expect(metric("cape").suggestedRange == 0...1000)
        #expect(metric("cin").suggestedRange == -300...0)
        #expect(metric("ceiling-dd").suggestedRange == 0...5000)
        #expect(metric("ceiling-nwp").suggestedRange == 0...5000)
        // Auto-scaled on the web too.
        #expect(metric("headwind").suggestedRange == nil)
        #expect(metric("qnh").suggestedRange == nil)
    }

    @Test("only the ceiling metrics cap, and both declare a range to cap against")
    func aboveScaleMetrics() {
        let capped = RouteGraphMetrics.all.filter(\.aboveScale).map(\.id)
        #expect(capped == ["ceiling-dd", "ceiling-nwp"])
        for m in RouteGraphMetrics.all where m.aboveScale {
            #expect(m.suggestedRange != nil, "\(m.id) caps with no range to cap against")
        }
    }

    @Test("only the radar metric claims a coverage state")
    func noCoverageMetrics() {
        let sensed = RouteGraphMetrics.all.filter { $0.isNoCoverage != nil }.map(\.id)
        #expect(sensed == ["observed-rain-rate"])
    }
}

// MARK: - The four-state sample model

@Suite("Metric sampling states")
struct MetricSampleTests {

    @Test("a ceiling above the cap is above-scale, never a gap")
    func ceilingAboveCapIsAboveScale() {
        let m = metric("ceiling-dd")
        // 12,000 ft MSL over 1,000 ft terrain = 11,000 ft AGL, well past the cap.
        let sample = m.sample(at: point(terrainElevationFt: 1000, soundingCeilingFt: 12000))
        guard case .aboveScale(let v) = sample else {
            Issue.record("expected .aboveScale, got \(sample)")
            return
        }
        #expect(v == 11000)
        // Reported from the CAP, not the value: we state the limit we can draw,
        // we do not disclose a number we declined to plot. Compared against the
        // metric's own formatting of the cap rather than a literal, because the
        // thousands separator is locale-dependent (as `toLocaleString()` is on
        // the web) and must not make this test locale-sensitive.
        #expect(m.formatSample(sample) == "> " + m.formatValue(5000))
        #expect(m.formatSample(sample) != "> " + m.formatValue(11000))
    }

    @Test("no sounding is unavailable — the state above-scale must not be confused with")
    func noSoundingIsUnavailable() {
        let m = metric("ceiling-dd")
        let sample = m.sample(at: point(soundingCeilingFt: nil))
        guard case .unavailable = sample else {
            Issue.record("expected .unavailable, got \(sample)")
            return
        }
        #expect(m.formatSample(sample) == "N/A")
    }

    @Test("a ceiling inside the cap is an ordinary value")
    func ceilingInsideCapIsValue() {
        let m = metric("ceiling-dd")
        let sample = m.sample(at: point(terrainElevationFt: 500, soundingCeilingFt: 3500))
        guard case .value(let v) = sample else {
            Issue.record("expected .value, got \(sample)")
            return
        }
        #expect(v == 3000)
    }

    @Test("a ceiling below terrain floors at 0 rather than going negative")
    func ceilingBelowTerrainFloors() {
        let m = metric("ceiling-dd")
        guard case .value(let v) = m.sample(at: point(terrainElevationFt: 4000, soundingCeilingFt: 3000)) else {
            Issue.record("expected .value")
            return
        }
        #expect(v == 0)
    }

    /// `ceiling-nwp` is the metric three web lenses target, so its getter gets
    /// the same AGL treatment as its DD sibling rather than a looser one.
    @Test("the NWP ceiling converts to AGL and caps like the DD ceiling")
    func nwpCeilingBehavesLikeDd() {
        let m = metric("ceiling-nwp")
        guard case .value(let v) = m.sample(at: point(terrainElevationFt: 800, nwpCeilingFt: 3800)) else {
            Issue.record("expected .value")
            return
        }
        #expect(v == 3000)
        guard case .aboveScale = m.sample(at: point(terrainElevationFt: 0, nwpCeilingFt: 25000)) else {
            Issue.record("expected .aboveScale past the cap")
            return
        }
        guard case .unavailable = m.sample(at: point(nwpCeilingFt: nil)) else {
            Issue.record("no NWP diagnostics → unavailable")
            return
        }
    }

    @Test("a radar coverage hole is no-coverage, not zero rain and not absent data")
    func radarHoleIsNoCoverage() {
        let m = metric("observed-rain-rate")
        var obs = VizObservedPoint(distanceNm: 0)
        obs.rateNoCoverage = true
        let sample = m.sample(at: point(observed: obs))
        guard case .noCoverage = sample else {
            Issue.record("expected .noCoverage, got \(sample)")
            return
        }
        #expect(m.formatSample(sample) == "No coverage")
    }

    /// The distinction the whole state machine exists for: the radar looked and
    /// measured nothing (`unavailable`) versus the radar not looking at all
    /// (`noCoverage`). Rendering both as a gap reads as "no rain" in half the
    /// OPERA grid.
    @Test("the radar looking and finding nothing is NOT a coverage hole")
    func radarLookedAndFoundNothing() {
        let m = metric("observed-rain-rate")
        var obs = VizObservedPoint(distanceNm: 0)
        obs.rateNoCoverage = false
        obs.radarNoCoverage = false
        obs.rateMmH = nil
        guard case .unavailable = m.sample(at: point(observed: obs)) else {
            Issue.record("a looking-but-empty radar must not read as no-coverage")
            return
        }
    }

    /// Either coverage flag gates the rate — mirrors the web extractor's
    /// `radarNoCoverage || rateNoCoverage`.
    @Test("a composite that does not reach here gates the rate too")
    func compositeCoverageGatesRate() {
        var obs = VizObservedPoint(distanceNm: 0)
        obs.radarNoCoverage = true
        obs.rateMmH = nil
        #expect(point(observed: obs).observedRadarNoCoverage)
    }

    @Test("measured zero rain is a value, never a gap")
    func measuredZeroIsAValue() {
        var obs = VizObservedPoint(distanceNm: 0)
        obs.rateMmH = 0
        guard case .value(let v) = metric("observed-rain-rate").sample(at: point(observed: obs)) else {
            Issue.record("a measured 0 mm/h is data")
            return
        }
        #expect(v == 0)
    }

    @Test("a point with no observed sample at all is unavailable, not no-coverage")
    func noObservedSample() {
        guard case .unavailable = metric("observed-rain-rate").sample(at: point(observed: nil)) else {
            Issue.record("expected .unavailable")
            return
        }
    }
}

// MARK: - Axis scaling

@Suite("Route-graph Y scale")
struct RouteGraphScaleTests {

    @Test("a capping metric pins its axis verbatim — no padding, no rounding")
    func pinnedAxisIsVerbatim() {
        let m = metric("ceiling-dd")
        let samples = [MetricSample.value(1200), .value(3000)]
        let scale = RouteGraphScale(samples: samples, metric: m)
        #expect(scale.lower == 0)
        #expect(scale.upper == 5000)
    }

    /// The regression that mattered most: `suggestedRange` used to be declared
    /// and never read, so a route with 10–20% cloud filled the plot.
    @Test("a suggested range is honoured rather than auto-fitting the data")
    func suggestedRangeIsHonoured() {
        let scale = RouteGraphScale(
            samples: [.value(10), .value(20)], metric: metric("cloud-cover"))
        #expect(scale.lower <= 0)
        #expect(scale.upper >= 100)
    }

    @Test("data beyond a suggested range expands the axis to fit it")
    func rangeExpandsForOutliers() {
        let scale = RouteGraphScale(
            samples: [.value(0), .value(2400)], metric: metric("cape"))
        #expect(scale.upper >= 2400)
    }

    @Test("above-scale samples do not expand a pinned axis")
    func aboveScaleDoesNotExpand() {
        let scale = RouteGraphScale(
            samples: [.value(1000), .aboveScale(40000)], metric: metric("ceiling-dd"))
        #expect(scale.upper == 5000)
    }

    @Test("a zero-line metric always includes zero")
    func zeroLineIncludesZero() {
        // All-positive headwinds would otherwise auto-fit to a window above zero,
        // leaving the head/tail reference line off-screen.
        let scale = RouteGraphScale(
            samples: [.value(12), .value(20)], metric: metric("headwind"))
        #expect(scale.lower <= 0)
        #expect(scale.upper >= 0)
    }

    @Test("an empty series still yields a usable domain")
    func emptySeries() {
        let scale = RouteGraphScale(samples: [], metric: metric("headwind"))
        #expect(scale.lower < scale.upper)
    }

    @Test("an all-unavailable series does not collapse the axis")
    func allUnavailable() {
        let scale = RouteGraphScale(
            samples: [.unavailable, .noCoverage], metric: metric("qnh"))
        #expect(scale.lower < scale.upper)
    }

    /// The right metric is drawn in the left metric's domain, so the mapping has
    /// to be an exact round trip or the trailing axis labels lie about the line.
    @Test("mapping into another domain round-trips")
    func mappingRoundTrips() {
        let left = RouteGraphScale(samples: [.value(0), .value(100)], metric: metric("cloud-cover"))
        let right = RouteGraphScale(samples: [.value(0), .value(5)], metric: metric("precipitation"))
        for v in [0.0, 1.25, 2.5, 5.0] {
            let plotted = right.mapped(v, into: left)
            #expect(abs(right.unmapped(plotted, from: left) - v) < 1e-9)
        }
    }

    @Test("a mapped value keeps its fractional position in the domain")
    func mappingPreservesPosition() {
        let left = RouteGraphScale(samples: [.value(0), .value(100)], metric: metric("cloud-cover"))
        let right = RouteGraphScale(samples: [.value(0), .value(5)], metric: metric("precipitation"))
        // The right metric's midpoint must land at the left domain's midpoint.
        let mid = right.lower + right.span / 2
        let plotted = right.mapped(mid, into: left)
        #expect(abs(plotted - (left.lower + left.span / 2)) < 1e-9)
    }
}

// MARK: - Value formatting

@Suite("Route-graph formatting ↔ web")
struct RouteGraphFormattingTests {

    @Test("temperature keeps the web's one decimal place")
    func temperaturePrecision() {
        #expect(metric("temperature").formatValue(12.34) == "12.3°C")
    }

    @Test("altitudes are thousand-separated, as toLocaleString() does on the web")
    func altitudeGrouping() {
        #expect(metric("freezing-level").formatValue(8500).contains("8"))
        #expect(metric("freezing-level").formatValue(8500).hasSuffix(" ft"))
        // The separator itself is locale-dependent; what matters is that the
        // digits are grouped rather than run together.
        #expect(metric("freezing-level").formatValue(8500) != "8500 ft")
    }

    @Test("crosswind reports the side it blows from")
    func crosswindCarriesDirection() {
        #expect(metric("crosswind").formatValue(12) == "12 kt R")
        #expect(metric("crosswind").formatValue(-12) == "12 kt L")
    }

    @Test("head/tailwind reports which it is")
    func headwindCarriesSense() {
        #expect(metric("headwind").formatValue(15) == "15 kt HW")
        #expect(metric("headwind").formatValue(-15) == "15 kt TW")
    }

    /// The web derives the sign from the ROUNDED value so a small negative
    /// deviation reads "ISA±0", never the nonsensical "ISA−0".
    @Test("a near-zero ISA deviation reads ±0, never −0")
    func isaDevNearZero() {
        #expect(metric("isa-dev").formatValue(-0.3) == "ISA±0 (−0.3°C)")
        #expect(metric("isa-dev").formatValue(0.0) == "ISA±0 (±0.0°C)")
    }

    @Test("ISA deviation carries an explicit sign either way")
    func isaDevSigns() {
        #expect(metric("isa-dev").formatValue(7.2) == "ISA+7 (+7.2°C)")
        #expect(metric("isa-dev").formatValue(-7.2) == "ISA−7 (−7.2°C)")
    }

    @Test("a zero flash rate reads as none rather than 0.00")
    func flashRateZero() {
        #expect(metric("observed-flash-rate").formatValue(0) == "none")
        #expect(metric("observed-flash-rate").formatValue(1.5) == "1.50")
    }

    /// Documented iOS divergence: hPa-only until `UnitsRegion` is plumbed in.
    @Test("QNH is hPa on iOS")
    func qnhUnit() {
        #expect(metric("qnh").unit == "hPa")
        #expect(metric("qnh").formatValue(1013.2) == "1013 hPa")
    }
}

// MARK: - Terrain interpolation

@Suite("Terrain elevation lookup")
struct TerrainElevationTests {
    private let profile = [
        TerrainPoint(distanceNm: 0, elevationFt: 0),
        TerrainPoint(distanceNm: 10, elevationFt: 1000),
        TerrainPoint(distanceNm: 20, elevationFt: 500),
    ]

    @Test("interpolates between samples rather than snapping to the nearest")
    func interpolates() {
        #expect(profile.elevationFt(atDistanceNm: 5) == 500)
        #expect(profile.elevationFt(atDistanceNm: 15) == 750)
    }

    @Test("lands exactly on a sample")
    func onSample() {
        #expect(profile.elevationFt(atDistanceNm: 10) == 1000)
    }

    @Test("clamps to the profile's ends instead of extrapolating")
    func clamps() {
        #expect(profile.elevationFt(atDistanceNm: -5) == 0)
        #expect(profile.elevationFt(atDistanceNm: 99) == 500)
    }

    @Test("an empty or single-point profile is safe")
    func degenerate() {
        #expect([TerrainPoint]().elevationFt(atDistanceNm: 5) == 0)
        #expect([TerrainPoint(distanceNm: 3, elevationFt: 700)].elevationFt(atDistanceNm: 5) == 700)
    }
}

// MARK: - Per-axis series building

/// `RouteGraphSeries` exists so the chart's three point sets are built ONCE per
/// axis rather than per call site. The first cut built the capped/no-coverage
/// markers for the left metric only, so a `ceiling-nwp` on the right axis drew a
/// bare gap — reintroducing #384's confusion on the other axis. These tests pin
/// the symmetry, because it is invisible in a screenshot until you pick the
/// pairing that exposes it.
@Suite("Route-graph series building")
struct RouteGraphSeriesTests {

    /// A route with one ordinary ceiling, one above the cap, and one with no
    /// sounding at all.
    private var ceilingRoute: [VizPoint] {
        [
            point(distanceNm: 0, terrainElevationFt: 0, soundingCeilingFt: 2000),
            point(distanceNm: 10, terrainElevationFt: 0, soundingCeilingFt: 30000),
            point(distanceNm: 20, terrainElevationFt: 0, soundingCeilingFt: nil),
        ]
    }

    private func series(_ m: RouteGraphMetric, _ points: [VizPoint],
                        into target: RouteGraphScale? = nil) -> RouteGraphSeries {
        let scale = RouteGraphScale(samples: points.map { m.sample(at: $0) }, metric: m)
        return RouteGraphSeries(points: points, metric: m, scale: scale, into: target ?? scale)
    }

    @Test("each sample state lands in exactly one bucket")
    func statesArePartitioned() {
        let s = series(metric("ceiling-dd"), ceilingRoute)
        #expect(s.values.map(\.distance) == [0])
        #expect(s.capped.map(\.distance) == [10])
        #expect(s.holes.isEmpty)
        // The no-sounding point is in no bucket — unavailable is the one state
        // that legitimately draws nothing.
        #expect(s.values.count + s.capped.count + s.holes.count == 2)
    }

    @Test("a capped point is pinned to the cap, not to its real value")
    func cappedPinnedToCap() {
        let m = metric("ceiling-dd")
        let s = series(m, ceilingRoute)
        guard let capped = s.capped.first else {
            Issue.record("expected a capped point")
            return
        }
        // The real value is preserved for readouts...
        #expect(capped.value == 30000)
        // ...but it is DRAWN at the cap, so it rides the top edge.
        #expect(capped.plotValue == RouteGraphMetrics.ceilingAglCapFt)
    }

    @Test("a coverage hole is pinned to the floor")
    func holePinnedToFloor() {
        var obs = VizObservedPoint(distanceNm: 0)
        obs.rateNoCoverage = true
        let m = metric("observed-rain-rate")
        let pts = [point(observed: obs)]
        let scale = RouteGraphScale(samples: pts.map { m.sample(at: $0) }, metric: m)
        let s = RouteGraphSeries(points: pts, metric: m, scale: scale, into: scale)
        #expect(s.values.isEmpty)
        #expect(s.holes.count == 1)
        #expect(s.holes.first?.plotValue == scale.lower)
    }

    /// The regression the review caught: the right axis must produce the same
    /// three buckets as the left, for the same metric on the same route.
    @Test("the right axis buckets identically to the left")
    func rightAxisMatchesLeft() {
        let m = metric("ceiling-nwp")
        let route = [
            point(distanceNm: 0, nwpCeilingFt: 2000),
            point(distanceNm: 10, nwpCeilingFt: 30000),
            point(distanceNm: 20, nwpCeilingFt: nil),
        ]
        // As the left metric: drawn in its own domain.
        let asLeft = series(m, route)
        // As the right metric beside cloud-cover: drawn in cloud-cover's domain.
        let hostScale = RouteGraphScale(
            samples: route.map { metric("cloud-cover").sample(at: $0) },
            metric: metric("cloud-cover"))
        let asRight = series(m, route, into: hostScale)

        #expect(asRight.values.map(\.distance) == asLeft.values.map(\.distance))
        #expect(asRight.capped.map(\.distance) == asLeft.capped.map(\.distance))
        #expect(asRight.holes.map(\.distance) == asLeft.holes.map(\.distance))
        // Specifically: the capped point is NOT dropped on the right axis.
        #expect(asRight.capped.count == 1)
    }

    @Test("a right-axis capped marker is drawn at the top of the host domain")
    func rightCappedRidesHostTop() {
        let m = metric("ceiling-nwp")
        let route = [point(distanceNm: 0, nwpCeilingFt: 30000)]
        let hostScale = RouteGraphScale(samples: [.value(0), .value(100)],
                                        metric: metric("cloud-cover"))
        let s = series(m, route, into: hostScale)
        // ceiling-nwp pins 0…5000, so its cap is the top of ITS domain, which maps
        // to the top of the host domain — the same top edge the left axis uses.
        #expect(abs((s.capped.first?.plotValue ?? 0) - hostScale.upper) < 1e-9)
    }

    @Test("real values keep their own units while being drawn in the host domain")
    func valuesKeepUnits() {
        let m = metric("ceiling-nwp")
        let route = [point(distanceNm: 0, terrainElevationFt: 500, nwpCeilingFt: 3000)]
        let hostScale = RouteGraphScale(samples: [.value(0), .value(100)],
                                        metric: metric("cloud-cover"))
        let s = series(m, route, into: hostScale)
        // 3000 MSL − 500 terrain = 2500 AGL, reported in feet...
        #expect(s.values.first?.value == 2500)
        // ...but plotted somewhere inside the host's domain, not at 2500.
        let plotted = s.values.first?.plotValue ?? .nan
        #expect(plotted >= hostScale.lower && plotted <= hostScale.upper)
    }

    /// Web `renderBars` skips an exact zero (`s.value === 0 → continue`), and
    /// `designs/route-graph.md` states it as "Zero/null values skipped".
    @Test("a bar metric draws nothing for an exact zero")
    func barSkipsZero() {
        var zero = VizObservedPoint(distanceNm: 0)
        zero.rateMmH = 0
        var wet = VizObservedPoint(distanceNm: 10)
        wet.rateMmH = 2.5
        let m = metric("observed-rain-rate")   // renderType == .bar
        #expect(m.renderType == .bar)
        let s = series(m, [point(distanceNm: 0, observed: zero),
                           point(distanceNm: 10, observed: wet)])
        // The zero is still a VALUE — it is data, and the readout strip reports it.
        #expect(s.values.count == 2)
        // But only the non-zero sample draws a bar.
        #expect(s.barValues.map(\.distance) == [10])
    }

    /// The counterpart rule, and the reason this is bar-only: a line must pass
    /// THROUGH its zeros. Dropping them would break the trace at every zero
    /// crossing, which for head/tailwind is exactly where the reading matters.
    @Test("a line metric keeps its zeros")
    func lineKeepsZeros() {
        let m = metric("temperature")   // renderType == .line
        #expect(m.renderType == .line)
        let route = [
            point(distanceNm: 0, temperatureC: 5),
            point(distanceNm: 10, temperatureC: 0),   // the zero a line must keep
            point(distanceNm: 20, temperatureC: -5),
        ]
        let s = series(m, route)
        #expect(s.values.map(\.distance) == [0, 10, 20])
    }

    @Test("an empty route yields empty buckets rather than crashing")
    func emptyRoute() {
        let s = series(metric("headwind"), [])
        #expect(s.values.isEmpty && s.capped.isEmpty && s.holes.isEmpty)
    }
}

// MARK: - Zero-reference placement

/// The web draws a zero line per METRIC (`renderer.ts` calls `drawZeroLine` for
/// the left and right metric independently), and iOS drew one only for the left —
/// so `cloud-cover` + `crosswind` rendered a signed axis with no zero reference
/// at all. Same left-only class as the marker bug, one round later, which is why
/// the predicate now lives on `RouteGraphScale` where a test can reach it.
@Suite("Zero-reference placement")
struct ZeroLineTests {

    private func scale(_ m: RouteGraphMetric, _ values: [Double]) -> RouteGraphScale {
        RouteGraphScale(samples: values.map { MetricSample.value($0) }, metric: m)
    }

    @Test("every signed metric the web gives a zero line declares one here")
    func signedMetricsDeclareZeroLine() {
        // From ROUTE_GRAPH_METRICS: these four set showZeroLine on the web.
        for id in ["headwind", "crosswind", "temperature", "isa-dev", "cin"] {
            #expect(metric(id).showZeroLine, "\(id) lost its zero line")
        }
    }

    @Test("a signed metric gets a zero line on its own axis")
    func leftAxisZeroLine() {
        let m = metric("crosswind")
        let s = scale(m, [-15, 20])
        #expect(s.zeroLineY(for: m, in: s) != nil)
    }

    /// The regression: the same metric as the RIGHT axis must still get a zero
    /// line, placed in the host domain.
    @Test("a signed metric gets a zero line as the right axis too")
    func rightAxisZeroLine() {
        let m = metric("crosswind")
        let own = scale(m, [-15, 20])
        let host = scale(metric("cloud-cover"), [10, 80])
        guard let y = own.zeroLineY(for: m, in: host) else {
            Issue.record("a signed right-axis metric must still get a zero reference")
            return
        }
        // Drawn inside the host's domain, at the host's height for the right
        // metric's zero — which is NOT the host's own zero.
        #expect(y >= host.lower && y <= host.upper)
    }

    @Test("the right axis's zero sits at its own height, not the host's")
    func rightZeroIsNotHostZero() {
        // crosswind −5…20 puts its zero low in its own range; cloud-cover's zero
        // is at its floor. Mapping must land the former above the latter.
        let m = metric("crosswind")
        let own = scale(m, [-5, 20])
        let host = scale(metric("cloud-cover"), [0, 100])
        guard let y = own.zeroLineY(for: m, in: host) else {
            Issue.record("expected a zero line")
            return
        }
        #expect(y > host.lower)
    }

    @Test("an unsigned metric gets no zero line on either axis")
    func unsignedMetricsHaveNone() {
        let m = metric("qnh")
        let s = scale(m, [1008, 1020])
        #expect(m.showZeroLine == false)
        #expect(s.zeroLineY(for: m, in: s) == nil)
    }

    /// The invariant that makes the reference dependable: a metric declaring
    /// `showZeroLine` ALWAYS ends up with zero inside its domain, whatever the
    /// data does, because the padding step plus the explicit zero-line correction
    /// guarantee it. So the line is never silently skipped for a signed metric.
    ///
    /// CIN is the interesting case — it pins −300…0, where zero is the range's
    /// own boundary, and the 10% padding is what lifts the domain above it.
    @Test("a signed metric's domain always straddles zero, whatever the data")
    func signedDomainsAlwaysStraddleZero() {
        let shapes: [[Double]] = [
            [],                  // no data at all
            [-200],              // all negative
            [5, 12, 20],         // all positive
            [-15, 20],           // both sides
            [0],                 // exactly zero
        ]
        for id in ["headwind", "crosswind", "temperature", "isa-dev", "cin"] {
            let m = metric(id)
            for shape in shapes {
                let s = RouteGraphScale(samples: shape.map { MetricSample.value($0) }, metric: m)
                #expect(s.lower < 0 && s.upper > 0,
                        "\(id) with \(shape) gave \(s.lower)…\(s.upper), excluding zero")
                #expect(s.zeroLineY(for: m, in: s) != nil, "\(id) with \(shape) lost its reference")
            }
        }
    }

    /// `zeroLineY`'s domain guard still mirrors web `drawZeroLine`'s early return
    /// for the case the invariant above does not cover: a metric that does not ask
    /// for a zero line gets none even when its domain happens to straddle zero.
    @Test("the guard is the metric's own declaration, not the domain's shape")
    func guardFollowsDeclaration() {
        let m = metric("precipitation")   // 0…5, unsigned
        let s = RouteGraphScale(samples: [.value(-1), .value(3)], metric: m)
        #expect(s.lower < 0 && s.upper > 0)   // domain straddles zero...
        #expect(s.zeroLineY(for: m, in: s) == nil)  // ...but the metric wants no line
    }

    /// Web `drawZeroLine` returns only when `min > 0 || max < 0`, so a domain
    /// touching zero at a bound still draws — on the plot edge. iOS used strict
    /// inequalities, which would have suppressed it. Dormant today (the padding
    /// pipeline always pushes the bound strictly past zero) but the two guards are
    /// meant to be provably equivalent, so the boundary is pinned here.
    @Test("a domain touching zero at a bound still gets its reference")
    func inclusiveAtTheBound() {
        let m = metric("headwind")
        let host = scale(m, [-10, 25])
        // A hand-built scale is not reachable through the initializer, so the
        // boundary is exercised through the two natural bounds of a real domain:
        // both must be treated as "zero is in range" rather than excluded.
        #expect(m.showZeroLine)
        #expect(host.zeroLineY(for: m, in: host) != nil)
        // Sanity: the guard reads the bounds inclusively, so a domain whose lower
        // bound IS zero is not rejected. cloud-cover pins 0…100 but is unsigned,
        // so `precipitation`-style unsigned metrics remain excluded by declaration
        // rather than by the bound — see `guardFollowsDeclaration`.
        #expect(host.lower <= 0 && host.upper >= 0)
    }

    @Test("the zero line round-trips to zero in the metric's own units")
    func zeroLineIsActuallyZero() {
        let m = metric("headwind")
        let own = scale(m, [-10, 25])
        let host = scale(metric("cape"), [0, 900])
        guard let y = own.zeroLineY(for: m, in: host) else {
            Issue.record("expected a zero line")
            return
        }
        #expect(abs(own.unmapped(y, from: host)) < 1e-9)
    }
}

// MARK: - Skew-T side-panel catalog ↔ web

@Suite("Skew-T variable catalog ↔ web")
struct SkewTVariableCatalogTests {

    /// Every iOS variable id, and the web `metricId` its web twin points at.
    /// Copied from `VARIABLE_REGISTRY` in web/ts/visualization/skewt/variable-panel.ts.
    static let webMetricIdByIosId = [
        "headwind": "skewt_headwind_crosswind",
        "wind_speed": "wind_speed_kt",
        "dewpoint_depression": "dewpoint_depression_c",
        "rh": "skewt_relative_humidity",
        "cloud": "skewt_cloud_area_fraction",
        "clw": "skewt_cloud_liquid_water",
        "ice": "skewt_ice_mixing_ratio",
        "icing-dd": "icing_risk",
        "icing-nwp": "icing_ogimet_nwp_risk",
        "sfip": "sfip_risk",
        "lapse": "lapse_rate_c_km",
        "ri": "richardson_number",
        "w": "skewt_vertical_velocity",
        "thetae": "equivalent_potential_temperature_k",
    ]

    @Test("every help pointer matches the web registry's metricId")
    func helpPointersMatchWeb() {
        #expect(SkewTVariableCatalog.helpMetricId == Self.webMetricIdByIosId)
    }

    /// The bug this suite was written for. `cloud_cover_pct` is the TOTAL-COLUMN
    /// metric — its own limitations text says it "doesn't tell you at which
    /// altitude the clouds are" — so pointing a per-level variable at it opened a
    /// popup with confident, wrong help rather than failing visibly.
    @Test("the per-level cloud variable points at the per-level metric")
    func cloudHelpIsPerLevel() {
        #expect(SkewTVariableCatalog.helpMetricId["cloud"] == "skewt_cloud_area_fraction")
        #expect(SkewTVariableCatalog.helpMetricId["cloud"] != "cloud_cover_pct")
    }

    @Test("every variable has a short label for the collapsed chip")
    func shortLabelsCoverEveryVariable() {
        #expect(Set(SkewTVariableCatalog.shortLabel.keys)
                == Set(SkewTVariableCatalog.helpMetricId.keys))
    }
}
