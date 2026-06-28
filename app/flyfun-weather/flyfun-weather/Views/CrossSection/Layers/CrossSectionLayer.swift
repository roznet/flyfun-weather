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
        ThermoConvectiveBgLayer(),
        NwpConvectiveBgLayer(),
        IcingBandsLayer(),
        IcingOgimetNwpBandsLayer(),
        SfipBandsLayer(),
        CATBandsLayer(),
        InversionBandsLayer(),
        TerrainLayer(),
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
