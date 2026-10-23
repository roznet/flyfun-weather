//
//  AirportCardTests.swift
//  flyfun-weatherTests
//
//  #882 — the Observed tab's departure / arrival airport cards: the #880
//  fields decode (and an older server still decodes), a card stands in for
//  its terminal line, and the card's pure mapping (`AirportCardRules`) picks
//  the server's values in the web card's order (airport-card-view.ts, #881).
//

import Testing
import Foundation
@testable import flyfun_weather

private let airportJSON = """
{
  "icao": "ZZDP", "name": "Zedfield",
  "metar_time": "2099-06-30T09:30:00Z", "metar_flight_category": "MVFR",
  "metar_report_type": "SPECI",
  "metar_previous_flight_category": "VFR", "metar_previous_time": "2099-06-30T09:00:00Z",
  "metar_trend": "worse",
  "taf_prevailing_category_at_eta": "VFR", "taf_temporary_category_at_eta": "IFR",
  "taf_temporary_type": "PROB30 TEMPO",
  "metar_wind_advisory": "amber",
  "metar_ceiling_meter": 0.62, "metar_visibility_meter": 1.4,
  "metar_sky_text": "BKN 2,500 ft", "metar_cavok": false, "metar_visibility_text": "10 km+",
  "metar_wind_short": "290/15G25",
  "metar_weather_words": [{"code": "-RA", "text": "Light rain", "icon": "rain"}],
  "metar_crosswind": {"runway": "25R", "side": "left", "kt": 10, "gust_kt": 16, "text": "10G16 kt"},
  "taf_hours": [
    {"time": "2099-06-30T09:00:00Z", "prevailing_category": "VFR", "temporary_category": "IFR",
     "temporary_type": "PROB30 TEMPO", "is_eta": false},
    {"time": "2099-06-30T10:00:00Z", "prevailing_category": "VFR", "temporary_category": null,
     "temporary_type": null, "is_eta": true}
  ]
}
"""

private let glanceJSON = """
{
  "as_of": "2099-06-30T09:40:00Z", "headline": "Observed 09:40Z · as briefed", "comparison": "as_briefed",
  "lines": [
    {"phase": "departure", "icao": "ZZDP", "text": "ZZDP MVFR"},
    {"phase": "enroute", "text": "no cells within 30 NM of track ahead"},
    {"phase": "arrival", "icao": "ZZAR", "text": "ZZAR VFR", "passed": false}
  ],
  "departure": {
    "icao": "ZZDP", "name": "Zedfield", "etd": "2099-06-30T10:00:00Z",
    "metar_text": "ZZDP MVFR", "metar_category": "MVFR", "metar_time": "2099-06-30T09:30:00Z",
    "taf_text": "TAF at ETD VFR, PROB30 IFR", "taf_category": "VFR",
    "wind_text": "Wind at ETD 290° 18G28 kt", "wind_advisory": "amber",
    "cells_text": "no cells within 30 NM", "cells": [], "lightning_text": "no lightning ≤20 NM",
    "cells_alert": false, "lightning_alert": true, "alert": false, "unavailable": [],
    "folded": false, "folded_at": null, "folded_source": null, "folded_text": null,
    "airport": \(airportJSON)
  },
  "destination": {
    "icao": "ZZAR", "name": "Zedville", "eta": "2099-06-30T11:35:00Z",
    "metar_text": "ZZAR VFR", "metar_category": "VFR", "taf_text": "TAF at ETA VFR",
    "cells_text": "no cells within 30 NM", "cells": [], "alert": false
  }
}
"""

@MainActor
@Suite struct AirportCardTests {
    private typealias R = AirportCardRules

    private func glance(_ json: String = glanceJSON) throws -> LiveGlance {
        try JSONDecoder.weatherBrief.decode(LiveGlance.self, from: Data(json.utf8))
    }

    private func departure() throws -> LiveGlanceDeparture {
        try #require(try glance().departure)
    }

    // MARK: Decoding

    @Test func decodesTheDepartureBlockAndTheCardFields() throws {
        let dep = try departure()
        #expect(dep.etd == "2099-06-30T10:00:00Z")
        #expect(dep.isFolded == false)
        #expect(dep.lightningAlert == true)
        #expect(dep.cellsAlert == false)
        let a = try #require(dep.airport)
        #expect(a.metarTrend == "worse")
        #expect(a.metarCeilingMeter == 0.62)
        #expect(a.metarSkyText == "BKN 2,500 ft")
        #expect(a.metarCavok == false)
        #expect(a.metarWindShort == "290/15G25")
        #expect(a.metarWeatherWords?.first?.icon == "rain")
        #expect(a.metarCrosswind?.runway == "25R")
        #expect(a.metarCrosswind?.gustKt == 16)
        #expect(a.tafHours?.map(\.isEta) == [false, true])
        #expect(a.tafHours?.first?.temporaryType == "PROB30 TEMPO")
    }

