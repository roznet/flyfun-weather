import SwiftUI

/// Route-map segment metrics (§4.9) — the iOS mirror of the web `MAP_METRICS`
/// registry (`web/ts/visualization/route-map/metrics.ts`). One metric drives
/// BOTH the segment colour and width (`color`/`width`), so e.g. a low ceiling
/// renders thick = dangerous. On iPad (regular width) the colour and width axes
/// can be driven by *separate* metrics; on iPhone one metric drives both.
/// Adding a metric here is the iOS half of the shared 13-metric registry (§1E).
struct MapMetric: Identifiable {
    /// How a segment's value is derived from its two endpoint viz-points.
    /// Ordinal/risk metrics take the worst (MAX) so a single hazardous endpoint
    /// is never diluted by a calm neighbour; continuous fields average.
    enum Aggregation { case average, worst }

    let id: String
    let label: String
    let altitudeDependent: Bool
    let aggregation: Aggregation
    let getValue: (VizPoint, Double?) -> Double?
    let color: (Double) -> Color
    let width: (Double) -> Double
    let legend: [(label: String, color: Color)]

    /// Combine the metric values at a segment's two endpoints per `aggregation`.
    func segmentValue(_ a: VizPoint, _ b: VizPoint, altitudeFt: Double?) -> Double? {
        let va = getValue(a, altitudeFt)
        let vb = getValue(b, altitudeFt)
        guard let va else { return vb }
        guard let vb else { return va }
        switch aggregation {
        case .average: return (va + vb) / 2
        case .worst: return Swift.max(va, vb)
        }
    }
}

enum MapMetrics {
    /// Segment line width grows with the metric value (clamped to [minW, maxW]).
    private static func linearWidth(_ v: Double, _ maxV: Double, _ minW: Double = 3, _ maxW: Double = 18) -> Double {
        let t = max(0, min(1, abs(v) / maxV))
        return minW + t * (maxW - minW)
    }

    private static let riskRank: [String: Double] = [
        "none": 0, "marginal": 1, "low": 1, "light": 1,
        "moderate": 2, "high": 3, "severe": 3, "extreme": 4,
    ]

    /// good → 0, moderate → 1, poor → 2 (ordinal, so segments take the worst).
    private static func agreementRank(_ s: String) -> Double {
        switch s.lowercased() {
        case "poor": return 2
        case "moderate": return 1
        default: return 0
        }
    }

    // MARK: Altitude-dependent helpers (port of metrics.ts)

    /// Worst risk ordinal among zones spanning the given altitude.
    private static func worstRiskAtAlt(_ zones: [VizIcingZone], _ altFt: Double) -> Double {
        var worst = 0.0
        for z in zones where altFt >= z.baseFt && altFt <= z.topFt {
            worst = Swift.max(worst, riskRank[z.risk] ?? 0)
        }
        return worst
    }

    private static func worstRiskAtAlt(_ layers: [VizCATLayer], _ altFt: Double) -> Double {
        var worst = 0.0
        for l in layers where altFt >= l.baseFt && altFt <= l.topFt {
            worst = Swift.max(worst, riskRank[l.risk] ?? 0)
        }
        return worst
    }

    /// Peak SFIP (0–100) among zones spanning the altitude; nil if none.
    private static func sfipAtAlt(_ zones: [VizSfipZone], _ altFt: Double) -> Double? {
        var best: Double?
        for z in zones where altFt >= z.baseFt && altFt <= z.topFt {
            guard let v = z.meanSfip100 else { continue }
            best = best.map { Swift.max($0, v) } ?? v
        }
        return best
    }

    private static let coverageWeight: [String: Double] = ["sct": 0.3, "bkn": 0.7, "ovc": 1.0]

    /// Max cloud presence (% via coverage weight) among layers at the altitude.
    private static func cloudAtAlt(_ layers: [VizCloudLayer], _ altFt: Double) -> Double {
        var maxCov = 0.0
        for l in layers where altFt >= l.baseFt && altFt <= l.topFt {
            maxCov = Swift.max(maxCov, coverageWeight[l.coverage.lowercased()] ?? 0.5)
        }
        return maxCov * 100
    }

