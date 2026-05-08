import SwiftUI

/// Amber/red bands for clear-air turbulence (Richardson-number based).
struct CATBandsLayer: CrossSectionLayerProtocol {
    let id = "cat-bands"
    let name = "CAT Turbulence"
    let group: LayerGroup = .turbulence

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        renderMatchedZones(
            &context, transform: transform, data: data,
            getZones: { $0.catLayers.filter { $0.risk != "none" } },
            getColor: { z, matched in
                let risk = matched.map { maxRisk(z.risk, $0.risk) } ?? z.risk
                return ColorScales.catRiskColor(risk)
            }
        )
    }
}
