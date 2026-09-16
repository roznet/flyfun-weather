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

    /// dBZ → strip colour, on the NWS VIP levels.
    ///
    /// Mirrors `_DBZ_STOPS` in `observed/imagery.py` stop for stop, so the map
    /// overlay and the cross-section strip cannot disagree about what a given
    /// reflectivity looks like. Keep the lists in step — the 65 dBZ magenta
    /// was once missing from the web layer while the server had it, so the most
    /// intense echo on the map rendered as ordinary red on the cross-section, the
    /// one case where the difference matters most. The server builds its copy
    /// from the intensity bands in `observed/intensity.py`, the source of truth
    /// for all three; `observed-surface.ts` carries the web copy.
    ///
    /// Breaks are the VIP boundaries rather than round numbers: a pilot's own
    /// airborne radar has been red since 40 dBZ and magenta since 50, so the
    /// previous 45/55 ramp read one notch optimistic against the box in the
    /// panel. Below 18 dBZ is below VIP 1 — drawn, but in the unclassified blue.
    static func echoColor(_ dbz: Double) -> Color {
        if dbz >= 50 { return Color(red: 0.745, green: 0.235, blue: 0.745) }  // #be3cbe VIP 5-6 extreme
        if dbz >= 46 { return Color(red: 0.882, green: 0.235, blue: 0.235) }  // #e13c3c VIP 4 very heavy
        if dbz >= 41 { return Color(red: 0.941, green: 0.549, blue: 0.157) }  // #f08c28 VIP 3 heavy
        if dbz >= 30 { return Color(red: 0.941, green: 0.824, blue: 0.235) }  // #f0d23c VIP 2 moderate
        if dbz >= 18 { return Color(red: 0.235, green: 0.745, blue: 0.353) }  // #3cbe5a VIP 1 light
        return Color(red: 0.353, green: 0.627, blue: 0.863)                   // #5aa0dc below the scale
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

        // Radar and lightning frames are minutes apart and neither is an instant;
        // whichever is on screen says so for itself — including which one it is.
        // The label must come from the source actually supplying the timestamp:
        // on a briefing where OPERA is down but lightning is up, a hardcoded
        // "Radar" would stamp a radar name on a lightning frame's age, which is
        // exactly the per-source blending this layer exists to avoid.
        guard let source = observed.reflectivity ?? observed.lightning else { return }
        // Second row whenever the cloud-top layer is also drawing a badge, so
        // neither source's age is hidden behind the other.
        let row = observed.cloudTops != nil ? 1 : 0
        ObservedBadge.draw(
            &context, transform: transform,
            text: ObservedBadge.ageText(source.validTime, source.ageMinutes, source.label),
            row: row
        )
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
            return
        }
        guard let dbz = point.dbz else { return }

        context.fill(
            Path(CGRect(x: x0, y: yBase - Self.stripHeightPx, width: width, height: Self.stripHeightPx)),
            with: .color(Self.echoColor(dbz).opacity(0.75))
        )
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
