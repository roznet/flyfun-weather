import SwiftUI

/// Protocol for cross-section rendering layers.
protocol CrossSectionLayerProtocol {
    var id: String { get }
    var name: String { get }
    var group: LayerGroup { get }
    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData)
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

/// Registry of all cross-section layers with default enabled states.
enum CrossSectionLayer {
    static let allLayers: [any CrossSectionLayerProtocol] = [
        CloudBandsLayer(),
        IcingBandsLayer(),
        CATBandsLayer(),
        InversionBandsLayer(),
        TerrainLayer(),
        TemperatureLinesLayer(metric: .freezingLevel),
        TemperatureLinesLayer(metric: .minus10c),
        TemperatureLinesLayer(metric: .minus20c),
        ReferenceLinesLayer(),
    ]

    static let defaultEnabled: [String: Bool] = [
        "cloud-bands": true,
        "icing-bands": true,
        "cat-bands": false,
        "inversion-bands": true,
        "terrain": true,
        "freezing-level": true,
        "minus-10c": true,
        "minus-20c": false,
        "reference-lines": true,
    ]
}
