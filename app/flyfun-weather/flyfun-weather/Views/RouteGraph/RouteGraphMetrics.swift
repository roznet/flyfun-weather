import SwiftUI

/// A metric that can be plotted on the route graph.
struct RouteGraphMetric: Identifiable {
    let id: String
    let label: String
    let unit: String
    let color: Color
    let renderType: RenderType
    let showZeroLine: Bool
    let zeroLineLabels: (above: String, below: String)?
    let suggestedRange: ClosedRange<Double>?
    let getValue: (VizPoint) -> Double?
    let formatValue: (Double) -> String

    enum RenderType {
        case line
        case bar
    }
}

/// Registry of available route graph metrics.
enum RouteGraphMetrics {
    static let all: [RouteGraphMetric] = [
        RouteGraphMetric(
            id: "headwind",
            label: "Head/Tailwind",
            unit: "kt",
            color: .blue,
            renderType: .line,
            showZeroLine: true,
            zeroLineLabels: (above: "Headwind", below: "Tailwind"),
            suggestedRange: nil,
            getValue: { $0.headwindKt },
            formatValue: { v in "\(abs(Int(v))) kt \(v >= 0 ? "HW" : "TW")" }
        ),
        RouteGraphMetric(
            id: "crosswind",
            label: "Crosswind",
            unit: "kt",
            color: .purple,
            renderType: .line,
            showZeroLine: true,
            zeroLineLabels: (above: "From Right", below: "From Left"),
            suggestedRange: nil,
            getValue: { $0.crosswindKt },
            formatValue: { v in "\(abs(Int(v))) kt \(v >= 0 ? "R" : "L")" }
        ),
        RouteGraphMetric(
            id: "temperature",
            label: "Temperature (2m)",
            unit: "°C",
            color: .red,
            renderType: .line,
            showZeroLine: true,
            zeroLineLabels: nil,
            suggestedRange: nil,
            getValue: { $0.temperatureC },
            formatValue: { "\(Int($0))°C" }
        ),
        RouteGraphMetric(
            id: "cloud-cover",
            label: "Cloud Cover",
            unit: "%",
            color: .gray,
            renderType: .bar,
            showZeroLine: false,
            zeroLineLabels: nil,
            suggestedRange: 0...100,
            getValue: { $0.cloudCoverTotalPct },
            formatValue: { "\(Int($0))%" }
        ),
        RouteGraphMetric(
            id: "cape",
            label: "CAPE",
            unit: "J/kg",
            color: .orange,
            renderType: .bar,
            showZeroLine: false,
            zeroLineLabels: nil,
            suggestedRange: nil,
            getValue: { $0.capeSurfaceJkg },
            formatValue: { "\(Int($0)) J/kg" }
        ),
        RouteGraphMetric(
            id: "freezing-level",
            label: "Freezing Level",
            unit: "ft",
            color: .cyan,
            renderType: .line,
            showZeroLine: false,
            zeroLineLabels: nil,
            suggestedRange: nil,
            getValue: { $0.altitudeLines.freezingLevelFt },
            formatValue: { "\(Int($0)) ft" }
        ),
        RouteGraphMetric(
            id: "precipitation",
            label: "Precipitation",
            unit: "mm",
            color: Color(.sRGB, red: 0.2, green: 0.4, blue: 1.0),
            renderType: .bar,
            showZeroLine: false,
            zeroLineLabels: nil,
            suggestedRange: nil,
            getValue: { $0.precipitationMm },
            formatValue: { String(format: "%.1f mm", $0) }
        ),
    ]

    static func metric(byId id: String) -> RouteGraphMetric? {
        all.first { $0.id == id }
    }
}
