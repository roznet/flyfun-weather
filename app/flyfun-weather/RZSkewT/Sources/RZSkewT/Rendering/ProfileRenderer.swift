import SwiftUI

/// Renders the temperature and dewpoint profiles on the Skew-T.
public struct ProfileRenderer {

    public static func render(
        context: inout GraphicsContext,
        transform: SkewTTransform,
        profile: SoundingProfile,
        config: SkewTConfiguration
    ) {
        let sorted = profile.levels.sorted { $0.pressureHPa > $1.pressureHPa }

        // Temperature profile
        drawProfile(&context, transform: transform, levels: sorted,
                    valueForLevel: { $0.temperatureC },
                    color: config.temperatureColor, lineWidth: config.profileLineWidth)

        // Dewpoint profile
        drawProfile(&context, transform: transform, levels: sorted,
                    valueForLevel: { $0.dewpointC },
                    color: config.dewpointColor, lineWidth: config.profileLineWidth)
    }

    /// Draw the parcel path and shade CAPE/CIN regions.
    public static func renderParcelPath(
        context: inout GraphicsContext,
        transform: SkewTTransform,
        parcelPath: [(tempC: Double, pressureHPa: Double)],
        environmentLevels: [SoundingLevel],
        config: SkewTConfiguration
    ) {
        guard parcelPath.count >= 2 else { return }

        // Sort environment levels once for all interpolation calls
        let sortedEnv = environmentLevels.sorted { $0.pressureHPa > $1.pressureHPa }

        // Shade CAPE (parcel warmer than environment) and CIN (parcel cooler)
        shadeBuoyancy(&context, transform: transform, parcelPath: parcelPath,
                      sortedEnvironment: sortedEnv)

        // Draw parcel path as dashed black line (on top of shading)
        var path = Path()
        let first = transform.point(tempC: parcelPath[0].tempC, pressureHPa: parcelPath[0].pressureHPa)
        path.move(to: first)
        for i in 1..<parcelPath.count {
            let pt = transform.point(tempC: parcelPath[i].tempC, pressureHPa: parcelPath[i].pressureHPa)
            path.addLine(to: pt)
        }
        context.stroke(path, with: .color(.black.opacity(0.7)),
                       style: StrokeStyle(lineWidth: 1.5, dash: [6, 3]))
    }

    // MARK: - Private

    private static func drawProfile(
        _ context: inout GraphicsContext,
        transform: SkewTTransform,
        levels: [SoundingLevel],
        valueForLevel: (SoundingLevel) -> Double?,
        color: Color,
        lineWidth: CGFloat
    ) {
        var path = Path()
        var started = false

        for level in levels {
            guard let t = valueForLevel(level) else { continue }
            let pt = transform.point(tempC: t, pressureHPa: level.pressureHPa)
            if !started {
                path.move(to: pt)
                started = true
            } else {
                path.addLine(to: pt)
            }
        }

        if started {
            context.stroke(path, with: .color(color), lineWidth: lineWidth)
        }
    }

    private static func shadeBuoyancy(
        _ context: inout GraphicsContext,
        transform: SkewTTransform,
        parcelPath: [(tempC: Double, pressureHPa: Double)],
        sortedEnvironment: [SoundingLevel]
    ) {
        for i in 0..<(parcelPath.count - 1) {
            let p = parcelPath[i].pressureHPa
            let tParcel = parcelPath[i].tempC
            guard let tEnv = interpolateEnvTemp(at: p, sortedLevels: sortedEnvironment) else { continue }

            let pNext = parcelPath[i + 1].pressureHPa
            let tParcelNext = parcelPath[i + 1].tempC
            let tEnvNext = interpolateEnvTemp(at: pNext, sortedLevels: sortedEnvironment) ?? tEnv

            let isPositive = tParcel > tEnv

            var region = Path()
            region.move(to: transform.point(tempC: tParcel, pressureHPa: p))
            region.addLine(to: transform.point(tempC: tParcelNext, pressureHPa: pNext))
            region.addLine(to: transform.point(tempC: tEnvNext, pressureHPa: pNext))
            region.addLine(to: transform.point(tempC: tEnv, pressureHPa: p))
            region.closeSubpath()

            let color: Color = isPositive
                ? .red.opacity(0.12)   // CAPE
                : .blue.opacity(0.12)  // CIN
            context.fill(region, with: .color(color))
        }
    }

    /// Interpolate environment temperature from pre-sorted levels (decreasing pressure).
    private static func interpolateEnvTemp(at pressureHPa: Double, sortedLevels: [SoundingLevel]) -> Double? {
        for i in 0..<(sortedLevels.count - 1) {
            let pBelow = sortedLevels[i].pressureHPa
            let pAbove = sortedLevels[i + 1].pressureHPa
            if pressureHPa <= pBelow && pressureHPa >= pAbove {
                let frac = log(pBelow / pressureHPa) / log(pBelow / pAbove)
                return sortedLevels[i].temperatureC + frac * (sortedLevels[i + 1].temperatureC - sortedLevels[i].temperatureC)
            }
        }
        return sortedLevels.min(by: { abs($0.pressureHPa - pressureHPa) < abs($1.pressureHPa - pressureHPa) })?.temperatureC
    }
}
