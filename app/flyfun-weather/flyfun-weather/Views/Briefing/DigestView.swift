import SwiftUI

/// Structured display of the LLM weather digest.
struct DigestView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                switch viewModel.digestState {
                case .idle, .loading:
                    ProgressView("Loading digest...")
                        .frame(maxWidth: .infinity)
                        .padding()
                case .loaded(let digest):
                    digestContent(digest)
                case .error(let error):
                    ContentUnavailableView(
                        "Digest Unavailable",
                        systemImage: "doc.text",
                        description: Text(error.localizedDescription)
                    )
                }
            }
            .padding()
        }
    }

    @ViewBuilder
    private func digestContent(_ digest: DigestResponse) -> some View {
        // Assessment header
        if let assessment = digest.assessment {
            HStack {
                AssessmentStringBadge(status: assessment)
                if let reason = digest.assessmentReason {
                    Text(reason)
                        .font(.subheadline)
                }
                Spacer()
            }
        }

        // Watch items
        if let items = digest.watchItems, !items.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("Watch Items")
                    .font(.headline)
                ForEach(items, id: \.self) { item in
                    Label(item, systemImage: "exclamationmark.circle")
                        .font(.subheadline)
                        .foregroundStyle(.orange)
                }
            }
        }

        // Sections
        ForEach(digest.sections, id: \.title) { section in
            VStack(alignment: .leading, spacing: 4) {
                Text(section.title)
                    .font(.headline)
                Text(section.text)
                    .font(.body)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
