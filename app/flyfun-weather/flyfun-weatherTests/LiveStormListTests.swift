//
//  LiveStormListTests.swift
//  flyfun-weatherTests
//
//  #816 — the Observed tab's Cells list and chip read the server's `/live`
//  storms (the ribbon's list): ahead / behind in the server's order, the
//  corridor from the server, closing and abeam in its words, and a dark feed
//  says why instead of "no cells".
//

import Testing
import Foundation
@testable import flyfun_weather

private let stormsJSON = """
{"status": "available", "frame_time": "2099-06-30T08:25:00Z", "corridor_nm": 30.0, "route_nm": 120.0,
 "lightning_pending": false,
 "storms": [
   {"id": "core35-behind", "lat": 43.5, "lon": 5.2, "peak_dbz": 44.0, "along_nm": 10.0, "offtrack_nm": 12.0,
    "cross_nm": -12.0, "side": "left", "ahead": false, "relative_motion": "moving_away", "closing_kt": -5.0},
   {"id": "core35-zz", "lat": 43.6, "lon": 5.5, "peak_dbz": 48.0, "intensity": "heavy", "flashes": 3,
    "trend": "developing", "top_fl": 320, "along_nm": 40.0, "offtrack_nm": 6.0, "cross_nm": 6.0, "side": "right",
    "ahead": true, "relative_motion": "closing", "closing_kt": 7.2, "abeam_eta": "2099-06-30T08:52:00Z",
    "minutes_to_abeam": 26.4},
   {"id": "core41-dest", "lat": 43.9, "lon": 5.9, "peak_dbz": 52.0, "along_nm": 125.0, "offtrack_nm": 8.0,
    "cross_nm": 0.0, "end": "destination", "end_icao": "ZZAR", "end_bearing": "NE", "ahead": true,
    "relative_motion": "unknown", "flashes_pending": true, "truncated": true}
 ]}
"""

@MainActor
@Suite struct LiveStormListTests {
    private func storms(_ json: String = stormsJSON) throws -> LiveStorms {
        try JSONDecoder.weatherBrief.decode(LiveStorms.self, from: Data(json.utf8))
    }

    @Test func splitsAheadAndBehindInServerOrder() throws {
        let s = try storms()
        #expect(LiveStormListRules.ahead(s).map(\.id) == ["core35-zz", "core41-dest"])
        #expect(LiveStormListRules.behind(s).map(\.id) == ["core35-behind"])
    }

    @Test func chipCountsStormsAheadWithinTheServerCorridor() throws {
        #expect(LiveStormListRules.chipText(try storms()) == "2 cells ahead within 30 NM of track")
        let none = try storms(#"{"status": "available", "corridor_nm": 30.0, "storms": []}"#)
        #expect(LiveStormListRules.chipText(none) == "No cells ahead within 30 NM of track")
        // A dark feed is not "no cells": no chip, the list says why.
        let stale = try storms(#"{"status": "stale", "corridor_nm": 30.0, "unavailable_since": "2099-06-30T07:40:00Z"}"#)
        #expect(LiveStormListRules.chipText(stale) == nil)
        #expect(LiveStormListRules.chipText(nil) == nil)
    }

    @Test func statusSaysWhyTheListIsMissing() throws {
        #expect(LiveStormListRules.statusText(try storms()) == nil)
        let stale = try storms(#"{"status": "stale", "corridor_nm": 30.0, "unavailable_since": "2099-06-30T07:40:00Z"}"#)
        #expect(LiveStormListRules.statusText(stale) == "Radar cells unavailable since 07:40Z")
        let off = try storms(#"{"status": "disabled", "corridor_nm": 30.0}"#)
        #expect(LiveStormListRules.statusText(off) == "Radar cells are not enabled on this server")
        let gone = try storms(#"{"status": "unavailable", "corridor_nm": 30.0}"#)
        #expect(LiveStormListRules.statusText(gone) == "Radar cells unavailable")
    }

    @Test func captionNamesTheCorridorAndTheFrame() throws {
        let now = try #require(Date.parseISO8601("2099-06-30T08:31:00Z"))
        #expect(LiveStormListRules.caption(try storms(), now: now)
                == "Cells within 30 NM of track, in route order · radar 08:25Z (6 min ago)")
    }

    @Test func rowWordsComeFromTheServer() throws {
        let s = try storms()
        let zz = try #require(s.items.first { $0.id == "core35-zz" })
        #expect(LiveStormListRules.title(zz) == "Heavy cell")
        #expect(LiveStormListRules.abeamText(zz) == "abeam in ~25 min (~08:52Z)")
        #expect(LiveStormListRules.detailLine(zz)
                == "closing 7 kt · abeam in ~25 min (~08:52Z) · developing · 3 flashes · top FL320")
        #expect(RouteRibbonRules.stormPositionText(zz) == "6 NM right of track at 40 NM")

        // Past the destination: no abeam, named from the airport.
        let dest = try #require(s.items.first { $0.id == "core41-dest" })
        #expect(LiveStormListRules.title(dest) == "Cell")
        #expect(LiveStormListRules.abeamText(dest) == nil)
        #expect(LiveStormListRules.detailLine(dest)
                == "motion not yet measured · lightning pending · partly outside radar coverage")
        #expect(RouteRibbonRules.stormPositionText(dest) == "8 NM NE of ZZAR")

        // Behind: no abeam time.
        let behind = try #require(s.items.first { $0.id == "core35-behind" })
        #expect(LiveStormListRules.abeamText(behind) == nil)
        #expect(LiveStormListRules.detailLine(behind) == "moving away 5 kt")
    }

    /// The row's dot colours `LiveStorm.trend` through the map's table: every
    /// word the server sends (models/live.py `LiveStorm.trend`) must have a
    /// colour, or the dot falls back to the unknown grey without a word.
    @Test func serverTrendWordsAllHaveAColour() {
        for word in ["developing", "steady", "decaying", "mixed", "new"] {
            #expect(CellsOverlay.trendHex[word] != nil, "no colour for server trend \(word)")
        }
    }

    @Test func abeamRoundsLikeTheServer() throws {
        func storm(_ minutes: Double?, eta: String? = nil) throws -> LiveStorm {
            let m = minutes.map { "\($0)" } ?? "null"
            let e = eta.map { "\"\($0)\"" } ?? "null"
            let json = #"{"id": "s", "along_nm": 1, "offtrack_nm": 1, "cross_nm": 1, "ahead": true, "minutes_to_abeam": "#
                + m + #", "abeam_eta": "# + e + "}"
            return try JSONDecoder.weatherBrief.decode(LiveStorm.self, from: Data(json.utf8))
        }
        #expect(LiveStormListRules.abeamText(try storm(1.5)) == "abeam now")
        #expect(LiveStormListRules.abeamText(try storm(7.4)) == "abeam in ~7 min")
        #expect(LiveStormListRules.abeamText(try storm(43)) == "abeam in ~45 min")
        #expect(LiveStormListRules.abeamText(try storm(nil, eta: "2099-06-30T09:05:00Z")) == "abeam ~09:05Z")
        #expect(LiveStormListRules.abeamText(try storm(nil)) == nil)
    }
}
