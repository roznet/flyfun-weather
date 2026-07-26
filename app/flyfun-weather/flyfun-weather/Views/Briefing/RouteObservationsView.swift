import SwiftUI

/// **Current Observations** (#492) — D-0 METAR/TAF along the route reconciled
/// against the model, sitting between Conditions and Alternates. Mirrors the
/// web's `renderRouteObservations` (`web/ts/managers/briefing-ui.ts`).
///
/// The web table is 11 columns: ICAO/Dist/ETA plus a 4-column **Condition**
/// group and a 4-column **Wind** group. That is too wide for a phone, so the
/// two groups become a switchable axis in compact width and render side-by-side
/// in regular width — the same size-class split `RouteMapView` uses for its
/// dual-metric layout.
///
/// Rendered only when `route_observations` is present, which the pipeline
/// populates on D-0 only.
///
/// SYNC: `web/ts/managers/briefing-ui.ts::renderRouteObservations`.
struct RouteObservationsView: View {
    let viewModel: BriefingViewModel

    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var axis: ObservationAxis = .condition
    @State private var detail: AirportObservation?
    @State private var showsAllAirports = false

    /// How many airports a phone shows before "Show all".
    private static let compactRowCap = 10

    /// iPad-class width. Drives two decisions that are independent but happen to
    /// share the same trigger: rendering both comparison groups side-by-side, and
    /// listing every reporting airport instead of the nearest few.
    private var isRegularWidth: Bool { sizeClass == .regular }

    /// iPad-class width shows both comparison groups at once, so the axis
    /// switcher would have nothing to do and is hidden.
    private var showsBothAxes: Bool { isRegularWidth }

