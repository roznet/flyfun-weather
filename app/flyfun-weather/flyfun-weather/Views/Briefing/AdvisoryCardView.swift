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
    /// When the host owns expansion (the all-clear strip drives it from a single
    /// `expandedId`), it passes a binding so the pill highlight and the card's
    /// expansion can't diverge. nil → the card manages its own state (grid cards).
    var externalExpanded: Binding<Bool>? = nil
    @State private var internalExpanded = false
    @State private var showingDetail = false
    @Environment(AppState.self) private var appState

    private var isExpanded: Bool { externalExpanded?.wrappedValue ?? internalExpanded }

    private func setExpanded(_ value: Bool) {
        if let externalExpanded { externalExpanded.wrappedValue = value }
        else { internalExpanded = value }
    }

    private var catalogEntry: AdvisoryCatalogEntry? {
        catalog.first { $0.id == advisory.advisoryId }
    }

    /// CTA row: "Why it's RED ›" (AMBER/RED only) + "On cross-section" lens chip
    /// (advisories that map to a cross-section lens, #6). Rendered only when at
    /// least one button is present, so GREEN/no-lens cards add no empty node.
    @ViewBuilder
    private var ctaRow: some View {
        let showWhy = viewModel != nil && (advisory.aggregateStatus == "amber" || advisory.aggregateStatus == "red")
        let lensPreset = viewModel != nil ? CrossSectionPresets.preset(forAdvisory: advisory.advisoryId) : nil
        if showWhy || lensPreset != nil {
            HStack(spacing: 14) {
                if showWhy {
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
                if let viewModel, let preset = lensPreset {
                    Button {
                        viewModel.setFocusIntent(FocusIntent(
                            target: .crossSection,
                            model: viewModel.selectedModel,
                            advisoryPresetId: preset.id,
                            pointIndex: viewModel.activePointIndex
                        ))
                    } label: {
                        HStack(spacing: 3) {
                            Image(systemName: "chart.xyaxis.line")
                            Text("On cross-section")
                        }
                        .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.accentColor)
                }
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header
            HStack {
                Button {
                    withAnimation { setExpanded(!isExpanded) }
                } label: {
                    HStack {
                        AssessmentStringBadge(status: advisory.aggregateStatus)
                        Text(catalogEntry?.name ?? advisory.advisoryId)
                            .font(.headline)
                    }
                }
                .buttonStyle(.plain)
                // (i) help popup — content from the cached help catalog (#311).
                // Gated on cache presence so first-run (pre-sync, before any
                // advisory help has been fetched) shows no dead button — mirrors
                // the Skew-T variable picker's gate.
                if appState.helpCatalog.advisory(advisory.advisoryId) != nil {
                    HelpInfoButton(topic: .advisory(advisory.advisoryId))
                }
                // Mitigation presence hint (#330): a soft lightbulb shown only when
                // this advisory carries mitigations. Presence cue only — the tip
                // text lives in the detail sheet. Neutral tint, never a status
                // colour; advice only, never a regrade.
                if !(advisory.aggregateMitigations ?? []).isEmpty {
                    Image(systemName: "lightbulb")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("mitigation available")
                }
                Spacer()
                Button {
                    withAnimation { setExpanded(!isExpanded) }
                } label: {
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }

            Text(advisory.aggregateDetail)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            // Per-model badges — FlowLayout packs them tightly and wraps to a
            // second row instead of stretching across evenly-spaced columns.
            if !advisory.perModel.isEmpty {
                FlowLayout(spacing: 4) {
                    ForEach(advisory.perModel) { modelResult in
                        ModelStatusBadge(model: modelResult.model, status: modelResult.status)
                    }
                }
            }

            ctaRow

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
                // The advisory description is intentionally NOT repeated here —
                // it's one tap away via the (i) HelpInfoButton in the header (#311),
                // and duplicating it just consumed vertical space (iOS testing feedback).
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        .sheet(isPresented: $showingDetail) {
            if let viewModel {
                AdvisoryDetailView(viewModel: viewModel, advisoryId: advisory.advisoryId,
                                   fallbackName: catalogEntry?.name ?? advisory.advisoryId,
                                   mitigations: advisory.aggregateMitigations ?? [])
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
                // Card expansion is driven by `expandedId`: collapsing inside the
                // card clears it, removing the card and un-highlighting the pill —
                // so the two never diverge.
                AdvisoryCardView(advisory: advisory, catalog: catalog, viewModel: viewModel,
                                 externalExpanded: Binding(
                                    get: { self.expandedId == advisory.advisoryId },
                                    set: { if !$0 { withAnimation { self.expandedId = nil } } }))
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.green.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
    }
}
