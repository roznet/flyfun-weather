//
//  CrossSectionLensTests.swift
//  flyfun-weatherTests
//
//  #605 — the cross-section layer model behind the layer bar: the seven
//  families, Emulate × Focus composition (port of web #591), pick-any clouds,
//  the compact family chip, and the graded methods read off the advisories
//  manifest. The web asserts the same contracts in layer-registry / advisory-
//  presets / advisory-highlights tests.
//
//  One serialized @MainActor suite: every test builds a CrossSectionViewModel,
//  which reads and writes shared UserDefaults keys. A class, so `deinit` can
//  clear them after each test as well as before — a lens left persisted leaked
//  into the UI journeys that run on the same simulator.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Builders

private func perModel(_ model: String, method: String?) -> ModelAdvisoryResult {
    ModelAdvisoryResult(
        model: model, status: "amber", detail: "d",
        affectedPoints: 1, totalPoints: 10, affectedPct: 10,
        affectedNm: 5, totalNm: 50, domainNm: nil,
        affectedDomain: nil, crossCheck: nil, mitigations: nil,
        highlights: nil, primaryMethodId: method)
}

private func advisory(_ id: String, rep: String? = "ecmwf", _ perModel: [ModelAdvisoryResult]) -> RouteAdvisoryResult {
    RouteAdvisoryResult(
        advisoryId: id, aggregateStatus: "amber", aggregateDetail: "d",
        perModel: perModel, parametersUsed: [:], aggregateMitigations: nil,
        representativeModel: rep)
}

private func manifest(_ advisories: [RouteAdvisoryResult]) -> AdvisoriesResponse {
    AdvisoriesResponse(
        advisories: advisories, catalog: [], routeName: "r",
        cruiseAltitudeFt: 6000, flightCeilingFt: 11000, totalDistanceNm: 50,
        models: ["ecmwf", "gfs"], aggregation: "worst", airportConditions: nil)
}

@MainActor
@Suite(.serialized) final class CrossSectionLensTests {

    init() { Self.clearDefaults() }
    deinit { Self.clearDefaults() }

