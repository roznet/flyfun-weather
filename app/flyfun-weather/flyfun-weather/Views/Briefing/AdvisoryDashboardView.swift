import SwiftUI

/// Dashboard showing advisory cards sorted by severity.
struct AdvisoryDashboardView: View {
    let viewModel: BriefingViewModel
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Assessment banner
                if let pack = viewModel.pack, let assessment = pack.assessment {
                    HStack {
                        AssessmentStringBadge(status: assessment)
                        if let reason = pack.assessmentReason {
                            Text(reason)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .padding(.horizontal)
                }

                // Airport conditions
                AirportConditionsView(viewModel: viewModel)

                // Advisories
                advisoryContent
            }
            .padding(.vertical)
        }
    }

    private var gridColumns: [GridItem] {
        if sizeClass == .regular {
            Array(repeating: GridItem(.flexible(), spacing: 12), count: 3)
        } else {
            [GridItem(.flexible())]
        }
    }

    @ViewBuilder
    private var advisoryContent: some View {
        switch viewModel.advisoriesState {
        case .idle, .loading:
            ProgressView("Loading advisories...")
                .padding()
        case .loaded(let response):
            let sorted = response.advisories.sorted { severityOrder($0.aggregateStatus) > severityOrder($1.aggregateStatus) }
            LazyVGrid(columns: gridColumns, alignment: .leading, spacing: 12) {
                ForEach(sorted) { advisory in
                    AdvisoryCardView(advisory: advisory, catalog: response.catalog)
                }
            }
            .padding(.horizontal)
        case .error(let error):
            ContentUnavailableView("Advisories Unavailable", systemImage: "exclamationmark.triangle", description: Text(error.localizedDescription))
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

/// Single advisory card.
private struct AdvisoryCardView: View {
    let advisory: RouteAdvisoryResult
    let catalog: [AdvisoryCatalogEntry]
    @State private var isExpanded = false

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

            // Per-model badges
            if !advisory.perModel.isEmpty {
                let columns = Array(repeating: GridItem(.flexible(), spacing: 4), count: min(advisory.perModel.count, 4))
                LazyVGrid(columns: columns, spacing: 4) {
                    ForEach(advisory.perModel) { modelResult in
                        ModelStatusBadge(model: modelResult.model, status: modelResult.status)
                    }
                }
            }

            // Expanded detail
            if isExpanded {
                Divider()
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
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}
