import SwiftUI

/// Fixed scrub-readout strip above the chart (§4.7). A floating tooltip under
/// the finger would be hand-occluded, so per-layer values live in a persistent
/// strip that updates live while scrubbing. Shows distance/time + cursor
/// altitude + conditions at the nearest route point, and a "Sounding ›"
/// deep-link to the Skew-T tab for the active point.
struct CrossSectionReadoutView: View {
    let vizData: VizRouteData
    let scrubDistanceNm: Double?
    let scrubAltitudeFt: Double?
    let onSounding: () -> Void
    /// Route-graph metric ids selected below the chart (§4.7 unified cursor):
    /// their value at the cursor is shown here too, so the strip and the graph
    /// share one cursor instead of two tooltips.
    var routeGraphMetricIds: [String] = []

    var body: some View {
        HStack(spacing: Theme.spacingM) {
            if let dist = scrubDistanceNm, let pt = nearestPoint(dist) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(headline(dist))
                        .font(.tabularData(.subheadline)).bold()
                        .foregroundStyle(Theme.text)
                    Text(timeText(pt))
                        .font(.tabularData(.caption2))
                        .foregroundStyle(Theme.textMuted)
                }
                Spacer()
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Theme.spacingS) {
                        // Route-graph metric value(s) at the cursor — colour-dotted
                        // to match the graph below (unified cursor, §4.7).
                        ForEach(graphChips(pt), id: \.text) { chip in
                            HStack(spacing: 3) {
                                Circle().fill(chip.color).frame(width: 6, height: 6)
                                Text(chip.text)
                            }
                            .font(.tabularData(.caption2))
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(Theme.bg, in: Capsule())
                            .foregroundStyle(Theme.text)
                        }
                        ForEach(chips(pt), id: \.self) { chip in
                            Text(chip)
                                .font(.tabularData(.caption2))
                                .padding(.horizontal, 6).padding(.vertical, 3)
                                .background(Theme.bg, in: Capsule())
                                .foregroundStyle(Theme.text)
                        }
                    }
                }
                soundingButton
            } else {
                Text("Drag across the chart to read values")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                Spacer()
                soundingButton
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
        .background(Theme.surface)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.border).frame(height: 0.5) }
    }

    private var soundingButton: some View {
        Button(action: onSounding) {
            HStack(spacing: 2) {
                Text("Sounding")
                Image(systemName: "chevron.right")
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(Theme.primary)
        }
    }

    private func nearestPoint(_ dist: Double) -> VizPoint? {
        vizData.points.min { abs($0.distanceNm - dist) < abs($1.distanceNm - dist) }
    }

    /// The selected route-graph metric value(s) at the cursor point, labelled and
    /// colour-keyed to the graph below.
    private func graphChips(_ pt: VizPoint) -> [(text: String, color: Color)] {
        routeGraphMetricIds.compactMap { id in
            guard id != "none", let m = RouteGraphMetrics.metric(byId: id),
                  let v = m.getValue(pt) else { return nil }
            return (text: "\(m.label): \(m.formatValue(v))", color: m.color)
        }
    }

    private func headline(_ dist: Double) -> String {
        var s = "\(Int(dist)) nm"
        if let alt = scrubAltitudeFt, alt > 0 {
            s += alt >= 10000 ? " · FL\(Int(alt / 100))" : " · \(Int(alt))′"
        }
        return s
    }

    private func timeText(_ pt: VizPoint) -> String {
        guard let date = ISO8601DateFormatter().date(from: pt.time) else { return "" }
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        fmt.timeZone = TimeZone(identifier: "UTC")
        return "\(fmt.string(from: date))Z"
    }

    /// Per-layer values at the cursor: temperature, wind, cloud cover, plus any
    /// hazard band the cursor altitude intersects (icing / CAT / convective).
    private func chips(_ pt: VizPoint) -> [String] {
        var out: [String] = []
        if let t = pt.temperatureC { out.append("\(Int(t))°C") }
        let hw = Int(pt.headwindKt.rounded())
        out.append("\(abs(hw)) kt \(hw >= 0 ? "HW" : "TW")")
        out.append("\(Int(pt.cloudCoverTotalPct))% cloud")
        if let alt = scrubAltitudeFt {
            if let icing = pt.icingOgimetNwpZones.first(where: { alt >= $0.baseFt && alt <= $0.topFt })
                ?? pt.icingZones.first(where: { alt >= $0.baseFt && alt <= $0.topFt }) {
                out.append("Icing: \(icing.risk)")
            }
            if let cat = pt.catLayers.first(where: { alt >= $0.baseFt && alt <= $0.topFt }) {
                out.append("CAT: \(cat.risk)")
            }
            if let base = pt.nwpConvectiveBaseFt, let top = pt.nwpConvectiveTopFt,
               alt >= base && alt <= top, pt.nwpConvectiveRisk != "none" {
                out.append("Convective: \(pt.nwpConvectiveRisk)")
            }
        }
        return out
    }
}
