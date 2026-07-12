import Foundation

// =============================================================================
// SYNC — keep this file in lockstep with the web preset definitions:
//   • Layer presets (GRAMET / Windy / ForeFlight):
//       web/ts/visualization/cross-section/layer-registry.ts  (PRESETS, *_ENABLED)
//   • Advisory lenses (Basic / Icing / Clouds / Convective / Turbulence / VFR / IFR):
//       web/ts/visualization/cross-section/advisory-presets.ts (ADVISORY_PRESETS,
//       ADVISORY_TO_PRESET, ADVISORY_OVERRIDES, getPresetForAdvisory)
//   • Cloud source×style axes:
//       web/ts/visualization/cross-section/cloud-bands-factory.ts (CLOUD_LAYER_BY_AXES,
//       parseCloudLayerId)
//
// iOS uses its own layer IDs and lacks a few web layers (ieng-icing-bands,
// e-shear-bands, sld-bands, surface-obscuration-bands). The maps below translate
// the web intent to iOS IDs; line IDs that don't exist on iOS are simply dropped
// when applied. When the web presets change, update this file — and add a note on
// the web side (both files carry the reciprocal SYNC comment).
// =============================================================================

/// One-click layer configuration. Port of web `LayerPreset`. `themeId` mirrors
/// the web preset's colour theme; the live wiring (selecting a preset also sets
/// its theme, #320) lives on `CrossSectionViewModel.Preset.themeId`. This struct
/// stays as a parity mirror of the web `PRESETS` table.
struct LayerPreset: Identifiable, Equatable {
    let id: String
    let label: String
    let themeId: String
    let enabledLayers: [String: Bool]
}

/// Hazard-oriented lens. Port of web `AdvisoryPreset` (cross-section directives
/// only — the web's route-graph / map / Skew-T directives are not yet wired on
/// iOS). `groups` enables the preferred layer of each method group on a clean
/// slate; `lines` force-enables explicit layer IDs.
struct AdvisoryPreset: Identifiable, Equatable {
    let id: String
    let label: String
    let caption: String
    var groups: [LayerGroup] = []
    var lines: [String] = []
}

/// Cloud rendering axes (orthogonal): which data feed, and how it's drawn.
/// Mirrors web's `cloud-bands-factory.ts`.
enum CloudSource: String, CaseIterable, Identifiable {
    case nwp, dd
    var id: String { rawValue }
    var label: String { self == .nwp ? "NWP" : "DD" }
}

enum CloudStyle: String, CaseIterable, Identifiable {
    case soft, natural, square
    var id: String { rawValue }
    var label: String {
        switch self {
        case .soft: "Soft"
        case .natural: "Natural"
        case .square: "Square"
        }
    }
}

enum CrossSectionPresets {
    // MARK: - Layer presets (ported from web *_ENABLED, translated to iOS IDs)

    /// Builds a complete enabled-map (every layer present) with the given IDs on.
    private static func layers(on onIds: Set<String>) -> [String: Bool] {
        var m: [String: Bool] = [:]
        for layer in CrossSectionLayer.allLayers { m[layer.id] = onIds.contains(layer.id) }
        return m
    }

    /// GRAMET — Natural NWP clouds + Ogimet-NWP icing + CAT (Ri) + NWP convective.
    static let gramet = layers(on: [
        "nwp-cloud-bands", "nwp-convective-bg", "icing-ogimet-nwp-bands",
        "cat-bands", "terrain", "freezing-level", "reference-lines",
    ])

    /// Windy — Natural NWP clouds + SFIP-NWP icing + CAT (Ri) + NWP convective.
    static let windy = layers(on: [
        "nwp-cloud-bands", "nwp-convective-bg", "sfip-bands",
        "cat-bands", "terrain", "freezing-level", "reference-lines",
    ])

    /// ForeFlight — Square DD clouds + Ogimet-DD icing + CAT (Ri) + NWP convective.
    static let foreflight = layers(on: [
        "square-cloud-bands", "nwp-convective-bg", "icing-bands",
        "cat-bands", "terrain", "freezing-level", "reference-lines",
    ])

    static let all: [LayerPreset] = [
        LayerPreset(id: "gramet", label: "GRAMET", themeId: "gramet", enabledLayers: gramet),
        LayerPreset(id: "windy", label: "Windy", themeId: "light", enabledLayers: windy),
        LayerPreset(id: "foreflight", label: "ForeFlight", themeId: "high-contrast", enabledLayers: foreflight),
    ]

    // MARK: - Advisory lenses (ported from web ADVISORY_PRESETS)

    /// Groups reset to OFF before applying a lens, so the view shows only what the
    /// lens specifies (plus always-on terrain + cruise reference).
    static let resetGroups: Set<LayerGroup> = [
        .clouds, .icing, .convection, .turbulence, .stability, .temperature,
    ]

