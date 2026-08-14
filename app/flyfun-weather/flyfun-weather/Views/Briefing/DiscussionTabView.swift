import SwiftUI

/// The **Discussion** tab (#310): the big-picture synoptic narrative. Renders
/// the digest's four narrative sections — Synoptic, Specific Concerns, Trend and
/// Watch Items — as markdown. Watch is repeated here from the Advisory tab on
/// purpose (it reads as the "what to keep an eye on" close of the discussion).
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

    /// Populated narrative sections, in order — built once and read by both the
    /// spy bar and the content body. Empty for loading/error/empty states.
    private var loadedSections: [(id: String, title: String, text: String)] {
        guard case .loaded(let digest) = viewModel.digestState else { return [] }
        return Self.sections(from: digest)
    }

    /// Spy-bar sections (the bar hides itself when there's fewer than two).
    private var spySections: [SpySection] {
        loadedSections.map { SpySection($0.id, $0.title) }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.digestState {
        case .idle, .loading:
            ProgressView("Generating discussion…")
                .frame(maxWidth: .infinity)
                .padding(.top, Theme.sectionSpacing)
        case .loaded:
            let sections = loadedSections
            if sections.isEmpty {
                ContentUnavailableView("No Discussion", systemImage: "text.alignleft",
                                       description: Text("No discussion is available for this briefing."))
                    .padding(.top, Theme.sectionSpacing)
            } else {
                // Above the prose, mirroring the web placement — the narrative
                // below is what still describes the pack's altitude.
                DigestAltitudeWarning(viewModel: viewModel)
                ForEach(sections, id: \.id) { section in
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
            }
        case .error(let error):
            ContentUnavailableView("Discussion Unavailable", systemImage: "text.alignleft",
                                   description: Text(error.localizedDescription))
                .padding(.top, Theme.sectionSpacing)
        }
    }

    /// The discussion's four narrative sections, in order, dropping any the
    /// digest didn't populate. Each carries a stable id used both as the
    /// `ForEach` key and the scroll-spy anchor/pill id.
    private static func sections(from digest: DigestResponse) -> [(id: String, title: String, text: String)] {
        var result: [(String, String, String)] = []
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
