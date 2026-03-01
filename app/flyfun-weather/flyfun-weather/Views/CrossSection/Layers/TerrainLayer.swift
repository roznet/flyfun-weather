import SwiftUI

/// Earth-tone filled polygon below terrain elevation with smooth interpolation.
struct TerrainLayer: CrossSectionLayerProtocol {
    let id = "terrain"
    let name = "Terrain"
    let group: LayerGroup = .terrain

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard let terrain = data.terrainProfile, terrain.count >= 2 else { return }

        var path = Path()
        let xs = terrain.map { transform.distanceToX($0.distanceNm) }
        let ys = terrain.map { transform.altitudeToY($0.elevationFt) }

        // Smooth terrain using monotone cubic tangents
        let tangents = monotoneCubicTangents(xs: xs, ys: ys)

        path.move(to: CGPoint(x: xs[0], y: transform.plotArea.bottom))
        path.addLine(to: CGPoint(x: xs[0], y: ys[0]))

        for i in 0..<(xs.count - 1) {
            let dx = xs[i + 1] - xs[i]
            let cp1 = CGPoint(x: xs[i] + dx / 3, y: ys[i] + tangents[i] * dx / 3)
            let cp2 = CGPoint(x: xs[i + 1] - dx / 3, y: ys[i + 1] - tangents[i + 1] * dx / 3)
            path.addCurve(to: CGPoint(x: xs[i + 1], y: ys[i + 1]), control1: cp1, control2: cp2)
        }

        path.addLine(to: CGPoint(x: xs.last!, y: transform.plotArea.bottom))
        path.closeSubpath()

        context.fill(path, with: .color(ColorScales.terrainFill))
        // Outline
        var outline = Path()
        outline.move(to: CGPoint(x: xs[0], y: ys[0]))
        for i in 0..<(xs.count - 1) {
            let dx = xs[i + 1] - xs[i]
            let cp1 = CGPoint(x: xs[i] + dx / 3, y: ys[i] + tangents[i] * dx / 3)
            let cp2 = CGPoint(x: xs[i + 1] - dx / 3, y: ys[i + 1] - tangents[i + 1] * dx / 3)
            outline.addCurve(to: CGPoint(x: xs[i + 1], y: ys[i + 1]), control1: cp1, control2: cp2)
        }
        context.stroke(outline, with: .color(ColorScales.terrainStroke), lineWidth: 1.5)
    }
}

// MARK: - Monotone cubic spline (Fritsch-Carlson)

/// Computes tangent slopes for monotone cubic interpolation.
func monotoneCubicTangents(xs: [CGFloat], ys: [CGFloat]) -> [CGFloat] {
    let n = xs.count
    guard n >= 2 else { return Array(repeating: 0, count: n) }

    // Slopes between adjacent points
    var deltas = [CGFloat](repeating: 0, count: n - 1)
    for i in 0..<(n - 1) {
        let dx = xs[i + 1] - xs[i]
        deltas[i] = dx != 0 ? (ys[i + 1] - ys[i]) / dx : 0
    }

    // Initial tangent estimates
    var tangents = [CGFloat](repeating: 0, count: n)
    tangents[0] = deltas[0]
    tangents[n - 1] = deltas[n - 2]
    for i in 1..<(n - 1) {
        tangents[i] = (deltas[i - 1] + deltas[i]) / 2
    }

    // Monotonicity corrections
    for i in 0..<(n - 1) {
        if deltas[i] == 0 {
            tangents[i] = 0
            tangents[i + 1] = 0
        } else {
            let alpha = tangents[i] / deltas[i]
            let beta = tangents[i + 1] / deltas[i]
            let tau = alpha * alpha + beta * beta
            if tau > 9 {
                let s = 3.0 / sqrt(tau)
                tangents[i] = s * alpha * deltas[i]
                tangents[i + 1] = s * beta * deltas[i]
            }
        }
    }

    return tangents
}
