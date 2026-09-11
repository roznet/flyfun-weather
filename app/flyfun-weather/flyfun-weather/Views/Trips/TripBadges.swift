import SwiftUI

/// Badges for a trip leg (#607).
///
/// **Four non-gradeable states, not three.** `unavailable` (briefed, but the
/// pack came back ungradeable) and `needsBriefing` (never briefed) are different
/// facts implying different actions — telling a pilot a leg has no briefing when
/// it has one that failed to grade is simply false — so they get distinct
/// labels. `pendingCoverage` is a third: no model reaches the date yet.
///
/// A leg beyond the GRIB horizon renders an **outlook**, never a traffic light.
/// The two are separate ladders by design: an outlook is a tendency, a traffic
/// light is a verdict, and folding one into the other is the main way this
/// feature fails.
struct TripLegBadge: View {
    let leg: TripLeg

    var body: some View {
        switch leg.gradeKind {
        case .assessment:
            AssessmentStringBadge(status: leg.assessment ?? "unavailable")
        case .outlook:
            OutlookBadge(outlook: leg.outlook ?? "MIXED_SIGNALS")
        case .pendingCoverage:
            NeutralTripBadge(label: "Pending", accessibility: "Pending: no model reaches this date yet")
        case .needsBriefing:
            NeutralTripBadge(label: "No briefing", accessibility: "No briefing yet")
        case .unavailable, .unknown:
            NeutralTripBadge(
                label: "Not assessed",
                accessibility: "Briefed, but the forecast could not be assessed"
            )
        }
    }
}

/// Grey chip for a leg that carries neither a verdict nor a tendency. Grey on
/// purpose: it is a "check back later", not a grade.
struct NeutralTripBadge: View {
    let label: String
    var accessibility: String?

    var body: some View {
        Text(label)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.secondary.opacity(0.12), in: Capsule())
            .overlay(Capsule().stroke(Color.secondary.opacity(0.3), lineWidth: 0.5))
            .accessibilityLabel(accessibility ?? label)
    }
}

/// "D-5" beside a badge.
///
/// Always shown next to a grade, because a D-7 amber and a D-1 amber are not the
/// same claim and on a phone there is no hover to recover the difference.
struct DaysOutChip: View {
    let daysOut: Int?

    var body: some View {
        if let daysOut {
            Text("D-\(daysOut)")
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.secondary)
                .accessibilityLabel("\(daysOut) days out")
        }
    }
}

/// Leg state, shown only when it is *not* `remaining` — the default needs no
/// label, and labelling it would add noise to every row.
struct TripLegStateBadge: View {
    let state: TripLegState

    var body: some View {
        switch state {
        case .flown:
            NeutralTripBadge(label: "Flown")
        case .cancelled:
            NeutralTripBadge(label: "Cancelled")
        case .monitoring, .remaining, .unknown:
            EmptyView()
        }
    }
}

/// The advisory chips the server already ranked (capped at 3 server-side).
struct TripAdvisoryChips: View {
    let summary: AdvisorySummary?

    var body: some View {
        if let top = summary?.top, !top.isEmpty {
            HStack(spacing: 4) {
                ForEach(Array(top.enumerated()), id: \.offset) { _, chip in
                    Text(chip.name)
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(
                            (chip.status.uppercased() == "RED" ? Theme.red : Theme.amber)
                                .opacity(0.15),
                            in: Capsule()
                        )
                        .foregroundStyle(chip.status.uppercased() == "RED" ? Theme.red : Theme.amber)
                }
            }
        }
    }
}

/// The binding-leg chip: **which leg decides this trip**, with that leg's own
/// grade.
///
/// This is the one thing a trip header must carry, and the reason it is not
/// redundant beside visible leg badges: three badges do not say which one
/// decides, and working it out by eye is exactly the client-side re-derivation
/// the design forbids. Note it renders the *leg's* colour — there is deliberately
/// no colour for the trip.
struct TripBindingChip: View {
    let summary: TripSummary
    /// Set when the summary came from an offline cache that has since gone
    /// calendar-stale, in which case the binding leg may no longer be the one
    /// that binds. See `TripListCache.isCalendarStale(now:)`.
    var isStale: Bool = false

    var body: some View {
        if isStale {
            NeutralTripBadge(
                label: "Offline",
                accessibility: "Offline — the deciding leg may have changed"
            )
        } else if let leg = summary.bindingLeg {
            HStack(spacing: 4) {
                Text("Decided by \(leg.label)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                TripLegBadge(leg: leg)
                DaysOutChip(daysOut: leg.daysOut)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(summary.headline)
        } else {
            NeutralTripBadge(
                label: "No deciding leg yet",
                accessibility: summary.headline.isEmpty
                    ? "No leg of this trip can be graded yet"
                    : summary.headline
            )
        }
    }
}
