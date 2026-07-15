//
//  RouteForecastOverlayTests.swift
//  flyfun-weatherTests
//
//  Unit tests for the briefing route-map airport-forecast overlay slot helpers
//  (#428) — the iOS mirror of web/tests/unit/forecast-overlay.test.ts: relative
//  UTC-day computation, nearest-sample-hour snapping honouring the ragged grid,
//  valid-time formatting, and full-map deep-link building.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("RouteForecastOverlay")
struct RouteForecastOverlayTests {

    // 2026-07-15T08:00Z, matching the web test's NOW.
    static let now = Date(timeIntervalSince1970: 1_784_102_400)

    // A representative (non-rectangular) grid: near days carry the fine hour set
    // and all three models; D+6 is coarse (6/12/18) with ICON dropped; D+5 absent.
    static let days: [ForecastDay] = [
        ForecastDay(day: 0, date: "2026-07-15", available: true, hours: [6, 9, 12, 15, 18], models: ["gfs", "icon", "ecmwf"]),
        ForecastDay(day: 3, date: "2026-07-18", available: true, hours: [6, 9, 12, 15, 18], models: ["gfs", "icon", "ecmwf"]),
        ForecastDay(day: 5, date: "2026-07-20", available: false, hours: [], models: []),
        ForecastDay(day: 6, date: "2026-07-21", available: true, hours: [6, 12, 18], models: ["gfs", "ecmwf"]),
    ]

    // MARK: relativeDayHour

    @Test("today → day 0, UTC hour")
    func relativeToday() {
        let r = RouteForecastOverlay.relativeDayHour(departureIso: "2026-07-15T14:30:00Z", now: Self.now)
        #expect(r?.day == 0)
        #expect(r?.hour == 14)
    }

    @Test("future day difference in UTC calendar days")
    func relativeFuture() {
        let r = RouteForecastOverlay.relativeDayHour(departureIso: "2026-07-18T09:00:00Z", now: Self.now)
        #expect(r?.day == 3)
        #expect(r?.hour == 9)
    }

    @Test("past flight → negative day")
    func relativePast() {
        let r = RouteForecastOverlay.relativeDayHour(departureIso: "2026-07-14T12:00:00Z", now: Self.now)
        #expect(r?.day == -1)
        #expect(r?.hour == 12)
    }

    @Test("nil / garbage → nil")
    func relativeNil() {
        #expect(RouteForecastOverlay.relativeDayHour(departureIso: nil, now: Self.now) == nil)
        #expect(RouteForecastOverlay.relativeDayHour(departureIso: "not-a-date", now: Self.now) == nil)
    }

    // MARK: nearestHour

    @Test("snaps to the closest offered hour")
    func nearestSnaps() {
        #expect(RouteForecastOverlay.nearestHour([6, 12, 18], 14) == 12)
        #expect(RouteForecastOverlay.nearestHour([6, 9, 12, 15, 18], 13) == 12)
        #expect(RouteForecastOverlay.nearestHour([6, 9, 12, 15, 18], 0) == 6)
        #expect(RouteForecastOverlay.nearestHour([6, 9, 12, 15, 18], 23) == 18)
    }

    @Test("ties resolve to the earlier hour")
    func nearestTie() {
        #expect(RouteForecastOverlay.nearestHour([6, 12, 18], 15) == 12) // equidistant 12/18 → 12
    }

    @Test("empty list → nil")
    func nearestEmpty() {
        #expect(RouteForecastOverlay.nearestHour([], 12) == nil)
    }

    // MARK: resolveSlot

    @Test("snaps the departure hour to the day's offered hours")
    func resolveNearDay() {
        let slot = RouteForecastOverlay.resolveSlot(departureIso: "2026-07-15T14:00:00Z", days: Self.days, now: Self.now)
        #expect(slot?.day == 0)
        #expect(slot?.hour == 15)
        #expect(slot?.models == ["gfs", "icon", "ecmwf"])
    }

    @Test("honours the coarse hour grid on the far day (not the fine set)")
    func resolveFarDay() {
        // A D+6 flight at 14Z must snap to 12 (the coarse grid), never 15.
        let slot = RouteForecastOverlay.resolveSlot(departureIso: "2026-07-21T14:00:00Z", days: Self.days, now: Self.now)
        #expect(slot?.day == 6)
        #expect(slot?.hour == 12)
        #expect(slot?.models == ["gfs", "ecmwf"])
    }

    @Test("returns nil beyond the advertised horizon")
    func resolveBeyondHorizon() {
        #expect(RouteForecastOverlay.resolveSlot(departureIso: "2026-07-22T12:00:00Z", days: Self.days, now: Self.now) == nil) // day 7 absent
    }

    @Test("returns nil for a day present but not available")
    func resolveUnavailableDay() {
        #expect(RouteForecastOverlay.resolveSlot(departureIso: "2026-07-20T12:00:00Z", days: Self.days, now: Self.now) == nil) // day 5 available:false
    }

    @Test("returns nil for a past flight")
    func resolvePast() {
        #expect(RouteForecastOverlay.resolveSlot(departureIso: "2026-07-14T12:00:00Z", days: Self.days, now: Self.now) == nil)
    }

    // MARK: formatForecastTime

    @Test("formats as \"<weekday> <HH>Z\" in UTC")
    func formatTime() {
        #expect(RouteForecastOverlay.formatForecastTime("1970-01-01T00:00:00Z") == "Thu 00Z") // epoch = Thursday
        #expect(RouteForecastOverlay.formatForecastTime("1970-01-04T15:00:00Z") == "Sun 15Z")
    }

    @Test("empty string for an unparseable / nil value")
    func formatTimeInvalid() {
        #expect(RouteForecastOverlay.formatForecastTime("nope") == "")
        #expect(RouteForecastOverlay.formatForecastTime(nil) == "")
    }

    // MARK: deepLink

    @Test("passes a supported individual model through as fc.model")
    func deepLinkIndividual() {
        let slot = RouteForecastSlot(day: 2, hour: 12, models: ["gfs", "icon", "ecmwf"])
        let link = RouteForecastOverlay.deepLink(slot: slot, model: "ecmwf", metric: "flight_category")
        #expect(link.day == 2)
        #expect(link.hour == 12)
        #expect(link.model == "ecmwf")
        #expect(link.metric == "flight_category")
        #expect(link.airport == nil)
    }

    @Test("omits fc.model for an unsupported model (full map uses its default)")
    func deepLinkUnsupported() {
        let slot = RouteForecastSlot(day: 2, hour: 12, models: ["gfs", "icon", "ecmwf"])
        let ukmo = RouteForecastOverlay.deepLink(slot: slot, model: "ukmo", metric: "wind_speed_kt")
        #expect(ukmo.model == nil)
        #expect(ukmo.metric == "wind_speed_kt")
        let worst = RouteForecastOverlay.deepLink(slot: slot, model: "worst", metric: "flight_category")
        #expect(worst.model == nil)
    }
}
