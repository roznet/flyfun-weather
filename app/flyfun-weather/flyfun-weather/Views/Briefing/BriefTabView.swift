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
                        BriefAdvisoryCard(viewModel: viewModel, advisory: advisory, catalog: response.catalog)
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
    let viewModel: BriefingViewModel
    let advisory: RouteAdvisoryResult
    let catalog: [AdvisoryCatalogEntry]
    @State private var isExpanded = false
    @State private var showingDetail = false
    @Environment(\.colorScheme) private var colorScheme

    private var catalogEntry: AdvisoryCatalogEntry? {
        catalog.first { $0.id == advisory.advisoryId }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
            HStack(alignment: .firstTextBaseline, spacing: WeatherTheme.Spacing.sm) {
                AssessmentStringBadge(status: advisory.aggregateStatus)
                Text(catalogEntry?.name ?? advisory.advisoryId)
                    .font(.headline)
                    .foregroundStyle(WeatherTheme.text(colorScheme))
                    .lineLimit(2)
                Spacer(minLength: WeatherTheme.Spacing.sm)
                Button {
                    showingDetail = true
                } label: {
                    Image(systemName: "doc.text.magnifyingglass")
                        .foregroundStyle(WeatherTheme.primary(colorScheme))
                }
                .accessibilityLabel("Advisory detail")

                Button {
                    withAnimation(.snappy) { isExpanded.toggle() }
                } label: {
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
                .accessibilityLabel(isExpanded ? "Collapse advisory" : "Expand advisory")
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
        .sheet(isPresented: $showingDetail) {
            AdvisoryDetailSheet(
                viewModel: viewModel,
                advisory: advisory,
                catalogEntry: catalogEntry
            )
        }
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

private struct AdvisoryDetailSheet: View {
    let viewModel: BriefingViewModel
    let advisory: RouteAdvisoryResult
    let catalogEntry: AdvisoryCatalogEntry?

    @Environment(\.dismiss) private var dismiss
    @State private var state: LoadingState<AdvisoryDetailResponse> = .loading

    var body: some View {
        NavigationStack {
            Group {
                switch state {
                case .idle, .loading:
                    ProgressView("Loading detail...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .error(let error):
                    ContentUnavailableView {
                        Label("Detail Unavailable", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(error.localizedDescription)
                    } actions: {
                        Button("Retry") {
                            Task { await load() }
                        }
                    }
                case .loaded(let detail):
                    if detail.isEmpty {
                        ContentUnavailableView(
                            "No Detail Available",
                            systemImage: "doc.text.magnifyingglass",
                            description: Text("This advisory has no route-specific drilldown in the selected pack.")
                        )
                    } else {
                        AdvisoryDetailContent(detail: detail, fallbackAdvisory: advisory, catalogEntry: catalogEntry)
                    }
                }
            }
            .navigationTitle(catalogEntry?.name ?? advisory.advisoryId)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .task(id: advisory.advisoryId) {
            await load()
        }
    }

    private func load() async {
        state = .loading
        do {
            state = .loaded(try await viewModel.advisoryDetail(advisoryId: advisory.advisoryId))
        } catch {
            state = .error(error)
        }
    }
}

private struct AdvisoryDetailContent: View {
    let detail: AdvisoryDetailResponse
    let fallbackAdvisory: RouteAdvisoryResult
    let catalogEntry: AdvisoryCatalogEntry?
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: WeatherTheme.Spacing.lg) {
                VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
                    HStack(spacing: WeatherTheme.Spacing.sm) {
                        AssessmentStringBadge(status: detail.aggregateStatus ?? fallbackAdvisory.aggregateStatus)
                        Text(detail.name ?? catalogEntry?.name ?? fallbackAdvisory.advisoryId)
                            .font(.title3.bold())
                            .foregroundStyle(WeatherTheme.text(colorScheme))
                        Spacer()
                    }

                    Text(detail.aggregateDetail ?? fallbackAdvisory.aggregateDetail)
                        .font(.subheadline)
                        .foregroundStyle(WeatherTheme.text(colorScheme))
                        .fixedSize(horizontal: false, vertical: true)

                    VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
                        if let routeName = detail.routeName {
                            Label(routeName.uppercased(), systemImage: "point.topleft.down.curvedto.point.bottomright.up")
                        }
                        if let timestamp = detail.briefingTimestamp {
                            Label(timestamp, systemImage: "calendar.badge.clock")
                        }
                        if let totalDistance = detail.totalDistanceNm {
                            Label("\(formatNumber(totalDistance)) nm analysed", systemImage: "ruler")
                        }
                    }
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
                .padding(WeatherTheme.Spacing.lg)
                .weatherCard(colorScheme)

                if !detail.perModel.isEmpty {
                    AdvisoryDetailSection(title: "Route Notes") {
                        ForEach(detail.perModel) { model in
                            AdvisoryDetailModelRow(model: model)
                            if model.id != detail.perModel.last?.id {
                                Divider()
                            }
                        }
                    }
                }

                if let convective = detail.convective, !convective.isEmpty {
                    AdvisoryDetailSection(title: "Convective Split") {
                        ForEach(convective.keys.sorted(), id: \.self) { model in
                            if let convectiveDetail = convective[model] {
                                AdvisoryConvectiveDetailRow(model: model, detail: convectiveDetail)
                                if model != convective.keys.sorted().last {
                                    Divider()
                                }
                            }
                        }
                        if let note = detail.convectiveNote {
                            Text(note)
                                .font(.caption)
                                .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                if !detail.parametersUsed.isEmpty {
                    AdvisoryDetailSection(title: "Thresholds") {
                        Text(parameterSummary(detail.parametersUsed))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                if let description = detail.description ?? catalogEntry?.description {
                    AdvisoryDetailSection(title: "Implication") {
                        Text(description)
                            .font(.subheadline)
                            .foregroundStyle(WeatherTheme.text(colorScheme))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                if let note = detail.crossCheckNote {
                    AdvisoryDetailSection(title: "Cross-Check") {
                        Text(note)
                            .font(.caption)
                            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(WeatherTheme.Spacing.lg)
        }
        .background(WeatherTheme.background(colorScheme))
    }

    private func parameterSummary(_ parameters: [String: Double]) -> String {
        parameters
            .sorted { $0.key < $1.key }
            .map { "\($0.key): \(formatNumber($0.value))" }
            .joined(separator: " · ")
    }
}

private struct AdvisoryDetailSection<Content: View>: View {
    let title: String
    let content: Content
    @Environment(\.colorScheme) private var colorScheme

    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            Text(title)
                .font(.headline)
                .foregroundStyle(WeatherTheme.text(colorScheme))
            content
        }
        .padding(WeatherTheme.Spacing.lg)
        .weatherCard(colorScheme)
    }
}

private struct AdvisoryDetailModelRow: View {
    let model: AdvisoryDetailModelResult
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
            HStack {
                BriefModelStatusBadge(model: model.model ?? "model", status: model.status ?? "unavailable")
                Spacer()
                if let affected = affectedText {
                    Text(affected)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
            }
            if let detail = model.detail {
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(WeatherTheme.text(colorScheme))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let crossCheck = model.crossCheck, !crossCheck.isEmpty {
                Label(crossCheck, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(WeatherTheme.primary(colorScheme))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var affectedText: String? {
        var parts: [String] = []
        if let pct = model.affectedPct {
            parts.append("\(formatNumber(pct))%")
        }
        if let nm = model.affectedNm {
            if let total = model.totalNm {
                parts.append("\(formatNumber(nm))/\(formatNumber(total)) nm")
            } else {
                parts.append("\(formatNumber(nm)) nm")
            }
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

private struct AdvisoryConvectiveDetailRow: View {
    let model: String
    let detail: AdvisoryConvectiveDetail
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
            Text(model.shortModelName.uppercased())
                .font(.caption.bold())
                .foregroundStyle(WeatherTheme.text(colorScheme))

            if let method = detail.assessmentMethod {
                Label(method, systemImage: "switch.2")
                    .font(.caption)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
            }

            if let range = detail.thermo?.capeRangeJkg, range.count == 2 {
                Label("\(formatNumber(range[0]))-\(formatNumber(range[1])) J/kg CAPE", systemImage: "chart.line.uptrend.xyaxis")
                    .font(.caption)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
            }

            if let peak = detail.thermo?.peak {
                Text(peakSummary(peak))
                    .font(.caption)
                    .foregroundStyle(WeatherTheme.text(colorScheme))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let nwp = detail.nwp {
                Text(nwpSummary(nwp))
                    .font(.caption)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func peakSummary(_ peak: AdvisoryThermoPeak) -> String {
        var parts: [String] = []
        if let cape = peak.capeJkg {
            parts.append("peak \(formatNumber(cape)) J/kg")
        }
        if let top = peak.elTopFt {
            parts.append("tops \(formatNumber(top)) ft")
        }
        if let waypoint = peak.waypointIcao {
            parts.append(waypoint)
        } else if let distance = peak.distanceNm {
            parts.append("\(formatNumber(distance)) nm")
        }
        return parts.joined(separator: " · ")
    }

    private func nwpSummary(_ nwp: AdvisoryNwpDetail) -> String {
        var parts: [String] = []
        if let cover = nwp.maxCoverPct {
            parts.append("NWP cover \(formatNumber(cover))%")
        }
        if let top = nwp.peakTopFt {
            parts.append("NWP tops \(formatNumber(top)) ft")
        }
        if let note = nwp.note {
            parts.append(note)
        }
        return parts.joined(separator: " · ")
    }
}

private func formatNumber(_ value: Double) -> String {
    if value.rounded() == value {
        return String(Int(value))
    }
    return String(format: "%.1f", value)
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
