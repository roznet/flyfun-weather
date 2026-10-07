//
//  LiveGlanceTests.swift
//  flyfun-weatherTests
//
//  The Observed tab's nutshell, route ribbon, storms and tap-to-map focus
//  (#688, #690): the `/live` blocks decode from the server's snake_case JSON,
//  survive the plain-JSON live cache, and a focus frames its box.
//

import Testing
import Foundation
import MapKit
@testable import flyfun_weather

private let liveJSON = """
{
  "flight_id": "fixture-1",
  "pack_timestamp": "2099-06-30T06:00:00+00:00",
  "live_updated_at": "2099-06-30T11:30:00Z",
  "glance": {
    "as_of": "2099-06-30T11:30:00Z",
    "headline": "Observed 11:30Z · as briefed",
    "comparison": "as_briefed",
    "lines": [
      {"phase": "departure", "icao": "ZZDP", "alert": false, "passed": false,
       "text": "ZZDP VFR · nearest cell 9 NM NE (46 dBZ), moving away 8 kt · no lightning ≤20 NM",
       "unavailable": [], "sources": ["metar:ZZDP", "storm:core35-dep"],
       "focus": {"kind": "station", "id": "ZZDP", "bbox": [-0.52, 49.67, 0.52, 50.33],
                 "layers": ["route", "radar", "cells", "lightning", "sigmets"], "time": null}},
      {"phase": "enroute", "icao": null, "alert": true, "passed": false,
       "text": "radar cells unavailable · SIGMETs unavailable",
       "unavailable": ["storms", "sigmets"], "sources": [], "focus": null},
      {"phase": "arrival", "icao": "ZZDS", "text": "ZZDS VFR · no TAF for ETA"}
    ]
  },
  "ribbon": {
    "route_nm": 154.3, "flown_nm": 0.0, "segment_nm": 10.3, "radar_radius_nm": 10.0,
    "departure_at": "2099-06-30T12:00:00Z", "arrival_at": "2099-06-30T13:30:00Z",
    "radar_time": "2099-06-30T11:25:00Z",
    "waypoints": [{"icao": "ZZDP", "along_nm": 0.0, "eta": "2099-06-30T12:00:00Z"},
                  {"icao": "ZZDS", "along_nm": 154.3, "eta": "2099-06-30T13:30:00Z"}],
    "segments": [
      {"index": 0, "from_nm": 0.0, "to_nm": 10.3, "radar_max_dbz": 47.0, "radar_intensity": "heavy",
       "radar_status": "measured", "lightning": false, "sigmet_ids": ["sigmet:ZZZZ|6"], "storm_ids": [],
       "focus": {"kind": "segment", "id": "seg:0", "bbox": [-0.2, 49.8, 0.3, 50.2], "layers": ["route"]}},
      {"index": 1, "from_nm": 10.3, "to_nm": 20.6, "radar_status": "no_coverage"}
    ],
    "stations": [{"icao": "ZZDP", "role": "departure", "along_nm": 0.0, "cross_nm": 0.0,
                  "metar_category": "VFR", "convective": ["CB", "TS"],
                  "taf_category_at_eta": "VFR", "taf_temporary_type": "PROB30",
                  "taf_temporary_category": "MVFR", "taf_weather": ["TS"]}],
    "sigmets": [{"id": "sigmet:ZZZZ|6", "label": "ZZZZ 6: EMBD TS", "hazard": "TS", "qualifier": "EMBD",
                 "from_nm": 0.0, "to_nm": 100.0, "pending": false, "new": false, "motion": "away"}]
  },
  "storms": {
    "status": "available", "frame_time": "2099-06-30T11:25:00Z", "corridor_nm": 30.0, "route_nm": 154.3,
    "storms": [{
      "id": "core35-a", "cell_ids": ["core35-a", "core41-b"], "lat": 50.4, "lon": 0.9,
      "peak_dbz": 48.0, "intensity": "heavy", "flashes": 1, "trend": "developing",
      "d_peak_db": 3.0, "area_ratio": 1.4, "d_flashes": 1, "motion_status": "available",
      "speed_kt": 15.0, "toward_deg": 0.0, "along_nm": 60.0, "offtrack_nm": 25.0, "cross_nm": -25.0,
      "side": "left", "abeam_eta": "2099-06-30T12:35:00Z", "minutes_to_abeam": 65.0, "ahead": true,
      "relative_motion": "moving_away", "closing_kt": -15.0,
      "history": [{"at": "2099-06-30T11:05:00Z", "offtrack_nm": 20.0, "cross_nm": -20.0}],
      "backing": [], "estimate": {"cpa_nm": 25.0, "cpa_time": "2099-06-30T11:30:00Z",
                                  "at_eta_offtrack_nm": 41.0, "horizon_min": 5.0},
      "focus": {"kind": "storm", "id": "core35-a", "bbox": [0.7, 49.8, 1.1, 50.6],
                "layers": ["route", "radar", "cells", "lightning"], "time": "2099-06-30T11:25:00Z"}
    }]
  }
}
"""

