import SwiftUI

/// The chain as a **vertical timeline**, one row per leg with the ground gaps
/// drawn in the connectors between them (#607).
///
/// Vertical rather than the web's horizontal chain strip, and the reason is the
/// device, not taste: a desktop has width to spare, a phone does not, and
/// `MAX_TRIP_LEGS` is 12 — so a horizontal strip turns the one screen whose job
/// is "see the whole chain at once" into a scroll-to-discover. Going vertical
/// also gives `gapHoursBefore` / `sameSortieAsPrevious` a natural home.
///
/// Unlike the flight list, this shows **every** leg including flown ones: the
/// list deliberately holds only what is still ahead, so this is the only place
/// the whole chain is visible.
struct TripTimelineView: View {
    let summary: TripSummary
    var refreshingLegIds: Set<String> = []
    var onOpenLeg: (String) -> Void
    var onRemoveLeg: (TripLeg) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(summary.legs.enumerated()), id: \.element.flightId) { index, leg in
                if index > 0 {
                    GroundGapConnector(leg: leg)
                }
                TripLegRow(
                    leg: leg,
                    isBinding: leg.flightId == summary.bindingLegId,
                    isRefreshing: refreshingLegIds.contains(leg.flightId),
                    onOpen: { onOpenLeg(leg.flightId) },
                    onRemove: { onRemoveLeg(leg) }
                )
            }
        }
        .accessibilityIdentifier("tripTimeline")
    }
}

/// The gap between two legs. Under `SORTIE_GAP_HOURS` (4 h) the two legs are one
/// *sortie* — a fuel or customs stop, not a decision point — so it draws as a
/// thin unlabelled connector; a longer gap gets a label.
struct GroundGapConnector: View {
    let leg: TripLeg

    var body: some View {
        HStack(spacing: Theme.spacingS) {
            Rectangle()
                .fill(Color.secondary.opacity(0.3))
                .frame(width: 2, height: 22)
                .padding(.leading, 5)
            if let label {
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label ?? "Continues")
    }

    private var label: String? {
        guard let hours = leg.gapHoursBefore else { return nil }
        if leg.sameSortieAsPrevious { return "Same sortie" }
        // Negative gaps are possible (overlapping legs the pilot scheduled) and
        // the server reports them as-is rather than clamping, because it is a
        // data problem worth seeing. Say so instead of rendering "-3 h on the
        // ground", which reads like a rendering bug rather than a schedule one.
        if hours < 0 { return "Overlaps the previous leg" }
        if hours >= 20 {
            let nights = max(1, Int((hours / 24).rounded()))
            return nights == 1 ? "1 night on the ground" : "\(nights) nights on the ground"
        }
        return "\(Int(hours.rounded())) h on the ground"
    }
}

/// One leg of the chain.
///
/// The binding leg is marked with an accent rail and a "decides this trip"
/// label. That is rule 1 of this feature rendered in pixels: the *leg* carries
/// the colour, the trip never does.
struct TripLegRow: View {
    let leg: TripLeg
    let isBinding: Bool
    var isRefreshing: Bool = false
    var onOpen: () -> Void
    var onRemove: () -> Void

    private var isPast: Bool { !leg.state.isRemaining }

    var body: some View {
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: Theme.spacingM) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(isBinding ? accent : Color.secondary.opacity(0.3))
                    .frame(width: isBinding ? 4 : 2)

                VStack(alignment: .leading, spacing: Theme.spacingXS) {
                    HStack(spacing: 6) {
                        TripLegStateBadge(state: leg.state)
                        Text(leg.label)
                            .font(.headline)
                            .lineLimit(1)
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }

                    if let date = leg.departureDate {
                        Text("\(DateFormatter.shortDate.string(from: date)) \(DateFormatter.utcTime.string(from: date))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    HStack(spacing: 6) {
                        if isRefreshing {
                            ProgressView().controlSize(.mini)
                        }
                        TripLegBadge(leg: leg)
                        DaysOutChip(daysOut: leg.daysOut)
                        if leg.durationHours > 0 {
                            Text(String(format: "%.1f h", leg.durationHours))
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }

                    TripAdvisoryChips(summary: leg.advisorySummary)

                    if isBinding {
                        Text("Decides this trip")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(accent)
                    }
                }
            }
            .padding(.vertical, Theme.spacingS)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .opacity(isPast ? 0.55 : 1)
        .accessibilityIdentifier("tripLegRow-\(leg.flightId)")
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens this leg's briefing")
        .contextMenu {
            Button(action: onOpen) {
                Label("Open Briefing", systemImage: "doc.text")
            }
            // An unlink, never a delete — and the confirmation it opens says so.
            Button(role: .destructive, action: onRemove) {
                Label("Remove from Trip", systemImage: "minus.circle")
            }
        }
    }

    /// The binding leg's own colour. An outlook uses the softer outlook tint, so
    /// a tendency can never be mistaken for a verdict.
    private var accent: Color {
        if leg.gradeKind == .outlook {
            return OutlookBadge.tint(for: leg.outlook ?? "")
        }
        return Assessment(rawValue: (leg.assessment ?? "").lowercased())?.color
            ?? Color.secondary.opacity(0.5)
    }
}
