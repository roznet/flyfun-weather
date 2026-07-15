import SwiftUI
import UIKit

/// Swift interpreter of the **served** forecast-map metric catalog
/// (`web/ts/data/map-metrics-catalog.json`, delivered in the `maps` section of
/// `/api/help/catalog`). This is the iOS half of the #419 "clients render, server
/// decides" contract: colour ramps, thresholds, labels and legends are data, not
/// code, so the same weather can never show in two colours on two devices and a
/// threshold change is a one-line JSON edit both clients pick up.
///
/// A faithful port of `weather-map-format.ts` — `bandColor`, the categorical
/// lookup, the gray ramp, `m_to_sm`, `alternate_needed`, and the per-metric
/// agreement key. Decode with a **plain** `JSONDecoder` (keys are snake_case
/// field names used verbatim as dictionary keys); see `ForecastMapResponse`.
struct ForecastMapCatalog: Decodable, Sendable {
    let version: Int
    let scales: Scales
    let metrics: [String: MetricSpec]

    /// Canonical metric order for the map's colour picker (web `FORECAST_METRICS`).
    static let metricOrder = [
        "flight_category", "alternate_needed", "wind_speed_kt", "crosswind_kt",
        "headwind_kt", "ceiling_ft", "visibility_m", "cape_jkg",
        "convective_risk", "cloud_cover_pct",
    ]

    /// Fallback grey for missing data / unknown scale (web `MUTED`).
    static var muted: UIColor { UIColor(rgbHex: 0x888888) }

    // MARK: - Nested catalog shapes

    struct Scales: Decodable, Sendable {
        let categorical: [String: [String: String]]
        let bands: [String: BandScale]
    }

    struct BandScale: Decodable, Sendable {
        let kind: String
        let nullColor: String?
        let convert: String?
        let stops: [BandStop]?
        let defaultColor: String?
        let base: Double?
        let span: Double?
        let blueBoost: Double?

        enum CodingKeys: String, CodingKey {
            case kind, convert, stops, base
            case nullColor = "null_color"
            case defaultColor = "default"
            case span
            case blueBoost = "blue_boost"
        }
    }

    struct BandStop: Decodable, Sendable {
        let lt: Double?
        let gte: Double?
        let color: String
    }

    struct MetricSpec: Decodable, Sendable {
        let label: String
        let color: ColorSpec
        let legend: Legend
    }

    struct ColorSpec: Decodable, Sendable {
        let kind: String
        let scale: String?
        let field: String?
        let defaultValue: String?
        /// `fallback` when it is a hex string (categorical / alternate_needed).
        let fallbackColor: String?
        /// `fallback` when it is a number (band substitution before banding).
        let fallbackNumber: Double?

        enum CodingKeys: String, CodingKey {
            case kind, scale, field, fallback
            case defaultValue = "default"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            kind = try c.decode(String.self, forKey: .kind)
            scale = try c.decodeIfPresent(String.self, forKey: .scale)
            field = try c.decodeIfPresent(String.self, forKey: .field)
            defaultValue = try c.decodeIfPresent(String.self, forKey: .defaultValue)
            // `fallback` is polymorphic: a hex string for categorical/alt, a
            // number (or null) for bands. Try string first, then number, else nil.
            if let hex = (try? c.decodeIfPresent(String.self, forKey: .fallback)) ?? nil {
                fallbackColor = hex
                fallbackNumber = nil
            } else if let num = (try? c.decodeIfPresent(Double.self, forKey: .fallback)) ?? nil {
                fallbackColor = nil
                fallbackNumber = num
            } else {
                fallbackColor = nil
                fallbackNumber = nil
            }
        }
    }

    struct Legend: Decodable, Sendable {
        let title: String
        let items: [LegendItem]
    }

    // MARK: - Bundled baseline

    /// The bundled copy of `map-metrics-catalog.json`, used before the first
    /// online `/api/help/catalog` sync (and if that sync ever fails). Decoded
    /// once; a plain decoder keeps the snake_case scale/field keys verbatim.
    static let bundledBaseline: ForecastMapCatalog? = {
        guard let url = Bundle.main.url(forResource: "map-metrics-catalog", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ForecastMapCatalog.self, from: data)
    }()

    struct LegendItem: Decodable, Sendable, Identifiable {
        let color: String
        let label: String
        var id: String { "\(color)-\(label)" }
    }
}

