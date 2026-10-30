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

    // MARK: - Duration-aware "upcoming" (#536 review round 2)
    //
    // The intent surfaces must agree with `FlightListView`/`resolvedSection`/the
    // server about when a flight stops being upcoming. `now` is 10:00Z; the
    // in-progress flight departed 09:30Z on a 3-hour trip, so it has not ended.

    private var inProgress: FlightResponse {
        makeFlight(id: "flying", departureTime: "2026-07-08T09:30:00Z", flightDurationHours: 3.0)
    }

    private var alreadyLanded: FlightResponse {
        makeFlight(id: "landed", departureTime: "2026-07-08T06:00:00Z", flightDurationHours: 1.0)
    }

    @Test("isUpcoming is duration-aware, matching the list and the server")
    func isUpcomingIsDurationAware() {
        #expect(FlightResolver.isUpcoming(inProgress, now: now))
        #expect(!FlightResolver.isUpcoming(alreadyLanded, now: now))
        // Zero duration: past the instant it departs.
        let zero = makeFlight(departureTime: "2026-07-08T09:59:00Z", flightDurationHours: 0)
        #expect(!FlightResolver.isUpcoming(zero, now: now))
    }

    @Test("nextFlight returns the flight you're currently on, not the next scheduled one")
    func nextFlightPrefersInProgress() {
        let flights = [makeFlight(id: "tomorrow", departureTime: "2026-07-09T12:00:00Z"), inProgress]
        #expect(FlightResolver.nextFlight(in: flights, now: now)?.id == "flying")
    }

    @Test("an in-progress flight is upcoming in the suggestions list, not past")
    func suggestionsIncludeInProgress() {
        let flights = [makeFlight(id: "tomorrow", departureTime: "2026-07-09T12:00:00Z"), inProgress, alreadyLanded]
        // Soonest-first: the in-progress flight leads, landed one falls to the past half.
        #expect(FlightResolver.orderedForSuggestions(flights, order: .soonestFirst, now: now).map(\.id)
            == ["flying", "tomorrow", "landed"])
        // Furthest-first flips the upcoming half only; past stays at the back.
        #expect(FlightResolver.orderedForSuggestions(flights, order: .furthestFirst, now: now).map(\.id)
            == ["tomorrow", "flying", "landed"])
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
nonisolated private final class FakePhraseResolver: FlightPhraseResolving, @unchecked Sendable {
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

    /// The day before `makeFlight`'s default 2026-06-24T12:00Z departure.
    private let dayBefore = iso("2026-06-23T10:00:00Z")

    @Test("check summary: no reason → assessment + top concerns, with when it departs")
    func check() {
        let briefing = makeBriefingStatus(
            assessment: "AMBER", hasAdvisories: true,
            advisorySummary: AdvisorySummary(red: 0, amber: 2, top: [
                AdvisoryChip(status: "AMBER", name: "convective"),
                AdvisoryChip(status: "AMBER", name: "icing"),
            ]))
        let flight = makeFlight(waypoints: ["LFMD", "LFML"], latestBriefing: briefing)
        #expect(IntentDialogs.checkSummary(flight, now: dayBefore)
            == "Your LFMD → LFML flight, tomorrow at 12:00Z, is graded amber. Top concerns: convective and icing.")
    }

    @Test("check summary: the briefing's reason replaces the concern list")
    func checkWithReason() {
        let briefing = makeBriefingStatus(
            assessment: "RED",
            assessmentReason: "Departure at EGTF is graded IFR, with a low overcast at about 850-1,000 ft",
            advisorySummary: AdvisorySummary(red: 2, amber: 0, top: [AdvisoryChip(status: "RED", name: "VFR Feasibility")]))
        let flight = makeFlight(waypoints: ["EGTF", "LFAT"], latestBriefing: briefing)
        #expect(IntentDialogs.checkSummary(flight, now: dayBefore)
            == "Your EGTF → LFAT flight, tomorrow at 12:00Z, is graded red. "
            + "Departure at EGTF is graded IFR, with a low overcast at about 850-1,000 ft.")
        #expect(IntentDialogs.spokenCheck(flight, now: dayBefore, names: ["EGTF": "Fairoaks", "LFAT": "Le Touquet"])
            == "Your Fairoaks to Le Touquet flight, tomorrow at 12:00 zulu, is graded red. "
            + "Departure at Fairoaks is graded IFR, with a low overcast at about 850 to 1,000 feet.")
        #expect(IntentDialogs.spokenSupporting(flight, now: dayBefore, names: ["EGTF": "Fairoaks", "LFAT": "Le Touquet"])
            == "Your Fairoaks to Le Touquet flight, tomorrow at 12:00 zulu, is graded red.")
    }

    @Test("check summary: never briefed")
    func checkUnbriefed() {
        let flight = makeFlight(waypoints: ["LFMD", "LFML"], latestBriefing: nil)
        #expect(IntentDialogs.checkSummary(flight, now: dayBefore)
            == "Your LFMD → LFML flight, tomorrow at 12:00Z, hasn't been briefed yet.")
    }

    @Test("when phrase: today, tomorrow, weekday within the week, date beyond")
    func whenPhrase() {
        let departure = iso("2026-06-24T12:00:00Z")   // a Wednesday
        #expect(IntentDialogs.whenPhrase(departure, now: iso("2026-06-24T06:00:00Z")) == "today at 12:00Z")
        #expect(IntentDialogs.whenPhrase(departure, now: dayBefore) == "tomorrow at 12:00Z")
        #expect(IntentDialogs.whenPhrase(departure, now: iso("2026-06-20T10:00:00Z")) == "on Wednesday at 12:00Z")
        #expect(IntentDialogs.whenPhrase(departure, now: iso("2026-06-10T10:00:00Z")) == "on 24 Jun at 12:00Z")
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

    @Test("overview: an in-progress flight still counts as upcoming")
    func overviewIncludesInProgress() {
        // `now` is 10:00Z (see the suite's `now`); departed 09:30Z on a 3-hour
        // trip. The list shows it at the top of Future, so Siri must not say
        // "you have no upcoming flights".
        let flying = makeFlight(
            waypoints: ["EGKB", "EGTF"],
            departureTime: "2026-07-08T09:30:00Z",
            flightDurationHours: 3.0
        )
        #expect(IntentDialogs.overviewSummary([flying], now: now)
            == "You have 1 upcoming flight: EGKB → EGTF, not yet briefed.")
    }

    @Test("overview: truncation always keeps the SOONEST five, whatever the list preference")
    func overviewTruncationIsChronological() {
        // The spoken overview is not the display list: it cuts at five, so if it
        // followed a furthest-first preference it would omit the flight
        // departing tomorrow. Distinct routes make the selection observable.
        let days = ["09", "10", "11", "12", "13", "14", "15"]
        let flights = days.map { day in
            makeFlight(
                id: "flt-\(day)",
                waypoints: ["LFMD", "LF\(day)"],
                departureTime: "2026-07-\(day)T12:00:00Z"
            )
        }
        let summary = IntentDialogs.overviewSummary(flights, now: now)
        for day in days.prefix(5) { #expect(summary.contains("LFMD → LF\(day)")) }
        for day in days.suffix(2) { #expect(!summary.contains("LFMD → LF\(day)")) }
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

// MARK: - Flight-day live lead (#365)

/// A `/live` payload as the server sends it (snake_case), decoded the way the
/// repository does. `alert` / `highlight` / `liveUpdatedAt` toggle the cases.
private func makeLiveLayer(
    liveUpdatedAt: String? = "2026-07-08T14:30:00Z",
    alert: String? = "New SIGMET at LFML",
    highlight: String? = "Route as briefed, storms building near LFML",
    headline: String = "Observed 14:30Z · as briefed",
    withGlance: Bool = true
) throws -> LiveLayerResponse {
    var lines: [[String: Any]] = [["phase": "departure", "icao": "LFMD", "alert": false, "text": "LFMD VFR"]]
    if let alert {
        lines.append(["phase": "arrival", "icao": "LFML", "alert": true, "text": alert])
    }
    var glance: [String: Any] = [
        "as_of": "2026-07-08T14:30:00Z",
        "headline": headline,
        "comparison": "as_briefed",
        "lines": lines,
    ]
    if let highlight {
        glance["highlight"] = ["text": highlight, "model": "fixture", "generated_at": "2026-07-08T14:29:00Z"]
    }
    var payload: [String: Any] = ["flight_id": "flt-1", "pack_timestamp": "2026-07-08T07:12:00Z"]
    if withGlance { payload["glance"] = glance }
    if let liveUpdatedAt { payload["live_updated_at"] = liveUpdatedAt }
    let data = try JSONSerialization.data(withJSONObject: payload)
    return try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: data)
}

@Suite("Check briefing — live layer first (#365)")
struct LiveCheckDialogTests {
    /// The day before `makeFlight`'s default 2026-06-24T12:00Z departure.
    private let now = iso("2026-06-23T10:00:00Z")

    private func flight(assessment: String = "AMBER") -> FlightResponse {
        let briefing = makeBriefingStatus(
            assessment: assessment, hasAdvisories: true,
            advisorySummary: AdvisorySummary(red: 0, amber: 1, top: [AdvisoryChip(status: "AMBER", name: "convective")]),
            fetchTimestamp: "2026-07-08T07:12:00Z")
        return makeFlight(waypoints: ["LFMD", "LFML"], latestBriefing: briefing)
    }

    @Test("alert lines first, then the highlight with its time, then the briefing's grade")
    func alertAndHighlight() throws {
        let live = try makeLiveLayer()
        #expect(IntentDialogs.checkSummary(flight(), live: live, now: now)
            == "Live update for your LFMD → LFML flight, tomorrow at 12:00Z. "
            + "New SIGMET at LFML. As of 14:29Z: Route as briefed, storms building near LFML. "
            + "The 07:12Z briefing graded it amber; top concern: convective.")
    }

    @Test("no highlight: the headline stands in, without an extra time")
    func headlineFallback() throws {
        let live = try makeLiveLayer(highlight: nil)
        #expect(IntentDialogs.checkSummary(flight(), live: live, now: now)
            == "Live update for your LFMD → LFML flight, tomorrow at 12:00Z. "
            + "New SIGMET at LFML. Observed 14:30Z · as briefed. "
            + "The 07:12Z briefing graded it amber; top concern: convective.")
    }

    @Test("a layer without data leaves today's line unchanged")
    func noData() throws {
        let live = try makeLiveLayer(liveUpdatedAt: nil)
        #expect(IntentDialogs.liveCheckLines(live) == nil)
        #expect(IntentDialogs.checkSummary(flight(), live: live, now: now) == IntentDialogs.checkSummary(flight(), now: now))
        #expect(IntentDialogs.checkSummary(flight(), live: live, now: now)
            == "Your LFMD → LFML flight, tomorrow at 12:00Z, is graded amber. Top concern: convective.")
    }

    @Test("UNAVAILABLE grade with live data: the live lines lead, the briefing is not a verdict")
    func unavailableWithLive() throws {
        let live = try makeLiveLayer(alert: nil)
        #expect(IntentDialogs.checkSummary(flight(assessment: "UNAVAILABLE"), live: live, now: now)
            == "Live update for your LFMD → LFML flight, tomorrow at 12:00Z. As of 14:29Z: Route as briefed, storms building near LFML. "
            + "The 07:12Z briefing couldn't assess it: there was no usable model data for that route.")
    }

    @Test("live lines keep server text verbatim, in spoken order")
    func liveLines() throws {
        let lines = try #require(IntentDialogs.liveCheckLines(try makeLiveLayer()))
        #expect(lines.alerts == ["New SIGMET at LFML"])
        #expect(lines.summary == "Route as briefed, storms building near LFML")
        #expect(lines.summaryTime == "14:29Z")
    }

    @Test("a layer with data but no glance leaves today's line unchanged")
    func noGlance() throws {
        let live = try makeLiveLayer(withGlance: false)
        #expect(IntentDialogs.liveCheckLines(live) == nil)
        #expect(IntentDialogs.checkSummary(flight(), live: live, now: now) == IntentDialogs.checkSummary(flight(), now: now))
    }

    @Test("blank alert text is dropped; nothing left to say means no live lead")
    func blankLiveText() throws {
        let blankAlert = try #require(IntentDialogs.liveCheckLines(try makeLiveLayer(alert: "  ")))
        #expect(blankAlert.alerts.isEmpty)
        #expect(blankAlert.summary == "Route as briefed, storms building near LFML")

        let empty = try makeLiveLayer(alert: " ", highlight: nil, headline: "")
        #expect(IntentDialogs.liveCheckLines(empty) == nil)
        #expect(IntentDialogs.checkSummary(flight(), live: empty, now: now) == IntentDialogs.checkSummary(flight(), now: now))
    }

    @Test("briefing grade line: two concerns, outlook, no assessment, not briefed")
    func briefingGradeLineBranches() {
        let twoConcerns = makeFlight(waypoints: ["LFMD", "LFML"], latestBriefing: makeBriefingStatus(
            assessment: "RED",
            advisorySummary: AdvisorySummary(red: 1, amber: 1, top: [
                AdvisoryChip(status: "RED", name: "convective"), AdvisoryChip(status: "AMBER", name: "icing"),
            ]),
            fetchTimestamp: "2026-07-08T07:12:00Z"))
        #expect(IntentDialogs.briefingGradeLine(twoConcerns)
            == "The 07:12Z briefing graded it red; top concerns: convective and icing.")

        let outlook = makeFlight(waypoints: ["LFMD", "LFML"],
                                 latestBriefing: makeBriefingStatus(outlook: "LIKELY_FLYABLE"))
        #expect(IntentDialogs.briefingGradeLine(outlook) == "The briefing gave a long-range outlook of likely flyable.")
        #expect(IntentDialogs.checkSummary(outlook, now: now)
            == "Your LFMD → LFML flight, tomorrow at 12:00Z, has a long-range outlook of likely flyable.")

        let none = makeFlight(waypoints: ["LFMD", "LFML"],
                              latestBriefing: makeBriefingStatus(fetchTimestamp: "2026-07-08T07:12:00Z"))
        #expect(IntentDialogs.briefingGradeLine(none) == "The 07:12Z briefing has no assessment yet.")
        #expect(IntentDialogs.checkSummary(none, now: now) == "Your LFMD → LFML flight, tomorrow at 12:00Z, has no assessment yet.")

        #expect(IntentDialogs.briefingGradeLine(makeFlight(waypoints: ["LFMD", "LFML"])) == "It hasn't been briefed yet.")
    }

    @Test("card note: nil for a traffic-light grade, the dialog's words otherwise")
    func briefingNote() {
        #expect(IntentDialogs.briefingNote(flight(), now: now) == nil)
        #expect(IntentDialogs.briefingNote(flight(assessment: "UNAVAILABLE"), now: now)
            == "I couldn't assess your LFMD → LFML flight, tomorrow at 12:00Z: there's no usable model data for that route yet.")
        #expect(IntentDialogs.briefingNote(makeFlight(waypoints: ["LFMD", "LFML"]), now: now)
            == "Your LFMD → LFML flight, tomorrow at 12:00Z, hasn't been briefed yet.")
    }
}

