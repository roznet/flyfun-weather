//
//  TripTests.swift
//  flyfun-weatherTests
//
//  Pure-logic coverage for the iOS trip port (#607).
//
//  Everything here is deliberately off the network and off the MainActor where
//  it can be: the rules being pinned are the ones the UI depends on and that a
//  reviewer cannot check by eye — row emission, badge selection, the selection
//  rule, link/push routing, and the two clocks a cached summary decays on.
//

import Foundation
import Testing
@testable import flyfun_weather

// MARK: - Builders

private enum TripFixture {
    /// A leg. Defaults describe an ordinary graded, remaining leg.
    static func leg(
        id: String,
        label: String = "EGTF → LSGS",
        departure: String = "2099-08-07T08:00:00Z",
        state: TripLegState = .remaining,
        gradeKind: TripGradeKind = .assessment,
        assessment: String? = "GREEN",
        outlook: String? = nil,
        daysOut: Int? = 3,
        gapHoursBefore: Double? = nil,
        sameSortie: Bool = false
    ) -> TripLeg {
        TripLeg(
            flightId: id,
            label: label,
            departureTime: departure,
            state: state,
            gradeKind: gradeKind,
            assessment: assessment,
            outlook: outlook,
            daysOut: daysOut,
            gapHoursBefore: gapHoursBefore,
            sameSortieAsPrevious: sameSortie
        )
    }

    static func trip(
        id: String = "trip-1",
        name: String = "Alps weekend",
        legs: [TripLeg],
        bindingLegId: String? = nil,
        headline: String = "Sunday's LSGS → EGTF decides this trip.",
        aiSummary: String? = nil,
        aiSummaryStale: Bool = false
    ) -> TripResponse {
        TripResponse(
            id: id,
            userId: "u1",
            name: name,
            summary: TripSummary(
                tripId: id,
                name: name,
                legs: legs,
                totalLegs: legs.count,
                remainingLegs: legs.filter { $0.state.isRemaining }.count,
                bindingLegId: bindingLegId,
                chainLabel: "EGTF → LSGS → EGTF",
                headline: headline
            ),
            aiSummary: aiSummary,
            aiSummaryStale: aiSummaryStale
        )
    }

    /// A flight row, optionally carrying trip membership.
    static func flight(
        id: String,
        departure: String = "2099-08-07T08:00:00Z",
        trip: TripLegRef? = nil
    ) -> FlightResponse {
        FlightResponse(
            id: id, userId: "u1", profileId: nil, aircraftId: nil, aircraft: nil,
            routeName: id, waypoints: ["EGTF", "LSGS"],
            departureTime: departure, targetDate: "2099-08-07", targetTimeUtc: 800,
            cruiseAltitudeFt: 9000, flightCeilingFt: 14000, flightDurationHours: 2,
            private: false, autoRefresh: false, autoRefreshHour: nil,
            createdAt: "2099-07-01T00:00:00Z", latestBriefing: nil, coverage: nil,
            role: nil, flexibility: nil, altDepartureTime: nil,
            trip: trip
        )
    }

    static func ref(
        _ tripId: String = "trip-1",
        name: String = "Alps weekend",
        position: Int = 1,
        total: Int = 2
    ) -> TripLegRef {
        TripLegRef(id: tripId, name: name, position: position, total: total)
    }
}

// MARK: - Flight-list row emission

@Suite("Trip grouping in the flight list")
struct TripGroupingTests {

    @Test("Without trips, every flight is a plain row")
    func plainRowsWhenNoTrips() {
        let flights = [TripFixture.flight(id: "a"), TripFixture.flight(id: "b")]
        let rows = TripGrouping.rows(for: flights, trips: [])
        #expect(rows.count == 2)
        #expect(rows.allSatisfy { if case .flight = $0 { true } else { false } })
    }

