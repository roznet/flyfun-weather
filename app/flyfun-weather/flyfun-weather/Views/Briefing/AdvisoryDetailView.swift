import SwiftUI

/// The advisory-detail ladder Rung 3 (§4.6): the "why it's RED" sheet. Shows
/// WHY THIS GRADE (per-model detail; for convective, the CAPE-vs-cover
/// reconciliation with peak location + ETA) and WHAT FIRED IT (the thresholds).
///
/// CARDINAL RULE: cross-check is an EXPLAINER, never an alert — rendered in
/// neutral/info chrome (primary blue / muted), never amber or red. The UI
/// explains why it's RED; it never offers "but maybe ignore it".
struct AdvisoryDetailView: View {
    let viewModel: BriefingViewModel
    let advisoryId: String
    let fallbackName: String
    /// Advice-only mitigations sourced from the card's already-loaded
    /// `RouteAdvisoryResult.aggregateMitigations` (#330) — NOT from the fetched
    /// `AdvisoryDetailResponse` (the shaper that would carry them there is the
    /// MCP slice). Keeping this client-side means the tip renders without a
    /// server round-trip. Advice only: never changes the grade.
    var mitigations: [Mitigation] = []

    @Environment(\.dismiss) private var dismiss
    @State private var state: LoadingState<AdvisoryDetailResponse> = .idle

