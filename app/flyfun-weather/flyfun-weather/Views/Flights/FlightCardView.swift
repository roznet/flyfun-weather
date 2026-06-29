import SwiftUI

/// Card displaying a flight summary in the list.
struct FlightCardView: View {
    let flight: FlightResponse
    var hasCachedData: Bool = false
    var isOffline: Bool = false

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(flight.shortTitle)
                        .font(.headline)
                        .lineLimit(1)

                    if flight.role == .subscriber {
                        Image(systemName: "person.2")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .accessibilityLabel("Shared flight")
                    }
                }

                Text(flight.waypoints.joined(separator: " - "))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                HStack(spacing: 8) {
                    if let date = flight.departureDate {
                        Label(DateFormatter.shortDate.string(from: date), systemImage: "calendar")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Label("FL\(flight.cruiseAltitudeFt / 100)", systemImage: "arrow.up.right")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if let aircraft = flight.aircraft {
                        Label(aircraft.displayName, systemImage: "airplane")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
            }

            Spacer()

            if hasCachedData {
                Image(systemName: "arrow.down.circle.fill")
                    .foregroundStyle(.green)
                    .font(.caption)
            }

            statusBadge
        }
        .padding(.vertical, 4)
        .opacity(isOffline && !hasCachedData ? 0.4 : 1.0)
        .accessibilityIdentifier("flightCard-\(flight.id)")
    }

    /// Trailing status tag: the GREEN/AMBER/RED traffic light when a short-range
    /// assessment exists, otherwise the soft long-range outlook badge (matches
    /// the web flights card). Nothing when the flight hasn't been briefed.
    @ViewBuilder
    private var statusBadge: some View {
        if let assessment = flight.latestBriefing?.assessment {
            AssessmentStringBadge(status: assessment)
        } else if let outlook = flight.latestBriefing?.outlook {
            OutlookBadge(outlook: outlook)
        }
    }
}

/// Soft tinted badge for a flight's long-range outlook (beyond the GRIB
/// horizon). Deliberately softer than the assessment traffic light — an
/// outlook is a tendency ("what to expect"), not a verdict.
private struct OutlookBadge: View {
    let outlook: String

    private var label: String {
        switch outlook.uppercased() {
        case "TRENDING_SETTLED": "Settled"
        case "TRENDING_UNSETTLED": "Unsettled"
        default: "Mixed"
        }
    }

    private var tint: Color {
        switch outlook.uppercased() {
        case "TRENDING_SETTLED": Theme.green
        case "TRENDING_UNSETTLED": Theme.red
        default: Theme.amber
        }
    }

    var body: some View {
        Text(label)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(tint.opacity(0.15), in: Capsule())
            .overlay(Capsule().stroke(tint.opacity(0.4), lineWidth: 0.5))
            .accessibilityLabel("Outlook: \(label)")
    }
}
