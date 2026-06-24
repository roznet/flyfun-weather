import SwiftUI

/// First-read briefing surface: hero, digest, watch items, airports, and advisory cards.
struct BriefTabView: View {
    @Bindable var viewModel: BriefingViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: WeatherTheme.Spacing.lg) {
                BriefHeroSection(viewModel: viewModel)
                BriefDigestSection(viewModel: viewModel)
                AirportConditionsView(viewModel: viewModel)
                BriefAdvisorySection(viewModel: viewModel)
            }
            .padding(.vertical, WeatherTheme.Spacing.lg)
        }
        .background(WeatherTheme.background(colorScheme))
    }
}

private struct BriefHeroSection: View {
    let viewModel: BriefingViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            HStack(alignment: .firstTextBaseline) {
                Text(assessmentText)
                    .font(.system(.largeTitle, design: .rounded, weight: .bold))
                    .foregroundStyle(assessmentColor)
                    .lineLimit(1)
                Spacer()
                if let pack = viewModel.pack {
                    Text(viewModel.packLabel(for: pack))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
            }

            if let reason = viewModel.pack?.assessmentReason, !reason.isEmpty {
                Text(reason)
                    .font(.subheadline)
                    .foregroundStyle(WeatherTheme.text(colorScheme))
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Briefing assessment is loading.")
                    .font(.subheadline)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
            }

            HStack(spacing: WeatherTheme.Spacing.sm) {
                Label("FL\(viewModel.flight.cruiseAltitudeFt / 100)", systemImage: "arrow.up.right")
                Label("\(Int(viewModel.flight.flightDurationHours * 60)) min", systemImage: "timer")
                if let point = viewModel.activePoint {
                    Label("\(Int(point.distanceNm)) nm selected", systemImage: "scope")
                }
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
        }
        .padding(WeatherTheme.Spacing.lg)
        .weatherCard(colorScheme)
        .padding(.horizontal, WeatherTheme.Spacing.lg)
    }

    private var assessmentText: String {
        viewModel.pack?.assessment?.uppercased() ?? "BRIEF"
    }

    private var assessmentColor: Color {
        let status = viewModel.pack?.assessment?.lowercased() ?? "unavailable"
        return (Assessment(rawValue: status) ?? .unavailable).color
    }
}

private struct BriefDigestSection: View {
    @Bindable var viewModel: BriefingViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        switch viewModel.digestState {
        case .idle, .loading:
            HStack {
                ProgressView()
                Text("Generating summary...")
                    .font(.subheadline)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                Spacer()
            }
            .padding(WeatherTheme.Spacing.lg)
            .weatherCard(colorScheme)
            .padding(.horizontal, WeatherTheme.Spacing.lg)
        case .error(let error):
            ContentUnavailableView("Digest Unavailable", systemImage: "doc.text", description: Text(error.localizedDescription))
                .padding(.horizontal, WeatherTheme.Spacing.lg)
        case .loaded(let digest):
            VStack(alignment: .leading, spacing: WeatherTheme.Spacing.lg) {
                if !digest.watchItemsList.isEmpty {
                    BriefWatchSection(viewModel: viewModel, watchItems: digest.watchItemsList)
                }

                if !hazardSections(from: digest).isEmpty {
                    VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
                        Text("Digest")
                            .font(.headline)
                        ForEach(Array(hazardSections(from: digest).enumerated()), id: \.offset) { _, section in
                            BriefTextSection(title: section.title, text: section.text)
                        }
                    }
                    .padding(WeatherTheme.Spacing.lg)
                    .weatherCard(colorScheme)
                }

                if let synoptic = digest.synoptic, !synoptic.isEmpty {
                    VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
                        Text("Synopsis")
                            .font(.headline)
                        Text(synoptic)
                            .font(.subheadline)
                            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                    }
                    .padding(WeatherTheme.Spacing.lg)
                    .weatherCard(colorScheme)
                }
            }
            .padding(.horizontal, WeatherTheme.Spacing.lg)
        }
    }

    private func hazardSections(from digest: DigestResponse) -> [(title: String, text: String)] {
        digest.sections.filter { $0.title != "Synoptic Overview" }
    }
}

private struct BriefWatchSection: View {
    @Bindable var viewModel: BriefingViewModel
    let watchItems: [String]
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            Text("Watch")
                .font(.headline)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: WeatherTheme.Spacing.sm) {
                    ForEach(watchItems, id: \.self) { item in
                        Button {
                            viewModel.setFocusIntent(.init(
                                target: .crossSection,
                                model: viewModel.selectedModel,
                                pointIndex: viewModel.activePoint?.pointIndex
                            ))
                        } label: {
                            Label(item, systemImage: "exclamationmark.circle")
                                .font(.caption)
                                .lineLimit(2)
                                .frame(maxWidth: 260, alignment: .leading)
                                .foregroundStyle(WeatherTheme.primary(colorScheme))
                                .padding(.horizontal, WeatherTheme.Spacing.sm)
                                .padding(.vertical, WeatherTheme.Spacing.xs)
                                .background(
                                    WeatherTheme.primary(colorScheme).opacity(0.10),
                                    in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.control)
                                )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(WeatherTheme.Spacing.lg)
        .weatherCard(colorScheme)
    }
}