    @Test("A trip emits one header followed by its remaining legs")
    func headerThenLegs() {
        let trip = TripFixture.trip(legs: [
            TripFixture.leg(id: "out"),
            TripFixture.leg(id: "back"),
        ])
        let flights = [
            TripFixture.flight(id: "out", trip: TripFixture.ref(position: 1)),
            TripFixture.flight(id: "back", trip: TripFixture.ref(position: 2)),
        ]
        let rows = TripGrouping.rows(for: flights, trips: [trip])
        #expect(rows.count == 3)
        guard case .tripHeader(let header) = rows[0] else {
            Issue.record("first row should be the trip header, got \(rows[0])")
            return
        }
        #expect(header.id == "trip-1")
        #expect(rows[1].flight?.id == "out")
        #expect(rows[2].flight?.id == "back")
    }

    /// The shrinking scope, and the thing that bounds the list's length: a flown
    /// leg is the one that stopped mattering, so it does not sit under the header.
    @Test("A flown leg renders on its own, not under the trip header")
    func flownLegStaysOnItsOwn() {
        let trip = TripFixture.trip(legs: [
            TripFixture.leg(id: "out", state: .flown),
            TripFixture.leg(id: "back", state: .remaining),
        ])
        let flights = [
            TripFixture.flight(id: "out", trip: TripFixture.ref(position: 1)),
            TripFixture.flight(id: "back", trip: TripFixture.ref(position: 2)),
        ]
        let rows = TripGrouping.rows(for: flights, trips: [trip])
        #expect(rows.count == 3)
        // The flown leg comes first in departure order and is a plain row.
        guard case .flight(let flown) = rows[0] else {
            Issue.record("a flown member should render as a plain flight row")
            return
        }
        #expect(flown.id == "out")
        guard case .tripHeader = rows[1] else {
            Issue.record("the header should be emitted at the first remaining leg")
            return
        }
        #expect(rows[2].flight?.id == "back")
    }

    /// A trip whose legs appear more than once in the same section must not draw
    /// two headers — the straddle case the web solved with "emit at the first
    /// member".
    @Test("A trip emits exactly one header even with several legs in the section")
    func singleHeaderPerSection() {
        let trip = TripFixture.trip(legs: [
            TripFixture.leg(id: "a"), TripFixture.leg(id: "b"), TripFixture.leg(id: "c"),
        ])
        let flights = ["a", "b", "c"].map {
            TripFixture.flight(id: $0, trip: TripFixture.ref(total: 3))
        }
        let rows = TripGrouping.rows(for: flights, trips: [trip])
        let headers = rows.filter { if case .tripHeader = $0 { true } else { false } }
        #expect(headers.count == 1)
        #expect(rows.count == 4)
    }

    /// Degrading to today's flat layout is deliberate: a header with no summary
    /// behind it could not draw a binding chip, which is its only reason to exist.
    @Test("A member whose trip wasn't loaded falls back to a plain row")
    func unknownTripFallsBack() {
        let flights = [TripFixture.flight(id: "a", trip: TripFixture.ref("missing"))]
        let rows = TripGrouping.rows(for: flights, trips: [
            TripFixture.trip(id: "other", legs: [TripFixture.leg(id: "z")]),
        ])
        #expect(rows.count == 1)
        #expect(rows[0].flight?.id == "a")
        if case .tripHeader = rows[0] { Issue.record("should not emit a header") }
    }

    @Test("Row ids are unique so the List can diff them")
    func rowIdsAreUnique() {
        let trip = TripFixture.trip(legs: [
            TripFixture.leg(id: "out"), TripFixture.leg(id: "back"),
        ])
        let flights = [
            TripFixture.flight(id: "out", trip: TripFixture.ref()),
            TripFixture.flight(id: "back", trip: TripFixture.ref(position: 2)),
        ]
        let ids = TripGrouping.rows(for: flights, trips: [trip]).map(\.id)
        #expect(Set(ids).count == ids.count)
    }

