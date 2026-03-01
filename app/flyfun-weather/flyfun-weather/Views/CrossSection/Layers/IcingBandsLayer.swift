import SwiftUI

/// Colored bands for icing zones (blue→orange→red by risk).
struct IcingBandsLayer: CrossSectionLayerProtocol {
    let id = "icing-bands"
    let name = "Icing (Ogimet)"
    let group: LayerGroup = .icing

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        drawRiskBands(
            context: &context,
            transform: transform,
            points: data.points,
            zonesForPoint: { $0.icingZones.map { ($0.baseFt, $0.topFt, $0.risk) } },
            colorForRisk: ColorScales.icingRiskColor
        )
    }
}

/// Reusable band renderer for risk-based layers (icing, CAT).
func drawRiskBands(
    context: inout GraphicsContext,
    transform: CoordTransform,
    points: [VizPoint],
    zonesForPoint: (VizPoint) -> [(baseFt: Double, topFt: Double, risk: String)],
    colorForRisk: (String) -> Color
) {
    guard points.count >= 2 else {
        // Single point: column
        for pt in points {
            let x = transform.distanceToX(pt.distanceNm)
            for zone in zonesForPoint(pt) {
                if zone.risk == "none" { continue }
                let color = colorForRisk(zone.risk)
                let top = transform.altitudeToY(zone.topFt)
                let base = transform.altitudeToY(zone.baseFt)
                let rect = CGRect(x: x - 5, y: top, width: 10, height: base - top)
                context.fill(Path(rect), with: .color(color))
            }
        }
        return
    }

    for i in 0..<(points.count - 1) {
        let current = points[i]
        let next = points[i + 1]
        let x0 = transform.distanceToX(current.distanceNm)
        let x1 = transform.distanceToX(next.distanceNm)
        let currentZones = zonesForPoint(current)
        let nextZones = zonesForPoint(next)

        var usedNext = Set<Int>()

        for zone in currentZones {
            if zone.risk == "none" { continue }

            // Find best overlap match
            var bestIdx = -1
            var bestOverlap: Double = 0
            for (j, nz) in nextZones.enumerated() {
                if usedNext.contains(j) || nz.risk == "none" { continue }
                let overlap = min(zone.topFt, nz.topFt) - max(zone.baseFt, nz.baseFt)
                if overlap > bestOverlap {
                    bestOverlap = overlap
                    bestIdx = j
                }
            }

            // Use higher risk for color
            let risk = zone.risk
            let color = colorForRisk(risk)

            if bestIdx >= 0 {
                let nz = nextZones[bestIdx]
                usedNext.insert(bestIdx)
                drawTrapezoidBand(&context, x0: x0, x1: x1,
                                  topLeft: transform.altitudeToY(zone.topFt),
                                  topRight: transform.altitudeToY(nz.topFt),
                                  bottomLeft: transform.altitudeToY(zone.baseFt),
                                  bottomRight: transform.altitudeToY(nz.baseFt),
                                  color: color)
            } else {
                let midX = (x0 + x1) / 2
                let midY = transform.altitudeToY((zone.baseFt + zone.topFt) / 2)
                drawTrapezoidBand(&context, x0: x0, x1: midX,
                                  topLeft: transform.altitudeToY(zone.topFt),
                                  topRight: midY,
                                  bottomLeft: transform.altitudeToY(zone.baseFt),
                                  bottomRight: midY,
                                  color: color)
            }
        }

        // Unmatched next zones
        for (j, nz) in nextZones.enumerated() {
            if usedNext.contains(j) || nz.risk == "none" { continue }
            let color = colorForRisk(nz.risk)
            let midX = (x0 + x1) / 2
            let midY = transform.altitudeToY((nz.baseFt + nz.topFt) / 2)
            drawTrapezoidBand(&context, x0: midX, x1: x1,
                              topLeft: midY,
                              topRight: transform.altitudeToY(nz.topFt),
                              bottomLeft: midY,
                              bottomRight: transform.altitudeToY(nz.baseFt),
                              color: color)
        }
    }
}

private func drawTrapezoidBand(_ context: inout GraphicsContext, x0: CGFloat, x1: CGFloat,
                               topLeft: CGFloat, topRight: CGFloat,
                               bottomLeft: CGFloat, bottomRight: CGFloat,
                               color: Color) {
    var path = Path()
    path.move(to: CGPoint(x: x0, y: topLeft))
    path.addLine(to: CGPoint(x: x1, y: topRight))
    path.addLine(to: CGPoint(x: x1, y: bottomRight))
    path.addLine(to: CGPoint(x: x0, y: bottomLeft))
    path.closeSubpath()
    context.fill(path, with: .color(color))
}
