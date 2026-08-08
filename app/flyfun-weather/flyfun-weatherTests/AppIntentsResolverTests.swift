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

    /// Two upcoming + two past, deliberately shuffled.
    private var mixed: [FlightResponse] {
        [
            makeFlight(id: "later", departureTime: "2026-07-20T12:00:00Z"),
            makeFlight(id: "past-old", departureTime: "2026-06-01T12:00:00Z"),
            makeFlight(id: "soon", departureTime: "2026-07-09T12:00:00Z"),
            makeFlight(id: "past-recent", departureTime: "2026-07-05T12:00:00Z"),
        ]
    }

    @Test("suggestions follow the soonest-first preference, past stays recent-first")
    func orderingSoonestFirst() {
        let ordered = FlightResolver.orderedForSuggestions(mixed, order: .soonestFirst, now: now).map(\.id)
        #expect(ordered == ["soon", "later", "past-recent", "past-old"])
    }

    @Test("suggestions follow the furthest-first preference, past stays recent-first")
    func orderingFurthestFirst() {
        let ordered = FlightResolver.orderedForSuggestions(mixed, order: .furthestFirst, now: now).map(\.id)
        #expect(ordered == ["later", "soon", "past-recent", "past-old"])
    }

    @Test("furthest-first is the default, matching the server's default preference")
    func orderingDefault() {
        #expect(FlightResolver.orderedForSuggestions(mixed, now: now).map(\.id)
            == FlightResolver.orderedForSuggestions(mixed, order: .furthestFirst, now: now).map(\.id))
    }

    @Test("nextFlight ignores the display preference — it always means the soonest")
    func nextFlightIgnoresPreference() {
        // Regression guard: "my next flight" returning the furthest-away one
        // would be a real bug, so `nextFlight` takes no `order` at all.
        for _ in FlightOrder.allCases {
            #expect(FlightResolver.nextFlight(in: mixed, now: now)?.id == "soon")
        }
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

@Suite("Resolver — airport label formatting")
struct AirportLabelTests {
    @Test("name + city + country")
    func full() {
        #expect(FlightResolver.airportLabel(
            icao: "EGKB", name: "London Biggin Hill Airport", city: "London", country: "GB")
            == "EGKB (London Biggin Hill Airport, London, GB)")
    }

    @Test("skips empty city and country")
    func minimal() {
        #expect(FlightResolver.airportLabel(icao: "EGTF", name: "Fairoaks", city: "", country: "")
            == "EGTF (Fairoaks)")
    }

    @Test("drops a city that just repeats the name")
    func cityEqualsName() {
        #expect(FlightResolver.airportLabel(icao: "LFMN", name: "Nice", city: "Nice", country: "FR")
            == "LFMN (Nice, FR)")
    }

    @Test("bare ICAO when nothing else is known")
    func bare() {
        #expect(FlightResolver.airportLabel(icao: "ZZZZ", name: "", city: "", country: "") == "ZZZZ")
    }
}

@Suite("Resolver — upcoming candidate filter")
struct UpcomingFilterTests {
    let now = iso("2026-07-08T10:00:00Z")

    @Test("includes today (even earlier today) and future, excludes past, soonest first")
    func filter() {
        let flights = [
            makeFlight(id: "future", departureTime: "2026-07-15T12:00:00Z"),
            makeFlight(id: "yesterday", departureTime: "2026-07-07T12:00:00Z"),
            makeFlight(id: "today-early", departureTime: "2026-07-08T06:00:00Z"),
        ]
        let ids = FlightResolver.upcoming(flights, now: now, calendar: utcCalendar).map(\.id)
        #expect(ids == ["today-early", "future"])
    }
}

/// Records the candidates it was offered and returns a canned id — stands in for
/// the on-device model so the grounded-selection tier is testable without a device.
private final class FakePhraseResolver: FlightPhraseResolving, @unchecked Sendable {
    let idToReturn: String?
    private(set) var receivedCandidates: [FlightCandidate] = []
    init(idToReturn: String?) { self.idToReturn = idToReturn }
    func pick(phrase: String, today: String, candidates: [FlightCandidate]) async -> String? {
        receivedCandidates = candidates
        return idToReturn
    }
}

@MainActor
@Suite("Resolver — grounded selection (tier 2)")
struct GroundedSelectionTests {
    let now = iso("2026-07-08T10:00:00Z")

    // A phrase with no ICAO / airport-name / date keyword, so tier 1 finds nothing
    // and tier 2 (the injected fake) runs.
    let phrase = "the one you were telling me about"

    private func flights() -> [FlightResponse] {
        [
            makeFlight(id: "past", waypoints: ["EGKA", "EGKB"], departureTime: "2026-07-01T12:00:00Z"),
            makeFlight(id: "soon", waypoints: ["EGKB", "EGTF"], departureTime: "2026-07-09T12:00:00Z"),
            makeFlight(id: "later", waypoints: ["LFMD", "LFMN"], departureTime: "2026-07-20T12:00:00Z"),
        ]
    }

    @Test("returns the flight the model selects")
    func selects() async {
        let fake = FakePhraseResolver(idToReturn: "later")
        let result = await FlightResolver.resolve(phrase, in: flights(), now: now, parser: fake)
        #expect(result.map(\.id) == ["later"])
    }

    @Test("only today-and-future flights are offered to the model")
    func candidatesUpcomingOnly() async {
        let fake = FakePhraseResolver(idToReturn: nil)
        _ = await FlightResolver.resolve(phrase, in: flights(), now: now, parser: fake)
        #expect(Set(fake.receivedCandidates.map(\.id)) == ["soon", "later"])
    }

