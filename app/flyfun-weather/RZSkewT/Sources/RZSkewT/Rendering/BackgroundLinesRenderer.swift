import SwiftUI

/// Precomputed background reference lines for the Skew-T diagram.
/// Created once at init time and cached.
public struct BackgroundLines: Sendable {
    let isotherms: [[(tempC: Double, pressureHPa: Double)]]
    let dryAdiabats: [[(tempC: Double, pressureHPa: Double)]]
    let moistAdiabats: [[(tempC: Double, pressureHPa: Double)]]
    let mixingRatioLines: [(w: Double, points: [(tempC: Double, pressureHPa: Double)])]
    let isobars: [Double]

    public static func compute(config: SkewTConfiguration) -> BackgroundLines {
        // Isotherms: every 10°C
        var isotherms: [[(Double, Double)]] = []
        for t in stride(from: -80.0, through: 50.0, by: 10.0) {
            isotherms.append([
                (t, config.pBottom),
                (t, config.pTop),
            ])
        }

        let dryAdiabats = Thermodynamics.dryAdiabats(
            thetaRange: 250...450, thetaStep: 10,
            pRange: config.pTop...config.pBottom
        )

        let moistAdiabats = Thermodynamics.moistAdiabats(
            pRange: config.pTop...config.pBottom
        )

        let mixingRatioLines = Thermodynamics.mixingRatioLines(
            pRange: max(config.pTop, 400)...config.pBottom
        )

        let isobars = SkewTTransform.standardPressureLevels.filter {
            $0 <= config.pBottom && $0 >= config.pTop
        }

        return BackgroundLines(
            isotherms: isotherms,
            dryAdiabats: dryAdiabats,
            moistAdiabats: moistAdiabats,
            mixingRatioLines: mixingRatioLines,
            isobars: isobars
        )
    }
}

/// Renders the background reference lines on the Skew-T canvas.
public struct BackgroundLinesRenderer {

    public static func render(
        context: inout GraphicsContext,
        transform: SkewTTransform,
        lines: BackgroundLines,
        config: SkewTConfiguration
    ) {
        let plot = transform.plotArea

        // Isobars (horizontal lines at standard pressure levels)
        for p in lines.isobars {
            let y = transform.pressureToY(p)
            var path = Path()
            path.move(to: CGPoint(x: plot.left, y: y))
            path.addLine(to: CGPoint(x: plot.right, y: y))
            context.stroke(path, with: .color(config.isothermColor), lineWidth: config.gridLineWidth)
        }

        // Isotherms (skewed vertical lines)
        for isotherm in lines.isotherms {
            drawCurve(&context, transform: transform, points: isotherm,
                      color: config.isothermColor, lineWidth: config.gridLineWidth)
        }

        // Dry adiabats
        for adiabat in lines.dryAdiabats {
            drawCurve(&context, transform: transform, points: adiabat,
                      color: config.dryAdiabatColor, lineWidth: config.gridLineWidth)
        }

        // Moist adiabats
        for adiabat in lines.moistAdiabats {
            drawCurve(&context, transform: transform, points: adiabat,
                      color: config.moistAdiabatColor, lineWidth: config.gridLineWidth,
                      dash: [4, 4])
        }

        // Mixing ratio lines
        for line in lines.mixingRatioLines {
            drawCurve(&context, transform: transform, points: line.points,
                      color: config.mixingRatioColor, lineWidth: config.gridLineWidth,
                      dash: [2, 4])
        }

        // Highlight 0°C isotherm
        let zeroIsotherm = [(0.0, config.pBottom), (0.0, config.pTop)]
        drawCurve(&context, transform: transform, points: zeroIsotherm,
                  color: .cyan.opacity(0.5), lineWidth: 1.0)
    }

    private static func drawCurve(
        _ context: inout GraphicsContext,
        transform: SkewTTransform,
        points: [(tempC: Double, pressureHPa: Double)],
        color: Color,
        lineWidth: CGFloat,
        dash: [CGFloat]? = nil
    ) {
        guard points.count >= 2 else { return }
        var path = Path()
        let first = transform.point(tempC: points[0].tempC, pressureHPa: points[0].pressureHPa)
        path.move(to: first)
        for i in 1..<points.count {
            let pt = transform.point(tempC: points[i].tempC, pressureHPa: points[i].pressureHPa)
            path.addLine(to: pt)
        }
        let style: StrokeStyle
        if let dash {
            style = StrokeStyle(lineWidth: lineWidth, dash: dash)
        } else {
            style = StrokeStyle(lineWidth: lineWidth)
        }
        context.stroke(path, with: .color(color), style: style)
    }
}
