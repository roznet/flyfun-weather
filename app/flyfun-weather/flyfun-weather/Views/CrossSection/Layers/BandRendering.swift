import SwiftUI

// MARK: - Monotone cubic spline (Fritsch-Carlson)

/// Computes tangent slopes for monotone cubic interpolation between (xs, ys).
/// Port of base.ts:monotoneCubicTangents.
func monotoneCubicTangents(xs: [CGFloat], ys: [CGFloat]) -> [CGFloat] {
    let n = xs.count
    guard n >= 2 else { return Array(repeating: 0, count: n) }

    var deltas = [CGFloat](repeating: 0, count: n - 1)
    for i in 0..<(n - 1) {
        let dx = xs[i + 1] - xs[i]
        deltas[i] = dx != 0 ? (ys[i + 1] - ys[i]) / dx : 0
    }

    var tangents = [CGFloat](repeating: 0, count: n)
    tangents[0] = deltas[0]
    tangents[n - 1] = deltas[n - 2]
    for i in 1..<(n - 1) {
        if deltas[i - 1] * deltas[i] <= 0 {
            tangents[i] = 0
        } else {
            tangents[i] = (deltas[i - 1] + deltas[i]) / 2
        }
    }

    for i in 0..<(n - 1) {
        if deltas[i] == 0 {
            tangents[i] = 0
            tangents[i + 1] = 0
        } else {
            let alpha = tangents[i] / deltas[i]
            let beta = tangents[i + 1] / deltas[i]
            let tau = alpha * alpha + beta * beta
            if tau > 9 {
                let s = 3.0 / sqrt(tau)
                tangents[i] = s * alpha * deltas[i]
                tangents[i + 1] = s * beta * deltas[i]
            }
        }
    }

    return tangents
}

// MARK: - Smooth band rendering

/// One altitude column (top/base) at a route distance, used to build a band path.
struct BandPoint {
    let distanceNm: Double
    let baseFt: Double
    let topFt: Double
}

/// Build a closed smooth-band path: cubic top curve L→R, then cubic base curve R→L.
/// Port of base.ts:buildBandPath.
func buildSmoothBandPath(_ points: [BandPoint], transform: CoordTransform) -> Path {
    var path = Path()
    guard points.count >= 2 else { return path }

    let xs = points.map { transform.distanceToX($0.distanceNm) }
    let topYs = points.map { transform.altitudeToY($0.topFt) }
    let baseYs = points.map { transform.altitudeToY($0.baseFt) }

    let topTangents = monotoneCubicTangents(xs: xs, ys: topYs)
    let baseTangents = monotoneCubicTangents(xs: xs, ys: baseYs)

    // Top curve, left → right.
    path.move(to: CGPoint(x: xs[0], y: topYs[0]))
    for i in 0..<(points.count - 1) {
        let dx = xs[i + 1] - xs[i]
        let cp1 = CGPoint(x: xs[i] + dx / 3, y: topYs[i] + topTangents[i] * dx / 3)
        let cp2 = CGPoint(x: xs[i + 1] - dx / 3, y: topYs[i + 1] - topTangents[i + 1] * dx / 3)
        path.addCurve(to: CGPoint(x: xs[i + 1], y: topYs[i + 1]), control1: cp1, control2: cp2)
    }

    // Drop down to the base on the right edge.
    let last = points.count - 1
    path.addLine(to: CGPoint(x: xs[last], y: baseYs[last]))

    // Base curve, right → left. dx is negative; same cubic formulation traces the
    // curve backwards correctly (matches base.ts).
    for i in stride(from: points.count - 2, through: 0, by: -1) {
        let dx = xs[i] - xs[i + 1]
        let cp1 = CGPoint(x: xs[i + 1] + dx / 3, y: baseYs[i + 1] + baseTangents[i + 1] * dx / 3)
        let cp2 = CGPoint(x: xs[i] - dx / 3, y: baseYs[i] - baseTangents[i] * dx / 3)
        path.addCurve(to: CGPoint(x: xs[i], y: baseYs[i]), control1: cp1, control2: cp2)
    }

    path.closeSubpath()
    return path
}