// MARK: - Speaking for the car

private let londonNames: [String: String] = [
    "EGTF": "Fairoaks", "EGLL": "London Heathrow", "EGLF": "Farnborough", "EGWU": "RAF Northolt",
    "EGKK": "London Gatwick", "EGLC": "London City", "EGMC": "London Southend",
    "LFAC": "Calais Marck", "LFAT": "Le Touquet",
]

@Suite("Spoken text — written briefing text made speakable")
struct SpokenTextTests {
    @Test("a real live highlight: names for codes, the source note dropped, nothing summarised away")
    func liveHighlight() {
        let written = "Departure EGTF is LIFR now (EGLL 8 NM NE used for METAR and TAF; EGTF has no METAR or TAF), "
            + "and several route airports are LIFR with fog: EGLF, EGWU, EGKK (LIFR at 07:21Z TAF), EGLC, "
            + "with EGMC IFR; LFAC is LIFR, 7 NM before LFAT. Destination LFAT is VFR now with no TAF at ETA. "
            + "No SIGMETs, cells or rain on the track ahead; conditions have improved since briefing."
        #expect(SpokenText.speakable(written, names: londonNames)
            == "Departure Fairoaks is low IFR now, and several route airports are low IFR with fog: "
            + "Farnborough, RAF Northolt, London Gatwick (low IFR at 07:21 zulu TAF), London City, with London Southend IFR; "
            + "Calais Marck is low IFR, 7 nautical miles before Le Touquet. "
            + "Destination Le Touquet is VFR now with no TAF at ETA. "
            + "No SIGMETs, cells or rain on the track ahead; conditions have improved since briefing.")
    }

    @Test("a parenthetical is kept unless it is a source note naming no weather")
    func parentheticals() {
        // Weather in brackets always stays, source wording or not.
        #expect(SpokenText.speakable("Destination IFR (fog)") == "Destination IFR (fog)")
        #expect(SpokenText.speakable("LFAT IFR (visibility 800 m)") == "LFAT IFR (visibility 800 m)")
        #expect(SpokenText.speakable("Ceiling low (BKN002 in the TAF)") == "Ceiling low (BKN002 in the TAF)")
        // Not a source note: kept by default.
        #expect(SpokenText.speakable("Route as briefed (unchanged)") == "Route as briefed (unchanged)")
        // A pure source note goes.
        #expect(SpokenText.speakable("Departure VFR (nearest METAR used for EGTF)") == "Departure VFR")
    }

    @Test("a parenthetical naming a hazard is kept")
    func hazardParentheticalKept() {
        #expect(SpokenText.speakable("LFAT is IFR (EMBD TS reported at 14:20Z)", names: londonNames)
            == "Le Touquet is IFR (EMBD TS reported at 14:20 zulu)")
        #expect(SpokenText.speakable("Route clear (moderate icing above FL080)")
            == "Route clear (moderate icing above flight level 80)")
    }

    @Test("units, ranges, categories and unknown codes")
    func units() {
        #expect(SpokenText.speakable("Overcast 850-1,000 ft, MVFR within 1 NM of XXXX")
            == "Overcast 850 to 1,000 feet, marginal VFR within 1 nautical mile of XXXX")
        #expect(SpokenText.speakable("EGTF → LFAT · as briefed", names: londonNames)
            == "Fairoaks to Le Touquet, as briefed")
    }

    @Test("airport names trimmed for speech")
    func names() {
        #expect(SpokenText.speakableName("Fairoaks Airport") == "Fairoaks")
        #expect(SpokenText.speakableName("Le Touquet-Côte d'Opale Airport") == "Le Touquet")
        #expect(SpokenText.speakableName("Charles de Gaulle International Airport") == "Charles de Gaulle")
        #expect(SpokenText.speakableName("Midden-Zeeland Airfield") == "Midden-Zeeland")
        #expect(SpokenText.speakableName("RAF Northolt") == "RAF Northolt")
    }
}

