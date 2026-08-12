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
    @State private var expanded: Set<Int> = []

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
            // Expand the newest entry, then refresh so a just-published entry
            // appears without waiting for the next foreground, and mark the
            // stream seen (clearing the dot here and on the web).
            if let newest = appState.whatsNew.messages.first { expanded.insert(newest.id) }
            await appState.refreshWhatsNew()
            if let newest = appState.whatsNew.messages.first { expanded.insert(newest.id) }
            await appState.markWhatsNewSeen()
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
        let isExpanded = expanded.contains(message.id)
        return VStack(alignment: .leading, spacing: Theme.spacingS) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) {
                    if isExpanded { expanded.remove(message.id) } else { expanded.insert(message.id) }
                }
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
