import SwiftUI

/// "Was this helpful? 👍 👎" strip shown below the hero for any briefing that
/// has an AI digest. Tapping a thumb opens a sheet for an optional comment, then
/// posts a digest rating (`POST /api/feedback`, category `digest_rating`).
///
/// Dedup is session-only via `AppState.ratedDigests` (matches the web widget) —
/// after a rating the strip collapses to a thank-you for that pack version.
struct DigestFeedbackView: View {
    let flightId: String
    let packTimestamp: String
    @Environment(AppState.self) private var appState
    @State private var pendingThumb: PendingThumb?

    /// Identifiable wrapper so a thumb tap can drive `.sheet(item:)` without a
    /// module-wide `String: Identifiable` conformance.
    private struct PendingThumb: Identifiable {
        let sentiment: String
        var id: String { sentiment }
    }

    var body: some View {
        Group {
            if appState.isDigestRated(flightId: flightId, packTimestamp: packTimestamp) {
                thanksStrip
            } else {
                promptStrip
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .sheet(item: $pendingThumb) { thumb in
            DigestFeedbackCommentSheet(
                flightId: flightId,
                packTimestamp: packTimestamp,
                sentiment: thumb.sentiment
            ) {
                appState.markDigestRated(flightId: flightId, packTimestamp: packTimestamp)
            }
        }
    }

    private var promptStrip: some View {
        HStack(spacing: Theme.spacingM) {
            Text("Was this briefing helpful?")
                .font(.subheadline)
                .foregroundStyle(Theme.textMuted)
            Spacer()
            thumbButton(sentiment: "up", systemImage: "hand.thumbsup", tint: Theme.green)
            thumbButton(sentiment: "down", systemImage: "hand.thumbsdown", tint: Theme.red)
        }
        .padding(.vertical, Theme.spacingS)
    }

    private func thumbButton(sentiment: String, systemImage: String, tint: Color) -> some View {
        Button {
            pendingThumb = PendingThumb(sentiment: sentiment)
        } label: {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundStyle(tint)
                .frame(width: 44, height: 44)
                .background(tint.opacity(0.12), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(sentiment == "up" ? "Helpful" : "Not helpful")
    }

    private var thanksStrip: some View {
        HStack(spacing: Theme.spacingS) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(Theme.green)
            Text("Thanks for the feedback!")
                .font(.subheadline)
                .foregroundStyle(Theme.textMuted)
            Spacer()
        }
        .padding(.vertical, Theme.spacingS)
    }
}

/// Optional-comment sheet shown after a thumb tap. Comment is optional (a bare
/// thumb is valid); the consent toggle mirrors the web "you can contact me" box.
private struct DigestFeedbackCommentSheet: View {
    let flightId: String
    let packTimestamp: String
    let sentiment: String
    /// Called after a successful submit so the caller can mark the pack rated.
    let onRated: () -> Void

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @State private var comment: String = ""
    @State private var contactOk: Bool = true
    @State private var state: SubmitState = .idle

    private enum SubmitState: Equatable { case idle, sending, failed(String) }

    private var isPositive: Bool { sentiment == "up" }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label(isPositive ? "Marked helpful" : "Marked not helpful",
                          systemImage: isPositive ? "hand.thumbsup.fill" : "hand.thumbsdown.fill")
                        .foregroundStyle(isPositive ? Theme.green : Theme.red)
                }
                Section {
                    TextField(isPositive ? "What worked well? (optional)"
                                         : "What was off or missing? (optional)",
                              text: $comment, axis: .vertical)
                        .lineLimit(3...6)
                } header: {
                    Text("Comment")
                } footer: {
                    Text("Optional — a thumb on its own is still useful.")
                }
                Section {
                    Toggle("You can contact me about this", isOn: $contactOk)
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
                            Label("Send", systemImage: "paperplane.fill")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(state == .sending)
                }
            }
            .navigationTitle("Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private func send() async {
        guard let repo = appState.repository else {
            state = .failed("Not signed in.")
            return
        }
        state = .sending
        let request = DigestFeedbackRequest(
            flightId: flightId,
            packTimestamp: packTimestamp,
            sentiment: sentiment,
            comment: comment.trimmingCharacters(in: .whitespacesAndNewlines),
            contactOk: contactOk
        )
        do {
            try await repo.submitDigestFeedback(request)
            onRated()
            dismiss()
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}
