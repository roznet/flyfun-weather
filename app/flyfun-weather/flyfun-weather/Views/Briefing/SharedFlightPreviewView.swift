import SwiftUI

/// Preview of a shared flight opened from a `/s/{code}` link (#446).
///
/// Shows the full briefing (reusing `BriefingContainerView`) beneath a banner
/// that names the owner and offers a Subscribe / Unsubscribe button. Opening a
/// shared link deliberately does **not** auto-subscribe — it's common to look
/// once without adding the flight to your list — so the resolved flight lives in
/// local `@State` and the banner button flips on `is_subscribed` without
/// re-resolving. Owner-gating in `BriefingContainerView` (`isEditable`) already
/// hides refresh/edit for the subscriber role.
struct SharedFlightPreviewView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    /// The resolved shared flight. Local so a subscribe/unsubscribe can flip the
    /// banner's `hasSubscribed` state in place.
    @State private var flight: FlightResponse
    @State private var busy = false
    @State private var errorMessage: String?

    /// Invoked after a successful subscribe/unsubscribe so the caller can reload
    /// `/api/flights` (the card badge + `isEditable` gating are handled there).
    private let onSubscriptionChanged: () -> Void

    init(flight: FlightResponse, onSubscriptionChanged: @escaping () -> Void) {
        _flight = State(initialValue: flight)
        self.onSubscriptionChanged = onSubscriptionChanged
    }

    var body: some View {
        VStack(spacing: 0) {
            banner
            BriefingContainerView(flight: flight)
        }
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
        .alert(
            "Couldn’t update",
            isPresented: Binding(get: { errorMessage != nil },
                                 set: { if !$0 { errorMessage = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(errorMessage ?? "")
        }
    }

    /// "Shared by {owner} · Subscribe" banner. Falls back to a generic label when
    /// the owner has no display name (never the owner's email — the server omits it).
    private var banner: some View {
        HStack(spacing: 12) {
            Image(systemName: "person.2.fill")
                .foregroundStyle(.secondary)
            Text(ownerLabel)
                .font(.subheadline.weight(.medium))
                .lineLimit(1)
            Spacer()
            Button(flight.hasSubscribed ? "Unsubscribe" : "Subscribe") {
                toggleSubscription()
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
            .disabled(busy || appState.repository == nil)
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity)
        .background(.regularMaterial)
    }

    private var ownerLabel: String {
        if let owner = flight.ownerDisplayName, !owner.isEmpty {
            return "Shared by \(owner)"
        }
        return "Shared flight"
    }

    /// Subscribe or unsubscribe (whichever the banner currently offers) and flip
    /// the button optimistically on success. A 409 (own flight) or 404
    /// (private/not-visible) surfaces in the alert.
    private func toggleSubscription() {
        guard let repo = appState.repository, !busy else { return }
        let subscribing = !flight.hasSubscribed
        busy = true
        Task {
            do {
                if subscribing {
                    try await repo.subscribeFlight(id: flight.id)
                } else {
                    try await repo.unsubscribeFlight(id: flight.id)
                }
                flight.isSubscribed = subscribing
                onSubscriptionChanged()
            } catch {
                errorMessage = (error as? APIError)?.errorDescription ?? error.localizedDescription
            }
            busy = false
        }
    }
}
