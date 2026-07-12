import SwiftUI

// =============================================================================
// SYNC — port of web/ts/visualization/cross-section/layers/highlight-layer.ts
// (#373 web / #374 iOS). Keep geometry constants and composition rules aligned
// so identical highlight data renders the same on both platforms.
// =============================================================================

/// Advisory highlight layer: scrim (focus) + verdict ribbon (judgement).
///
/// Two visual elements, each doing exactly one job:
///  - **Scrim** — a translucent dim wash over the plot area with cutouts punched
///    out where the hazard physically is, each cutout framed by a thin
///    severity-colored outline. Dimming means "not the focus", never a verdict.
///    Composed in a transparency layer (so `.destinationOut` punches the wash,
///    not the sky/terrain beneath). No scrim at all when there are no flagged
///    regions (the all-green case — never dim a clean chart).
///  - **Verdict ribbon** — a ~6pt strip in the bottom margin (below the plot,
///    above the distance labels) partitioning the whole route into
///    green/amber/red/gray for this advisory. Renders even in the all-green case
///    (an explicit "checked: clear the whole way").
///
/// Geometry is derived reactively from (advisories manifest × selected model)
/// and attached as `data.advisoryHighlights`; this layer is pure rendering and
/// no-ops when there is no highlight data. It is NOT registered in
/// `CrossSectionLayer.allLayers`: the ribbon draws into the bottom margin, so it
/// must bypass the plot-area clip the layer loop applies — the renderer invokes
/// it explicitly last (top of the stack, above the axes), and it manages its own
/// clipping.
struct HighlightLayer: CrossSectionLayerProtocol {
    let id = "advisory-highlight"
    let name = "Highlight"
    let group = LayerGroup.highlight

    /// Ribbon strip geometry within the bottom margin: [plot.bottom + 2, + 8],
    /// which sits ABOVE the distance-axis labels — the renderer pushes those to
    /// `distanceLabelDY` (+11) to keep this strip clear. Keep in sync.
    static let ribbonHeight: CGFloat = 6
    static let ribbonGap: CGFloat = 2
    private static let cutoutOutlineWidth: CGFloat = 1.5

    @MainActor func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard let highlights = data.advisoryHighlights else { return }
        drawScrim(context: &context, transform: transform, regions: highlights.regions)
        drawRibbon(context: &context, transform: transform, ribbon: highlights.ribbon)
    }

    /// Severity colour, aligned with the app's advisory status colours (the same
    /// mapping the badges use). Unavailable = neutral gray. System colours adapt
    /// to light/dark automatically.
    private func severityColor(_ severity: String) -> Color {
        (Assessment(rawValue: severity) ?? .unavailable).color
    }

    // MARK: - Scrim

    @MainActor
    private func drawScrim(context: inout GraphicsContext, transform: CoordTransform,
                           regions: [VizAdvisoryHighlights.Region]) {
        guard !regions.isEmpty else { return }  // all-green: never dim a clean chart
        let plot = transform.plotArea
        let plotRect = CGRect(x: plot.left, y: plot.top, width: plot.width, height: plot.height)
        let merged = Self.mergedRegions(regions)

        // Compose the dim wash + cutouts in a transparency layer so
        // `.destinationOut` punches only the wash, not the sky/terrain/weather
        // beneath, then composite the whole layer onto the main context.
        context.drawLayer { layer in
            layer.fill(Path(plotRect), with: .color(CrossSectionTheme.active.scrimWash))
            layer.blendMode = .destinationOut
            for region in merged {
                layer.fill(Path(rect(for: region, transform: transform)), with: .color(.black))
            }
        }

        // Stroke each cutout with a severity-coloured outline, clipped to the
        // plot area so a full-column outline doesn't bleed into the margins.
        var clipped = context
        clipped.clip(to: Path(plotRect))
        for region in merged {
            clipped.stroke(Path(rect(for: region, transform: transform)),
                           with: .color(severityColor(region.severity)),
                           lineWidth: Self.cutoutOutlineWidth)
        }
    }

    /// Pixel rect of a region. base/top nil → terrain-to-top (full column).
    private func rect(for region: VizAdvisoryHighlights.Region, transform: CoordTransform) -> CGRect {
        let plot = transform.plotArea
        let x0 = transform.distanceToX(region.distFromNm)
        let x1 = transform.distanceToX(region.distToNm)
        let yTop = region.topFt.map { transform.altitudeToY($0) } ?? plot.top
        let yBottom = region.baseFt.map { transform.altitudeToY($0) } ?? plot.bottom
        return CGRect(x: x0, y: yTop, width: x1 - x0, height: yBottom - yTop)
    }

    /// Merge abutting same-kind/same-severity/same-span regions into one rect so
    /// the outline pass doesn't stroke seams inside what reads as one visual
    /// region. The server already envelope-merges contiguous runs; this is the
    /// defensive client-side pass the issue asks for (visual parity with web).
    static func mergedRegions(_ regions: [VizAdvisoryHighlights.Region]) -> [VizAdvisoryHighlights.Region] {
        var out: [VizAdvisoryHighlights.Region] = []
        for region in regions {
            if let last = out.last,
               last.kind == region.kind, last.severity == region.severity,
               last.baseFt == region.baseFt, last.topFt == region.topFt,
               abs(last.distToNm - region.distFromNm) < 0.01 {
                out[out.count - 1] = VizAdvisoryHighlights.Region(
                    distFromNm: last.distFromNm, distToNm: region.distToNm,
                    baseFt: last.baseFt, topFt: last.topFt,
                    kind: last.kind, severity: last.severity)
            } else {
                out.append(region)
            }
        }
        return out
    }

    // MARK: - Verdict ribbon

    @MainActor
    private func drawRibbon(context: inout GraphicsContext, transform: CoordTransform,
                            ribbon: [VizAdvisoryHighlights.Segment]) {
        guard !ribbon.isEmpty else { return }
        let y = transform.plotArea.bottom + Self.ribbonGap
        for segment in ribbon {
            let x0 = transform.distanceToX(segment.distFromNm)
            let x1 = transform.distanceToX(segment.distToNm)
            guard x1 > x0 else { continue }
            context.fill(Path(CGRect(x: x0, y: y, width: x1 - x0, height: Self.ribbonHeight)),
                         with: .color(severityColor(segment.severity)))
        }
    }
}
