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

    // Formatters are expensive to build; scrubbing calls timeText per drag tick.
    private static let isoParser = ISO8601DateFormatter()
    private static let hhmmUTC: DateFormatter = {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        fmt.timeZone = TimeZone(identifier: "UTC")
        return fmt
    }()

    private func timeText(_ pt: VizPoint) -> String {
        guard let date = Self.isoParser.date(from: pt.time) else { return "" }
        return "\(Self.hhmmUTC.string(from: date))Z"
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
        out += Self.observedChips(
            pt, sources: vizData.observed, scrubAltitudeFt: scrubAltitudeFt)
        return out
    }

    /// Measured values at this point (#574), kept visibly separate from the
    /// forecast chips above by their "Obs" prefix — the whole point of the layer
    /// is that a pilot can tell measured from modelled at a glance.
    ///
    /// SYNC — the same facts the web's `observedSurface` / `observedTops` tooltip
    /// definitions report, condensed to the chip idiom.
    ///
    /// `sources` is not decoration: it says which of the four streams was
    /// actually sampled, and a chip may only be emitted for a stream that was.
    /// `ObservedResolver` builds a route point from whichever source has a
    /// station, so on a briefing where OPERA is down but lightning is up — a
    /// state `ObservedSourceStatus` reports explicitly — the point carries
    /// `dbz == nil` with `radarNoCoverage == false`. Reading that as "no echo"
    /// asserts an empty observation for an instrument that never looked, which
    /// is the exact three-state conflation this feature exists to prevent. The
    /// drawing layers avoid it by guarding on the field; in words there is no
    /// equivalent to "draw nothing", so the guard has to be explicit.
    static func observedChips(
        _ pt: VizPoint, sources: VizObserved?, scrubAltitudeFt: Double?
    ) -> [String] {
        guard let o = pt.observed, let sources else { return [] }
        var out: [String] = []

        // Radar reflectivity. Three states, never two: "does not see here" is
        // not "clear" — and neither is "was not sampled", handled above.
        if sources.reflectivity != nil {
            if o.radarNoCoverage {
                out.append("Obs radar: no coverage")
            } else if let dbz = o.dbz {
                out.append("Obs radar: \(Int(dbz.rounded())) dBZ")
            } else {
                out.append("Obs radar: no echo")
            }
        }

        // Rain rate is its own OPERA product on its own 15-minute cadence, so it
        // gets its own chip rather than a suffix on the reflectivity one: the two
        // can be present and absent independently, and hanging the rate off
        // `dbz != nil` dropped it on exactly the marginal-echo points where a
        // measured rate is worth having.
        if sources.rainRate != nil {
            if o.rateNoCoverage {
                out.append("Obs rain: no coverage")
            } else if let rate = o.rateMmH {
                out.append("Obs rain \(String(format: "%.1f", rate)) mm/h")
            }
        }

        // Lightning is the one source where absence is a positive observation:
        // it is a point product and the imager sees the whole disc, so an empty
        // window means no flashes, not "we could not tell". Saying so is the
        // answer to the question; omitting the chip would leave the pilot unable
        // to distinguish it from a source that never reported.
        if sources.lightning != nil {
            if o.flashCount > 0 {
                let rate = o.flashRate.map { " (\(String(format: "%.1f", $0))/1000km²/min)" } ?? ""
                out.append("Obs \(o.flashCount) flash\(o.flashCount == 1 ? "" : "es")\(rate)")
            } else {
                out.append("Obs no flashes")
            }
        }

        if sources.cloudTops != nil {
            out += observedTopChips(o, scrubAltitudeFt: scrubAltitudeFt)
        }
        return out
    }

    private static func observedTopChips(
        _ o: VizObservedPoint, scrubAltitudeFt: Double?
    ) -> [String] {
        guard !o.topsNoCoverage else { return ["Obs tops: no retrieval"] }
        var out: [String] = []
        if let highest = o.topsHighestFt {
            var s = "Obs top \(ObservedBadge.flLabel(highest))"
            // The number that separates "cannot get on top" from "wispy" —
            // height alone renders a solid deck and thin cirrus identically.
            if let opacity = o.topsHighestCloudiness {
                let pct = Int((opacity * 100).rounded())
                let how = pct >= 90 ? "solid" : pct >= 50 ? "broken" : "thin"
                s += " · \(pct)% opaque (\(how))"
            }
            out.append(s)
        }
        // Pressure-based FL of that same top: what an altimeter agrees with,
        // unlike the geometric height above it. Carried separately because the
        // two answer different questions and can differ materially — which is
        // only visible if both are on screen.
        if let aviationFl = o.topsHighestAviationFl {
            out.append("Obs ≈FL\(Int(aviationFl.rounded())) pressure")
        }
        if let coldest = o.topsColdestC {
            out.append("Obs coldest \(Int(coldest.rounded()))°C")
        }
        // Which band the cursor is sitting in, with share AND count: 4% of 201
        // pixels and 4% of 3 are very different evidence, and only one of them is
        // worth acting on. The share is of the SKY the retrieval could see, so it
        // reads as coverage — "4% of the area around here had its cloud top in
        // FL180-190" — and the bands at a point sum to how cloudy the disc was,
        // not to 100%.
        if let alt = scrubAltitudeFt,
           let bin = o.topsBins.first(where: { alt >= $0.loFt && alt < $0.hiFt && $0.count > 0 }) {
            let pct = bin.fraction * 100
            let share = pct >= 1 ? "\(Int(pct.rounded()))%" : "<1%"
            out.append("Obs \(bin.label): \(share) of sky (\(bin.count) px)")
        }
        // The retrieval's own multi-layer-suspect flag — the case where
        // committing to one cloud top is least trustworthy.
        if o.topsMultiLayerFraction > 0.1 {
            out.append("Obs \(Int((o.topsMultiLayerFraction * 100).rounded()))% multi-layer suspect")
        }
        return out
    }
}
