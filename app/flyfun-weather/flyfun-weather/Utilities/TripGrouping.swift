import Foundation

/// Pure list-shaping rules for trips (#607). No SwiftUI, no repository — so the
/// rules the flight list depends on are unit-testable without a host app.

// MARK: - Flight-list rows

/// One row in the flight list. A trip contributes a header plus one row per
/// remaining leg; everything else contributes a single flight row.
enum FlightListRow: Identifiable, Equatable {
    /// An ungrouped flight, or a member leg rendered on its own (a flown leg in
    /// Recent/Past keeps its `TripBadge` rather than being pulled into the trip).
    case flight(FlightResponse)
    /// A trip's header: chain label, dates, legs-ahead count, binding chip.
    case tripHeader(TripResponse)
    /// A remaining member leg, drawn indented beneath its header.
    case tripLeg(FlightResponse, tripId: String)

    var id: String {
        switch self {
        case .flight(let f): "flight:\(f.id)"
        case .tripHeader(let t): "trip:\(t.id)"
        case .tripLeg(let f, let tripId): "trip:\(tripId):leg:\(f.id)"
        }
    }

    /// The flight this row opens, if it opens one. A header opens a trip instead.
    var flight: FlightResponse? {
        switch self {
        case .flight(let f), .tripLeg(let f, _): f
        case .tripHeader: nil
        }
    }
}

enum TripGrouping {
    /// Rows for a single list of flights — ``sectionRows(for:trips:)`` over one
    /// section.
    static func rows(
        for flights: [FlightResponse],
        trips: [TripResponse]
    ) -> [FlightListRow] {
        sectionRows(for: [flights], trips: trips)[0]
    }

    /// Expand the list's sections into rows in **one pass**, so each trip is drawn
    /// exactly once: a header, then all of its remaining legs.
    ///
    /// Four rules, each load-bearing:
    ///
    /// 1. **One pass over every grouping section, never one call per section.**
    ///    A per-section pass can only dedupe within its section, so a trip with
    ///    remaining legs in both Future and Recent drew two headers. Both
    ///    boundaries are "has the flight ended", so on fresh data they agree —
    ///    but the flights and the trips are fetched separately, and an offline
    ///    trip document can be older than the flight list, so they can disagree.
    /// 2. **The header sits at the trip's earliest remaining leg**, the web's
    ///    placement rule: a trip appears in the section of its earliest un-flown
    ///    leg, and at that leg's position within it.
    /// 3. **The legs are gathered under the header in chain order**, whatever the
    ///    list's sort and wherever else they would fall. Emitting each leg at its
    ///    own list position let an unrelated flight dated between two legs split
    ///    the group, and ran the chain backwards under a furthest-first sort.
    /// 4. **Only *remaining* legs go under the header.** A flown leg stays an
    ///    ordinary row with its trip badge — which is both what bounds the list's
    ///    length and the shrinking scope made visible: the flown leg is precisely
    ///    the one that stopped mattering. Past is not passed in at all: it is
    ///    server-paginated, so a leg pulled into a header could vanish or
    ///    duplicate depending on which page is loaded.
    ///
    /// A section can come back empty when its only flights were gathered under a
    /// header elsewhere; the caller should then skip the section.
    ///
    /// A flight whose `trip` names a trip absent from `trips` (an older server, a
    /// trip the list hasn't fetched, or an offline load with no cached trip
    /// document) renders as a plain flight row. Degrading to today's layout is
    /// deliberate: a header with no summary behind it could show no binding chip,
    /// which is the only reason the header exists.
    static func sectionRows(
        for sections: [[FlightResponse]],
        trips: [TripResponse]
    ) -> [[FlightListRow]] {
        guard !trips.isEmpty else { return sections.map { $0.map { .flight($0) } } }
        let tripsById = Dictionary(trips.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var present: [String: FlightResponse] = [:]
        for flight in sections.joined() { present[flight.id] = flight }

        // Each trip's group: its remaining legs that are in these sections, in
        // the server's chain order. "Remaining" is the server's call (clock time,
        // refined by a debrief) and never re-derived from `departureTime` here —
        // a second definition of "flown" is exactly the drift the single
        // binding-leg rule exists to prevent.
        var groupByTrip: [String: [FlightResponse]] = [:]
        for trip in trips {
            groupByTrip[trip.id] = trip.summary.remainingLegsList.compactMap { leg in
                guard let flight = present[leg.flightId], flight.trip?.id == trip.id else { return nil }
                return flight
            }
        }

        var emitted: Set<String> = []
        return sections.map { flights in
            var rows: [FlightListRow] = []
            for flight in flights {
                guard let ref = flight.trip,
                      let trip = tripsById[ref.id],
                      let group = groupByTrip[ref.id],
                      group.contains(where: { $0.id == flight.id })
                else {
                    // Ungrouped, or a flown/cancelled member: its own row.
                    rows.append(.flight(flight))
                    continue
                }
                // Any other member is drawn under the header, at the anchor.
                guard flight.id == group.first?.id, emitted.insert(trip.id).inserted else {
                    continue
                }
                rows.append(.tripHeader(trip))
                rows.append(contentsOf: group.map { .tripLeg($0, tripId: trip.id) })
            }
            return rows
        }
    }

    /// "2 of 3 legs ahead" for a trip header.
    ///
    /// The total comes from the server's `totalLegs`, not from the rows drawn, so
    /// it stays honest when the header holds fewer legs than the trip has — which
    /// is the normal case once anything has been flown.
    static func legsAheadLabel(for trip: TripResponse) -> String {
        let remaining = trip.summary.remainingLegs
        let total = trip.summary.totalLegs
        if total <= 1 { return remaining == 1 ? "1 leg" : "\(total) legs" }
        return "\(remaining) of \(total) legs ahead"
    }
}

// MARK: - Selection-bar rule

/// What the multi-select sheet needs to know about trips, derived from the
/// picks. A Swift port of `web/ts/helpers/trip-selection.ts` — kept pure and in
/// lock-step with it so the two clients cannot offer different actions for the
/// same selection.
struct TripSelectionContext: Equatable {
    var selectedIds: [String] = []
    /// Picks not currently in any trip — what "Add to …" would move.
    var ungroupedIds: [String] = []
    var memberships: [Membership] = []
    /// Set only when the selection touches exactly one trip. Explicit `= nil` so
    /// the synthesized memberwise initializer gives it a default like its
    /// siblings.
    var singleTrip: Trip? = nil
    /// How many *distinct* trips the selection touches.
    var tripCount: Int = 0

