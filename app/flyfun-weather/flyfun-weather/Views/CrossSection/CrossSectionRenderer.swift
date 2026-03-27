import SwiftUI

/// Orchestrates rendering of all enabled layers on a GraphicsContext.
struct CrossSectionRenderer {
    let data: VizRouteData
    let enabledLayers: [String: Bool]
    var selectedDistanceNm: Double?

    func render(context: inout GraphicsContext, size: CGSize) {
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
            if enabledLayers[layer.id] ?? false {
                layer.render(context: &clipped, transform: transform, data: data)
            }
        }

        // Selected-point indicator (drawn inside clip so it stays in plot area)
        if let selectedNm = selectedDistanceNm {
            let x = transform.distanceToX(selectedNm)
            var selPath = Path()
            selPath.move(to: CGPoint(x: x, y: transform.plotArea.top))
            selPath.addLine(to: CGPoint(x: x, y: transform.plotArea.bottom))
            clipped.stroke(selPath, with: .color(.orange), lineWidth: 1.5)
        }

        // Axes and grid (drawn outside clip)
        drawAxes(context: &context, transform: transform, data: data)
    }

    // MARK: - Axes drawing

    private func drawAxes(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let plot = transform.plotArea
        let gridColor = Color.gray.opacity(0.2)
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

            // Label
            let text = context.resolve(Text("\(Int(dist)) nm").font(.system(size: 9)).foregroundColor(textColor))
            context.draw(text, at: CGPoint(x: x, y: plot.bottom + 4), anchor: .top)

            dist += distInterval
        }

        // Waypoint markers
        for wp in data.waypointMarkers {
            let x = transform.distanceToX(wp.distanceNm)
            var wpPath = Path()
            wpPath.move(to: CGPoint(x: x, y: plot.top))
            wpPath.addLine(to: CGPoint(x: x, y: plot.bottom))
            context.stroke(wpPath, with: .color(Color.gray.opacity(0.4)), style: StrokeStyle(lineWidth: 0.5, dash: [4, 4]))

            let text = context.resolve(Text(wp.icao).font(.system(size: 8, weight: .bold)).foregroundColor(textColor))
            context.draw(text, at: CGPoint(x: x, y: plot.top - 4), anchor: .bottom)
        }
    }

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
