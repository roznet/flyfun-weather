import SwiftUI

/// What a 👍/👎 strip rates: the briefing's AI digest, or the Observed tab's
/// highlight (#697). Picks the request, the dedup key and the wording.
enum ThumbFeedbackTarget: Equatable {
    case digest(flightId: String, packTimestamp: String)
    /// The rated line itself rides along as the request's `context`.
    case highlight(flightId: String, packTimestamp: String, highlight: LiveHighlight)

    var isHighlight: Bool {
        if case .highlight = self { return true }
        return false
    }
}

/// "Was this helpful? 👍 👎" strip shown below the hero for any briefing that
/// has an AI digest. Tapping a thumb opens a sheet for an optional comment, then
/// posts a digest rating (`POST /api/feedback`, category `digest_rating`).
/// With a `.highlight` target (#697) it is the compact thumb pair under the
/// Observed highlight, posting a `highlight_rating` with the rated line.
///
/// Dedup is session-only via `AppState.ratedDigests` / `ratedHighlights`
/// (matches the web widget) — after a rating the strip collapses to a
/// thank-you for that pack version / that line.
struct DigestFeedbackView: View {
    let target: ThumbFeedbackTarget
    @Environment(AppState.self) private var appState
    @State private var pendingThumb: PendingThumb?

    init(flightId: String, packTimestamp: String) {
        self.target = .digest(flightId: flightId, packTimestamp: packTimestamp)
    }

    init(target: ThumbFeedbackTarget) {
        self.target = target
    }

    /// Identifiable wrapper so a thumb tap can drive `.sheet(item:)` without a
    /// module-wide `String: Identifiable` conformance.
    private struct PendingThumb: Identifiable {
        let sentiment: String
        var id: String { sentiment }
    }

    var body: some View {
        Group {
            if isRated {
                thanksStrip
            } else {
                promptStrip
            }
        }
        // The highlight's strip sits inside its card, which has the padding.
        .padding(.horizontal, target.isHighlight ? 0 : Theme.cardPadding)
        .sheet(item: $pendingThumb) { thumb in
            DigestFeedbackCommentSheet(target: target, sentiment: thumb.sentiment) {
                markRated()
            }
        }
    }

    private var isRated: Bool {
        switch target {
        case .digest(let flightId, let packTimestamp):
            appState.isDigestRated(flightId: flightId, packTimestamp: packTimestamp)
        case .highlight(let flightId, _, let highlight):
            appState.isHighlightRated(flightId: flightId, factsHash: highlight.factsHash ?? highlight.text)
        }
    }

    private func markRated() {
        switch target {
        case .digest(let flightId, let packTimestamp):
            appState.markDigestRated(flightId: flightId, packTimestamp: packTimestamp)
        case .highlight(let flightId, _, let highlight):
            appState.markHighlightRated(flightId: flightId, factsHash: highlight.factsHash ?? highlight.text)
        }
    }

    private var promptStrip: some View {
        HStack(spacing: Theme.spacingM) {
            if !target.isHighlight {
                // The highlight's caption above already asks for flags.
                Text("Was this briefing helpful?")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
            }
            Spacer()
            thumbButton(sentiment: "up", systemImage: "hand.thumbsup", tint: Theme.green)
            thumbButton(sentiment: "down", systemImage: "hand.thumbsdown", tint: Theme.red)
        }
        .padding(.vertical, target.isHighlight ? 0 : Theme.spacingS)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(target.isHighlight ? "highlightFeedback" : "digestFeedback")
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
        .accessibilityIdentifier("\(target.isHighlight ? "highlight" : "digest")Thumb-\(sentiment)")
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
    let target: ThumbFeedbackTarget
    let sentiment: String
    /// Called after a successful submit so the caller can mark the pack rated.
    let onRated: () -> Void

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @State private var comment: String = ""
    @State private var contactOk: Bool = true
    @State private var state: SubmitState = .idle

    private enum SubmitState: Equatable { case idle, sending, failed(String) }

    /// Server caps the feedback comment at 2000 chars (`FeedbackRequest.comment`);
    /// mirror it client-side so a long paste is caught before submit.
    private static let commentMaxLength = 2000
    private var commentTooLong: Bool { comment.count > Self.commentMaxLength }

    private var isPositive: Bool { sentiment == "up" }

    private var placeholder: LocalizedStringKey {
        switch (target.isHighlight, isPositive) {
        case (false, true): "What worked well? (optional)"
        case (false, false): "What was off or missing? (optional)"
        case (true, true): "What was useful in this line? (optional)"
        // The calibration questions (#697): a place, a distance, the lead.
        case (true, false): "What was wrong or missing: a place, a distance, the wrong thing leading? (optional)"
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label(isPositive ? "Marked helpful" : "Marked not helpful",
                          systemImage: isPositive ? "hand.thumbsup.fill" : "hand.thumbsdown.fill")
                        .foregroundStyle(isPositive ? Theme.green : Theme.red)
                }
                Section {
                    TextField(placeholder,
                              text: $comment, axis: .vertical)
                        .lineLimit(3...6)
                } header: {
                    Text("Comment")
                } footer: {
                    HStack {
                        Text("Optional — a thumb on its own is still useful.")
                        Spacer()
                        if comment.count > Self.commentMaxLength - 200 {
                            Text("\(Self.commentMaxLength - comment.count)")
                                .foregroundStyle(commentTooLong ? .red : .secondary)
                        }
                    }
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
                    .disabled(state == .sending || commentTooLong)
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
        let trimmed = comment.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            switch target {
            case .digest(let flightId, let packTimestamp):
                try await repo.submitDigestFeedback(DigestFeedbackRequest(
                    flightId: flightId,
                    packTimestamp: packTimestamp,
                    sentiment: sentiment,
                    comment: trimmed,
                    contactOk: contactOk
                ))
            case .highlight(let flightId, let packTimestamp, let highlight):
                try await repo.submitHighlightFeedback(HighlightFeedbackRequest(
                    flightId: flightId,
                    packTimestamp: packTimestamp,
                    highlight: highlight,
                    sentiment: sentiment,
                    comment: trimmed,
                    contactOk: contactOk
                ))
            }
            onRated()
            dismiss()
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}
