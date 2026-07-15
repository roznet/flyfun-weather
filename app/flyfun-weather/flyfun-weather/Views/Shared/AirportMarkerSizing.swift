import MapKit
import UIKit

/// Shared zoom→marker-diameter bucketing for the airport dots, so the full
/// forecast map (`ForecastMapKitView`) and the briefing route-map overlay
/// (`RouteMapKitView`) can't drift on the `log2(360/span)` zoom formula — they
/// only differ in the per-bucket radii (#429 review).
///
/// `radii` are the five per-bucket radii, smallest→largest zoom; `fallback` is
/// the diameter used before the map has a valid span.
enum AirportMarkerSizing {
    static func diameter(for map: MKMapView, radii: [CGFloat], fallback: CGFloat) -> CGFloat {
        let span = map.region.span.longitudeDelta
        guard span > 0, radii.count == 5 else { return fallback }
        let zoom = log2(360 / span)
        let idx: Int
        switch zoom {
        case ..<4.5: idx = 0
        case ..<5.5: idx = 1
        case ..<6.5: idx = 2
        case ..<7.5: idx = 3
        default: idx = 4
        }
        return radii[idx] * 2
    }
}
