import SwiftUI

/// Natural and square cloud styles for the same DD/NWP source axis the web uses.
/// Soft cloud bands stay in SoftCloudBandsLayer; these styles fill the parity gap
/// for web's cloud-bands-factory source x style matrix.
struct CloudStyleBandsLayer: CrossSectionLayerProtocol {
    enum Source {
        case dd
        case nwp
    }

    enum Style {
        case natural
        case square
    }

    let source: Source
    let style: Style

    var id: String {
        switch (source, style) {
        case (.dd, .natural): "cloud-bands"
        case (.nwp, .natural): "nwp-cloud-bands"
        case (.dd, .square): "square-cloud-bands"
        case (.nwp, .square): "square-nwp-cloud-bands"
        }
    }

    var name: String {
        switch (source, style) {
        case (.dd, .natural): "DD Natural"
        case (.nwp, .natural): "NWP Natural"
        case (.dd, .square): "Square DD"
        case (.nwp, .square): "Square NWP"
        }
    }

    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        let getZones: (VizPoint) -> [VizCloudLayer] = { pt in
            source == .dd ? pt.cloudLayers : (pt.nwpCloudLayers ?? [])
        }

        guard data.points.contains(where: { getZones($0).isEmpty == false }) else { return }

        renderMatchedCloudZones(
            &context,
            transform: transform,
            data: data,
            getZones: getZones,
            draw: { ctx, points, cloud, matched in
                switch style {
                case .natural:
                    drawNaturalCloudBand(&ctx, points: points, transform: transform, cloud: cloud, matched: matched, source: source)
                case .square:
                    drawSquareCloudBand(&ctx, points: points, transform: transform, cloud: cloud, matched: matched, source: source)
                }
            }
        )
    }
}

private struct NaturalCloudConfig {
    let fillFraction: [String: Double]
    let minFillFraction: Double
    let blobSpacingPx: CGFloat
    let blobRadiusXPx: CGFloat
    let blobHeightFraction: CGFloat
    let blobJitterX: CGFloat
    let blobJitterY: CGFloat
    let blobSizeVariation: CGFloat
    let subBlobsPerSlot: Int
    let subBlobOffsetFraction: CGFloat
    let subBlobSizeFraction: CGFloat
    let subBlobSizeJitter: CGFloat
    let radiusCoverageScaleFloor: Double
}

private let defaultNaturalCloudConfig = NaturalCloudConfig(
    fillFraction: ["FEW": 1.0, "SCT": 1.0, "BKN": 1.0, "OVC": 1.0],
    minFillFraction: 1.0,
    blobSpacingPx: 28,
    blobRadiusXPx: 36,
    blobHeightFraction: 0.6,
    blobJitterX: 0.4,
    blobJitterY: 0.15,
    blobSizeVariation: 0.5,
    subBlobsPerSlot: 3,
    subBlobOffsetFraction: 0.35,
    subBlobSizeFraction: 0.65,
    subBlobSizeJitter: 0.25,
    radiusCoverageScaleFloor: 1.0
)

