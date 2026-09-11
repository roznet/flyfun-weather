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
    /// Expand one section's flights into rows, emitting each trip once at the
    /// position of its earliest member **in this section**.
    ///
    /// Three rules, each load-bearing:
    ///
    /// 1. **Group before sectioning, emit at the first member.** This mirrors the
    ///    web's placement rule and means a trip appears in the section its
    ///    earliest un-flown leg belongs to, rather than needing a rendering
    ///    special case for a trip that straddles Future and Recent.
    /// 2. **Emit each trip at most once per call**, so a straddling trip whose
    ///    later legs also appear in this section doesn't draw two headers.
    /// 3. **Only *remaining* legs go under the header.** A flown leg stays an
    ///    ordinary row with its trip badge — which is both what bounds the list's
    ///    length and the shrinking scope made visible: the flown leg is precisely
    ///    the one that stopped mattering. It also keeps the Past section honest,
    ///    since Past is server-paginated and a leg pulled into a header could
    ///    otherwise vanish or duplicate depending on which page is loaded.
    ///
    /// A flight whose `trip` names a trip absent from `trips` (an older server, a
    /// trip the list hasn't fetched, or an offline load with no cached trip
    /// document) renders as a plain flight row. Degrading to today's layout is
    /// deliberate: a header with no summary behind it could show no binding chip,
    /// which is the only reason the header exists.
    static func rows(
        for flights: [FlightResponse],
        trips: [TripResponse]
    ) -> [FlightListRow] {
        guard !trips.isEmpty else { return flights.map { .flight($0) } }
        let tripsById = Dictionary(trips.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        // Which legs the *server* says are still remaining. Never re-derived from
        // `departureTime` here: leg state is the server's call (clock time,
        // refined by a debrief), and a second definition of "flown" is exactly
        // the drift the single binding-leg rule exists to prevent.
        var remainingByTrip: [String: Set<String>] = [:]
        for trip in trips {
            remainingByTrip[trip.id] = Set(trip.summary.remainingLegsList.map(\.flightId))
        }

        var emitted: Set<String> = []
        var rows: [FlightListRow] = []
        for flight in flights {
            guard let ref = flight.trip, let trip = tripsById[ref.id] else {
                rows.append(.flight(flight))
                continue
            }
            let remaining = remainingByTrip[trip.id] ?? []
            guard remaining.contains(flight.id) else {
                // A flown/cancelled member: its own row, badge intact.
                rows.append(.flight(flight))
                continue
            }
            if emitted.insert(trip.id).inserted {
                rows.append(.tripHeader(trip))
            }
            rows.append(.tripLeg(flight, tripId: trip.id))
        }
        return rows
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
