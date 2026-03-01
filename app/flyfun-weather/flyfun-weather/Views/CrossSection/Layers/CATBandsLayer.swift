import SwiftUI

/// Amber/red bands for clear-air turbulence.
struct CATBandsLayer: CrossSectionLayerProtocol {
    let id = "cat-bands"
    let name = "CAT Turbulence"
    let group: LayerGroup = .turbulence

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        drawRiskBands(
            context: &context,
            transform: transform,
            points: data.points,
            zonesForPoint: { $0.catLayers.map { ($0.baseFt, $0.topFt, $0.risk) } },
            colorForRisk: ColorScales.catRiskColor
        )
    }
}
