//
//  ObservedHighlightTests.swift
//  flyfun-weatherTests
//
//  #697 — the Observed highlight: `glance.highlight` decodes from `/live`
//  (and from the plain-JSON live cache), the alert lines split from the rest,
//  the caption carries the written time, and a rating sends the rated line
//  back verbatim in snake_case.
//

import Testing
import Foundation
@testable import flyfun_weather

private let glanceJSON = """
{
  "as_of": "2099-06-30T08:30:00Z",
  "headline": "Observed 08:30Z · 1 worse since the briefing (arrival)",
  "comparison": "worse",
  "lines": [
    {"phase": "departure", "icao": "ZZDP", "text": "ZZDP VFR"},
    {"phase": "enroute", "text": "EMBD TS SIGMET ZZFR 3 covers last 40 NM", "alert": true},
    {"phase": "arrival", "icao": "ZZAR", "text": "ZZAR VFR", "alert": false}
  ],
  "highlight": {
    "text": "New EMBD TS SIGMET ZZFR 3 from 235 NM to ZZAR.",
    "model": "fixture",
    "facts_hash": "hash-9f3e",
    "gate": null,
    "generated_at": "2099-06-30T08:20:00Z",
    "latency_ms": null
  }
}
"""

@MainActor
@Suite struct ObservedHighlightTests {
    private func glance(_ json: String = glanceJSON) throws -> LiveGlance {
        try JSONDecoder.weatherBrief.decode(LiveGlance.self, from: Data(json.utf8))
    }

    @Test func decodesTheHighlight() throws {
        let hl = try #require(try glance().highlight)
        #expect(hl.text == "New EMBD TS SIGMET ZZFR 3 from 235 NM to ZZAR.")
        #expect(hl.model == "fixture")
        #expect(hl.factsHash == "hash-9f3e")
        #expect(hl.generatedAt == "2099-06-30T08:20:00Z")
    }

    @Test func aNullOrMissingHighlightDecodesAsNil() throws {
        let null = #"{"as_of": "2099-06-30T08:30:00Z", "headline": "h", "comparison": "as_briefed", "lines": [], "highlight": null}"#
        #expect(try glance(null).highlight == nil)
        let older = #"{"as_of": "2099-06-30T08:30:00Z", "headline": "h", "comparison": "as_briefed", "lines": []}"#
        #expect(try glance(older).highlight == nil)
    }

    @Test func survivesThePlainJSONLiveCache() throws {
        let original = try glance()
        let cached = try JSONDecoder().decode(LiveGlance.self, from: try JSONEncoder().encode(original))
        #expect(cached.highlight == original.highlight)
    }

    @Test func alertLinesSplitFromTheRestInServerOrder() throws {
        let g = try glance()
        #expect(g.alertItems.map(\.phase) == ["enroute"])
        #expect(g.otherItems.map(\.phase) == ["departure", "arrival"])
    }

    @Test func captionCarriesTheWrittenTime() throws {
        let hl = try #require(try glance().highlight)
        #expect(ObservedHighlightCard.caption(hl)
                == "Experimental, still being calibrated. Thanks for flagging issues. · written 08:20Z")
        let undated = LiveHighlight(text: "x", model: "m", factsHash: "h", generatedAt: "nope")
        #expect(ObservedHighlightCard.caption(undated)
                == "Experimental, still being calibrated. Thanks for flagging issues.")
    }

    @Test func ratingSendsTheRatedLineVerbatim() throws {
        let hl = try #require(try glance().highlight)
        let request = HighlightFeedbackRequest(
            flightId: "zzdp_zzar-2099-06-30-abcd", packTimestamp: "2099-06-30T06:00:00+00:00",
            highlight: hl, sentiment: "down", comment: "Cell was west", contactOk: false
        )
        let data = try JSONEncoder.weatherBrief.encode(request)
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["category"] as? String == "highlight_rating")
        #expect(json["target"] as? String == "live_highlight")
        #expect(json["sentiment"] as? String == "down")
        #expect(json["comment"] as? String == "Cell was west")
        #expect(json["contact_ok"] as? Bool == false)
        #expect(json["pack_timestamp"] as? String == "2099-06-30T06:00:00+00:00")
        let context = try #require(json["context"] as? [String: String])
        #expect(context == [
            "facts_hash": "hash-9f3e",
            "generated_at": "2099-06-30T08:20:00Z",
            "model": "fixture",
            "text": "New EMBD TS SIGMET ZZFR 3 from 235 NM to ZZAR.",
        ])
    }
}
