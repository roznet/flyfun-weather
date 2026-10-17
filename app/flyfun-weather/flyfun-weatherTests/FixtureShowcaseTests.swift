//
//  FixtureShowcaseTests.swift
//  flyfun-weatherTests
//
//  The App Store showcase fixture (`flyfun-weatherUITests/Showcase/showcase.json`)
//  still decodes through the production DTOs, and its dates move so the flight
//  departs tomorrow. The screenshot journey only runs on demand, so this is
//  what tells CI the fixture has drifted from the models — rerun
//  `scripts/export_showcase_fixture_ios.py trim` then.
//

import Testing
import Foundation
@testable import flyfun_weather

private let showcasePath = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .appendingPathComponent("flyfun-weatherUITests/Showcase/showcase.json").path

@Suite struct FixtureShowcaseTests {

    @Test func decodesAsAnEcmwfOnlyBriefing() throws {
        let showcase = try FixtureShowcase.load(path: showcasePath)
        #expect(showcase.flight.waypoints == ["EGTF", "BIG", "REM", "SAPRE", "LSGS"])
        #expect(showcase.routeAnalyses.models == ["ecmwf"])
        #expect(showcase.pack.charts == nil)
        #expect(showcase.pack.hasGramet == false)
        #expect(!showcase.soundingProfiles.isEmpty)
        #expect(showcase.advisoryDetails["vfr_feasibility"] != nil)
    }

    @Test func departsTomorrow() throws {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC")!
        let now = try #require(utc.date(from: DateComponents(year: 2027, month: 3, day: 31, hour: 22)))
        let showcase = try FixtureShowcase.load(path: showcasePath, now: now)
        #expect(showcase.flight.targetDate == "2027-04-01")
        // Times of day are untouched: only whole days move.
        #expect(showcase.flight.departureTime.contains("2027-04-01T13:00:00"))
    }

    @Test func shiftsEveryDateAcrossMonthEnds() {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC")!
        let now = utc.date(from: DateComponents(year: 2026, month: 12, day: 30))!
        let text = #"{"target_date": "2026-10-12", "a": "2026-10-11T23:00:00Z", "b": "on 2026-10-13 18Z"}"#
        let shifted = FixtureShowcase.shiftDates(in: text, toDepartTomorrowFrom: now)
        #expect(shifted == #"{"target_date": "2026-12-31", "a": "2026-12-30T23:00:00Z", "b": "on 2027-01-01 18Z"}"#)
    }
}
