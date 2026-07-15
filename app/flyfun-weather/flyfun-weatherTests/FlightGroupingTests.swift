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

    private func flight(_ id: String, days: Double, section: String? = nil) -> FlightResponse {
        var f = makeFlight(id: id, departureTime: Self.iso(days))
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
