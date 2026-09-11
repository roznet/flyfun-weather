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
    /// True for groups that hold several ways of computing the same thing, so
    /// one of them is "the preferred one" — what a compact family chip and a
    /// Focus lens resolve to. Not a UI constraint any more: every group is
    /// pick-any on the layer bar, since two icing methods overlaid is a
    /// legitimate comparison (#605). Toggle groups (terrain, reference,
    /// temperature, stability) are sets of independent lines.
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

    // The boot layer set lives in `CrossSectionPresets.bootDefaults` (GRAMET plus
    // the observed layers at their defaults).

    /// Layers the web registry marks `defaultEnabled`, restricted to ids iOS has.
    /// A family chip switching on a group that is a set of independent lines —
    /// levels, stability, observed — brings back these, the same lines the web's
    /// compact chip does. Method groups resolve through the graded methods instead.
    static let defaultEnabled: Set<String> = [
        "terrain", "freezing-level", "minus-10c", "minus-20c", "reference-lines",
        "lcl", "lfc", "el", "observed-tops",
        "icing-ogimet-nwp-bands", "nwp-convective-bg", "square-nwp-cloud-bands",
    ]

    /// The layers of one group in pill order: render order, except the observed
    /// layers read surface → tops as on the web (`PANEL_ORDER`).
    static func layers(in group: LayerGroup) -> [any CrossSectionLayerProtocol] {
        let inGroup = allLayers.filter { $0.group == group }
        guard group == .conditions else { return inGroup }
        let order = ["observed-surface", "observed-tops"]
        let rank = { (id: String) in order.firstIndex(of: id) ?? order.count }
        return inGroup.sorted { rank($0.id) < rank($1.id) }
    }

    static func layerIds(in group: LayerGroup) -> [String] { layers(in: group).map(\.id) }

    /// The name a layer pill shows — the web's `viz.layer.*`, so both clients
    /// call a layer the same thing. Falls back to the layer's own name.
    static func label(_ id: String) -> String {
        labels[id] ?? allLayers.first { $0.id == id }?.name ?? id
    }

    /// Chip-sized label for the bar summary (web `viz.layerShort.*`), falling
    /// back to the full label where that already fits in a chip.
    static func shortLabel(_ id: String) -> String { shortLabels[id] ?? label(id) }

    /// Metrics-catalog entry each computed layer is explained by — the cards in a
    /// family's About panel. Lines (isotherms, parcel levels, cruise) carry none.
    /// Mirrors the web layers' `metricId`.
    static let metricIds: [String: String] = [
        "cloud-bands": "cloud_coverage",
        "nwp-cloud-bands": "nwp_cloud_cover",
        "soft-cloud-bands": "soft_cloud_dd",
        "soft-nwp-cloud-bands": "soft_cloud_nwp",
        "square-cloud-bands": "square_cloud_dd",
        "square-nwp-cloud-bands": "square_cloud_nwp",
        "icing-bands": "icing_risk",
        "icing-ogimet-nwp-bands": "icing_ogimet_nwp_risk",
        "sfip-bands": "sfip_risk",
        "cat-bands": "cat_risk",
        "inversion-bands": "inversion_layer",
        "nwp-convective-bg": "nwp_convective_risk",
        "thermo-convective-bg": "convective_risk",
        "observed-tops": "observed_tops",
        "observed-surface": "observed_surface",
    ]

    private static let labels: [String: String] = [
        "soft-nwp-cloud-bands": "Soft NWP",
        "soft-cloud-bands": "Soft DD",
        "nwp-cloud-bands": "NWP Natural",
        "cloud-bands": "DD Natural",
        "square-nwp-cloud-bands": "Square NWP",
        "square-cloud-bands": "Square DD",
        "icing-ogimet-nwp-bands": "Ogimet-NWP",
        "icing-bands": "Ogimet-DD",
        "sfip-bands": "SFIP-NWP",
        "cat-bands": "CAT (Ri)",
        "inversion-bands": "Inversions",
        "nwp-convective-bg": "NWP Convective",
        "thermo-convective-bg": "Thermo Convective",
        "freezing-level": "Freezing Level (0°C)",
        "minus-10c": "−10°C Level",
        "minus-20c": "−20°C Level",
        "lcl": "LCL",
        "lfc": "LFC",
        "el": "EL",
        "reference-lines": "Cruise / Flight ceiling",
        "observed-tops": "Cloud tops",
        "observed-surface": "Rain & lightning",
        "terrain": "Terrain",
    ]

    private static let shortLabels: [String: String] = [
        "freezing-level": "0°C",
        "minus-10c": "−10°C",
        "minus-20c": "−20°C",
        "reference-lines": "cruise",
        "thermo-convective-bg": "thermo",
        "nwp-convective-bg": "NWP",
        "inversion-bands": "inversions",
        "cat-bands": "Ri",
        "observed-surface": "radar",
        "observed-tops": "tops",
    ]
}