private func renderMatchedCloudZones(
    _ context: inout GraphicsContext,
    transform: CoordTransform,
    data: VizRouteData,
    getZones: (VizPoint) -> [VizCloudLayer],
    draw: (inout GraphicsContext, [BandPoint], VizCloudLayer, VizCloudLayer?) -> Void
) {
    let pts = data.points

    if pts.count == 1 {
        let p = pts[0]
        for cloud in getZones(p) {
            draw(
                &context,
                [BandPoint(distanceNm: p.distanceNm, baseFt: cloud.baseFt, topFt: cloud.topFt)],
                cloud,
                nil
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

        for cloud in currZones {
            var bestIdx = -1
            var bestOverlap: Double = 0
            for (j, nextCloud) in nextZones.enumerated() {
                if usedNext.contains(j) { continue }
                let overlap = min(cloud.topFt, nextCloud.topFt) - max(cloud.baseFt, nextCloud.baseFt)
                if overlap > bestOverlap {
                    bestOverlap = overlap
                    bestIdx = j
                }
            }

            if bestIdx >= 0 {
                let matched = nextZones[bestIdx]
                usedNext.insert(bestIdx)
                draw(
                    &context,
                    [
                        BandPoint(distanceNm: curr.distanceNm, baseFt: cloud.baseFt, topFt: cloud.topFt),
                        BandPoint(distanceNm: next.distanceNm, baseFt: matched.baseFt, topFt: matched.topFt),
                    ],
                    cloud,
                    matched
                )
            } else {
                let midDist = (curr.distanceNm + next.distanceNm) / 2
                let midAlt = (cloud.baseFt + cloud.topFt) / 2
                draw(
                    &context,
                    [
                        BandPoint(distanceNm: curr.distanceNm, baseFt: cloud.baseFt, topFt: cloud.topFt),
                        BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                    ],
                    cloud,
                    nil
                )
            }
        }

        for (j, nextCloud) in nextZones.enumerated() {
            if usedNext.contains(j) { continue }
            let midDist = (curr.distanceNm + next.distanceNm) / 2
            let midAlt = (nextCloud.baseFt + nextCloud.topFt) / 2
            draw(
                &context,
                [
                    BandPoint(distanceNm: midDist, baseFt: midAlt, topFt: midAlt),
                    BandPoint(distanceNm: next.distanceNm, baseFt: nextCloud.baseFt, topFt: nextCloud.topFt),
                ],
                nextCloud,
                nil
            )
        }
    }
}

private func drawNaturalCloudBand(
    _ context: inout GraphicsContext,
    points: [BandPoint],
    transform: CoordTransform,
    cloud: VizCloudLayer,
    matched: VizCloudLayer?,
    source: CloudStyleBandsLayer.Source
) {
    let config = defaultNaturalCloudConfig
    let fill = matchedCloudColor(cloud, matched: matched, source: source)

    if points.count == 1 {
        guard let p = points.first else { return }
        let x = transform.distanceToX(p.distanceNm)
        let yTop = transform.altitudeToY(p.topFt)
        let yBase = transform.altitudeToY(p.baseFt)
        drawNaturalCloudBlobs(
            &context,
            xLeft: x - 10,
            xRight: x + 10,
            baseAt: { _ in yBase },
            topAt: { _ in yTop },
            fill: fill,
            fillFraction: naturalFillFraction(cloud, matched: matched, config: config),
            seed: bandSeed(cloud, source: source),
            config: config
        )
        return
    }

    guard points.count >= 2 else { return }
    let left = points[0]
    let right = points[1]
    let xLeft = transform.distanceToX(left.distanceNm)
    let xRight = transform.distanceToX(right.distanceNm)
    guard xRight > xLeft else { return }

    let yBaseLeft = transform.altitudeToY(left.baseFt)
    let yBaseRight = transform.altitudeToY(right.baseFt)
    let yTopLeft = transform.altitudeToY(left.topFt)
    let yTopRight = transform.altitudeToY(right.topFt)
    let width = xRight - xLeft

    drawNaturalCloudBlobs(
        &context,
        xLeft: xLeft,
        xRight: xRight,
        baseAt: { x in yBaseLeft + (yBaseRight - yBaseLeft) * (x - xLeft) / width },
        topAt: { x in yTopLeft + (yTopRight - yTopLeft) * (x - xLeft) / width },
        fill: fill,
        fillFraction: naturalFillFraction(cloud, matched: matched, config: config),
        seed: bandSeed(cloud, source: source),
        config: config
    )
}

private func drawNaturalCloudBlobs(
    _ context: inout GraphicsContext,
    xLeft: CGFloat,
    xRight: CGFloat,
    baseAt: (CGFloat) -> CGFloat,
    topAt: (CGFloat) -> CGFloat,
    fill: Color,
    fillFraction: Double,
    seed: Int,
    config: NaturalCloudConfig
) {
    guard xRight > xLeft else { return }
    let slotWidth = config.blobSpacingPx
    let slotStart = Int(floor(xLeft / slotWidth)) - 1
    let slotEnd = Int(ceil(xRight / slotWidth)) + 1
    let radiusScale = CGFloat(config.radiusCoverageScaleFloor
        + (1 - config.radiusCoverageScaleFloor) * min(1, max(0, fillFraction))
    )

    for slot in slotStart..<slotEnd {
        if hash01(slot &* 0x1f1f1f &+ seed) > fillFraction { continue }

        let xJitter = CGFloat(hash01(slot &* 0x2f3f4f &+ seed) - 0.5) * slotWidth * config.blobJitterX
        let centerX = (CGFloat(slot) + 0.5) * slotWidth + xJitter
        if centerX < xLeft || centerX >= xRight { continue }

        let yTop = topAt(centerX)
        let yBase = baseAt(centerX)
        let bandHeight = yBase - yTop
        if bandHeight <= 0 { continue }

        let yJitter = CGFloat(hash01(slot &* 0x5b6c79 &+ seed) - 0.5) * bandHeight * config.blobJitterY
        let centerY = (yTop + yBase) / 2 + yJitter
        let sizeJitter = 1 + CGFloat(hash01(slot &* 0x7e8f91 &+ seed) - 0.5) * config.blobSizeVariation
        let radiusX = config.blobRadiusXPx * sizeJitter * radiusScale
        let radiusY = bandHeight * config.blobHeightFraction * sizeJitter * radiusScale
        let aspect = radiusX > 0 ? radiusY / radiusX : 1

        for i in 0..<max(1, config.subBlobsPerSlot) {
            let subSeed = (slot &* 0x9e3779b1) ^ (i &* 0x85ebca6b) ^ seed
            let angle = hash01(subSeed) * Double.pi * 2
            let offsetMag = CGFloat(hash01(subSeed ^ 0x12345)) * config.subBlobOffsetFraction * radiusX
            let offsetX = CGFloat(cos(angle)) * offsetMag
            let offsetY = CGFloat(sin(angle)) * offsetMag * aspect
            let subSize = config.subBlobSizeFraction
                + CGFloat(hash01(subSeed ^ 0x67890) - 0.5) * config.subBlobSizeJitter
            let rect = CGRect(
                x: centerX + offsetX - radiusX * subSize,
                y: centerY + offsetY - radiusY * subSize,
                width: radiusX * subSize * 2,
                height: radiusY * subSize * 2
            )
            context.fill(Path(ellipseIn: rect), with: .color(fill))
        }
    }
}

private func drawSquareCloudBand(
    _ context: inout GraphicsContext,
    points: [BandPoint],
    transform: CoordTransform,
    cloud: VizCloudLayer,
    matched: VizCloudLayer?,
    source: CloudStyleBandsLayer.Source
) {
    let fill = matchedCloudColor(cloud, matched: matched, source: source)

    if points.count == 1, let point = points.first {
        let x = transform.distanceToX(point.distanceNm)
        let yTop = transform.altitudeToY(point.topFt)
        let yBase = transform.altitudeToY(point.baseFt)
        context.fill(Path(CGRect(x: x - 10, y: yTop, width: 20, height: yBase - yTop)), with: .color(fill))
        return
    }

    guard points.count >= 2 else { return }
    let left = points[0]
    let right = points[1]
    var path = Path()
    path.move(to: CGPoint(x: transform.distanceToX(left.distanceNm), y: transform.altitudeToY(left.topFt)))
    path.addLine(to: CGPoint(x: transform.distanceToX(right.distanceNm), y: transform.altitudeToY(right.topFt)))
    path.addLine(to: CGPoint(x: transform.distanceToX(right.distanceNm), y: transform.altitudeToY(right.baseFt)))
    path.addLine(to: CGPoint(x: transform.distanceToX(left.distanceNm), y: transform.altitudeToY(left.baseFt)))
    path.closeSubpath()
    context.fill(path, with: .color(fill))
}

private func matchedCloudColor(
    _ cloud: VizCloudLayer,
    matched: VizCloudLayer?,
    source: CloudStyleBandsLayer.Source
) -> Color {
    switch source {
    case .dd:
        let dd = average(cloud.meanDewpointDepressionC, matched?.meanDewpointDepressionC)
        return ColorScales.cloudFill(dewpointDepressionC: dd, coverage: cloud.coverage)
    case .nwp:
        let coverA = cloud.meanCloudCoverPct ?? ColorScales.coverageToPct(cloud.coverage)
        let coverB = matched?.meanCloudCoverPct ?? matched.map { ColorScales.coverageToPct($0.coverage) } ?? coverA
        return ColorScales.nwpCloudFill(pct: (coverA + coverB) / 2)
    }
}

private func naturalFillFraction(
    _ cloud: VizCloudLayer,
    matched: VizCloudLayer?,
    config: NaturalCloudConfig
) -> Double {
    if let coverA = cloud.meanCloudCoverPct {
        let coverB = matched?.meanCloudCoverPct ?? coverA
        return max(config.minFillFraction, min(1, ((coverA + coverB) / 2) / 100))
    }
    let coverA = config.fillFraction[coverageBucket(cloud.coverage), default: 1]
    let coverB = matched.map { config.fillFraction[coverageBucket($0.coverage), default: coverA] } ?? coverA
    return max(config.minFillFraction, (coverA + coverB) / 2)
}

private func coverageBucket(_ coverage: String) -> String {
    let value = coverage.uppercased()
    if value == "OVC" || value == "BKN" || value == "SCT" || value == "FEW" {
        return value
    }
    return "BKN"
}

private func bandSeed(_ cloud: VizCloudLayer, source: CloudStyleBandsLayer.Source) -> Int {
    let sourceSalt = source == .nwp ? 0xdeadbeef : 0
    return (Int((cloud.baseFt / 100).rounded()) ^ 0x4d36e96) ^ sourceSalt
}

private func average(_ a: Double?, _ b: Double?) -> Double? {
    if let a, let b { return (a + b) / 2 }
    return a ?? b
}

private func hash01(_ n: Int) -> Double {
    var h = UInt32(truncatingIfNeeded: n)
    h &+= 0x9e3779b9
    h = (h ^ (h >> 16)) &* 0x85ebca6b
    h = (h ^ (h >> 13)) &* 0xc2b2ae35
    h = h ^ (h >> 16)
    return Double(h) / 4_294_967_296.0
}
