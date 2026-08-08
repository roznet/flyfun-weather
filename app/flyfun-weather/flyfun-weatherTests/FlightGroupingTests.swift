//
//  FlightGroupingTests.swift
//  flyfun-weatherTests
//
//  Covers the two branches added with the debrief feature (#430):
//  `FlightListView.groupedFlights` (server-`section`-driven vs. legacy 7-day
//  date fallback) and `FlightResponse.flightSection` (the per-card fallback).
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("FlightGrouping")
struct FlightGroupingTests {

    static let now = ISO8601DateFormatter().date(from: "2026-07-15T12:00:00Z")!
    static func iso(_ offsetDays: Double) -> String {
        ISO8601DateFormatter().string(from: now.addingTimeInterval(offsetDays * 86400))
    }

    private func flight(
        _ id: String, days: Double, section: String? = nil, durationHours: Double = 2.0
    ) -> FlightResponse {
        var f = makeFlight(id: id, departureTime: Self.iso(days), flightDurationHours: durationHours)
        f.section = section
        return f
    }

    private func titles(_ groups: [FlightListView.FlightGroup]) -> [String] { groups.map(\.title) }
    private func ids(_ group: FlightListView.FlightGroup?) -> [String] { group?.flights.map(\.id) ?? [] }

    // MARK: - Legacy fallback (no server section)

    @Test("Legacy grouping buckets by date; Recent = departed within 7 days")
    func legacyBuckets() {
        let groups = FlightListView.groupedFlights(
            [flight("future", days: 1), flight("recent", days: -2), flight("old", days: -30)],
            now: Self.now
        )
        #expect(titles(groups) == ["Future", "Recent", "Past"])
        #expect(ids(groups.first { $0.title == "Recent" }) == ["recent"])
        #expect(ids(groups.first { $0.title == "Past" }) == ["old"])
    }

    @Test("Empty groups are dropped")
    func dropsEmptyGroups() {
        let groups = FlightListView.groupedFlights([flight("a", days: 2)], now: Self.now)
        #expect(titles(groups) == ["Future"])
    }

    // MARK: - Server-section driven

    @Test("Server `section` wins over date bucketing")
    func serverSectionWins() {
        // A long-past departure the server nonetheless marks "recent" (e.g. still
        // within the debrief-nudge window) must land in Recent, not Past.
        let groups = FlightListView.groupedFlights(
            [flight("a", days: -100, section: "recent"), flight("b", days: 5, section: "past")],
            now: Self.now
        )
        #expect(ids(groups.first { $0.title == "Recent" }) == ["a"])
        #expect(ids(groups.first { $0.title == "Past" }) == ["b"])
    }

    // MARK: - Upcoming-flights ordering preference (#536)

    /// Future first (furthest → soonest by default), Recent and Past always
    /// most-recent-first. Sections come from the server so the test pins the
    /// ordering alone.
    private var orderingFixture: [FlightResponse] {
        [
            flight("near", days: 2, section: "future"),
            flight("far", days: 9, section: "future"),
            flight("mid", days: 5, section: "future"),
            flight("recent-old", days: -5, section: "recent"),
            flight("recent-new", days: -1, section: "recent"),
            flight("past-old", days: -40, section: "past"),
            flight("past-new", days: -30, section: "past"),
        ]
    }

    @Test("Default (furthest first) keeps Future most-distant-first")
    func futureFurthestFirst() {
        let groups = FlightListView.groupedFlights(orderingFixture, order: .furthestFirst, now: Self.now)
        #expect(ids(groups.first { $0.title == "Future" }) == ["far", "mid", "near"])
    }

    @Test("Soonest first reverses Future only")
    func futureSoonestFirst() {
        let groups = FlightListView.groupedFlights(orderingFixture, order: .soonestFirst, now: Self.now)
        #expect(ids(groups.first { $0.title == "Future" }) == ["near", "mid", "far"])
    }

    @Test("Recent and Past stay most-recent-first under both orders")
    func recentAndPastUnaffected() {
        for order in FlightOrder.allCases {
            let groups = FlightListView.groupedFlights(orderingFixture, order: order, now: Self.now)
            #expect(ids(groups.first { $0.title == "Recent" }) == ["recent-new", "recent-old"])
            #expect(ids(groups.first { $0.title == "Past" }) == ["past-new", "past-old"])
        }
    }