@Suite("Flight spoken names — what Siri listens for")
struct FlightSpokenNamesTests {
    private let now = iso("2026-10-30T20:00:00Z")
    private var flights: [FlightResponse] {
        [
            makeFlight(id: "week", waypoints: ["EGTF", "LFAT"], departureTime: "2026-11-07T11:00:00Z"),
            makeFlight(id: "back", waypoints: ["LFAT", "EGTF"], departureTime: "2026-10-31T14:00:00Z"),
            makeFlight(id: "out", waypoints: ["EGTF", "LFAT"], departureTime: "2026-10-31T11:00:00Z"),
        ]
    }

    @Test("a bare place, 'to' and the code belong to the soonest flight going there")
    func outbound() throws {
        let synonyms = FlightSpokenNames.synonyms(for: flights, names: londonNames, now: now)
        let out = try #require(synonyms["out"])
        for phrase in ["Le Touquet", "to Le Touquet", "LFAT", "L F A T", "Fairoaks to Le Touquet", "from Fairoaks",
                       "Fairoaks to Le Touquet tomorrow"] {
            #expect(out.contains(phrase), "\(phrase)")
        }
    }

    @Test("the return answers to 'from Le Touquet' and its own destination, not 'Le Touquet'")
    func inbound() throws {
        let back = try #require(FlightSpokenNames.synonyms(for: flights, names: londonNames, now: now)["back"])
        #expect(back.contains("from Le Touquet"))
        #expect(back.contains("Fairoaks"))
        #expect(back.contains("Le Touquet to Fairoaks"))
        #expect(!back.contains("Le Touquet"))
    }

