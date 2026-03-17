import SwiftUI

/// Main orchestrator for rendering a complete Skew-T log-P diagram.
public struct SkewTRenderer {
    public let profile: SoundingProfile
    public let config: SkewTConfiguration
    public let backgroundLines: BackgroundLines

    /// Precomputed parcel path (from surface level).
    public let parcelPath: [(tempC: Double, pressureHPa: Double)]

    public init(profile: SoundingProfile, config: SkewTConfiguration = .default) {
        self.profile = profile
        self.config = config
        self.backgroundLines = BackgroundLines.compute(config: config)

        // Compute parcel path from the lowest level
        if let surface = profile.levels.max(by: { $0.pressureHPa < $1.pressureHPa }),
           let surfaceTd = surface.dewpointC {
            self.parcelPath = Thermodynamics.parcelPath(
                surfaceTempC: surface.temperatureC,
                surfaceDewpointC: surfaceTd,
                surfacePressureHPa: surface.pressureHPa,
                topPressureHPa: config.pTop
            )
        } else {
            self.parcelPath = []
        }
    }

    /// Render the complete Skew-T diagram onto a GraphicsContext.
    public func render(context: inout GraphicsContext, size: CGSize) {
        let transform = SkewTTransform(size: size, config: config)
        let plot = transform.plotArea

        // Background fill
        let plotRect = CGRect(x: plot.left, y: plot.top, width: plot.width, height: plot.height)
        context.fill(Path(plotRect), with: .color(config.backgroundColor))

        // Clip layers to plot area
        var clipped = context
        clipped.clip(to: Path(plotRect))

        // Background reference lines
        BackgroundLinesRenderer.render(context: &clipped, transform: transform,
                                       lines: backgroundLines, config: config)

        // Parcel path with CAPE/CIN shading
        if !parcelPath.isEmpty {
            ProfileRenderer.renderParcelPath(context: &clipped, transform: transform,
                                              parcelPath: parcelPath,
                                              environmentLevels: profile.levels,
                                              config: config)
        }

        // Temperature and dewpoint profiles
        ProfileRenderer.render(context: &clipped, transform: transform,
                                profile: profile, config: config)

        // Axes (outside clip)
        drawAxes(context: &context, transform: transform)

        // Wind barbs (outside clip, to the right)
        WindBarbRenderer.render(context: &context, transform: transform,
                                profile: profile, config: config)

        // Indices text panel
        drawIndices(context: &context, transform: transform)
    }

    // MARK: - Axes

    private func drawAxes(context: inout GraphicsContext, transform: SkewTTransform) {
        let plot = transform.plotArea
        let textColor = Color.primary

        // Plot border
        context.stroke(Path(CGRect(x: plot.left, y: plot.top, width: plot.width, height: plot.height)),
                       with: .color(.gray.opacity(0.5)), lineWidth: 0.5)

        // Pressure labels (left axis)
        for p in transform.visiblePressureLevels {
            let y = transform.pressureToY(p)
            let label = context.resolve(Text("\(Int(p))").font(.system(size: 9)).foregroundColor(textColor))
            context.draw(label, at: CGPoint(x: plot.left - 4, y: y), anchor: .trailing)
        }

        // Temperature labels (bottom axis)
        for t in stride(from: config.tMin, through: config.tMax, by: 10.0) {
            let x = transform.temperatureToX(t, atPressure: config.pBottom)
            guard x >= plot.left && x <= plot.right else { continue }
            let label = context.resolve(Text("\(Int(t))°").font(.system(size: 9)).foregroundColor(textColor))
            context.draw(label, at: CGPoint(x: x, y: plot.bottom + 4), anchor: .top)
        }

        // Axis titles
        let pLabel = context.resolve(Text("hPa").font(.system(size: 8)).foregroundColor(.secondary))
        context.draw(pLabel, at: CGPoint(x: plot.left - 4, y: plot.top - 8), anchor: .trailing)

        let tLabel = context.resolve(Text("°C").font(.system(size: 8)).foregroundColor(.secondary))
        context.draw(tLabel, at: CGPoint(x: plot.right + 4, y: plot.bottom + 4), anchor: .topLeading)
    }

    // MARK: - Indices panel

    private func drawIndices(context: inout GraphicsContext, transform: SkewTTransform) {
        guard let indices = profile.indices else { return }
        let plot = transform.plotArea

        var lines: [String] = []
        if let cape = indices.capeSurfaceJkg {
            lines.append("CAPE: \(Int(cape)) J/kg")
        }
        if let cin = indices.cinSurfaceJkg {
            lines.append("CIN: \(Int(cin)) J/kg")
        }
        if let li = indices.liftedIndex {
            lines.append("LI: \(String(format: "%.1f", li))")
        }
        if let lcl = indices.lclPressureHPa {
            lines.append("LCL: \(Int(lcl)) hPa")
        }

        guard !lines.isEmpty else { return }

        let textStr = lines.joined(separator: "\n")
        let text = context.resolve(
            Text(textStr)
                .font(.system(size: 8, design: .monospaced))
                .foregroundColor(.primary)
        )
        let textSize = text.measure(in: CGSize(width: 200, height: 200))
        let padding: CGFloat = 6
        let boxRect = CGRect(
            x: plot.left + 4,
            y: plot.top + 4,
            width: textSize.width + padding * 2,
            height: textSize.height + padding * 2
        )
        context.fill(Path(roundedRect: boxRect, cornerRadius: 4),
                     with: .color(.white.opacity(0.85)))
        context.draw(text, at: CGPoint(x: boxRect.midX, y: boxRect.midY), anchor: .center)
    }
}