    /// Display order for the lens picker.
    static let advisoryOrder = ["basic", "icing", "clouds", "convective", "turbulence", "vfr", "ifr"]

    static let advisory: [String: AdvisoryPreset] = [
        "basic": AdvisoryPreset(
            id: "basic", label: "Basic / Learn",
            caption: "Temperature, dewpoint, and the parcel path with LCL/LFC/EL — no hazard bands.",
            groups: [], lines: ["freezing-level"]),
        "icing": AdvisoryPreset(
            id: "icing", label: "Icing",
            caption: "Icing bands vs the 0 °C line and terrain — is there an ice-free descent?",
            groups: [.icing, .clouds], lines: ["freezing-level"]),
        "clouds": AdvisoryPreset(
            id: "clouds", label: "Clouds",
            caption: "Cloud tops & coverage vs your cruise level.",
            groups: [.clouds], lines: ["freezing-level"]),
        "convective": AdvisoryPreset(
            id: "convective", label: "Convective",
            caption: "Towers framed by LCL→LFC→EL and instability along route.",
            groups: [.convection, .clouds],
            lines: ["lcl", "lfc", "el", "freezing-level", "minus-10c", "minus-20c"]),
        "turbulence": AdvisoryPreset(
            id: "turbulence", label: "Turbulence",
            caption: "CAT/shear layers near cruise; terrain + wind for orographic risk.",
            groups: [.turbulence], lines: ["inversion-bands"]),
        "vfr": AdvisoryPreset(
            id: "vfr", label: "VFR feasibility",
            caption: "VMC picture: clouds & obscuration vs cruise and airports.",
            groups: [.clouds], lines: ["freezing-level"]),
        "ifr": AdvisoryPreset(
            id: "ifr", label: "IFR feasibility",
            caption: "IFR hazards: icing + convection + cloud along route.",
            groups: [.icing, .convection, .clouds], lines: ["freezing-level", "minus-10c"]),
    ]

    static var advisoryList: [AdvisoryPreset] { advisoryOrder.compactMap { advisory[$0] } }

    /// advisory_id → lens id (card chips). Mirrors web ADVISORY_TO_PRESET.
    static let advisoryToPreset: [String: String] = [
        "icing_escape": "icing", "fiki_icing": "icing", "freezing_precip": "icing",
        "cloud_top": "clouds", "vmc_cruise": "clouds",
        "convective": "convective",
        "turbulence": "turbulence", "mountain_wind": "turbulence",
        "vfr_feasibility": "vfr", "ifr_feasibility": "ifr",
        // enroute_precip is a visibility proxy → the VFR lens; the web
        // override's routeGraph swap has no iOS equivalent (#375).
        "enroute_precip": "vfr",
    ]

    /// Per-advisory extras unioned onto the base lens. Mirrors web
    /// `ADVISORY_OVERRIDES`: e.g. FIKI icing adds warm-nose isotherms. iOS-missing
    /// line ids (e.g. `sld-bands`) are kept here for parity and dropped at apply
    /// time by `applyAdvisoryPreset`'s `m[id] != nil` guard.
    static let advisoryOverrides: [String: (groups: [LayerGroup], lines: [String])] = [
        "fiki_icing": (groups: [], lines: ["minus-10c", "minus-20c", "sld-bands"]),
        "freezing_precip": (groups: [], lines: ["sld-bands"]),
    ]

    /// The lens a given advisory's chip should apply, or nil if it has no chip.
    /// Unions `advisoryOverrides` onto the base lens (mirrors web
    /// `getPresetForAdvisory`).
    static func preset(forAdvisory advisoryId: String) -> AdvisoryPreset? {
        guard let presetId = advisoryToPreset[advisoryId], var p = advisory[presetId] else { return nil }
        if let o = advisoryOverrides[advisoryId] {
            p.groups += o.groups
            p.lines += o.lines
        }
        return p
    }

    // MARK: - Cloud axes (source × style ⇄ layer id)

    /// (source, style) → cloud layer id.
    static func cloudLayerId(source: CloudSource, style: CloudStyle) -> String {
        switch (style, source) {
        case (.soft, .nwp): "soft-nwp-cloud-bands"
        case (.soft, .dd): "soft-cloud-bands"
        case (.natural, .nwp): "nwp-cloud-bands"
        case (.natural, .dd): "cloud-bands"
        case (.square, .nwp): "square-nwp-cloud-bands"
        case (.square, .dd): "square-cloud-bands"
        }
    }

    /// cloud layer id → (source, style), or nil if not a cloud layer.
    static func parseCloudLayerId(_ id: String) -> (source: CloudSource, style: CloudStyle)? {
        for source in CloudSource.allCases {
            for style in CloudStyle.allCases where cloudLayerId(source: source, style: style) == id {
                return (source, style)
            }
        }
        return nil
    }
}