    @Test("Legacy fallback honours the order too")
    func legacyFallbackOrdering() {
        let flights = [flight("near", days: 2), flight("far", days: 9), flight("mid", days: 5)]
        #expect(ids(FlightListView.groupedFlights(flights, order: .soonestFirst, now: Self.now)
            .first { $0.title == "Future" }) == ["near", "mid", "far"])
        #expect(ids(FlightListView.groupedFlights(flights, order: .furthestFirst, now: Self.now)
            .first { $0.title == "Future" }) == ["far", "mid", "near"])
    }

    // MARK: - Duration-aware Future boundary (#536 part 2)

    @Test("An in-progress flight is Future, and leads under soonest-first")
    func inProgressFlightIsFuture() {
        // Departed 30 min ago on a 3-hour trip — still flying.
        let flying = flight("flying", days: -0.5 / 24, durationHours: 3.0)
        let later = flight("later", days: 3)
        let groups = FlightListView.groupedFlights([flying, later], order: .soonestFirst, now: Self.now)
        #expect(ids(groups.first { $0.title == "Future" }) == ["flying", "later"])
    }

    @Test("A zero-duration flight is past the instant it departs")
    func zeroDurationIsPastImmediately() {
        let f = flight("zero", days: -1.0 / 1440, durationHours: 0)   // departed 1 min ago
        let groups = FlightListView.groupedFlights([f], now: Self.now)
        #expect(titles(groups) == ["Recent"])
    }

    @Test("hasEnded mirrors the server boundary")
    func hasEndedBoundary() {
        let f = makeFlight(departureTime: "2026-06-24T12:00:00Z", flightDurationHours: 2.0)
        func at(_ iso: String) -> Date { ISO8601DateFormatter().date(from: iso)! }
        #expect(!f.hasEnded(now: at("2026-06-24T12:00:00Z")))   // departing
        #expect(!f.hasEnded(now: at("2026-06-24T13:59:59Z")))   // airborne
        #expect(!f.hasEnded(now: at("2026-06-24T14:00:00Z")))   // exactly at the end
        #expect(f.hasEnded(now: at("2026-06-24T14:00:01Z")))    // landed
    }

    // MARK: - flightSection computed fallback (uses real `Date()`)

    @Test("flightSection: server value wins; else 7-day date fallback")
    func flightSectionFallback() {
        // Relative to the live clock so the fallback's `Date()` agrees.
        let fmt = ISO8601DateFormatter()
        var recent = makeFlight(id: "r", departureTime: fmt.string(from: Date().addingTimeInterval(-2 * 86400)))
        #expect(recent.flightSection == .recent)   // legacy fallback
        recent.section = "past"
        #expect(recent.flightSection == .past)      // server value wins

        let future = makeFlight(id: "f", departureTime: fmt.string(from: Date().addingTimeInterval(3 * 86400)))
        #expect(future.flightSection == .future)
        let old = makeFlight(id: "o", departureTime: fmt.string(from: Date().addingTimeInterval(-30 * 86400)))
        #expect(old.flightSection == .past)
    }
}

/// `FlightResponse.isInTrackingWindow` — the shared window used by the briefing
/// Start/Stop + PIREP controls and the flight-list "Add PIREP" gate, and the
/// window the server links a pack_id-less PIREP by. Pins the boundaries so a
/// future edit can't silently drift the ±2h edges.
@Suite("FlightTrackingWindow")
struct FlightTrackingWindowTests {
    // Departure 12:00Z, 2h flight → window [10:00Z, 16:00Z]
    // (departure −2h … departure + duration + 2h).
    static let flight = makeFlight(departureTime: "2026-06-24T12:00:00Z", flightDurationHours: 2.0)
    static func at(_ iso: String) -> Date { ISO8601DateFormatter().date(from: iso)! }

    @Test("inside: start edge, departure, and end edge are all in-window")
    func inside() {
        #expect(Self.flight.isInTrackingWindow(now: Self.at("2026-06-24T10:00:00Z")))  // departure −2h
        #expect(Self.flight.isInTrackingWindow(now: Self.at("2026-06-24T12:00:00Z")))  // departure
        #expect(Self.flight.isInTrackingWindow(now: Self.at("2026-06-24T16:00:00Z")))  // departure +dur +2h
    }

    @Test("outside: one second before the start and after the end are out-of-window")
    func outside() {
        #expect(!Self.flight.isInTrackingWindow(now: Self.at("2026-06-24T09:59:59Z")))
        #expect(!Self.flight.isInTrackingWindow(now: Self.at("2026-06-24T16:00:01Z")))
    }
}
