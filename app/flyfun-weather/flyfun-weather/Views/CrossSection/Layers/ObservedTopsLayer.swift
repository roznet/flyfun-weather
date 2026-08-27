import SwiftUI

/// Observed satellite cloud tops (#574) — group `conditions`, **default ON**.
///
/// SYNC — port of web/ts/visualization/cross-section/layers/observed-tops.ts.
///
/// This layer is the whole cross-check. It renders in the same space as the NWP
/// cloud bands, so "model says FL120, satellite saw FL280" is visible to the eye
/// with nobody computing it. Phase 1 deliberately computes no verdict: the
/// comparison is the pilot's to make, and the two things are drawn in
/// unmistakably different styles so it stays obvious which is measured and which
/// is forecast.
///
/// Three things the drawing has to be honest about:
///
///  - **The retrieval commits to one top per pixel.** A cirrus-over-stratus
///    stack shows up only in aggregate, so each route point draws its FL-band
///    histogram as ruled ticks rather than a single line. A line would be a
///    claim the data does not support.
///  - **No coverage is not a clear sky.** Points where the retrieval could not
///    answer draw a hatched "no data" mark at the top of the plot, never a gap
///    (which reads as "nothing up there").
///  - **The frame has an age.** A badge in the corner carries the valid time and
///    how old it is, because this layer and the NWP bands under it are not
///    contemporaneous.
struct ObservedTopsLayer: CrossSectionLayerProtocol {
    let id = "observed-tops"
    let name = "Observed cloud tops"
    let group: LayerGroup = .conditions

    /// Half-width of a point's mark on the X axis, in nm.
    private static let markHalfWidthNm: Double = 4
    /// A band has to cover MORE than this share of the looked-at sky to be drawn.
    ///
    /// 5%: the fine 10-FL bands split a deck into a dozen slivers, and below a
    /// twentieth of the sky a band is a fuzz of stray retrievals drawn at the
    /// same weight as a deck you could fly into. Measured over local packs the
    /// cut removes 60% of the bands at 20 NM while the survivors still account
    /// for 87% of the disc's cloud cover — it takes the noise, not the picture —
    /// and only 2 route points in 96 lost every band they had.
    ///
    /// Measured against the SKY, the same denominator the band is drawn as, so
    /// the floor means what the legend means.
    ///
    /// Safe to discard here only because the HIGHEST top is drawn separately,
    /// from `topsHighestFt`, and never passes through this filter — so a single
    /// cold pixel still gets its cap line or its off-scale arrow even when its
    /// band is too thin to draw.
    static let minBinFraction: Double = 0.05
    /// How far the "depth unknown" hatching hangs below a deck's base, px.
    /// Deliberately short: long enough to read as "there is cloud under this",
    /// short enough that it cannot be mistaken for measured vertical extent.
    private static let hatchDepthPx: CGFloat = 9
    /// Fixed height of the off-scale box at the chart ceiling, px. Fixed because
    /// the real value has no position on this chart — the badge and the readout
    /// carry the number instead.
    private static let aboveScaleBoxPx: CGFloat = 14
    private static let arrowPx: CGFloat = 4
    /// Spacing of the horizontal rules that fill a band, px. Closer together the
    /// bigger that band's share of the disc — density is a second, redundant
    /// encoding of the same number the opacity carries, because a chart read in a
    /// cockpit at a glance should not depend on judging one faint alpha.
    private static let ruleSpacingMinPx: CGFloat = 2.5
    private static let ruleSpacingMaxPx: CGFloat = 7
    /// Constant: the share is already carried by colour and by rule density, and
    /// a third redundant encoding muddies both.
    private static let bandAlpha: Double = 0.85

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard let observed = data.observed, let tops = observed.cloudTops else { return }

        for point in Self.drawablePoints(observed) {
            draw(point, context: &context, transform: transform)
        }

