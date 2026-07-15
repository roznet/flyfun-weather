//
//  DebriefTaxonomyTests.swift
//  flyfun-weatherTests
//
//  Decode/encode regression tests for the debrief feature (#430). The key risk,
//  mirrored from ForecastMapTests, is the JSON key strategy mangling dictionary
//  keys: `advisory_tag_map` ids (icing_escape, vfr_feasibility) contain
//  underscores, and `outcomes` is keyed by tag ids that must NOT be lowercased
//  (IMC → imc) on the way out. These tests pin that behaviour.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("DebriefTaxonomy")
struct DebriefTaxonomyTests {

    /// A help-catalog payload carrying a `debrief` section (metrics/advisories
    /// trimmed to the minimum each decode path needs).
    static let catalogJSON = """
    {
      "version": "deadbeef",
      "metrics": { "cape_surface_jkg": { "name": "CAPE" } },
      "advisories": [],
      "debrief": {
        "decisions": [
          { "id": "flown", "label": "Flown" },
          { "id": "cancelled", "label": "Cancelled" },
          { "id": "monitoring", "label": "Monitor only" }
        ],
        "tags": [
          { "id": "IMC", "label": "IMC", "description": "Low ceilings", "outcome_category": true },
          { "id": "OPS", "label": "Operational", "description": "Non-weather", "outcome_category": false }
        ],
        "outcome_values": [
          { "id": "consistent", "label": "As forecast" },
          { "id": "worse", "label": "Worse than forecast" }
        ],
        "advisory_tag_map": { "icing_escape": "ICE", "vfr_feasibility": "IMC" },
        "note_max_length": 300
      }
    }
    """

    @Test("Help catalog decodes the debrief section with underscored map keys intact")
    func decodesDebriefSection() throws {
        let catalog = try HelpCatalogResponse.decode(from: Data(Self.catalogJSON.utf8))
        let debrief = try #require(catalog.debrief)

        #expect(debrief.decisions.map(\.id) == ["flown", "cancelled", "monitoring"])
        #expect(debrief.noteMaxLength == 300)

        // outcome_category → outcomeCategory via explicit CodingKeys.
        #expect(debrief.tag("IMC")?.outcomeCategory == true)
        #expect(debrief.tag("OPS")?.outcomeCategory == false)
        #expect(debrief.outcomeCategoryTags.map(\.id) == ["IMC"])

        // The underscored advisory-map keys must survive verbatim (the plain
        // decoder path); a snake→camel strategy would break the lookup.
        #expect(debrief.advisoryTagMap["icing_escape"] == "ICE")
        #expect(debrief.advisoryTagMap["vfr_feasibility"] == "IMC")
    }

    @Test("Missing debrief key decodes to nil (old cached payload)")
    func backwardCompatibleWithoutDebrief() throws {
        let json = #"{ "version": "v", "metrics": { "x": { "name": "X" } }, "advisories": [] }"#
        let catalog = try HelpCatalogResponse.decode(from: Data(json.utf8))
        #expect(catalog.debrief == nil)
    }

    @Test("flaggedTagIds maps AMBER/RED advisories to tags in taxonomy order")
    func flaggedTags() {
        let taxonomy = DebriefTaxonomy.bundledBaseline
        let flagged = taxonomy.flaggedTagIds(fromAdvisories: [
            (id: "convective", status: "red"),
            (id: "icing_escape", status: "amber"),
            (id: "vmc_cruise", status: "green"),   // green → ignored
            (id: "unknown_id", status: "red"),      // unmapped → ignored
        ])
        // ICE precedes TS in the taxonomy tag order.
        #expect(flagged == ["ICE", "TS"])
    }

    @Test("DebriefResponse decode keeps tag-keyed outcomes verbatim")
    func decodesOutcomesDict() throws {
        let json = """
        { "flight_id": "f1", "decision": "flown", "reasons": [],
          "outcomes": { "IMC": "worse", "ICE": "consistent" }, "note": null,
          "created_at": "2026-07-15T10:00:00Z", "updated_at": "2026-07-15T10:00:00Z" }
        """
        let d = try JSONDecoder.weatherBrief.decode(DebriefResponse.self, from: Data(json.utf8))
        #expect(d.flightId == "f1")
        // Single-word keys must NOT be lowercased by .convertFromSnakeCase.
        #expect(d.outcomes["IMC"] == "worse")
        #expect(d.outcomes["ICE"] == "consistent")
    }

    @Test("DebriefRequest encodes tag-id keys without snake-casing")
    func encodesOutcomeKeysVerbatim() throws {
        let req = DebriefRequest(decision: "flown", reasons: [], outcomes: ["IMC": "worse"], note: nil)
        let data = try req.encoded()
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let outcomes = try #require(obj?["outcomes"] as? [String: Any])
        // Must be "IMC", never "imc" — a snake-casing encoder would break server
        // enum validation.
        #expect(outcomes["IMC"] as? String == "worse")
        #expect(outcomes["imc"] == nil)
    }
}