    struct Membership: Equatable {
        let tripId: String
        let flightId: String
    }

    struct Trip: Equatable {
        let id: String
        let name: String
    }

    /// Whether "Group as Trip" applies: nothing picked is already in one.
    var canGroup: Bool { tripCount == 0 && !selectedIds.isEmpty }

    /// Whether "Add to <name>" applies: exactly one trip is represented, and
    /// there is at least one pick not already in it. More than one trip offers
    /// neither action — merging two trips is a different decision the bar has no
    /// way to ask about.
    var canAddToTrip: Bool { tripCount == 1 && !ungroupedIds.isEmpty }

    /// Whether "Remove from Trip" applies. An unlink, never a delete — the label
    /// and confirmation must say so.
    var canRemoveFromTrip: Bool { !memberships.isEmpty }
}

extension TripSelectionContext {
    /// Derive the context from the current picks.
    init(flights: [FlightResponse], selectedIds: Set<String>) {
        let picked = flights.filter { selectedIds.contains($0.id) }
        let inTrips = picked.filter { $0.trip != nil }
        // Distinct trips in first-seen order, so "Add to …" names the one the
        // pilot would expect when the selection is homogeneous.
        var distinct: [(id: String, name: String)] = []
        for flight in inTrips {
            guard let ref = flight.trip else { continue }
            if !distinct.contains(where: { $0.id == ref.id }) {
                distinct.append((ref.id, ref.name))
            }
        }
        // Delegate to the memberwise initializer rather than assigning through
        // `self`: an initializer in an extension must either initialize every
        // stored property or delegate, and delegating keeps that obligation
        // checked by the compiler as fields are added.
        self.init(
            selectedIds: picked.map(\.id),
            ungroupedIds: picked.filter { $0.trip == nil }.map(\.id),
            memberships: inTrips.compactMap { flight in
                flight.trip.map { Membership(tripId: $0.id, flightId: flight.id) }
            },
            singleTrip: distinct.count == 1
                ? Trip(id: distinct[0].id, name: distinct[0].name)
                : nil,
            tripCount: distinct.count
        )
    }
}
