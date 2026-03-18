import SwiftUI

/// Precomputed background reference lines for the Skew-T diagram.
/// Created once at init time and cached.
public struct BackgroundLines: Sendable {
    public let isotherms: [[AtmosphericPoint]]
    public let dryAdiabats: [[AtmosphericPoint]]
    public let moistAdiabats: [[AtmosphericPoint]]
    public let mixingRatioLines: [MixingRatioLine]
    public let isobars: [Double]

    public static func compute(config: SkewTConfiguration) -> BackgroundLines {
        // Isotherms: every 10°C
        var isotherms: [[AtmosphericPoint]] = []
        for t in stride(from: -80.0, through: 60.0, by: 10.0) {
            isotherms.append([
                AtmosphericPoint(tempC: t, pressureHPa: config.pBottom),
                AtmosphericPoint(tempC: t, pressureHPa: config.pTop),
            ])
        }

        // Dry adiabats: every 20K (MetPy-like density)
        let dryAdiabats = Thermodynamics.dryAdiabats(
            thetaRange: 250...450, thetaStep: 20,
            pRange: config.pTop...config.pBottom
        )

        // Moist adiabats: every 5°C (MetPy-like density)
        let moistAdiabats = Thermodynamics.moistAdiabats(
            startTemps: Array(stride(from: -30.0, through: 35.0, by: 5.0)),
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
        // Isobars (horizontal lines at standard pressure levels)
        for p in lines.isobars {
            let y = transform.pressureToY(p)
            var path = Path()
            path.move(to: CGPoint(x: transform.plotArea.left, y: y))
            path.addLine(to: CGPoint(x: transform.plotArea.right, y: y))
            context.stroke(path, with: .color(config.isothermColor), lineWidth: config.gridLineWidth)
        }

        // Isotherms (skewed vertical lines)
        for isotherm in lines.isotherms {
            drawCurve(&context, transform: transform, points: isotherm,
                      color: config.isothermColor, lineWidth: config.gridLineWidth)
        }

        // 0°C isotherm — prominent cyan line
        let zeroIsotherm = [
            AtmosphericPoint(tempC: 0, pressureHPa: config.pBottom),
            AtmosphericPoint(tempC: 0, pressureHPa: config.pTop),
        ]
        drawCurve(&context, transform: transform, points: zeroIsotherm,
                  color: .cyan.opacity(0.6), lineWidth: 1.5)

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

        // Mixing ratio lines with labels
        for line in lines.mixingRatioLines {
            drawCurve(&context, transform: transform, points: line.points,
                      color: config.mixingRatioColor, lineWidth: config.gridLineWidth,
                      dash: [2, 4])

            // Label at the bottom of the line
            if let bottom = line.points.last {
                let pt = transform.point(tempC: bottom.tempC, pressureHPa: bottom.pressureHPa)
                let labelStr = line.mixingRatioGkg < 1
                    ? String(format: "%.1f", line.mixingRatioGkg)
                    : "\(Int(line.mixingRatioGkg))"
                let label = context.resolve(
                    Text(labelStr).font(.system(size: 7)).foregroundColor(config.mixingRatioColor.opacity(2))
                )
                context.draw(label, at: CGPoint(x: pt.x, y: pt.y + 2), anchor: .top)
            }
        }
    }

    static func drawCurve(
        _ context: inout GraphicsContext,
        transform: SkewTTransform,
        points: [AtmosphericPoint],
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
