import SwiftUI

/// The **Discussion** tab (#310): the big-picture synoptic narrative. v1 ships
/// synopsis text only — surface-pressure & front charts are a deferred
/// fast-follow (see the issue's Phasing). Per-hazard digest narrative lives on
/// the matching advisory card (Advisory tab), keeping Discussion big-picture.
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
            if let synopsis = digest.synopsis, !synopsis.isEmpty {
                VStack(alignment: .leading, spacing: Theme.spacingS) {
                    Text("Synoptic Overview")
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    Text(synopsis)
                        .font(.body)
                        .foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, Theme.cardPadding)
            } else {
                ContentUnavailableView("No Discussion", systemImage: "text.alignleft",
                                       description: Text("No synoptic overview is available for this briefing."))
                    .padding(.top, Theme.sectionSpacing)
            }
        case .error(let error):
            ContentUnavailableView("Discussion Unavailable", systemImage: "text.alignleft",
                                   description: Text(error.localizedDescription))
                .padding(.top, Theme.sectionSpacing)
        }
    }
}
