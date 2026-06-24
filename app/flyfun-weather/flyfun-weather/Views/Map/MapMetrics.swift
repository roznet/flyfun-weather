import SwiftUI

/// Route-map segment metrics (§4.9) — the iOS mirror of the web `MAP_METRICS`
/// registry. One metric drives BOTH the segment colour and width
/// (`getColor`/`getWidth`), so e.g. a low ceiling renders thick = dangerous.
/// Adding a metric here is the iOS half of the shared registry (§1E).
struct MapMetric: Identifiable {
    let id: String
    let label: String
    let altitudeDependent: Bool
    let getValue: (VizPoint, Double?) -> Double?
    let color: (Double) -> Color
    let width: (Double) -> Double
    let legend: [(label: String, color: Color)]
}

enum MapMetrics {
    /// Segment line width grows with the metric value (clamped to [minW, maxW]).
    private static func linearWidth(_ v: Double, _ maxV: Double, _ minW: Double = 3, _ maxW: Double = 18) -> Double {
        let t = max(0, min(1, v / maxV))
        return minW + t * (maxW - minW)
    }

    private static let riskRank: [String: Double] = ["none": 0, "low": 1, "light": 1, "moderate": 2, "high": 3, "severe": 3, "extreme": 4]

    static let all: [MapMetric] = [
        MapMetric(
            id: "cloud-cover-total", label: "Cloud Cover", altitudeDependent: false,
            getValue: { p, _ in p.cloudCoverTotalPct },
            color: { MapColors.cloudCover($0) },
            width: { linearWidth($0, 100) },
            legend: [("Clear", MapColors.cloudCover(0)), ("50%", MapColors.cloudCover(50)), ("OVC", MapColors.cloudCover(100))]
        ),
        MapMetric(
            id: "headwind", label: "Headwind", altitudeDependent: false,
            getValue: { p, _ in max(0, p.headwindKt) },
            color: { MapColors.headwind($0) },
            width: { linearWidth($0, 30) },
            legend: [("Calm", MapColors.headwind(0)), ("15 kt", MapColors.headwind(15)), ("30 kt", MapColors.headwind(30))]
        ),
        MapMetric(
            id: "tailwind", label: "Tailwind", altitudeDependent: false,
            getValue: { p, _ in max(0, -p.headwindKt) },
            color: { MapColors.headwind(-$0) },
            width: { linearWidth($0, 30) },
            legend: [("Calm", MapColors.headwind(0)), ("15 kt", MapColors.headwind(-15)), ("30 kt", MapColors.headwind(-30))]
        ),
        MapMetric(
            id: "cape", label: "CAPE", altitudeDependent: false,
            getValue: { p, _ in p.capeSurfaceJkg },
            color: { MapColors.cape($0) },
            width: { linearWidth($0, 2000) },
            legend: [("0", MapColors.cape(0)), ("1000", MapColors.cape(1000)), ("2000+", MapColors.cape(2000))]
        ),
        MapMetric(
            id: "convective-risk", label: "Convective Risk", altitudeDependent: false,
            getValue: { p, _ in riskRank[p.convectiveRisk] ?? 0 },
            color: { MapColors.risk($0) },
            width: { linearWidth($0, 4) },
            legend: [("None", MapColors.risk(0)), ("Mod", MapColors.risk(2)), ("Extreme", MapColors.risk(4))]
        ),
        MapMetric(
            id: "nwp-ceiling", label: "Ceiling", altitudeDependent: false,
            getValue: { p, _ in p.nwpCloudDiag?.ceilingFt },
            color: { MapColors.ceiling($0) },
            // Lower ceiling → thicker line (more hazardous).
            width: { ceiling in linearWidth(max(0, 5000 - ceiling), 5000) },
            legend: [("LIFR", MapColors.ceiling(200)), ("IFR", MapColors.ceiling(800)), ("MVFR", MapColors.ceiling(2000)), ("VFR", MapColors.ceiling(5000))]
        ),
        MapMetric(
            id: "icing-at-level", label: "Icing at FL", altitudeDependent: true,
            getValue: { p, altFt in
                guard let alt = altFt else { return nil }
                let zones = p.icingOgimetNwpZones.isEmpty ? p.icingZones : p.icingOgimetNwpZones
                let hit = zones.filter { alt >= $0.baseFt && alt <= $0.topFt }
                    .map { riskRank[$0.risk] ?? 0 }.max()
                return hit ?? 0
            },
            color: { MapColors.risk($0) },
            width: { linearWidth($0, 3) },
            legend: [("None", MapColors.risk(0)), ("Light", MapColors.risk(1)), ("Mod", MapColors.risk(2)), ("Severe", MapColors.risk(3))]
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

    static func ceiling(_ ft: Double) -> Color {
        if ft < 500 { return Color(.sRGB, red: 0.56, green: 0.14, blue: 0.67, opacity: 0.9) }   // LIFR purple
        if ft < 1000 { return Color(.sRGB, red: 0.86, green: 0.21, blue: 0.27, opacity: 0.9) }  // IFR red
        if ft < 3000 { return Color(.sRGB, red: 0.90, green: 0.60, blue: 0.0, opacity: 0.9) }   // MVFR amber
        return Color(.sRGB, red: 0.10, green: 0.53, blue: 0.33, opacity: 0.9)                   // VFR green
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
