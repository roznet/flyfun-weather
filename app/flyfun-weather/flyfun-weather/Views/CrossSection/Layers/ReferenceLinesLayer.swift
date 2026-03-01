import SwiftUI

/// Cruise altitude + flight ceiling dashed reference lines.
struct ReferenceLinesLayer: CrossSectionLayerProtocol {
    let id = "reference-lines"
    let name = "Reference Lines"
    let group: LayerGroup = .reference

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let plot = transform.plotArea

        // Cruise altitude
        let cruiseY = transform.altitudeToY(data.cruiseAltitudeFt)
        drawRefLine(&context, y: cruiseY, left: plot.left, right: plot.right,
                    label: "FL\(Int(data.cruiseAltitudeFt / 100))",
                    color: ColorScales.cruiseAltitudeColor)

        // Flight ceiling (only if significantly different from cruise)
        if abs(data.ceilingAltitudeFt - data.cruiseAltitudeFt) >= 1000 {
            let ceilingY = transform.altitudeToY(data.ceilingAltitudeFt)
            drawRefLine(&context, y: ceilingY, left: plot.left, right: plot.right,
                        label: "Ceiling \(Int(data.ceilingAltitudeFt))'",
                        color: .gray.opacity(0.5))
        }
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
