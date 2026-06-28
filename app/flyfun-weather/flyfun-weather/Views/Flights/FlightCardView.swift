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

            if let assessment = flight.assessment {
                AssessmentStringBadge(status: assessment)
            }
        }
        .padding(.vertical, 4)
        .opacity(isOffline && !hasCachedData ? 0.4 : 1.0)
        .accessibilityIdentifier("flightCard-\(flight.id)")
    }
}

// Extension to get assessment from a FlightResponse — not in server response for flights,
// but will be available after we fetch the pack meta. For now, this is nil.
extension FlightResponse {
    var assessment: String? { nil }
}