    nonisolated private static func clearDefaults() {
        for key in CrossSectionViewModel.persistedDefaultsKeys {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }

    /// The layers of one method group currently on.
    private func on(_ vm: CrossSectionViewModel, in group: LayerGroup) -> [String] {
        CrossSectionLayer.layerIds(in: group).filter { vm.isLayerOn($0) }
    }

    // MARK: Families

    @Test func everyToggleableGroupBelongsToExactlyOneFamily() {
        // A group missing from a family would vanish from the bar with no error.
        for group in LayerGroup.allCases where !LayerFamily.familylessGroups.contains(group) {
            let owners = LayerFamily.allCases.filter { $0.groups.contains(group) }
            #expect(owners.count == 1, "\(group) owned by \(owners)")
        }
        for group in LayerFamily.familylessGroups {
            #expect(LayerFamily.family(for: group) == nil)
        }
    }

    @Test func bootDefaultsCoverEveryLayer() {
        for layer in CrossSectionLayer.allLayers {
            #expect(CrossSectionPresets.bootDefaults[layer.id] != nil, "\(layer.id) missing")
        }
    }

    @Test func familySummaryNamesAnswersThenCounts() {
        let vm = CrossSectionViewModel()  // GRAMET boot
        let icing = LayerFamily.icing.summary(enabledLayers: vm.enabledLayers, cloudStyle: vm.cloudStyle)
        #expect(icing.text == "Ogimet-NWP" && !icing.off)
        let clouds = LayerFamily.clouds.summary(enabledLayers: vm.enabledLayers, cloudStyle: vm.cloudStyle)
        #expect(clouds.text == "NWP · Natural")
        let levels = LayerFamily.levels.summary(enabledLayers: vm.enabledLayers, cloudStyle: vm.cloudStyle)
        #expect(levels.text == "0°C + cruise")
        let stability = LayerFamily.stability.summary(enabledLayers: vm.enabledLayers, cloudStyle: vm.cloudStyle)
        #expect(stability.text == "off" && stability.off)

        vm.applyAdvisoryPreset(CrossSectionPresets.advisory["convective"]!)
        let lines = LayerFamily.stability.summary(enabledLayers: vm.enabledLayers, cloudStyle: vm.cloudStyle)
        #expect(lines.text == "3 on")
    }

    // MARK: Emulate

    @Test func freshInstallBootsGramet() {
        let vm = CrossSectionViewModel()
        #expect(vm.activeEmulation == "gramet")
        #expect(vm.themeId == .gramet)
    }

    @Test func emulationReadsBackAsTheMethodsItChose() {
        let gramet = CrossSectionPresets.methods(from: CrossSectionPresets.emulation("gramet")!)
        #expect(gramet.methods == [.clouds: "nwp", .icing: "ogimet_nwp", .turbulence: "ri", .convection: "nwp"])
        #expect(gramet.cloudStyle == .natural)
        let windy = CrossSectionPresets.methods(from: CrossSectionPresets.emulation("windy")!)
        #expect(windy.methods[.icing] == "sfip_nwp")
        let foreflight = CrossSectionPresets.methods(from: CrossSectionPresets.emulation("foreflight")!)
        #expect(foreflight.methods[.icing] == "ogimet_dd")
        #expect(foreflight.methods[.clouds] == "dd")
        #expect(foreflight.cloudStyle == .square)
    }

    @Test func emulationNeverTouchesTheObservedLayers() {
        let vm = CrossSectionViewModel()
        vm.toggleLayer("observed-surface")  // on
        vm.applyEmulation("foreflight")
        #expect(vm.isLayerOn("observed-surface"))
        #expect(vm.isLayerOn("observed-tops"))
    }

    @Test func pickingAnEmulationSetsItsTheme() {
        let vm = CrossSectionViewModel()
        vm.applyEmulation("windy")
        #expect(vm.themeId == .light)
        vm.applyEmulation("foreflight")
        #expect(vm.themeId == .highContrast)
        vm.applyEmulation(nil)  // FlyFun leaves the theme alone
        #expect(vm.themeId == .highContrast)
        #expect(vm.activeEmulation == nil)
    }

    @Test func flyFunAppliesTheGradedMethodsOneOfEach() {
        let vm = CrossSectionViewModel()
        vm.setGradedMethods([.clouds: "dd", .icing: "sfip_nwp", .convection: "thermo"])
        vm.applyEmulation(nil)
        #expect(on(vm, in: .icing) == ["sfip-bands"])
        #expect(on(vm, in: .convection) == ["thermo-convective-bg"])
        #expect(on(vm, in: .clouds) == ["cloud-bands"])  // DD, in the natural style already drawn
        #expect(on(vm, in: .turbulence) == ["cat-bands"])
    }

    @Test func manualEditDropsTheLensButKeepsTheEmulation() {
        let vm = CrossSectionViewModel()
        vm.applyEmulation("windy")
        vm.applyAdvisoryPreset(CrossSectionPresets.advisory["icing"]!)
        vm.toggleLayer("cat-bands")
        #expect(vm.activeAdvisoryPreset == nil)
        #expect(vm.activeEmulation == "windy")
    }

    @Test func emulationIsRestoredAndMigrated() {
        let vm = CrossSectionViewModel()
        vm.applyEmulation("foreflight")
        #expect(CrossSectionViewModel().activeEmulation == "foreflight")

        vm.applyEmulation(nil)
        #expect(CrossSectionViewModel().activeEmulation == nil)

        // Pre-#605 install: a stored layer map matching Windy, no emulation key.
        UserDefaults.standard.removeObject(forKey: "crossSectionEmulation")
        let windy = CrossSectionPresets.bootDefaults.merging(CrossSectionPresets.windy) { $1 }
        UserDefaults.standard.set(try! JSONEncoder().encode(windy), forKey: "crossSectionEnabledLayers")
        #expect(CrossSectionViewModel().activeEmulation == "windy")
    }

    // MARK: Focus × Emulate

    @Test func focusResolvesThroughTheEmulationsMethods() {
        let vm = CrossSectionViewModel()
        vm.applyEmulation("windy")
        vm.applyAdvisoryPreset(CrossSectionPresets.advisory["icing"]!)
        #expect(on(vm, in: .icing) == ["sfip-bands"])
        #expect(on(vm, in: .clouds) == ["nwp-cloud-bands"])
        #expect(on(vm, in: .turbulence).isEmpty)  // not in the icing lens
        #expect(vm.activeEmulation == "windy")
        #expect(vm.activeAdvisoryPreset == "icing")
    }

    @Test func changingTheEmulationReappliesTheFocusLens() {
        let vm = CrossSectionViewModel()
        vm.applyAdvisoryPreset(CrossSectionPresets.advisory["icing"]!)
        vm.applyEmulation("foreflight")
        // ForeFlight's icing and cloud methods, still only the icing lens's groups.
        #expect(on(vm, in: .icing) == ["icing-bands"])
        #expect(on(vm, in: .clouds) == ["square-cloud-bands"])
        #expect(on(vm, in: .turbulence).isEmpty)
        #expect(vm.activeAdvisoryPreset == "icing")
    }

    @Test func focusCustomKeepsTheEmulationAndTheLayers() {
        let vm = CrossSectionViewModel()
        vm.applyEmulation("windy")
        vm.applyAdvisoryPreset(CrossSectionPresets.advisory["clouds"]!)
        let before = vm.enabledLayers
        vm.clearAdvisoryPreset()
        #expect(vm.activeEmulation == "windy")
        #expect(vm.enabledLayers == before)
    }

    @Test func basicShowsOneOfEachMethodGroup() {
        let vm = CrossSectionViewModel()
        vm.applyAdvisoryPreset(CrossSectionPresets.advisory["basic"]!)
        for group in CrossSectionPresets.methodGroups {
            #expect(on(vm, in: group).count == 1, "\(group)")
        }
        #expect(vm.isLayerOn("freezing-level"))
    }

    @Test func advisoryChipMethodsOverrideOnlyItsGroup() {
        let adv = advisory("icing_escape", [perModel("ecmwf", method: "ogimet_dd")])
        let methods = CrossSectionPresets.advisoryMethodOverrides(
            adv, model: "ecmwf", methods: [.icing: "sfip_nwp", .clouds: "nwp"])
        #expect(methods == [.icing: "ogimet_dd", .clouds: "nwp"])
        // No primary method on that model (e.g. GREEN) → the user's own methods.
        #expect(CrossSectionPresets.advisoryMethodOverrides(adv, model: "gfs", methods: [.icing: "sfip_nwp"])
                == [.icing: "sfip_nwp"])
    }