        // When the cap line is off-scale the badge is the only place the number
        // survives, so carry it there rather than leaving the pilot to infer
        // "higher than the chart" from a row of chevrons.
        let highest = Self.highestTopFt(observed)
        let suffix: String = {
            guard let highest, Self.topsAboveScale(observed, transform) else { return "" }
            return " · tops to \(ObservedBadge.flLabel(highest))"
        }()
        ObservedBadge.draw(
            &context, transform: transform,
            text: ObservedBadge.ageText(tops.validTime, tops.ageMinutes, "Satellite") + suffix
        )
    }

    // MARK: - Selection helpers (shared with the readout)

    /// Bands worth drawing at this point, strongest-signal filter applied.
    static func significantBins(_ point: VizObservedPoint) -> [VizObservedTopBin] {
        point.topsBins.filter { $0.fraction > minBinFraction }
    }

    /// Contiguous runs of populated bands — the decks.
    ///
    /// A gap between runs is a real, measured absence of cloud top, and it is the
    /// thing coarse bins destroyed: one station had decks at FL7-31, FL60-92 and
    /// FL302-370 with nothing between, rendered as slabs implying continuous
    /// cloud from the surface to FL150. Only the BASE of each run gets the "depth
    /// unknown" hatching, because that is the one edge where cloud genuinely
    /// continues below into air the satellite cannot see.
    static func bandRuns(_ bins: [VizObservedTopBin]) -> [[VizObservedTopBin]] {
        let sorted = bins.sorted { $0.loFt < $1.loFt }
        var runs: [[VizObservedTopBin]] = []
        for bin in sorted {
            if let previous = runs.last?.last, abs(bin.loFt - previous.hiFt) < 1 {
                runs[runs.count - 1].append(bin)
            } else {
                runs.append([bin])
            }
        }
        return runs
    }

    /// Points with a cloud top, a band, or an explicit no-coverage state.
    static func drawablePoints(_ observed: VizObserved) -> [VizObservedPoint] {
        observed.points.filter {
            $0.topsNoCoverage || $0.topsHighestFt != nil || !significantBins($0).isEmpty
        }
    }

    /// Highest observed top anywhere on the route, in ft — used to put the number
    /// in the badge when the cap line itself is above the chart.
    static func highestTopFt(_ observed: VizObserved) -> Double? {
        observed.points.compactMap(\.topsHighestFt).max()
    }

    /// True when that highest top sits above the plotted altitude range.
    static func topsAboveScale(_ observed: VizObserved, _ transform: CoordTransform) -> Bool {
        guard let highest = highestTopFt(observed) else { return false }
        return transform.altitudeToY(highest) < transform.plotArea.top
    }

    // MARK: - Drawing

    private func draw(
        _ point: VizObservedPoint, context: inout GraphicsContext, transform: CoordTransform
    ) {
        let theme = CrossSectionTheme.active
        let plotArea = transform.plotArea
        let x0 = transform.distanceToX(point.distanceNm - Self.markHalfWidthNm)
        let x1 = transform.distanceToX(point.distanceNm + Self.markHalfWidthNm)
        let width = max(2, x1 - x0)

        if point.topsNoCoverage {
            // Hatched mark at the top of the column: the satellite could not
            // answer here. Drawn rather than skipped — a gap reads as "nothing up
            // there", which is the one thing we did not observe.
            let y = plotArea.top + 6
            var hatch = Path()
            var offset: CGFloat = 0
            while offset < width {
                hatch.move(to: CGPoint(x: x0 + offset, y: y))
                hatch.addLine(to: CGPoint(x: x0 + offset + 3, y: y + 6))
                offset += 4
            }
            context.stroke(hatch, with: .color(theme.observed.noCoverageColor), lineWidth: 1)
            return
        }

        // One marker per populated FL band. NOT a filled band: this product
        // carries no cloud base at all, so a solid rect spanning the bin reads as
        // "cloud occupies FL150-250" when the data only says "this share of the
        // TOPS is somewhere in FL150-250".
        let bins = Self.significantBins(point)
        // Normalised against this point's own busiest band, so the dominant deck
        // reads strongest whatever the absolute counts are. Fine bands hold a
        // much smaller share each than the old buckets did, and an absolute scale
        // would render every one of them uniformly faint.
        let peakFraction = max(bins.map(\.fraction).max() ?? 0, .leastNonzeroMagnitude)

        for run in Self.bandRuns(bins) {
            for bin in run {
                let yLo = transform.altitudeToY(bin.loFt)
                let yHi = transform.altitudeToY(bin.hiFt)
                if yLo < plotArea.top { continue }  // above the chart; the arrow says so
                let top = max(yHi, plotArea.top)
                let height = max(1.5, yLo - top)
                let share = bin.fraction / peakFraction
                // Colour carries the band's share of the LOOKED-AT SKY, not of
                // the cloud that was found: a band then says "this much of the
                // area around the point had its top here", which is the quantity
                // a pilot can act on, and 8 bands over a broken sky no longer
                // colour like 8 bands over a solid overcast. (Share rather than
                // temperature because the vertical axis already says how high the
                // band is, and cloud-top temperature is nearly a function of
                // height — the map keeps the temperature ramp, having no
                // altitude axis to spend.)
                let bandColor = theme.observed.shareColor(bin.fraction)

                // Filled with HORIZONTAL RULES, not a solid block. A solid fill
                // at an altitude reads as a physical layer sitting there; this is
                // a tally — "this share of the tops in the disc fell in this
                // band". Rules say "counted" the way a solid says "substance",
                // and they cannot be confused with the diagonal hatching, which
                // everywhere in this layer means "unknown".
                //
                // Density carries the share as well as opacity: a band holding
                // most of the pixels is closely ruled, a band holding a handful
                // is sparse.
                let spacing = Self.ruleSpacingMaxPx
                    - (Self.ruleSpacingMaxPx - Self.ruleSpacingMinPx) * share
                var rules = Path()
                var y = top + 0.5
                // Always at least one rule, so a single-pixel band is still
                // visible — it is often the coldest top on the chart.
                repeat {
                    rules.move(to: CGPoint(x: x0, y: y))
                    rules.addLine(to: CGPoint(x: x0 + width, y: y))
                    y += max(spacing, 0.5)
                } while y < top + height
                context.stroke(
                    rules, with: .color(bandColor.opacity(Self.bandAlpha)), lineWidth: 1)

                // A light edge so the band's extent stays legible when rules are
                // sparse.
                context.stroke(
                    Path(CGRect(x: x0, y: top, width: width, height: height)),
                    with: .color(bandColor.opacity(Self.bandAlpha * 0.6)), lineWidth: 1)
            }

            // Only under the base of the deck: that is the one edge where cloud
            // really does continue down into air the satellite cannot see.
            // Hatching under every band would re-imply the continuous slab the
            // fine histogram exists to remove.
            guard let base = run.last else { continue }
            let yBase = transform.altitudeToY(base.loFt)
            if yBase >= plotArea.top {
                var hatch = Path()
                var offset: CGFloat = 0
                while offset < width {
                    hatch.move(to: CGPoint(x: x0 + offset, y: yBase))
                    hatch.addLine(to: CGPoint(
                        x: x0 + offset - Self.hatchDepthPx * 0.5, y: yBase + Self.hatchDepthPx))
                    offset += 4
                }
                context.stroke(
                    hatch, with: .color(theme.observed.hatchColor.opacity(0.45)), lineWidth: 1)
            }
        }

        guard let highestFt = point.topsHighestFt else { return }

        let capColor = point.topsMultiLayerFraction > 0.1
            ? theme.observed.capMultiLayerColor
            : theme.observed.capColor
        let yTop = transform.altitudeToY(highestFt)

        // Above the chart's ceiling the cap line has nowhere to go. A GA
        // cross-section is scaled to the aircraft's flight ceiling — 18,000 ft is
        // typical — while satellite tops routinely sit at FL350+, so on a normal
        // piston briefing the single most important number this layer produces is
        // off-scale. Clipping it silently leaves only the minority FL bands
        // visible, which reads as "tops are around FL200" when they are nowhere
        // near it. Draw an explicit above-scale chevron instead; the badge and
        // the readout carry the value.
        guard yTop < plotArea.top else {
            // The highest observed top: a solid cap, visually distinct from the
            // soft NWP cloud bands underneath so measured and modelled never blur
            // together.
            var cap = Path()
            cap.move(to: CGPoint(x: x0, y: yTop))
            cap.addLine(to: CGPoint(x: x1, y: yTop))
            context.stroke(cap, with: .color(capColor), lineWidth: 2)
            return
        }

        // A fixed-height hatched box pinned to the chart ceiling, with an arrow:
        // "the top is above this chart". The height is fixed and meaningless on
        // purpose — scaling it to the real value would invent a position for
        // something that has none here.
        //
        // Coloured by the SHARE of the disc whose tops are above the ceiling, the
        // same convention as every other band: taking the cap colour would put a
        // single-value encoding next to a row of share-encoded bands and make a
        // box holding 2% of the disc look identical to one holding 90%.
        let ceilingFt = transform.yToAltitude(plotArea.top)
        // Share of SKY, as the bands.
        let aboveShare = point.topsBins
            .filter { $0.loFt >= ceilingFt }
            .reduce(0.0) { $0 + $1.fraction }
        let boxColor = aboveShare > 0 ? theme.observed.shareColor(aboveShare) : capColor

        let yBox = plotArea.top + 1
        var boxHatch = Path()
        var offset: CGFloat = 0
        while offset < width + Self.aboveScaleBoxPx {
            let sx = x0 + offset
            boxHatch.move(to: CGPoint(x: min(sx, x1), y: yBox))
            boxHatch.addLine(to: CGPoint(
                x: max(x0, sx - Self.aboveScaleBoxPx), y: yBox + Self.aboveScaleBoxPx))
            offset += 4
        }
        context.stroke(boxHatch, with: .color(boxColor.opacity(0.5)), lineWidth: 1)
        context.stroke(
            Path(CGRect(x: x0, y: yBox, width: width, height: Self.aboveScaleBoxPx)),
            with: .color(boxColor), lineWidth: 1)

        // Up arrow, centred. Keeps the cap colour: it marks the highest top,
        // which is a single value, not a share — and it must stay legible against
        // whatever share colour the box took.
        let cx = (x0 + x1) / 2
        var arrow = Path()
        arrow.move(to: CGPoint(x: cx, y: yBox + 2))
        arrow.addLine(to: CGPoint(x: cx - Self.arrowPx, y: yBox + 2 + Self.arrowPx))
        arrow.addLine(to: CGPoint(x: cx + Self.arrowPx, y: yBox + 2 + Self.arrowPx))
        arrow.closeSubpath()
        context.fill(arrow, with: .color(capColor))
    }
}

