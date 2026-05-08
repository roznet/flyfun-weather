import SwiftUI

/// SFIP icing zone bands. Distinct hue (purple/orange) from Ogimet so users
/// can tell which method is rendering when comparing.
struct SfipBandsLayer: CrossSectionLayerProtocol {
    let id = "sfip-bands"
    let name = "Icing (SFIP)"
    let group: LayerGroup = .icing

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        renderMatchedZones(
            &context, transform: transform, data: data,
            getZones: { $0.sfipZones.filter { $0.risk != "none" } },
            getColor: { z, matched in
                let risk = matched.map { maxRisk(z.risk, $0.risk) } ?? z.risk
                return ColorScales.sfipRiskColor(risk)
            }
        )
    }
}

extension VizSfipZone: MatchableZone {}
