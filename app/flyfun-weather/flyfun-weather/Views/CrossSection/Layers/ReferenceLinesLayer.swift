import SwiftUI

/// Cruise altitude (the climb / descent path when the pack has a live one,
/// #1005) + flight ceiling dashed reference lines.
struct ReferenceLinesLayer: CrossSectionLayerProtocol {
    let id = "reference-lines"
    let name = "Reference Lines"
    let group: LayerGroup = .reference

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let plot = transform.plotArea

        // Cruise altitude: follows the climb and descent when there is a path.
        let cruiseLabel = "FL\(Int(data.cruiseAltitudeFt / 100))"
        if let flightPath = data.flightPath, flightPath.count >= 2 {
            drawPathLine(&context, path: flightPath, transform: transform, label: cruiseLabel,
                         cruiseFt: data.cruiseAltitudeFt, color: ColorScales.cruiseAltitudeColor)
        } else {
            let cruiseY = transform.altitudeToY(data.cruiseAltitudeFt)
            drawRefLine(&context, y: cruiseY, left: plot.left, right: plot.right,
                        label: cruiseLabel,
                        color: ColorScales.cruiseAltitudeColor)
        }

        // Flight ceiling (only if significantly different from cruise)
        if abs(data.ceilingAltitudeFt - data.cruiseAltitudeFt) >= 1000 {
            let ceilingY = transform.altitudeToY(data.ceilingAltitudeFt)
            drawRefLine(&context, y: ceilingY, left: plot.left, right: plot.right,
                        label: "Flight ceiling \(Int(data.ceilingAltitudeFt))'",
                        color: ColorScales.ceilingColor)
        }
    }

    /// The flight path as a polyline, labelled above the top of climb.
    private func drawPathLine(_ context: inout GraphicsContext, path points: [VizFlightPathPoint],
                              transform: CoordTransform, label: String, cruiseFt: Double, color: Color) {
        var path = Path()
        for (index, point) in points.enumerated() {
            let location = CGPoint(x: transform.distanceToX(point.distanceNm),
                                   y: transform.altitudeToY(point.altitudeFt))
            if index == 0 { path.move(to: location) } else { path.addLine(to: location) }
        }
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 1.5, dash: [8, 4]))

        guard let top = points.labelPoint(cruiseFt: cruiseFt) else { return }
        let plot = transform.plotArea
        let text = context.resolve(Text(label).font(.caption2).foregroundColor(color))
        let textSize = text.measure(in: CGSize(width: 200, height: 20))
        let padding: CGFloat = 4
        let width = textSize.width + padding * 2
        let height = textSize.height + padding * 2
        // Kept inside the plot: the top of climb can sit near either edge on a short leg.
        let x = min(max(transform.distanceToX(top.distanceNm) + 4, plot.left), plot.right - width)
        let y = max(transform.altitudeToY(top.altitudeFt) - height - 4, plot.top)
        let pillRect = CGRect(x: x, y: y, width: width, height: height)
        context.fill(Path(roundedRect: pillRect, cornerRadius: 3), with: .color(.white.opacity(0.8)))
        context.draw(text, at: CGPoint(x: pillRect.midX, y: pillRect.midY), anchor: .center)
    }

    private func drawRefLine(_ context: inout GraphicsContext, y: CGFloat, left: CGFloat, right: CGFloat,
                             label: String, color: Color) {
        var path = Path()
        path.move(to: CGPoint(x: left, y: y))
        path.addLine(to: CGPoint(x: right, y: y))
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 1.5, dash: [8, 4]))

        // Label with background pill
        let text = context.resolve(Text(label).font(.caption2).foregroundColor(color))
        let textSize = text.measure(in: CGSize(width: 200, height: 20))
        let padding: CGFloat = 4
        let pillRect = CGRect(
            x: right - textSize.width - padding * 2 - 4,
            y: y - textSize.height / 2 - padding,
            width: textSize.width + padding * 2,
            height: textSize.height + padding * 2
        )
        context.fill(Path(roundedRect: pillRect, cornerRadius: 3), with: .color(.white.opacity(0.8)))
        context.draw(text, at: CGPoint(x: pillRect.midX, y: pillRect.midY), anchor: .center)
    }
}