    /// The count comes from the server's totals, so it stays honest once the
    /// header holds fewer legs than the trip has.
    @Test("Legs-ahead label counts remaining against the server's total")
    func legsAheadLabel() {
        let trip = TripFixture.trip(legs: [
            TripFixture.leg(id: "a", state: .flown),
            TripFixture.leg(id: "b"),
            TripFixture.leg(id: "c"),
        ])
        #expect(TripGrouping.legsAheadLabel(for: trip) == "2 of 3 legs ahead")
    }
}

// MARK: - Selection rule

@Suite("Trip selection bar rule")
struct TripSelectionTests {

    @Test("No picks in a trip offers Group as Trip")
    func groupWhenNoneGrouped() {
        let flights = [TripFixture.flight(id: "a"), TripFixture.flight(id: "b")]
        let ctx = TripSelectionContext(flights: flights, selectedIds: ["a", "b"])
        #expect(ctx.tripCount == 0)
        #expect(ctx.canGroup)
        #expect(!ctx.canAddToTrip)
        #expect(!ctx.canRemoveFromTrip)
    }

    @Test("Exactly one trip represented offers Add to that trip")
    func addWhenOneTrip() {
        let flights = [
            TripFixture.flight(id: "a", trip: TripFixture.ref()),
            TripFixture.flight(id: "b"),
        ]
        let ctx = TripSelectionContext(flights: flights, selectedIds: ["a", "b"])
        #expect(ctx.tripCount == 1)
        #expect(ctx.singleTrip?.id == "trip-1")
        #expect(ctx.canAddToTrip)
        #expect(ctx.canRemoveFromTrip)
        #expect(!ctx.canGroup)
    }

    /// Merging two trips is a different decision the bar has no way to ask about.
    @Test("More than one trip offers neither Group nor Add")
    func neitherWhenManyTrips() {
        let flights = [
            TripFixture.flight(id: "a", trip: TripFixture.ref("t1")),
            TripFixture.flight(id: "b", trip: TripFixture.ref("t2")),
        ]
        let ctx = TripSelectionContext(flights: flights, selectedIds: ["a", "b"])
        #expect(ctx.tripCount == 2)
        #expect(ctx.singleTrip == nil)
        #expect(!ctx.canGroup)
        #expect(!ctx.canAddToTrip)
        // Unlink still applies — it is unambiguous per flight.
        #expect(ctx.canRemoveFromTrip)
    }

    @Test("Every pick already in the one trip offers no Add")
    func noAddWhenNothingToMove() {
        let flights = [
            TripFixture.flight(id: "a", trip: TripFixture.ref()),
            TripFixture.flight(id: "b", trip: TripFixture.ref(position: 2)),
        ]
        let ctx = TripSelectionContext(flights: flights, selectedIds: ["a", "b"])
        #expect(ctx.tripCount == 1)
        #expect(!ctx.canAddToTrip)
        #expect(ctx.memberships.count == 2)
    }

    @Test("An empty selection offers nothing")
    func emptySelection() {
        let ctx = TripSelectionContext(flights: [TripFixture.flight(id: "a")], selectedIds: [])
        #expect(!ctx.canGroup)
        #expect(!ctx.canAddToTrip)
        #expect(!ctx.canRemoveFromTrip)
    }
}

// MARK: - Navigation routing

@Suite("Trip deep links and push routing")
struct TripRoutingTests {

    /// The coalesced trip push carries `trip_id` and deliberately no `flight_id`
    /// — the chain, not one leg, is the unit of attention.
    @Test("A trip push routes to the trip")
    func tripPushRoutes() {
        let nav = PushSupport.pendingNavigation(from: ["trip_id": "trip-1", "trip_name": "Alps"])
        #expect(nav == .trip(id: "trip-1"))
    }

    @Test("A briefing push still routes to the briefing")
    func briefingPushRoutes() {
        let nav = PushSupport.pendingNavigation(from: ["flight_id": "f1"])
        #expect(nav == .briefing(flightId: "f1"))
    }

