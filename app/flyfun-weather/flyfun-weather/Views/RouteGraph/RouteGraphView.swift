import Charts
import SwiftUI

/// Data point for chart rendering. `plotValue` is what Charts draws; for the
/// right metric it is the value mapped into the left metric's domain (see
/// `RouteGraphScale`), and `value` stays the real number for readouts.
struct RouteGraphPoint: Identifiable {
    let id: Int
    let distance: Double
    let value: Double
    let plotValue: Double
}

/// A metric's Y domain and the mapping into it. Port of web `computeYScale`
/// (`web/ts/visualization/route-graph/axes.ts`) — the padding, zero-line
/// inclusion and nice-rounding rules must match or the two clients draw the same
/// numbers at different magnitudes.
///
/// Swift Charts has a single Y scale per chart, so the right metric is mapped
/// into the left metric's domain and its trailing axis labels invert the mapping.
/// That is what makes a dual-metric graph readable at all: CAPE (0…1000) and
/// precipitation (0…5) on one shared domain renders the precipitation bars as a
/// flat line on the floor.
struct RouteGraphScale {
    let lower: Double
    let upper: Double

    var span: Double { max(upper - lower, .leastNonzeroMagnitude) }

    init(samples: [MetricSample], metric: RouteGraphMetric) {
        // Pinned axis: the range verbatim — no padding, no nice-rounding, no
        // expanding to fit — so the top tick *is* the cap and a marker riding the
        // top edge unambiguously reads "higher than that number".
        if metric.aboveScale, let range = metric.suggestedRange {
            lower = range.lowerBound
            upper = range.upperBound
            return
        }

        // Only plottable values drive the scale. Above-scale samples deliberately
        // do not — expanding the axis to fit them is what the cap exists to avoid.
        let nums: [Double] = samples.compactMap { sample -> Double? in
            guard case .value(let v) = sample else { return nil }
            return v
        }
        let dataMin = nums.min()
        let dataMax = nums.max()

        var lo: Double
        var hi: Double
        if let range = metric.suggestedRange {
            lo = min(range.lowerBound, dataMin ?? range.lowerBound)
            hi = max(range.upperBound, dataMax ?? range.upperBound)
        } else if let dataMin, let dataMax {
            lo = dataMin
            hi = dataMax
        } else {
            lo = 0
            hi = 1
        }

        let padding = (hi - lo == 0 ? 1 : hi - lo) * 0.1
        lo -= padding
        hi += padding

        if metric.showZeroLine {
            if lo > 0 { lo = -padding }
            if hi < 0 { hi = padding }
        }

        let step = Self.niceTickInterval(hi - lo, targetTicks: 4)
        lo = (lo / step).rounded(.down) * step
        hi = (hi / step).rounded(.up) * step
        if lo == hi { hi = lo + step }

        lower = lo
        upper = hi
    }

    /// Map a value from this scale into `target`'s domain, preserving position.
    func mapped(_ v: Double, into target: RouteGraphScale) -> Double {
        target.lower + (v - lower) / span * target.span
    }

    /// Invert `mapped` — a position in `target`'s domain back to this metric's
    /// own units, for the trailing axis labels.
    func unmapped(_ y: Double, from target: RouteGraphScale) -> Double {
        lower + (y - target.lower) / target.span * span
    }

    /// A 1/2/5×10ⁿ step that lands near `targetTicks` divisions. Port of web
    /// `niceTickInterval`.
    private static func niceTickInterval(_ range: Double, targetTicks: Int) -> Double {
        guard range > 0, targetTicks > 0 else { return 1 }
        let rough = range / Double(targetTicks)
        let magnitude = pow(10, (log10(rough)).rounded(.down))
        let normalized = rough / magnitude
        let nice: Double = normalized <= 1.5 ? 1 : normalized <= 3.5 ? 2 : normalized <= 7.5 ? 5 : 10
        return nice * magnitude
    }
}

