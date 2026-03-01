import SwiftUI

/// Card displaying a flight summary in the list.
struct FlightCardView: View {
    let flight: FlightResponse

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(flight.routeName)
                    .font(.headline)

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
                }
            }

            Spacer()

            if let assessment = flight.assessment {
                AssessmentStringBadge(status: assessment)
            }
        }
        .padding(.vertical, 4)
    }
}

// Extension to get assessment from a FlightResponse — not in server response for flights,
// but will be available after we fetch the pack meta. For now, this is nil.
extension FlightResponse {
    var assessment: String? { nil }
}
