import SwiftUI

/// Soft cloud bands: smooth band shape filled with a vertical gradient that
/// fades at top and bottom edges. GRAMET-style aesthetic. Two variants —
/// one for DD-derived layers, one for NWP layers.
struct SoftCloudBandsLayer: CrossSectionLayerProtocol {
    enum Source { case dd, nwp }

    let source: Source

    var id: String { source == .dd ? "soft-cloud-bands" : "soft-nwp-cloud-bands" }
    var name: String { source == .dd ? "Soft Clouds (DD)" : "Soft Clouds (NWP)" }
    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let getZones: (VizPoint) -> [VizCloudLayer] = { pt in
            self.source == .dd ? pt.cloudLayers : (pt.nwpCloudLayers ?? [])
        }

        // Skip rendering entirely if no zones — avoids stray overlay.
        guard data.points.contains(where: { getZones($0).isEmpty == false }) else { return }

        // Render via the generic matcher: matched pair → smooth band path,
        // then we fill the path with a vertical feathered gradient.
        renderMatchedZonesSoft(
            &context, transform: transform, data: data,
            getZones: getZones,
            ddFor: { cl in cl.meanDewpointDepressionC }
        )
    }
}

/// Specialised matcher that paints each band with a feathered vertical gradient
/// instead of a flat colour. Mirrors web's softCloudFill onBand callback.
private func renderMatchedZonesSoft(
    _ context: inout GraphicsContext,
    transform: CoordTransform,
    data: VizRouteData,
    getZones: (VizPoint) -> [VizCloudLayer],
    ddFor: (VizCloudLayer) -> Double?
) {
    let pts = data.points
    guard pts.count >= 2 else {
        if let p = pts.first {
            for z in getZones(p) {
                fillSoftCloudColumn(&context, transform: transform, distanceNm: p.distanceNm, baseFt: z.baseFt, topFt: z.topFt, coverage: z.coverage, dd: ddFor(z))
            }
        }
        return
    }

    for i in 0..<(pts.count - 1) {
        let curr = pts[i]
        let next = pts[i + 1]
        let currZones = getZones(curr)
        let nextZones = getZones(next)
        var usedNext = Set<Int>()

        for cz in currZones {
            var bestIdx = -1
            var bestOverlap: Double = 0
            for (j, nz) in nextZones.enumerated() {
                if usedNext.contains(j) { continue }
                let overlap = min(cz.topFt, nz.topFt) - max(cz.baseFt, nz.baseFt)
                if overlap > bestOverlap { bestOverlap = overlap; bestIdx = j }
            }

            if bestIdx >= 0 {
                let nz = nextZones[bestIdx]
                usedNext.insert(bestIdx)
                let bandPoints = [
                    BandPoint(distanceNm: curr.distanceNm, baseFt: cz.baseFt, topFt: cz.topFt),
                    BandPoint(distanceNm: next.distanceNm, baseFt: nz.baseFt, topFt: nz.topFt),
                ]
                let avgDD = combinedDD(ddFor(cz), ddFor(nz))
                fillSoftCloudBand(&context, points: bandPoints, transform: transform, coverage: cz.coverage, dd: avgDD)
            } else {
                let midDist = (curr.distanceNm + next.distanceNm) / 2
                let midAlt = (cz.baseFt + cz.topFt) / 2
                let bandPoints = [
                    BandPoint(distanceNm: curr.distanceNm, baseFt: cz.baseFt, topFt: cz.topFt),
                    BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                ]
                fillSoftCloudBand(&context, points: bandPoints, transform: transform, coverage: cz.coverage, dd: ddFor(cz))
            }
        }

        for (j, nz) in nextZones.enumerated() {
            if usedNext.contains(j) { continue }
            let midDist = (curr.distanceNm + next.distanceNm) / 2
            let midAlt = (nz.baseFt + nz.topFt) / 2
            let bandPoints = [
                BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                BandPoint(distanceNm: next.distanceNm, baseFt: nz.baseFt, topFt: nz.topFt),
            ]
            fillSoftCloudBand(&context, points: bandPoints, transform: transform, coverage: nz.coverage, dd: ddFor(nz))
        }
    }
}

private func combinedDD(_ a: Double?, _ b: Double?) -> Double? {
    if let av = a, let bv = b { return (av + bv) / 2 }
    return a ?? b
}

private func fillSoftCloudBand(
    _ context: inout GraphicsContext,
    points: [BandPoint],
    transform: CoordTransform,
    coverage: String,
    dd: Double?
) {
    guard points.count >= 2 else { return }
    let path = buildSmoothBandPath(points, transform: transform)

    // Compute the band's vertical extent in pixels for gradient stop placement.
    let topYs = points.map { transform.altitudeToY($0.topFt) }
    let baseYs = points.map { transform.altitudeToY($0.baseFt) }
    let minY = min(topYs.min() ?? 0, baseYs.min() ?? 0)
    let maxY = max(topYs.max() ?? 0, baseYs.max() ?? 0)
    let bandHeight = maxY - minY
    guard bandHeight > 0 else { return }

    // Coverage-driven alpha at band centre, modulated by DD (denser cloud → darker).
    var alpha = ColorScales.softCloudCenterAlpha(coverage)
    if let dd = dd {
        let ddFactor = max(0.3, 1.0 - dd / 4.0)
        alpha *= ddFactor
    }

    let (r, g, b) = ColorScales.softCloudFillRGB
    let solid = Color(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    let clear = Color(.sRGB, red: r, green: g, blue: b, opacity: 0)

    // Feathered top/bottom — top 15%, bottom 15% fade.
    let feather = ColorScales.softCloudFeatherFraction
    let topStop = min(feather, 0.3)
    let botStop = max(1.0 - feather, 0.7)

    let gradient = Gradient(stops: [
        .init(color: clear, location: 0),
        .init(color: solid, location: topStop),
        .init(color: solid, location: botStop),
        .init(color: clear, location: 1),
    ])

    context.fill(
        path,
        with: .linearGradient(
            gradient,
            startPoint: CGPoint(x: 0, y: minY),
            endPoint: CGPoint(x: 0, y: maxY)
        )
    )
}

private func fillSoftCloudColumn(
    _ context: inout GraphicsContext,
    transform: CoordTransform,
    distanceNm: Double,
    baseFt: Double,
    topFt: Double,
    coverage: String,
    dd: Double?
) {
    let x = transform.distanceToX(distanceNm)
    let yTop = transform.altitudeToY(topFt)
    let yBase = transform.altitudeToY(baseFt)
    let rect = CGRect(x: x - 10, y: yTop, width: 20, height: yBase - yTop)
    let path = Path(rect)

    var alpha = ColorScales.softCloudCenterAlpha(coverage)
    if let dd = dd {
        alpha *= max(0.3, 1.0 - dd / 4.0)
    }

    let (r, g, b) = ColorScales.softCloudFillRGB
    let solid = Color(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    let clear = Color(.sRGB, red: r, green: g, blue: b, opacity: 0)
    let gradient = Gradient(stops: [
        .init(color: clear, location: 0),
        .init(color: solid, location: ColorScales.softCloudFeatherFraction),
        .init(color: solid, location: 1 - ColorScales.softCloudFeatherFraction),
        .init(color: clear, location: 1),
    ])

    context.fill(
        path,
        with: .linearGradient(
            gradient,
            startPoint: CGPoint(x: 0, y: yTop),
            endPoint: CGPoint(x: 0, y: yBase)
        )
    )
}