// MARK: - Colour evaluation (port of getForecastColor / bandColor)

extension ForecastMapCatalog {
    /// The fill colour for one airport under the active metric + model mode —
    /// the single function markers, card cells and legends all agree on.
    func color(metric: String, airport: ForecastAirport, mode: ForecastModelMode) -> UIColor {
        guard let spec = metrics[metric] else { return Self.muted }
        switch spec.color.kind {
        case "alternate_needed":
            return alternateColor(airport: airport, mode: mode)
        case "categorical":
            return categoricalColor(spec.color, cell: airport.cell(for: mode))
        case "band":
            return bandColorFor(spec.color, cell: airport.cell(for: mode))
        default:
            return Self.muted
        }
    }

    /// Colour a bare consensus/model cell (used by the card's matrix cells, which
    /// colour a single already-selected cell rather than an airport+mode).
    func color(metric: String, cell: (any ForecastCellData)?, airport: ForecastAirport, mode: ForecastModelMode) -> UIColor {
        guard let spec = metrics[metric] else { return Self.muted }
        switch spec.color.kind {
        case "alternate_needed":
            return alternateColor(airport: airport, mode: mode)
        case "categorical":
            return categoricalColor(spec.color, cell: cell)
        case "band":
            return bandColorFor(spec.color, cell: cell)
        default:
            return Self.muted
        }
    }

    private func categoricalColor(_ color: ColorSpec, cell: (any ForecastCellData)?) -> UIColor {
        guard let cell, let scale = color.scale else { return Self.muted }
        var key = cell.categoryField(scale)
        if (key == nil || key?.isEmpty == true), let def = color.defaultValue { key = def }
        guard let key, let table = scales.categorical[scale] else { return color.fallbackUIColor }
        return table[key].flatMap(UIColor.parse) ?? color.fallbackUIColor
    }

    private func bandColorFor(_ color: ColorSpec, cell: (any ForecastCellData)?) -> UIColor {
        guard let cell, let scaleKey = color.scale, let field = color.field,
              let band = scales.bands[scaleKey] else { return Self.muted }
        return bandColor(band: band, raw: cell.numericField(field), fallbackNumber: color.fallbackNumber)
    }

    /// Value → colour for a band scale (`bandColor` in the web).
    func bandColor(band: BandScale, raw: Double?, fallbackNumber: Double?) -> UIColor {
        var v = raw
        if v == nil {
            if let nc = band.nullColor { return UIColor.parse(nc) ?? Self.muted }
            guard let fb = fallbackNumber else { return Self.muted }
            v = fb
        }
        guard let value = v else { return Self.muted }
        if band.kind == "gray_ramp" { return grayRamp(band, value) }
        let val = band.convert == "m_to_sm" ? value / 1609.34 : value
        if band.kind == "threshold_desc" {
            for s in band.stops ?? [] where s.gte.map({ val >= $0 }) == true {
                return UIColor.parse(s.color) ?? Self.muted
            }
            return band.defaultColor.flatMap(UIColor.parse) ?? Self.muted
        }
        // threshold_asc (default)
        for s in band.stops ?? [] where s.lt.map({ val < $0 }) == true {
            return UIColor.parse(s.color) ?? Self.muted
        }
        return band.defaultColor.flatMap(UIColor.parse) ?? Self.muted
    }

    private func grayRamp(_ band: BandScale, _ pct: Double) -> UIColor {
        let base = band.base ?? 220
        let span = band.span ?? 160
        let boost = band.blueBoost ?? 10
        let g = (base - (pct / 100) * span).rounded()
        let clamp: (Double) -> CGFloat = { CGFloat(min(255, max(0, $0)) / 255) }
        return UIColor(red: clamp(g), green: clamp(g), blue: clamp(g + boost), alpha: 1)
    }

    private func alternateColor(airport: ForecastAirport, mode: ForecastModelMode) -> UIColor {
        let flag: AltRequired?
        if mode.isConsensus {
            flag = airport.aggregatedAltRequired(mode: mode)
        } else if case .model(let name) = mode {
            flag = airport.models[name]?.altRequired
        } else {
            flag = nil
        }
        guard let flag else { return Self.muted }
        let n = (flag.faa ? 1 : 0) + (flag.easa ? 1 : 0)
        switch n {
        case 0: return UIColor(rgbHex: 0x22c55e)
        case 1: return UIColor(rgbHex: 0xeab308)
        default: return UIColor(rgbHex: 0xef4444)
        }
    }