/// One metric's points, already mapped into the domain the chart draws in.
///
/// All three sets are built together, for BOTH axes, deliberately: the capped and
/// coverage-hole markers exist so those states cannot read as "no data", and
/// building them per-axis by hand is how the right axis silently lost them once
/// already. A metric on the right gets the same treatment as one on the left
/// because there is one builder, not two call sites to keep in step.
struct RouteGraphSeries {
    /// Plottable values — the line or bars.
    let values: [RouteGraphPoint]
    /// Known values off the top of a pinned axis, pinned to the cap.
    let capped: [RouteGraphPoint]
    /// Points the sensor does not cover, pinned to the floor.
    let holes: [RouteGraphPoint]

    /// `scale` is the metric's own domain; `target` is the domain the chart draws
    /// in (the left metric's). They are the same object for the left metric.
    init(points: [VizPoint], metric: RouteGraphMetric,
         scale: RouteGraphScale, into target: RouteGraphScale) {
        var values: [RouteGraphPoint] = []
        var capped: [RouteGraphPoint] = []
        var holes: [RouteGraphPoint] = []
        for (i, point) in points.enumerated() {
            switch metric.sample(at: point) {
            case .value(let v):
                values.append(RouteGraphPoint(id: i, distance: point.distanceNm, value: v,
                                              plotValue: scale.mapped(v, into: target)))
            case .aboveScale(let v):
                capped.append(RouteGraphPoint(id: i, distance: point.distanceNm, value: v,
                                              plotValue: scale.mapped(scale.upper, into: target)))
            case .noCoverage:
                holes.append(RouteGraphPoint(id: i, distance: point.distanceNm, value: 0,
                                             plotValue: scale.mapped(scale.lower, into: target)))
            case .unavailable:
                continue
            }
        }
        self.values = values
        self.capped = capped
        self.holes = holes
    }
}

/// Route graph using Swift Charts — scalar metrics along the route.
struct RouteGraphView: View {
    let viewModel: BriefingViewModel
    let vizData: VizRouteData?
    /// Shared cross-section scrub cursor (§4.7) — one cursor, X-aligned, so the
    /// route graph highlights the same distance the readout strip describes.
    var scrubDistanceNm: Double? = nil

    /// Lifted to the cross-section so the readout strip shares the same metric
    /// selection (one unified cursor, §4.7).
    @Binding var leftMetricId: String
    @Binding var rightMetricId: String

    private var leftMetric: RouteGraphMetric? { RouteGraphMetrics.metric(byId: leftMetricId) }
    private var rightMetric: RouteGraphMetric? {
        rightMetricId == RouteGraphMetrics.metricNone ? nil : RouteGraphMetrics.metric(byId: rightMetricId)
    }

    var body: some View {
        VStack(spacing: 4) {
            HStack {
                metricPicker(selection: $leftMetricId, label: "Left")
                Spacer()
                metricPicker(selection: $rightMetricId, label: "Right")
            }
            .padding(.horizontal, 12)

            if let vizData, let leftMetric {
                chartView(vizData: vizData, leftMetric: leftMetric)
            }
        }
    }

