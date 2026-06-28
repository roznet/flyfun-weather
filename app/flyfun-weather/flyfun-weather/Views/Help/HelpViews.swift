import SwiftUI

/// What a help (i) button explains — a metric or an advisory, by catalog id.
enum HelpTopic: Equatable {
    case metric(String)
    case advisory(String)
}

/// Small "(i)" button that opens the help popup for a metric or advisory.
///
/// All content comes from `AppState.helpCatalog` (the cached web-app catalog) —
/// no help text is hand-written here. Renders offline once the catalog has been
/// synced (or, for metrics, from the bundled baseline on first run).
struct HelpInfoButton: View {
    let topic: HelpTopic
    var size: Font = .caption

    @State private var showing = false

    var body: some View {
        Button { showing = true } label: {
            Image(systemName: "info.circle")
                .font(size)
                .foregroundStyle(Theme.primary)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Help")
        .sheet(isPresented: $showing) { HelpDetailView(topic: topic) }
    }
}

/// Full help popup for a metric or advisory, rendered from the cached catalog.
struct HelpDetailView: View {
    let topic: HelpTopic

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                    switch topic {
                    case .metric(let id):
                        if let metric = appState.helpCatalog.metric(id) {
                            metricBody(metric)
                        } else {
                            unavailable
                        }
                    case .advisory(let id):
                        if let advisory = appState.helpCatalog.advisory(id) {
                            advisoryBody(advisory)
                        } else {
                            unavailable
                        }
                    }
                }
                .padding(Theme.cardPadding)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .background(Theme.bg)
        }
    }

    private var title: String {
        switch topic {
        case .metric(let id): appState.helpCatalog.metric(id)?.name ?? id
        case .advisory(let id): appState.helpCatalog.advisory(id)?.name ?? id
        }
    }

    private var unavailable: some View {
        ContentUnavailableView(
            "Help Unavailable Offline",
            systemImage: "wifi.slash",
            description: Text("Connect once to download the help guide, then it works offline.")
        )
        .padding(.top, 40)
    }

    // MARK: - Metric

    @ViewBuilder
    private func metricBody(_ metric: MetricHelp) -> some View {
        if let vibe = metric.vibe, !vibe.isEmpty {
            Text(vibe)
                .font(.title3)
                .italic()
                .foregroundStyle(Theme.text)
        }
        if let unit = metric.unit, !unit.isEmpty {
            Text("Unit: \(unit)").font(.caption).foregroundStyle(Theme.textMuted)
        }
        if let goal = metric.primaryGoal, !goal.isEmpty {
            section("What it measures") { paragraph(goal) }
        }
        if let best = metric.bestUsedFor, !best.isEmpty {
            section("Best used for") { paragraph(best) }
        }
        if let theory = metric.theory, !theory.isEmpty {
            section("How it works") { paragraph(theory) }
        }
        if let limits = metric.limitations, !limits.isEmpty {
            section("Limitations") { paragraph(limits) }
        }
        if let thresholds = metric.thresholds, !thresholds.isEmpty {
            section("Thresholds") { thresholdTable(thresholds) }
        }
        if let wiki = metric.wikipedia, let url = URL(string: wiki) {
            Link(destination: url) {
                Label("Wikipedia", systemImage: "arrow.up.right.square")
                    .font(.callout)
            }
        }
    }

    private func thresholdTable(_ thresholds: [MetricThreshold]) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            ForEach(thresholds) { t in
                HStack(alignment: .top, spacing: Theme.spacingS) {
                    Circle().fill(riskColor(t.risk)).frame(width: 8, height: 8).padding(.top, 5)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(t.label).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.text)
                            if let range = rangeText(min: t.min, max: t.max) {
                                Text(range).font(.caption).foregroundStyle(Theme.textMuted)
                            }
                        }
                        Text(t.meaning).font(.caption).foregroundStyle(Theme.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func rangeText(min: Double?, max: Double?) -> String? {
        let fmt: (Double) -> String = { v in
            v == v.rounded() ? String(Int(v)) : String(format: "%g", v)
        }
        switch (min, max) {
        case let (lo?, hi?): return "\(fmt(lo))–\(fmt(hi))"
        case let (lo?, nil): return "≥ \(fmt(lo))"
        case let (nil, hi?): return "< \(fmt(hi))"
        default: return nil
        }
    }

    private func riskColor(_ risk: String) -> Color {
        switch risk.lowercased() {
        case "none": Theme.textMuted
        case "low": Theme.green
        case "moderate": Theme.amber
        case "high": Theme.red
        case "severe", "extreme": Theme.lifr
        default: Theme.textMuted
        }
    }

    // MARK: - Advisory

    @ViewBuilder
    private func advisoryBody(_ advisory: AdvisoryCatalogEntry) -> some View {
        Text(advisory.category.capitalized)
            .font(.caption.weight(.semibold))
            .foregroundStyle(Theme.primary)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Theme.primary.opacity(0.12), in: Capsule())
        paragraph(advisory.description)
        if !advisory.parameters.isEmpty {
            section("Parameters") {
                VStack(alignment: .leading, spacing: Theme.spacingM) {
                    ForEach(advisory.parameters, id: \.key) { p in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(p.label).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.text)
                                Text(defaultText(p)).font(.caption).foregroundStyle(Theme.textMuted)
                            }
                            Text(p.description).font(.caption).foregroundStyle(Theme.textMuted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func defaultText(_ p: AdvisoryParameterDef) -> String {
        let value = p.default == p.default.rounded() ? String(Int(p.default)) : String(format: "%g", p.default)
        return p.unit.isEmpty ? value : "\(value) \(p.unit)"
    }

    // MARK: - Shared

    private func section(_ title: String, @ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(Theme.textMuted)
            content()
        }
    }

    private func paragraph(_ text: String) -> some View {
        Text(text)
            .font(.body)
            .foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