/// Per-source age badge, top-right of the plot.
///
/// `row` stacks them: with both observed layers enabled, drawing at one fixed
/// position let whichever layer rendered last paint over the other, hiding one
/// source's age entirely. That is the same "one age for four sources" outcome
/// the design rules out, reached by z-order instead of by string concatenation —
/// and the layers really are minutes apart, so the hidden number was never the
/// one on top.
enum ObservedBadge {
    static let rowHeightPx: CGFloat = 15

    /// "Satellite 14:00Z · 12 min old", or just "14:00Z · 12 min old" where the
    /// caller already names the source in its own column (the Layers sheet does).
    static func ageText(_ validTime: String, _ ageMinutes: Double, _ label: String = "") -> String {
        let hhmm = utcHHMM(validTime) ?? "--:--"
        let age = ageMinutes < 1 ? "just now" : "\(Int(ageMinutes.rounded())) min old"
        let stamp = "\(hhmm)Z · \(age)"
        return label.isEmpty ? stamp : "\(label) \(stamp)"
    }

    /// "FL381" for a height in feet.
    static func flLabel(_ ft: Double) -> String { "FL\(Int((ft / 100).rounded()))" }

    @MainActor
    static func draw(
        _ context: inout GraphicsContext, transform: CoordTransform, text: String, row: Int = 0
    ) {
        let plotArea = transform.plotArea
        let resolved = context.resolve(
            Text(text).font(.system(size: 10, weight: .semibold)).foregroundColor(Color(white: 0.22))
        )
        let size = resolved.measure(in: CGSize(width: plotArea.width, height: rowHeightPx))
        let right = plotArea.left + plotArea.width - 6
        let y = plotArea.top + 4 + CGFloat(row) * rowHeightPx
        context.fill(
            Path(CGRect(x: right - size.width - 5, y: y - 2, width: size.width + 10, height: rowHeightPx)),
            with: .color(.white.opacity(0.82))
        )
        context.draw(resolved, at: CGPoint(x: right, y: y), anchor: .topTrailing)
    }

    /// "14:00" from an ISO-8601 instant, in UTC. nil when unparseable — the
    /// caller renders "--:--" rather than inventing a time.
    static func utcHHMM(_ iso: String) -> String? {
        guard let date = isoParser.date(from: iso) ?? isoParserNoFraction.date(from: iso) else {
            return nil
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }

    private static let isoParser: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoParserNoFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
}