    @ViewBuilder
    private func chartView(vizData: VizRouteData, leftMetric: RouteGraphMetric) -> some View {
        let leftSamples = vizData.points.map { leftMetric.sample(at: $0) }
        let leftScale = RouteGraphScale(samples: leftSamples, metric: leftMetric)
        let left = RouteGraphSeries(points: vizData.points, metric: leftMetric,
                                    scale: leftScale, into: leftScale)

        let rightMetric = self.rightMetric
        // The right metric's own domain, then its points mapped into the left one —
        // Charts has a single Y scale, so everything is drawn in the left domain and
        // the trailing axis labels invert the mapping.
        let rightScale = rightMetric.map { m in
            RouteGraphScale(samples: vizData.points.map { m.sample(at: $0) }, metric: m)
        }
        let right: RouteGraphSeries? = {
            guard let m = rightMetric, let rs = rightScale else { return nil }
            return RouteGraphSeries(points: vizData.points, metric: m, scale: rs, into: leftScale)
        }()

        Chart {
            ForEach(left.values) { pt in
                if leftMetric.renderType == .bar {
                    BarMark(x: .value("Distance", pt.distance), y: .value(leftMetric.label, pt.plotValue))
                        .foregroundStyle(leftMetric.color.opacity(0.6))
                } else {
                    LineMark(x: .value("Distance", pt.distance), y: .value(leftMetric.label, pt.plotValue))
                        .foregroundStyle(leftMetric.color)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
            }

            // Capped markers ride the top edge of the pinned axis.
            ForEach(left.capped) { pt in
                PointMark(x: .value("Distance", pt.distance), y: .value(leftMetric.label, pt.plotValue))
                    .symbol(.triangle)
                    .symbolSize(40)
                    .foregroundStyle(leftMetric.color)
            }

            // Coverage holes sit on the floor as hollow marks — distinct from a
            // bar of zero, which would read as a confident "nothing here".
            ForEach(left.holes) { pt in
                PointMark(x: .value("Distance", pt.distance), y: .value(leftMetric.label, pt.plotValue))
                    .symbol(.cross)
                    .symbolSize(30)
                    .foregroundStyle(.secondary.opacity(0.5))
            }

            if let rm = rightMetric, let rs = rightScale, let right {
                // A right-axis bar must start from the RIGHT metric's own zero
                // mapped into the left domain — `BarMark(y:)` would baseline it at
                // the left metric's zero, which is a different height entirely and
                // would draw bars growing from the wrong place.
                let rightBaseline = rs.mapped(max(rs.lower, min(rs.upper, 0)), into: leftScale)
                ForEach(right.values) { pt in
                    if rm.renderType == .bar {
                        BarMark(x: .value("Distance", pt.distance),
                                yStart: .value(rm.label, rightBaseline),
                                yEnd: .value(rm.label, pt.plotValue))
                            .foregroundStyle(rm.color.opacity(0.4))
                    } else {
                        LineMark(x: .value("Distance", pt.distance), y: .value(rm.label, pt.plotValue))
                            .foregroundStyle(rm.color)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                    }
                }

                // The right axis gets the same two marker layers as the left. A
                // ceiling above the cap or a radar hole on THIS axis is the same
                // claim about the same weather, and `cloud-cover` + `ceiling-nwp`
                // is the web Clouds lens's own pairing — so it is a selection
                // pilots will actually make, not a corner case. Hollow symbols
                // keep them readable against the left metric's solid markers.
                ForEach(right.capped) { pt in
                    PointMark(x: .value("Distance", pt.distance), y: .value(rm.label, pt.plotValue))
                        .symbol(.triangle)
                        .symbolSize(30)
                        .foregroundStyle(rm.color.opacity(0.7))
                }
                ForEach(right.holes) { pt in
                    PointMark(x: .value("Distance", pt.distance), y: .value(rm.label, pt.plotValue))
                        .symbol(.cross)
                        .symbolSize(22)
                        .foregroundStyle(.secondary.opacity(0.4))
                }
            }

            // Zero reference for the signed metrics (head/tailwind, crosswind,
            // ISA dev, CIN). A signed axis with no labelled zero is the one thing
            // these readings must not be: "12 kt" tells a pilot nothing about
            // which side it is coming from.
            //
            // Drawn as two coincident rules rather than one mark with two
            // annotations: a mark takes a single `.annotation`, so stacking two
            // would keep only the last. The second rule adds no visible ink.
            if leftMetric.showZeroLine, leftScale.lower < 0, leftScale.upper > 0 {
                RuleMark(y: .value("Zero", 0))
                    .foregroundStyle(.gray.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
                    .annotation(position: .top, alignment: .leading) {
                        if let labels = leftMetric.zeroLineLabels {
                            Text(labels.above).font(.system(size: 8)).foregroundStyle(.secondary)
                        }
                    }
                if let labels = leftMetric.zeroLineLabels {
                    RuleMark(y: .value("Zero", 0))
                        .foregroundStyle(.clear)
                        .annotation(position: .bottom, alignment: .leading) {
                            Text(labels.below).font(.system(size: 8)).foregroundStyle(.secondary)
                        }
                }
            }

            ForEach(vizData.waypointMarkers, id: \.icao) { wp in
                RuleMark(x: .value("Distance", wp.distanceNm))
                    .foregroundStyle(.gray.opacity(0.3))
                    .lineStyle(StrokeStyle(dash: [4, 4]))
            }

            // Shared scrub cursor — matches the cross-section's vertical indicator.
            if let cursor = scrubDistanceNm {
                RuleMark(x: .value("Distance", cursor))
                    .foregroundStyle(.orange)
                    .lineStyle(StrokeStyle(lineWidth: 1.5))
            }
        }
        // Pin the X domain to the full route (0…total) so a given distance maps
        // to the SAME fraction of the plot width the cross-section uses — without
        // this, Charts auto-domains to the data's min/max distance and the cursor
        // drifts out of register with the cross-section above (#6, iOS feedback).
        .chartXScale(domain: 0...max(vizData.totalDistanceNm, 1))
        // Pin the Y domain too, so a metric that declares a `suggestedRange`
        // renders at the same magnitude as on the web. Before this, the axis
        // always auto-fit and a 20% cloud cover filled the plot.
        .chartYScale(domain: leftScale.lower...leftScale.upper)
        .chartXAxisLabel("Distance (nm)")
        .chartYAxis {
            AxisMarks(position: .leading) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        // Fixed-width label pins the leading gutter to the
                        // cross-section's left margin so the two plot areas share
                        // the same left edge (#6). Right-aligned in the gutter.
                        Text(Self.axisLabel(v))
                            .frame(width: CoordTransform.margins.left - CoordTransform.axisLabelPadding,
                                   alignment: .trailing)
                    }
                }
                .foregroundStyle(leftMetric.color)
            }
            // Trailing axis in the RIGHT metric's own units: the tick sits at a
            // position in the left domain, the label inverts the mapping back.
            if let rm = rightMetric, let rs = rightScale {
                AxisMarks(position: .trailing) { value in
                    AxisValueLabel {
                        if let y = value.as(Double.self) {
                            Text(Self.axisLabel(rs.unmapped(y, from: leftScale)))
                        }
                    }
                    .foregroundStyle(rm.color)
                }
            }
        }
        .frame(height: 150)
        // Reserve the cross-section's right margin so the plot's right edge lines
        // up too; the leading edge is handled by the fixed-width Y label above.
        .padding(.trailing, CoordTransform.margins.right)
    }

    /// Compact Y-axis number: integer-valued metrics (kt, %, °C) print clean;
    /// fractional ones (precip mm) keep one decimal. Keeps the fixed-width
    /// gutter narrow enough to match the cross-section's left margin.
    private static func axisLabel(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(format: "%.1f", v)
    }

    private func metricPicker(selection: Binding<String>, label: String) -> some View {
        Menu {
            if label == "Right" {
                Button("None") { selection.wrappedValue = RouteGraphMetrics.metricNone }
                Divider()
            }
            ForEach(RouteGraphMetrics.all) { metric in
                Button(metric.label) { selection.wrappedValue = metric.id }
            }
        } label: {
            HStack(spacing: 4) {
                Circle()
                    .fill(RouteGraphMetrics.metric(byId: selection.wrappedValue)?.color ?? .clear)
                    .frame(width: 8, height: 8)
                Text(RouteGraphMetrics.metric(byId: selection.wrappedValue)?.label ?? "None")
                    .font(.caption)
                Image(systemName: "chevron.down")
                    .font(.caption2)
            }
        }
        .buttonStyle(.plain)
    }
}
