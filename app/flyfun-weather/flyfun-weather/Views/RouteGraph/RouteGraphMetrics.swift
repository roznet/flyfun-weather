import SwiftUI

// =============================================================================
// SYNC — keep this registry in lockstep with the web route-graph metric registry:
//   web/ts/visualization/route-graph/metrics.ts
//     (ROUTE_GRAPH_METRICS, CEILING_AGL_CAP_FT, MetricSample, sampleMetric,
//      formatSample, METRIC_NONE)
//
// Metric IDs are shared vocabulary, not per-client names: the web advisory
// lenses name them in their `routeGraph` directives
// (web/ts/visualization/cross-section/advisory-presets.ts), so an id that
// differs here silently breaks that lens when the directive is wired on iOS.
//
// Labels mirror the web's `graph.<id>` i18n strings from
// web/ts/i18n/locales/en.json — iOS is not localized yet, so the English string
// is copied. When a metric's label changes on either side, change both.
// =============================================================================

/// A metric's value at one route point, in four states rather than two.
///
/// `unavailable` means we have no value. `aboveScale` means we have one and it
/// sits off the top of the axis — a distinction a two-state model collapses,
/// rendering "ceiling is excellent" identically to "no data". `noCoverage`
/// means the sensor does not look here at all, which is emphatically not
/// `value(0)`: a radar coverage hole drawn as a gap reads as "no rain", and
/// about half the OPERA grid is such a hole.
enum MetricSample {
    case value(Double)
    case aboveScale(Double)
    case unavailable
    case noCoverage

    /// The number to plot, or nil for the two states that plot nothing.
    /// `aboveScale` yields its value so the renderer can pin it to the cap.
    var plottable: Double? {
        switch self {
        case .value(let v), .aboveScale(let v): v
        case .unavailable, .noCoverage: nil
        }
    }
}

/// A metric that can be plotted on the route graph.
struct RouteGraphMetric: Identifiable {
    let id: String
    let label: String
    let unit: String
    let color: Color
    let renderType: RenderType
    let showZeroLine: Bool
    let zeroLineLabels: (above: String, below: String)?
    /// Fixed Y-axis range. Honoured by `RouteGraphView.chartYScale` — the same
    /// numbers must render at the same visual magnitude as on the web, so this is
    /// never decorative.
    let suggestedRange: ClosedRange<Double>?
    /// Treat values above `suggestedRange`'s upper bound as a distinct
    /// *above-scale* state rather than as data: the axis is pinned to the range
    /// exactly (no padding, so the top tick *is* the cap) and such points render
    /// as a capped marker on the top edge rather than as a gap. Requires
    /// `suggestedRange`.
    ///
    /// A presentation state, not a claim about the weather — the value is known
    /// and never clipped or rewritten, it just does not fit the axis.
    let aboveScale: Bool
    /// True when the sensor behind this metric does not cover the point at all
    /// (#574). Only observed metrics implement it; forecast metrics have no such
    /// state and leave it nil.
    let isNoCoverage: ((VizPoint) -> Bool)?
    /// Extract the numeric value. Returns nil — and *only* nil — when the value
    /// is genuinely unavailable. A known value that happens to sit off the top of
    /// the axis is not nil; see `aboveScale`.
    let getValue: (VizPoint) -> Double?
    let formatValue: (Double) -> String

    enum RenderType {
        case line
        case bar
    }

    init(
        id: String,
        label: String,
        unit: String,
        color: Color,
        renderType: RenderType,
        showZeroLine: Bool = false,
        zeroLineLabels: (above: String, below: String)? = nil,
        suggestedRange: ClosedRange<Double>? = nil,
        aboveScale: Bool = false,
        isNoCoverage: ((VizPoint) -> Bool)? = nil,
        getValue: @escaping (VizPoint) -> Double?,
        formatValue: @escaping (Double) -> String
    ) {
        self.id = id
        self.label = label
        self.unit = unit
        self.color = color
        self.renderType = renderType
        self.showZeroLine = showZeroLine
        self.zeroLineLabels = zeroLineLabels
        self.suggestedRange = suggestedRange
        self.aboveScale = aboveScale
        self.isNoCoverage = isNoCoverage
        self.getValue = getValue
        self.formatValue = formatValue
    }

    /// Classify this metric's value at a point. The single place the four states
    /// are derived — chart, axis and readout strip all read this rather than
    /// re-deriving the cap, so they cannot drift apart. Port of web
    /// `sampleMetric`.
    func sample(at point: VizPoint) -> MetricSample {
        // Checked before the value: for an observed metric a missing number means
        // "the sensor saw nothing" only when it was looking, and the metric alone
        // knows which of the two it is.
        if isNoCoverage?(point) == true { return .noCoverage }
        guard let v = getValue(point) else { return .unavailable }
        if aboveScale, let range = suggestedRange, v > range.upperBound {
            return .aboveScale(v)
        }
        return .value(v)
    }

