import SwiftUI
import UIKit

/// The tapped-airport card (#420, PR2). A pure function of data the map already
/// holds — zero network. Mirrors the web `airport-summary-card.ts`: verdict
/// header, alternate-required row, a metric × model matrix, and an init-times
/// footnote. Tapping a metric row switches the map's colour metric (already the
/// shipped web behaviour), and the header carries a duplicate hour stepper so you
/// can step time and watch the map *and* the card update together.
struct ForecastAirportCard: View {
    let viewModel: ForecastMapViewModel
    let airport: ForecastAirport
    let catalog: ForecastMapCatalog

    /// The card's consensus column mirrors the map mode, falling back to worst
    /// when an individual model is active.
    private var consensusMode: ForecastModelMode { viewModel.mode.consensusMode }
    private var consensus: ForecastConsensus { airport.consensus(mode: consensusMode) }
    private var modeLabel: String { consensusMode == .majority ? "Majority" : "Worst" }

    /// Model columns: fixed order, present models only.
    private var presentModels: [String] {
        ["gfs", "icon", "ecmwf"].filter { airport.models[$0] != nil }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                verdictHeader
                alternateRow
                matrix
                footer
            }
            .padding(Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    // MARK: - Verdict header

    private var verdictHeader: some View {
        HStack(alignment: .center, spacing: Theme.spacingM) {
            categoryBadge
            VStack(alignment: .leading, spacing: 2) {
                Text(airport.icao).font(.title3.bold()).foregroundStyle(Theme.text)
                Text(validTimeLabel).font(.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                if let chip = agreementChip { agreementChipView(chip) }
            }
            Spacer()
            headerHourStepper
        }
    }

    private var categoryBadge: some View {
        let bg = catalog.color(metric: "flight_category", airport: airport, mode: consensusMode)
        return Text(consensus.flightCategory ?? "—")
            .font(.headline.weight(.bold))
            .foregroundStyle(Color(uiColor: bg.readableText))
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(Color(uiColor: bg), in: RoundedRectangle(cornerRadius: 10))
    }

    private var headerHourStepper: some View {
        HStack(spacing: 6) {
            Button { viewModel.stepHour(-1) } label: { Image(systemName: "chevron.left") }
                .disabled(!viewModel.canStepHourBack)
            Text(String(format: "%02dZ", viewModel.selectedHour))
                .font(.subheadline.weight(.medium)).monospacedDigit()
            Button { viewModel.stepHour(1) } label: { Image(systemName: "chevron.right") }
                .disabled(!viewModel.canStepHourForward)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.thinMaterial, in: Capsule())
    }

    private struct AgreementChip { let text: String; let color: Color }

    private var agreementChip: AgreementChip? {
        guard let bucket = consensus.agreement(forMetric: "flight_category") else { return nil }
        let text: String
        switch bucket {
        case "divergent": text = "Models split"
        case "mixed": text = "Some disagreement"
        case "consistent": text = "Models agree"
        default: return nil
        }
        return AgreementChip(text: text, color: Color(uiColor: catalog.agreementColor(bucket)))
    }

    private func agreementChipView(_ chip: AgreementChip) -> some View {
        HStack(spacing: 4) {
            Circle().fill(chip.color).frame(width: 7, height: 7)
            Text(chip.text).font(.caption2).foregroundStyle(Theme.textMuted)
        }
    }

    // MARK: - Alternate-required row

    private var alternateRow: some View {
        let alt = airport.aggregatedAltRequired(mode: consensusMode)
        let required = (alt?.faa ?? false) || (alt?.easa ?? false)
        return HStack {
            Text("Alternate required").font(.subheadline).foregroundStyle(Theme.textMuted)
            Spacer()
            Text(Self.altLabel(alt))
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(required ? Theme.red : Theme.green)
            Text(modeLabel)
                .font(.caption2).foregroundStyle(Theme.textMuted)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(Theme.surface, in: Capsule())
        }
        .padding(.vertical, 6)
        .overlay(Divider(), alignment: .bottom)
    }

    // MARK: - Metric × model matrix

    private struct MatrixRow { let metric: String; let label: String; let unit: String? }
    private static let matrixRows: [MatrixRow] = [
        .init(metric: "flight_category", label: "Category", unit: nil),
        .init(metric: "ceiling_ft", label: "Ceiling", unit: "ft"),
        .init(metric: "visibility_m", label: "Visibility", unit: nil),
        .init(metric: "wind_speed_kt", label: "Wind", unit: "kt"),
        .init(metric: "crosswind_kt", label: "Xwind", unit: "kt"),
        .init(metric: "headwind_kt", label: "Headwind", unit: "kt"),
        .init(metric: "convective_risk", label: "Convective", unit: nil),
        .init(metric: "cape_jkg", label: "CAPE", unit: "J/kg"),
        .init(metric: "cloud_cover_pct", label: "Cloud cover", unit: nil),
    ]

    private var matrix: some View {
        let cols = presentModels
        return VStack(spacing: 0) {
            // Header row.
            HStack(spacing: 0) {
                Text("").frame(maxWidth: .infinity, alignment: .leading)
                ForEach(cols, id: \.self) { m in
                    Text(m.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(Theme.textMuted)
                        .frame(width: 56)
                }
                Text(modeLabel).font(.caption2.weight(.semibold)).foregroundStyle(Theme.textMuted)
                    .frame(width: 56)
            }
            .padding(.vertical, 4)

            ForEach(Self.matrixRows, id: \.metric) { row in
                metricRow(row, cols: cols)
            }
            tempRow(cols: cols)
        }
    }

    private func metricRow(_ row: MatrixRow, cols: [String]) -> some View {
        let diverging = consensus.agreement(forMetric: row.metric) == "divergent"
        let isActive = viewModel.metric == row.metric
        return Button {
            viewModel.metric = row.metric  // row-tap switches the map's colour metric
        } label: {
            HStack(spacing: 0) {
                HStack(spacing: 4) {
                    if isActive { Image(systemName: "map.fill").font(.system(size: 9)).foregroundStyle(Theme.primary) }
                    if diverging { Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 9)).foregroundStyle(Theme.amber) }
                    Text(rowLabel(row)).font(.caption).foregroundStyle(Theme.text)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                ForEach(cols, id: \.self) { m in
                    cell(metric: row.metric, data: airport.models[m].map { $0 as any ForecastCellData }, mode: .model(m))
                }
                cell(metric: row.metric, data: consensus, mode: consensusMode)
            }
            .padding(.vertical, 3)
            .background(isActive ? Theme.primary.opacity(0.08) : .clear)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Colour the map by \(row.label.lowercased())")
    }

    /// The bottom Temp row — plain (uncoloured) per-model, consensus "—". Not a
    /// colour metric, so it isn't tappable.
    private func tempRow(cols: [String]) -> some View {
        let anyTemp = cols.contains { airport.models[$0]?.temperatureC != nil }
        return Group {
            if anyTemp {
                HStack(spacing: 0) {
                    Text("Temp").font(.caption).foregroundStyle(Theme.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    ForEach(cols, id: \.self) { m in
                        Text(airport.models[m]?.temperatureC.map { "\(Int($0.rounded()))°" } ?? "—")
                            .font(.caption2).monospacedDigit().foregroundStyle(Theme.text)
                            .frame(width: 56)
                    }
                    Text("—").font(.caption2).foregroundStyle(Theme.textMuted).frame(width: 56)
                }
                .padding(.vertical, 3)
            }
        }
    }

    private func cell(metric: String, data: (any ForecastCellData)?, mode: ForecastModelMode) -> some View {
        let bg = catalog.color(metric: metric, cell: data, airport: airport, mode: mode)
        return Text(Self.compactCell(data, metric: metric))
            .font(.caption2).monospacedDigit()
            .foregroundStyle(Color(uiColor: bg.readableText))
            .frame(width: 56, height: 22)
            .background(Color(uiColor: bg))
    }

    private func rowLabel(_ row: MatrixRow) -> String {
        var label = row.label
        if let unit = row.unit { label += " (\(unit))" }
        // Name the best runway on the wind-component rows.
        if row.metric == "crosswind_kt" || row.metric == "headwind_kt",
           let rwy = presentModels.compactMap({ airport.models[$0]?.bestRunwayId }).first {
            label += " · RWY \(rwy)"
        }
        return label
    }

    // MARK: - Footer

    private var footer: some View {
        Text(initTimesLabel)
            .font(.caption2).foregroundStyle(Theme.textMuted)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var validTimeLabel: String {
        guard let iso = viewModel.payload?.forecastTime,
              let date = Self.isoFormatter.date(from: iso) else { return "" }
        return Self.validTimeFormatter.string(from: date)
    }

    private var initTimesLabel: String {
        guard let inits = viewModel.payload?.modelInitTimes else { return "" }
        let parts = presentModels.compactMap { m -> String? in
            guard let iso = inits[m], let d = Self.isoFormatter.date(from: iso) else { return nil }
            return "\(m.uppercased()) \(Self.hourFormatter.string(from: d))Z"
        }
        return parts.isEmpty ? "" : "Init: " + parts.joined(separator: " · ")
    }

    // MARK: - Formatting

    static func altLabel(_ f: AltRequired?) -> String {
        guard let f else { return "—" }
        if f.faa && f.easa { return "FAA + EASA" }
        if f.easa { return "EASA" }
        if f.faa { return "FAA" }
        return "None"
    }

    /// Narrow matrix-cell value (units live in the row label). Port of `compactCell`.
    static func compactCell(_ data: (any ForecastCellData)?, metric: String) -> String {
        guard let data else { return "—" }
        switch metric {
        case "flight_category":
            return data.categoryField("flight_category") ?? "—"
        case "convective_risk":
            return data.categoryField("convective_risk") ?? "none"
        case "ceiling_ft":
            guard let v = data.numericField("ceiling_ft") else { return "—" }
            return v >= 10000 ? "CAVOK" : "\(Int(v.rounded()))"
        case "visibility_m":
            // Matches web `formatVisibility` (metric/EU): >10 km / N km / N m. A
            // US statute-mile variant awaits the shared units-region pref (no
            // UnitsRegion is plumbed into iOS yet — see project_unit_system).
            guard let m = data.numericField("visibility_m") else { return "—" }
            if m >= 10000 { return ">10 km" }
            return m >= 5000 ? "\(Int((m / 1000).rounded())) km" : "\(Int(m.rounded())) m"
        case "wind_speed_kt":
            guard let s = data.numericField("wind_speed_kt") else { return "—" }
            let dir = data.numericField("wind_dir_deg").map { "\(Int($0.rounded()))/" } ?? ""
            // Gust as `Ggust` (web `dir/speedGgust`); consensus has no gust → omitted.
            // Web uses a truthy check on this field, so a literal 0 gust is omitted.
            let gust = data.numericField("wind_gust_kt").flatMap { $0 != 0 ? "G\(Int($0.rounded()))" : nil } ?? ""
            return "\(dir)\(Int(s.rounded()))\(gust)"
        case "crosswind_kt":
            guard let v = data.numericField("crosswind_kt") else { return "—" }
            let gust = data.numericField("gust_crosswind_kt").map { " (\(Int($0.rounded())))" } ?? ""
            return "\(Int(v.rounded()))\(gust)"
        case "headwind_kt":
            guard let v = data.numericField("headwind_kt") else { return "—" }
            let gust = data.numericField("gust_headwind_kt").map { " (\(Int($0.rounded())))" } ?? ""
            return "\(Int(v.rounded()))\(gust)"
        case "cape_jkg":
            guard let v = data.numericField("cape_jkg") else { return "—" }
            return "\(Int(v.rounded()))"
        case "cloud_cover_pct":
            guard let v = data.numericField("cloud_cover_pct") else { return "—" }
            return "\(Int(v.rounded()))%"
        default:
            return "—"
        }
    }

    private static let isoFormatter = ISO8601DateFormatter()
    private static let validTimeFormatter: DateFormatter = {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "UTC")
        f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "EEE HH:mm'Z'"; return f
    }()
    private static let hourFormatter: DateFormatter = {
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "UTC")
        f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "HH"; return f
    }()
}

// MARK: - Readable text on a coloured cell (textOn)

extension UIColor {
    /// Black or white, whichever reads on this background (luminance > 0.6 → dark).
    var readableText: UIColor {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        getRed(&r, green: &g, blue: &b, alpha: &a)
        let luminance = 0.299 * r + 0.587 * g + 0.114 * b
        return luminance > 0.6 ? UIColor(white: 0.07, alpha: 1) : .white
    }
}
