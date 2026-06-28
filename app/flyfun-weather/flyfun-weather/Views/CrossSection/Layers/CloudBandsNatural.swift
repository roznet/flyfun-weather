import SwiftUI

/// Natural + square cloud-band rendering — a faithful port of the web
/// `cloud-bands-factory.ts` (orthogonal source × style axes).
///
/// - **natural**: each band is painted as a row of overlapping soft elliptical
///   blobs (a deterministic per-slot hash decides fill/gap and jitter), giving
///   the puffy GRAMET-improved look. Coverage is encoded via the source colour
///   /alpha, not horizontal sparseness (matches `DEFAULT_NATURAL_CONFIG`).
/// - **square**: solid straight-edged cells (ForeFlight-like), same continuous
///   colour scale as natural.
///
/// Layer IDs match the web (`cloud-bands`, `nwp-cloud-bands` for natural;
/// `square-cloud-bands`, `square-nwp-cloud-bands` for square) so persisted
/// prefs/presets stay compatible.

// MARK: - Source axis

enum CloudBandSource {
    case dd, nwp

    func zones(_ p: VizPoint) -> [VizCloudLayer] {
        switch self {
        case .dd: return p.cloudLayers
        case .nwp: return p.nwpCloudLayers ?? []
        }
    }

    /// Continuous fill colour for a (possibly matched) zone — the same colour
    /// the natural and square styles share.
    func matchedRGBA(_ cl: VizCloudLayer, _ matched: VizCloudLayer?) -> RGBA {
        switch self {
        case .dd:
            return ColorScales.cloudRGBA(dewpointDepressionC: avgDD(cl, matched), coverage: cl.coverage)
        case .nwp:
            // Prefer the granular model cloud fraction; fall back to the 4-bucket
            // coverage category — matches web `NWP_SOURCE.matchedColor`.
            let covA = cl.meanCloudCoverPct ?? ColorScales.coverageToPct(cl.coverage)
            let covB = matched.map { $0.meanCloudCoverPct ?? ColorScales.coverageToPct($0.coverage) } ?? covA
            return ColorScales.nwpCloudRGBA(pct: (covA + covB) / 2)
        }
    }

    /// Per-band salt so DD and NWP bands at the same base altitude don't draw
    /// identical puff/gap patterns (would look artificially correlated).
    var salt: UInt32 { self == .nwp ? 0xdeadbeef : 0x0 }
}

private func avgDD(_ a: VizCloudLayer, _ b: VizCloudLayer?) -> Double? {
    guard let b else { return a.meanDewpointDepressionC }
    if let av = a.meanDewpointDepressionC, let bv = b.meanDewpointDepressionC { return (av + bv) / 2 }
    return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC
}

// MARK: - Natural cloud config (shared spec — mirrors DEFAULT_NATURAL_CONFIG)

/// Rendering knobs for the natural style. Values mirror the web
/// `DEFAULT_NATURAL_CONFIG` so clouds look identical across web/iOS; only the
/// Canvas drawing is per-client.
struct NaturalCloudConfig: Sendable {
    var fillFew = 1.0
    var fillSct = 1.0
    var fillBkn = 1.0
    var fillOvc = 1.0
    var minFillFraction = 1.0
    /// Fixed alpha override applied to the source colour; nil keeps the
    /// source's coverage-modulated alpha (coverage lives in colour, not density).
    var fillAlpha: Double? = nil
    var blobSpacingPx: Double = 28
    var blobRadiusXPx: Double = 36
    var blobHeightFraction: Double = 0.6
    var blobJitterX: Double = 0.4
    var blobJitterY: Double = 0.15
    var blobSizeVariation: Double = 0.5
    var coreFraction: Double = 0.3
    var subBlobsPerSlot: Int = 3
    var subBlobOffsetFraction: Double = 0.35
    var subBlobSizeFraction: Double = 0.65
    var subBlobSizeJitter: Double = 0.25
    var radiusCoverageScaleFloor: Double = 1.0

    static let `default` = NaturalCloudConfig()

    func fillFraction(forCoverage coverage: String) -> Double {
        switch coverage.uppercased() {
        case "FEW": return fillFew
        case "SCT": return fillSct
        case "BKN": return fillBkn
        case "OVC": return fillOvc
        default: return fillBkn   // unknown coverage → treat as BKN
        }
    }
}

