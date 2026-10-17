import SwiftUI

/// The flight route drawn on a surface chart (#799): magenta polyline, a dot
/// per waypoint, and the two endpoint ICAOs pushed outside the route's bounding
/// box — a port of the web's `renderRouteSvg` (briefing-ui.ts).
///
/// Waypoints arrive in the chart's *native* pixel space (`entry.nativeSize`);
/// the canvas scales them to whatever size it is laid out at. That is what
/// makes the downscaled DWD analysis work unchanged: same aspect ratio, so one
/// width ratio maps native pixels onto it. Lay this out exactly over the image
/// (`.overlay` on an aspect-fit image).
struct ChartRouteOverlay: View {
    let entry: ChartOverlayEntry

    var body: some View {
        Canvas { context, size in
            Self.draw(entry: entry, in: &context, size: size)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private static let magenta = Color(red: 1, green: 0, blue: 1)

    static func draw(entry: ChartOverlayEntry, in context: inout GraphicsContext, size: CGSize) {
        let wps = entry.waypoints
        guard !wps.isEmpty, size.width > 0, size.height > 0 else { return }
        let sx = size.width / entry.nativeWidth
        let sy = size.height / entry.nativeHeight
        let points = wps.map { CGPoint(x: $0.x * sx, y: $0.y * sy) }

        // Sizes follow the drawn width (as the web's follow the native width),
        // so the route reads the same on a phone-width chart and in the zoom
        // viewer, which lays the chart out at full pixel size.
        let w = size.width
        let lineWidth = max(2.5, w / 320)
        let dotR = max(2.5, w / 250)
        let labelFont = max(10, w / 35)
        let labelDistance = max(dotR * 5, w / 60)

        var line = Path()
        line.addLines(points)
        context.stroke(line, with: .color(magenta),
                       style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))

        for p in points {
            let dot = Path(ellipseIn: CGRect(x: p.x - dotR, y: p.y - dotR, width: dotR * 2, height: dotR * 2))
            context.fill(dot, with: .color(magenta))
            context.stroke(dot, with: .color(.white), lineWidth: max(1, dotR / 3))
        }

        // Endpoint labels pushed away from the bbox centre, so they sit past
        // the endpoint in empty space whatever the route's shape.
        let xs = points.map(\.x), ys = points.map(\.y)
        let cx = ((xs.min() ?? 0) + (xs.max() ?? 0)) / 2
        let cy = ((ys.min() ?? 0) + (ys.max() ?? 0)) / 2
        let endpoints: [(CGPoint, String)] = wps.count >= 2
            ? [(points[0], wps[0].icao), (points[points.count - 1], wps[wps.count - 1].icao)]
            : [(points[0], wps[0].icao)]

        for (p, icao) in endpoints {
            var dx = p.x - cx, dy = p.y - cy
            let mag = hypot(dx, dy)
            if mag == 0 { dx = 1; dy = 0 }
            let ux = mag > 0 ? dx / mag : 1
            let uy = mag > 0 ? dy / mag : 0
            let at = CGPoint(x: p.x + ux * labelDistance, y: p.y + uy * labelDistance)
            // Text extends away from the dot: anchor on the side facing it.
            let anchor = UnitPoint(x: ux > 0 ? 0 : (ux < 0 ? 1 : 0.5),
                                   y: uy > 0 ? 0 : (uy < 0 ? 1 : 0.5))
            let font = Font.system(size: labelFont, weight: .bold)
            let halo = context.resolve(Text(icao).font(font).foregroundStyle(.black))
            let halo2 = max(1, labelFont / 14)
            let haloOffsets: [(CGFloat, CGFloat)] = [(-halo2, 0), (halo2, 0), (0, -halo2), (0, halo2)]
            for (ox, oy) in haloOffsets {
                context.draw(halo, at: CGPoint(x: at.x + ox, y: at.y + oy), anchor: anchor)
            }
            context.draw(Text(icao).font(font).foregroundStyle(.white), at: at, anchor: anchor)
        }
    }
}
