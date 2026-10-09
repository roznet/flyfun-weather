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
///
/// On the Observed tab with a nutshell (#697) the alert-tier rows are shown
/// above the highlight (`ObservedAlertsCard`) and this panel, inside the
/// "Details" fold, lists the rest (`excludesAlerts`); its counts still cover
/// every row.
struct LiveChangesView: View {
    let changes: LiveChanges
    /// When the briefing (assessment + AI digest) was written.
    let baseline: Date?
    /// Leave the alert-tier rows out: they are already on screen above.
    var excludesAlerts: Bool = false

    private var rows: [LiveChange] {
        excludesAlerts ? changes.items.filter { !$0.isAlert } : changes.items
    }

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
                } else if rows.isEmpty {
                    Text("Alert changes are shown at the top")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    ForEach(rows) { change in
                        LiveChangeRow(change: change, now: context.date)
                    }
                }
                // #669: what cleared on the weather within the hour, plain,
                // below the current rows. The server owns the window.
                if !changes.clearedItems.isEmpty {
                    Divider()
                    ForEach(changes.clearedItems) { change in
                        LiveChangeRow(change: change, now: context.date, cleared: true)
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

/// One change: ↑/↓ arrow, source badge, message, age — and, when it says more
/// than the row (#669), the trail line under it ("12:42–13:02Z, since 13:33Z ·
/// 2nd time today", or a category row's report strip). A recently cleared row
/// is plain (never alert-styled) with "cleared HH:MMZ" in place of the age.
struct LiveChangeRow: View {
    let change: LiveChange
    let now: Date
    var cleared: Bool = false

    /// Alert styling only for a change that is still true.
    private var isAlert: Bool { change.isAlert && !cleared }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
                Image(systemName: arrowName)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(cleared ? Theme.textMuted : tint)
                    .accessibilityLabel(directionLabel)
                Text(change.sourceLabel)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(cleared ? Theme.textMuted : badgeColor, in: Capsule())
                Text(change.displayMessage)
                    .font(isAlert ? Font.subheadline.weight(.semibold) : Font.subheadline)
                    .foregroundStyle(cleared ? Theme.textMuted : Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if let stamp = cleared ? LiveTrailText.cleared(change) : age {
                    Text(stamp)
                        .font(.caption2)
                        .foregroundStyle(Theme.textMuted)
                        .fixedSize()
                }
            }
            if let trail = LiveTrailText.line(change, cleared: cleared) {
                Text(trail)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 22)
            }
        }
        .padding(.vertical, isAlert ? 6 : 2)
        .padding(.horizontal, isAlert ? Theme.spacingS : 0)
        .background(
            isAlert ? tint.opacity(Theme.tableRowHighlightOpacity) : Color.clear,
            in: RoundedRectangle(cornerRadius: 8)
        )
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(cleared ? "liveClearedRow-\(change.key)" : "liveChangeRow-\(change.key)")
        // Tier + direction, so a UI test can assert how a change is rendered
        // (an alert must look like one) and VoiceOver says which it is.
        .accessibilityValue(stateValue)
    }

    /// "alert, worse" / "highlight, better" / "cleared, worse".
    private var stateValue: String {
        let state = cleared ? "cleared" : (change.isAlert ? "alert" : "highlight")
        return "\(state), \(change.direction ?? "")"
    }

    private var arrowName: String {
        switch change.directionValue {
        case .worse: "arrow.up"
        case .better: "arrow.down"
        case .updated, nil: "arrow.left.and.right"
        }
    }

    private var directionLabel: String {
        switch change.directionValue {
        case .worse: String(localized: "Worse")
        case .better: String(localized: "Better")
        case .updated: String(localized: "Updated")
        case nil: String(localized: "Changed")
        }
    }

    /// Worse at an alert-tier airport is red, worse elsewhere orange; better is
    /// green.
    private var tint: Color {
        switch change.directionValue {
        case .worse: change.isAlert ? Theme.red : Color.orange
        case .better: Theme.green
        case .updated, nil: Theme.textMuted
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
        // A future instant has no age: a pending SIGMET's message already says
        // "from HH:MMZ" (#683). Its age shows once it has started.
        guard observed <= now else { return nil }
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