/// Cheap, deterministic hash → [0, 1). Bit-for-bit port of web `hash01`
/// (UInt32 wrapping arithmetic reproduces JS `Math.imul` / `>>>`).
private func hash01(_ n: UInt32) -> Double {
    var h = n &+ 0x9e37_79b9
    h = (h ^ (h >> 16)) &* 0x85eb_ca6b
    h = (h ^ (h >> 13)) &* 0xc2b2_ae35
    h = h ^ (h >> 16)
    return Double(h) / 4_294_967_296.0
}

/// `hash01(s * mul + seed)` with JS 32-bit truncation semantics.
private func slotHash(_ s: Int, _ mul: Int, _ seed: UInt32) -> Double {
    hash01(UInt32(truncatingIfNeeded: s &* mul) &+ seed)
}

// MARK: - Natural blob drawing

/// One soft elliptical blob via a scaled radial gradient: core at full alpha,
/// fading to transparent at the outer radius. Port of web `drawSoftBlob`.
private func drawSoftBlob(
    _ context: inout GraphicsContext,
    cx: CGFloat, cy: CGFloat, rx: CGFloat, ry: CGFloat,
    color: RGBA, coreFraction: Double
) {
    guard rx > 0, ry > 0 else { return }
    var sub = context
    sub.translateBy(x: cx, y: cy)
    sub.scaleBy(x: 1, y: ry / rx)   // circle of radius rx → ellipse ry tall
    let gradient = Gradient(stops: [
        .init(color: color.color, location: 0),
        .init(color: color.color, location: coreFraction),
        .init(color: color.clear, location: 1),
    ])
    sub.fill(
        Path(CGRect(x: -rx, y: -rx, width: 2 * rx, height: 2 * rx)),
        with: .radialGradient(gradient, center: .zero, startRadius: 0, endRadius: rx)
    )
}

/// Paint a cloud band as overlapping soft blobs inside the linearly-interpolated
/// envelope between the left/right band columns. Port of `drawNaturalCloudBand`.
private func drawNaturalCloudBand(
    _ context: inout GraphicsContext,
    xL: CGFloat, xR: CGFloat,
    baseAt: (CGFloat) -> CGFloat, topAt: (CGFloat) -> CGFloat,
    color: RGBA, fillFraction: Double, seed: UInt32, config: NaturalCloudConfig
) {
    guard xR > xL else { return }
    let slotW = config.blobSpacingPx
    let slotStart = Int((Double(xL) / slotW).rounded(.down)) - 1
    let slotEnd = Int((Double(xR) / slotW).rounded(.up)) + 1

    let floor = config.radiusCoverageScaleFloor
    let radiusScale = floor + (1 - floor) * max(0, min(1, fillFraction))

    var s = slotStart
    while s < slotEnd {
        defer { s += 1 }
        if slotHash(s, 0x1f_1f1f, seed) > fillFraction { continue }

        let xJ = (slotHash(s, 0x2f_3f4f, seed) - 0.5) * slotW * config.blobJitterX
        let cx = (Double(s) + 0.5) * slotW + xJ
        // Ownership rule: a slot is drawn by exactly one segment.
        if cx < Double(xL) || cx >= Double(xR) { continue }

        let cxF = CGFloat(cx)
        let yTop = topAt(cxF)
        let yBase = baseAt(cxF)
        let bandH = Double(yBase - yTop)
        if bandH <= 0 { continue }

        let yJ = (slotHash(s, 0x5b_6c79, seed) - 0.5) * bandH * config.blobJitterY
        let cy = (Double(yTop) + Double(yBase)) / 2 + yJ

        let sizeJ = 1 + (slotHash(s, 0x7e_8f91, seed) - 0.5) * config.blobSizeVariation
        let rx = config.blobRadiusXPx * sizeJ * radiusScale
        let ry = bandH * config.blobHeightFraction * sizeJ * radiusScale

        let n = max(1, config.subBlobsPerSlot)
        let aspect = rx > 0 ? ry / rx : 1
        for i in 0..<n {
            let subSeed = UInt32(truncatingIfNeeded: s &* 0x9e37_79b1)
                ^ UInt32(truncatingIfNeeded: i &* 0x85eb_ca6b)
                ^ seed
            let angle = hash01(subSeed) * 2 * .pi
            let offsetMag = hash01(subSeed ^ 0x12345) * config.subBlobOffsetFraction * rx
            let ox = cos(angle) * offsetMag
            let oy = sin(angle) * offsetMag * aspect
            let subSize = config.subBlobSizeFraction
                + (hash01(subSeed ^ 0x67890) - 0.5) * config.subBlobSizeJitter
            drawSoftBlob(
                &context,
                cx: cxF + CGFloat(ox), cy: CGFloat(cy + oy),
                rx: CGFloat(rx * subSize), ry: CGFloat(ry * subSize),
                color: color, coreFraction: config.coreFraction
            )
        }
    }
}

