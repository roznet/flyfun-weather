import SwiftUI

/// Categorized free-text feedback sheet — the iOS twin of the web help page's
/// "Submit Feedback" modal (`help-main.ts::showFeedbackModal`). Posts to
/// `POST /api/feedback` with `target = "general"`, so a report filed here lands
/// in the same admin queue (and triggers the same notification email) as a web one.
///
/// Two entry points:
/// - the flight list's overflow menu — app-level feedback, no briefing context;
/// - a briefing's pack menu — pinned to that flight + pack timestamp (#616), the
///   iOS equivalent of the web briefing page's Feedback button. Without the link
///   a report about a briefing reaches admin with nothing to open.
///
/// Distinct from `DigestFeedbackView`, which is the per-pack 👍/👎 on a briefing's
/// AI digest: that one always carries a `sentiment`; this one requires text.
struct FeedbackFormView: View {
    /// The briefing this report is about, when opened from one. Sent as
    /// `flight_id` / `pack_timestamp`; nil for app-level feedback.
    private let flightId: String?
    private let packTimestamp: String?

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var category: FeedbackCategory = .dataIssue
    @State private var comment: String = ""
    @State private var contactOk: Bool = true
    @State private var state: SubmitState = .idle

    private enum SubmitState: Equatable { case idle, sending, sent, failed(String) }

    init(flightId: String? = nil, packTimestamp: String? = nil) {
        self.flightId = flightId
        self.packTimestamp = packTimestamp
    }

    /// Server caps the comment at 2000 chars (`FeedbackRequest.comment`); mirror
    /// it client-side so a long paste is caught before the round trip.
    private static let commentMaxLength = 2000
    private var trimmedComment: String {
        comment.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    private var commentTooLong: Bool { comment.count > Self.commentMaxLength }
    private var canSubmit: Bool {
        state != .sending && !trimmedComment.isEmpty && !commentTooLong
    }
    private var isAboutBriefing: Bool { flightId != nil && packTimestamp != nil }

    var body: some View {
        NavigationStack {
            Group {
                if state == .sent {
                    thanksView
                } else {
                    form
                }
            }
            .navigationTitle(isAboutBriefing ? "Briefing Feedback" : "Send Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(state == .sent ? "Done" : "Cancel") { dismiss() }
                }
            }
        }
    }

    private var form: some View {
        Form {
            Section {
                Picker("Category", selection: $category) {
                    ForEach(FeedbackCategory.allCases) { option in
                        Text(option.label).tag(option)
                    }
                }
            } footer: {
                if isAboutBriefing {
                    Text("Sent with a link to this briefing, so we can see exactly what you saw.")
                        .accessibilityIdentifier("feedbackBriefingLinkNote")
                } else {
                    Text("Report an issue or suggest an improvement.")
                }
            }

            Section {
                TextField("Describe the issue or suggestion...",
                          text: $comment, axis: .vertical)
                    .lineLimit(4...10)
                    .accessibilityIdentifier("feedbackCommentField")
            } header: {
                Text("Comment")
            } footer: {
                HStack {
                    Spacer()
                    if comment.count > Self.commentMaxLength - 200 {
                        Text("\(Self.commentMaxLength - comment.count)")
                            .foregroundStyle(commentTooLong ? .red : .secondary)
                    }
                }
            }

            Section {
                Toggle("OK to email me a reply about this", isOn: $contactOk)
            }

            Section {
                if case .failed(let message) = state {
                    Text(message)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                Button {
                    Task { await send() }
                } label: {
                    if state == .sending {
                        ProgressView().frame(maxWidth: .infinity)
                    } else {
                        Label("Submit", systemImage: "paperplane.fill")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!canSubmit)
                .accessibilityIdentifier("feedbackSubmitButton")
            }
        }
    }

    private var thanksView: some View {
        ContentUnavailableView {
            Label("Thanks for your feedback!", systemImage: "checkmark.circle.fill")
        } description: {
            Text("Your report has been submitted.")
        }
    }

    private func send() async {
        guard let repo = appState.repository else {
            state = .failed("Not signed in.")
            return
        }
        state = .sending
        let request = GeneralFeedbackRequest(
            category: category,
            comment: trimmedComment,
            contactOk: contactOk,
            flightId: flightId ?? "",
            packTimestamp: packTimestamp ?? ""
        )
        do {
            try await repo.submitGeneralFeedback(request)
            state = .sent
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}