    @Test("a fabricated id is rejected — never invents a flight")
    func rejectsFabricated() async {
        let fake = FakePhraseResolver(idToReturn: "does-not-exist")
        let result = await FlightResolver.resolve(phrase, in: flights(), now: now, parser: fake)
        #expect(result.isEmpty)
    }

    @Test("no upcoming flights → model isn't consulted, empty result")
    func noUpcoming() async {
        let pastOnly = [makeFlight(id: "past", departureTime: "2026-07-01T12:00:00Z")]
        let fake = FakePhraseResolver(idToReturn: "past")
        let result = await FlightResolver.resolve(phrase, in: pastOnly, now: now, parser: fake)
        #expect(result.isEmpty)
        #expect(fake.receivedCandidates.isEmpty)
    }

    @Test("resolution ignores a past flight even when its ICAO is named (tier 1 scoping)")
    func ignoresPastPlaceMatch() async {
        // A past flight to LFMN and no upcoming one: naming "lfmn" must NOT resolve
        // it — tier 1 is scoped to upcoming flights, so it's not even a candidate.
        let pastOnly = [makeFlight(id: "past", waypoints: ["LFMD", "LFMN"], departureTime: "2026-01-05T12:00:00Z")]
        let fake = FakePhraseResolver(idToReturn: "past")
        let result = await FlightResolver.resolve("weather at lfmn", in: pastOnly, now: now, parser: fake)
        #expect(result.isEmpty)
        #expect(fake.receivedCandidates.isEmpty)
    }
}

@MainActor
@Suite("Refresh outcome classification")
struct RefreshOutcomeTests {
    @Test("429 → rate limited")
    func rateLimited() {
        #expect(RefreshDriver.classify(APIError.serverError(429, nil)) == .rateLimited)
    }

    @Test("409 → already in progress")
    func inProgress() {
        #expect(RefreshDriver.classify(APIError.serverError(409, nil)) == .alreadyInProgress)
    }

    @Test("401 → the shared sign-in line")
    func unauthorized() {
        #expect(RefreshDriver.classify(APIError.unauthorized) == .failed(IntentSupport.signedOutSpokenLine))
    }

    @Test("other error → generic failure")
    func other() {
        if case .failed = RefreshDriver.classify(APIError.notFound) {} else {
            Issue.record("expected .failed for a non-auth error")
        }
    }
}

@MainActor
@Suite("Spoken dialog builders")
struct DialogTests {
    private let now = iso("2026-07-08T10:00:00Z")

    @Test("check summary: assessment + top concerns")
    func check() {
        let briefing = makeBriefingStatus(
            assessment: "AMBER", hasAdvisories: true,
            advisorySummary: AdvisorySummary(red: 0, amber: 2, top: [
                AdvisoryChip(status: "AMBER", name: "convective"),
                AdvisoryChip(status: "AMBER", name: "icing"),
            ]))
        let flight = makeFlight(waypoints: ["LFMD", "LFML"], latestBriefing: briefing)
        #expect(IntentDialogs.checkSummary(flight)
            == "Your LFMD → LFML flight is graded amber. Top concerns: convective and icing.")
    }

    @Test("check summary: never briefed")
    func checkUnbriefed() {
        let flight = makeFlight(waypoints: ["LFMD", "LFML"], latestBriefing: nil)
        #expect(IntentDialogs.checkSummary(flight) == "Your LFMD → LFML flight hasn't been briefed yet.")
    }

    @Test("overview: one upcoming flight")
    func overview() {
        let briefing = makeBriefingStatus(assessment: "GREEN", hasAdvisories: false)
        let flight = makeFlight(waypoints: ["EGKB", "EGTF"], departureTime: "2026-08-01T12:00:00Z", latestBriefing: briefing)
        #expect(IntentDialogs.overviewSummary([flight], now: now)
            == "You have 1 upcoming flight: EGKB → EGTF, green.")
    }

    @Test("overview: none upcoming")
    func overviewEmpty() {
        let past = makeFlight(departureTime: "2026-01-01T12:00:00Z")
        #expect(IntentDialogs.overviewSummary([past], now: now) == "You have no upcoming flights.")
    }

    @Test("overview: more than five upcoming lists five and states the remainder")
    func overviewTruncates() {
        // 7 upcoming flights, soonest-first: 2026-07-09 … 2026-07-15.
        let flights = (9...15).map { day in
            makeFlight(id: "flt-\(day)", departureTime: "2026-07-\(day)T12:00:00Z")
        }
        let listed = Array(repeating: "LFMD → LFML, not yet briefed", count: 5).joined(separator: "; ")
        #expect(IntentDialogs.overviewSummary(flights, now: now)
            == "You have 7 upcoming flights: \(listed); and 2 more.")
    }

    @Test("airport weather: snapped result keys off the data's airport, not the request")
    func airportWeather() {
        let entry = AirportWeatherEntry(
            icao: "EGKB",
            consensus: AirportWeatherConsensus(
                flightCategory: "VFR", windSpeedKt: 10, windDirDeg: 240, ceilingFt: nil, visibilityM: nil),
            observation: nil,
            requestedIcao: "EGKA",
            resolutionDistanceNm: 5)
        #expect(IntentDialogs.airportWeather(entry, dayLabel: "Tomorrow")
            == "EGKA isn't monitored, so here's the nearest, EGKB. Tomorrow, EGKB is forecast VFR with wind 10 knots from 240 degrees.")
    }
}
