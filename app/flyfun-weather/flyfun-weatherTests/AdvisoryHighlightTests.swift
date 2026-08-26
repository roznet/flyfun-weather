//
//  AdvisoryHighlightTests.swift
//  flyfun-weatherTests
//
//  Advisory cross-section highlights (#374) — the iOS mirror of the web's
//  advisory-highlights tests: decode contract (old packs → nil), the
//  representative-model policy, the derive selector's nil paths, the store
//  clearing rules, and the outline seam-merge.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Builders

private func modelResult(
    model: String,
    status: String = "amber",
    highlights: AdvisoryHighlights? = nil,
    domainNm: Double? = nil,
    affectedDomain: String? = nil
) -> ModelAdvisoryResult {
    ModelAdvisoryResult(
        model: model, status: status, detail: "d",
        affectedPoints: 1, totalPoints: 10, affectedPct: 10,
        affectedNm: 5, totalNm: 50, domainNm: domainNm,
        affectedDomain: affectedDomain, crossCheck: nil, mitigations: nil,
        highlights: highlights)
}

private func advisory(
    id: String = "vmc_cruise",
    aggregateStatus: String = "amber",
    perModel: [ModelAdvisoryResult]
) -> RouteAdvisoryResult {
    RouteAdvisoryResult(
        advisoryId: id, aggregateStatus: aggregateStatus, aggregateDetail: "d",
        perModel: perModel, parametersUsed: [:], aggregateMitigations: nil)
}

private func manifest(_ advisories: [RouteAdvisoryResult]) -> AdvisoriesResponse {
    AdvisoriesResponse(
        advisories: advisories, catalog: [], routeName: "r",
        cruiseAltitudeFt: 6000, flightCeilingFt: 11000, totalDistanceNm: 50,
        models: advisories.flatMap { $0.perModel.map(\.model) },
        aggregation: "worst", airportConditions: nil)
}

private let sampleHighlights = AdvisoryHighlights(
    ribbon: [RibbonSegment(distFromNm: 0, distToNm: 50, severity: "green")],
    regions: [],
    peakDistNm: nil)

// MARK: - Decode contract

@Suite struct HighlightDecodeTests {

    @Test func decodesHighlightsFromSnakeCase() throws {
        let json = """
        {"model":"gfs","status":"amber","detail":"d","affected_points":1,
         "total_points":10,"affected_pct":10.0,"affected_nm":5.0,"total_nm":50.0,
         "highlights":{
            "ribbon":[{"dist_from_nm":0,"dist_to_nm":50,"severity":"amber"}],
            "regions":[{"dist_from_nm":10,"dist_to_nm":20,"base_ft":2000,
                        "top_ft":5000,"kind":"cruise_imc","severity":"amber"},
                       {"dist_from_nm":20,"dist_to_nm":30,"base_ft":null,
                        "top_ft":null,"kind":"tower","severity":"red"}],
            "peak_dist_nm":15.0}}
        """
        let result = try JSONDecoder.weatherBrief.decode(ModelAdvisoryResult.self, from: Data(json.utf8))
        let highlights = try #require(result.highlights)
        #expect(highlights.ribbon.count == 1)
        #expect(highlights.ribbon[0].severity == "amber")
        #expect(highlights.regions.count == 2)
        #expect(highlights.regions[0].baseFt == 2000)
        #expect(highlights.regions[1].baseFt == nil)   // full column
        #expect(highlights.regions[1].topFt == nil)
        #expect(highlights.peakDistNm == 15)
    }

    /// Old packs carry no `highlights` key at all — must decode to nil, so the
    /// feature is silently absent and the chip behaves exactly as before.
    @Test func oldPackDecodesToNil() throws {
        let json = """
        {"model":"gfs","status":"amber","detail":"d","affected_points":1,
         "total_points":10,"affected_pct":10.0,"affected_nm":5.0,"total_nm":50.0}
        """
        let result = try JSONDecoder.weatherBrief.decode(ModelAdvisoryResult.self, from: Data(json.utf8))
        #expect(result.highlights == nil)
    }

    /// The shared fixture (a real pre-#373 manifest) still decodes end to end.
    @MainActor @Test func fixtureManifestStillDecodes() {
        #expect(FixtureBriefingData.advisories.advisories
            .allSatisfy { adv in adv.perModel.allSatisfy { $0.highlights == nil } })
    }
}

// MARK: - Representative model + derive selector

@MainActor @Suite struct HighlightDeriveTests {

    @Test func representativeModelPrefersAggregateStatusMatch() {
        let adv = advisory(aggregateStatus: "red", perModel: [
            modelResult(model: "gfs", status: "amber"),
            modelResult(model: "ecmwf", status: "red"),
        ])
        #expect(CrossSectionViewModel.representativeModel(for: adv) == "ecmwf")
    }

    @Test func representativeModelFallsBackToFirstEntry() {
        let adv = advisory(aggregateStatus: "red", perModel: [
            modelResult(model: "gfs", status: "amber"),
            modelResult(model: "ecmwf", status: "green"),
        ])
        #expect(CrossSectionViewModel.representativeModel(for: adv) == "gfs")
        #expect(CrossSectionViewModel.representativeModel(for: advisory(perModel: [])) == nil)
    }

    @Test func deriveReturnsSelectedModelsGeometry() {
        let m = manifest([advisory(perModel: [
            modelResult(model: "gfs"),
            modelResult(model: "ecmwf", highlights: sampleHighlights),
        ])])
        let derived = CrossSectionViewModel.deriveHighlights(
            manifest: m, advisoryId: "vmc_cruise", model: "ecmwf")
        #expect(derived?.ribbon.count == 1)
        #expect(derived?.ribbon.first?.severity == "green")
    }

