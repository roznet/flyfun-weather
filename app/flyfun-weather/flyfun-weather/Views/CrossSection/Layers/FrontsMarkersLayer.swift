import SwiftUI

/// Air-mass boundaries (experimental Hewson fronts, #196). Port of the web
/// `fronts-markers` layer, chain rendering only.
///
/// A front is a sloping surface, so a boundary the server linked across
/// 925/850/700 hPa draws as ONE slanted line through its per-level crossings,
/// tilting back over the cold air with height. Colour = kind (cold blue, warm
/// red, quasi-stationary purple), weight = intensity, opacity = depth: a
/// single-level chain draws as a faint dashed vertical, a linked one solid with
/// dashed extrapolation to the column edges. A dot marks each detected level, a
/// triangle under it a convective node, and a kind-initial chip tops the line.
///
/// Packs built before vertical linking carry no chains and draw nothing here
/// (the web falls back to deduped per-level crossings, which would mean deriving
/// on the phone). Advisory-only free-atmosphere boundaries — not drawn SIGWX.
///
/// SYNC: `web/ts/visualization/cross-section/layers/fronts-markers.ts`,
/// colours and weights from `web/ts/visualization/front-style.ts`.
struct FrontsMarkersLayer: CrossSectionLayerProtocol {
    let id = "fronts-markers"
    let name = "Air-mass boundary (experimental)"
    let group: LayerGroup = .fronts

    private static let kmPerNm = 1.852

    /// Pressure level → representative MSL altitude (ft), matching the backend
    /// `_LEVEL_FT` in `tasks/fronts.py`.
    private static let levelFt: [Int: Double] = [925: 2_500, 850: 5_000, 700: 10_000]

    /// Surface-analysis convention (web `FRONT_KIND_COLOR`).
    static func color(_ kind: String?) -> Color {
        switch kind {
        case "cold": Color(.sRGB, red: 37 / 255.0, green: 99 / 255.0, blue: 235 / 255.0)      // #2563eb
        case "warm": Color(.sRGB, red: 220 / 255.0, green: 38 / 255.0, blue: 38 / 255.0)      // #dc2626
        default: Color(.sRGB, red: 124 / 255.0, green: 58 / 255.0, blue: 237 / 255.0)        // #7c3aed
        }
    }

    /// Line weight by intensity (web `FRONT_INTENSITY_WEIGHT`).
    private static func weight(_ intensity: String?) -> CGFloat {
        switch intensity {
        case "significant": 2
        case "classical": 3
        case "sharp": 4.5
        default: 2
        }
    }

    private static func kindInitial(_ kind: String?) -> String {
        switch kind {
        case "cold": "C"
        case "warm": "W"
        default: "Q"
        }
    }

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard !data.frontChains.isEmpty else { return }
        let plot = transform.plotArea
        for chain in data.frontChains {
            renderChain(context: context, transform: transform, chain: chain,
                        yTop: plot.top, yBottom: plot.bottom)
        }
    }

    /// Takes the context by value: each chain sets its own opacity on a copy.
    private func renderChain(
        context: GraphicsContext, transform: CoordTransform, chain: FrontChain,
        yTop: CGFloat, yBottom: CGFloat
    ) {
        let pts = chain.nodes.map {
            CGPoint(x: transform.distanceToX($0.distanceKm / Self.kmPerNm),
                    y: transform.altitudeToY(Self.levelFt[$0.levelHpa] ?? 5_000))
        }
        guard let first = pts.first, let last = pts.last else { return }

        let color = Self.color(chain.kind)
        let lineWidth = chain.nodes.map { Self.weight($0.intensity) }.max() ?? 2
        let alpha = (chain.nLevels ?? 1) >= 2 ? 0.95 : 0.5  // single-level = shallow/suspect → faint
        let dashed = StrokeStyle(lineWidth: lineWidth, dash: [5, 5])
        var ctx = context
        ctx.opacity = alpha

        if pts.count == 1 {
            // One level only: a full-height dashed vertical, faint — only a
            // vertically-linked front earns the solid slant.
            var path = Path()
            path.move(to: CGPoint(x: first.x, y: yTop))
            path.addLine(to: CGPoint(x: first.x, y: yBottom))
            ctx.stroke(path, with: .color(color), style: dashed)
        } else {
            // Dashed extrapolation past the outermost detected levels, along the
            // end segments' slopes so the dash meets the solid without a kink.
            // pts run bottom → top (925 → 850 → 700).
            let topX = Self.extend(pts[pts.count - 2], last, toY: yTop)
            let botX = Self.extend(pts[1], first, toY: yBottom)
            var ext = Path()
            ext.move(to: CGPoint(x: topX, y: yTop))
            ext.addLine(to: last)
            ext.move(to: first)
            ext.addLine(to: CGPoint(x: botX, y: yBottom))
            var faint = ctx
            faint.opacity = alpha * 0.5
            faint.stroke(ext, with: .color(color), style: dashed)

            var line = Path()
            line.addLines(pts)
            ctx.stroke(line, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, lineJoin: .round))
        }

        // Node dot per detected level; a triangle below a convective node
        // (towers above an overflown front).
        for (i, p) in pts.enumerated() {
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - 3, y: p.y - 3, width: 6, height: 6)), with: .color(color))
            if chain.nodes[i].coLocation == "convective" {
                let ty = p.y + 5
                var tri = Path()
                tri.move(to: CGPoint(x: p.x, y: ty))
                tri.addLine(to: CGPoint(x: p.x - 5, y: ty + 8))
                tri.addLine(to: CGPoint(x: p.x + 5, y: ty + 8))
                tri.closeSubpath()
                ctx.fill(tri, with: .color(color))
            }
        }

        // Kind-initial chip: at the column top for a single-level line, above
        // the top (700 hPa) node for a slanted chain.
        let label = ctx.resolve(Text(Self.kindInitial(chain.kind))
            .font(.system(size: 11, weight: .bold)).foregroundColor(.white))
        let size = label.measure(in: CGSize(width: 40, height: 20))
        let chipW = size.width + 6
        let chipH: CGFloat = 15
        let chipY = pts.count == 1 ? yTop : max(yTop, last.y - chipH - 4)
        let chip = CGRect(x: last.x - chipW / 2, y: chipY, width: chipW, height: chipH)
        ctx.fill(Path(chip), with: .color(color))
        ctx.draw(label, at: CGPoint(x: chip.midX, y: chip.midY), anchor: .center)
    }

    /// The x where the line through `a` and `b` meets `yEdge`; `b.x` when the
    /// segment is horizontal.
    private static func extend(_ a: CGPoint, _ b: CGPoint, toY yEdge: CGFloat) -> CGFloat {
        let dy = b.y - a.y
        guard abs(dy) >= 1e-6 else { return b.x }
        let t = (yEdge - a.y) / dy
        return a.x + t * (b.x - a.x)
    }
}
