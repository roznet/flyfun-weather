import SwiftUI

/// **Since this briefing** (#637) — the live layer's significant changes versus
/// the observations the briefing was built on, at the top of the Advisory tab.
///
/// One row per `LiveChange`, in the server's order (alert tier first, then
/// departure/destination/alternate/route, worse before better): direction arrow,
/// source badge, the server's language-neutral `message`, and the age of the
/// evidence. Alert-tier rows (what the pilot must not miss — destination and
/// pre-departure weather, convective weather ahead, a new SIGMET on the route;
/// meteorology-decisions §36) are emphasised. The layer annotates — it never re-grades the briefing, so the
/// hero's traffic light above is untouched.
///
/// Rendered only when the snapshot carries a live layer (`liveChanges != nil`);
/// an empty list still renders, as an explicit "no significant change" line, so
/// "nothing moved" is distinguishable from "not checked".
struct LiveChangesView: View {
    let changes: LiveChanges
    /// When the briefing (assessment + AI digest) was written.
    let baseline: Date?

    var body: some View {
        // Ages keep counting while the tab stays open.
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                header
                if changes.isEmpty {
                    Text(emptyText)
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("liveChangesEmpty")
                } else {
                    ForEach(changes.items) { change in
                        LiveChangeRow(change: change, now: context.date)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Theme.spacingM)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        }
        .padding(.horizontal, Theme.cardPadding)
        // A containing element, so the section id stays on the section: set on
        // a plain container, SwiftUI copies it onto every child and each row's
        // own `liveChangeRow-<key>` id is lost.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("liveChangesSection")
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Label(
                changes.isFromLiveStart ? "Since live tracking began" : "Since this briefing",
                systemImage: "dot.radiowaves.left.and.right"
            )
                .font(.headline)
                .foregroundStyle(Theme.text)
            Spacer(minLength: 0)
            if let summary = countsText {
                Text(summary)
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }

    /// "2 worse · 1 better". Counted locally from the rows (the server's counts
    /// are optional) so the summary can never disagree with the list.
    private var countsText: String? {
        let worse = changes.items.filter { $0.directionValue == .worse }.count
        let better = changes.items.filter { $0.directionValue == .better }.count
        var parts: [String] = []
        if worse > 0 { parts.append(String(localized: "\(worse) worse")) }
        if better > 0 { parts.append(String(localized: "\(better) better")) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var emptyText: String {
        if changes.isFromLiveStart {
            guard let baseline else { return String(localized: "No significant change since live tracking began") }
            return String(localized: "No significant change since live tracking began (\(LiveTime.zulu(baseline)))")
        }
        guard let baseline else { return String(localized: "No significant change since the briefing") }
        return String(localized: "No significant change since the briefing (\(LiveTime.zulu(baseline)))")
    }
}

/// One change: ↑/↓ arrow, source badge, message, age.
private struct LiveChangeRow: View {
    let change: LiveChange
    let now: Date

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Image(systemName: arrowName)
                .font(.caption.weight(.bold))
                .foregroundStyle(tint)
                .accessibilityLabel(directionLabel)
            Text(change.sourceLabel)
                .font(.caption2.weight(.bold))
                .foregroundStyle(.white)
                .padding(.horizontal, 5)
                .padding(.vertical, 1)
                .background(badgeColor, in: Capsule())
            Text(change.displayMessage)
                .font(change.isAlert ? Font.subheadline.weight(.semibold) : Font.subheadline)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if let age {
                Text(age)
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize()
            }
        }
        .padding(.vertical, change.isAlert ? 6 : 2)
        .padding(.horizontal, change.isAlert ? Theme.spacingS : 0)
        .background(
            change.isAlert ? tint.opacity(Theme.tableRowHighlightOpacity) : Color.clear,
            in: RoundedRectangle(cornerRadius: 8)
        )
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("liveChangeRow-\(change.key)")
        // Tier + direction, so a UI test can assert how a change is rendered
        // (an alert must look like one) and VoiceOver says which it is.
        .accessibilityValue("\(change.isAlert ? "alert" : "highlight"), \(change.direction ?? "")")
    }

    private var arrowName: String {
        switch change.directionValue {
        case .worse: "arrow.up"
        case .better: "arrow.down"
        case nil: "arrow.left.and.right"
        }
    }

    private var directionLabel: String {
        switch change.directionValue {
        case .worse: String(localized: "Worse")
        case .better: String(localized: "Better")
        case nil: String(localized: "Changed")
        }
    }

    /// Worse at an alert-tier airport is red, worse elsewhere orange; better is
    /// green.
    private var tint: Color {
        switch change.directionValue {
        case .worse: change.isAlert ? Theme.red : Color.orange
        case .better: Theme.green
        case nil: Theme.textMuted
        }
    }

    private var badgeColor: Color {
        switch change.sourceLabel {
        case "METAR", "SPECI", "TAF": Theme.primary
        case "SIGMET": Theme.lifr
        case "LIGHTNING", "RADAR": Theme.amber
        default: Theme.textMuted
        }
    }

    private var age: String? {
        guard let observed = change.observedAt.flatMap(Date.parseISO8601) else { return nil }
        return LiveTime.ageLabel(from: observed, now: now)
    }
}

/// The AI-digest caveat (#637): the hero's reason line and the digest were
/// written from the briefing's observations, so once the live layer reports
/// changes say so — "Written at 06:00Z, before 3 changes" — rather than let the
/// prose silently contradict the panel below it.
struct DigestLiveCaveat: View {
    let changeCount: Int
    let writtenAt: Date?

    var body: some View {
        if changeCount > 0 {
            Label(text, systemImage: "clock.arrow.circlepath")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Theme.cardPadding)
                .accessibilityIdentifier("digestLiveCaveat")
        }
    }

    private var text: String {
        let when = writtenAt.map(LiveTime.zulu)
        switch (when, changeCount) {
        case (let when?, 1): return String(localized: "Written at \(when), before 1 change")
        case (let when?, _): return String(localized: "Written at \(when), before \(changeCount) changes")
        case (nil, 1): return String(localized: "Written before 1 change")
        case (nil, _): return String(localized: "Written before \(changeCount) changes")
        }
    }
}
