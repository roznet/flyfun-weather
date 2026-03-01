import SwiftUI

/// Freezing level, -10C, -20C isotherms as smooth lines.
struct TemperatureLinesLayer: CrossSectionLayerProtocol {
    enum Metric {
        case freezingLevel
        case minus10c
        case minus20c
    }

    let metric: Metric

    var id: String {
        switch metric {
        case .freezingLevel: "freezing-level"
        case .minus10c: "minus-10c"
        case .minus20c: "minus-20c"
        }
    }

    var name: String {
        switch metric {
        case .freezingLevel: "Freezing Level (0°C)"
        case .minus10c: "-10°C Level"
        case .minus20c: "-20°C Level"
        }
    }

    let group: LayerGroup = .temperature

    private var color: Color {
        switch metric {
        case .freezingLevel: ColorScales.freezingLevelColor
        case .minus10c: ColorScales.minus10cColor
        case .minus20c: ColorScales.minus20cColor
        }
    }

    private var lineWidth: CGFloat {
        switch metric {
        case .freezingLevel: 2.0
        case .minus10c: 1.5
        case .minus20c: 1.0
        }
    }

    private var dashed: Bool {
        metric == .minus20c
    }

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        var xs: [CGFloat] = []
        var ys: [CGFloat] = []

        for pt in data.points {
            let alt: Double?
            switch metric {
            case .freezingLevel: alt = pt.altitudeLines.freezingLevelFt
            case .minus10c: alt = pt.altitudeLines.minus10cLevelFt
            case .minus20c: alt = pt.altitudeLines.minus20cLevelFt
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

        var style = StrokeStyle(lineWidth: lineWidth)
        if dashed {
            style.dash = [6, 4]
        }
        context.stroke(path, with: .color(color), style: style)
    }
}