    /// Readout text for a sample, unit-aware via `formatValue`. Above-scale reads
    /// "> <cap>" — formatted from the cap, not the value, because we are
    /// reporting the axis limit we can show, not disclosing a number we declined
    /// to plot. Only a genuinely absent value reads "N/A". Port of web
    /// `formatSample`.
    func formatSample(_ sample: MetricSample) -> String {
        switch sample {
        case .value(let v): formatValue(v)
        case .aboveScale: "> \(formatValue(suggestedRange?.upperBound ?? 0))"
        case .unavailable: "N/A"
        case .noCoverage: "No coverage"
        }
    }
}

/// Registry of available route graph metrics.
enum RouteGraphMetrics {
    /// Sentinel for "no metric selected" (the optional right Y-axis).
    /// Port of web `METRIC_NONE`. Not spelled `none` — that shadows
    /// `Optional.none` at every inference site that touches this type.
    static let metricNone = "none"

    /// Display cap for the ceiling metrics, in ft AGL. Ceilings above this are of
    /// no practical interest to plot point-by-point, so the axis stops here and
    /// higher ceilings render as above-scale. Purely a display bound — it changes
    /// no meteorological value or threshold. Port of web `CEILING_AGL_CAP_FT`.
    static let ceilingAglCapFt: Double = 5000

