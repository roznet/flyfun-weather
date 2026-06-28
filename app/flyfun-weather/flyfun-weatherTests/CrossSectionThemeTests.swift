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
//  test race the active-theme assertions would otherwise hit.
//

import Testing
import Foundation
@testable import flyfun_weather

@MainActor
@Suite(.serialized) struct CrossSectionThemeTests {

    /// Runs before each test (Swift Testing builds a fresh suite instance per
    /// test). Clears the persisted theme so a test that switches+persists a theme
    /// (#320) doesn't leak into the next test's boot-default expectation.
    init() {
        UserDefaults.standard.removeObject(forKey: "crossSectionThemeId")
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

    @Test func bootDefaultsToGrametThemeMatchingTheGrametLayerPreset() {
        let vm = CrossSectionViewModel()
        // The booted layer preset is GRAMET, so the theme agrees on boot.
        #expect(vm.themeId == .gramet)
        #expect(vm.currentPreset == .gramet)
        #expect(CrossSectionTheme.active.id == .gramet)
    }

    @Test func selectingALayerPresetAlsoAppliesItsTheme() {
        let vm = CrossSectionViewModel()

        vm.applyPreset(.windy)
        #expect(vm.themeId == .light)          // web mapping: windy → light
        #expect(CrossSectionTheme.active.id == .light)

        vm.applyPreset(.foreFlight)
        #expect(vm.themeId == .highContrast)   // web mapping: foreflight → high-contrast
        #expect(CrossSectionTheme.active.id == .highContrast)

        vm.applyPreset(.gramet)
        #expect(vm.themeId == .gramet)
    }

    @Test func setThemeIsOrthogonalToTheLayerPreset() {
        let vm = CrossSectionViewModel()
        // Changing the theme alone must NOT disturb the layer set / preset label
        // (mirrors web `setVizTheme`, which leaves the preset alone).
        vm.setTheme(.standard)
        #expect(vm.themeId == .standard)
        #expect(CrossSectionTheme.active.id == .standard)
        #expect(vm.currentPreset == .gramet)   // layers untouched → still GRAMET
    }

    @Test func themeChoiceIsPersistedAcrossViewModelInstances() {
        // The suite's init() cleared the persisted key, so this starts from the
        // GRAMET boot default.
        let vm1 = CrossSectionViewModel()
        vm1.setTheme(.highContrast)
        // A fresh instance (next launch / a re-created CrossSectionView) restores it.
        let vm2 = CrossSectionViewModel()
        #expect(vm2.themeId == .highContrast)
    }
}
