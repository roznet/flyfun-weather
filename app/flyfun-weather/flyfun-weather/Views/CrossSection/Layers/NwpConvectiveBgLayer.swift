import SwiftUI

/// NWP convective tower visualisation. Uses model-computed convective base/top
/// directly — no thermodynamic estimate fallback. Same column rendering as
/// the thermo layer for visual consistency.
struct NwpConvectiveBgLayer: CrossSectionLayerProtocol {
    let id = "nwp-convective-bg"
    let name = "Convective (NWP)"
    let group: LayerGroup = .convection

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let pts = data.points
        for i in 0..<pts.count {
            let p = pts[i]
            let risk = p.nwpConvectiveRisk
            if risk == "none" || risk == "marginal" { continue }
            // Skip if the model didn't supply bounds — drawing full-height columns
            // would be misleading.
            guard let baseFt = p.nwpConvectiveBaseFt, let topFt = p.nwpConvectiveTopFt else { continue }

            let bounds = columnBounds(pointIndex: i, in: pts, transform: transform)
            let yTop = transform.altitudeToY(topFt)
            let yBase = transform.altitudeToY(baseFt)
            drawConvectiveTower(
                &context, transform: transform,
                risk: risk,
                xLeft: bounds.left, xRight: bounds.right,
                yTop: yTop, yBase: yBase
            )
        }
    }
}
