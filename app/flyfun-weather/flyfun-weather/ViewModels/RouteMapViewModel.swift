import Foundation
import MapKit
import SwiftUI

struct RouteMapLegendStop: Identifiable {
    let value: Double
    let label: String
    let color: Color

    var id: String { "\(label)-\(value)" }
}

/// Native sibling of the web MAP_METRICS registry.
enum RouteMapMetric: String, CaseIterable, Identifiable {
    case cloudCoverTotal = "cloud-cover-total"
    case cloudCoverLow = "cloud-cover-low"
    case convectiveRisk = "convective-risk"
    case headwind = "headwind"
    case tailwind = "tailwind"
    case cape = "cape"
    case nwpCeiling = "nwp-ceiling"
    case tempAtLevel = "temp-at-level"
    case modelAgreement = "model-agreement"
    case icingRiskAtLevel = "icing-risk-at-level"
    case sfipAtLevel = "sfip-at-level"
    case catRiskAtLevel = "cat-risk-at-level"
    case cloudAtLevel = "cloud-at-level"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .cloudCoverTotal: "Cloud Cover (Total)"
        case .cloudCoverLow: "Cloud Cover (Low)"
        case .convectiveRisk: "Convective Risk"
        case .headwind: "Headwind"
        case .tailwind: "Tailwind"
        case .cape: "CAPE"
        case .nwpCeiling: "NWP Ceiling"
        case .tempAtLevel: "Temperature at FL"
        case .modelAgreement: "Model Agreement"
        case .icingRiskAtLevel: "Icing Risk at FL"
        case .sfipAtLevel: "SFIP at FL"
        case .catRiskAtLevel: "CAT Risk at FL"
        case .cloudAtLevel: "Cloud at FL"
        }
    }

    var unit: String {
        switch self {
        case .cloudCoverTotal, .cloudCoverLow, .cloudAtLevel: "%"
        case .headwind, .tailwind: "kt"
        case .cape: "J/kg"
        case .nwpCeiling: "ft"
        case .tempAtLevel: "C"
        default: ""
        }
    }

    var altitudeDependent: Bool {
        switch self {
        case .tempAtLevel, .icingRiskAtLevel, .sfipAtLevel, .catRiskAtLevel, .cloudAtLevel:
            true
        default:
            false
        }
    }

    var legendStops: [RouteMapLegendStop] {
        switch self {
        case .cloudCoverTotal, .cloudCoverLow, .cloudAtLevel:
            [
                .init(value: 0, label: "Clear", color: Self.cloudCoverColor(0)),
                .init(value: 25, label: "25%", color: Self.cloudCoverColor(25)),
                .init(value: 50, label: "50%", color: Self.cloudCoverColor(50)),
                .init(value: 75, label: "75%", color: Self.cloudCoverColor(75)),
                .init(value: 100, label: "Overcast", color: Self.cloudCoverColor(100)),
            ]
        case .convectiveRisk:
            [
                .init(value: 0, label: "None", color: Self.riskColor("none")),
                .init(value: 1, label: "Low", color: Self.riskColor("low")),
                .init(value: 2, label: "Moderate", color: Self.riskColor("moderate")),
                .init(value: 3, label: "High", color: Self.riskColor("high")),
                .init(value: 4, label: "Extreme", color: Self.riskColor("extreme")),
            ]
        case .headwind:
            [
                .init(value: 0, label: "Calm", color: Self.headwindColor(0)),
                .init(value: 10, label: "10 kt", color: Self.headwindColor(10)),
                .init(value: 20, label: "20 kt", color: Self.headwindColor(20)),
                .init(value: 30, label: "30 kt", color: Self.headwindColor(30)),
            ]
        case .tailwind:
            [
                .init(value: 0, label: "Calm", color: Self.headwindColor(0)),
                .init(value: 10, label: "10 kt", color: Self.headwindColor(-10)),
                .init(value: 20, label: "20 kt", color: Self.headwindColor(-20)),
                .init(value: 30, label: "30 kt", color: Self.headwindColor(-30)),
            ]
        case .cape:
            [
                .init(value: 0, label: "0", color: Self.capeColor(0)),
                .init(value: 500, label: "500", color: Self.capeColor(500)),
                .init(value: 1000, label: "1000", color: Self.capeColor(1000)),
                .init(value: 2000, label: "2000+", color: Self.capeColor(2000)),
            ]
        case .nwpCeiling:
            [
                .init(value: 200, label: "LIFR", color: Self.ceilingColor(200)),
                .init(value: 800, label: "IFR", color: Self.ceilingColor(800)),
                .init(value: 2000, label: "MVFR", color: Self.ceilingColor(2000)),
                .init(value: 5000, label: "VFR", color: Self.ceilingColor(5000)),
            ]
        case .tempAtLevel:
            [
                .init(value: -20, label: "-20C", color: Self.temperatureColor(-20)),
                .init(value: -10, label: "-10C", color: Self.temperatureColor(-10)),
                .init(value: 0, label: "0C", color: Self.temperatureColor(0)),
                .init(value: 10, label: "10C", color: Self.temperatureColor(10)),
            ]
        case .modelAgreement:
            [
                .init(value: 0, label: "Good", color: Self.agreementColor("good")),
                .init(value: 1, label: "Moderate", color: Self.agreementColor("moderate")),
                .init(value: 2, label: "Poor", color: Self.agreementColor("poor")),
            ]
        case .icingRiskAtLevel, .catRiskAtLevel:
            [
                .init(value: 0, label: "None", color: Self.riskColor("none")),
                .init(value: 1, label: "Light", color: Self.riskColor("light")),
                .init(value: 2, label: "Moderate", color: Self.riskColor("moderate")),
                .init(value: 3, label: "Severe", color: Self.riskColor("severe")),
            ]
        case .sfipAtLevel:
            [
                .init(value: 0, label: "Low", color: .rgb(34, 197, 94)),
                .init(value: 35, label: "Med", color: .rgb(250, 204, 21)),
                .init(value: 65, label: "High", color: .rgb(249, 115, 22)),
                .init(value: 90, label: "Very High", color: .rgb(239, 68, 68)),
            ]
        }
    }

    func value(for point: RoutePointAnalysis, model: String, altitudeFt: Double) -> Double? {
        let sounding = point.sounding[model] ?? point.sounding.values.first
        switch self {
        case .cloudCoverTotal:
            guard let sounding else { return nil }
            return Self.randomOverlap(
                low: sounding.cloudCoverLowPct ?? 0,
                mid: sounding.cloudCoverMidPct ?? 0,
                high: sounding.cloudCoverHighPct ?? 0
            )
        case .cloudCoverLow:
            return sounding?.cloudCoverLowPct
        case .convectiveRisk:
            return Self.riskOrder(sounding?.convective?.riskLevel)
        case .headwind:
            guard let headwind = point.windComponents[model]?.headwindKt else { return nil }
            return max(0, headwind)
        case .tailwind:
            guard let headwind = point.windComponents[model]?.headwindKt else { return nil }
            return max(0, -headwind)
        case .cape:
            return sounding?.indices?.capeSurfaceJkg ?? sounding?.convective?.capeJkg
        case .nwpCeiling:
            return sounding?.nwpCloudDiagnostics?.ceilingFt
        case .tempAtLevel:
            guard let sounding else { return nil }
            return Self.temperatureAtAltitude(sounding: sounding, altitudeFt: altitudeFt)
        case .modelAgreement:
            let worst = point.modelDivergence
                .map { Self.agreementScore($0.agreement) }
                .max()
            return worst
        case .icingRiskAtLevel:
            return Self.worstRiskAtAlt(
                sounding?.icingZones ?? [],
                altitudeFt: altitudeFt,
                base: { $0.baseFt },
                top: { $0.topFt },
                risk: { $0.risk }
            )
        case .sfipAtLevel:
            return Self.sfipAtAlt(sounding?.sfipZones ?? [], altitudeFt: altitudeFt)
        case .catRiskAtLevel:
            return Self.worstRiskAtAlt(
                sounding?.verticalMotion?.catRiskLayers ?? [],
                altitudeFt: altitudeFt,
                base: { $0.baseFt },
                top: { $0.topFt },
                risk: { $0.risk }
            )
        case .cloudAtLevel:
            return Self.cloudAtAlt(sounding?.cloudLayers ?? [], altitudeFt: altitudeFt)
        }
    }

    func color(for value: Double) -> Color {
        switch self {
        case .cloudCoverTotal, .cloudCoverLow, .cloudAtLevel:
            return Self.cloudCoverColor(value)
        case .convectiveRisk:
            return Self.riskColor(Self.riskLabel(value, convective: true))
        case .headwind:
            return Self.headwindColor(value)
        case .tailwind:
            return Self.headwindColor(-value)
        case .cape:
            return Self.capeColor(value)
        case .nwpCeiling:
            return Self.ceilingColor(value)
        case .tempAtLevel:
            return Self.temperatureColor(value)
        case .modelAgreement:
            return value >= 2 ? Self.agreementColor("poor") : (value >= 1 ? Self.agreementColor("moderate") : Self.agreementColor("good"))
        case .icingRiskAtLevel, .catRiskAtLevel:
            return Self.riskColor(Self.riskLabel(value, convective: false))
        case .sfipAtLevel:
            if value <= 20 { return .rgb(34, 197, 94) }
            if value <= 50 { return .rgb(250, 204, 21) }
            if value <= 80 { return .rgb(249, 115, 22) }
            return .rgb(239, 68, 68)
        }
    }

    func width(for value: Double) -> CGFloat {
        switch self {
        case .cloudCoverTotal, .cloudCoverLow, .cloudAtLevel:
            return Self.linearWidth(value, maxValue: 100, min: 3, max: 25)
        case .convectiveRisk:
            return Self.linearWidth(value, maxValue: 4, min: 3, max: 25)
        case .headwind, .tailwind:
            return Self.linearWidth(value, maxValue: 30, min: 3, max: 25)
        case .cape:
            return Self.linearWidth(value, maxValue: 2000, min: 3, max: 25)
        case .nwpCeiling:
            let clamped = Swift.max(0, Swift.min(5000, value))
            return CGFloat(25 - (clamped / 5000) * 22)
        case .tempAtLevel:
            let clamped = Swift.max(-20, Swift.min(5, value))
            return CGFloat(3 + (5 - clamped) / 25 * 22)
        case .modelAgreement:
            return Self.linearWidth(value, maxValue: 2, min: 3, max: 25)
        case .icingRiskAtLevel, .catRiskAtLevel:
            return Self.linearWidth(value, maxValue: 3, min: 3, max: 25)
        case .sfipAtLevel:
            return Self.linearWidth(value, maxValue: 100, min: 3, max: 25)
        }
    }

    func format(_ value: Double) -> String {
        switch self {
        case .cloudCoverTotal, .cloudCoverLow, .cloudAtLevel:
            return "\(Int(value.rounded()))%"
        case .headwind:
            return "\(Int(value.rounded())) kt HW"
        case .tailwind:
            return "\(Int(value.rounded())) kt TW"
        case .cape:
            return "\(Int(value.rounded())) J/kg"
        case .nwpCeiling:
            return "\(Int(value.rounded())) ft"
        case .tempAtLevel:
            return String(format: "%.1f C", value)
        case .convectiveRisk:
            return Self.riskLabel(value, convective: true)
        case .modelAgreement:
            return value >= 2 ? "poor" : (value >= 1 ? "moderate" : "good")
        case .icingRiskAtLevel, .catRiskAtLevel:
            return Self.riskLabel(value, convective: false)
        case .sfipAtLevel:
            return "SFIP \(Int(value.rounded()))"
        }
    }

    private static func riskOrder(_ risk: String?) -> Double {
        switch risk?.lowercased() {
        case "marginal", "light", "low": 1
        case "moderate": 2
        case "high", "severe": 3
        case "extreme": 4
        default: 0
        }
    }

    private static func riskLabel(_ value: Double, convective: Bool) -> String {
        let rounded = Int(value.rounded())
        if convective {
            return ["none", "low", "moderate", "high", "extreme"][safe: min(rounded, 4)] ?? "none"
        }
        return ["none", "light", "moderate", "severe"][safe: min(rounded, 3)] ?? "none"
    }

    private static func agreementScore(_ agreement: String) -> Double {
        switch agreement.lowercased() {
        case "poor": 2
        case "moderate": 1
        default: 0
        }
    }

    private static func randomOverlap(low: Double, mid: Double, high: Double) -> Double {
        func clamp(_ value: Double) -> Double { Swift.min(100, Swift.max(0, value)) / 100 }
        let l = clamp(low)
        let m = clamp(mid)
        let h = clamp(high)
        return (1 - (1 - l) * (1 - m) * (1 - h)) * 100
    }

    private static func worstRiskAtAlt<Zone>(
        _ zones: [Zone],
        altitudeFt: Double,
        base: (Zone) -> Double,
        top: (Zone) -> Double,
        risk: (Zone) -> String
    ) -> Double {
        var worst = 0.0
        for zone in zones where altitudeFt >= base(zone) && altitudeFt <= top(zone) {
            worst = Swift.max(worst, riskOrder(risk(zone)))
        }
        return worst
    }

    private static func sfipAtAlt(_ zones: [SfipZone], altitudeFt: Double) -> Double? {
        var best: Double?
        for zone in zones where altitudeFt >= zone.baseFt && altitudeFt <= zone.topFt {
            guard let value = zone.meanSfip100 else { continue }
            best = best.map { Swift.max($0, value) } ?? value
        }
        return best
    }

    private static func cloudAtAlt(_ layers: [EnhancedCloudLayer], altitudeFt: Double) -> Double {
        var maxCoverage = 0.0
        for layer in layers where altitudeFt >= layer.baseFt && altitudeFt <= layer.topFt {
            let weight: Double
            switch layer.coverage.lowercased() {
            case "sct": weight = 0.3
            case "bkn": weight = 0.7
            case "ovc": weight = 1.0
            default: weight = 0.5
            }
            maxCoverage = Swift.max(maxCoverage, weight)
        }
        return maxCoverage * 100
    }

    private static func temperatureAtAltitude(sounding: SoundingAnalysis, altitudeFt: Double) -> Double? {
        guard let freezingLevel = sounding.indices?.freezingLevelFt else { return nil }
        var refs: [(altitude: Double, temperature: Double)] = [(freezingLevel, 0)]
        if let minus10 = sounding.indices?.minus10cLevelFt {
            refs.append((minus10, -10))
        }
        if let minus20 = sounding.indices?.minus20cLevelFt {
            refs.append((minus20, -20))
        }
        refs.sort { $0.altitude < $1.altitude }

        if altitudeFt <= refs[0].altitude {
            return refs[0].temperature + (refs[0].altitude - altitudeFt) * 2 / 1000
        }
        if altitudeFt >= refs[refs.count - 1].altitude {
            return refs[refs.count - 1].temperature - (altitudeFt - refs[refs.count - 1].altitude) * 2 / 1000
        }

        for index in 0..<(refs.count - 1) where altitudeFt >= refs[index].altitude && altitudeFt <= refs[index + 1].altitude {
            let lower = refs[index]
            let upper = refs[index + 1]
            let fraction = (altitudeFt - lower.altitude) / (upper.altitude - lower.altitude)
            return lower.temperature + fraction * (upper.temperature - lower.temperature)
        }
        return nil
    }

    private static func riskColor(_ risk: String) -> Color {
        switch risk.lowercased() {
        case "light", "low": .rgb(250, 204, 21)
        case "moderate": .rgb(249, 115, 22)
        case "high", "severe": .rgb(239, 68, 68)
        case "extreme": .rgb(153, 27, 27)
        case "marginal": .rgb(163, 163, 163)
        default: .rgb(34, 197, 94)
        }
    }

    private static func cloudCoverColor(_ percent: Double) -> Color {
        let t = Swift.min(1, Swift.max(0, percent / 100))
        let v = Int((220 - 160 * t).rounded())
        return .rgb(v, v, Swift.min(255, v + 10))
    }

    private static func headwindColor(_ kt: Double) -> Color {
        let t = Swift.min(1, Swift.max(0, abs(kt) / 30))
        if kt >= 0 {
            return .rgb(255, Int((255 - 200 * t).rounded()), Int((255 - 200 * t).rounded()))
        }
        return .rgb(Int((255 - 200 * t).rounded()), Int((255 - 60 * t).rounded()), Int((255 - 200 * t).rounded()))
    }

    private static func capeColor(_ jkg: Double) -> Color {
        if jkg <= 0 { return .rgb(34, 197, 94) }
        if jkg >= 2000 { return .rgb(220, 38, 38) }
        let t = Swift.min(1, jkg / 2000)
        if t < 0.5 {
            let s = t * 2
            return .rgb(
                Int((34 + 221 * s).rounded()),
                Int((197 - 10 * s).rounded()),
                Int((94 - 73 * s).rounded())
            )
        }
        let s = (t - 0.5) * 2
        return .rgb(
            Int((255 - 35 * s).rounded()),
            Int((187 - 149 * s).rounded()),
            Int((21 + 17 * s).rounded())
        )
    }

    private static func ceilingColor(_ feet: Double) -> Color {
        if feet < 200 { return .rgb(142, 36, 170) }
        if feet < 1000 { return .rgb(220, 53, 69) }
        if feet < 3000 { return .rgb(245, 158, 11) }
        return .rgb(34, 197, 94)
    }

    private static func temperatureColor(_ celsius: Double) -> Color {
        let t = Swift.min(1, Swift.max(0, (celsius + 20) / 60))
        if t < 0.33 {
            let s = t / 0.33
            return .rgb(Int((30 + 125 * s).rounded()), Int((80 + 125 * s).rounded()), Int((220 + 35 * s).rounded()))
        }
        if t < 0.67 {
            let s = (t - 0.33) / 0.34
            return .rgb(Int((155 + 100 * s).rounded()), Int((205 + 50 * s).rounded()), Int((255 - 40 * s).rounded()))
        }
        let s = (t - 0.67) / 0.33
        return .rgb(255, Int((255 - 185 * s).rounded()), Int((215 - 175 * s).rounded()))
    }

    private static func agreementColor(_ agreement: String) -> Color {
        switch agreement.lowercased() {
        case "poor": .rgb(239, 68, 68)
        case "moderate": .rgb(249, 115, 22)
        default: .rgb(34, 197, 94)
        }
    }

    private static func linearWidth(_ value: Double, maxValue: Double, min minWidth: CGFloat, max maxWidth: CGFloat) -> CGFloat {
        let t = Swift.min(1, Swift.max(0, abs(value) / maxValue))
        return minWidth + (maxWidth - minWidth) * CGFloat(t)
    }
}

