//
//  RouteCreateLogicTests.swift
//  flyfun-weatherTests
//
//  Pure-logic tests for the flight-create revamp: TZ-aware departure time
//  (#4) and route autocomplete token parsing (#6). These are silent-regression
//  prone — wrong timezone math sends the wrong UTC instant to the server, and
//  wrong tokenisation completes the wrong waypoint, neither of which crashes.
//

import Testing
import Foundation
@testable import flyfun_weather

private func iso(_ s: String) -> Date {
    let f = ISO8601DateFormatter()
    return f.date(from: s)!
}

/// A device-timezone date at noon for the given Y/M/D (matches how
/// `DepartureTimeModel.dateProxy` reads the date component).
private func deviceDate(_ y: Int, _ m: Int, _ d: Int) -> Date {
    var c = DateComponents()
    c.year = y; c.month = m; c.day = d; c.hour = 12
    return Calendar.current.date(from: c)!
}

@MainActor
@Suite struct DepartureTimeModelTests {
    @Test func formatOffsetSignAndMinutes() {
        #expect(DepartureTimeModel.formatOffset(120) == "GMT+2")
        #expect(DepartureTimeModel.formatOffset(0) == "GMT+0")
        #expect(DepartureTimeModel.formatOffset(-330) == "GMT-5:30")
        #expect(DepartureTimeModel.formatOffset(330) == "GMT+5:30")
    }

    @Test func nearestMinuteSnapsToQuarter() {
        #expect(DepartureTimeModel.nearestMinuteOption(7) == 0)
        #expect(DepartureTimeModel.nearestMinuteOption(8) == 15)
        #expect(DepartureTimeModel.nearestMinuteOption(22) == 15)
        #expect(DepartureTimeModel.nearestMinuteOption(23) == 30)
        #expect(DepartureTimeModel.nearestMinuteOption(45) == 45)
    }

    @Test func wallClockBuildsCorrectUtcInSummerDst() {
        // 09:00 on 2026-07-15 in Paris is CEST (GMT+2) → 07:00Z.
        let model = DepartureTimeModel(instant: iso("2026-01-01T00:00:00Z"))
        model.timeZoneId = "Europe/Paris"
        model.dateProxy = deviceDate(2026, 7, 15)
        model.setHour(9)
        model.setMinute(0)
        #expect(model.instant == iso("2026-07-15T07:00:00Z"))
    }

    @Test func wallClockBuildsCorrectUtcInWinterStandardTime() {
        // 09:00 on 2026-01-15 in Paris is CET (GMT+1) → 08:00Z — the DST case
        // flyfun-forms' fixed-offset picker gets wrong.
        let model = DepartureTimeModel(instant: iso("2026-07-01T00:00:00Z"))
        model.timeZoneId = "Europe/Paris"
        model.dateProxy = deviceDate(2026, 1, 15)
        model.setHour(9)
        model.setMinute(0)
        #expect(model.instant == iso("2026-01-15T08:00:00Z"))
    }

    @Test func switchingTimeZonePreservesTheInstant() {
        let model = DepartureTimeModel(instant: iso("2026-01-01T00:00:00Z"))
        model.timeZoneId = "Europe/Paris"
        model.dateProxy = deviceDate(2026, 7, 15)
        model.setHour(9)
        model.setMinute(0)
        let fixed = model.instant            // 07:00Z

        model.timeZoneId = "UTC"
        #expect(model.instant == fixed)      // instant unchanged
        #expect(model.hour == 7)             // same moment, displayed in UTC
        #expect(model.minute == 0)
    }

    @Test func optionsAlwaysIncludeUtcFirst() {
        let model = DepartureTimeModel(instant: iso("2026-07-15T12:00:00Z"))
        model.setRouteTimeZones(["Europe/Paris", "Europe/London"], preferred: "Europe/Paris")
        let ids = model.options.map(\.identifier)
        #expect(ids.first == "UTC")
        #expect(ids.contains("Europe/Paris"))
        #expect(ids.contains("Europe/London"))
        // Preferred departure zone becomes the selection (was the UTC default).
        #expect(model.timeZoneId == "Europe/Paris")
    }

    @Test func resetsSelectionWhenRouteDropsTheCurrentZone() {
        let model = DepartureTimeModel(instant: iso("2026-07-15T12:00:00Z"))
        model.setRouteTimeZones(["Europe/Paris"], preferred: "Europe/Paris")
        #expect(model.timeZoneId == "Europe/Paris")
        // Route changes to a zone set that no longer offers Paris — the picker
        // must not end up on a value absent from its options.
        model.setRouteTimeZones(["America/New_York"], preferred: "America/New_York")
        #expect(model.timeZoneId == "America/New_York")
    }
}

@MainActor
@Suite struct RouteAutocompleteTests {
    @Test func lastTokenIsTheTrailingIdentifier() {
        #expect(RouteAutocompleteController.lastToken(in: "EGTF LFQ") == "LFQ")
        #expect(RouteAutocompleteController.lastToken(in: "egtf lfq") == "LFQ")
        #expect(RouteAutocompleteController.lastToken(in: "EGTF-LF") == "LF")
        #expect(RouteAutocompleteController.lastToken(in: "EGTF,LO") == "LO")
    }

    @Test func lastTokenEmptyAfterSeparatorOrEmptyField() {
        // A trailing separator means the previous token is finished.
        #expect(RouteAutocompleteController.lastToken(in: "EGTF ") == "")
        #expect(RouteAutocompleteController.lastToken(in: "") == "")
    }
}