    static let all: [RouteGraphMetric] = [
        // --- Observed (#574) ---
        // Measurements, not forecasts. They sit alongside their modelled sibling
        // (`precipitation`) on purpose: putting the two on one axis is how a pilot
        // sees where the model and the radar disagree, and phase 1 leaves that
        // judgement to them rather than computing a verdict.
        RouteGraphMetric(
            id: "observed-rain-rate",
            label: "Observed Rain Rate (radar)",
            unit: "mm/h",
            color: Color(.sRGB, red: 0.03, green: 0.57, blue: 0.70),  // #0891b2
            renderType: .bar,
            isNoCoverage: { $0.observedRadarNoCoverage },
            getValue: { $0.observedRateMmH },
            formatValue: { String(format: "%.1f mm/h", $0) }
        ),
        RouteGraphMetric(
            id: "observed-flash-rate",
            label: "Observed Lightning (flash rate)",
            unit: "/1000km²/min",
            color: Color(.sRGB, red: 0.49, green: 0.23, blue: 0.93),  // #7c3aed
            renderType: .bar,
            getValue: { $0.observedFlashRate },
            formatValue: { $0 == 0 ? "none" : String(format: "%.2f", $0) }
        ),
        RouteGraphMetric(
            id: "headwind",
            label: "Head/Tailwind",
            unit: "kt",
            color: .blue,
            renderType: .line,
            showZeroLine: true,
            zeroLineLabels: (above: "Headwind ↑", below: "Tailwind ↓"),
            getValue: { $0.headwindKt },
            formatValue: { "\(abs(Int($0.rounded()))) kt \($0 >= 0 ? "HW" : "TW")" }
        ),
        RouteGraphMetric(
            id: "crosswind",
            label: "Crosswind",
            unit: "kt",
            color: .purple,
            renderType: .line,
            showZeroLine: true,
            zeroLineLabels: (above: "From Right →", below: "From Left ←"),
            getValue: { $0.crosswindKt },
            formatValue: { "\(abs(Int($0.rounded()))) kt \($0 >= 0 ? "R" : "L")" }
        ),
        RouteGraphMetric(
            id: "temperature",
            label: "Temperature (2m)",
            unit: "°C",
            color: .red,
            renderType: .line,
            showZeroLine: true,
            getValue: { $0.temperatureC },
            formatValue: { String(format: "%.1f°C", $0) }
        ),
        RouteGraphMetric(
            id: "isa-dev",
            label: "ISA Deviation (cruise)",
            unit: "°C",
            color: Color(.sRGB, red: 0.92, green: 0.35, blue: 0.05),  // #ea580c
            renderType: .line,
            // ISA deviation at the elected cruise level (actual − ISA standard).
            // Zero line = on-ISA; above = warmer than standard (higher density
            // altitude, degraded TAS/climb), below = colder. Performance reading.
            showZeroLine: true,
            zeroLineLabels: (above: "Warmer than ISA ↑", below: "Colder than ISA ↓"),
            getValue: { $0.isaDevC },
            // Sign derived from the *rounded* value so a small negative deviation
            // (e.g. −0.3) reads "ISA±0", never "ISA−0"; same guard for the °C part.
            formatValue: { v in
                let dev = Int(v.rounded())
                let isa = dev == 0 ? "±0" : "\(dev > 0 ? "+" : "−")\(abs(dev))"
                let c = String(format: "%.1f", abs(v))
                let cSign = c == "0.0" ? "±" : (v > 0 ? "+" : "−")
                return "ISA\(isa) (\(cSign)\(c)°C)"
            }
        ),
        RouteGraphMetric(
            id: "precipitation",
            label: "Precipitation",
            unit: "mm",
            color: Color(.sRGB, red: 0.05, green: 0.65, blue: 0.91),  // #0ea5e9
            renderType: .bar,
            suggestedRange: 0...5,
            getValue: { $0.precipitationMm },
            formatValue: { String(format: "%.1f mm", $0) }
        ),
        RouteGraphMetric(
            id: "cloud-cover",
            label: "Cloud Cover",
            unit: "%",
            color: .gray,
            renderType: .bar,
            suggestedRange: 0...100,
            getValue: { $0.cloudCoverTotalPct },
            formatValue: { "\(Int($0.rounded()))%" }
        ),
        RouteGraphMetric(
            id: "cape",
            label: "CAPE",
            unit: "J/kg",
            color: .orange,
            renderType: .bar,
            suggestedRange: 0...1000,
            getValue: { $0.capeSurfaceJkg },
            formatValue: { "\(Int($0.rounded())) J/kg" }
        ),
        RouteGraphMetric(
            id: "cin",
            label: "CIN",
            unit: "J/kg",
            color: Color(.sRGB, red: 0.05, green: 0.58, blue: 0.53),  // #0d9488
            renderType: .bar,
            // CIN is convention-negative (energy that inhibits convection), so
            // bars hang below the zero line — the inhibition "cap" reading next to
            // CAPE. Zero sits at the top of the range, so draw it for reference.
            showZeroLine: true,
            suggestedRange: -300...0,
            getValue: { $0.cinSurfaceJkg },
            formatValue: { "\(Int($0.rounded())) J/kg" }
        ),
        RouteGraphMetric(
            id: "qnh",
            // NOT region-aware on iOS: the web switches to "Altimeter"/inHg for
            // the US region, but `UnitsRegion` is not plumbed into iOS yet (see
            // ForecastAirportCard.swift and `project_unit_system`). hPa is correct
            // for the European fleet this app serves; a US user sees hPa here and
            // inHg on the web until the region lands. Tracked as a follow-up.
            label: "QNH",
            unit: "hPa",
            color: Color(.sRGB, red: 0.28, green: 0.33, blue: 0.41),  // #475569
            renderType: .line,
            getValue: { $0.qnhHpa },
            formatValue: { "\(Int($0.rounded())) hPa" }
        ),
        RouteGraphMetric(
            id: "freezing-level",
            label: "Freezing Level",
            unit: "ft",
            color: .cyan,
            renderType: .line,
            getValue: { $0.altitudeLines.freezingLevelFt },
            formatValue: { RouteGraphMetrics.groupedFt($0) + " ft" }
        ),
        RouteGraphMetric(
            id: "ceiling-dd",
            label: "Ceiling DD",
            unit: "ft AGL",
            color: Color(.sRGB, red: 0.55, green: 0.36, blue: 0.96),  // #8b5cf6
            renderType: .line,
            suggestedRange: 0...RouteGraphMetrics.ceilingAglCapFt,
            aboveScale: true,
            // A ceiling well above the route is the best possible news, so it must
            // not return nil — that is reserved for "no sounding", and the two
            // would otherwise be indistinguishable (gap in the line, "N/A").
            // Above the cap is an above-scale state instead.
            getValue: { p in
                guard let ceiling = p.soundingCeilingFt else { return nil }
                return max(0, ceiling - p.terrainElevationFt)
            },
            formatValue: { RouteGraphMetrics.groupedFt($0) + " ft AGL" }
        ),
        RouteGraphMetric(
            id: "ceiling-nwp",
            label: "Ceiling NWP",
            unit: "ft AGL",
            color: Color(.sRGB, red: 0.85, green: 0.27, blue: 0.94),  // #d946ef
            renderType: .line,
            suggestedRange: 0...RouteGraphMetrics.ceilingAglCapFt,
            aboveScale: true,
            getValue: { p in
                guard let ceiling = p.nwpCloudDiag?.ceilingFt else { return nil }
                return max(0, ceiling - p.terrainElevationFt)
            },
            formatValue: { RouteGraphMetrics.groupedFt($0) + " ft AGL" }
        ),
    ]

    static func metric(byId id: String) -> RouteGraphMetric? {
        all.first { $0.id == id }
    }

    /// Thousand-separated feet, matching the web's `toLocaleString()` on the
    /// altitude metrics — a five-digit freezing level is unreadable without it.
    private static func groupedFt(_ v: Double) -> String {
        RouteGraphMetrics.ftFormatter.string(from: NSNumber(value: v.rounded())) ?? "\(Int(v.rounded()))"
    }

    private static let ftFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        return f
    }()
}
