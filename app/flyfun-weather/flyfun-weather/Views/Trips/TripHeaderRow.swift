import SwiftUI

/// The trip's row in the flight list (#607) — chain, dates, legs-ahead, and the
/// binding chip. Its remaining legs are drawn as ordinary rows directly beneath
/// it by `FlightListView`.
///
/// **Why the legs are siblings rather than children.** Observed usage of the web
/// app is that the trip *page* is rarely opened and the expanded card in the list
/// is what gets used, with the common action being "go straight to the leg I want
/// to know more about". Putting the legs behind a navigation step, or behind a
/// disclosure, optimises for a journey that does not happen. Plain sibling rows
/// also avoid nesting `NavigationLink`s inside an expandable container in a
/// `NavigationSplitView` sidebar, which is the fragile corner of the API that
/// `FlightSelectionView`'s doc comment already documents dodging.
struct TripHeaderRow: View {
    let trip: TripResponse
    /// The summary came from an offline cache that has since gone calendar-stale
    /// — a departure has passed, or the UTC day rolled over — so the binding leg
    /// it names may no longer be the one that binds.
    var isStale: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            HStack(spacing: 6) {
                Image(systemName: "point.topleft.down.to.point.bottomright.curvepath")
                    .font(.caption)
                    .foregroundStyle(Theme.primary)
                    .accessibilityHidden(true)
                Text(trip.displayName)
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                if trip.refresh?.active == true {
                    ProgressView().controlSize(.mini)
                }
            }

            Text(trip.summary.chainLabel)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            HStack(spacing: Theme.spacingS) {
                if let range = dateRange {
                    Label(range, systemImage: "calendar")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text(TripGrouping.legsAheadLabel(for: trip))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // The one piece of deliberate redundancy in this layout: the leg
            // badges are visible directly below, but three badges do not say
            // *which one decides the trip* — and working that out by eye is
            // exactly the client-side re-derivation the design forbids.
            TripBindingChip(summary: trip.summary, isStale: isStale)
        }
        .padding(.vertical, 2)
        .accessibilityIdentifier("tripHeaderRow-\(trip.id)")
    }

    /// "7–9 Aug" across the whole chain, flown legs included, so the row
    /// describes the trip rather than only what is left of it.
    private var dateRange: String? {
        let dates = trip.summary.legs.compactMap(\.departureDate).sorted()
        guard let first = dates.first else { return nil }
        let formatter = DateFormatter.shortDate
        guard let last = dates.last, last != first else { return formatter.string(from: first) }
        return "\(formatter.string(from: first)) – \(formatter.string(from: last))"
    }
}

/// Row backgrounds that make a trip read as a *group* in the flight list rather
/// than as one more flight: the header carries a trace of the accent, and each
/// member leg a thin accent rail on its leading edge, which runs unbroken down
/// consecutive legs so they visibly hang off the header.
///
/// The accent, never a grade colour: the trip gets no traffic light
/// (`designs/flight-trips.md`), and a green/amber/red wash on the header would
/// read as exactly that. Both sit over the list's own cell colour so they stay
/// opaque and track light/dark mode. Callers pass `nil` instead while the row is
/// selected, so the iPad sidebar's selection highlight is not painted over.
enum TripRowStyle {
    static var headerBackground: some View {
        ZStack {
            Color(uiColor: .secondarySystemGroupedBackground)
            Theme.primary.opacity(0.07)
        }
    }

    static var legBackground: some View {
        ZStack(alignment: .leading) {
            Color(uiColor: .secondarySystemGroupedBackground)
            Theme.tripRail
                .frame(width: 2)
                .padding(.leading, 18)
        }
    }
}
