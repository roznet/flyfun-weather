//
//  CrossSectionThemeTests.swift
//  flyfun-weatherTests
//
//  #320 — cross-section colour-theme system: registry completeness, the
//  module-level active-theme indirection, and the preset→theme wiring on
//  CrossSectionViewModel. Asserts on theme IDs and the raw RGB/Double fields
//  (SwiftUI `Color` equality across independently-built instances is unreliable,
//  so the ported values are checked via the 0–255 `RGB` channels).
//
//  All tests live in ONE `@MainActor @Suite(.serialized)`: they read and write
//  the shared `CrossSectionTheme._active` global, and Swift Testing parallelises
//  suites by default. Serializing (and pinning to the main actor, which is where
//  the renderer/view model mutate the global in production) removes the cross-
//  test race the active-theme assertions would otherwise hit. Other suites that
//  build a view model race on the same state, so this one is nested under
//  `CrossSectionSharedStateTests` to serialize against them too.
//

import Testing
import Foundation
@testable import flyfun_weather

extension CrossSectionSharedStateTests {
    @MainActor
    @Suite(.serialized) struct CrossSectionThemeTests {

        /// Runs before each test (Swift Testing builds a fresh suite instance per
        /// test). Clears the persisted theme so a test that switches+persists a theme
        /// (#320) doesn't leak into the next test's boot-default expectation.
        init() {
            for key in CrossSectionViewModel.persistedDefaultsKeys {
                UserDefaults.standard.removeObject(forKey: key)
            }
        }

        // MARK: Registry

        @Test func registryHasAllFourThemesMatchingWeb() {
            // IDs (and their raw values) mirror the web `ThemeId` union so a route
            // renders identically and the two files diff cleanly.
            #expect(Set(CrossSectionTheme.all.keys) == Set(CrossSectionThemeID.allCases))
            #expect(CrossSectionThemeID.standard.rawValue == "standard")
            #expect(CrossSectionThemeID.highContrast.rawValue == "high-contrast")
            #expect(CrossSectionThemeID.gramet.rawValue == "gramet")
            #expect(CrossSectionThemeID.light.rawValue == "light")
        }

        @Test func eachThemeHasItsOwnIdAndLabel() {
            for id in CrossSectionThemeID.allCases {
                #expect(id.theme.id == id)
                #expect(id.theme.label.isEmpty == false)
            }
        }

        @Test func standardThemePortsWebRgbValuesVerbatim() {
            let std = CrossSectionTheme.standard
            // Cloud ramp: dense [140,140,150] → thin [250,250,255] (web Standard).
            #expect((std.cloudDense.r, std.cloudDense.g, std.cloudDense.b) == (140, 140, 150))
            #expect((std.cloudThin.r, std.cloudThin.g, std.cloudThin.b) == (250, 250, 255))
            // Inversion base #e91e63 with floor/cap.
            #expect((std.inversionBase.r, std.inversionBase.g, std.inversionBase.b) == (233, 30, 99))
            #expect(std.inversionFloor == 0.15)
            #expect(std.inversionCap == 0.65)
        }

        @Test func grametInheritsStandardCloudRampLightOverridesNwpOpacity() {
            // Derived themes are built by copy-and-override, so GRAMET keeps the
            // Standard cloud ramp (only sky/terrain/icing/etc. change)…
            #expect(CrossSectionTheme.gramet.cloudDense.r == CrossSectionTheme.standard.cloudDense.r)
            // …while Light bumps the NWP cloud opacity scale (0.55 → 0.70).
            #expect(CrossSectionTheme.light.nwpOpacityScale == 0.70)
            #expect(CrossSectionTheme.standard.nwpOpacityScale == 0.55)
        }

        // MARK: Active-theme indirection

        @Test func setActiveSwitchesTheThemeColorScalesResolvesAgainst() {
            let original = CrossSectionTheme.active.id
            defer { CrossSectionTheme.setActive(original) }

            CrossSectionTheme.setActive(.light)
            #expect(CrossSectionTheme.active.id == .light)

            CrossSectionTheme.setActive(.gramet)
            #expect(CrossSectionTheme.active.id == .gramet)
        }

        // MARK: Preset → theme wiring (CrossSectionViewModel)

        @Test func bootDefaultsToGrametThemeMatchingTheGrametEmulation() {
            let vm = CrossSectionViewModel()
            // The booted emulation is GRAMET, so the theme agrees on boot.
            #expect(vm.themeId == .gramet)
            #expect(vm.activeEmulation == "gramet")
            #expect(CrossSectionTheme.active.id == .gramet)
        }

        @Test func selectingAnEmulationAlsoAppliesItsTheme() {
            let vm = CrossSectionViewModel()

            vm.applyEmulation("windy")
            #expect(vm.themeId == .light)          // web mapping: windy → light
            #expect(CrossSectionTheme.active.id == .light)

            vm.applyEmulation("foreflight")
            #expect(vm.themeId == .highContrast)   // web mapping: foreflight → high-contrast
            #expect(CrossSectionTheme.active.id == .highContrast)

            vm.applyEmulation("gramet")
            #expect(vm.themeId == .gramet)
        }

