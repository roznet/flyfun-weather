import Charts
import SwiftUI

/// Data point for chart rendering.
private struct ChartDataPoint: Identifiable {
    let id: Int
    let distance: Double
    let value: Double
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
    private var rightMetric: RouteGraphMetric? { RouteGraphMetrics.metric(byId: rightMetricId) }

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
        let leftData = extractData(points: vizData.points, metric: leftMetric)
        let rightData: [ChartDataPoint] = {
            guard rightMetricId != "none", let rm = rightMetric else { return [] }
            return extractData(points: vizData.points, metric: rm)
        }()

        Chart {
            ForEach(leftData) { pt in
                if leftMetric.renderType == .bar {
                    BarMark(x: .value("Distance", pt.distance), y: .value(leftMetric.label, pt.value))
                        .foregroundStyle(leftMetric.color.opacity(0.6))
                } else {
                    LineMark(x: .value("Distance", pt.distance), y: .value(leftMetric.label, pt.value))
                        .foregroundStyle(leftMetric.color)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
            }

            if let rm = rightMetric, rightMetricId != "none" {
                ForEach(rightData) { pt in
                    if rm.renderType == .bar {
                        BarMark(x: .value("Distance", pt.distance), y: .value(rm.label, pt.value))
                            .foregroundStyle(rm.color.opacity(0.4))
                    } else {
                        LineMark(x: .value("Distance", pt.distance), y: .value(rm.label, pt.value))
                            .foregroundStyle(rm.color)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
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

    private func extractData(points: [VizPoint], metric: RouteGraphMetric) -> [ChartDataPoint] {
        points.enumerated().compactMap { (i, pt) in
            guard let val = metric.getValue(pt) else { return nil }
            return ChartDataPoint(id: i, distance: pt.distanceNm, value: val)
        }
    }

    private func metricPicker(selection: Binding<String>, label: String) -> some View {
        Menu {
            if label == "Right" {
                Button("None") { selection.wrappedValue = "none" }
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
