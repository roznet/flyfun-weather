import SwiftUI

/// Card displaying a flight summary in the list.
struct FlightCardView: View {
    let flight: FlightResponse
    var hasCachedData: Bool = false
    var isOffline: Bool = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
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
                        Label("\(DateFormatter.shortDate.string(from: date)) \(DateFormatter.utcTime.string(from: date))",
                              systemImage: "calendar")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Label("FL\(flight.cruiseAltitudeFt / 100)", systemImage: "arrow.up.right")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    Label(String(format: "%.1fh", flight.flightDurationHours), systemImage: "timer")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 6) {
                    if let aircraft = flight.aircraft {
                        Label(aircraft.displayName, systemImage: "airplane")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }

                    if let summary = flight.latestBriefing?.advisorySummary,
                       (summary.red ?? 0) + (summary.amber ?? 0) > 0 {
                        Text("\(summary.red ?? 0) RED · \(summary.amber ?? 0) AMBER")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }

                    if let debrief = flight.debrief {
                        Label(debrief.decision.capitalized, systemImage: "checkmark.circle")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    } else if flight.displaySection == .recent {
                        Label("Debrief", systemImage: "checklist")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Spacer()

            if hasCachedData {
                Image(systemName: "circle.fill")
                    .foregroundStyle(.green)
                    .font(.system(size: 8))
                    .padding(.top, 6)
            }

            if let assessment = flight.latestBriefing?.displayAssessment {
                AssessmentStringBadge(status: assessment)
            } else if flight.latestBriefing?.hasDigest == false {
                Text("PENDING")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .opacity(isOffline && !hasCachedData ? 0.4 : 1.0)
    }
}
