import AppIntents
import SwiftUI

/// The result card `CheckBriefingIntent` shows in Siri, Shortcuts and
/// Spotlight (#365). Everything on it is already in hand when the intent
/// returns (the cache-first flight and, in the live window, the live layer),
/// so it renders offline and makes no request of its own.
///
/// Layout, top to bottom: route and departure; the live block when there is
/// one (alert lines, then the highlight or headline with its time), set apart
/// from the briefing part below it; the briefing's grade, top advisories and
/// when it was built; then Refresh and Open buttons. The live block never
/// re-grades: the badge is always the briefing's.
struct CheckBriefingSnippetView: View {
    /// What the card draws for one flight. nil `content` is the error card
    /// (signed out, flight not found, load failed): an "Open FlyFun" button
    /// under the spoken line.
    struct Content {
        let flight: FlightResponse
        let live: IntentDialogs.LiveCheckLines?
    }

    let content: Content?

    var body: some View {
        if let content {
            CheckCardFlight(flight: content.flight, live: content.live)
                .padding()
        } else {
            Button(intent: OpenBriefingIntent(flight: nil)) {
                Label("Open FlyFun", systemImage: "airplane")
            }
            .buttonStyle(.bordered)
            .padding()
        }
    }
}

private struct CheckCardFlight: View {
    let flight: FlightResponse
    let live: IntentDialogs.LiveCheckLines?

    private var entity: FlightEntity { FlightEntity(flight) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if let live {
                CheckCardLiveBlock(live: live)
            }
            CheckCardBriefingBlock(flight: flight)
            actions
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(flight.shortTitle)
                .font(.headline)
            if let date = flight.departureDate {
                Label(
                    "\(DateFormatter.shortDate.string(from: date)) \(DateFormatter.utcTime.string(from: date))",
                    systemImage: "calendar"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }

    private var actions: some View {
        HStack {
            Button(intent: RefreshBriefingIntent(flight: entity)) {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            Button(intent: OpenBriefingIntent(flight: entity)) {
                Label("Open Briefing", systemImage: "doc.text")
            }
        }
        .buttonStyle(.bordered)
        .font(.subheadline)
    }
}

/// The flight-day live lines, in the order they are spoken. Server text as is.
private struct CheckCardLiveBlock: View {
    let live: IntentDialogs.LiveCheckLines

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Live", systemImage: "dot.radiowaves.left.and.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(Array(live.alerts.enumerated()), id: \.offset) { _, alert in
                Label(alert, systemImage: "exclamationmark.triangle.fill")
                    .font(.subheadline)
                    .foregroundStyle(.orange)
            }
            if let summary = live.summary {
                Text(summary)
                    .font(.subheadline)
            }
            if let time = live.summaryTime {
                Text("As of \(time)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// The briefing's own part: grade badge and top advisories, or the dialog's
/// empty-state words when there is no grade to draw (not briefed, out of
/// range, `UNAVAILABLE` is not a verdict, outlook only).
private struct CheckCardBriefingBlock: View {
    let flight: FlightResponse

    private var briefing: BriefingStatusInfo? { flight.latestBriefing }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let note = IntentDialogs.briefingNote(flight) {
                Text(note)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else if let assessment = briefing?.assessment {
                HStack(spacing: 8) {
                    AssessmentStringBadge(status: assessment)
                    if let briefing, let time = IntentDialogs.briefingTime(briefing) {
                        Text("Briefing \(time)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(Array((briefing?.advisorySummary?.top ?? []).prefix(3).enumerated()), id: \.offset) { _, chip in
                    HStack(spacing: 6) {
                        Circle()
                            .fill((Assessment(rawValue: chip.status.lowercased()) ?? .unavailable).color)
                            .frame(width: 8, height: 8)
                        Text(chip.name)
                            .font(.subheadline)
                    }
                }
            }
        }
    }
}
