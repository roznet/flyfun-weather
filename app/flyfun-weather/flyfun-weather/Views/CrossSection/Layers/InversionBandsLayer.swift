import SwiftUI

/// Pink bands for temperature inversions with strength-based opacity.
struct InversionBandsLayer: CrossSectionLayerProtocol {
    let id = "inversion-bands"
    let name = "Inversions"
    let group: LayerGroup = .temperature

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let points = data.points
        guard points.count >= 2 else { return }

        for i in 0..<(points.count - 1) {
            let current = points[i]
            let next = points[i + 1]
            let x0 = transform.distanceToX(current.distanceNm)
            let x1 = transform.distanceToX(next.distanceNm)

            var usedNext = Set<Int>()

            for inv in current.inversions {
                var bestIdx = -1
                var bestOverlap: Double = 0
                for (j, ninv) in next.inversions.enumerated() {
                    if usedNext.contains(j) { continue }
                    let overlap = min(inv.topFt, ninv.topFt) - max(inv.baseFt, ninv.baseFt)
                    if overlap > bestOverlap {
                        bestOverlap = overlap
                        bestIdx = j
                    }
                }

                let opacity = ColorScales.inversionOpacity(inv.strengthC)
                let color = ColorScales.inversionColor.opacity(opacity)

                if bestIdx >= 0 {
                    let ninv = next.inversions[bestIdx]
                    usedNext.insert(bestIdx)
                    fillTrapezoid(&context, x0: x0, x1: x1,
                                  topLeft: transform.altitudeToY(inv.topFt),
                                  topRight: transform.altitudeToY(ninv.topFt),
                                  bottomLeft: transform.altitudeToY(inv.baseFt),
                                  bottomRight: transform.altitudeToY(ninv.baseFt),
                                  color: color)
                } else {
                    let midX = (x0 + x1) / 2
                    let midY = transform.altitudeToY((inv.baseFt + inv.topFt) / 2)
                    fillTrapezoid(&context, x0: x0, x1: midX,
                                  topLeft: transform.altitudeToY(inv.topFt),
                                  topRight: midY,
                                  bottomLeft: transform.altitudeToY(inv.baseFt),
                                  bottomRight: midY,
                                  color: color)
                }
            }
        }
    }

    private func fillTrapezoid(_ context: inout GraphicsContext, x0: CGFloat, x1: CGFloat,
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
}