private struct BriefTextSection: View {
    let title: String
    let text: String
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
            Text(title)
                .font(.subheadline.bold())
                .foregroundStyle(WeatherTheme.text(colorScheme))
            Text(text)
                .font(.subheadline)
                .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct BriefAdvisorySection: View {
    let viewModel: BriefingViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            Text("Advisories")
                .font(.headline)
                .padding(.horizontal, WeatherTheme.Spacing.lg)

            switch viewModel.advisoriesState {
            case .idle, .loading:
                ProgressView("Loading advisories...")
                    .frame(maxWidth: .infinity)
                    .padding(WeatherTheme.Spacing.lg)
            case .error(let error):
                ContentUnavailableView("Advisories Unavailable", systemImage: "exclamationmark.triangle", description: Text(error.localizedDescription))
            case .loaded(let response):
                let sorted = response.advisories.sorted { severityOrder($0.aggregateStatus) > severityOrder($1.aggregateStatus) }
                LazyVStack(spacing: WeatherTheme.Spacing.md) {
                    ForEach(sorted) { advisory in
                        BriefAdvisoryCard(advisory: advisory, catalog: response.catalog)
                    }
                }
                .padding(.horizontal, WeatherTheme.Spacing.lg)
            }
        }
    }

    private func severityOrder(_ status: String) -> Int {
        switch status.lowercased() {
        case "red": 3
        case "amber": 2
        case "green": 1
        default: 0
        }
    }
}

private struct BriefAdvisoryCard: View {
    let advisory: RouteAdvisoryResult
    let catalog: [AdvisoryCatalogEntry]
    @State private var isExpanded = false
    @Environment(\.colorScheme) private var colorScheme

    private var catalogEntry: AdvisoryCatalogEntry? {
        catalog.first { $0.id == advisory.advisoryId }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
            Button {
                withAnimation(.snappy) { isExpanded.toggle() }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: WeatherTheme.Spacing.sm) {
                    AssessmentStringBadge(status: advisory.aggregateStatus)
                    Text(catalogEntry?.name ?? advisory.advisoryId)
                        .font(.headline)
                        .foregroundStyle(WeatherTheme.text(colorScheme))
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
            }
            .buttonStyle(.plain)

            Text(advisory.aggregateDetail)
                .font(.subheadline)
                .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: WeatherTheme.Spacing.xs) {
                ForEach(advisory.perModel) { modelResult in
                    BriefModelStatusBadge(model: modelResult.model, status: modelResult.status)
                }
            }

            if isExpanded {
                Divider()
                ForEach(advisory.perModel) { modelResult in
                    VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
                        HStack {
                            Text(modelResult.model.uppercased())
                                .font(.caption.bold())
                            Spacer()
                            Text("\(Int(modelResult.affectedPct))% · \(Int(modelResult.affectedNm)) nm")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                        }
                        Text(modelResult.detail)
                            .font(.caption)
                            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                        if let crossCheck = modelResult.crossCheck, !crossCheck.isEmpty {
                            Label(crossCheck, systemImage: "info.circle")
                                .font(.caption)
                                .foregroundStyle(WeatherTheme.primary(colorScheme))
                        }
                    }
                }

                if !advisory.parametersUsed.isEmpty {
                    Text(parameterSummary)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
            }
        }
        .padding(WeatherTheme.Spacing.lg)
        .weatherCard(colorScheme)
    }

    private var parameterSummary: String {
        advisory.parametersUsed
            .sorted { $0.key < $1.key }
            .map { "\($0.key): \(formatNumber($0.value))" }
            .joined(separator: " · ")
    }

    private func formatNumber(_ value: Double) -> String {
        if value.rounded() == value {
            return String(Int(value))
        }
        return String(format: "%.1f", value)
    }
}

private struct BriefModelStatusBadge: View {
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

private extension View {
    func weatherCard(_ scheme: ColorScheme) -> some View {
        background(WeatherTheme.surface(scheme), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.card))
            .overlay {
                RoundedRectangle(cornerRadius: WeatherTheme.Radius.card)
                    .stroke(WeatherTheme.border(scheme), lineWidth: 0.5)
            }
            .shadow(color: WeatherTheme.shadow(scheme), radius: 8, y: 3)
    }
}
