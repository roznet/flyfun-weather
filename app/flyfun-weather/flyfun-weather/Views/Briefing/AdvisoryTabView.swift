import SwiftUI

/// The **Advisory** tab (#310): the read-me-first surface. A prominent accented
/// hero (traffic-light + assessment reason) pinned at the top, then watch chips,
/// a responsive advisory grid (AMBER/RED as cards, GREEN collapsed into one
/// all-clear strip), current airport conditions, and — on marginal D-0/D-2
/// packs — weather alternates. A sticky scroll-spy bar jumps between sections.
struct AdvisoryTabView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        ScrollSpyScroll(sections: spySections) {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                heroSection
                    .spyAnchor("hero")
                watchSection
                advisoriesSection
                    .spyAnchor("advisories")
                AirportConditionsView(viewModel: viewModel)
                    .spyAnchor("conditions")
                if hasAlternates {
                    AlternatesView(viewModel: viewModel)
                        .spyAnchor("alternates")
                }
            }
            .padding(.vertical, Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    // MARK: Scroll-spy sections (only those present)

    private var hasAlternates: Bool {
        if case .loaded(let snapshot) = viewModel.snapshotState, snapshot.alternates != nil { return true }
        return false
    }

    private var spySections: [SpySection] {
        var sections = [SpySection("hero", "Summary"), SpySection("advisories", "Advisories"),
                        SpySection("conditions", "Conditions")]
        if hasAlternates { sections.append(SpySection("alternates", "Alternates")) }
        return sections
    }

    // MARK: Hero — accented traffic-light + reason (#310 item 3)

    @ViewBuilder
    private var heroSection: some View {
        if let pack = viewModel.pack, let assessment = pack.assessment {
            let severity = Assessment(rawValue: assessment.lowercased()) ?? .unavailable
            HStack(spacing: 0) {
                Rectangle()
                    .fill(severity.color)
                    .frame(width: 5)
                VStack(alignment: .leading, spacing: Theme.spacingS) {
                    AssessmentStringBadge(status: assessment)
                    if let reason = pack.assessmentReason {
                        Text(reason)
                            .font(Theme.heroLabel)
                            .foregroundStyle(Theme.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(Theme.cardPadding)
                Spacer(minLength: 0)
            }
            .background(severity.color.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: Theme.cornerRadius))
            .padding(.horizontal, Theme.cardPadding)
        }
    }

    // MARK: Watch chips

    @ViewBuilder
    private var watchSection: some View {
        if case .loaded(let digest) = viewModel.digestState {
            let items = digest.watchItemsList
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: Theme.spacingS) {
                    Text("Watch")
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    FlowLayout(spacing: Theme.spacingS) {
                        ForEach(items, id: \.self) { item in
                            Button {
                                viewModel.setFocusIntent(FocusIntent(
                                    target: .crossSection,
                                    model: viewModel.selectedModel,
                                    layerId: Self.watchLayerId(for: item),
                                    pointIndex: viewModel.activePointIndex
                                ))
                            } label: {
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
                .padding(.horizontal, Theme.cardPadding)
            }
        }
    }

    /// Map a watch-item phrase to the cross-section layer it most relates to.
    private static func watchLayerId(for item: String) -> String? {
        let s = item.lowercased()
        if s.contains("ice") || s.contains("icing") { return "icing-ogimet-nwp-bands" }
        if s.contains("conv") || s.contains("storm") || s.contains("cb") || s.contains("cape") { return "thermo-convective-bg" }
        if s.contains("turb") || s.contains("shear") { return "cat-bands" }
        return nil
    }

    // MARK: Advisories — responsive grid (#310 item 2)

    @ViewBuilder
    private var advisoriesSection: some View {
        switch viewModel.advisoriesState {
        case .idle, .loading:
            ProgressView("Loading advisories…")
                .padding(.horizontal, Theme.cardPadding)
        case .loaded(let response):
            let digest = loadedDigest
            let sorted = response.advisories.sorted { severityRank($0.aggregateStatus) > severityRank($1.aggregateStatus) }
            let active = sorted.filter { $0.aggregateStatus == "amber" || $0.aggregateStatus == "red" }
            let greens = sorted.filter { $0.aggregateStatus == "green" }
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                Text("Advisories")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                if active.isEmpty && greens.isEmpty {
                    Text("No advisories for this route.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                }
                // 1 col iPhone, 2–3 cols iPad by width.
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: Theme.spacingM)],
                          alignment: .leading, spacing: Theme.spacingM) {
                    ForEach(active) { advisory in
                        AdvisoryCardView(advisory: advisory, catalog: response.catalog,
                                         hazardNarrative: Self.narrative(for: advisory, catalog: response.catalog, digest: digest),
                                         viewModel: viewModel)
                    }
                }
                if !greens.isEmpty {
                    GreenAdvisoryStrip(advisories: greens, catalog: response.catalog, viewModel: viewModel)
                }
            }
            .padding(.horizontal, Theme.cardPadding)
        case .error(let error):
            ContentUnavailableView("Advisories Unavailable", systemImage: "exclamationmark.triangle",
                                   description: Text(error.localizedDescription))
        }
    }

    private var loadedDigest: DigestResponse? {
        if case .loaded(let digest) = viewModel.digestState { return digest }
        return nil
    }

    /// Attach the matching per-hazard digest narrative to an advisory (#310):
    /// match the advisory's id/category/name against the digest's hazard fields.
    private static func narrative(for advisory: RouteAdvisoryResult,
                                  catalog: [AdvisoryCatalogEntry],
                                  digest: DigestResponse?) -> String? {
        guard let digest else { return nil }
        let entry = catalog.first { $0.id == advisory.advisoryId }
        let hay = [advisory.advisoryId, entry?.category ?? "", entry?.name ?? ""]
            .joined(separator: " ").lowercased()
        if hay.contains("icing") || hay.contains("ice") { return digest.icing }
        if hay.contains("turb") { return digest.turbulence }
        if hay.contains("wind") { return digest.winds }
        if hay.contains("precip") || hay.contains("rain") { return digest.precipitation }
        if hay.contains("vis") { return digest.visibility }
        return nil
    }
}
