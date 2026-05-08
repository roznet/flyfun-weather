import SwiftUI

/// NWP cloud bands rendered from server-computed nwp_cloud_layers.
/// Boundaries come from the backend (GRIB diagnostics or DD-narrowed model %)
/// — no client-side heuristic narrowing.
struct NwpCloudBandsLayer: CrossSectionLayerProtocol {
    let id = "nwp-cloud-bands"
    let name = "Clouds (NWP)"
    let group: LayerGroup = .clouds

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        // Only render when at least one point has native NWP data.
        guard data.points.contains(where: { ($0.nwpCloudLayers ?? []).isEmpty == false }) else { return }

        renderMatchedZones(
            &context, transform: transform, data: data,
            getZones: { $0.nwpCloudLayers ?? [] },
            getColor: { cl, matched in
                let pct: Double
                if let m = matched {
                    pct = (ColorScales.coverageToPct(cl.coverage) + ColorScales.coverageToPct(m.coverage)) / 2
                } else {
                    pct = ColorScales.coverageToPct(cl.coverage)
                }
                return ColorScales.nwpCloudFill(pct: pct)
            }
        )
    }
}
