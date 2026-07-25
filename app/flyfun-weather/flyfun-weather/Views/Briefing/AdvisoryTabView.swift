import SwiftUI

/// The **Advisory** tab (#310): the read-me-first surface. A prominent accented
/// hero (traffic-light + assessment reason) pinned at the top, then watch chips,
/// a responsive advisory grid (AMBER/RED as cards, GREEN collapsed into one
/// all-clear strip), current airport conditions, — on D-0 — the METAR/TAF
/// observations comparison, and — on marginal D-0/D-2 packs — weather
/// alternates. A sticky scroll-spy bar jumps between sections.
struct AdvisoryTabView: View {
    let viewModel: BriefingViewModel
    @Environment(AppState.self) private var appState
    /// Held in `@State` and created once (in the card's action) so a parent
    /// `body` re-render — frequent during the SSE refresh stream — can't
    /// re-allocate it and wipe in-progress form input. `.sheet(item:)` presents
    /// while it's non-nil.
    @State private var debriefVM: DebriefViewModel?

    var body: some View {
        ScrollSpyScroll(sections: spySections) {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                heroSection
                    .spyAnchor("hero")
                digestFeedbackSection
                debriefSection
                advisoriesSection
                    .spyAnchor("advisories")
                AirportConditionsView(viewModel: viewModel)
                    .spyAnchor("conditions")
                if hasObservations {
                    RouteObservationsView(viewModel: viewModel)
                        .spyAnchor("observations")
                }
                if hasAlternates {
                    AlternatesView(viewModel: viewModel)
                        .spyAnchor("alternates")
                }
                if hasTimingScenarios {
                    TimingScenariosView(viewModel: viewModel)
                        .spyAnchor("timing")
                }
                // Watch reads as the "keep an eye on this" close, so it sits at
                // the very end (#4); anchored internally.
                watchSection
            }
            .padding(.vertical, Theme.cardPadding)
        }
        .background(Theme.bg)
        .sheet(item: $debriefVM) { vm in
            DebriefFormView(viewModel: vm, onFinished: { saved in
                viewModel.setDebrief(saved)
                // Re-sync the flights list so the "Debriefed ✓" glyph and the
                // Recent-debrief nudge update — the list VM persists across
                // navigation, so it won't otherwise re-fetch until foreground.
                appState.signalExternalSync(flightId: viewModel.flight.id)
            })
        }
    }

    /// Build the debrief view model once, at tap time, from the current flagged
    /// categories — then present it via `$debriefVM`. A fresh add is only reached
    /// once advisories are terminal (the button is disabled otherwise), so
    /// `flaggedTagIds` is trustworthy here; the guard is belt-and-suspenders.
    private func presentDebrief() {
        guard let repo = appState.repository else { return }
        guard viewModel.debrief != nil || advisoriesReady else { return }
        debriefVM = DebriefViewModel(
            flight: flightForDebrief,
            taxonomy: appState.helpCatalog.debriefTaxonomy,
            flaggedTagIds: flaggedTagIds,
            repository: repo
        )
    }

    /// The flight with the freshest known debrief folded in, so re-opening the
    /// sheet after a save seeds from the just-saved debrief (the immutable
    /// `viewModel.flight` still carries the list-load value).
    private var flightForDebrief: FlightResponse {
        var f = viewModel.flight
        f.debrief = viewModel.debrief
        return f
    }

    // MARK: Digest feedback (👍/👎) — below the hero, shown once a digest loads

    @ViewBuilder
    private var digestFeedbackSection: some View {
        if hasDigest, let timestamp = viewModel.pack?.fetchTimestamp {
            DigestFeedbackView(flightId: viewModel.flight.id, packTimestamp: timestamp)
        }
    }

    private var hasDigest: Bool {
        if case .loaded = viewModel.digestState { return true }
        return false
    }

    // MARK: Debrief — post-flight judgement (past owned flights only)

    /// The pilot's debrief card: an "Add debrief" prompt or a read-only summary
    /// with "Edit". Only for owned flights that have already ended — matches the
    /// web flight-detail placement (a section under the assessment).
    @ViewBuilder
    private var debriefSection: some View {
        if viewModel.flight.isPast && viewModel.flight.isEditable {
            DebriefCard(debrief: viewModel.debrief, advisoriesReady: advisoriesReady) {
                presentDebrief()
            }
            .padding(.horizontal, Theme.cardPadding)
        }
    }

    /// Advisories have reached a terminal state (loaded or failed) — the point at
    /// which `flaggedTagIds` is trustworthy for a fresh debrief.
    private var advisoriesReady: Bool {
        switch viewModel.advisoriesState {
        case .loaded, .error: return true
        case .idle, .loading: return false
        }
    }

    /// Advisory categories flagged AMBER/RED on the loaded briefing, mapped to
    /// debrief tags — the flown-form outcome rows. Empty until advisories load.
    private var flaggedTagIds: [String] {
        guard case .loaded(let response) = viewModel.advisoriesState else { return [] }
        let pairs = response.advisories.map { (id: $0.advisoryId, status: $0.aggregateStatus) }
        return appState.helpCatalog.debriefTaxonomy.flaggedTagIds(fromAdvisories: pairs)
    }

    // MARK: Scroll-spy sections (only those present)

    private var hasAlternates: Bool {
        if case .loaded(let snapshot) = viewModel.snapshotState, snapshot.alternates != nil { return true }
        return false
    }

    /// D-0 METAR/TAF observations (#492). Gated on an airport actually having
    /// reported — the same filter the section's table applies — so the scroll-spy
    /// never offers an anchor that renders empty.
    private var hasObservations: Bool {
        if case .loaded(let snapshot) = viewModel.snapshotState,
           let obs = snapshot.routeObservations {
            return !obs.reportingAirports.isEmpty
        }
        return false
    }

    private var hasWatchItems: Bool {
        if case .loaded(let digest) = viewModel.digestState { return !digest.watchItemsList.isEmpty }
        return false
    }

    /// The Timing Scenarios panel appears only once it has something to render —
    /// live data (`timeOptions`) or the offline placeholder — so the scroll-spy
    /// doesn't jump to an empty anchor while the first poll is in flight.
    private var hasTimingScenarios: Bool {
        viewModel.showsTimingScenarios && (viewModel.timeOptions != nil || viewModel.timeOptionsOffline)
    }

    private var spySections: [SpySection] {
        var sections = [SpySection("hero", "Summary")]
        sections.append(SpySection("advisories", "Advisories"))
        sections.append(SpySection("conditions", "Conditions"))
        if hasObservations { sections.append(SpySection("observations", "Observations")) }
        if hasAlternates { sections.append(SpySection("alternates", "Alternates")) }
        if hasTimingScenarios { sections.append(SpySection("timing", "Timing")) }
        if hasWatchItems { sections.append(SpySection("watch", "Watch")) }
        return sections
    }

    // MARK: Hero — accented traffic-light + reason (#310 item 3)

    @ViewBuilder
    private var heroSection: some View {
        if let pack = viewModel.pack {
            if let assessment = pack.assessment {
                let severity = Assessment(rawValue: assessment.lowercased()) ?? .unavailable
                // #392: for UNAVAILABLE the server's `assessmentReason` is an
                // internal English diagnostic ("No advisory could be graded — …"),
                // not the LLM's localized prose every other grade carries. Showing
                // it would leak English into fr/de/es, so substitute the localized
                // copy — the same substitution the web banner makes.
                let reason = severity == .unavailable
                    ? String(localized: "We have no usable model data for this route. Treat this as missing information, not as clear weather.")
                    : pack.assessmentReason
                heroCard(accent: severity.color, reason: reason) {
                    AssessmentStringBadge(status: assessment)
                }
            } else if let outlook = pack.outlook {
                // Long-range briefing (beyond the GRIB horizon): no traffic-light
                // verdict yet, only a soft outlook tendency. Mirrors the flight-list
                // card, which already falls back to the outlook badge — without this
                // the hero was blank for far-out flights.
                heroCard(accent: OutlookBadge.tint(for: outlook), reason: pack.outlookReason) {
                    OutlookBadge(outlook: outlook)
                }
            }
        }
    }

    /// Shared accented hero card: a colored side bar, a status badge, and the
    /// reason line. Used by both the short-range assessment and the long-range
    /// outlook so the two can't drift.
    @ViewBuilder
    private func heroCard<Badge: View>(
        accent: Color, reason: String?, @ViewBuilder badge: () -> Badge
    ) -> some View {
        HStack(spacing: 0) {
            Rectangle()
                .fill(accent)
                .frame(width: 5)
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                badge()
                if let reason {
                    Text(reason)
                        .font(Theme.heroLabel)
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(Theme.cardPadding)
            Spacer(minLength: 0)
        }
        .background(accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .padding(.horizontal, Theme.cardPadding)
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
