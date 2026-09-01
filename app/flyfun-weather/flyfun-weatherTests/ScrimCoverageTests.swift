import Testing
import CoreGraphics
@testable import flyfun_weather

/// The scrim's coverage guard — the Swift half of the rule web enforces in
/// `scrim-coverage.test.ts`.
///
/// A scrim says "look here, not there". Once the cut-outs cover most of the plot
/// that sentence inverts: the small *unflagged* remainder becomes the only dimmed
/// thing, so the eye lands on the one stretch that is fine. Observed on
/// `ifr_feasibility` over a convective route — 14 regions covering 71% of the
/// plot, two of them full-column ghosts — which read as blank white boxes.
@Suite struct ScrimCoverageTests {

    // 400x200 plot, margins L50/R40/T20/B30 -> plotArea width 310, 0-100 nm.
    private let t = CoordTransform(size: CGSize(width: 400, height: 200),
                                   maxDistanceNm: 100, maxAltitudeFt: 10000)

    private func region(_ from: Double, _ to: Double) -> VizAdvisoryHighlights.Region {
        VizAdvisoryHighlights.Region(
            distFromNm: from, distToNm: to, baseFt: nil, topFt: nil,
            kind: "tower", severity: "amber")
    }

    @Test func zeroWithNoRegions() {
        #expect(HighlightLayer.cutoutCoverage([], transform: t) == 0)
    }

    @Test func measuresASingleSpanAsAFractionOfPlotWidth() {
        #expect(abs(HighlightLayer.cutoutCoverage([region(10, 30)], transform: t) - 0.2) < 1e-9)
    }

    @Test func unionsOverlappingRegionsRatherThanSummingThem() {
        // An icing band and a convective tower at the same distance are two
        // regions; summing would report 0.4 for a chart that is 80% clear.
        let c = HighlightLayer.cutoutCoverage([region(10, 30), region(20, 30)], transform: t)
        #expect(abs(c - 0.2) < 1e-9)
    }

    @Test func addsDisjointSpans() {
        let c = HighlightLayer.cutoutCoverage([region(0, 20), region(60, 80)], transform: t)
        #expect(abs(c - 0.4) < 1e-9)
    }

    @Test func mergesTouchingSpansWithoutDoubleCountingTheSeam() {
        let c = HighlightLayer.cutoutCoverage([region(0, 50), region(50, 90)], transform: t)
        #expect(abs(c - 0.9) < 1e-9)
    }

    @Test func isOrderIndependent() {
        let a = HighlightLayer.cutoutCoverage([region(60, 80), region(0, 20)], transform: t)
        let b = HighlightLayer.cutoutCoverage([region(0, 20), region(60, 80)], transform: t)
        #expect(abs(a - b) < 1e-9)
    }

    @Test func handlesAReversedSpanWithoutGoingNegative() {
        let c = HighlightLayer.cutoutCoverage([region(30, 10)], transform: t)
        #expect(abs(c - 0.2) < 1e-9)
    }

    @Test func thresholdSeparatesASpotlightFromARouteWideHighlight() {
        // vmc_cruise on the reported pack: 12% of route flagged — a real spotlight.
        #expect(HighlightLayer.cutoutCoverage([region(0, 12)], transform: t)
                <= HighlightLayer.scrimMaxCoverage)
        // ifr_feasibility on the same pack: ~71% — not a spotlight any more.
        #expect(HighlightLayer.cutoutCoverage([region(0, 71)], transform: t)
                > HighlightLayer.scrimMaxCoverage)
    }

    @Test func matchesTheWebThreshold() {
        // SYNC: web `SCRIM_MAX_COVERAGE` in highlight-layer.ts.
        #expect(HighlightLayer.scrimMaxCoverage == 0.6)
    }
}
