import SwiftUI

/// Observed satellite cloud tops (#574) — group `conditions`, **default ON**.
///
/// SYNC — port of web/ts/visualization/cross-section/layers/observed-tops.ts.
///
/// This layer is the whole cross-check. It renders in the same space as the NWP
/// cloud bands, so forecast and retrieved geometric heights are visible to the eye
/// with nobody computing it. Phase 1 deliberately computes no verdict: the
/// comparison is the pilot's to make, and the two things are drawn in
/// unmistakably different styles so it stays obvious which is measured and which
/// is forecast.
///
/// Three things the drawing has to be honest about:
///
///  - **The retrieval commits to one top per pixel.** A cirrus-over-stratus
///    stack cannot be resolved from one top. Ruled height-bin ticks describe
///    variability among nearby samples, not layers in one vertical column.
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
    /// Visual floor as a share of valid retrieval samples, not sky area.
    /// The highest top bypasses it, preserving isolated detections.
    /// Missing or suppressed bins do not prove clear air or a cloud-free gap.
    static let minBinFraction: Double = 0.05
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
        guard let observed = data.observed, observed.cloudTops != nil else { return }

        for point in Self.drawablePoints(observed) {
            draw(point, context: &context, transform: transform)
        }

        // Ages render in the cheap clock overlay, not this cached static scene.
    }

    // MARK: - Selection helpers (shared with the readout)

    /// Bands worth drawing at this point, strongest-signal filter applied.
    static func significantBins(_ point: VizObservedPoint) -> [VizObservedTopBin] {
        point.topsBins.filter { $0.fraction > minBinFraction }
    }

    /// Contiguous runs of populated height bins, not measured cloud decks.
    /// A gap says nothing about clear air or a shared cloud base.
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
                // Colour carries valid-sample share, not sky area or visual
                // opacity. The map separately offers the temperature ramp.
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

            // No geometry below a run: nearby-pixel tops cannot establish a
            // shared base or vertical extent. Coverage hatches remain separate.
        }

        guard let highestFt = point.topsHighestFt else { return }

        let capColor = theme.observed.capColor
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
    static func ageText(
        _ validTime: String, _ ageMinutes: Double, _ label: String = "", now: Date = Date()
    ) -> String {
        // Server age is retained for wire compatibility only; it freezes in a
        // downloaded pack. Always derive display age from the source valid time.
        guard let date = Date.parseISO8601(validTime), date.timeIntervalSince1970.isFinite,
              now.timeIntervalSince1970.isFinite else {
            return label.isEmpty ? "time / age unknown" : "\(label) time / age unknown"
        }
        let minutes = now.timeIntervalSince(date) / 60
        let age: String
        if minutes < 0 {
            age = "future time — check clock"
        } else if minutes < 1 {
            age = "just now"
        } else {
            // Informational age policy only, never a weather-severity grade.
            age = "\(Int(minutes.rounded(.down))) min old" + (minutes >= 30 ? " · stale" : "")
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: date)
        // Always include the UTC date: history/offline packs must remain explicit
        // even if the caller cannot tell whether this is today's pack.
        let utc = String(format: "%04d-%02d-%02d %02d:%02dZ", parts.year!, parts.month!, parts.day!, parts.hour!, parts.minute!)
        let stamp = "\(utc) · \(age)"
        return label.isEmpty ? stamp : "\(label) \(stamp)"
    }

    static func heightLabel(_ ft: Double) -> String { "\(Int(ft.rounded())) ft MSL (geometric)" }

    static func sourceText(_ source: VizObservedSource, now: Date, includeLabel: Bool = true) -> String {
        var text = ageText(source.validTime, source.ageMinutes, includeLabel ? source.label : "", now: now)
        if source.windowMinutes.isFinite, source.windowMinutes > 0 {
            let window = Int(source.windowMinutes.rounded())
            let kind = source.source == "opera_dbzh" ? "composite scan window"
                : source.source == "eumetsat_li" ? "accumulation" : "acquisition window"
            text += " · \(window) min \(kind)"
            if let end = Date.parseISO8601(source.validTime) {
                let start = end.addingTimeInterval(-source.windowMinutes * 60)
                let formatter = DateFormatter()
                formatter.locale = Locale(identifier: "en_US_POSIX")
                formatter.timeZone = TimeZone(secondsFromGMT: 0)
                var calendar = Calendar(identifier: .gregorian)
                calendar.timeZone = TimeZone(secondsFromGMT: 0)!
                formatter.dateFormat = calendar.isDate(start, inSameDayAs: end)
                    ? "HH:mm'Z'" : "yyyy-MM-dd HH:mm'Z'"
                text += " (\(formatter.string(from: start))–\(formatter.string(from: end)))"
            }
        }
        return text
    }

    static func surfaceTexts(_ observed: VizObserved, now: Date) -> [String] {
        [observed.reflectivity, observed.lightning].compactMap { $0 }.map { sourceText($0, now: now) }
    }

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