    var body: some View {
        NavigationStack {
            Group {
                switch state {
                case .idle, .loading:
                    ProgressView("Loading detail…").frame(maxWidth: .infinity, maxHeight: .infinity)
                case .loaded(let detail):
                    content(detail)
                case .error(let error):
                    ContentUnavailableView("Detail Unavailable", systemImage: "exclamationmark.triangle",
                                           description: Text(error.localizedDescription))
                }
            }
            .navigationTitle(fallbackName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .background(Theme.bg)
        }
        .task(id: advisoryId) { await load() }
    }

    private func load() async {
        state = .loading
        do { state = .loaded(try await viewModel.fetchAdvisoryDetail(advisoryId: advisoryId)) }
        catch { state = .error(error) }
    }

    @ViewBuilder
    private func content(_ detail: AdvisoryDetailResponse) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                header(detail)
                whyThisGrade(detail)
                optionsToImprove()
                whatFiredIt(detail)
                if let desc = detail.description {
                    section("About this advisory") {
                        Text(desc).font(.body).foregroundStyle(Theme.textMuted)
                    }
                }
                showOnCrossSection(detail)   // RUNG 4 (§4.6)
            }
            .padding(Theme.cardPadding)
        }
        .accessibilityIdentifier("advisoryDetail")
    }

    private func header(_ detail: AdvisoryDetailResponse) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack {
                AssessmentStringBadge(status: detail.aggregateStatus)
                Text(detail.name ?? fallbackName).font(.headline).foregroundStyle(Theme.text)
            }
            Text(detail.aggregateDetail).font(.subheadline).foregroundStyle(Theme.textMuted)
        }
    }

    // MARK: WHY THIS GRADE

    @ViewBuilder
    private func whyThisGrade(_ detail: AdvisoryDetailResponse) -> some View {
        section("Why this grade") {
            ForEach(detail.perModel) { m in
                modelRow(m, convective: detail.convective?[m.model])
            }
            // The explainer note — neutral chrome, never amber/red.
            if let note = detail.crossCheckNote {
                explainer(note)
            }
            if let cnote = detail.convectiveNote {
                explainer(cnote)
            }
        }
    }

    @ViewBuilder
    private func modelRow(_ m: ModelAdvisoryDetail, convective: ConvectiveModelDetail?) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            HStack {
                AssessmentStringBadge(status: m.status)
                Text(m.model.uppercased()).font(.caption.bold()).foregroundStyle(Theme.text)
                Spacer()
                if let pct = m.affectedPct {
                    Text("\(Int(pct))% affected").font(.tabularData(.caption2)).foregroundStyle(Theme.textMuted)
                }
            }
            if !m.detail.isEmpty {
                Text(m.detail).font(.caption).foregroundStyle(Theme.textMuted)
            }
            if let conv = convective { convectiveBlock(conv) }
            // Per-model cross-check — neutral, the explainer (cardinal rule).
            if let cc = m.crossCheck, !cc.isEmpty {
                explainer(cc)
            }
        }
        .padding(.vertical, Theme.spacingXS)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("advisoryDetailModel-\(m.model)")
    }

    /// Convective reconciliation: the "RED under blue sky" story with peak + ETA.
    @ViewBuilder
    private func convectiveBlock(_ conv: ConvectiveModelDetail) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            if let method = conv.assessmentMethod {
                Text("Graded by: \(method == "thermo" ? "thermodynamic (CAPE)" : method)")
                    .font(.caption2).foregroundStyle(Theme.textMuted)
            }
            if let peakText = peakText(conv.thermo?.peak) {
                Text("Peak: \(peakText)")
                    .font(.tabularData(.caption2)).foregroundStyle(Theme.text)
            }
            if let cover = conv.nwp?.maxCoverPct {
                Text("Model convective cover: ~\(Int(cover))%\(cover < 5 ? " (blue sky)" : "")")
                    .font(.caption2).foregroundStyle(Theme.textMuted)
            }
        }
        .padding(.leading, Theme.spacingS)
    }

    private func peakText(_ peak: ConvectivePeak?) -> String? {
        guard let peak else { return nil }
        var parts: [String] = []
        if let cape = peak.capeJkg { parts.append("CAPE \(Int(cape)) J/kg") }
        if let el = peak.elTopFt { parts.append("tops ~FL\(Int(el / 100))") }
        if let icao = peak.waypointIcao { parts.append("near \(icao)") }
        if let eta = peak.eta { parts.append("ETA \(shortTime(eta))") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: OPTIONS TO IMPROVE (advice only — #330)

    /// Advice-only mitigations: decisions that would improve a specific flagged
    /// sub-issue. Rendered in the neutral "tip" variant of `explainer()` — same
    /// blue info chrome, lightbulb instead of info-circle. A mitigation NEVER
    /// changes the grade: a RED advisory with a mitigation is still RED.
    @ViewBuilder
    private func optionsToImprove() -> some View {
        if !mitigations.isEmpty {
            section("Options to improve") {
                ForEach(mitigations) { m in
                    tip(m.detail)
                }
            }
        }
    }

    // MARK: WHAT FIRED IT

    @ViewBuilder
    private func whatFiredIt(_ detail: AdvisoryDetailResponse) -> some View {
        if !detail.parametersUsed.isEmpty {
            section("What fired it") {
                ForEach(detail.parametersUsed.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                    HStack {
                        Text(key).font(.tabularData(.caption2)).foregroundStyle(Theme.textMuted)
                        Spacer()
                        Text(formatted(value)).font(.tabularData(.caption2)).foregroundStyle(Theme.text)
                    }
                }
            }
        }
    }

    // MARK: RUNG 4 — deep-link to the cross-section (§4.6)

    /// "Show on cross-section ›" — jumps to the cross-section tab, enables this
    /// advisory's layer, and scrubs to the convective peak point (when known).
    private func showOnCrossSection(_ detail: AdvisoryDetailResponse) -> some View {
        Button {
            let (model, pointIndex) = peakModelAndPoint(detail)
            viewModel.setFocusIntent(FocusIntent(
                target: .crossSection,
                model: model,
                layerId: Self.crossSectionLayerId(for: advisoryId),
                pointIndex: pointIndex
            ))
            dismiss()
        } label: {
            HStack {
                Label("Show on cross-section", systemImage: "chart.xyaxis.line")
                Spacer()
                Image(systemName: "chevron.right").font(.caption)
            }
            .font(.subheadline.weight(.medium))
            .foregroundStyle(Theme.primary)
            .padding(Theme.cardPadding)
            .frame(maxWidth: .infinity)
            .background(Theme.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        }
        .buttonStyle(.plain)
    }

    /// Representative cross-section layer for an advisory category; nil leaves
    /// the default layers as-is (still jumps to the chart at the point).
    private static func crossSectionLayerId(for advisoryId: String) -> String? {
        switch advisoryId {
        case "convective": return "thermo-convective-bg"
        case "icing": return "icing-ogimet-nwp-bands"
        case "turbulence", "cat": return "cat-bands"
        default: return nil
        }
    }

    /// The model + route point of the first resolvable convective peak (so the
    /// cross-section opens at the peak for the model that drove the grade).
    private func peakModelAndPoint(_ detail: AdvisoryDetailResponse) -> (String, Int?) {
        guard case .loaded(let analyses) = viewModel.routeAnalysesState else {
            return (detail.perModel.first?.model ?? viewModel.selectedModel, nil)
        }
        for m in detail.perModel {
            if let icao = detail.convective?[m.model]?.thermo?.peak?.waypointIcao,
               let p = analyses.analyses.first(where: { $0.waypointIcao == icao }) {
                return (m.model, p.pointIndex)
            }
        }
        return (detail.perModel.first?.model ?? viewModel.selectedModel, viewModel.activePointIndex)
    }

    // MARK: Building blocks

    private func explainer(_ text: String) -> some View {
        HStack(alignment: .top, spacing: Theme.spacingXS) {
            Image(systemName: "info.circle").foregroundStyle(Theme.primary)
            Text(text).font(.caption).foregroundStyle(Theme.textMuted)
        }
        .padding(Theme.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: Theme.spacingS))
    }

    /// Tip variant of `explainer()` for advice-only mitigations (#330): identical
    /// neutral blue chrome, lightbulb (idea/actionable) instead of the info-circle
    /// (diagnostic). Never amber/red — a mitigation is advice, never a regrade.
    private func tip(_ text: String) -> some View {
        HStack(alignment: .top, spacing: Theme.spacingXS) {
            Image(systemName: "lightbulb").foregroundStyle(Theme.primary)
            Text(text).font(.caption).foregroundStyle(Theme.textMuted)
        }
        .padding(Theme.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: Theme.spacingS))
    }

    @ViewBuilder
    private func section(_ title: String, @ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(Theme.textMuted)
            content()
        }
    }

    private func formatted(_ v: Double) -> String {
        v == v.rounded() ? "\(Int(v))" : String(format: "%.1f", v)
    }

    private static let isoParser = ISO8601DateFormatter()
    private static let hhmmUTC: DateFormatter = {
        let fmt = DateFormatter(); fmt.dateFormat = "HH:mm"; fmt.timeZone = TimeZone(identifier: "UTC")
        return fmt
    }()

    private func shortTime(_ iso: String) -> String {
        guard let date = Self.isoParser.date(from: iso) else { return iso }
        return "\(Self.hhmmUTC.string(from: date))Z"
    }
}