/// Fill a smooth band between two cubic curves. Single-point input falls back to a column.
func drawSmoothBand(
    _ context: inout GraphicsContext,
    points: [BandPoint],
    transform: CoordTransform,
    color: Color
) {
    if points.count >= 2 {
        let path = buildSmoothBandPath(points, transform: transform)
        context.fill(path, with: .color(color))
        return
    }
    // Single-point fallback: 20pt-wide column at the point.
    if let p = points.first {
        let x = transform.distanceToX(p.distanceNm)
        let yTop = transform.altitudeToY(p.topFt)
        let yBase = transform.altitudeToY(p.baseFt)
        let rect = CGRect(x: x - 10, y: yTop, width: 20, height: yBase - yTop)
        context.fill(Path(rect), with: .color(color))
    }
}

// MARK: - Generic zone matcher

/// Minimum interface for a zone that can be matched by altitude overlap between adjacent route points.
protocol MatchableZone {
    var baseFt: Double { get }
    var topFt: Double { get }
}

extension VizCloudLayer: MatchableZone {}
extension VizIcingZone: MatchableZone {}
extension VizCATLayer: MatchableZone {}
extension VizInversionLayer: MatchableZone {}

/// Render zones as smooth bands by matching them across adjacent route points by altitude overlap.
/// - Matched pair → single smooth band between the two altitudes.
/// - Unmatched zone → tapered band that collapses to the midpoint between the two route points.
///
/// Port of zone-matching.ts:renderMatchedZones.
func renderMatchedZones<Z: MatchableZone>(
    _ context: inout GraphicsContext,
    transform: CoordTransform,
    data: VizRouteData,
    getZones: (VizPoint) -> [Z],
    getColor: (Z, Z?) -> Color
) {
    let pts = data.points

    // Single-point fallback: column per zone.
    if pts.count == 1 {
        let p = pts[0]
        for z in getZones(p) {
            drawSmoothBand(
                &context,
                points: [BandPoint(distanceNm: p.distanceNm, baseFt: z.baseFt, topFt: z.topFt)],
                transform: transform,
                color: getColor(z, nil)
            )
        }
        return
    }

    guard pts.count >= 2 else { return }

    for i in 0..<(pts.count - 1) {
        let curr = pts[i]
        let next = pts[i + 1]
        let currZones = getZones(curr)
        let nextZones = getZones(next)
        var usedNext = Set<Int>()

        for cz in currZones {
            // Best-overlap match in the next point.
            var bestIdx = -1
            var bestOverlap: Double = 0
            for (j, nz) in nextZones.enumerated() {
                if usedNext.contains(j) { continue }
                let overlap = min(cz.topFt, nz.topFt) - max(cz.baseFt, nz.baseFt)
                if overlap > bestOverlap {
                    bestOverlap = overlap
                    bestIdx = j
                }
            }

            if bestIdx >= 0 {
                let nz = nextZones[bestIdx]
                usedNext.insert(bestIdx)
                let bandPoints = [
                    BandPoint(distanceNm: curr.distanceNm, baseFt: cz.baseFt, topFt: cz.topFt),
                    BandPoint(distanceNm: next.distanceNm, baseFt: nz.baseFt, topFt: nz.topFt),
                ]
                drawSmoothBand(&context, points: bandPoints, transform: transform, color: getColor(cz, nz))
            } else {
                // Taper to midpoint.
                let midDist = (curr.distanceNm + next.distanceNm) / 2
                let midAlt = (cz.baseFt + cz.topFt) / 2
                let bandPoints = [
                    BandPoint(distanceNm: curr.distanceNm, baseFt: cz.baseFt, topFt: cz.topFt),
                    BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                ]
                drawSmoothBand(&context, points: bandPoints, transform: transform, color: getColor(cz, nil))
            }
        }

        // Unmatched zones in the next point: emerge from midpoint.
        for (j, nz) in nextZones.enumerated() {
            if usedNext.contains(j) { continue }
            let midDist = (curr.distanceNm + next.distanceNm) / 2
            let midAlt = (nz.baseFt + nz.topFt) / 2
            let bandPoints = [
                BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                BandPoint(distanceNm: next.distanceNm, baseFt: nz.baseFt, topFt: nz.topFt),
            ]
            drawSmoothBand(&context, points: bandPoints, transform: transform, color: getColor(nz, nil))
        }
    }
}

// MARK: - Risk severity ordering

private let RISK_RANK: [String: Int] = ["none": 0, "light": 1, "moderate": 2, "severe": 3]

/// Return the higher of two risk levels — used to colour matched-zone bands by their worst end.
/// Port of zone-matching.ts:maxRisk.
func maxRisk(_ a: String, _ b: String) -> String {
    return RISK_RANK[a, default: 0] >= RISK_RANK[b, default: 0] ? a : b
}
