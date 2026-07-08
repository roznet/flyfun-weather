//
//  AppIntentsResolverTests.swift
//  flyfun-weatherTests
//
//  Pure-logic coverage for the App Intents flight resolver (#364): the
//  deterministic relative-date/place matching and flight ordering that Siri /
//  Shortcuts rely on. The AirportDatabase- and FoundationModels-backed tiers
//  need a device, but the deterministic floor is pure and tested here.
//

import Testing
import Foundation
@testable import flyfun_weather

private func iso(_ s: String) -> Date {
    ISO8601DateFormatter().date(from: s)!
}

private var utcCalendar: Calendar {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    return cal
}

@Suite("Resolver — relative dates")
struct RelativeDateMatchingTests {
    // Wed 8 Jul 2026, 10:00 UTC.
    let now = iso("2026-07-08T10:00:00Z")

    @Test("tomorrow matches the next day only")
    func tomorrow() {
        let cal = utcCalendar
        #expect(FlightResolver.relativeDateMatches(
            departure: iso("2026-07-09T12:00:00Z"),
            query: "the flight tomorrow to fairoaks", now: now, calendar: cal))
        #expect(!FlightResolver.relativeDateMatches(
            departure: iso("2026-07-08T12:00:00Z"),
            query: "tomorrow", now: now, calendar: cal))
        #expect(!FlightResolver.relativeDateMatches(
            departure: iso("2026-07-10T12:00:00Z"),
            query: "tomorrow", now: now, calendar: cal))
    }

    @Test("today matches same day")
    func today() {
        #expect(FlightResolver.relativeDateMatches(
            departure: iso("2026-07-08T18:00:00Z"),
            query: "check my flight today", now: now, calendar: utcCalendar))
    }

    @Test("weekday name matches the soonest upcoming instance")
    func weekday() {
        let cal = utcCalendar
        // Fri 10 Jul 2026.
        #expect(FlightResolver.relativeDateMatches(
            departure: iso("2026-07-10T09:00:00Z"),
            query: "my friday flight", now: now, calendar: cal))
        // Thu 9 Jul is not Monday.
        #expect(!FlightResolver.relativeDateMatches(
            departure: iso("2026-07-09T09:00:00Z"),
            query: "monday", now: now, calendar: cal))
    }

    @Test("weekend matches a Saturday/Sunday departure")
    func weekend() {
        // Sat 11 Jul 2026.
        #expect(FlightResolver.relativeDateMatches(
            departure: iso("2026-07-11T09:00:00Z"),
            query: "this weekend", now: now, calendar: utcCalendar))
    }

    @Test("past departures never match")
    func past() {
        #expect(!FlightResolver.relativeDateMatches(
            departure: iso("2026-07-07T12:00:00Z"),
            query: "tomorrow", now: now, calendar: utcCalendar))
    }
}

@Suite("Resolver — ordering")
struct FlightOrderingTests {
    let now = iso("2026-07-08T10:00:00Z")

    @Test("nextFlight returns the soonest upcoming flight")
    func next() {
        let flights = [
            makeFlight(id: "past", departureTime: "2026-07-01T12:00:00Z"),
            makeFlight(id: "soon", departureTime: "2026-07-09T12:00:00Z"),
            makeFlight(id: "later", departureTime: "2026-07-20T12:00:00Z"),
        ]
        #expect(FlightResolver.nextFlight(in: flights, now: now)?.id == "soon")
    }

    @Test("nextFlight is nil when nothing is upcoming")
    func noneUpcoming() {
        let flights = [makeFlight(id: "past", departureTime: "2026-07-01T12:00:00Z")]
        #expect(FlightResolver.nextFlight(in: flights, now: now) == nil)
    }

    @Test("suggestions list upcoming soonest-first then past recent-first")
    func ordering() {
        let flights = [
            makeFlight(id: "later", departureTime: "2026-07-20T12:00:00Z"),
            makeFlight(id: "past-old", departureTime: "2026-06-01T12:00:00Z"),
            makeFlight(id: "soon", departureTime: "2026-07-09T12:00:00Z"),
            makeFlight(id: "past-recent", departureTime: "2026-07-05T12:00:00Z"),
        ]
        let ordered = FlightResolver.orderedForSuggestions(flights, now: now).map(\.id)
        #expect(ordered == ["soon", "later", "past-recent", "past-old"])
    }
}

@Suite("Resolver — place name matching")
struct PlaceNameMatchingTests {
    @Test("matches a significant airport-name word in the query")
    func matches() {
        #expect(FlightResolver.nameMatches(name: "Fairoaks", query: "the flight tomorrow to fairoaks"))
        #expect(FlightResolver.nameMatches(name: "Cannes Mandelieu", query: "my cannes trip"))
        #expect(FlightResolver.nameMatches(name: "Le Touquet Paris Plage", query: "flying to le touquet"))
    }

    @Test("does not match on stopwords or unrelated places")
    func noMatch() {
        #expect(!FlightResolver.nameMatches(name: "International Airport", query: "international"))
        #expect(!FlightResolver.nameMatches(name: "London Heathrow", query: "paris"))
    }
}