@MainActor
@Suite struct LiveGlanceDecodeTests {
    private func layer() throws -> LiveLayerResponse {
        try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(liveJSON.utf8))
    }

    @Test func decodesTheNutshellWordForWord() throws {
        let glance = try #require(try layer().glance)
        #expect(glance.headline == "Observed 11:30Z · as briefed")
        #expect(glance.items.map(\.phase) == ["departure", "enroute", "arrival"])
        #expect(glance.items[0].text?.hasPrefix("ZZDP VFR · nearest cell 9 NM NE") == true)
        #expect(glance.items[1].alert == true)
        #expect(glance.items[1].unavailable == ["storms", "sigmets"])
        #expect(glance.items[1].focus == nil)
        // A line missing optional fields still decodes.
        #expect(glance.items[2].alert == nil)
        #expect(glance.items.map(\.phaseLabel) == ["Departure", "En route", "Arrival"])
    }

    @Test func decodesTheRibbonLanes() throws {
        let ribbon = try #require(try layer().ribbon)
        #expect(ribbon.routeNm == 154.3)
        #expect(ribbon.segments?.count == 2)
        #expect(ribbon.segments?.first?.radarMaxDbz == 47.0)
        #expect(ribbon.segments?.last?.radarStatus == "no_coverage")
        #expect(ribbon.segments?.first?.focus?.bbox == [-0.2, 49.8, 0.3, 50.2])
        #expect(ribbon.stations?.first?.tafTemporaryType == "PROB30")
        #expect(ribbon.stations?.first?.convective == ["CB", "TS"])
        #expect(ribbon.sigmets?.first?.motion == "away")
        #expect(ribbon.sigmets?.first?.new == false)
    }

    @Test func decodesStormsWithTheEstimateApart() throws {
        let storms = try #require(try layer().storms)
        #expect(storms.isAvailable)
        let storm = try #require(storms.items.first)
        #expect(storm.dPeakDb == 3.0)
        #expect(storm.offtrackNm == 25.0)
        #expect(storm.estimate?.cpaNm == 25.0)
        #expect(storm.estimate?.atEtaOfftrackNm == 41.0)
        #expect(storm.history?.first?.offtrackNm == 20.0)
        #expect(storm.focus?.wantsCells == true)
        #expect(RouteRibbonRules.stormPositionText(storm) == "25 NM left of track at 60 NM")
        #expect(RouteRibbonRules.stormMotionText(storm) == "moving away 15 kt")
    }

    /// The live cache stores the layer with a plain encoder (camelCase keys);
    /// the new blocks must come back intact after a relaunch.
    @Test func survivesThePlainJSONLiveCache() throws {
        let original = try layer()
        let data = try JSONEncoder().encode(original)
        let cached = try JSONDecoder().decode(LiveLayerResponse.self, from: data)
        #expect(cached.glance?.headline == original.glance?.headline)
        #expect(cached.ribbon?.segments?.count == 2)
        #expect(cached.storms?.items.first?.focus == original.storms?.items.first?.focus)
    }

    @Test func olderServerWithoutTheBlocksDecodes() throws {
        let json = #"{"flight_id": "f", "pack_timestamp": "2099-06-30T06:00:00+00:00", "live_updated_at": null}"#
        let layer = try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(json.utf8))
        #expect(layer.glance == nil && layer.ribbon == nil && layer.storms == nil)
    }
}

@MainActor
@Suite struct LiveFocusTests {
    @Test func regionFramesTheBox() throws {
        let focus = LiveFocus(kind: "storm", id: "s", bbox: [0.7, 49.8, 1.1, 50.6], layers: ["cells"], time: nil)
        let region = try #require(focus.region)
        #expect(abs(region.center.latitude - 50.2) < 1e-9)
        #expect(abs(region.center.longitude - 0.9) < 1e-9)
        #expect(region.span.latitudeDelta >= 0.8)
        #expect(region.span.longitudeDelta >= 0.4)
    }

    @Test func malformedBoxHasNoRegion() {
        #expect(LiveFocus(kind: nil, id: nil, bbox: [1, 2, 3], layers: nil, time: nil).region == nil)
        #expect(LiveFocus(kind: nil, id: nil, bbox: [1, 2, 0, 3], layers: nil, time: nil).region == nil)
        #expect(LiveFocus(kind: nil, id: nil, bbox: nil, layers: nil, time: nil).region == nil)
    }

    @Test func layersDriveTheMap() {
        let storm = LiveFocus(kind: "storm", id: "s", bbox: nil, layers: ["route", "radar", "cells"], time: nil)
        #expect(storm.wantsCells && storm.wantsRadar)
        let sigmet = LiveFocus(kind: "sigmet", id: "x", bbox: nil, layers: ["route", "sigmets"], time: nil)
        #expect(!sigmet.wantsCells && !sigmet.wantsRadar)
    }
}
