//
//  RouteRibbonRulesTests.swift
//  flyfun-weatherTests
//
//  The Observed ribbon's pure rules (#690).
//
//  SYNC — these cases mirror web/tests/unit/ribbon-core.test.ts one for one,
//  with the same inputs and the same expected strings. The two files are the
//  guarantee that `RouteRibbonRules.swift` and `ribbon-core.ts` still agree:
//  change a rule on one platform and the other platform's test is what tells
//  you the ports have parted. The two documented divergences (the MVFR colour
//  and the dBZ palette) are deliberately NOT asserted as equal here — see the
//  banner in RouteRibbonRules.swift.
//

import Testing
import Foundation
@testable import flyfun_weather

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try JSONDecoder.weatherBrief.decode(type, from: Data(json.utf8))
}

/// The web helper's defaults, as JSON so the optional fields stay honest.
private func storm(
    relativeMotion: String = "moving_away",
    closingKt: String = "-15.0",
    side: String = "\"left\"",
    end: String = "null",
    endIcao: String = "null",
    endBearing: String = "null",
    peakDbz: String = "47.0",
    flashes: String = "null"
) throws -> LiveStorm {
    try decode(LiveStorm.self, """
    {"id": "c1", "cell_ids": ["c1"], "lat": 50.0, "lon": 0.0, "peak_dbz": \(peakDbz),
     "along_nm": 60.0, "offtrack_nm": 25.0, "cross_nm": -25.0, "side": \(side),
     "end": \(end), "end_icao": \(endIcao), "end_bearing": \(endBearing),
     "flashes": \(flashes),
     "relative_motion": "\(relativeMotion)", "closing_kt": \(closingKt)}
    """)
}

private func segment(status: String, dbz: String = "null") throws -> LiveRibbonSegment {
    try decode(LiveRibbonSegment.self, """
    {"index": 0, "from_nm": 0.0, "to_nm": 25.0, "radar_status": "\(status)", "radar_max_dbz": \(dbz)}
    """)
}

private func band(tier: String = "core", peakDbz: String = "47.0") throws -> RibbonWeather {
    try decode(RibbonWeather.self, """
    {"id": "b1", "tier": "\(tier)", "from_nm": 10.0, "to_nm": 30.0, "side": "left",
     "near_nm": 5.0, "far_nm": 20.0, "peak_dbz": \(peakDbz)}
    """)
}

@Suite struct RouteRibbonRulesTests {

    // MARK: Geometry

    @Test func mapsAlongRouteDistanceClampedAtBothEnds() {
        let width: CGFloat = 520
        #expect(RouteRibbonRules.x(0, routeNm: 100, width: width) == RouteRibbonRules.inset)
        #expect(RouteRibbonRules.x(100, routeNm: 100, width: width) == width - RouteRibbonRules.inset)
        // A cell past the route's end sits ON the end, never off the drawing.
        #expect(RouteRibbonRules.x(180, routeNm: 100, width: width) == width - RouteRibbonRules.inset)
        #expect(RouteRibbonRules.x(-20, routeNm: 100, width: width) == RouteRibbonRules.inset)
    }

    @Test func neverDividesByAZeroRouteLength() {
        #expect(RouteRibbonRules.x(10, routeNm: 0, width: 520).isFinite)
    }

    @Test func putsLeftOfCourseAboveTheLineAndRightBelow() {
        #expect(RouteRibbonRules.y(cross: 0, corridor: 30) == RouteRibbonRules.trackY)
        #expect(RouteRibbonRules.y(cross: -30, corridor: 30) == RouteRibbonRules.zoneTop)
        #expect(RouteRibbonRules.y(cross: 30, corridor: 30) == RouteRibbonRules.zoneBottom)
        // Beyond the corridor clamps to its edge rather than drawing outside.
        #expect(RouteRibbonRules.y(cross: -90, corridor: 30) == RouteRibbonRules.zoneTop)
        #expect(RouteRibbonRules.y(cross: 90, corridor: 30) == RouteRibbonRules.zoneBottom)
    }

    // MARK: Marks

    @Test func pointsTheArrowAtTheTrackWhenClosingAndAwayWhenLeaving() throws {
        // cross < 0 is left of course, drawn ABOVE the line: closing is downward.
        #expect(RouteRibbonRules.motionArrow(try storm(relativeMotion: "closing"), cross: -25) == "arrow.down")
        #expect(RouteRibbonRules.motionArrow(try storm(relativeMotion: "moving_away"), cross: -25) == "arrow.up")
        #expect(RouteRibbonRules.motionArrow(try storm(relativeMotion: "closing"), cross: 25) == "arrow.up")
        #expect(RouteRibbonRules.motionArrow(try storm(relativeMotion: "moving_away"), cross: 25) == "arrow.down")
    }

    @Test func drawsNoArrowWhenTheMotionWasNotMeasured() throws {
        #expect(RouteRibbonRules.motionArrow(try storm(relativeMotion: "unknown"), cross: 10) == nil)
        #expect(RouteRibbonRules.motionArrow(try storm(relativeMotion: "parallel"), cross: 10) == nil)
    }

    @Test func sizesTheMarkerByStrength() {
        #expect(RouteRibbonRules.stormMarkSize(20) == 10)
        #expect(RouteRibbonRules.stormMarkSize(41) == 14)
        #expect(RouteRibbonRules.stormMarkSize(50) == 18)
        #expect(RouteRibbonRules.stormMarkSize(nil) == 10)
    }

    // MARK: Words

