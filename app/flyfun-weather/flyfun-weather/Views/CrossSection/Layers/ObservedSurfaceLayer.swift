import SwiftUI

/// Observed radar echo and lightning at the surface (#574) — group `conditions`,
/// default OFF.
///
/// SYNC — port of web/ts/visualization/cross-section/layers/observed-surface.ts.
///
/// Reflectivity is drawn as a colour strip along the terrain rather than as a
/// vertical extent, because the composite is a 2-D surface product: it says
/// *there is an echo here*, not how tall it is. Drawing it with height would
/// invent structure the data does not contain — the cloud-top layer is where
/// vertical information legitimately comes from.
///
/// Lightning is drawn as tick marks whose count reflects flash density in the
/// selected corridor, above the echo strip.
struct ObservedSurfaceLayer: CrossSectionLayerProtocol {
    let id = "observed-surface"
    let name = "Observed radar & lightning"
    let group: LayerGroup = .conditions

    private static let stripHeightPx: CGFloat = 10
    private static let markHalfWidthNm: Double = 4
    private static let flashTickHeightPx: CGFloat = 9
    private static let maxFlashTicks = 4

    /// dBZ → strip colour.
    ///
    /// Mirrors `_DBZ_STOPS` in `observed/imagery.py` stop for stop, so the map
    /// overlay and the cross-section strip cannot disagree about what a given
    /// reflectivity looks like. Keep the two lists in step — the 65 dBZ magenta
    /// was once missing from the web layer while the server had it, so the most
    /// intense echo on the map rendered as ordinary red on the cross-section, the
    /// one case where the difference matters most.
    static func echoColor(_ dbz: Double) -> Color {
        if dbz >= 65 { return Color(red: 0.745, green: 0.235, blue: 0.745) }  // #be3cbe
        if dbz >= 55 { return Color(red: 0.882, green: 0.235, blue: 0.235) }  // #e13c3c
        if dbz >= 45 { return Color(red: 0.941, green: 0.549, blue: 0.157) }  // #f08c28
        if dbz >= 35 { return Color(red: 0.941, green: 0.824, blue: 0.235) }  // #f0d23c
        if dbz >= 20 { return Color(red: 0.235, green: 0.745, blue: 0.353) }  // #3cbe5a
        return Color(red: 0.353, green: 0.627, blue: 0.863)                   // #5aa0dc
    }

    /// How many flash ticks to draw for a disc's flash count.
    static func flashTickCount(_ flashCount: Int) -> Int {
        guard flashCount > 0 else { return 0 }
        return min(maxFlashTicks, 1 + Int(log2(Double(flashCount))))
    }

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard let observed = data.observed else { return }
        guard observed.reflectivity != nil || observed.lightning != nil else { return }

        for point in observed.points {
            drawEcho(point, context: &context, transform: transform, terrain: data.terrainProfile)
            drawFlashes(point, context: &context, transform: transform, terrain: data.terrainProfile)
        }

        // Each source's age/window renders in the independent clock overlay.
    }

    /// Terrain elevation nearest a route distance; 0 when the pack carries no
    /// elevation profile (the strip then hugs the axis, which is where sea level
    /// is anyway).
    private func terrainAt(_ terrain: [TerrainPoint]?, _ distanceNm: Double) -> Double {
        guard let terrain, let first = terrain.first else { return 0 }
        var best = first
        for p in terrain
        where abs(p.distanceNm - distanceNm) < abs(best.distanceNm - distanceNm) { best = p }
        return best.elevationFt
    }

    private func drawEcho(
        _ point: VizObservedPoint, context: inout GraphicsContext,
        transform: CoordTransform, terrain: [TerrainPoint]?
    ) {
        let x0 = transform.distanceToX(point.distanceNm - Self.markHalfWidthNm)
        let x1 = transform.distanceToX(point.distanceNm + Self.markHalfWidthNm)
        let width = max(2, x1 - x0)
        let yBase = transform.altitudeToY(terrainAt(terrain, point.distanceNm))

        if let dbz = point.dbz {
            context.fill(
                Path(CGRect(x: x0, y: yBase - Self.stripHeightPx, width: width, height: Self.stripHeightPx)),
                with: .color(Self.echoColor(dbz).opacity(0.75))
            )
        }
        if point.radarNoCoverage {
            // A hatched strip: the radar does not see here. Distinct from a blank
            // strip, which is the radar looking and finding nothing.
            //
            // DEVIATION from observed-surface.ts, which hardcodes one gray: this
            // takes the theme's `noCoverageColor`, so "the sensor does not look
            // here" is the same colour in both observed layers and stays legible
            // on the Light theme's white sky, where the fixed gray is weak.
            var hatch = Path()
            var offset: CGFloat = 0
            while offset < width {
                hatch.move(to: CGPoint(x: x0 + offset, y: yBase))
                hatch.addLine(to: CGPoint(x: x0 + offset + 3, y: yBase - Self.stripHeightPx))
                offset += 4
            }
            context.stroke(
                hatch, with: .color(CrossSectionTheme.active.observed.noCoverageColor),
                lineWidth: 1)
        }
    }

    private func drawFlashes(
        _ point: VizObservedPoint, context: inout GraphicsContext,
        transform: CoordTransform, terrain: [TerrainPoint]?
    ) {
        let ticks = Self.flashTickCount(point.flashCount)
        guard ticks > 0 else { return }
        let cx = transform.distanceToX(point.distanceNm)
        let yTop = transform.altitudeToY(terrainAt(terrain, point.distanceNm))
            - Self.stripHeightPx - 3

        var path = Path()
        for i in 0..<ticks {
            let x = cx + (CGFloat(i) - CGFloat(ticks - 1) / 2) * 3
            path.move(to: CGPoint(x: x, y: yTop))
            path.addLine(to: CGPoint(x: x, y: yTop - Self.flashTickHeightPx))
        }
        context.stroke(path, with: .color(Color(red: 0.486, green: 0.227, blue: 0.929)), lineWidth: 1.6)
    }
}
