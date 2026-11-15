//
//  FlightSummaryTests.swift
//  flyfun-weatherTests
//
//  The Advisory tab's flight summary strip (#996): one formatter, mirrored by
//  Android `FlightSummaryTest` with the same cases.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("FlightSummary")
struct FlightSummaryTests {

    private func progress(departedAt: String? = nil, eta: String? = nil, landedAt: String? = nil) -> LiveProgress {
        LiveProgress(source: "app", asOf: nil, alongNm: nil, routeNm: nil, lat: nil, lon: nil,
                     fixAt: nil, fixAgeMin: nil, estimated: nil, offRoute: nil,
                     departedAt: departedAt, etaDestination: eta, landedAt: landedAt, delayMin: nil)
    }

    @Test func plannedTimesLevelAndDuration() {
        let flight = makeFlight(waypoints: ["egtf", "LFAT"], departureTime: "2026-11-21T10:00:00Z",
                                cruiseAltitudeFt: 6500, flightDurationHours: 85.0 / 60.0)
        let lines = FlightSummary.lines(flight: flight)
        #expect(lines.main == "EGTF 10:00Z → LFAT 11:25Z")
        #expect(lines.detail == "Sat 21 Nov · 1h25 · FL065")
        #expect(lines.route == nil)
    }

    @Test func routeIsRawRouteElseInnerWaypoints() {
        let viaWaypoints = makeFlight(waypoints: ["EGTF", "MID", "SFD", "LFAT"])
        #expect(FlightSummary.lines(flight: viaWaypoints).route == "MID SFD")
        let viaRaw = makeFlight(waypoints: ["EGTF", "MID", "LFAT"], rawRoute: " MID DCT SFD ")
        #expect(FlightSummary.lines(flight: viaRaw).route == "MID DCT SFD")
    }

    @Test func liveProgressReplacesPlannedTimes() {
        let flight = makeFlight(waypoints: ["EGTF", "LFAT"], departureTime: "2026-11-21T10:00:00Z",
                                flightDurationHours: 1.5)
        let airborne = FlightSummary.lines(flight: flight, progress: progress(
            departedAt: "2026-11-21T10:12:00Z", eta: "2026-11-21T11:36:00Z"))
        #expect(airborne.main == "EGTF off 10:12Z → LFAT est 11:36Z")
        let landed = FlightSummary.lines(flight: flight, progress: progress(
            departedAt: "2026-11-21T10:12:00Z", eta: "2026-11-21T11:36:00Z", landedAt: "2026-11-21T11:40:00Z"))
        #expect(landed.main == "EGTF off 10:12Z → LFAT landed 11:40Z")
    }

    @Test func etaBeforeTakeOffKeepsThePlan() {
        // Before take-off the server's ETA is the plan carried forward; the
        // strip says "est" only once the flight is off.
        let flight = makeFlight(waypoints: ["EGTF", "LFAT"], departureTime: "2026-11-21T10:00:00Z",
                                flightDurationHours: 1.5)
        let lines = FlightSummary.lines(flight: flight, progress: progress(eta: "2026-11-21T11:45:00Z"))
        #expect(lines.main == "EGTF 10:00Z → LFAT 11:30Z")
    }

    @Test func durationLabelUsesExactMinutes() {
        #expect(FlightSummary.durationLabel(0.75) == "0h45")
        #expect(FlightSummary.durationLabel(2.0) == "2h00")
        #expect(FlightSummary.durationLabel(1.0 + 2.0 / 60.0) == "1h02")
    }
}
