import SwiftUI

/// Orchestrates rendering of all enabled layers on a GraphicsContext.
///
/// `@MainActor` because it drives `ColorScales` / the active theme; it's only ever
/// built and invoked from the main-actor `Canvas` closures, so this just makes the
/// existing reality compiler-enforced (#320).
@MainActor
struct CrossSectionRenderer {
    let data: VizRouteData
    let enabledLayers: [String: Bool]
    var selectedDistanceNm: Double?
    var aircraftPosition: AircraftPosition?
    /// Active colour theme (#320). The renderer pins the module-level active
    /// theme to this before drawing so every `ColorScales` lookup in the layer
    /// stack resolves against the same theme for the whole frame.
    var themeId: CrossSectionThemeID = .gramet

    /// Lightweight position data for rendering the aircraft icon on the cross-section.
    struct AircraftPosition {
        let distanceNm: Double
        let altitudeFt: Double
        let opacity: Double
    }

    /// Convenience: render the whole scene in one pass (static scene + dynamic
    /// cursor/aircraft overlay). The live view splits these into two separate
    /// Canvases so a scrub tick redraws only the cheap overlay (#303); this stays
    /// for any single-pass caller (previews, snapshots).
    func render(context: inout GraphicsContext, size: CGSize) {
        renderStatic(context: &context, size: size)
        renderCursor(context: &context, size: size)
    }

    /// The static scene: sky, all enabled data layers, and the axes/grid. This is
    /// the expensive pass (O(n_slots × subBlobs) gradient fills for the natural
    /// cloud style) and must only re-run on data/model/layer/size change — never
    /// on a scrub tick (#303). Does NOT draw the cursor or aircraft.
    func renderStatic(context: inout GraphicsContext, size: CGSize) {
        // Pin the shared active theme for this frame. The whole type is
        // `@MainActor`, so the previously-needed runtime assert is now a
        // compile-time guarantee (#320).
        CrossSectionTheme.setActive(themeId)
        let transform = CoordTransform(
            size: size,
            maxDistanceNm: data.totalDistanceNm,
            maxAltitudeFt: data.flightCeilingFt
        )

        // Sky background
        let skyRect = CGRect(
            x: transform.plotArea.left,
            y: transform.plotArea.top,
            width: transform.plotArea.width,
            height: transform.plotArea.height
        )
        context.fill(Path(skyRect), with: .color(ColorScales.skyBlue))

        // Render layers clipped to plot area
        var clipped = context
        clipped.clip(to: Path(skyRect))
        for layer in CrossSectionLayer.allLayers {
            // Terrain always renders (force-on, no UI toggle — mirrors the web
            // panel which omits the terrain group). Its position in `allLayers`
            // keeps the correct mid-stack z-order (after bands, before lines).
            if layer.id == "terrain" || (enabledLayers[layer.id] ?? false) {
                layer.render(context: &clipped, transform: transform, data: data)
            }
        }

        // Axes and grid (drawn outside clip)
        drawAxes(context: &context, transform: transform, data: data)

        // Advisory highlight (scrim + verdict ribbon, #374) renders last — top of
        // the stack, above the axes, matching the web layer order. It is invoked
        // outside the plot-area clip (the ribbon draws in the bottom margin) and
        // manages its own clipping; it no-ops when `data.advisoryHighlights` is
        // nil (no tracked advisory / old pack / model without highlight data).
        HighlightLayer().render(context: &context, transform: transform, data: data)
    }

    /// The dynamic overlay: the scrub cursor rule + the live aircraft marker.
    /// Cheap (O(1)) so it can redraw every drag tick / GPS update without jank
    /// (#303). Both are clipped to the plot area, matching the static pass.
    func renderCursor(context: inout GraphicsContext, size: CGSize) {
        guard selectedDistanceNm != nil || aircraftPosition != nil else { return }
        let transform = CoordTransform(
            size: size,
            maxDistanceNm: data.totalDistanceNm,
            maxAltitudeFt: data.flightCeilingFt
        )
        let skyRect = CGRect(
            x: transform.plotArea.left,
            y: transform.plotArea.top,
            width: transform.plotArea.width,
            height: transform.plotArea.height
        )
        var clipped = context
        clipped.clip(to: Path(skyRect))

        // Selected-point indicator (drawn inside clip so it stays in plot area)
        if let selectedNm = selectedDistanceNm {
            let x = transform.distanceToX(selectedNm)
            var selPath = Path()
            selPath.move(to: CGPoint(x: x, y: transform.plotArea.top))
            selPath.addLine(to: CGPoint(x: x, y: transform.plotArea.bottom))
            clipped.stroke(selPath, with: .color(.orange), lineWidth: 1.5)
        }

        // Aircraft position icon
        if let aircraft = aircraftPosition {
            drawAircraft(context: &clipped, transform: transform, position: aircraft)
        }
    }

    // MARK: - Axes drawing

