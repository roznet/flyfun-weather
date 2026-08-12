import SwiftUI

/// The release stream ("What's New"), reachable from the More menu alongside
/// Help and Send Feedback.
///
/// Renders `AppState.whatsNew` — the same unified stream the web help page shows,
/// with one deliberate omission: the **install call to action** that web entries
/// in the `app_release` category carry. That is web-only chrome; a reader already
/// inside the app has nothing to install. Emitting it from the category rather
/// than baking it into each entry's body is what makes it a per-client decision.
///
/// Opening the view marks the stream seen. The seen-pointer is one value per
/// user shared with the web app, so this also clears the web's nav dot — the same
/// cross-surface behaviour as the briefing badge.
struct WhatsNewView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    /// Ids of entries the user has expanded, plus the newest one (expanded by
    /// default, matching the web card list).
    ///
    /// Held as the reader's *explicit* choices rather than a plain expanded-set,
    /// so "newest is open" stays derived. Accumulating into a set left the old
    /// and new newest both open when a refresh surfaced a fresh top entry.
    @State private var userOpened: Set<Int> = []
    @State private var userClosed: Set<Int> = []

    var body: some View {
        NavigationStack {
            Group {
                if appState.whatsNew.messages.isEmpty {
                    empty
                } else {
                    list
                }
            }
            .navigationTitle("What's New")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .background(Theme.bg)
        }
        .task {
            // Refresh so a just-published entry appears without waiting for the
            // next foreground, then mark the stream seen (clearing the dot here
            // and on the web). Nothing to expand — `isExpanded` derives it.
            await appState.refreshWhatsNew()
            await appState.markWhatsNewSeen()
        }
    }

    /// Whether an entry's body is showing: the reader's explicit choice if they
    /// made one, else open for the newest entry and closed for the rest.
    /// `messages` is newest-first (`GET /api/messages` orders by date desc).
    private func isExpanded(_ message: SystemMessage) -> Bool {
        Self.isExpanded(id: message.id,
                        newestId: appState.whatsNew.messages.first?.id,
                        opened: userOpened, closed: userClosed)
    }

    /// The rule, extracted so it can be tested: an explicit choice wins, else
    /// only the newest entry is open. Deriving rather than accumulating is what
    /// keeps a refresh that surfaces a new top entry from leaving two open.
    nonisolated static func isExpanded(id: Int, newestId: Int?,
                                       opened: Set<Int>, closed: Set<Int>) -> Bool {
        if opened.contains(id) { return true }
        if closed.contains(id) { return false }
        return id == newestId
    }

    private func toggle(_ message: SystemMessage) {
        if isExpanded(message) {
            userOpened.remove(message.id)
            userClosed.insert(message.id)
        } else {
            userClosed.remove(message.id)
            userOpened.insert(message.id)
        }
    }

    private var list: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Theme.spacingM) {
                ForEach(appState.whatsNew.messages) { message in
                    card(message)
                }
            }
            .padding(Theme.cardPadding)
        }
    }

    private func card(_ message: SystemMessage) -> some View {
        let isExpanded = isExpanded(message)
        return VStack(alignment: .leading, spacing: Theme.spacingS) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { toggle(message) }
            } label: {
                VStack(alignment: .leading, spacing: Theme.spacingXS) {
                    HStack(spacing: Theme.spacingS) {
                        categoryChip(message)
                        Spacer(minLength: 0)
                        Text(message.displayDate)
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                        Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                    }
                    Text(message.title)
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .buttonStyle(.plain)
            .accessibilityHint(isExpanded ? "Collapse" : "Expand")

            if isExpanded {
                // Authored text: it already carries real newlines, so the LLM
                // run-on-list heuristic is off; `- ` lines become real bullets.
                MarkdownLiteText(
                    markdown: message.body,
                    normalizeRunOnLists: false,
                    bulletLists: true
                )
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cornerRadius)
                .stroke(Theme.border, lineWidth: 1)
        )
    }

    private func categoryChip(_ message: SystemMessage) -> some View {
        Text(message.categoryLabel)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(chipColor(message.category))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(chipColor(message.category).opacity(0.12), in: Capsule())
    }

    private func chipColor(_ category: String) -> Color {
        switch category {
        case "feature": Theme.green
        case "change": Theme.primary
        case "fix": Theme.amber
        case "app_release": Theme.lifr
        default: Theme.textMuted
        }
    }

    private var empty: some View {
        ContentUnavailableView(
            appState.whatsNew.hasLoaded ? "No Updates Yet" : "Updates Unavailable Offline",
            systemImage: appState.whatsNew.hasLoaded ? "sparkles" : "wifi.slash",
            description: Text(
                appState.whatsNew.hasLoaded
                    ? "New features and changes will show up here."
                    : "Connect once to download the release notes, then they stay readable offline."
            )
        )
    }
}