        // MARK: Theme ownership (#647, web #597)

        @Test func anEmulationNeverWritesTheUsersTheme() {
            let vm = CrossSectionViewModel()
            vm.applyEmulation("windy")
            #expect(vm.userThemeId == nil)
            // Nothing persisted either: a relaunch on FlyFun would draw standard.
            #expect(CrossSectionViewModel().userThemeId == nil)
        }

        @Test func flyFunOnAFreshInstallDrawsStandard() {
            let vm = CrossSectionViewModel()
            #expect(vm.themeId == .gramet)        // boot emulation's look
            vm.applyEmulation(nil)
            #expect(vm.themeId == .standard)
            #expect(CrossSectionTheme.active.id == .standard)
        }

        @Test func flyFunRestoresTheUsersOwnTheme() {
            let vm = CrossSectionViewModel()
            vm.applyEmulation(nil)
            vm.setTheme(.light)
            vm.applyEmulation("gramet")
            #expect(vm.themeId == .gramet)
            vm.applyEmulation(nil)
            #expect(vm.themeId == .light)
            #expect(CrossSectionTheme.active.id == .light)
        }

        @Test func reopeningMidEmulationStillDrawsItsTheme() {
            let vm1 = CrossSectionViewModel()
            vm1.applyEmulation(nil)
            vm1.setTheme(.light)
            vm1.applyEmulation("foreflight")
            let vm2 = CrossSectionViewModel()
            #expect(vm2.activeEmulation == "foreflight")
            #expect(vm2.themeId == .highContrast)
            #expect(vm2.userThemeId == .light)
        }

        @Test func handPickedThemeWhileEmulatingDropsTheEmulationInFull() {
            let vm = CrossSectionViewModel()
            vm.setGradedMethods([.clouds: "dd", .icing: "sfip_nwp", .convection: "thermo"])
            vm.setHighlightAdvisory("icing")
            #expect(vm.activeEmulation == "gramet")
            vm.setTheme(.standard)
            #expect(vm.activeEmulation == nil)
            #expect(vm.themeId == .standard)
            #expect(CrossSectionTheme.active.id == .standard)
            #expect(vm.activeHighlightAdvisoryId == nil)
            // FlyFun's graded methods, not GRAMET's Ogimet-NWP icing.
            #expect(vm.effectiveMethods[.icing] == "sfip_nwp")
            let icingOn = CrossSectionLayer.allLayers
                .filter { $0.group == .icing && vm.enabledLayers[$0.id] == true }.map(\.id)
            #expect(icingOn == ["sfip-bands"])
        }

        @Test func setThemeOutsideAnEmulationTouchesColoursOnly() {
            let vm = CrossSectionViewModel()
            vm.applyEmulation(nil)
            let layers = vm.enabledLayers
            vm.setTheme(.highContrast)
            #expect(vm.themeId == .highContrast)
            #expect(vm.activeEmulation == nil)
            #expect(vm.enabledLayers == layers)
        }

        @Test func themeChoiceIsPersistedAcrossViewModelInstances() {
            let vm1 = CrossSectionViewModel()
            vm1.setTheme(.highContrast)
            // A fresh instance (next launch / a re-created CrossSectionView) restores it.
            let vm2 = CrossSectionViewModel()
            #expect(vm2.themeId == .highContrast)
            #expect(vm2.userThemeId == .highContrast)
        }

        // MARK: One-time migration of the pre-#647 theme write

        @Test func migrationClearsAStoredThemeEqualToTheActiveEmulations() {
            // Pre-#647 state: GRAMET emulation wrote "gramet" as the user's theme.
            UserDefaults.standard.set("gramet", forKey: "crossSectionEmulation")
            UserDefaults.standard.set("gramet", forKey: "crossSectionThemeId")
            let vm = CrossSectionViewModel()
            #expect(vm.userThemeId == nil)
            #expect(vm.themeId == .gramet)        // still emulating
            vm.applyEmulation(nil)
            #expect(vm.themeId == .standard)
        }

        @Test func migrationKeepsAThemeThatDiffersFromTheEmulations() {
            UserDefaults.standard.set("gramet", forKey: "crossSectionEmulation")
            UserDefaults.standard.set("light", forKey: "crossSectionThemeId")
            let vm = CrossSectionViewModel()
            #expect(vm.userThemeId == .light)
        }

        @Test func migrationRunsOnlyOnce() {
            // Post-#647 a matching pair is a real choice: Light, then Windy.
            let vm1 = CrossSectionViewModel()   // stamps the settings version
            vm1.applyEmulation(nil)
            vm1.setTheme(.light)
            vm1.applyEmulation("windy")         // windy draws light too
            let vm2 = CrossSectionViewModel()
            #expect(vm2.userThemeId == .light)
        }
    }
}
