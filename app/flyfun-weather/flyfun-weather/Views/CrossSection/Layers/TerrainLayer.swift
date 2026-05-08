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