    private var observations: RouteObservations? {
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot.routeObservations }
        return nil
    }

    var body: some View {
        if let obs = observations, !obs.reportingAirports.isEmpty {
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                header(obs)
                summary(obs)
                if obs.hasConflicts == true {
                    conflictBanner
                }
                if !showsBothAxes {
                    axisPicker
                }
                table(obs)
            }
            .padding(.horizontal, Theme.cardPadding)
            .accessibilityIdentifier("observationsSection")
            .sheet(item: $detail) { apt in
                ObservationDetailSheet(
                    airport: apt,
                    comparison: obs.comparisonsByIcao[apt.icao]
                )
            }
        }
    }

    // MARK: Header + summary

    @ViewBuilder
    private func header(_ obs: RouteObservations) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Text("Current Observations")
                .font(.headline)
                .foregroundStyle(Theme.text)
            Spacer(minLength: 0)
            if let fetched = obs.fetchTime, let label = Self.zuluTime(fetched) {
                Text("Fetched \(label)")
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }

    /// Coverage line: how many airports reported, the corridor width, the worst
    /// observed category, and any phenomena seen along the route.
    @ViewBuilder
    private func summary(_ obs: RouteObservations) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            HStack(spacing: Theme.spacingS) {
                Text(coverageText(obs))
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                if let worst = obs.worstMetarCategory {
                    Text("Worst")
                        .font(.caption2)
                        .foregroundStyle(Theme.textMuted)
                    FlightCategoryBadge(category: worst)
                }
                Spacer(minLength: 0)
            }
            if let phenomena = obs.phenomenaAlongRoute, !phenomena.isEmpty {
                Label(phenomena.joined(separator: ", "), systemImage: "cloud.bolt.rain")
                    .font(.caption)
                    .foregroundStyle(Theme.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func coverageText(_ obs: RouteObservations) -> String {
        var parts: [String] = []
        if let m = obs.airportsWithMetar { parts.append("\(m) METAR") }
        if let t = obs.airportsWithTaf { parts.append("\(t) TAF") }
        if let c = obs.corridorNm { parts.append("\(Int(c.rounded()))nm corridor") }
        return parts.joined(separator: " · ")
    }

    private var conflictBanner: some View {
        Label(
            "Observations conflict with the model at one or more airports — the model may be misreading current conditions.",
            systemImage: "exclamationmark.triangle.fill"
        )
        .font(.caption)
        .foregroundStyle(Theme.amber)
        .fixedSize(horizontal: false, vertical: true)
        .padding(Theme.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.amber.opacity(Theme.tableRowHighlightOpacity), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: Axis switcher (compact width only)

    private var axisPicker: some View {
        Picker("Compare", selection: $axis) {
            ForEach(ObservationAxis.allCases) { a in
                Text(a.label).tag(a)
            }
        }
        .pickerStyle(.segmented)
        .frame(maxWidth: 260)
        .accessibilityIdentifier("observationsAxisPicker")
    }

    // MARK: Table

    /// Horizontally scrollable so a large Dynamic Type size degrades into a pan
    /// rather than truncating badges. `.basedOnSize` suppresses the bounce when
    /// the row already fits, which is the common case.
    @ViewBuilder
    private func table(_ obs: RouteObservations) -> some View {
        // Build the ICAO lookup once per render. `comparisonsByIcao` is a computed
        // property, so reading it inside the row `ForEach` rebuilt the whole
        // dictionary for every row — 29 builds per pass on a real corridor.
        let comparisons = obs.comparisonsByIcao
        // Same reason: `displayedAirports` re-runs the nearest-N sort (and rebuilds
        // the comparison lookup for the conflict pin) on every call, and both the
        // rows and the "Show all" toggle need it.
        let displayed = displayedAirports(obs)
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            ScrollView(.horizontal) {
                // `horizontalSpacing: 0` with per-cell padding instead of grid
                // spacing: a conflicting row is highlighted by shading its cells,
                // and Grid applies a row background per cell — with spacing > 0
                // that tiles into visible gaps rather than one continuous band.
                Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: Theme.spacingS) {
                    if showsBothAxes {
                        GridRow {
                            Color.clear.frame(height: 0).gridCellColumns(3)
                            groupHeader(ObservationAxis.condition.label).gridCellColumns(4)
                            groupHeader(ObservationAxis.wind.label).gridCellColumns(4)
                        }
                    }
                    GridRow {
                        columnHeader("Airport")
                        columnHeader("Dist")
                        columnHeader("ETA")
                        ForEach(visibleAxes) { _ in
                            columnHeader("METAR")
                            columnHeader("TAF")
                            columnHeader("Model")
                            // Agreement column — the icon is self-explanatory and
                            // a header would only widen the row.
                            Color.clear.frame(width: 1, height: 0)
                        }
                    }
                    Divider().gridCellUnsizedAxes(.horizontal).gridCellColumns(gridColumnCount)
                    ForEach(displayed) { apt in
                        row(apt, comparison: comparisons[apt.icao])
                    }
                }
                .padding(.vertical, Theme.spacingXS)
            }
            .scrollBounceBehavior(.basedOnSize, axes: .horizontal)

            showAllToggle(obs, shown: displayed.count)
            legend
        }
    }

    /// Rows to render: every reporting airport on iPad or once expanded, else the
    /// nearest few (see `nearestReportingAirports`).
    private func displayedAirports(_ obs: RouteObservations) -> [AirportObservation] {
        guard !isRegularWidth, !showsAllAirports else { return obs.reportingAirports }
        return obs.nearestReportingAirports(limit: Self.compactRowCap)
    }

    /// Expand/collapse control, shown only when the cap is actually hiding rows.
    /// Takes `shown` from the caller rather than recomputing `displayedAirports`,
    /// which would re-run the nearest-N sort a second time per render.
    @ViewBuilder
    private func showAllToggle(_ obs: RouteObservations, shown: Int) -> some View {
        let total = obs.reportingAirports.count
        if !isRegularWidth, total > shown || showsAllAirports {
            Button {
                withAnimation { showsAllAirports.toggle() }
            } label: {
                HStack(spacing: Theme.spacingXS) {
                    Image(systemName: showsAllAirports ? "chevron.up" : "chevron.down")
                        .font(.caption2)
                    // "Show fewer" rather than "Show nearest 10": a pinned
                    // CONFLICTING airport can push the collapsed count above the
                    // cap, so naming the number here would sometimes lie.
                    Text(showsAllAirports ? "Show fewer" : "Show all \(total) airports")
                }
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.primary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("observationsShowAll")
        }
    }

    /// What the cells mean. The Wind axis especially needs it: the cell is a bare
    /// crosswind number, so without a unit and a colour key it reads as an
    /// unlabelled score.
    @ViewBuilder
    private var legend: some View {
        Text(legendText)
            .font(.caption2)
            .foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var legendText: String {
        let condition = "Condition: flight category from the METAR, the TAF at ETA, and the model."
        let wind = "Wind: crosswind (kt) on the best runway, coloured by wind advisory."
        let agreement = "✓ agrees · ⚠ one category apart · ✗ conflicts."
        if showsBothAxes { return "\(condition) \(wind) \(agreement)" }
        return "\(axis == .condition ? condition : wind) \(agreement)"
    }

    /// Which comparison groups are drawn — both on iPad, the selected one on
    /// iPhone.
    private var visibleAxes: [ObservationAxis] {
        showsBothAxes ? ObservationAxis.allCases : [axis]
    }

    private var gridColumnCount: Int { 3 + visibleAxes.count * 4 }

    private func groupHeader(_ text: String) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(Theme.textMuted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .tableCell()
    }

    private func columnHeader(_ text: String) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundStyle(Theme.textMuted)
            .tableCell()
    }

    @ViewBuilder
    private func row(_ apt: AirportObservation, comparison: ObservationComparison?) -> some View {
        let conflicting = comparison?.categoryMatch == "CONFLICTING"
        // Amber is this table's flag colour (a model conflict); the Area Hazards
        // table passes red. `tableCell` owns the shared tint strength.
        let highlight: Color? = conflicting ? Theme.amber : nil
        GridRow {
            Button { detail = apt } label: {
                HStack(spacing: Theme.spacingXS) {
                    Text(apt.icao)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.text)
                    Image(systemName: "info.circle")
                        .font(.caption2)
                        .foregroundStyle(Theme.primary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(apt.icao) details")
            .tableCell(highlight: highlight)

            Text(apt.distanceFromRouteNm.map { "\(Int($0.rounded()))nm" } ?? "—")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .tableCell(highlight: highlight)

            Text(apt.etaHourOffset.map { "+\($0)h" } ?? "—")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .tableCell(highlight: highlight)

            ForEach(visibleAxes) { a in
                switch a {
                case .condition:
                    categoryCell(apt.metarFlightCategory, highlight: highlight)
                    categoryCell(apt.tafFlightCategoryAtEta, highlight: highlight)
                    categoryCell(comparison?.modelCategory, highlight: highlight)
                    AgreementIcon(match: comparison?.categoryMatch).tableCell(highlight: highlight)
                case .wind:
                    WindAdvisoryBadge(status: apt.metarWindAdvisory, crosswindKt: apt.metarCrosswindKt)
                        .tableCell(highlight: highlight)
                    WindAdvisoryBadge(status: apt.tafWindAdvisory, crosswindKt: apt.tafCrosswindKt)
                        .tableCell(highlight: highlight)
                    WindAdvisoryBadge(status: comparison?.modelWindAdvisory, crosswindKt: comparison?.modelCrosswindKt)
                        .tableCell(highlight: highlight)
                    AgreementIcon(match: comparison?.windAdvisoryMatch).tableCell(highlight: highlight)
                }
            }
        }
    }

    /// A flight-category cell. Absent data renders as plain muted text, not a
    /// filled badge — a grey capsule carries the same visual weight as a real
    /// category and reads as if "—" were itself a rating. Matches how
    /// `WindAdvisoryBadge` treats a missing advisory.
    @ViewBuilder
    private func categoryCell(_ category: String?, highlight: Color?) -> some View {
        if let category, !category.isEmpty {
            FlightCategoryBadge(category: category).tableCell(highlight: highlight)
        } else {
            Text("—")
                .font(.caption2)
                .foregroundStyle(Theme.textMuted)
                .tableCell(highlight: highlight)
        }
    }

    /// "0550Z" from an ISO timestamp.
    static func zuluTime(_ iso: String) -> String? {
        guard let date = Date.parseISO8601(iso) else { return nil }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.hour, .minute], from: date)
        guard let h = c.hour, let m = c.minute else { return nil }
        return String(format: "%02d%02dZ", h, m)
    }
}

/// The two comparison groups the web renders side-by-side.
enum ObservationAxis: String, CaseIterable, Identifiable, Sendable {
    case condition
    case wind

    var id: String { rawValue }

    var label: String {
        switch self {
        case .condition: "Condition"
        case .wind: "Wind"
        }
    }
}

// MARK: - Cell components

/// CONFIRMING / SIGNIFICANT / CONFLICTING as a glyph, matching the web's
/// ✓ / ⚠ / ✗ agreement column.
struct AgreementIcon: View {
    let match: String?

    var body: some View {
        switch match {
        case "CONFIRMING":
            Image(systemName: "checkmark").font(.caption2.bold()).foregroundStyle(Theme.green)
                .accessibilityLabel("Model agrees")
        case "SIGNIFICANT":
            Image(systemName: "exclamationmark.triangle.fill").font(.caption2).foregroundStyle(Theme.amber)
                .accessibilityLabel("One category apart")
        case "CONFLICTING":
            Image(systemName: "xmark").font(.caption2.bold()).foregroundStyle(Theme.red)
                .accessibilityLabel("Model conflicts")
        default:
            Text("—").font(.caption2).foregroundStyle(Theme.textMuted)
        }
    }
}

/// Runway wind advisory as a status-coloured capsule. The web shows a bare
/// G/A/R letter with the crosswind in a hover tooltip; touch has no hover, so
/// the crosswind value *is* the label and the colour carries the status.
struct WindAdvisoryBadge: View {
    let status: String?
    let crosswindKt: Double?

    private var color: Color {
        switch status?.lowercased() {
        case "green": Theme.green
        case "amber": Theme.amber
        case "red": Theme.red
        default: Theme.textMuted
        }
    }

    var body: some View {
        if status == nil {
            Text("—").font(.caption2).foregroundStyle(Theme.textMuted)
        } else {
            Text(crosswindKt.map { "\(Int($0.rounded()))" } ?? "•")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.white)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(color, in: Capsule())
                .accessibilityLabel(accessibilityText)
        }
    }

    private var accessibilityText: String {
        let s = status?.lowercased() ?? "unknown"
        guard let xw = crosswindKt else { return "Wind \(s)" }
        return "Wind \(s), \(Int(xw.rounded())) knot crosswind"
    }
}

// MARK: - Per-airport detail

/// The web's ⓘ popup: raw METAR, raw TAF with the applicable lines highlighted,
/// and the per-source wind breakdown.
private struct ObservationDetailSheet: View {
    let airport: AirportObservation
    let comparison: ObservationComparison?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.spacingM) {
                    if let name = airport.name, !name.isEmpty {
                        Text(name)
                            .font(.subheadline)
                            .foregroundStyle(Theme.textMuted)
                    }

                    metarBlock
                    tafBlock
                    windBlock

                    if let detail = comparison?.detail, !detail.isEmpty {
                        section("Model comparison") {
                            Text(detail)
                                .font(.caption)
                                .foregroundStyle(Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding(Theme.cardPadding)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Theme.bg)
            .navigationTitle(airport.icao)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private var metarBlock: some View {
        section("METAR") {
            if let raw = airport.metarRaw, !raw.isEmpty {
                VStack(alignment: .leading, spacing: Theme.spacingXS) {
                    Text(raw)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(Theme.text)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    if let cat = airport.metarFlightCategory {
                        FlightCategoryBadge(category: cat)
                    }
                }
            } else {
                unavailable
            }
        }
    }

    /// TAF with the base line + the BECMG/TEMPO groups active at the ETA
    /// emphasised, the same highlight the web applies from
    /// `taf_applicable_lines`.
    @ViewBuilder
    private var tafBlock: some View {
        section("TAF") {
            if let raw = airport.tafRaw, !raw.isEmpty {
                let applicable = Set(airport.tafApplicableLines ?? [])
                let lines = raw.components(separatedBy: .newlines)
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                        Text(line)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(applicable.contains(index) ? Theme.text : Theme.textMuted)
                            .fontWeight(applicable.contains(index) ? .semibold : .regular)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 4)
                            .background(
                                applicable.contains(index) ? Theme.primary.opacity(0.10) : .clear,
                                in: RoundedRectangle(cornerRadius: 4)
                            )
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let cat = airport.tafFlightCategoryAtEta {
                        HStack(spacing: Theme.spacingXS) {
                            Text("At ETA")
                                .font(.caption2)
                                .foregroundStyle(Theme.textMuted)
                            FlightCategoryBadge(category: cat)
                            if let trend = airport.tafTrendType {
                                Text(trend)
                                    .font(.caption2)
                                    .foregroundStyle(Theme.textMuted)
                            }
                        }
                        .padding(.top, Theme.spacingXS)
                    }
                }
            } else {
                unavailable
            }
        }
    }

    @ViewBuilder
    private var windBlock: some View {
        let rows = windRows
        if !rows.isEmpty {
            section("Runway wind") {
                VStack(alignment: .leading, spacing: Theme.spacingXS) {
                    ForEach(rows, id: \.source) { r in
                        HStack(spacing: Theme.spacingS) {
                            Text(r.source)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(Theme.textMuted)
                                .frame(width: 52, alignment: .leading)
                            WindAdvisoryBadge(status: r.status, crosswindKt: r.crosswind)
                            if let wind = r.wind {
                                Text("\(wind)kt")
                                    .font(.caption)
                                    .foregroundStyle(Theme.text)
                            }
                            if let rwy = r.runway {
                                Text("Rwy \(rwy)")
                                    .font(.caption)
                                    .foregroundStyle(Theme.textMuted)
                            }
                            if let xw = r.crosswind {
                                Text("\(Int(xw.rounded()))kt xwind")
                                    .font(.caption2)
                                    .foregroundStyle(Theme.textMuted)
                            }
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
        }
    }

    private struct WindRow {
        let source: String
        let status: String?
        let wind: String?
        let runway: String?
        let crosswind: Double?
    }

    private var windRows: [WindRow] {
        var rows: [WindRow] = []
        if airport.metarWindAdvisory != nil || airport.metarWindSpeedKt != nil {
            rows.append(WindRow(
                source: "METAR",
                status: airport.metarWindAdvisory,
                wind: WindFormat.wind(speed: airport.metarWindSpeedKt.map(Double.init),
                                     dir: airport.metarWindDir.map(Double.init),
                                     gust: airport.metarWindGustKt.map(Double.init)),
                runway: airport.metarBestRunwayId,
                crosswind: airport.metarCrosswindKt
            ))
        }
        if airport.tafWindAdvisory != nil || airport.tafWindSpeedKt != nil {
            rows.append(WindRow(
                source: "TAF",
                status: airport.tafWindAdvisory,
                wind: WindFormat.wind(speed: airport.tafWindSpeedKt.map(Double.init),
                                     dir: airport.tafWindDir.map(Double.init),
                                     gust: airport.tafWindGustKt.map(Double.init)),
                runway: airport.tafBestRunwayId,
                crosswind: airport.tafCrosswindKt
            ))
        }
        if let comparison, comparison.modelWindAdvisory != nil || comparison.modelWindSpeedKt != nil {
            rows.append(WindRow(
                source: "Model",
                status: comparison.modelWindAdvisory,
                wind: WindFormat.wind(speed: comparison.modelWindSpeedKt,
                                     dir: comparison.modelWindDir,
                                     gust: comparison.modelWindGustKt),
                runway: comparison.modelBestRunwayId,
                crosswind: comparison.modelCrosswindKt
            ))
        }
        return rows
    }

    private var unavailable: some View {
        Text("Not available")
            .font(.caption)
            .foregroundStyle(Theme.textMuted)
    }

    @ViewBuilder
    private func section(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingS)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.border, lineWidth: 0.5))
    }
}
