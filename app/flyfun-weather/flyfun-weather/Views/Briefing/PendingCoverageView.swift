import SwiftUI

/// Shown in place of the tabbed briefing when a flight is saved beyond the
/// forecast horizon (no model data yet). Neutral and informational — it states
/// when weather coverage begins so the pilot knows when to come back, rather
/// than dead-ending on an empty briefing.
struct PendingCoverageView: View {
    let flight: FlightResponse
    let coverage: CoveragePending

    private static let longDate: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEEE d MMMM"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "clock.badge.questionmark")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)

            Text("Saved ahead of the forecast")
                .font(.headline)

            VStack(spacing: 8) {
                if let dep = flight.departureDate {
                    Text("No weather model reaches \(Self.longDate.string(from: dep)) yet.")
                        .foregroundStyle(.secondary)
                }
                if let avail = coverage.availableDay {
                    Text("Forecast coverage begins \(Self.longDate.string(from: avail)).")
                        .fontWeight(.medium)
                }
                if let full = coverage.fullBriefingDay {
                    Text("A full briefing follows from \(Self.longDate.string(from: full)).")
                        .foregroundStyle(.secondary)
                }
                Text("It briefs automatically once it's within range — check back then.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
            }
            .font(.subheadline)
            .multilineTextAlignment(.center)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}