    @Test("a later same-route flight only answers to its dated form")
    func laterSameRoute() throws {
        let week = try #require(FlightSpokenNames.synonyms(for: flights, names: londonNames, now: now)["week"])
        #expect(week.contains("Fairoaks to Le Touquet on 7 Nov"))
        #expect(!week.contains("Le Touquet"))
        #expect(!week.contains("Fairoaks to Le Touquet"))
    }
}

@MainActor
@Suite("Resolver — direction and spelled codes")
struct DirectionResolverTests {
    private let out = makeFlight(id: "out", waypoints: ["EGTF", "LFAT"], departureTime: "2026-10-31T11:00:00Z")
    private let back = makeFlight(id: "back", waypoints: ["LFAT", "EGTF"], departureTime: "2026-10-31T14:00:00Z")

    @Test("from / to clauses")
    func clauses() {
        #expect(FlightResolver.directionClauses("from fairoaks to le touquet") == ("fairoaks", "le touquet"))
        #expect(FlightResolver.directionClauses("to le touquet") == (nil, "le touquet"))
        #expect(FlightResolver.directionClauses("le touquet tomorrow") == (nil, nil))
        #expect(FlightResolver.directionClauses("fairoaks to le touquet") == (nil, "le touquet"))
    }

    @Test("'from' must be the departure, 'to' the destination")
    func direction() {
        #expect(FlightResolver.matchesPlace(back, query: "from lfat"))
        #expect(!FlightResolver.matchesPlace(out, query: "from lfat"))
        #expect(FlightResolver.matchesPlace(out, query: "to lfat"))
        #expect(!FlightResolver.matchesPlace(back, query: "to lfat"))
        // "Origin to destination" without "from": the destination still decides.
        #expect(FlightResolver.matchesPlace(out, query: "egtf to lfat"))
        #expect(!FlightResolver.matchesPlace(back, query: "egtf to lfat"))
    }

