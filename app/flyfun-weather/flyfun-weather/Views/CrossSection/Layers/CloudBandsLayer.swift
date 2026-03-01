import SwiftUI

/// Semi-transparent cloud bands with dewpoint depression coloring.
struct CloudBandsLayer: CrossSectionLayerProtocol {
    let id = "cloud-bands"
    let name = "Clouds (Sounding)"
    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let points = data.points
        guard points.count >= 2 else {
            // Single point: draw columns
            for pt in points {
                let x = transform.distanceToX(pt.distanceNm)
                for cl in pt.cloudLayers {
                    let color = ColorScales.cloudFill(dewpointDepressionC: cl.meanDewpointDepressionC, coverage: cl.coverage)
                    let top = transform.altitudeToY(cl.topFt)
                    let base = transform.altitudeToY(cl.baseFt)
                    let rect = CGRect(x: x - 5, y: top, width: 10, height: base - top)
                    context.fill(Path(rect), with: .color(color))
                }
            }
            return
        }

        // Draw matched cloud layers between adjacent points
        for i in 0..<(points.count - 1) {
            let current = points[i]
            let next = points[i + 1]
            let x0 = transform.distanceToX(current.distanceNm)
            let x1 = transform.distanceToX(next.distanceNm)

            var usedNext = Set<Int>()

            for cl in current.cloudLayers {
                // Find best overlap match in next point
                var bestIdx = -1
                var bestOverlap: Double = 0
                for (j, ncl) in next.cloudLayers.enumerated() {
                    if usedNext.contains(j) { continue }
                    let overlap = min(cl.topFt, ncl.topFt) - max(cl.baseFt, ncl.baseFt)
                    if overlap > bestOverlap {
                        bestOverlap = overlap
                        bestIdx = j
                    }
                }

                let color = ColorScales.cloudFill(dewpointDepressionC: cl.meanDewpointDepressionC, coverage: cl.coverage)

                if bestIdx >= 0 {
                    let ncl = next.cloudLayers[bestIdx]
                    usedNext.insert(bestIdx)
                    // Draw trapezoid between matched layers
                    drawTrapezoid(&context, x0: x0, x1: x1,
                                  topLeft: transform.altitudeToY(cl.topFt),
                                  topRight: transform.altitudeToY(ncl.topFt),
                                  bottomLeft: transform.altitudeToY(cl.baseFt),
                                  bottomRight: transform.altitudeToY(ncl.baseFt),
                                  color: color)
                } else {
                    // Unmatched: fade to midpoint
                    let midX = (x0 + x1) / 2
                    let midY = transform.altitudeToY((cl.baseFt + cl.topFt) / 2)
                    drawTrapezoid(&context, x0: x0, x1: midX,
                                  topLeft: transform.altitudeToY(cl.topFt),
                                  topRight: midY,
                                  bottomLeft: transform.altitudeToY(cl.baseFt),
                                  bottomRight: midY,
                                  color: color)
                }
            }

            // Unmatched next-point layers: fade in from midpoint
            for (j, ncl) in next.cloudLayers.enumerated() {
                if usedNext.contains(j) { continue }
                let color = ColorScales.cloudFill(dewpointDepressionC: ncl.meanDewpointDepressionC, coverage: ncl.coverage)
                let midX = (x0 + x1) / 2
                let midY = transform.altitudeToY((ncl.baseFt + ncl.topFt) / 2)
                drawTrapezoid(&context, x0: midX, x1: x1,
                              topLeft: midY,
                              topRight: transform.altitudeToY(ncl.topFt),
                              bottomLeft: midY,
                              bottomRight: transform.altitudeToY(ncl.baseFt),
                              color: color)
            }
        }
    }
}

/// Draw a filled trapezoid (used for smooth band transitions).
private func drawTrapezoid(_ context: inout GraphicsContext, x0: CGFloat, x1: CGFloat,
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
