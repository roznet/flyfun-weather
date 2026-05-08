import SwiftUI

/// Thermodynamic convective tower visualisation. Uses LFC/LCL → EL bounds from
/// the parcel sounding analysis, with a fallback that pushes tower tops up to
/// freezing / -10°C / -20°C levels when the EL on coarse pressure levels is
/// suspiciously shallow (matches web's estimateTowerTop logic).
struct ThermoConvectiveBgLayer: CrossSectionLayerProtocol {
    let id = "thermo-convective-bg"
    let name = "Convective (Thermo)"
    let group: LayerGroup = .convection

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let pts = data.points
        for i in 0..<pts.count {
            let p = pts[i]
            let risk = p.convectiveRisk
            if risk == "none" || risk == "marginal" { continue }
            guard let baseFt = p.convectiveBaseFt, let rawTopFt = p.convectiveTopFt else { continue }

            let topFt = estimateThermoTowerTop(point: p, baseFt: baseFt, thermodynamicElFt: rawTopFt)

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