    /// Temperature (°C) at an altitude, interpolated from the known isotherm
    /// levels (freezing / −10 / −20), extrapolated at ~2°C/1000ft. Mirrors the
    /// web `tempAtAltitude`. nil when no freezing level is known.
    private static func tempAtAltitude(_ p: VizPoint, _ altFt: Double) -> Double? {
        guard let fz = p.altitudeLines.freezingLevelFt else { return nil }
        var refs: [(alt: Double, temp: Double)] = [(fz, 0)]
        if let m10 = p.altitudeLines.minus10cLevelFt { refs.append((m10, -10)) }
        if let m20 = p.altitudeLines.minus20cLevelFt { refs.append((m20, -20)) }
        refs.sort { $0.alt < $1.alt }

        if altFt <= refs[0].alt {
            return refs[0].temp + (refs[0].alt - altFt) * 2 / 1000
        }
        if altFt >= refs[refs.count - 1].alt {
            return refs[refs.count - 1].temp - (altFt - refs[refs.count - 1].alt) * 2 / 1000
        }
        for i in 0..<(refs.count - 1) where altFt >= refs[i].alt && altFt <= refs[i + 1].alt {
            let frac = (altFt - refs[i].alt) / (refs[i + 1].alt - refs[i].alt)
            return refs[i].temp + frac * (refs[i + 1].temp - refs[i].temp)
        }
        return nil
    }

    // MARK: Registry (13-metric shared contract — ids match web MAP_METRICS)