    // MARK: - Labels, legends, agreement

    func label(metric: String) -> String { metrics[metric]?.label ?? metric }

    func legend(metric: String) -> Legend? { metrics[metric]?.legend }

    /// The agreement bucket colour for a label (categorical `agreement` scale).
    func agreementColor(_ label: String?) -> UIColor {
        guard let label, let hex = scales.categorical["agreement"]?[label] else { return Self.muted }
        return UIColor.parse(hex) ?? Self.muted
    }

    /// Which `consensus.agreement` key a metric reads — the per-active-metric ring
    /// ("do the models disagree about the thing you're looking at"). Crosswind and
    /// headwind proxy to wind, convective to CAPE (web `metricAgreementKey`).
    static func agreementKey(forMetric metric: String) -> String {
        switch metric {
        case "flight_category": return "flight_category"
        case "wind_speed_kt", "crosswind_kt", "headwind_kt": return "wind_speed_kt"
        case "ceiling_ft": return "ceiling_ft"
        case "cape_jkg", "convective_risk": return "cape_jkg"
        case "visibility_m": return "visibility_m"
        case "cloud_cover_pct": return "cloud_cover_pct"
        default: return "flight_category"
        }
    }
}

extension ForecastMapCatalog.ColorSpec {
    var fallbackUIColor: UIColor {
        fallbackColor.flatMap(UIColor.parse) ?? ForecastMapCatalog.muted
    }
}

// MARK: - Alt-required aggregation (aggAltRequired)

extension ForecastAirport {
    /// Aggregate per-model FAA/EASA alternate-required flags for a consensus mode
    /// (`aggAltRequired`): worst = any model says yes; majority = modal with a
    /// worst tiebreak. nil when no present model carries the flag.
    func aggregatedAltRequired(mode: ForecastModelMode) -> AltRequired? {
        let flags = models.values.compactMap { $0.altRequired }
        guard !flags.isEmpty else { return nil }
        let majority = mode == .majority
        return AltRequired(
            faa: Self.ordinalYes(flags.map { $0.faa }, majority: majority),
            easa: Self.ordinalYes(flags.map { $0.easa }, majority: majority)
        )
    }

    private static func ordinalYes(_ bools: [Bool], majority: Bool) -> Bool {
        guard majority else { return bools.contains(true) }
        let yes = bools.filter { $0 }.count
        let no = bools.count - yes
        // Modal; ties break to the worst ordinal (yes).
        return yes >= no
    }
}

// MARK: - Colour parsing (hex + rgb())

extension UIColor {
    convenience init(rgbHex: UInt32) {
        self.init(
            red: CGFloat((rgbHex >> 16) & 0xff) / 255,
            green: CGFloat((rgbHex >> 8) & 0xff) / 255,
            blue: CGFloat(rgbHex & 0xff) / 255,
            alpha: 1
        )
    }

    /// Parse a catalog colour string: `#rgb`, `#rrggbb`, or `rgb(r,g,b)`.
    static func parse(_ string: String) -> UIColor? {
        let s = string.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") {
            let hex = String(s.dropFirst())
            if hex.count == 3 {
                // #rgb → #rrggbb
                let chars = Array(hex)
                let expanded = chars.map { "\($0)\($0)" }.joined()
                guard let v = UInt32(expanded, radix: 16) else { return nil }
                return UIColor(rgbHex: v)
            }
            guard hex.count == 6, let v = UInt32(hex, radix: 16) else { return nil }
            return UIColor(rgbHex: v)
        }
        if s.lowercased().hasPrefix("rgb(") {
            let inner = s.dropFirst(4).dropLast()
            let parts = inner.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
            guard parts.count == 3 else { return nil }
            return UIColor(red: CGFloat(parts[0] / 255), green: CGFloat(parts[1] / 255),
                           blue: CGFloat(parts[2] / 255), alpha: 1)
        }
        return nil
    }
}

extension Color {
    /// SwiftUI wrapper for a parsed catalog colour, muted grey on failure.
    static func catalog(_ hex: String) -> Color {
        Color(uiColor: UIColor.parse(hex) ?? ForecastMapCatalog.muted)
    }
}
