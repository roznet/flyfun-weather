//
//  ObservedFlightStripTests.swift
//  flyfun-weatherTests
//
//  #816 — the flight strip and "Destination at your ETA" decode from `/live`
//  (and survive the plain-JSON live cache), the destination block stands in
//  for a non-alert arrival line, passed lines go to Details, and `progress`
//  and a change row's `storm_ids` decode.
//

import Testing
import Foundation
@testable import flyfun_weather

private let glanceJSON = """
{
  "as_of": "2099-06-30T08:30:00Z",
  "headline": "Observed 08:30Z · as briefed",
  "comparison": "as_briefed",
  "lines": [
    {"phase": "departure", "icao": "ZZDP", "text": "ZZDP VFR", "passed": true},
    {"phase": "enroute", "text": "no cells within 30 NM of track ahead", "passed": false},
    {"phase": "arrival", "icao": "ZZAR", "text": "ZZAR VFR · TAF at ETA VFR", "alert": false}
  ],
  "flight": {
    "phase": "enroute", "source": "app",
    "text": "120 of 280 NM · ETA ZZAR 10:32Z (+12 min vs plan) · position 4 min ago",
    "eta": "2099-06-30T10:32:00Z", "eta_delay_min": 12, "along_nm": 120.0, "route_nm": 280.0,
    "fix_age_min": 4.0, "estimated": false, "departed_at": "2099-06-30T08:12:00Z", "landed_at": null,
    "marker_label": "You · 4 min ago"
  },
  "destination": {
    "icao": "ZZAR", "name": "Zedville", "eta": "2099-06-30T10:32:00Z",
    "metar_text": "ZZAR VFR", "metar_category": "VFR", "metar_time": "2099-06-30T08:20:00Z",
    "proxy_caption": null, "taf_text": "TAF at ETA VFR, PROB30 MVFR TS", "taf_category": "VFR",
    "wind_text": "Wind at ETA 240° 18G28 kt · RWY 27 crosswind 12 kt", "wind_advisory": "amber",
    "cells_text": "nearest cell 7 NM SW (52 dBZ), closing 6 kt now",
    "cells": [{"id": "core41-zz", "text": "7 NM SW · 52 dBZ · closing 6 kt",
               "focus": {"kind": "storm", "id": "core41-zz", "bbox": [3.9, 49.8, 4.1, 50.0], "layers": ["cells"], "time": null}}],
    "lightning_text": "no lightning ≤20 NM", "alert": false, "unavailable": [],
    "focus": {"kind": "station", "id": "ZZAR", "bbox": [3.5, 49.6, 4.5, 50.4], "layers": ["route"], "time": null}
  }
}
"""

@MainActor
@Suite struct ObservedFlightStripTests {
    private func glance(_ json: String = glanceJSON) throws -> LiveGlance {
        try JSONDecoder.weatherBrief.decode(LiveGlance.self, from: Data(json.utf8))
    }

    @Test func decodesTheStripAndTheDestination() throws {
        let g = try glance()
        let flight = try #require(g.flight)
        #expect(flight.phase == "enroute")
        #expect(flight.text == "120 of 280 NM · ETA ZZAR 10:32Z (+12 min vs plan) · position 4 min ago")
        #expect(flight.etaDelayMin == 12)
        #expect(flight.markerLabel == "You · 4 min ago")
        #expect(!flight.isLanded)
        let dest = try #require(g.destination)
        #expect(dest.windAdvisory == "amber")
        #expect(dest.cells?.map(\.id) == ["core41-zz"])
        #expect(dest.cells?.first?.focus?.wantsCells == true)
        #expect(dest.focus?.id == "ZZAR")
    }

    @Test func anOlderServerDecodesWithoutThem() throws {
        let older = #"{"as_of": "2099-06-30T08:30:00Z", "headline": "h", "comparison": "as_briefed", "lines": []}"#
        let g = try glance(older)
        #expect(g.flight == nil)
        #expect(g.destination == nil)
    }

    @Test func survivesThePlainJSONLiveCache() throws {
        let original = try glance()
        let cached = try JSONDecoder().decode(LiveGlance.self, from: try JSONEncoder().encode(original))
        #expect(cached.flight == original.flight)
        #expect(cached.destination == original.destination)
    }

    @Test func theDestinationStandsInForTheArrivalLineAndPassedLinesFold() throws {
        let g = try glance()
        #expect(g.aheadItems.map(\.phase) == ["enroute"])
        #expect(g.passedItems.map(\.phase) == ["departure"])
    }

    @Test func withoutADestinationTheArrivalLineStaysAhead() throws {
        var g = try glance()
        g.destination = nil
        #expect(g.aheadItems.map(\.phase) == ["enroute", "arrival"])
    }

    @Test func progressAndChangeStormIdsDecode() throws {
        let json = """
        {"flight_id": "zz", "pack_timestamp": "2099-06-30T06:00:00+00:00", "live_updated_at": "2099-06-30T08:30:00Z",
         "progress": {"source": "app", "as_of": "2099-06-30T08:30:00Z", "along_nm": 120.0, "route_nm": 280.0,
                      "fix_at": "2099-06-30T08:26:00Z", "fix_age_min": 4.0, "estimated": false, "off_route": false,
                      "departed_at": "2099-06-30T08:12:00Z", "eta_destination": "2099-06-30T10:32:00Z",
                      "landed_at": null, "delay_min": 12},
         "changes": {"computed_at": "2099-06-30T08:30:00Z", "changes": [
           {"key": "storm:route", "kind": "radar", "tier": "alert", "message": "Cell 4 NM right of track",
            "storm_ids": ["core41-zz"]}]}}
        """
        let layer = try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(json.utf8))
        #expect(layer.progress?.source == "app")
        #expect(layer.progress?.fixAgeMin == 4.0)
        #expect(layer.progress?.delayMin == 12)
        #expect(layer.changes?.items.first?.stormIds == ["core41-zz"])
    }

    @Test func highlightCaptionCarriesItsAgeAndGoesStale() throws {
        let hl = LiveHighlight(text: "x", model: "m", factsHash: "h", generatedAt: "2099-06-30T08:20:00Z")
        let written = try #require(Date.parseISO8601("2099-06-30T08:20:00Z"))
        let fresh = written.addingTimeInterval(12 * 60)
        #expect(ObservedHighlightCard.caption(hl, now: fresh)
                == "Experimental, still being calibrated. Thanks for flagging issues. · written 08:20Z · 12 min ago")
        #expect(!ObservedHighlightCard.isStale(hl, now: fresh))
        #expect(ObservedHighlightCard.isStale(hl, now: written.addingTimeInterval(31 * 60)))
    }
}
