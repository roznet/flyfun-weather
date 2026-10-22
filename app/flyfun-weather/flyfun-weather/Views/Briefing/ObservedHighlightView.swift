import SwiftUI

// The top of the Observed tab once the server sends a nutshell (#697,
// reordered around the pilot's in-flight questions in #816):
//
//   1. flight strip — where the flight is and its ETA (ObservedFlightStrip);
//   2. alerts       — alert-tier nutshell lines and alert-tier change rows,
//                     never folded;
//   3. highlight    — the model-written line, its caption (written time and
//                     age, orange once stale) and 👍/👎; the nutshell
//                     headline stands in its place when there is none;
//   4. ahead        — the lines not yet passed, unfolded, then "Destination
//                     at your ETA" (DestinationAtETACard);
//   5. ribbon       — (RouteRibbonCard, its marker captioned by the strip);
//   6. Details      — collapsed by default and remembered: the headline, the
//                     passed lines, the other change rows.
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
                // #816: the age, re-read each minute; orange once stale (a
                // line carried forward over unchanged ticks gets old).
                TimelineView(.periodic(from: .now, by: 60)) { context in
                    Text(Self.caption(highlight, now: context.date))
                        .font(.caption)
                        .foregroundStyle(Self.isStale(highlight, now: context.date) ? Color.orange : Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("observedHighlightCaption")
                }
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
    /// carried forward over unchanged ticks is older than the layer. With
    /// `now`, also its age ("written 08:20Z · 12 min ago", #816).
    static func caption(_ highlight: LiveHighlight, now: Date? = nil) -> String {
        let note = String(localized: "Experimental, still being calibrated. Thanks for flagging issues.")
        guard let written = highlight.generatedAt.flatMap(Date.parseISO8601) else { return note }
        var text = note + " · " + String(localized: "written \(LiveTime.zulu(written))")
        if let now { text += " · " + LiveTime.ageLabel(from: written, now: now) }
        return text
    }

    /// Older than the observations' stale age (`LiveTime.staleAfter`).
    static func isStale(_ highlight: LiveHighlight, now: Date) -> Bool {
        guard let written = highlight.generatedAt.flatMap(Date.parseISO8601) else { return false }
        return now.timeIntervalSince(written) > LiveTime.staleAfter
    }
}

// MARK: - Details fold

/// Everything below the ribbon that a pilot reads only when the top raised a
/// question: the headline (when the highlight took its slot), the lines the
/// flight has passed (#816: the lines ahead are unfolded above), the
/// non-alert change rows. Collapsed by default; the state is remembered
/// across launches, except under UI tests, which start collapsed (#803).
///
/// A plain button rather than a `DisclosureGroup`, so the toggle carries an
/// `expanded` / `collapsed` accessibility value a UI test can read.
struct ObservedDetailsFold: View {
    let viewModel: BriefingViewModel
    let glance: LiveGlance
    let changes: LiveChanges?
    let baseline: Date?
    /// Cleared on every UI-test launch (`AppState.setupUITestSession`).
    static let expandedDefaultsKey = "observedDetailsExpanded"
    @AppStorage(ObservedDetailsFold.expandedDefaultsKey) private var expanded = false

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
                let passed = ObservedNutshellCard(
                    viewModel: viewModel, glance: glance,
                    lines: glance.passedItems, showsHeadline: glance.highlight != nil
                )
                if !passed.isEmpty {
                    passed
                }
                if let changes {
                    LiveChangesView(changes: changes, baseline: baseline, excludesAlerts: true)
                }
            }
        }
    }
}
