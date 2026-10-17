import SwiftUI

/// The **Discussion** tab (#310): the big-picture synoptic narrative. Renders
/// the digest's four narrative sections — Synoptic, Specific Concerns, Trend and
/// Watch Items — as markdown. Watch lives here only since #661 (it reads as
/// the "what to keep an eye on" close of the discussion).
///
/// Since #799 the web's Discussion extras come along: the surface charts (DWD /
/// Met Office / TEMSI) right after the Synoptic Overview they illustrate, and
/// the GRAMET at the very end, after Watch Items. They come from the pack, not
/// the digest, so they show even while the digest is still generating, failed,
/// or was never requested.
///
/// A sticky scroll-spy pill bar (the same `ScrollSpyScroll` the Advisory tab
/// uses) lets the pilot jump straight to a section instead of hunting through
/// the prose (#5, iOS feedback). The bar auto-suppresses when only one section
/// is present.
struct DiscussionTabView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        ScrollSpyScroll(sections: spySections) {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    private typealias NarrativeSection = (id: String, title: String, text: String)

    /// Populated narrative sections, in order — built once and read by both the
    /// spy bar and the content body. Empty for loading/error/empty states.
    private var loadedSections: [NarrativeSection] {
        guard case .loaded(let digest) = viewModel.digestState else { return [] }
        return Self.sections(from: digest)
    }

    /// The charts manifest when the Charts section has something to say: charts
    /// to show, or "expected but unavailable" for a route inside coverage.
    private var chartsManifest: PackChartsManifest? {
        guard let manifest = viewModel.pack?.charts,
              !manifest.sources.isEmpty || manifest.inCoverage else { return nil }
        return manifest
    }

    private static let chartsId = "charts"
    private static let grametId = "gramet"

    /// Spy-bar sections in display order (the bar hides itself when there's
    /// fewer than two).
    private var spySections: [SpySection] {
        let narrative = loadedSections
        var result = narrative.prefix { $0.id == "synoptic" }.map { SpySection($0.id, $0.title) }
        if chartsManifest != nil { result.append(SpySection(Self.chartsId, "Charts")) }
        result += narrative.drop { $0.id == "synoptic" }.map { SpySection($0.id, $0.title) }
        if viewModel.pack != nil { result.append(SpySection(Self.grametId, "GRAMET")) }
        return result
    }

    @ViewBuilder
    private var content: some View {
        let sections = loadedSections
        let synoptic = sections.prefix { $0.id == "synoptic" }
        let rest = sections.drop { $0.id == "synoptic" }

        digestStatus(sectionsEmpty: sections.isEmpty)
        if !sections.isEmpty {
            // Above the prose, mirroring the web placement — the narrative
            // below is what still describes the pack's altitude.
            DigestAltitudeWarning(viewModel: viewModel)
        }
        ForEach(Array(synoptic), id: \.id) { narrativeBlock($0) }

        if let manifest = chartsManifest {
            Group {
                if manifest.sources.isEmpty {
                    SurfaceChartsUnavailableView()
                } else {
                    SurfaceChartsView(viewModel: viewModel, manifest: manifest)
                }
            }
            .spyAnchor(Self.chartsId)
        }

        ForEach(Array(rest), id: \.id) { narrativeBlock($0) }

        if let pack = viewModel.pack {
            GrametSectionView(viewModel: viewModel, hasGramet: pack.hasGramet)
                .spyAnchor(Self.grametId)
        }
    }

    /// Loading / error / empty state for the digest. Inline (not a full-page
    /// placeholder) because the charts and GRAMET still render below it.
    @ViewBuilder
    private func digestStatus(sectionsEmpty: Bool) -> some View {
        switch viewModel.digestState {
        case .idle, .loading:
            ProgressView("Generating discussion…")
                .frame(maxWidth: .infinity)
        case .loaded:
            if sectionsEmpty {
                ContentUnavailableView("No Discussion", systemImage: "text.alignleft",
                                       description: Text("No discussion is available for this briefing."))
            }
        case .error(let error):
            ContentUnavailableView("Discussion Unavailable", systemImage: "text.alignleft",
                                   description: Text(error.localizedDescription))
        }
    }

    private func narrativeBlock(_ section: NarrativeSection) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text(section.title)
                .font(.headline)
                .foregroundStyle(Theme.text)
            MarkdownLiteText(markdown: section.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .spyAnchor(section.id)
    }

    /// The discussion's four narrative sections, in order, dropping any the
    /// digest didn't populate. Each carries a stable id used both as the
    /// `ForEach` key and the scroll-spy anchor/pill id.
    private static func sections(from digest: DigestResponse) -> [NarrativeSection] {
        var result: [NarrativeSection] = []
        func add(_ id: String, _ title: String, _ text: String?) {
            if let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                result.append((id, title, text))
            }
        }
        add("synoptic", "Synoptic Overview", digest.synoptic)
        add("concerns", "Specific Concerns", digest.specificConcerns)
        add("trend", "Trend", digest.trend)
        add("watch", "Watch Items", digest.watchItemsMarkdown)
        return result
    }
}
