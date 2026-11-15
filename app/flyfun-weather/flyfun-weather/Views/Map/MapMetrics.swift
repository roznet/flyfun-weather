import SwiftUI
import UIKit

/// Route-map metrics (#998): what colours and sizes the briefing map's route
/// line, read from the **served** route catalog (`route_maps` in
/// `/api/help/catalog`, source `web/ts/data/route-map-metrics-catalog.json`,
/// bundled copy `route-map-metrics-catalog.json`).
///
/// The catalog is the one source for the metric list, labels, groups, colour
/// scales, widths, legends and the default. This file only knows how to read a
/// value off a viz point (`extractors`, same names on web and Android); a
/// catalog metric whose extractor this build does not know is skipped rather
/// than drawn empty, so the server can add one ahead of an app release.
///
/// SYNC: web `web/ts/visualization/route-map/metrics.ts`, Android
/// `ui/map/MapMetrics.kt`.
nonisolated struct RouteMapCatalog: Decodable, Sendable {
    let version: Int
    let defaultMetric: String
    let noDataColor: String
    let groups: [Group]
    let scales: Scales
    let metrics: [Spec]

    struct Group: Decodable, Sendable {
        let key: String
        let label: String
    }

    struct Scales: Decodable, Sendable {
        let bands: [String: ForecastMapCatalog.BandScale]
    }

    struct Spec: Decodable, Sendable {
        let key: String
        let label: String
        let unit: String
        let group: String
        let usesAltitude: Bool
        let value: String
        let aggregation: String
        let scale: String
        let width: Width
        let format: Format
        let legend: [LegendItem]

        enum CodingKeys: String, CodingKey {
            case key, label, unit, group, value, aggregation, scale, width, format, legend
            case usesAltitude = "uses_altitude"
        }
    }

    struct Width: Decodable, Sendable {
        let thinAt: Double
        let thickAt: Double

        enum CodingKeys: String, CodingKey {
            case thinAt = "thin_at"
            case thickAt = "thick_at"
        }
    }

    struct Format: Decodable, Sendable {
        let kind: String
        let decimals: Int?
        let prefix: String?
        let suffix: String?
        let multiply: Double?
        let labels: [String]?
    }

    struct LegendItem: Decodable, Sendable {
        let value: Double
        let label: String
    }

    enum CodingKeys: String, CodingKey {
        case version, groups, scales, metrics
        case defaultMetric = "default_metric"
        case noDataColor = "no_data_color"
    }

    /// The bundled copy, used before the first `/api/help/catalog` sync.
    static let bundledBaseline: RouteMapCatalog? = {
        guard let url = Bundle.main.url(forResource: "route-map-metrics-catalog", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(RouteMapCatalog.self, from: data)
    }()
}

/// One route metric, built from a catalog entry. One metric drives both the
/// segment colour and width (#998: the separate Width metric is gone).
nonisolated struct MapMetric: Identifiable {
    enum Aggregation { case average, worst }

    let id: String
    let label: String
    let unit: String
    let group: String
    let altitudeDependent: Bool
    let aggregation: Aggregation
    let getValue: (VizPoint, Double?) -> Double?
    let color: (Double) -> Color
    let width: (Double) -> Double
    let format: (Double) -> String
    let legend: [(label: String, color: Color)]

    /// Combine the metric values at a segment's two endpoints per `aggregation`:
    /// hazards take the worst so one bad endpoint is never averaged away.
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

/// A SHOWING menu section: At altitude / Along route.
nonisolated struct MapMetricGroup: Identifiable {
    let id: String
    let label: String
    let metrics: [MapMetric]
}

/// The metrics of one catalog, plus its groups, default and no-data colour.
nonisolated struct MapMetrics {
    /// Route-line width range (pt), the same 3…18 as the web's px.
    static let minWidth = 3.0
    static let maxWidth = 18.0

    let all: [MapMetric]
    let groups: [MapMetricGroup]
    let defaultId: String
    let noDataColor: UIColor

    init(catalog: RouteMapCatalog) {
        let built = catalog.metrics.compactMap { Self.build($0, catalog) }
        all = built
        groups = catalog.groups.compactMap { g in
            let ms = built.filter { $0.group == g.key }
            return ms.isEmpty ? nil : MapMetricGroup(id: g.key, label: g.label, metrics: ms)
        }
        defaultId = catalog.defaultMetric
        noDataColor = UIColor.parse(catalog.noDataColor) ?? ForecastMapCatalog.muted
    }

    func metric(byId id: String) -> MapMetric? { all.first { $0.id == id } }

    /// A stored id resolved to a metric: an unknown (renamed, dropped) id falls
    /// back to the catalog default rather than drawing nothing.
    func resolve(_ id: String?) -> MapMetric? {
        id.flatMap(metric(byId:)) ?? metric(byId: defaultId) ?? all.first
    }

    /// Fraction 0 (thinnest) … 1 (thickest) on a metric's width spec.
    static func widthFraction(_ w: RouteMapCatalog.Width, _ v: Double) -> Double {
        let span = w.thickAt - w.thinAt
        guard span != 0 else { return 0 }
        return Swift.min(1, Swift.max(0, (v - w.thinAt) / span))
    }

    private static func build(_ spec: RouteMapCatalog.Spec, _ cat: RouteMapCatalog) -> MapMetric? {
        guard let extract = extractors[spec.value], let band = cat.scales.bands[spec.scale] else { return nil }
        let colorOf: (Double) -> Color = { Color(uiColor: ForecastMapCatalog.bandScaleColor(band, $0)) }
        let fmt = spec.format
        return MapMetric(
            id: spec.key,
            label: spec.label,
            unit: spec.unit,
            group: spec.group,
            altitudeDependent: spec.usesAltitude,
            aggregation: spec.aggregation == "max" ? .worst : .average,
            getValue: extract,
            color: colorOf,
            width: { minWidth + widthFraction(spec.width, $0) * (maxWidth - minWidth) },
            format: { format(fmt, $0) },
            legend: spec.legend.map { ($0.label, colorOf($0.value)) }
        )
    }

    static func format(_ f: RouteMapCatalog.Format, _ v: Double) -> String {
        if f.kind == "rank", let labels = f.labels, !labels.isEmpty {
            let i = Swift.max(0, Swift.min(labels.count - 1, Int(v.rounded())))
            return labels[i]
        }
        let n = v * (f.multiply ?? 1)
        return (f.prefix ?? "") + String(format: "%.\(f.decimals ?? 0)f", n) + (f.suffix ?? "")
    }

    // MARK: Extractors (the only per-metric code; same names on web and Android)

    private static let riskRank: [String: Double] = [
        "none": 0, "marginal": 1, "low": 1, "light": 1,
        "moderate": 2, "high": 3, "severe": 3, "extreme": 4,
    ]

    private static func agreementRank(_ s: String) -> Double {
        switch s.lowercased() {
        case "poor": return 2
        case "moderate": return 1
        default: return 0
        }
    }

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

    private static func sfipAtAlt(_ zones: [VizSfipZone], _ altFt: Double) -> Double? {
        var best: Double?
        for z in zones where altFt >= z.baseFt && altFt <= z.topFt {
            guard let v = z.meanSfip100 else { continue }
            best = best.map { Swift.max($0, v) } ?? v
        }
        return best
    }

    private static let coverageWeight: [String: Double] = ["sct": 0.3, "bkn": 0.7, "ovc": 1.0]

    private static func cloudAtAlt(_ layers: [VizCloudLayer], _ altFt: Double) -> Double {
        var maxCov = 0.0
        for l in layers where altFt >= l.baseFt && altFt <= l.topFt {
            maxCov = Swift.max(maxCov, coverageWeight[l.coverage.lowercased()] ?? 0.5)
        }
        return maxCov * 100
    }

    /// Temperature (°C) at an altitude from the isotherm levels (0 / −10 /
    /// −20 °C), interpolated between them, extrapolated at ~2 °C per 1000 ft.
    private static func tempAtAltitude(_ p: VizPoint, _ altFt: Double) -> Double? {
        guard let fz = p.altitudeLines.freezingLevelFt else { return nil }
        var refs: [(alt: Double, temp: Double)] = [(fz, 0)]
        if let m10 = p.altitudeLines.minus10cLevelFt { refs.append((m10, -10)) }
        if let m20 = p.altitudeLines.minus20cLevelFt { refs.append((m20, -20)) }
        refs.sort { $0.alt < $1.alt }
        if altFt <= refs[0].alt { return refs[0].temp + (refs[0].alt - altFt) * 2 / 1000 }
        let last = refs[refs.count - 1]
        if altFt >= last.alt { return last.temp - (altFt - last.alt) * 2 / 1000 }
        for i in 0..<(refs.count - 1) where altFt >= refs[i].alt && altFt <= refs[i + 1].alt {
            let frac = (altFt - refs[i].alt) / (refs[i + 1].alt - refs[i].alt)
            return refs[i].temp + frac * (refs[i + 1].temp - refs[i].temp)
        }
        return nil
    }

    typealias Extractor = @Sendable (VizPoint, Double?) -> Double?

    /// Catalog `value` → how to read it off a viz point. Altitude extractors
    /// return nil without an altitude.
    static let extractors: [String: Extractor] = [
        "cloud_cover_total_pct": { p, _ in p.cloudCoverTotalPct },
        "cloud_cover_low_pct": { p, _ in p.cloudCoverLowPct },
        "convective_risk_rank": { p, _ in riskRank[p.convectiveRisk] ?? 0 },
        "headwind_kt": { p, _ in Swift.max(0, p.headwindKt) },
        "tailwind_kt": { p, _ in Swift.max(0, -p.headwindKt) },
        "wind_speed_kt": { p, _ in p.windSpeedKt },
        "crosswind_kt": { p, _ in abs(p.crosswindKt) },
        "cape_surface_jkg": { p, _ in p.capeSurfaceJkg },
        "nwp_ceiling_ft": { p, _ in p.nwpCloudDiag?.ceilingFt },
        "visibility_m": { p, _ in p.visibilityM },
        "model_agreement_rank": { p, _ in agreementRank(p.worstModelAgreement) },
        "temperature_at_level_c": { p, alt in alt.flatMap { tempAtAltitude(p, $0) } },
        // The diagnostic icing zones, as the web map (#998: the OGIMET-NWP
        // preference was iOS-only).
        "icing_risk_at_level_rank": { p, alt in alt.map { worstRiskAtAlt(p.icingZones, $0) } },
        "sfip_at_level": { p, alt in alt.flatMap { sfipAtAlt(p.sfipZones, $0) } },
        "cat_risk_at_level_rank": { p, alt in alt.map { worstRiskAtAlt(p.catLayers, $0) } },
        "cloud_at_level_pct": { p, alt in alt.map { cloudAtAlt(p.cloudLayers, $0) } },
    ]
}

/// Canonical flight-category colours, identical hues to `FlightCategoryBadge`
/// (the cross-section's current-conditions layer). The route-map line colours
/// live in the served catalog (#998), not here.
enum MapColors {
    static func flightCategory(_ category: String) -> Color {
        switch category.uppercased() {
        case "VFR": return .green
        case "MVFR": return .blue
        case "IFR": return .red
        case "LIFR": return .purple
        default: return .gray
        }
    }
}
