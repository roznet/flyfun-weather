import SwiftUI

/// LCL, LFC, and EL lines as smooth route-following stability references.
struct StabilityLinesLayer: CrossSectionLayerProtocol {
    enum Metric {
        case lcl
        case lfc
        case el
    }

    let metric: Metric

    var id: String {
        switch metric {
        case .lcl: "lcl"
        case .lfc: "lfc"
        case .el: "el"
        }
    }

    var name: String {
        switch metric {
        case .lcl: "LCL"
        case .lfc: "LFC"
        case .el: "EL"
        }
    }

    let group: LayerGroup = .stability

    private var color: Color {
        switch metric {
        case .lcl: ColorScales.lclColor
        case .lfc: ColorScales.lfcColor
        case .el: ColorScales.elColor
        }
    }

    private var lineWidth: CGFloat {
        switch metric {
        case .lcl: 2.0
        case .lfc, .el: 1.5
        }
    }

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        var xs: [CGFloat] = []
        var ys: [CGFloat] = []

        for pt in data.points {
            let alt: Double?
            switch metric {
            case .lcl: alt = pt.altitudeLines.lclAltitudeFt
            case .lfc: alt = pt.altitudeLines.lfcAltitudeFt
            case .el: alt = pt.altitudeLines.elAltitudeFt
            }
            guard let alt else { continue }
            xs.append(transform.distanceToX(pt.distanceNm))
            ys.append(transform.altitudeToY(alt))
        }

        guard xs.count >= 2 else { return }

        let tangents = monotoneCubicTangents(xs: xs, ys: ys)
        var path = Path()
        path.move(to: CGPoint(x: xs[0], y: ys[0]))

        for i in 0..<(xs.count - 1) {
            let dx = xs[i + 1] - xs[i]
            let cp1 = CGPoint(x: xs[i] + dx / 3, y: ys[i] + tangents[i] * dx / 3)
            let cp2 = CGPoint(x: xs[i + 1] - dx / 3, y: ys[i + 1] - tangents[i + 1] * dx / 3)
            path.addCurve(to: CGPoint(x: xs[i + 1], y: ys[i + 1]), control1: cp1, control2: cp2)
        }

        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, dash: [6, 4]))
    }
}
