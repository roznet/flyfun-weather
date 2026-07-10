import SwiftUI

/// Card displaying a flight summary in the list.
struct FlightCardView: View {
    let flight: FlightResponse
    var hasCachedData: Bool = false
    var isOffline: Bool = false
    /// The flight's briefing is queued/refreshing server-side — show a live
    /// "Updating…" tag alongside the (still-current) assessment badge.
    var isRefreshing: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Title row carries the status badge so the metadata row below can
            // use the full card width without being squeezed by the badge.
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

                Spacer()

                if hasCachedData {
                    Image(systemName: "arrow.down.circle.fill")
                        .foregroundStyle(.green)
                        .font(.caption)
                }

                if isRefreshing {
                    HStack(spacing: 4) {
                        ProgressView().controlSize(.small)
                        Text("Updating…")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Briefing updating")
                }

                statusBadge
            }

            Text(flight.waypoints.joined(separator: " - "))
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            HStack(spacing: 8) {
                if let date = flight.departureDate {
                    Label(
                        "\(DateFormatter.shortDate.string(from: date)) \(DateFormatter.utcTime.string(from: date))",
                        systemImage: "calendar"
                    )
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
            .lineLimit(1)
        }
        .padding(.vertical, 4)
        .opacity(isOffline && !hasCachedData ? 0.4 : 1.0)
        .accessibilityIdentifier("flightCard-\(flight.id)")
    }

    /// Trailing status tag: a neutral "Pending" chip when the flight is saved
    /// beyond the forecast horizon (no data yet), else the GREEN/AMBER/RED
    /// traffic light when a short-range assessment exists, else the soft
    /// long-range outlook badge (matches the web flights card). Nothing when the
    /// flight hasn't been briefed.
    @ViewBuilder
    private var statusBadge: some View {
        if let coverage = flight.coverage {
            PendingCoverageBadge(coverage: coverage)
        } else if let assessment = flight.latestBriefing?.assessment {
            AssessmentStringBadge(status: assessment)
        } else if let outlook = flight.latestBriefing?.outlook {
            OutlookBadge(outlook: outlook)
        }
    }
}

/// Neutral pending-coverage chip for a flight saved beyond the forecast horizon.
/// Reads "Pending · dd MMM" (the date coverage begins). Deliberately grey — it
/// is a "check back later", not a verdict or a tendency.
private struct PendingCoverageBadge: View {
    let coverage: CoveragePending

    private var dateText: String? {
        coverage.availableDay.map { DateFormatter.shortDate.string(from: $0) }
    }

    private var label: String {
        if let dateText { return "Pending · \(dateText)" }
        return "Pending"
    }

    private var accessibilityText: String {
        if let dateText { return "Pending, available \(dateText)" }
        return "Pending"
    }

    var body: some View {
        Text(label)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.secondary.opacity(0.12), in: Capsule())
            .overlay(Capsule().stroke(Color.secondary.opacity(0.3), lineWidth: 0.5))
            .accessibilityLabel(accessibilityText)
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
