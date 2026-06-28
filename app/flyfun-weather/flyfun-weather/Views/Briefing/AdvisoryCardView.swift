import SwiftUI

/// Model name inside a colored status bubble.
private struct ModelStatusBadge: View {
    let model: String
    let status: String

    private var color: Color {
        (Assessment(rawValue: status.lowercased()) ?? .unavailable).color
    }

    var body: some View {
        Text(model.shortModelName)
            .font(.caption2.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color, in: Capsule())
    }
}

/// Single advisory card. AMBER/RED render as full cards in the responsive grid;
/// GREEN advisories collapse into the compact `GreenAdvisoryStrip` (#310). The
/// matching per-hazard digest narrative (when one maps to this advisory) is
/// shown in the expanded body so Discussion stays big-picture.
struct AdvisoryCardView: View {
    let advisory: RouteAdvisoryResult
    let catalog: [AdvisoryCatalogEntry]
    /// Per-hazard digest narrative attached to this advisory (#310). nil = none.
    var hazardNarrative: String? = nil
    /// Needed for the Rung-3 "why it's RED" detail sheet (§4.6).
    var viewModel: BriefingViewModel? = nil
    /// Green pills start expanded when surfaced from the all-clear strip.
    var startExpanded: Bool = false
    @State private var isExpanded = false
    @State private var showingDetail = false

    private var catalogEntry: AdvisoryCatalogEntry? {
        catalog.first { $0.id == advisory.advisoryId }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header
            Button {
                withAnimation { isExpanded.toggle() }
            } label: {
                HStack {
                    AssessmentStringBadge(status: advisory.aggregateStatus)
                    Text(catalogEntry?.name ?? advisory.advisoryId)
                        .font(.headline)
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.plain)

            Text(advisory.aggregateDetail)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            // Per-model badges
            if !advisory.perModel.isEmpty {
                let columns = Array(repeating: GridItem(.flexible(), spacing: 4), count: min(advisory.perModel.count, 4))
                LazyVGrid(columns: columns, spacing: 4) {
                    ForEach(advisory.perModel) { modelResult in
                        ModelStatusBadge(model: modelResult.model, status: modelResult.status)
                    }
                }
            }

            // "Why it's RED ›" CTA — only when AMBER/RED (GREEN cards stay calm, §4.6 Rung 1).
            if viewModel != nil, advisory.aggregateStatus == "amber" || advisory.aggregateStatus == "red" {
                Button {
                    showingDetail = true
                } label: {
                    HStack(spacing: 2) {
                        Text("Why it's \(advisory.aggregateStatus.uppercased())")
                        Image(systemName: "chevron.right")
                    }
                    .font(.caption.weight(.semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.accentColor)
            }

            // Expanded detail
            if isExpanded {
                Divider()
                // Per-hazard digest narrative (#310): the LLM's plain-language read
                // on this hazard, attached to the card it explains.
                if let hazardNarrative, !hazardNarrative.isEmpty {
                    Text(hazardNarrative)
                        .font(.callout)
                        .foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, 2)
                }
                ForEach(advisory.perModel) { modelResult in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(modelResult.model.uppercased())
                                .font(.caption.bold())
                            Spacer()
                            Text("\(Int(modelResult.affectedPct))% affected")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Text(modelResult.detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if let entry = catalogEntry {
                    Text(entry.description)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .padding(.top, 4)
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        .onAppear { if startExpanded { isExpanded = true } }
        .sheet(isPresented: $showingDetail) {
            if let viewModel {
                AdvisoryDetailView(viewModel: viewModel, advisoryId: advisory.advisoryId,
                                   fallbackName: catalogEntry?.name ?? advisory.advisoryId)
            }
        }
    }
}

/// Compact "all clear" strip collapsing every GREEN advisory into a row of
/// pills (mirrors the web app, #310 item 2). Tapping a pill expands that
/// advisory's full card inline below the strip.
struct GreenAdvisoryStrip: View {
    let advisories: [RouteAdvisoryResult]
    let catalog: [AdvisoryCatalogEntry]
    var viewModel: BriefingViewModel? = nil

    @State private var expandedId: String?

    private func name(for advisory: RouteAdvisoryResult) -> String {
        catalog.first { $0.id == advisory.advisoryId }?.name ?? advisory.advisoryId
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack(spacing: Theme.spacingXS) {
                Image(systemName: "checkmark.seal.fill")
                    .foregroundStyle(Theme.green)
                Text("All clear")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                Text("\(advisories.count)")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Theme.textMuted)
            }

            FlowLayout(spacing: Theme.spacingS) {
                ForEach(advisories) { advisory in
                    let isOpen = expandedId == advisory.advisoryId
                    Button {
                        withAnimation { expandedId = isOpen ? nil : advisory.advisoryId }
                    } label: {
                        HStack(spacing: 4) {
                            Circle().fill(Theme.green).frame(width: 6, height: 6)
                            Text(name(for: advisory))
                                .font(.caption.weight(.medium))
                        }
                        .foregroundStyle(Theme.text)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Theme.green.opacity(isOpen ? 0.18 : 0.10), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }

            if let expandedId, let advisory = advisories.first(where: { $0.advisoryId == expandedId }) {
                AdvisoryCardView(advisory: advisory, catalog: catalog, viewModel: viewModel,
                                 startExpanded: true)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.green.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
    }
}
