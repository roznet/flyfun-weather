import SwiftUI

/// Protocol for cross-section rendering layers.
///
/// `render` is `@MainActor` (it reads the main-actor `ColorScales`/active theme);
/// conforming layers inherit that isolation, and they all already render from the
/// main-actor `Canvas` closures, so no call site changes.
protocol CrossSectionLayerProtocol {
    var id: String { get }
    var name: String { get }
    var group: LayerGroup { get }
    @MainActor func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData)
}

enum LayerGroup: String, CaseIterable {
    case terrain
    case temperature
    case clouds
    case icing
    case stability
    case turbulence
    case convection
    case reference
    /// Observed (remotely-sensed) conditions — radar, lightning, satellite cloud
    /// tops (#574). A toggle group, not a method group: the two layers show
    /// different things (a vertical histogram vs a surface strip) and are meant
    /// to be on together, not chosen between. Deliberately absent from
    /// `CrossSectionPresets.resetGroups`, mirroring the web's `RESET_GROUPS` —
    /// applying an advisory lens must not silently drop the measured picture.
    case conditions
    /// Advisory highlight (scrim + verdict ribbon, #374). Not a toggleable data
    /// layer: its visibility is driven by the active advisory highlight, so it
    /// never appears in the method/reference sections of the config sheet.
    case highlight
}

extension LayerGroup {
    /// True for groups where multiple methods are mutually exclusive — the UI
    /// surfaces these as a single dropdown ("None / method A / method B / ...")
    /// rather than a row of independent toggles. Toggle groups (terrain,
    /// reference, temperature, stability) keep individual on/off chips.
    var isMethodGroup: Bool {
        switch self {
        case .clouds, .icing, .turbulence, .convection: return true
        default: return false
        }
    }

    var label: String {
        switch self {
        case .terrain: "Terrain"
        case .temperature: "Temperature"
        case .clouds: "Clouds"
        case .icing: "Icing"
        case .stability: "Stability"
        case .turbulence: "Turbulence"
        case .convection: "Convection"
        case .reference: "Reference"
        case .conditions: "Observed conditions"
        case .highlight: "Highlight"
        }
    }
}

/// Registry of all cross-section layers with default enabled states.
enum CrossSectionLayer {
    /// Rendering order: clouds → convection → icing → other bands → terrain → lines → reference.
    /// Matches the web layer-registry.ts ordering so identical data renders the same on both platforms.
    static let allLayers: [any CrossSectionLayerProtocol] = [
        SoftCloudBandsLayer(source: .nwp),
        SoftCloudBandsLayer(source: .dd),
        NaturalCloudBandsLayer(source: .nwp),
        NaturalCloudBandsLayer(source: .dd),
        SquareCloudBandsLayer(source: .nwp),
        SquareCloudBandsLayer(source: .dd),
        // Observed cloud tops draw over the NWP cloud bands on purpose: that
        // overlap IS the cross-check (#574). Nothing computes the comparison in
        // phase 1 — it is read off the picture.
        ObservedTopsLayer(),
        ThermoConvectiveBgLayer(),
        NwpConvectiveBgLayer(),
        IcingBandsLayer(),
        IcingOgimetNwpBandsLayer(),
        SfipBandsLayer(),
        CATBandsLayer(),
        InversionBandsLayer(),
        TerrainLayer(),
        // Observed radar/lightning hugs the terrain, so it sits with the other
        // surface-referenced overlays rather than in the cloud stack — and above
        // the terrain fill, which would otherwise mask the strip.
        ObservedSurfaceLayer(),
        TemperatureLinesLayer(metric: .freezingLevel),
        TemperatureLinesLayer(metric: .minus10c),
        TemperatureLinesLayer(metric: .minus20c),
        StabilityLinesLayer(metric: .lcl),
        StabilityLinesLayer(metric: .lfc),
        StabilityLinesLayer(metric: .el),
        ReferenceLinesLayer(),
    ]

    // The default enabled-layer set now lives in `CrossSectionPresets.gramet`
    // (the boot state and the GRAMET preset are the same map). The old
    // `defaultEnabled` static was removed once both its uses moved there.

    /// For each method group, ordered list of layer IDs (from preferred to alternates).
    /// Used by the dropdown picker UI: "None" + each method, in this display order.
    /// Mirrors web's PREFERRED_METHOD_LAYER mapping in layer-registry.ts.
    static let methodGroupOrder: [LayerGroup: [String]] = [
        .clouds: ["soft-nwp-cloud-bands", "soft-cloud-bands", "nwp-cloud-bands", "cloud-bands", "square-nwp-cloud-bands", "square-cloud-bands"],
        .icing: ["icing-ogimet-nwp-bands", "icing-bands", "sfip-bands"],
        .turbulence: ["cat-bands"],
        .convection: ["nwp-convective-bg", "thermo-convective-bg"],
    ]

    /// Friendlier short labels for the dropdown menu items.
    static let methodLabels: [String: String] = [
        "soft-nwp-cloud-bands": "Soft NWP",
        "soft-cloud-bands": "Soft DD",
        "nwp-cloud-bands": "Natural NWP",
        "cloud-bands": "Natural DD",
        "square-nwp-cloud-bands": "Square NWP",
        "square-cloud-bands": "Square DD",
        "icing-ogimet-nwp-bands": "Ogimet-NWP",
        "icing-bands": "Ogimet-DD",
        "sfip-bands": "SFIP",
        "cat-bands": "CAT (Ri)",
        "nwp-convective-bg": "NWP",
        "thermo-convective-bg": "Thermo",
    ]
}
