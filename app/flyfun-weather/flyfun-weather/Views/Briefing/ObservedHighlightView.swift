import SwiftUI

// The top of the Observed tab once the server sends a nutshell (#697):
//
//   1. highlight   — the model-written line, its caption and 👍/👎; the
//                    nutshell headline stands in its place when there is none;
//   2. alerts      — alert-tier nutshell lines and alert-tier change rows,
//                    never folded;
//   3. ribbon      — (RouteRibbonCard, unchanged);
//   4. Details     — collapsed by default and remembered: the headline, the
//                    other nutshell lines, the other change rows.
//
// Every word is the server's: the client never re-words the highlight, and
// it is never styled as an alert.
//
// SYNC — the highlight block's web counterpart is `highlightHtml` in
// web/ts/visualization/observed/nutshell-view.ts. Documented divergence: the
// web leaves the headline and the other lines unfolded (the desktop has the
// room), and keeps the alert change rows in its page-level "Since this
// briefing" banner.

// MARK: - Alerts

/// Alert-tier nutshell lines, then alert-tier change rows. Renders nothing
/// when there are none.
struct ObservedAlertsCard: View {
    let viewModel: BriefingViewModel
    let glance: LiveGlance
    let changes: LiveChanges?

    private var alertChanges: [LiveChange] { (changes?.items ?? []).filter(\.isAlert) }

    var body: some View {
        if !glance.alertItems.isEmpty || !alertChanges.isEmpty {
            TimelineView(.periodic(from: .now, by: 60)) { context in
                VStack(alignment: .leading, spacing: Theme.spacingS) {
                    ForEach(glance.alertItems) { line in
                        ObservedNutshellLineRow(viewModel: viewModel, line: line)
                    }
                    ForEach(alertChanges) { change in
                        LiveChangeRow(change: change, now: context.date)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Theme.spacingM)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
            }
            .padding(.horizontal, Theme.cardPadding)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("observedAlerts")
        }
    }
}

// MARK: - Highlight

/// The one-glance highlight at body size, then one gray caption line
/// ("Experimental, still being calibrated. Thanks for flagging issues. ·
/// written HH:MMZ") and 👍/👎. With no highlight, the nutshell headline in
/// its place, with no caption and no thumbs.
struct ObservedHighlightCard: View {
    let viewModel: BriefingViewModel
    let glance: LiveGlance

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            if let highlight = glance.highlight {
                Text(highlight.text)
                    .font(.body)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("observedHighlightText")
                Text(Self.caption(highlight))
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("observedHighlightCaption")
                if let packTimestamp = viewModel.liveLayerForPack?.packTimestamp {
                    DigestFeedbackView(target: .highlight(
                        flightId: viewModel.flight.id, packTimestamp: packTimestamp, highlight: highlight
                    ))
                }
            } else if let headline = glance.headline {
                Text(headline)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("observedNutshellHeadline")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedHighlight")
    }

    /// The experimental note, then the written time when it parses: a line
    /// carried forward over unchanged ticks is older than the layer.
    static func caption(_ highlight: LiveHighlight) -> String {
        let note = String(localized: "Experimental, still being calibrated. Thanks for flagging issues.")
        guard let written = highlight.generatedAt.flatMap(Date.parseISO8601) else { return note }
        return note + " · " + String(localized: "written \(LiveTime.zulu(written))")
    }
}

// MARK: - Details fold

/// Everything below the ribbon that a pilot reads only when the top raised a
/// question: the headline (when the highlight took its slot), the non-alert
/// nutshell lines, the non-alert change rows. Collapsed by default; the
/// state is remembered across launches.
///
/// A plain button rather than a `DisclosureGroup`, so the toggle carries an
/// `expanded` / `collapsed` accessibility value a UI test can read before it
/// taps (the remembered state survives between test launches).
struct ObservedDetailsFold: View {
    let viewModel: BriefingViewModel
    let glance: LiveGlance
    let changes: LiveChanges?
    let baseline: Date?
    @AppStorage("observedDetailsExpanded") private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() }
            } label: {
                HStack {
                    Text("Details")
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, Theme.cardPadding)
            .accessibilityIdentifier("observedDetailsToggle")
            .accessibilityValue(expanded ? "expanded" : "collapsed")

            if expanded {
                ObservedNutshellCard(
                    viewModel: viewModel, glance: glance,
                    lines: glance.otherItems, showsHeadline: glance.highlight != nil
                )
                if let changes {
                    LiveChangesView(changes: changes, baseline: baseline, excludesAlerts: true)
                }
            }
        }
    }
}
