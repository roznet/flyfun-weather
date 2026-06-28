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
                advisoriesSection
                    .spyAnchor("advisories")
                AirportConditionsView(viewModel: viewModel)
                    .spyAnchor("conditions")
                if hasAlternates {
                    AlternatesView(viewModel: viewModel)
                        .spyAnchor("alternates")
                }
                // Watch reads as the "keep an eye on this" close, so it sits at
                // the very end (#4); anchored internally.
                watchSection
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

    private var hasWatchItems: Bool {
        if case .loaded(let digest) = viewModel.digestState { return !digest.watchItemsList.isEmpty }
        return false
    }

    private var spySections: [SpySection] {
        var sections = [SpySection("hero", "Summary")]
        sections.append(SpySection("advisories", "Advisories"))
        sections.append(SpySection("conditions", "Conditions"))
        if hasAlternates { sections.append(SpySection("alternates", "Alternates")) }
        if hasWatchItems { sections.append(SpySection("watch", "Watch")) }
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

    /// Watch items as readable markdown text (no deep-link — these are just a
    /// list of things to keep an eye on, #4). Rendered last in the tab.
    @ViewBuilder
    private var watchSection: some View {
        if case .loaded(let digest) = viewModel.digestState, let watch = digest.watchItemsMarkdown {
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                Text("Watch")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                DigestMarkdownText(markdown: watch)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Theme.cardPadding)
            .spyAnchor("watch")
        }
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
        // The winds digest is the en-route winds-aloft narrative — don't attach it
        // to a runway crosswind/headwind advisory (a different phenomenon).
        if (hay.contains("wind") || hay.contains("jet")) &&
            !hay.contains("crosswind") && !hay.contains("headwind") { return digest.winds }
        if hay.contains("precip") || hay.contains("rain") { return digest.precipitation }
        if hay.contains("vis") { return digest.visibility }
        return nil
    }
}