    static let all: [MapMetric] = [
        MapMetric(
            id: "cloud-cover-total", label: "Cloud Cover (Total)", altitudeDependent: false, aggregation: .average,
            getValue: { p, _ in p.cloudCoverTotalPct },
            color: { MapColors.cloudCover($0) },
            width: { linearWidth($0, 100) },
            legend: [("Clear", MapColors.cloudCover(0)), ("50%", MapColors.cloudCover(50)), ("OVC", MapColors.cloudCover(100))]
        ),
        MapMetric(
            id: "cloud-cover-low", label: "Cloud Cover (Low)", altitudeDependent: false, aggregation: .average,
            getValue: { p, _ in p.cloudCoverLowPct },
            color: { MapColors.cloudCover($0) },
            width: { linearWidth($0, 100) },
            legend: [("Clear", MapColors.cloudCover(0)), ("50%", MapColors.cloudCover(50)), ("OVC", MapColors.cloudCover(100))]
        ),
        MapMetric(
            id: "convective-risk", label: "Convective Risk", altitudeDependent: false, aggregation: .worst,
            getValue: { p, _ in riskRank[p.convectiveRisk] ?? 0 },
            color: { MapColors.risk($0) },
            width: { linearWidth($0, 4) },
            legend: [("None", MapColors.risk(0)), ("Mod", MapColors.risk(2)), ("Extreme", MapColors.risk(4))]
        ),
        MapMetric(
            id: "headwind", label: "Headwind", altitudeDependent: false, aggregation: .average,
            getValue: { p, _ in max(0, p.headwindKt) },
            color: { MapColors.headwind($0) },
            width: { linearWidth($0, 30) },
            legend: [("Calm", MapColors.headwind(0)), ("15 kt", MapColors.headwind(15)), ("30 kt", MapColors.headwind(30))]
        ),
        MapMetric(
            id: "tailwind", label: "Tailwind", altitudeDependent: false, aggregation: .average,
            getValue: { p, _ in max(0, -p.headwindKt) },
            color: { MapColors.headwind(-$0) },
            width: { linearWidth($0, 30) },
            legend: [("Calm", MapColors.headwind(0)), ("15 kt", MapColors.headwind(-15)), ("30 kt", MapColors.headwind(-30))]
        ),
        MapMetric(
            id: "cape", label: "CAPE", altitudeDependent: false, aggregation: .average,
            getValue: { p, _ in p.capeSurfaceJkg },
            color: { MapColors.cape($0) },
            width: { linearWidth($0, 2000) },
            legend: [("0", MapColors.cape(0)), ("1000", MapColors.cape(1000)), ("2000+", MapColors.cape(2000))]
        ),
        MapMetric(
            id: "nwp-ceiling", label: "NWP Ceiling", altitudeDependent: false, aggregation: .average,
            getValue: { p, _ in p.nwpCloudDiag?.ceilingFt },
            color: { MapColors.ceiling($0) },
            // Lower ceiling → thicker line (more hazardous).
            width: { ceiling in linearWidth(max(0, 5000 - ceiling), 5000) },
            legend: [("LIFR", MapColors.ceiling(200)), ("IFR", MapColors.ceiling(800)), ("MVFR", MapColors.ceiling(2000)), ("VFR", MapColors.ceiling(5000))]
        ),
        MapMetric(
            id: "temp-at-level", label: "Temperature at FL", altitudeDependent: true, aggregation: .average,
            getValue: { p, altFt in altFt.flatMap { tempAtAltitude(p, $0) } },
            color: { MapColors.temperature($0) },
            // Colder → thicker (icing territory).
            width: { temp in linearWidth(5 - max(-20, min(5, temp)), 25) },
            legend: [("-20°", MapColors.temperature(-20)), ("0°", MapColors.temperature(0)), ("10°", MapColors.temperature(10))]
        ),
        MapMetric(
            id: "model-agreement", label: "Model Agreement", altitudeDependent: false, aggregation: .worst,
            getValue: { p, _ in agreementRank(p.worstModelAgreement) },
            color: { MapColors.agreement($0) },
            width: { linearWidth($0, 2) },
            legend: [("Good", MapColors.agreement(0)), ("Mod", MapColors.agreement(1)), ("Poor", MapColors.agreement(2))]
        ),
        MapMetric(
            id: "icing-risk-at-level", label: "Icing Risk at FL", altitudeDependent: true, aggregation: .worst,
            getValue: { p, altFt in
                guard let alt = altFt else { return nil }
                // Prefer the OGIMET-NWP icing envelope when present (matches the
                // cross-section's preference), else the diagnostic icing zones.
                let zones = p.icingOgimetNwpZones.isEmpty ? p.icingZones : p.icingOgimetNwpZones
                return worstRiskAtAlt(zones, alt)
            },
            color: { MapColors.risk($0) },
            width: { linearWidth($0, 3) },
            legend: [("None", MapColors.risk(0)), ("Light", MapColors.risk(1)), ("Mod", MapColors.risk(2)), ("Severe", MapColors.risk(3))]
        ),
        MapMetric(
            id: "sfip-at-level", label: "SFIP at FL", altitudeDependent: true, aggregation: .average,
            getValue: { p, altFt in altFt.flatMap { sfipAtAlt(p.sfipZones, $0) } },
            color: { MapColors.sfip($0) },
            width: { linearWidth($0, 100) },
            legend: [("Low", MapColors.sfip(0)), ("Med", MapColors.sfip(35)), ("High", MapColors.sfip(65)), ("V.High", MapColors.sfip(90))]
        ),
        MapMetric(
            id: "cat-risk-at-level", label: "CAT Risk at FL", altitudeDependent: true, aggregation: .worst,
            getValue: { p, altFt in altFt.map { worstRiskAtAlt(p.catLayers, $0) } },
            color: { MapColors.risk($0) },
            width: { linearWidth($0, 3) },
            legend: [("None", MapColors.risk(0)), ("Light", MapColors.risk(1)), ("Mod", MapColors.risk(2)), ("Severe", MapColors.risk(3))]
        ),
        MapMetric(
            id: "cloud-at-level", label: "Cloud at FL", altitudeDependent: true, aggregation: .average,
            getValue: { p, altFt in altFt.map { cloudAtAlt(p.cloudLayers, $0) } },
            color: { MapColors.cloudCover($0) },
            width: { linearWidth($0, 100) },
            legend: [("Clear", MapColors.cloudCover(0)), ("50%", MapColors.cloudCover(50)), ("OVC", MapColors.cloudCover(100))]
        ),
    ]

    static func metric(byId id: String) -> MapMetric? { all.first { $0.id == id } }
}

/// Map segment colour scales — distinct from the cross-section viz palette but
/// the same severity-as-meaning intent.
enum MapColors {
    static func cloudCover(_ pct: Double) -> Color {
        let t = max(0, min(1, pct / 100))
        // Light blue-gray (clear) → dark slate (overcast).
        return Color(.sRGB, red: 0.75 - 0.45 * t, green: 0.80 - 0.45 * t, blue: 0.88 - 0.40 * t, opacity: 0.9)
    }

