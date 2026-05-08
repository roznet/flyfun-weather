import SwiftUI

/// Ogimet-NWP icing zone bands. Same risk colour scale as Ogimet-DD; the
/// distinction is in the source — these zones are scaled by NWP cloud
/// coverage rather than dewpoint depression.
struct IcingOgimetNwpBandsLayer: CrossSectionLayerProtocol {
    let id = "icing-ogimet-nwp-bands"
    let name = "Icing (Ogimet-NWP)"
    let group: LayerGroup = .icing

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        renderMatchedZones(
            &context, transform: transform, data: data,
            getZones: { $0.icingOgimetNwpZones.filter { $0.risk != "none" } },
            getColor: { z, matched in
                let risk = matched.map { maxRisk(z.risk, $0.risk) } ?? z.risk
                return ColorScales.icingRiskColor(risk)
            }
        )
    }
}
