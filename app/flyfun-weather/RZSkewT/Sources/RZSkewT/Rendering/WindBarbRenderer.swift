import SwiftUI

/// Renders standard WMO wind barbs alongside the Skew-T plot.
public struct WindBarbRenderer {

    /// Draw wind barbs in a column to the right of the plot area.
    public static func render(
        context: inout GraphicsContext,
        transform: SkewTTransform,
        profile: SoundingProfile,
        config: SkewTConfiguration
    ) {
        let barbX = transform.plotArea.right + 30
        let barbLength: CGFloat = 20

        for level in profile.levels {
            guard let speed = level.windSpeedKt,
                  let direction = level.windDirectionDeg,
                  speed >= 0 else { continue }

            let y = transform.pressureToY(level.pressureHPa)
            guard y >= transform.plotArea.top && y <= transform.plotArea.bottom else { continue }

            drawBarb(context: &context, center: CGPoint(x: barbX, y: y),
                     speedKt: speed, directionDeg: direction,
                     length: barbLength, color: config.windBarbColor)
        }
    }

    /// Draw a single wind barb at the given position.
    /// Direction is meteorological (degrees from which wind blows).
    private static func drawBarb(
        context: inout GraphicsContext,
        center: CGPoint,
        speedKt: Double,
        directionDeg: Double,
        length: CGFloat,
        color: Color
    ) {
        if speedKt < 2.5 {
            // Calm — draw circle
            let r: CGFloat = 4
            let circle = Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2))
            context.stroke(circle, with: .color(color), lineWidth: 1)
            return
        }

        // Direction: meteorological convention — wind FROM this direction
        // On a standard plot, 0° = North (up), 90° = East (right)
        let angleRad = (directionDeg + 180) * .pi / 180.0 // direction wind blows TO
        let dx = CGFloat(sin(angleRad))
        let dy = CGFloat(-cos(angleRad))

        // Staff line from center in the wind direction
        let staffEnd = CGPoint(x: center.x + dx * length, y: center.y + dy * length)
        var path = Path()
        path.move(to: center)
        path.addLine(to: staffEnd)

        // Decompose speed into flags (50kt), full barbs (10kt), half barbs (5kt)
        var remaining = Int(speedKt + 2.5) / 5 * 5 // round to nearest 5
        let flags = remaining / 50
        remaining %= 50
        let fullBarbs = remaining / 10
        remaining %= 10
        let halfBarbs = remaining / 5

        // Barb perpendicular direction (rotated 60° from staff)
        let barbAngle = angleRad + .pi / 3 // 60° off staff
        let barbDx = CGFloat(sin(barbAngle))
        let barbDy = CGFloat(-cos(barbAngle))
        let barbLen: CGFloat = 10
        let flagLen: CGFloat = 10
        let spacing: CGFloat = 3

        var offset: CGFloat = 0

        // Draw flags (triangles for 50kt)
        for _ in 0..<flags {
            let base = CGPoint(x: staffEnd.x - dx * offset, y: staffEnd.y - dy * offset)
            let tip = CGPoint(x: base.x + barbDx * flagLen, y: base.y + barbDy * flagLen)
            let next = CGPoint(x: staffEnd.x - dx * (offset + spacing * 2), y: staffEnd.y - dy * (offset + spacing * 2))
            var flag = Path()
            flag.move(to: base)
            flag.addLine(to: tip)
            flag.addLine(to: next)
            flag.closeSubpath()
            context.fill(flag, with: .color(color))
            offset += spacing * 2
        }

        // Draw full barbs (long lines for 10kt)
        for _ in 0..<fullBarbs {
            let base = CGPoint(x: staffEnd.x - dx * offset, y: staffEnd.y - dy * offset)
            let tip = CGPoint(x: base.x + barbDx * barbLen, y: base.y + barbDy * barbLen)
            path.move(to: base)
            path.addLine(to: tip)
            offset += spacing
        }

        // Draw half barbs (short lines for 5kt)
        for _ in 0..<halfBarbs {
            let base = CGPoint(x: staffEnd.x - dx * offset, y: staffEnd.y - dy * offset)
            let tip = CGPoint(x: base.x + barbDx * barbLen * 0.5, y: base.y + barbDy * barbLen * 0.5)
            path.move(to: base)
            path.addLine(to: tip)
            offset += spacing
        }

        context.stroke(path, with: .color(color), lineWidth: 1.5)
    }
}