    /// If a payload ever carried both, the trip must win — a chain refresh that
    /// also named a leg would otherwise land on a leg the client chose.
    @Test("trip_id wins over flight_id")
    func tripWinsOverFlight() {
        let nav = PushSupport.pendingNavigation(from: ["trip_id": "t", "flight_id": "f"])
        #expect(nav == .trip(id: "t"))
    }

    @Test("A silent badge push routes nowhere")
    func silentPushRoutesNowhere() {
        #expect(PushSupport.pendingNavigation(from: ["aps": ["badge": 2]]) == nil)
        #expect(PushSupport.pendingNavigation(from: ["trip_id": ""]) == nil)
    }

    @Test("A /trip.html link opens the trip")
    func tripUniversalLink() {
        let url = URL(string: "https://weather.flyfun.aero/trip.html?id=trip-42")!
        #expect(AppState.navigationTarget(for: url) == .trip(id: "trip-42"))
    }

    @Test("A /trip.html link with no id routes nowhere")
    func tripUniversalLinkWithoutId() {
        #expect(AppState.navigationTarget(
            for: URL(string: "https://weather.flyfun.aero/trip.html")!) == nil)
        #expect(AppState.navigationTarget(
            for: URL(string: "https://weather.flyfun.aero/trip.html?id=")!) == nil)
    }

    @Test("Another host is never routed")
    func foreignHostIgnored() {
        #expect(AppState.navigationTarget(
            for: URL(string: "https://evil.example.com/trip.html?id=x")!) == nil)
    }
}

// MARK: - Offline cache staleness

@Suite("Cached trip summaries decay on two clocks")
struct TripCacheStalenessTests {

    private func cache(fetchedAt: Date, legs: [TripLeg]) -> TripListCache {
        TripListCache(trips: [TripFixture.trip(legs: legs)], fetchedAt: fetchedAt)
    }

    private static let iso = ISO8601DateFormatter()

    @Test("A cache written today with no passed departure is usable")
    func freshCacheIsUsable() {
        let now = Self.iso.date(from: "2026-05-10T12:00:00Z")!
        let subject = cache(
            fetchedAt: now.addingTimeInterval(-3600),
            legs: [TripFixture.leg(id: "a", departure: "2026-05-20T08:00:00Z")]
        )
        #expect(!subject.isCalendarStale(now: now))
    }

    /// Every `D-N` in the headline moves with the calendar, and no push reports
    /// it — so a headline cached yesterday is simply wrong today.
    @Test("A cache from an earlier UTC day is stale")
    func previousDayIsStale() {
        let now = Self.iso.date(from: "2026-05-10T00:30:00Z")!
        let subject = cache(
            fetchedAt: Self.iso.date(from: "2026-05-09T23:30:00Z")!,
            legs: [TripFixture.leg(id: "a", departure: "2026-05-20T08:00:00Z")]
        )
        #expect(subject.isCalendarStale(now: now))
    }

    /// A leg flips remaining → flown from the clock alone, and only remaining
    /// legs can bind — so the binding leg changes identity with no new data.
    @Test("A departure passing since the fetch is stale")
    func passedDepartureIsStale() {
        let now = Self.iso.date(from: "2026-05-10T12:00:00Z")!
        let subject = cache(
            fetchedAt: Self.iso.date(from: "2026-05-10T06:00:00Z")!,
            legs: [TripFixture.leg(id: "a", departure: "2026-05-10T09:00:00Z")]
        )
        #expect(subject.isCalendarStale(now: now))
    }

    @Test("A departure already past when the cache was written is not stale")
    func departureBeforeFetchIsNotStale() {
        let now = Self.iso.date(from: "2026-05-10T12:00:00Z")!
        let subject = cache(
            fetchedAt: Self.iso.date(from: "2026-05-10T10:00:00Z")!,
            legs: [TripFixture.leg(id: "a", departure: "2026-05-10T08:00:00Z")]
        )
        #expect(!subject.isCalendarStale(now: now))
    }
}

