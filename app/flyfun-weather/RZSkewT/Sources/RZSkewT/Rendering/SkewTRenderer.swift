import SwiftUI

/// Main orchestrator for rendering a complete Skew-T log-P diagram.
public struct SkewTRenderer {
    public let profile: SoundingProfile
    public let config: SkewTConfiguration
    public let backgroundLines: BackgroundLines

    /// Precomputed parcel path (from surface level).
    public let parcelPath: [AtmosphericPoint]

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

        // LCL / LFC / EL marker lines (inside clip)
        drawLevelMarkers(context: &clipped, transform: transform)

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

        // Pressure labels (left axis) + FL labels (right axis)
        for p in transform.visiblePressureLevels {
            let y = transform.pressureToY(p)

            // Left: pressure in hPa
            let pLabel = context.resolve(Text("\(Int(p))").font(.system(size: 9)).foregroundColor(textColor))
            context.draw(pLabel, at: CGPoint(x: plot.left - 4, y: y), anchor: .trailing)

            // Right: flight level (approximate from standard atmosphere)
            let altFt = Thermodynamics.pressureToAltitude(p)
            let flLabel: String
            if altFt >= 5000 {
                flLabel = "FL\(Int(altFt / 100))"
            } else {
                flLabel = "\(Int(altFt))'"
            }
            let flText = context.resolve(Text(flLabel).font(.system(size: 8)).foregroundColor(.secondary))
            context.draw(flText, at: CGPoint(x: plot.right + 4, y: y), anchor: .leading)
        }

        // Temperature labels (bottom axis)
        for t in stride(from: config.tMin, through: config.tMax, by: 10.0) {
            let x = transform.temperatureToX(t, atPressure: config.pBottom)
            guard x >= plot.left && x <= plot.right else { continue }
            let label = context.resolve(Text("\(Int(t))°").font(.system(size: 9)).foregroundColor(textColor))
            context.draw(label, at: CGPoint(x: x, y: plot.bottom + 4), anchor: .top)
        }

        // Axis titles
        let hpaLabel = context.resolve(Text("hPa").font(.system(size: 8)).foregroundColor(.secondary))
        context.draw(hpaLabel, at: CGPoint(x: plot.left - 4, y: plot.top - 8), anchor: .trailing)

        let flTitle = context.resolve(Text("FL").font(.system(size: 8)).foregroundColor(.secondary))
        context.draw(flTitle, at: CGPoint(x: plot.right + 4, y: plot.top - 8), anchor: .leading)
    }

    // MARK: - LCL / LFC / EL marker lines

    private func drawLevelMarkers(context: inout GraphicsContext, transform: SkewTTransform) {
        guard let indices = profile.indices else { return }

        // LCL
        if let lcl = indices.lclPressureHPa {
            drawMarkerLine(context: &context, transform: transform, pressureHPa: lcl,
                          label: "LCL", color: .orange)
        }

        // LFC
        if let lfc = indices.lfcPressureHPa {
            drawMarkerLine(context: &context, transform: transform, pressureHPa: lfc,
                          label: "LFC", color: .brown)
        }

        // EL
        if let el = indices.elPressureHPa {
            drawMarkerLine(context: &context, transform: transform, pressureHPa: el,
                          label: "EL", color: .purple)
        }

        // Freezing level (from altitude, convert to approximate pressure)
        if let fzFt = indices.freezingLevelFt {
            let fzP = Thermodynamics.altitudeToPressure(fzFt)
            if fzP >= config.pTop && fzP <= config.pBottom {
                drawMarkerLine(context: &context, transform: transform, pressureHPa: fzP,
                              label: "0°C", color: .cyan)
            }
        }
    }

    private func drawMarkerLine(
        context: inout GraphicsContext,
        transform: SkewTTransform,
        pressureHPa: Double,
        label: String,
        color: Color
    ) {
        let plot = transform.plotArea
        let y = transform.pressureToY(pressureHPa)
        guard y >= plot.top && y <= plot.bottom else { return }

        // Dashed horizontal line
        var path = Path()
        path.move(to: CGPoint(x: plot.left, y: y))
        path.addLine(to: CGPoint(x: plot.right, y: y))
        context.stroke(path, with: .color(color.opacity(0.6)),
                       style: StrokeStyle(lineWidth: 1.0, dash: [6, 3]))

        // Label pill on the left edge
        let text = context.resolve(Text(label).font(.system(size: 7, weight: .bold)).foregroundColor(color))
        let textSize = text.measure(in: CGSize(width: 60, height: 20))
        let padding: CGFloat = 2
        let pillRect = CGRect(
            x: plot.left + 2,
            y: y - textSize.height / 2 - padding,
            width: textSize.width + padding * 2,
            height: textSize.height + padding * 2
        )
        context.fill(Path(roundedRect: pillRect, cornerRadius: 2),
                     with: .color(config.panelBackgroundColor))
        context.draw(text, at: CGPoint(x: pillRect.midX, y: pillRect.midY), anchor: .center)
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
            x: plot.right - textSize.width - padding * 2 - 4,
            y: plot.top + 4,
            width: textSize.width + padding * 2,
            height: textSize.height + padding * 2
        )
        context.fill(Path(roundedRect: boxRect, cornerRadius: 4),
                     with: .color(config.panelBackgroundColor))
        context.draw(text, at: CGPoint(x: boxRect.midX, y: boxRect.midY), anchor: .center)
    }
}
