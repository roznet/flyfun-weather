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
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                heroSection
                watchSection
                digestSection
                AirportConditionsView(viewModel: viewModel)
                advisoriesSection
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
                    FlowChips(items: items)
                }
                .padding(.horizontal, Theme.cardPadding)
            }
        }
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
            ForEach(digest.sections, id: \.title) { section in
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

    // MARK: Advisories

    @ViewBuilder
    private var advisoriesSection: some View {
        switch viewModel.advisoriesState {
        case .idle, .loading:
            ProgressView("Loading advisories…")
                .padding(.horizontal, Theme.cardPadding)
        case .loaded(let response):
            let sorted = response.advisories.sorted { severityOrder($0.aggregateStatus) > severityOrder($1.aggregateStatus) }
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                Text("Advisories")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                ForEach(sorted) { advisory in
                    AdvisoryCardView(advisory: advisory, catalog: response.catalog)
                }
            }
            .padding(.horizontal, Theme.cardPadding)
        case .error(let error):
            ContentUnavailableView("Advisories Unavailable", systemImage: "exclamationmark.triangle",
                                   description: Text(error.localizedDescription))
        }
    }

    private func severityOrder(_ status: String) -> Int {
        switch status {
        case "red": 3
        case "amber": 2
        case "green": 1
        default: 0
        }
    }
}

/// Simple wrapping chip row for watch items (deep-link to instruments comes in
/// Phase 3/5; for now they're glanceable chips).
private struct FlowChips: View {
    let items: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            ForEach(items, id: \.self) { item in
                Label(item, systemImage: "exclamationmark.circle")
                    .font(.subheadline)
                    .foregroundStyle(Theme.amber)
            }
        }
    }
}
