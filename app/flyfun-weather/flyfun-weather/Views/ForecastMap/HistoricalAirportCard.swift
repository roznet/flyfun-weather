import SwiftUI
import UIKit

/// The historical map's tapped-airport card (#629), the port of the web side
/// panel: METAR, TAF and the three models side by side at the selected instant,
/// each cell coloured by the served catalog, then per-source provenance and the
/// raw METAR/TAF. Zero network: it reads the payload the map already holds.
///
/// Oriented like `ForecastAirportCard` (metric rows, source columns), and a row
/// tap recolours the map by that metric, as there.
struct HistoricalAirportCard: View {
    let viewModel: HistoricalMapViewModel
    let airport: HistoricalAirport
    let catalog: ForecastMapCatalog

    private static let columns: [HistoricalSource] = [.metar, .taf, .gfs, .icon, .ecmwf]
    private static let columnWidth: CGFloat = 52

    private struct MatrixRow { let metric: String; let label: String; let unit: String? }
    /// The web panel's columns: what a pilot compares observation vs forecast on.
    private static let matrixRows: [MatrixRow] = [
        .init(metric: "flight_category", label: "Category", unit: nil),
        .init(metric: "ceiling_ft", label: "Ceiling", unit: "ft"),
        .init(metric: "visibility_m", label: "Visibility", unit: nil),
        .init(metric: "wind_speed_kt", label: "Wind", unit: "kt"),
        .init(metric: "crosswind_kt", label: "Xwind", unit: "kt"),
        .init(metric: "alternate_needed", label: "Alt req", unit: nil),
    ]