    @Test("a bare place prefers the flight going there")
    func barePlace() {
        #expect(FlightResolver.preferDestinations([out, back], query: "lfat").map(\.id) == ["out"])
        #expect(FlightResolver.preferDestinations([out, back], query: "from lfat").map(\.id) == ["out", "back"])
    }

    @Test("a code read letter by letter is joined; lone letters and words are left alone")
    func spelled() {
        #expect(FlightResolver.joinSpelledICAO("e g t f") == "egtf")
        #expect(FlightResolver.joinSpelledICAO("echo golf tango foxtrot tomorrow") == "egtf tomorrow")
        #expect(FlightResolver.joinSpelledICAO("to lima on friday") == "to lima on friday")
        #expect(FlightResolver.joinSpelledICAO("a b c") == "a b c")
    }
}

@Suite("Refresh — spoken lines")
struct RefreshSpokenLineTests {
    @Test("started tells the pilot when to ask again, with names")
    func started() {
        let line = RefreshBriefingIntent.spokenLine(.started, route: "EGTF → LFAT")
        #expect(SpokenText.speakable(line, names: londonNames)
            == "Refreshing your Fairoaks to Le Touquet briefing. It takes about two minutes; ask me to read it again then.")
    }

    @Test("server reason for an already-fresh briefing is used verbatim")
    func alreadyFresh() {
        #expect(RefreshBriefingIntent.spokenLine(.alreadyFresh(reason: "No newer model run yet."), route: "EGTF → LFAT")
            == "No newer model run yet.")
    }
}
