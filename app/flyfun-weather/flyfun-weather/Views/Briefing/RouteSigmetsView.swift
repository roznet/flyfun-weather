import SwiftUI

/// **Area Hazards** (#493) — the D-0 route SIGMET table, sitting between the
/// Observations section and Alternates. Mirrors the web's `renderRouteSigmets`
/// (`web/ts/managers/briefing-ui.ts`).
///
/// The sibling of `RouteObservationsView` on the same fetch/refresh seam, but
/// area-keyed rather than airport-keyed and with **no model comparison** —
/// SIGMETs are presented, not reconciled. That makes this the simpler of the two
/// tables: one row per bulletin, no agreement column, no axis switcher.
///
/// The web row is 6 columns; on a phone the **movement** column folds into the
/// detail sheet (it is the least decision-relevant of the pair the issue calls
/// out — the vertical band decides whether a GA route is even in the hazard,
/// while "MOV NE 30kt" only refines the timing). Regular width shows it inline.
///
/// Rendered only when the pack carries at least one matched SIGMET, which the
/// pipeline populates on D-0 only.
///
/// SYNC: `web/ts/managers/briefing-ui.ts::renderRouteSigmets`.
struct RouteSigmetsView: View {
    let viewModel: BriefingViewModel

    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var detail: SigmetAlongRoute?

    /// iPad-class width has room for the movement column inline; a phone reads it
    /// in the detail sheet.
    private var showsMovement: Bool { sizeClass == .regular }