    /// Every source keyed by its raw value, so each cell colours via `.model(source)`.
    private var combined: ForecastAirport { airport.allSourcesAirport }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                header
                matrix
                sourceNotes
                rawReports
            }
            .padding(Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .center, spacing: Theme.spacingM) {
            categoryBadge
            VStack(alignment: .leading, spacing: 2) {
                Text(airport.icao).font(.title3.bold()).foregroundStyle(Theme.text)
                Text(instantLabel).font(.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
            }
            Spacer()
            headerStepper
        }
    }

    /// The active source's category (the map's colour for this airport).
    private var categoryBadge: some View {
        let source = viewModel.source
        let cell: (any ForecastCellData)?
        if source.isConsensus {
            cell = airport.consensus(for: source)
        } else {
            cell = airport.entry(for: source)
        }
        let bg = catalog.color(metric: "flight_category", cell: cell, airport: combined, mode: source.mapMode)
        return VStack(spacing: 2) {
            Text(cell?.categoryField("flight_category") ?? "—")
                .font(.headline.weight(.bold))
                .foregroundStyle(Color(uiColor: bg.readableText))
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Color(uiColor: bg), in: RoundedRectangle(cornerRadius: 10))
            Text(source.label).font(.caption2).foregroundStyle(Theme.textMuted)
        }
    }

    private var headerStepper: some View {
        HStack(spacing: 6) {
            Button { viewModel.step(-1) } label: { Image(systemName: "chevron.left") }
                .disabled(!viewModel.canStepBack)
            Text(viewModel.selectedInstant.map { HistoricalTime.timeLabel($0) } ?? "")
                .font(.subheadline.weight(.medium)).monospacedDigit()
            Button { viewModel.step(1) } label: { Image(systemName: "chevron.right") }
                .disabled(!viewModel.canStepForward)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.thinMaterial, in: Capsule())
    }

    private var instantLabel: String {
        guard let payload = viewModel.payload, let at = HistoricalTime.parse(payload.at) else { return "" }
        let lead = payload.leadDays == 0 ? "latest runs" : "runs from D-\(payload.leadDays)"
        return "\(HistoricalTime.dateLabel(at)) \(HistoricalTime.timeLabel(at)) · \(lead)"
    }

    // MARK: - Metric × source matrix

    private var matrix: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Text("").frame(maxWidth: .infinity, alignment: .leading)
                ForEach(Self.columns, id: \.self) { s in
                    Text(s.label)
                        .font(.caption2.weight(s == viewModel.source ? .bold : .semibold))
                        .foregroundStyle(s == viewModel.source ? Theme.primary : Theme.textMuted)
                        .frame(width: Self.columnWidth)
                }
            }
            .padding(.vertical, 4)

            ForEach(Self.matrixRows, id: \.metric) { row in
                metricRow(row)
            }
        }
    }

    private func metricRow(_ row: MatrixRow) -> some View {
        let isActive = viewModel.metric == row.metric
        return Button {
            viewModel.metric = row.metric  // row-tap switches the map's colour metric
        } label: {
            HStack(spacing: 0) {
                HStack(spacing: 4) {
                    if isActive { Image(systemName: "map.fill").font(.system(size: 9)).foregroundStyle(Theme.primary) }
                    Text(rowLabel(row)).font(.caption).foregroundStyle(Theme.text)
                        .lineLimit(2).minimumScaleFactor(0.8)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                ForEach(Self.columns, id: \.self) { s in
                    cell(metric: row.metric, source: s)
                }
            }
            .padding(.vertical, 3)
            .background(isActive ? Theme.primary.opacity(0.08) : .clear)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Colour the map by \(row.label.lowercased())")
    }

    private func cell(metric: String, source: HistoricalSource) -> some View {
        let entry = airport.entry(for: source)
        let bg = entry == nil ? UIColor.clear
            : catalog.color(metric: metric, cell: entry, airport: combined, mode: source.mapMode)
        return Text(Self.cellText(entry, metric: metric))
            .font(.caption2).monospacedDigit()
            .lineLimit(1).minimumScaleFactor(0.7)
            .foregroundStyle(entry == nil ? Theme.textMuted : Color(uiColor: bg.readableText))
            .frame(width: Self.columnWidth, height: 22)
            .background(Color(uiColor: bg))
    }

    static func cellText(_ entry: ForecastModelEntry?, metric: String) -> String {
        guard let entry else { return "—" }
        if metric == "alternate_needed" { return ForecastAirportCard.altLabel(entry.altRequired) }
        return ForecastAirportCard.compactCell(entry, metric: metric)
    }

    private func rowLabel(_ row: MatrixRow) -> String {
        var label = row.label
        if let unit = row.unit { label += " (\(unit))" }
        if row.metric == "crosswind_kt",
           let rwy = Self.columns.compactMap({ airport.entry(for: $0)?.bestRunwayId }).first {
            label += " · RWY \(rwy)"
        }
        return label
    }

    // MARK: - Provenance

    private var sourceNotes: some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(Self.columns, id: \.self) { s in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(s.label).font(.caption2.weight(.semibold)).foregroundStyle(Theme.textMuted)
                        .frame(width: 48, alignment: .leading)
                    Text(note(for: s)).font(.caption2).foregroundStyle(Theme.textMuted)
                }
            }
        }
    }

    private func note(for source: HistoricalSource) -> String {
        switch source {
        case .metar:
            guard let metar = airport.metar else {
                let age = viewModel.payload?.sources["metar"]?.maxAgeMin ?? 90
                return "No report in the \(age) min before"
            }
            var text = HistoricalTime.timeLabel(metar.observationTime)
            if metar.reportType == "SPECI" { text += " SPECI" }
            if let age = metar.ageMin { text += " · \(age) min old" }
            return text
        case .taf:
            guard let taf = airport.taf else { return "No TAF valid at this time" }
            if let type = taf.temporaryType {
                return "\(type) \(taf.temporaryCategory ?? "") over prevailing \(taf.prevailingCategory ?? "—")"
            }
            return "Prevailing conditions"
        case .worst, .majority:
            return ""
        case .gfs, .icon, .ecmwf:
            if let run = airport.modelRuns[source.rawValue], airport.models[source.rawValue] != nil {
                return "Run \(HistoricalTime.runLabel(run.modelInitTime)) → valid \(HistoricalTime.timeLabel(run.validTime))"
            }
            if viewModel.payload?.sources[source.rawValue]?.available == true {
                return "No data for this airport"
            }
            return viewModel.unavailableText(source)
        }
    }

    // MARK: - Raw reports

    @ViewBuilder private var rawReports: some View {
        if let raw = airport.metar?.raw {
            rawBlock(title: "METAR", text: raw)
        }
        if let taf = airport.taf, let raw = taf.raw {
            let issued = taf.issueTime.map { " (issued \(HistoricalTime.runLabel($0)))" } ?? ""
            rawBlock(title: "TAF" + issued, text: raw)
        }
    }

    private func rawBlock(title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption2.weight(.semibold)).foregroundStyle(Theme.textMuted)
            Text(text)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

private extension HistoricalAirport {
    /// The consensus block a consensus source reads.
    func consensus(for source: HistoricalSource) -> ForecastConsensus? {
        source == .majority ? consensusMajority : consensus
    }
}
