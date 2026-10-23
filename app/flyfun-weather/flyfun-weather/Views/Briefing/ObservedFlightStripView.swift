import SwiftUI

// The flight strip at the top of the Observed tab (#816): where the flight
// is and when it lands (`glance.flight`), always first. Every word is the
// server's (`tasks/live_glance.py::flight_strip`). "Destination at your ETA"
// is the arrival card since #882 (`AirportCardView.swift`).
//
// SYNC — web counterparts: `flightStripHtml` in
// web/ts/visualization/observed/nutshell-view.ts; the arrival card is
// observed/airport-card-view.ts (#881).

// MARK: - Flight strip

struct ObservedFlightStrip: View {
    let flight: LiveGlanceFlight

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Image(systemName: flight.isLanded ? "airplane.arrival" : "airplane")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.primary)
                .accessibilityHidden(true)
            Text(flight.text ?? "")
                .font(.subheadline.weight(flight.phase == "planned" ? .regular : .semibold))
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.spacingM)
        .padding(.vertical, Theme.spacingS)
        .background(
            flight.phase == "planned" ? Theme.surface : Theme.primary.opacity(0.1),
            in: RoundedRectangle(cornerRadius: Theme.cornerRadius)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cornerRadius)
                .stroke(Theme.border, style: StrokeStyle(lineWidth: 0.5, dash: flight.phase == "planned" ? [4, 3] : []))
        )
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("observedFlightStrip")
        .accessibilityValue(flight.phase ?? "")
    }
}
