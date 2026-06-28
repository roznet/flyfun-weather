import SwiftUI

/// The **Discussion** tab (#310): the big-picture synoptic narrative. Renders
/// the digest's four narrative sections — Synoptic, Specific Concerns, Trend and
/// Watch Items — as markdown. Watch is repeated here from the Advisory tab on
/// purpose (it reads as the "what to keep an eye on" close of the discussion).
struct DiscussionTabView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.digestState {
        case .idle, .loading:
            ProgressView("Generating discussion…")
                .frame(maxWidth: .infinity)
                .padding(.top, Theme.sectionSpacing)
        case .loaded(let digest):
            let sections = Self.sections(from: digest)
            if sections.isEmpty {
                ContentUnavailableView("No Discussion", systemImage: "text.alignleft",
                                       description: Text("No discussion is available for this briefing."))
                    .padding(.top, Theme.sectionSpacing)
            } else {
                ForEach(sections, id: \.title) { section in
                    VStack(alignment: .leading, spacing: Theme.spacingS) {
                        Text(section.title)
                            .font(.headline)
                            .foregroundStyle(Theme.text)
                        DigestMarkdownText(markdown: section.text)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, Theme.cardPadding)
                }
            }
        case .error(let error):
            ContentUnavailableView("Discussion Unavailable", systemImage: "text.alignleft",
                                   description: Text(error.localizedDescription))
                .padding(.top, Theme.sectionSpacing)
        }
    }

    /// The discussion's four narrative sections, in order, dropping any the
    /// digest didn't populate.
    private static func sections(from digest: DigestResponse) -> [(title: String, text: String)] {
        var result: [(String, String)] = []
        func add(_ title: String, _ text: String?) {
            if let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                result.append((title, text))
            }
        }
        add("Synoptic Overview", digest.synoptic)
        add("Specific Concerns", digest.specificConcerns)
        add("Trend", digest.trend)
        add("Watch Items", digest.watchItemsMarkdown)
        return result
    }
}