    private var sigmets: RouteSigmets? {
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot.routeSigmets }
        return nil
    }

    var body: some View {
        if let block = sigmets, !block.matched.isEmpty {
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                header(block)
                summary(block)
                if block.severe {
                    severeBanner
                }
                table(block)
            }
            .padding(.horizontal, Theme.cardPadding)
            .accessibilityIdentifier("sigmetsSection")
            .sheet(item: $detail) { s in
                SigmetDetailSheet(sigmet: s)
            }
        }
    }

    // MARK: Header + summary

    @ViewBuilder
    private func header(_ block: RouteSigmets) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Text("Area Hazards")
                .font(.headline)
                .foregroundStyle(Theme.text)
            Spacer(minLength: 0)
            if let fetched = block.fetchTime, let label = RouteObservationsView.zuluTime(fetched) {
                Text("Fetched \(label)")
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }

    /// Coverage line: how many bulletins matched, the corridor width, and the
    /// union of hazard types — the same three facts the web summary carries.
    @ViewBuilder
    private func summary(_ block: RouteSigmets) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            Text(coverageText(block))
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            if !block.hazardTypes.isEmpty {
                Label(block.hazardTypes.joined(separator: ", "), systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(Theme.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func coverageText(_ block: RouteSigmets) -> String {
        var parts: [String] = []
        let n = block.sigmetCount
        parts.append(n == 1 ? "1 SIGMET" : "\(n) SIGMETs")
        if let c = block.corridorNm { parts.append("\(Int(c.rounded()))nm corridor") }
        // The band the fetch filtered on, so an empty-looking table is readable as
        // "nothing in *this* band" rather than "nothing anywhere".
        if let high = block.altitudeHighFt {
            parts.append("\(SigmetAlongRoute.level(block.altitudeLowFt ?? 0))–\(SigmetAlongRoute.level(high))")
        }
        return parts.joined(separator: " · ")
    }

    private var severeBanner: some View {
        Label(
            "A SIGMET along this route is qualified SEV — check the raw bulletin before you go.",
            systemImage: "exclamationmark.triangle.fill"
        )
        .font(.caption)
        .foregroundStyle(Theme.red)
        .fixedSize(horizontal: false, vertical: true)
        .padding(Theme.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: Table

    /// Horizontally scrollable so a large Dynamic Type size degrades into a pan
    /// rather than truncating the level band. `.basedOnSize` suppresses the
    /// bounce when the row already fits, which is the common case.
    @ViewBuilder
    private func table(_ block: RouteSigmets) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            ScrollView(.horizontal) {
                // `horizontalSpacing: 0` with per-cell padding, for the same reason
                // as the observations table: a severe row is highlighted by shading
                // its cells, and Grid applies a row background per cell — with
                // spacing > 0 that tiles into visible gaps rather than one band.
                Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: Theme.spacingS) {
                    GridRow {
                        columnHeader("Hazard")
                        columnHeader("FIR")
                        columnHeader("Levels")
                        columnHeader("Enroute")
                        if showsMovement { columnHeader("Move") }
                    }
                    Divider().gridCellUnsizedAxes(.horizontal).gridCellColumns(showsMovement ? 5 : 4)
                    ForEach(block.matched) { s in
                        row(s)
                    }
                }
                .padding(.vertical, Theme.spacingXS)
            }
            .scrollBounceBehavior(.basedOnSize, axes: .horizontal)

            legend
        }
    }

    private func columnHeader(_ text: String) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundStyle(Theme.textMuted)
            .cell()
    }

    @ViewBuilder
    private func row(_ s: SigmetAlongRoute) -> some View {
        GridRow {
            Button { detail = s } label: {
                HStack(spacing: Theme.spacingXS) {
                    Text(s.headline)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(s.isSevere ? Theme.red : Theme.text)
                    Image(systemName: "info.circle")
                        .font(.caption2)
                        .foregroundStyle(Theme.primary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(s.firId) \(s.headline) details")
            .cell(highlighted: s.isSevere)

            Text(s.firId)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(Theme.text)
                .cell(highlighted: s.isSevere)

            Text(s.levelBand)
                .font(.caption)
                .foregroundStyle(Theme.text)
                .cell(highlighted: s.isSevere)

            Text(s.enrouteLabel)
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .cell(highlighted: s.isSevere)

            if showsMovement {
                Text(s.movementLabel ?? "STNR")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .cell(highlighted: s.isSevere)
            }
        }
    }

    /// What the columns mean. "Enroute" especially needs it: the cell is a bare
    /// nm span that reads as a distance *from* something without the key.
    @ViewBuilder
    private var legend: some View {
        Text(legendText)
            .font(.caption2)
            .foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var legendText: String {
        let base = "Levels: the hazard's vertical band. Enroute: where your track crosses the area, or how far it passes off-track."
        let move = showsMovement ? " Move: area movement (STNR = stationary)." : ""
        return "\(base)\(move) Tap a row for the raw bulletin."
    }
}

private extension View {
    /// One table cell — same construction as the observations table: the Grid
    /// runs at `horizontalSpacing: 0` so the gutter lives *inside* the cell and a
    /// highlighted row shades as one continuous band.
    func cell(highlighted: Bool = false) -> some View {
        self
            .padding(.horizontal, Theme.spacingXS)
            .padding(.vertical, 3)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .background(highlighted ? Theme.red.opacity(0.12) : .clear)
    }
}

// MARK: - Per-SIGMET detail

/// The web's ⓘ popup: the vertical band, movement, validity window and route
/// intersection, then the raw bulletin — which is the authoritative text and the
/// reason the row is tappable at all.
private struct SigmetDetailSheet: View {
    let sigmet: SigmetAlongRoute
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.spacingM) {
                    Text(firLine)
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)

                    section("Hazard") {
                        VStack(alignment: .leading, spacing: Theme.spacingXS) {
                            metaRow("Levels", sigmet.levelBand)
                            metaRow("Move", sigmet.movementLabel ?? "Stationary")
                            if let valid = sigmet.validityLabel {
                                metaRow("Valid", valid)
                            }
                            metaRow("Enroute", sigmet.enrouteLabel)
                            if let min = sigmet.minDistanceNm {
                                metaRow("Nearest", "\(Int(min.rounded()))nm from track")
                            }
                        }
                    }

                    section("Raw bulletin") {
                        if let raw = sigmet.rawText, !raw.isEmpty {
                            Text(raw)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(Theme.text)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        } else {
                            Text("Not available")
                                .font(.caption)
                                .foregroundStyle(Theme.textMuted)
                        }
                    }
                }
                .padding(Theme.cardPadding)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Theme.bg)
            .navigationTitle(sigmet.headline)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    /// "LFFF PARIS" when the source names the FIR, else the bare id. `fir_name`
    /// already embeds the id upstream, so don't print it twice.
    private var firLine: String {
        guard let name = sigmet.firName, !name.isEmpty else { return sigmet.firId }
        return name.hasPrefix(sigmet.firId) ? name : "\(sigmet.firId) \(name)"
    }

    private func metaRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
                .frame(width: 62, alignment: .leading)
            Text(value)
                .font(.caption)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
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
