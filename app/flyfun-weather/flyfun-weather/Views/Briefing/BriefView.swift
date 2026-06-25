import SwiftUI

/// The "Brief" tab (§4.1): a single Flighty-style scroll that folds the old
/// Advisories + Digest tabs into one narrative, encoding the read-me-first
/// order. Phase 2 establishes the composition (hero → watch → digest →
/// airports → advisories); Phase 5 decomposes the digest by role and adds the
/// advisory-detail ladder + synopsis-last ordering.
struct BriefView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        ScrollView {
            // §4.1 read-me-first order: HERO → DIGEST → WATCH → AIRPORTS →
            // ADVISORIES → SYNOPSIS (last; context, not a quick-read).
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                heroSection
                digestSection
                watchSection
                AirportConditionsView(viewModel: viewModel)
                advisoriesSection
                synopsisSection
            }
            .padding(.vertical, Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    // MARK: Hero — traffic light + reason

    @ViewBuilder
    private var heroSection: some View {
        if let pack = viewModel.pack, let assessment = pack.assessment {
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                AssessmentStringBadge(status: assessment)
                if let reason = pack.assessmentReason {
                    Text(reason)
                        .font(Theme.heroLabel)
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, Theme.cardPadding)
        }
    }

    // MARK: Watch items

    @ViewBuilder
    private var watchSection: some View {
        if case .loaded(let digest) = viewModel.digestState {
            let items = digest.watchItemsList
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: Theme.spacingS) {
                    Text("Watch")
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    FlowChips(items: items) { item in
                        viewModel.setFocusIntent(FocusIntent(
                            target: .crossSection,
                            model: viewModel.selectedModel,
                            layerId: Self.watchLayerId(for: item),
                            pointIndex: viewModel.activePointIndex
                        ))
                    }
                }
                .padding(.horizontal, Theme.cardPadding)
            }
        }
    }

    /// Map a watch-item phrase to the cross-section layer it most relates to, so
    /// the deep-link turns on the relevant view. nil → just jump to the chart.
    private static func watchLayerId(for item: String) -> String? {
        let s = item.lowercased()
        if s.contains("ice") || s.contains("icing") { return "icing-ogimet-nwp-bands" }
        if s.contains("conv") || s.contains("storm") || s.contains("cb") || s.contains("cape") { return "thermo-convective-bg" }
        if s.contains("turb") || s.contains("shear") { return "cat-bands" }
        return nil
    }

    // MARK: Digest hazard narrative

    @ViewBuilder
    private var digestSection: some View {
        switch viewModel.digestState {
        case .idle, .loading:
            ProgressView("Generating summary…")
                .frame(maxWidth: .infinity)
                .padding(.horizontal, Theme.cardPadding)
        case .loaded(let digest):
            ForEach(digest.hazardSections, id: \.title) { section in
                VStack(alignment: .leading, spacing: Theme.spacingXS) {
                    Text(section.title)
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    Text(section.text)
                        .font(.body)
                        .foregroundStyle(Theme.textMuted)
                }
                .padding(.horizontal, Theme.cardPadding)
            }
        case .error:
            EmptyView()
        }
    }

    // MARK: Synopsis (last — context, not a quick-read)

    @ViewBuilder
    private var synopsisSection: some View {
        if case .loaded(let digest) = viewModel.digestState, let synopsis = digest.synopsis {
            VStack(alignment: .leading, spacing: Theme.spacingXS) {
                Text("Synoptic Overview")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                Text(synopsis)
                    .font(.body)
                    .foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, Theme.cardPadding)
        }
    }

    // MARK: Advisories

    @ViewBuilder
    private var advisoriesSection: some View {
        switch viewModel.advisoriesState {
        case .idle, .loading:
            ProgressView("Loading advisories…")
                .padding(.horizontal, Theme.cardPadding)
        case .loaded(let response):
            let sorted = response.advisories.sorted { severityRank($0.aggregateStatus) > severityRank($1.aggregateStatus) }
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                Text("Advisories")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                ForEach(sorted) { advisory in
                    AdvisoryCardView(advisory: advisory, catalog: response.catalog, viewModel: viewModel)
                }
            }
            .padding(.horizontal, Theme.cardPadding)
        case .error(let error):
            ContentUnavailableView("Advisories Unavailable", systemImage: "exclamationmark.triangle",
                                   description: Text(error.localizedDescription))
        }
    }

}

/// Wrapping chip row for watch items. Each chip deep-links to the cross-section
/// at the shared active point (§4.7), enabling the layer its keyword implies.
private struct FlowChips: View {
    let items: [String]
    let onTap: (String) -> Void

    var body: some View {
        // Horizontal, glanceable chip row (§4.1) — keeps the Brief scannable on
        // one screen instead of pushing Advisories below the fold.
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Theme.spacingS) {
                ForEach(items, id: \.self) { item in
                    Button { onTap(item) } label: {
                        HStack(spacing: Theme.spacingXS) {
                            Label(item, systemImage: "exclamationmark.circle")
                            Image(systemName: "chevron.right").font(.caption2)
                        }
                        .font(.subheadline)
                        .foregroundStyle(Theme.amber)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Theme.amber.opacity(0.12), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}