    static func headwind(_ kt: Double) -> Color {
        // Tailwind (negative) green, calm neutral, headwind red.
        if kt <= 0 { return Color(.sRGB, red: 0.20, green: 0.65, blue: 0.30, opacity: 0.9) }
        let t = min(1, kt / 30)
        return Color(.sRGB, red: 0.55 + 0.40 * t, green: 0.60 - 0.45 * t, blue: 0.20, opacity: 0.9)
    }

    static func cape(_ j: Double) -> Color {
        let t = max(0, min(1, j / 2000))
        return Color(.sRGB, red: 0.95, green: 0.85 - 0.65 * t, blue: 0.20 * (1 - t), opacity: 0.9)
    }

    /// Ceiling severity = standard aviation flight-category colours, matching
    /// `FlightCategoryBadge` (VFR green, MVFR blue, IFR red, LIFR purple). We
    /// deliberately do NOT use the web's amber MVFR here — within the app there
    /// is a single MVFR colour, the badge's blue.
    static func ceiling(_ ft: Double) -> Color {
        if ft < 500 { return flightCategory("LIFR") }
        if ft < 1000 { return flightCategory("IFR") }
        if ft < 3000 { return flightCategory("MVFR") }
        return flightCategory("VFR")
    }

    /// Canonical flight-category colour — identical hues to `FlightCategoryBadge`.
    static func flightCategory(_ category: String) -> Color {
        switch category.uppercased() {
        case "VFR": return .green
        case "MVFR": return .blue
        case "IFR": return .red
        case "LIFR": return .purple
        default: return .gray
        }
    }

    static func temperature(_ c: Double) -> Color {
        // -20°C blue → ~0°C pale → +40°C red (web temperatureMapColor).
        let t = max(0, min(1, (c + 20) / 60))
        let r: Double, g: Double, b: Double
        if t < 0.33 {
            let s = t / 0.33
            r = (30 + 125 * s) / 255; g = (80 + 125 * s) / 255; b = (220 + 35 * s) / 255
        } else if t < 0.67 {
            let s = (t - 0.33) / 0.34
            r = (155 + 100 * s) / 255; g = (205 + 50 * s) / 255; b = (255 - 40 * s) / 255
        } else {
            let s = (t - 0.67) / 0.33
            r = 1; g = (255 - 185 * s) / 255; b = (215 - 175 * s) / 255
        }
        return Color(.sRGB, red: r, green: g, blue: b, opacity: 0.9)
    }

    static func agreement(_ rank: Double) -> Color {
        switch Int(rank.rounded()) {
        case 0: return Color(.sRGB, red: 34 / 255, green: 197 / 255, blue: 94 / 255, opacity: 0.9)   // good — green
        case 1: return Color(.sRGB, red: 249 / 255, green: 115 / 255, blue: 22 / 255, opacity: 0.9)  // moderate — orange
        default: return Color(.sRGB, red: 239 / 255, green: 68 / 255, blue: 68 / 255, opacity: 0.9)  // poor — red
        }
    }

    static func sfip(_ v: Double) -> Color {
        if v <= 20 { return Color(.sRGB, red: 34 / 255, green: 197 / 255, blue: 94 / 255, opacity: 0.9) }
        if v <= 50 { return Color(.sRGB, red: 250 / 255, green: 204 / 255, blue: 21 / 255, opacity: 0.9) }
        if v <= 80 { return Color(.sRGB, red: 249 / 255, green: 115 / 255, blue: 22 / 255, opacity: 0.9) }
        return Color(.sRGB, red: 239 / 255, green: 68 / 255, blue: 68 / 255, opacity: 0.9)
    }

    static func risk(_ rank: Double) -> Color {
        switch Int(rank.rounded()) {
        case 0: return Color(.sRGB, red: 0.55, green: 0.55, blue: 0.6, opacity: 0.55)
        case 1: return Color(.sRGB, red: 0.95, green: 0.80, blue: 0.20, opacity: 0.9)
        case 2: return Color(.sRGB, red: 0.95, green: 0.55, blue: 0.0, opacity: 0.9)
        case 3: return Color(.sRGB, red: 0.86, green: 0.18, blue: 0.18, opacity: 0.9)
        default: return Color(.sRGB, red: 0.50, green: 0.0, blue: 0.45, opacity: 0.9)
        }
    }
}