    @Test func namesAStretchOfRouteByWhatTheRadarDidThere() throws {
        #expect(RouteRibbonRules.segmentLabel(try segment(status: "measured", dbz: "47.0"))
                == "0–25 NM: radar peak 47 dBZ")
        #expect(RouteRibbonRules.segmentLabel(try segment(status: "measured"))
                == "0–25 NM: no radar echo")
        #expect(RouteRibbonRules.segmentLabel(try segment(status: "no_coverage"))
                == "0–25 NM: radar coverage insufficient")
        #expect(RouteRibbonRules.segmentLabel(try segment(status: "no_sample"))
                == "0–25 NM: no radar sample")
    }

    @Test func saysMetarUnavailableRatherThanImplyingItIsClear() throws {
        let st = try decode(RibbonStation.self, #"{"icao": "LPPR", "role": "departure"}"#)
        #expect(RouteRibbonRules.stationLabel(st) == "LPPR METAR unavailable")
    }

    @Test func readsAnEnRouteStationWithItsGroupsAndSideOfCourse() throws {
        let st = try decode(RibbonStation.self, """
        {"icao": "LFMT", "role": "route", "metar_category": "VFR", "convective": ["CB", "TS"],
         "taf_category_at_eta": "MVFR", "taf_temporary_type": "PROB30",
         "taf_temporary_category": "IFR", "cross_nm": -12.0}
        """)
        #expect(RouteRibbonRules.stationLabel(st)
                == "LFMT VFR CB TS TAF at ETA MVFR PROB30 IFR 12 NM left of course")
    }

    @Test func placesACellAgainstTheTrackOrOffAnEndFromItsAirport() throws {
        #expect(RouteRibbonRules.stormPositionText(try storm()) == "25 NM left of track at 60 NM")
        #expect(RouteRibbonRules.stormPositionText(try storm(side: "null")) == "on track at 60 NM")
        #expect(RouteRibbonRules.stormPositionText(
            try storm(end: "\"departure\"", endIcao: "\"LPPR\"", endBearing: "\"NE\"")
        ) == "25 NM NE of LPPR")
    }

    @Test func statesObservedMotionAndSaysSoWhenThereIsNone() throws {
        #expect(RouteRibbonRules.stormMotionText(try storm()) == "moving away 15 kt")
        #expect(RouteRibbonRules.stormMotionText(try storm(relativeMotion: "closing", closingKt: "8.0"))
                == "closing 8 kt")
        #expect(RouteRibbonRules.stormMotionText(try storm(relativeMotion: "closing", closingKt: "null"))
                == "closing")
        #expect(RouteRibbonRules.stormMotionText(try storm(relativeMotion: "parallel"))
                == "moving along the track")
        #expect(RouteRibbonRules.stormMotionText(try storm(relativeMotion: "stationary"))
                == "nearly stationary")
        #expect(RouteRibbonRules.stormMotionText(try storm(relativeMotion: "unknown"))
                == "motion not yet measured")
    }

    @Test func turnsMotionRelativeToTheCourseIntoWordsAt30And150() {
        #expect(RouteRibbonRules.relativeMotionText(0) == "moving along the course")
        #expect(RouteRibbonRules.relativeMotionText(30) == "moving along the course")
        #expect(RouteRibbonRules.relativeMotionText(31) == "drifting toward the right of course")
        #expect(RouteRibbonRules.relativeMotionText(-31) == "drifting toward the left of course")
        #expect(RouteRibbonRules.relativeMotionText(150) == "moving against the course")
        #expect(RouteRibbonRules.relativeMotionText(-179) == "moving against the course")
    }

    @Test func summarisesTheZonesWithoutClaimingClear() throws {
        #expect(RouteRibbonRules.weatherSummary([]) == "No rain or cells within the corridor")
        let bands = [try band(tier: "rain", peakDbz: "22.0"), try band()]
        #expect(RouteRibbonRules.weatherSummary(bands)
                == "1 rain areas, 1 cells along the route, strongest 47 dBZ")
    }

    @Test func namesASigmetByItsFirAndHazardFallingBackToSigmet() throws {
        let full = try decode(RibbonSigmet.self, """
        {"id": "sigmet:LECM|6", "label": "LECM 6: EMBD TS", "hazard": "TS", "qualifier": "EMBD"}
        """)
        #expect(RouteRibbonRules.sigmetText(full) == "LECM 6 EMBD TS")
        let bare = try decode(RibbonSigmet.self, #"{"id": "sigmet:LECM|6", "label": "LECM 6: ???"}"#)
        #expect(RouteRibbonRules.sigmetText(bare) == "LECM 6 SIGMET")
    }

    /// The phase label is title case on both clients; each shouts it in its
    /// own presentation layer (SwiftUI `.uppercased()`, CSS `text-transform`).
    @Test func labelsThePhasesTheWayTheNutshellReadsThem() throws {
        let dep = try decode(LiveGlanceLine.self, #"{"phase": "departure", "text": "x"}"#)
        let enr = try decode(LiveGlanceLine.self, #"{"phase": "enroute", "text": "x"}"#)
        let arr = try decode(LiveGlanceLine.self, #"{"phase": "arrival", "text": "x"}"#)
        #expect(dep.phaseLabel == "Departure")
        #expect(enr.phaseLabel == "En route")
        #expect(arr.phaseLabel == "Arrival")
    }

    // MARK: The cell label that stitches position and motion together

    @Test func describesACellInOneLine() throws {
        #expect(RouteRibbonRules.stormLabel(try storm(flashes: "3"))
                == "Cell 47 dBZ, 25 NM left of track at 60 NM, moving away 15 kt, 3 flashes")
        // No flashes reported: the clause is absent, not "0 flashes".
        #expect(RouteRibbonRules.stormLabel(try storm())
                == "Cell 47 dBZ, 25 NM left of track at 60 NM, moving away 15 kt")
    }
}
