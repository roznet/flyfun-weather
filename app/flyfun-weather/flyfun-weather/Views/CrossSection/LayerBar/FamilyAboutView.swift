import SwiftUI

/// "About <family>" (#605, web `familyAboutHtml`): one card per option in the
/// family, from the metrics catalog the app already ships — `vibe` as the
/// summary, `primary_goal` and `best_used_for` under it — with "Full detail"
/// into the existing metric help for the limitations, theory and thresholds.
///
/// Per family rather than per layer, because "NWP versus DD" is one comparative
/// question, not two definitions. Presented as a popover — a real popover on
/// iPad, a sheet on iPhone: reading about a method is a different mode from
/// comparing layers, and dismissing it returns you to the chart as it was.
struct FamilyAboutView: View {
    let family: LayerFamily
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @State private var detail: MetricRef?

    private struct MetricRef: Identifiable { let id: String }

    private struct Card: Identifiable {
        let layerId: String
        let metricId: String
        let metric: MetricHelp
        var id: String { layerId }
    }

    private var cards: [Card] {
        family.layerIds.compactMap { id in
            guard let metricId = CrossSectionLayer.metricIds[id],
                  let metric = appState.helpCatalog.metric(metricId) else { return nil }
            return Card(layerId: id, metricId: metricId, metric: metric)
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.spacingM) {
                    if let intro = family.aboutIntro {
                        Text(intro)
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if cards.isEmpty {
                        // The line layers carry no catalog entry. Say so rather
                        // than show an empty list that reads as a load failure.
                        Text("These are reference lines rather than computed layers, so there is nothing to compare — the hint under the pills says what each one is.")
                            .font(.callout)
                            .foregroundStyle(Theme.textMuted)
                    }
                    ForEach(cards) { cardView($0) }
                }
                .padding(Theme.cardPadding)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Theme.bg)
            .navigationTitle("About \(family.label.lowercased())")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
            }
        }
        .frame(idealWidth: 460, idealHeight: 560)
        .presentationDetents([.medium, .large])
        .sheet(item: $detail) { HelpDetailView(topic: .metric($0.id)) }
    }

    private func cardView(_ card: Card) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(CrossSectionLayer.label(card.layerId)).font(.headline)
                Spacer()
                Text(card.metricId)
                    .font(.caption2.monospaced())
                    .foregroundStyle(Theme.textMuted)
            }
            if let vibe = card.metric.vibe, !vibe.isEmpty {
                Text(vibe).font(.subheadline).fixedSize(horizontal: false, vertical: true)
            }
            if let goal = card.metric.primaryGoal, !goal.isEmpty { field("Goal", goal) }
            if let best = card.metric.bestUsedFor, !best.isEmpty { field("Best used for", best) }
            Button("Full detail →") { detail = MetricRef(id: card.metricId) }
                .font(.caption.weight(.semibold))
                .buttonStyle(.borderless)
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
    }

    private func field(_ title: String, _ text: String) -> some View {
        Text("**\(title)**  \(text)")
            .font(.caption)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The "About <family>" button. Owns its own presentation, so it behaves the
/// same in the detail row and in the options sheet.
struct FamilyAboutButton: View {
    let family: LayerFamily
    @State private var showing = false

    var body: some View {
        Button { showing = true } label: {
            Label("About \(family.label.lowercased())", systemImage: "info.circle")
                .font(.caption.weight(.medium))
        }
        .buttonStyle(.borderless)
        .popover(isPresented: $showing) { FamilyAboutView(family: family) }
        .accessibilityIdentifier("layerFamilyAbout-\(family.rawValue)")
    }
}