private func naturalFillFraction(_ cl: VizCloudLayer, _ matched: VizCloudLayer?, _ config: NaturalCloudConfig) -> Double {
    // Prefer the granular cloud cover% when present (NWP); else the coverage
    // bucket. Mirrors web `naturalFillFraction`.
    if let covA = cl.meanCloudCoverPct {
        let pct = matched?.meanCloudCoverPct.map { (covA + $0) / 2 } ?? covA
        return max(config.minFillFraction, min(1.0, pct / 100))
    }
    let a = config.fillFraction(forCoverage: cl.coverage)
    let b = matched.map { config.fillFraction(forCoverage: $0.coverage) } ?? a
    return max(config.minFillFraction, (a + b) / 2)
}

// MARK: - Matched-band iteration (mirrors zone-matching onBand callback)

private struct CloudBand {
    let left: BandPoint
    let right: BandPoint
    let cl: VizCloudLayer
    let matched: VizCloudLayer?
}

/// Match cloud zones across adjacent route points by altitude overlap, yielding
/// a band per match. Unmatched zones taper to the segment midpoint. Mirrors the
/// soft layer's matcher and web `renderMatchedZones`.
private func forEachCloudBand(_ data: VizRouteData, source: CloudBandSource, _ body: (CloudBand) -> Void) {
    let pts = data.points
    guard pts.count >= 2 else { return }

    for i in 0..<(pts.count - 1) {
        let curr = pts[i]
        let next = pts[i + 1]
        let currZones = source.zones(curr)
        let nextZones = source.zones(next)
        var usedNext = Set<Int>()

        for cz in currZones {
            var bestIdx = -1
            var bestOverlap: Double = 0
            for (j, nz) in nextZones.enumerated() where !usedNext.contains(j) {
                let overlap = min(cz.topFt, nz.topFt) - max(cz.baseFt, nz.baseFt)
                if overlap > bestOverlap { bestOverlap = overlap; bestIdx = j }
            }
            if bestIdx >= 0 {
                let nz = nextZones[bestIdx]
                usedNext.insert(bestIdx)
                body(CloudBand(
                    left: BandPoint(distanceNm: curr.distanceNm, baseFt: cz.baseFt, topFt: cz.topFt),
                    right: BandPoint(distanceNm: next.distanceNm, baseFt: nz.baseFt, topFt: nz.topFt),
                    cl: cz, matched: nz
                ))
            } else {
                let midDist = (curr.distanceNm + next.distanceNm) / 2
                let midAlt = (cz.baseFt + cz.topFt) / 2
                body(CloudBand(
                    left: BandPoint(distanceNm: curr.distanceNm, baseFt: cz.baseFt, topFt: cz.topFt),
                    right: BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                    cl: cz, matched: nil
                ))
            }
        }

        for (j, nz) in nextZones.enumerated() where !usedNext.contains(j) {
            let midDist = (curr.distanceNm + next.distanceNm) / 2
            let midAlt = (nz.baseFt + nz.topFt) / 2
            body(CloudBand(
                left: BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                right: BandPoint(distanceNm: next.distanceNm, baseFt: nz.baseFt, topFt: nz.topFt),
                cl: nz, matched: nil
            ))
        }
    }
}

private func bandSeed(_ cl: VizCloudLayer, _ source: CloudBandSource) -> UInt32 {
    // Round baseFt to 100ft so small boundary drift doesn't reshuffle the pattern.
    let rounded = UInt32(truncatingIfNeeded: Int((cl.baseFt / 100).rounded()))
    return (rounded ^ 0x4d3_6e96) ^ source.salt
}

// MARK: - Natural layer

struct NaturalCloudBandsLayer: CrossSectionLayerProtocol {
    let source: CloudBandSource
    private let config = NaturalCloudConfig.default

    var id: String { source == .dd ? "cloud-bands" : "nwp-cloud-bands" }
    var name: String { source == .dd ? "Clouds (DD)" : "Clouds (NWP)" }
    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard data.points.contains(where: { source.zones($0).isEmpty == false }) else { return }

        // Single-point fallback: a column per zone (no horizontal span for puffs).
        if data.points.count == 1, let p = data.points.first {
            for cl in source.zones(p) {
                fillColumn(&context, transform: transform, distanceNm: p.distanceNm,
                           baseFt: cl.baseFt, topFt: cl.topFt, color: source.matchedRGBA(cl, nil).color)
            }
            return
        }