    // MARK: Compact family chip

    @Test func familyChipResolvesThePreferredMethod() {
        let vm = CrossSectionViewModel()
        vm.setFamily(.icing, on: false)
        #expect(on(vm, in: .icing).isEmpty)
        vm.setGradedMethods([.icing: "sfip_nwp"])
        vm.applyEmulation(nil)
        vm.setFamily(.icing, on: false)
        vm.setFamily(.icing, on: true)
        #expect(on(vm, in: .icing) == ["sfip-bands"])
    }

    @Test func familyChipRestoresEveryDefaultLine() {
        let vm = CrossSectionViewModel()
        vm.setFamily(.levels, on: false)
        vm.setFamily(.levels, on: true)
        for id in ["freezing-level", "minus-10c", "minus-20c", "reference-lines"] {
            #expect(vm.isLayerOn(id), "\(id)")
        }
        vm.setFamily(.stability, on: true)
        for id in ["lcl", "lfc", "el"] { #expect(vm.isLayerOn(id), "\(id)") }
        #expect(!vm.isLayerOn("inversion-bands"))
    }

    @Test func noneClearsTheWholeGroup() {
        let vm = CrossSectionViewModel()
        vm.toggleLayer("sfip-bands")
        vm.clearGroup(.icing)
        #expect(on(vm, in: .icing).isEmpty)
    }

    // MARK: Clouds

    @Test func cloudSourcesArePickAnyInOneStyle() {
        let vm = CrossSectionViewModel()  // NWP natural
        vm.toggleCloudSource(.dd)
        #expect(Set(on(vm, in: .clouds)) == ["nwp-cloud-bands", "cloud-bands"])
        vm.setCloudStyle(.square)
        #expect(Set(on(vm, in: .clouds)) == ["square-nwp-cloud-bands", "square-cloud-bands"])
        vm.toggleCloudSource(.nwp)
        #expect(on(vm, in: .clouds) == ["square-cloud-bands"])
        #expect(vm.cloudStyle == .square)
    }

    // MARK: Graded methods

    @Test func gradedMethodsComeFromTheRepresentativeModel() {
        let m = manifest([
            advisory("icing_escape", [perModel("gfs", method: "ogimet_dd"), perModel("ecmwf", method: "sfip_nwp")]),
            advisory("vfr_feasibility", [perModel("ecmwf", method: "nwp_synthesized")]),
            advisory("convective", [perModel("ecmwf", method: nil)]),
        ])
        let methods = CrossSectionPresets.gradedMethods(from: m)
        #expect(methods[.icing] == "sfip_nwp")
        #expect(methods[.clouds] == "nwp")        // synthesized draws on the NWP band
        #expect(methods[.convection] == "nwp")    // silent → engine default
        #expect(CrossSectionPresets.gradedMethods(from: nil) == CrossSectionPresets.engineMethodDefaults)
    }

    @Test func unknownGradedMethodFallsBackToTheGroupDefault() {
        // IENG has no iOS layer.
        #expect(CrossSectionPresets.preferredLayer(for: .icing, method: "ieng", cloudStyle: .square)
                == "icing-ogimet-nwp-bands")
    }
}