// MARK: - Contract details

@Suite("Trip DTO contract")
struct TripDTOTests {

    /// The server may add leg states or grade kinds; a build that doesn't know
    /// one must still render the trip rather than throwing away the whole screen.
    @Test("Unknown enum values decode instead of throwing")
    func unknownEnumsDecode() throws {
        let json = """
        {
          "flight_id": "a", "label": "EGTF → LSGS",
          "departure_time": "2099-08-07T08:00:00Z",
          "state": "teleported", "grade_kind": "vibes"
        }
        """
        let leg = try JSONDecoder.weatherBrief.decode(TripLeg.self, from: Data(json.utf8))
        #expect(leg.state == .unknown)
        #expect(leg.gradeKind == .unknown)
        // An unknown state is not remaining: a leg the client can't classify must
        // not be counted as one that still has to work.
        #expect(!leg.state.isRemaining)
    }

    /// `decidable_from` is a bare `YYYY-MM-DD`, which the shared `.iso8601`
    /// decoder cannot parse — which is why it is a String on the wire.
    @Test("decidable_from parses as a plain UTC day")
    func decidableFromParses() throws {
        var summary = TripSummary(tripId: "t")
        summary.decidableFrom = "2026-09-18"
        let parsed = try #require(summary.decidableFromDate)
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC")!
        let parts = utc.dateComponents([.year, .month, .day], from: parsed)
        #expect(parts.year == 2026)
        #expect(parts.month == 9)
        #expect(parts.day == 18)
    }

    @Test("A malformed decidable_from is nil, not a crash")
    func decidableFromRejectsGarbage() {
        var summary = TripSummary(tripId: "t")
        summary.decidableFrom = "not-a-date"
        #expect(summary.decidableFromDate == nil)
    }

    @Test("The binding leg resolves from the summary's own legs")
    func bindingLegResolves() {
        let trip = TripFixture.trip(
            legs: [TripFixture.leg(id: "out"), TripFixture.leg(id: "back")],
            bindingLegId: "back"
        )
        #expect(trip.summary.bindingLeg?.flightId == "back")
    }

    @Test("A binding id naming no loaded leg resolves to nil")
    func bindingLegMissing() {
        let trip = TripFixture.trip(
            legs: [TripFixture.leg(id: "out")],
            bindingLegId: "ghost"
        )
        #expect(trip.summary.bindingLeg == nil)
    }

    /// The server can return an empty name for a trip created before its default
    /// was derived; the row must not render a blank title.
    @Test("An empty trip name falls back to the chain label")
    func displayNameFallsBack() {
        let trip = TripFixture.trip(name: "", legs: [TripFixture.leg(id: "a")])
        #expect(trip.displayName == "EGTF → LSGS → EGTF")
    }

    /// Both per-flight refresh paths refuse a claimed leg, and the app must tell
    /// that apart from an ordinary already-refreshing 409 — the retry policy and
    /// the banner styling both depend on it.
    @Test("The trip-claim refusal is recognised on both paths")
    func tripClaimRecognised() {
        let sentence = "This flight is part of a trip refresh that is already running. Wait for the trip to finish."
        #expect(TripRefreshConflict.matches(message: sentence))
        #expect(TripRefreshConflict.matches(error: APIError.serverError(409, sentence)))
    }

    @Test("An ordinary 409 is not a trip claim")
    func ordinaryConflictIsNotATripClaim() {
        #expect(!TripRefreshConflict.matches(
            error: APIError.serverError(409, "A refresh is already in progress for this flight.")))
        #expect(!TripRefreshConflict.matches(message: nil))
        #expect(!TripRefreshConflict.matches(error: APIError.notFound))
    }
}