        forEachCloudBand(data, source: source) { band in
            let xL = transform.distanceToX(band.left.distanceNm)
            let xR = transform.distanceToX(band.right.distanceNm)
            guard xR > xL else { return }
            let segW = xR - xL
            let yBaseL = transform.altitudeToY(band.left.baseFt)
            let yBaseR = transform.altitudeToY(band.right.baseFt)
            let yTopL = transform.altitudeToY(band.left.topFt)
            let yTopR = transform.altitudeToY(band.right.topFt)

            let baseAt: (CGFloat) -> CGFloat = { x in yBaseL + (yBaseR - yBaseL) * (x - xL) / segW }
            let topAt: (CGFloat) -> CGFloat = { x in yTopL + (yTopR - yTopL) * (x - xL) / segW }

            var fill = source.matchedRGBA(band.cl, band.matched)
            if let override = config.fillAlpha { fill = RGBA(r: fill.r, g: fill.g, b: fill.b, a: override) }
            let frac = naturalFillFraction(band.cl, band.matched, config)

            drawNaturalCloudBand(&context, xL: xL, xR: xR, baseAt: baseAt, topAt: topAt,
                                 color: fill, fillFraction: frac, seed: bandSeed(band.cl, source), config: config)
        }
    }
}

// MARK: - Square layer

struct SquareCloudBandsLayer: CrossSectionLayerProtocol {
    let source: CloudBandSource

    var id: String { source == .dd ? "square-cloud-bands" : "square-nwp-cloud-bands" }
    var name: String { source == .dd ? "Square Clouds (DD)" : "Square Clouds (NWP)" }
    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard data.points.contains(where: { source.zones($0).isEmpty == false }) else { return }

        if data.points.count == 1, let p = data.points.first {
            for cl in source.zones(p) {
                fillColumn(&context, transform: transform, distanceNm: p.distanceNm,
                           baseFt: cl.baseFt, topFt: cl.topFt, color: source.matchedRGBA(cl, nil).color)
            }
            return
        }

        forEachCloudBand(data, source: source) { band in
            // Flat rectangular cells (ForeFlight-like): each endpoint contributes
            // a flat cell at its own base/top, split at the segment midpoint — so
            // adjacent cells step vertically instead of sloping. A single sloped
            // trapezoid read as a soft/smooth band; this mirrors web
            // drawColumnBand's per-point fillRect. (#7)
            let xL = transform.distanceToX(band.left.distanceNm)
            let xR = transform.distanceToX(band.right.distanceNm)
            let xMid = (xL + xR) / 2
            let rgba = source.matchedRGBA(band.cl, band.matched)
            fillFlatCell(&context, x0: xL, x1: xMid, baseFt: band.left.baseFt, topFt: band.left.topFt,
                         transform: transform, fill: rgba)
            fillFlatCell(&context, x0: xMid, x1: xR, baseFt: band.right.baseFt, topFt: band.right.topFt,
                         transform: transform, fill: rgba)
        }
    }
}

/// One flat (horizontal top/base) rectangular cloud cell. Skips tapered
/// zero-height ends produced for unmatched zones.
///
/// Square has no blobs/feathering to add structure, so a faint cover-based fill
/// dissolves into the (light) sky. Bump the fill to a visible floor and stroke a
/// darker border so cells read as crisp ForeFlight-style blocks on any sky. (#7)
private func fillFlatCell(
    _ context: inout GraphicsContext,
    x0: CGFloat, x1: CGFloat, baseFt: Double, topFt: Double,
    transform: CoordTransform, fill rgba: RGBA
) {
    guard x1 > x0 else { return }
    let yTop = transform.altitudeToY(topFt)
    let yBase = transform.altitudeToY(baseFt)
    guard yBase > yTop else { return }
    let rect = CGRect(x: x0, y: yTop, width: x1 - x0, height: yBase - yTop)
    context.fill(Path(rect), with: .color(rgba.withAlpha(max(rgba.a, 0.55))))
    let border = RGBA(r: rgba.r * 0.55, g: rgba.g * 0.55, b: rgba.b * 0.65, a: 0.9)
    context.stroke(Path(rect), with: .color(border.color), lineWidth: 0.75)
}

// MARK: - Shared single-point column fallback

private func fillColumn(
    _ context: inout GraphicsContext,
    transform: CoordTransform,
    distanceNm: Double, baseFt: Double, topFt: Double, color: Color
) {
    let x = transform.distanceToX(distanceNm)
    let yTop = transform.altitudeToY(topFt)
    let yBase = transform.altitudeToY(baseFt)
    context.fill(Path(CGRect(x: x - 10, y: yTop, width: 20, height: yBase - yTop)), with: .color(color))
}