/// View model for the route map — extracts waypoint coordinates and computes map region.
@Observable
@MainActor
final class RouteMapViewModel {
    struct WaypointAnnotation: Identifiable {
        let id: String // ICAO
        let name: String
        let coordinate: CLLocationCoordinate2D
    }

    struct RouteSegment: Identifiable {
        let id: Int
        let coordinates: [CLLocationCoordinate2D]
        let color: Color
        let width: CGFloat
        let midpoint: CLLocationCoordinate2D
        let targetPoint: RoutePointAnalysis
        let valueDescription: String?
    }

    var waypoints: [WaypointAnnotation] = []
    var routeCoordinates: [CLLocationCoordinate2D] = []
    var routePoints: [RoutePointAnalysis] = []
    var mapRegion: MKCoordinateRegion = .init()

    func update(from snapshot: SnapshotResponse) {
        let wps = snapshot.route.waypoints
        waypoints = wps.map {
            WaypointAnnotation(id: $0.icao, name: $0.name, coordinate: CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon))
        }
        routeCoordinates = wps.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }
        routePoints = []

        // Compute region to fit all waypoints with padding
        guard !routeCoordinates.isEmpty else { return }
        let lats = routeCoordinates.map(\.latitude)
        let lons = routeCoordinates.map(\.longitude)
        let center = CLLocationCoordinate2D(
            latitude: (lats.min()! + lats.max()!) / 2,
            longitude: (lons.min()! + lons.max()!) / 2
        )
        let span = MKCoordinateSpan(
            latitudeDelta: (lats.max()! - lats.min()!) * 1.4 + 0.5,
            longitudeDelta: (lons.max()! - lons.min()!) * 1.4 + 0.5
        )
        mapRegion = MKCoordinateRegion(center: center, span: span)
    }

    func update(from analyses: RouteAnalysesResponse) {
        routePoints = analyses.analyses
        // Use route analysis points for finer route line
        routeCoordinates = analyses.analyses.map {
            CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)
        }

        // Waypoint markers from analysis waypoint_icao
        let waypointAnalyses = analyses.analyses.filter { $0.waypointIcao != nil }
        waypoints = waypointAnalyses.map {
            WaypointAnnotation(
                id: $0.waypointIcao!,
                name: $0.waypointName ?? $0.waypointIcao!,
                coordinate: CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)
            )
        }

        guard !routeCoordinates.isEmpty else { return }
        let lats = routeCoordinates.map(\.latitude)
        let lons = routeCoordinates.map(\.longitude)
        let center = CLLocationCoordinate2D(
            latitude: (lats.min()! + lats.max()!) / 2,
            longitude: (lons.min()! + lons.max()!) / 2
        )
        let span = MKCoordinateSpan(
            latitudeDelta: (lats.max()! - lats.min()!) * 1.4 + 0.5,
            longitudeDelta: (lons.max()! - lons.min()!) * 1.4 + 0.5
        )
        mapRegion = MKCoordinateRegion(center: center, span: span)
    }

    func segments(
        colorMetric: RouteMapMetric,
        widthMetric: RouteMapMetric,
        model: String,
        altitudeFt: Double
    ) -> [RouteSegment] {
        guard routePoints.count > 1 else { return [] }

        return (0..<(routePoints.count - 1)).map { index in
            let start = routePoints[index]
            let end = routePoints[index + 1]
            let colorValue = averagedValue(
                metric: colorMetric,
                start: start,
                end: end,
                model: model,
                altitudeFt: altitudeFt
            )
            let widthValue = averagedValue(
                metric: widthMetric,
                start: start,
                end: end,
                model: model,
                altitudeFt: altitudeFt
            )
            let startCoord = CLLocationCoordinate2D(latitude: start.lat, longitude: start.lon)
            let endCoord = CLLocationCoordinate2D(latitude: end.lat, longitude: end.lon)
            return RouteSegment(
                id: index,
                coordinates: [startCoord, endCoord],
                color: colorValue.map { colorMetric.color(for: $0) } ?? .blue,
                width: widthValue.map { widthMetric.width(for: $0) } ?? 8,
                midpoint: CLLocationCoordinate2D(
                    latitude: (start.lat + end.lat) / 2,
                    longitude: (start.lon + end.lon) / 2
                ),
                targetPoint: end,
                valueDescription: colorValue.map { colorMetric.format($0) }
            )
        }
    }

    private func averagedValue(
        metric: RouteMapMetric,
        start: RoutePointAnalysis,
        end: RoutePointAnalysis,
        model: String,
        altitudeFt: Double
    ) -> Double? {
        let startValue = metric.value(for: start, model: model, altitudeFt: altitudeFt)
        let endValue = metric.value(for: end, model: model, altitudeFt: altitudeFt)
        if let startValue, let endValue {
            return (startValue + endValue) / 2
        }
        return startValue ?? endValue
    }
}

private extension Array {
    subscript(safe index: Index) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

private extension Color {
    static func rgb(_ red: Int, _ green: Int, _ blue: Int) -> Color {
        Color(
            red: Double(red) / 255,
            green: Double(green) / 255,
            blue: Double(blue) / 255
        )
    }
}