    @Test func anOlderServerDecodesWithoutThem() throws {
        let older = #"{"as_of": "2099-06-30T08:30:00Z", "headline": "h", "comparison": "as_briefed", "lines": [],"#
            + #" "destination": {"icao": "ZZAR", "metar_text": "ZZAR VFR", "taf_text": "t", "cells_text": "c"}}"#
        let g = try glance(older)
        #expect(g.departure == nil)
        #expect(g.destination?.airport == nil)
        #expect(g.destination?.cellsAlert == nil)
    }

    @Test func survivesThePlainJSONLiveCache() throws {
        let original = try glance()
        let cached = try JSONDecoder().decode(LiveGlance.self, from: try JSONEncoder().encode(original))
        #expect(cached.departure == original.departure)
        #expect(cached.destination == original.destination)
    }

    /// The exported `/live` ticks shared with the UI test and the web carry
    /// the departure block with its airport row.
    @Test func everyExportedTickDecodesItsDepartureCard() throws {
        let dir = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("fixtures/live-scenarios")
        let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
        #expect(!files.isEmpty)
        for file in files {
            let layer = try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(contentsOf: file))
            let dep = try #require(layer.glance?.departure, "\(file.lastPathComponent)")
            #expect(dep.airport?.icao == dep.icao, "\(file.lastPathComponent)")
            #expect(layer.glance?.destination?.airport != nil, "\(file.lastPathComponent)")
        }
    }

    /// The server-written fixture every client draws (#881), once it is on
    /// the branch: every block decodes with its airport row.
    @Test(.enabled(if: FileManager.default.fileExists(atPath: AirportCardTests.sharedFixture.path)))
    func theSharedWebFixtureDecodes() throws {
        let data = try Data(contentsOf: Self.sharedFixture)
        let root = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        for phase in ["before", "after"] {
            let blocks = try #require(root[phase] as? [String: Any])
            let dep = try JSONDecoder.weatherBrief.decode(
                LiveGlanceDeparture.self, from: JSONSerialization.data(withJSONObject: blocks["departure"] ?? [:]))
            let arr = try JSONDecoder.weatherBrief.decode(
                LiveGlanceDestination.self, from: JSONSerialization.data(withJSONObject: blocks["destination"] ?? [:]))
            #expect(dep.airport != nil, "\(phase)")
            #expect(arr.airport != nil, "\(phase)")
            if phase == "after" { #expect(dep.isFolded, "the departure folds after take-off") }
        }
    }

    nonisolated static let sharedFixture = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().appendingPathComponent("web/tests/fixtures/airport_cards.json")

    // MARK: Lines

    @Test func eachCardStandsInForItsTerminalLine() throws {
        var g = try glance()
        #expect(g.aheadItems.map(\.phase) == ["enroute"])
        g.departure = nil
        #expect(g.aheadItems.map(\.phase) == ["departure", "enroute"])
    }

    // MARK: Header

    @Test func boxesReadNowThenAtTheEtdThenTheDashedGroup() throws {
        let boxes = R.boxes(try departure(), role: .departure)
        #expect(boxes == [
            .init(category: "MVFR", caption: "now", dashed: false),
            .init(category: "VFR", caption: "at ETD", dashed: false),
            .init(category: "IFR", caption: "PROB30", dashed: true),
        ])
    }

    @Test func withoutAnAirportRowOnlyTheBlocksCategoryShows() throws {
        let dest = try #require(try glance().destination)
        #expect(R.boxes(dest, role: .arrival) == [.init(category: "VFR", caption: "now", dashed: false)])
        #expect(R.tafNote(dest) == "TAF at ETA VFR")
    }

    @Test func shortTypeKeepsOneWord() {
        #expect(R.shortType("PROB30 TEMPO") == "PROB30")
        #expect(R.shortType("TEMPO") == "TEMPO")
        #expect(R.shortType(nil) == "TEMPO")
        #expect(R.shortType("") == "TEMPO")
    }

    // MARK: Rows

    @Test func crosswindReadsTheSideAndTakesTheMetarAdvisory() throws {
        let a = try #require(try departure().airport)
        let x = try #require(a.metarCrosswind)
        #expect(R.crosswindText(x) == "◀ 10G16 kt X")
        #expect(R.crosswindGlyph("right") == "▶ ")
        #expect(R.crosswindGlyph("") == "")
        #expect(R.crosswindTone(a) == "amber")
    }

    @Test func aGreenAdvisoryStaysQuiet() throws {
        let json = airportJSON.replacingOccurrences(of: #""metar_wind_advisory": "amber""#,
                                                    with: #""metar_wind_advisory": "green""#)
        let a = try JSONDecoder.weatherBrief.decode(AirportObservation.self, from: Data(json.utf8))
        #expect(R.crosswindTone(a) == nil)
    }

    @Test func meterPositionsAreClampedAndMissingOnesLeftOut() {
        #expect(R.meterPosition(0.62) == 0.62)
        #expect(R.meterPosition(1.4) == 1)
        #expect(R.meterPosition(-0.2) == 0)
        #expect(R.meterPosition(nil) == nil)
        #expect(R.meterPosition(.nan) == nil)
    }

    @Test func weatherIconKindsMapToSymbols() {
        #expect(R.weatherSymbol("thunder") == "cloud.bolt")
        #expect(R.weatherSymbol("freezing") == "cloud.snow")
        #expect(R.weatherSymbol("mist") == "cloud.fog")
        #expect(R.weatherSymbol("something new") == "cloud")
        #expect(R.weatherSymbol(nil) == "cloud")
    }

    // MARK: Provenance and strip

    @Test func trendNamesTheEarlierCategoryAndTime() throws {
        let trend = try #require(R.trend(try departure().airport))
        #expect(trend == .init(arrow: "↘", text: "was VFR 09:00Z", worse: true))
        #expect(R.trend(nil) == nil)
    }

    @Test func etaTextFollowsTheRole() throws {
        let g = try glance()
        #expect(R.etaText(try #require(g.departure), role: .departure) == "ETD 10:00Z")
        #expect(R.etaText(try #require(g.destination), role: .arrival) == "ETA 11:35Z")
    }

    @Test func hourLabelsAreZulu() {
        #expect(R.hourLabel("2099-06-30T09:00:00Z") == "09Z")
        #expect(R.hourLabel("2099-06-30T13:00:00+00:00") == "13Z")
        #expect(R.hourLabel("garbage") == "")
    }

    // MARK: Layout

    @Test func theFoldedDepartureGoesBelowTheArrival() throws {
        let g = try glance()
        #expect(R.slots(departure: g.departure, destination: g.destination) == [.departure, .arrival])
        var folded = try #require(g.departure)
        folded.folded = true
        #expect(R.slots(departure: folded, destination: g.destination) == [.arrival, .foldedDeparture])
        #expect(R.slots(departure: nil, destination: g.destination) == [.arrival])
        #expect(R.slots(departure: nil, destination: nil).isEmpty)
    }

    @Test func departureOpenIsRememberedPerFlight() {
        #expect(R.departureOpenKey(flightId: "abc") == "observedDepartureOpen:abc")
    }

    // MARK: Dial

    @Test func theCardsDialCarriesTheTafGhostForTheDepartureToo() throws {
        let json = """
        {"icao": "ZZDP", "runway_wind": {"icao": "ZZDP",
         "runways": [{"id": "07L/25R", "ends": [{"ident": "07L", "heading_true": 70.0},
                                                {"ident": "25R", "heading_true": 250.0}]}],
         "winds": [{"wind": {"source": "metar", "direction_true": 290, "speed_kt": 15}, "ends": []},
                   {"wind": {"source": "taf", "direction_true": 280, "speed_kt": 12}, "ends": []}]}}
        """
        let a = try JSONDecoder.weatherBrief.decode(AirportObservation.self, from: Data(json.utf8))
        let dial = RunwayWindRules.dial(role: "DEP", icao: "zzdp", airport: a, ghost: true)
        #expect(dial.icao == "ZZDP")
        #expect(dial.primary?.wind.source == "metar")
        #expect(dial.ghost?.wind.source == "taf")
        #expect(RunwayWindRules.dial(role: "DEP", icao: "ZZDP", airport: a, ghost: false).ghost == nil)
    }

    // MARK: Accessibility

    @Test func theSpokenSummaryReadsTheCardInOrder() throws {
        let summary = R.accessibilitySummary(try departure(), role: .departure)
        #expect(summary == "Departure ZZDP, MVFR now, VFR at ETD, PROB30 IFR, BKN 2,500 ft, visibility 10 km+, "
                + "Light rain, wind 290/15G25, runway 25R, crosswind 10G16 kt from the left, "
                + "no cells within 30 NM, no lightning ≤20 NM, was VFR 09:00Z, ETD 10:00Z")
    }
}
