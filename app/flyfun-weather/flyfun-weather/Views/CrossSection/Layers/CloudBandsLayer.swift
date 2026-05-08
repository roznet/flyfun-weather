import SwiftUI

/// Semi-transparent cloud bands with dewpoint-depression-derived colour and
/// coverage-modulated opacity. Smooth cubic top + base curves.
struct CloudBandsLayer: CrossSectionLayerProtocol {
    let id = "cloud-bands"
    let name = "Clouds (Sounding)"
    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        renderMatchedZones(
            &context, transform: transform, data: data,
            getZones: { $0.cloudLayers },
            getColor: { cl, matched in
                let dd = avgDD(cl, matched)
                return ColorScales.cloudFill(dewpointDepressionC: dd, coverage: cl.coverage)
            }
        )
    }

    private func avgDD(_ a: VizCloudLayer, _ b: VizCloudLayer?) -> Double? {
        guard let b else { return a.meanDewpointDepressionC }
        if let av = a.meanDewpointDepressionC, let bv = b.meanDewpointDepressionC {
            return (av + bv) / 2
        }
        return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC
    }
}