    private func drawAxes(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let plot = transform.plotArea
        // Grid lines cross the (themed) sky, so the grid colour is theme-driven
        // — white-ish on the coloured skies, dark on the Light theme. Tick
        // labels sit outside the plot on the app background, so they stay
        // `.primary`.
        let gridColor = ColorScales.gridColor
        let textColor = Color.primary

        // Plot border
        context.stroke(Path(CGRect(x: plot.left, y: plot.top, width: plot.width, height: plot.height)),
                       with: .color(.gray.opacity(0.5)), lineWidth: 0.5)

        // Altitude axis (left) + grid lines
        let altInterval = altitudeTickInterval(transform.maxAltitudeFt)
        var alt = altInterval
        while alt < transform.maxAltitudeFt {
            let y = transform.altitudeToY(alt)
            // Grid line
            var gridPath = Path()
            gridPath.move(to: CGPoint(x: plot.left, y: y))
            gridPath.addLine(to: CGPoint(x: plot.right, y: y))
            context.stroke(gridPath, with: .color(gridColor), lineWidth: 0.5)

            // Label
            let label: String
            if alt >= 10000 {
                label = "FL\(Int(alt / 100))"
            } else {
                label = "\(Int(alt))"
            }
            let text = context.resolve(Text(label).font(.system(size: 9)).foregroundColor(textColor))
            context.draw(text, at: CGPoint(x: plot.left - 4, y: y), anchor: .trailing)

            alt += altInterval
        }

        // Distance axis (bottom)
        let distInterval = distanceTickInterval(data.totalDistanceNm)
        var dist = 0.0
        while dist <= data.totalDistanceNm {
            let x = transform.distanceToX(dist)
            // Grid line
            var gridPath = Path()
            gridPath.move(to: CGPoint(x: x, y: plot.top))
            gridPath.addLine(to: CGPoint(x: x, y: plot.bottom))
            context.stroke(gridPath, with: .color(gridColor), lineWidth: 0.5)

            // Label — offset below the verdict-ribbon strip, which hugs the plot
            // bottom at [+2, +8] (#374). Keep in sync with `HighlightLayer`'s
            // ribbonGap/ribbonHeight so the ribbon never paints over the labels
            // (mirrors the web's DISTANCE_LABEL_DY fix).
            let text = context.resolve(Text("\(Int(dist)) nm").font(.system(size: 9)).foregroundColor(textColor))
            context.draw(text, at: CGPoint(x: x, y: plot.bottom + Self.distanceLabelDY), anchor: .top)

            dist += distInterval
        }

        // Waypoint markers
        for wp in data.waypointMarkers {
            let x = transform.distanceToX(wp.distanceNm)
            var wpPath = Path()
            wpPath.move(to: CGPoint(x: x, y: plot.top))
            wpPath.addLine(to: CGPoint(x: x, y: plot.bottom))
            context.stroke(wpPath, with: .color(ColorScales.waypointLineColor), style: StrokeStyle(lineWidth: 0.5, dash: [4, 4]))

            let text = context.resolve(Text(wp.icao).font(.system(size: 8, weight: .bold)).foregroundColor(textColor))
            context.draw(text, at: CGPoint(x: x, y: plot.top - 4), anchor: .bottom)
        }
    }

    // MARK: - Aircraft icon

    private func drawAircraft(context: inout GraphicsContext, transform: CoordTransform, position: AircraftPosition) {
        let x = transform.distanceToX(position.distanceNm)
        // Clamp Y so the icon stays visible even at 0ft (don't let it clip below plot)
        let rawY = transform.altitudeToY(position.altitudeFt)
        let y = min(rawY, transform.plotArea.bottom - 12)
        let point = CGPoint(x: x, y: y)

        // Resolve SF Symbol as side-view airplane
        let symbol = context.resolve(
            Text(Image(systemName: "airplane"))
                .font(.system(size: 22, weight: .bold))
                .foregroundColor(.orange.opacity(position.opacity))
        )
        context.draw(symbol, at: point, anchor: .center)

        // Small crosshair at aircraft position for precision
        let crossSize: CGFloat = 4
        var hLine = Path()
        hLine.move(to: CGPoint(x: x - crossSize, y: y))
        hLine.addLine(to: CGPoint(x: x + crossSize, y: y))
        var vLine = Path()
        vLine.move(to: CGPoint(x: x, y: y - crossSize))
        vLine.addLine(to: CGPoint(x: x, y: y + crossSize))
        let crossColor = Color.orange.opacity(position.opacity * 0.5)
        context.stroke(hLine, with: .color(crossColor), lineWidth: 0.5)
        context.stroke(vLine, with: .color(crossColor), lineWidth: 0.5)
    }

    /// Distance tick labels sit below the advisory verdict ribbon ([+2, +8],
    /// #374) with a small gap. Fits the 30pt bottom margin (label ends ~+22).
    private static let distanceLabelDY: CGFloat = 11

    private func altitudeTickInterval(_ maxAlt: Double) -> Double {
        if maxAlt <= 8000 { return 1000 }
        if maxAlt <= 15000 { return 2000 }
        return 5000
    }

    private func distanceTickInterval(_ totalDist: Double) -> Double {
        if totalDist <= 50 { return 10 }
        if totalDist <= 200 { return 25 }
        if totalDist <= 500 { return 50 }
        return 100
    }
}