    @Test func deriveNilPaths() {
        let m = manifest([advisory(perModel: [
            modelResult(model: "ecmwf", highlights: sampleHighlights),
            modelResult(model: "gfs"),  // no highlights for this model
        ])])
        // No tracked advisory / no manifest.
        #expect(CrossSectionViewModel.deriveHighlights(manifest: m, advisoryId: nil, model: "ecmwf") == nil)
        #expect(CrossSectionViewModel.deriveHighlights(manifest: nil, advisoryId: "vmc_cruise", model: "ecmwf") == nil)
        // Advisory vanished (recalc / other pack).
        #expect(CrossSectionViewModel.deriveHighlights(manifest: m, advisoryId: "convective", model: "ecmwf") == nil)
        // Model has no entry, or its entry carries no geometry (old pack).
        #expect(CrossSectionViewModel.deriveHighlights(manifest: m, advisoryId: "vmc_cruise", model: "icon") == nil)
        #expect(CrossSectionViewModel.deriveHighlights(manifest: m, advisoryId: "vmc_cruise", model: "gfs") == nil)
    }
}

// MARK: - Store clearing rules

@MainActor @Suite struct HighlightClearingTests {

    /// The view model persists its layer config to shared `UserDefaults`, and
    /// other suites (e.g. CrossSectionThemeTests' boot-default test) construct
    /// fresh view models expecting clean defaults. Run each test body against
    /// cleared keys and clear again in a `defer` — the body is synchronous
    /// main-actor code, so no other main-actor test can observe the dirty state
    /// mid-flight.
    private static let persistedKeys = [
        "crossSectionThemeId", "crossSectionEnabledLayers",
        "crossSectionAdvisoryPreset", "crossSectionHighlightAdvisory",
    ]

    private func withCleanDefaults(_ body: (CrossSectionViewModel) -> Void) {
        let clear = { for key in Self.persistedKeys { UserDefaults.standard.removeObject(forKey: key) } }
        clear()
        defer { clear() }
        body(CrossSectionViewModel())
    }

    @Test func activationForcesVisibility() {
        withCleanDefaults { vm in
            vm.setHighlightVisible(false)
            vm.setHighlightAdvisory("convective")
            #expect(vm.activeHighlightAdvisoryId == "convective")
            #expect(vm.highlightVisible)   // fresh intent — never an invisible highlight
        }
    }

    @Test func visibilityToggleIsNotACustomEdit() {
        withCleanDefaults { vm in
            vm.applyAdvisoryPreset(CrossSectionPresets.advisory["convective"]!)
            vm.setHighlightAdvisory("convective")
            vm.setHighlightVisible(false)
            // Hiding the highlight clears neither the highlight nor the lens.
            #expect(vm.activeHighlightAdvisoryId == "convective")
            #expect(vm.activeAdvisoryPreset == "convective")
        }
    }

    @Test func manualLayerEditsClearTheHighlight() {
        withCleanDefaults { vm in
            vm.setHighlightAdvisory("convective")
            vm.toggleLayer("freezing-level")
            #expect(vm.activeHighlightAdvisoryId == nil)

            vm.setHighlightAdvisory("convective")
            vm.setMethod("icing-bands", for: .icing)
            #expect(vm.activeHighlightAdvisoryId == nil)

            vm.setHighlightAdvisory("convective")
            vm.applyPreset(.gramet)
            #expect(vm.activeHighlightAdvisoryId == nil)
        }
    }

    @Test func lensChangesClearTheHighlight() {
        withCleanDefaults { vm in
            vm.setHighlightAdvisory("convective")
            // A bare lens from the picker clears it (the chip path re-sets it after).
            vm.applyAdvisoryPreset(CrossSectionPresets.advisory["icing"]!)
            #expect(vm.activeHighlightAdvisoryId == nil)

            vm.setHighlightAdvisory("convective")
            vm.clearAdvisoryPreset()
            #expect(vm.activeHighlightAdvisoryId == nil)
        }
    }

    /// Model switches must NOT clear the highlight — there is deliberately no
    /// model hook in the view model; geometry re-derives per model. Guard the
    /// persistence contract instead: a relaunch restores the tracked advisory.
    @Test func persistedHighlightRestores() {
        withCleanDefaults { vm in
            vm.setHighlightAdvisory("convective")
            let restored = CrossSectionViewModel()
            #expect(restored.activeHighlightAdvisoryId == "convective")
        }
    }
}

// MARK: - Outline seam-merge

@Suite struct HighlightRegionMergeTests {

    private func region(
        _ from: Double, _ to: Double,
        base: Double? = 2000, top: Double? = 5000,
        kind: String = "cruise_imc", severity: String = "amber"
    ) -> VizAdvisoryHighlights.Region {
        .init(distFromNm: from, distToNm: to, baseFt: base, topFt: top,
              kind: kind, severity: severity)
    }

    @Test func mergesAbuttingSameKindSameSeverityRects() {
        let merged = HighlightLayer.mergedRegions([
            region(0, 10), region(10, 20), region(20, 30),
        ])
        #expect(merged.count == 1)
        #expect(merged[0].distFromNm == 0)
        #expect(merged[0].distToNm == 30)
    }

    @Test func keepsDifferingOrGappedRectsApart() {
        let merged = HighlightLayer.mergedRegions([
            region(0, 10),
            region(10, 20, severity: "red"),        // severity break
            region(20, 30, base: 3000),             // span break
            region(35, 40),                         // gap
            region(40, 50, kind: "tower"),          // kind break
        ])
        #expect(merged.count == 5)
    }
}
