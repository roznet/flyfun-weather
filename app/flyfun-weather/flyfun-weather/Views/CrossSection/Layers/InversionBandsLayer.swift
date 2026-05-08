import SwiftUI

/// Pink bands for temperature inversions with strength-based opacity.
struct InversionBandsLayer: CrossSectionLayerProtocol {
    let id = "inversion-bands"
    let name = "Inversions"
    let group: LayerGroup = .stability

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        renderMatchedZones(
            &context, transform: transform, data: data,
            getZones: { $0.inversions },
            getColor: { inv, matched in
                let avgStrength = matched.map { (inv.strengthC + $0.strengthC) / 2 } ?? inv.strengthC
                let opacity = ColorScales.inversionOpacity(avgStrength)
                return ColorScales.inversionColor.opacity(opacity)
            }
        )
    }
}
