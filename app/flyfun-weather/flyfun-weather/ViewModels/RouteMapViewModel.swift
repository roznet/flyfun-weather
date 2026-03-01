import Foundation
import MapKit

/// View model for the route map — extracts waypoint coordinates and computes map region.
@Observable
@MainActor
final class RouteMapViewModel {
    struct WaypointAnnotation: Identifiable {
        let id: String // ICAO
        let name: String
        let coordinate: CLLocationCoordinate2D
    }

    var waypoints: [WaypointAnnotation] = []
    var routeCoordinates: [CLLocationCoordinate2D] = []
    var mapRegion: MKCoordinateRegion = .init()

    func update(from snapshot: SnapshotResponse) {
        let wps = snapshot.route.waypoints
        waypoints = wps.map {
            WaypointAnnotation(id: $0.icao, name: $0.name, coordinate: CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon))
        }
        routeCoordinates = wps.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }

        // Compute region to fit all waypoints with padding
        guard !routeCoordinates.isEmpty else { return }
        let lats = routeCoordinates.map(\.latitude)
        let lons = routeCoordinates.map(\.longitude)
        let center = CLLocationCoordinate2D(
            latitude: (lats.min()! + lats.max()!) / 2,
            longitude: (lons.min()! + lons.max()!) / 2
        )
        let span = MKCoordinateSpan(
            latitudeDelta: (lats.max()! - lats.min()!) * 1.4 + 0.5,
            longitudeDelta: (lons.max()! - lons.min()!) * 1.4 + 0.5
        )
        mapRegion = MKCoordinateRegion(center: center, span: span)
    }

    func update(from analyses: RouteAnalysesResponse) {
        // Use route analysis points for finer route line
        routeCoordinates = analyses.analyses.map {
            CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)
        }

        // Waypoint markers from analysis waypoint_icao
        let waypointAnalyses = analyses.analyses.filter { $0.waypointIcao != nil }
        waypoints = waypointAnalyses.map {
            WaypointAnnotation(
                id: $0.waypointIcao!,
                name: $0.waypointName ?? $0.waypointIcao!,
                coordinate: CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)
            )
        }

        guard !routeCoordinates.isEmpty else { return }
        let lats = routeCoordinates.map(\.latitude)
        let lons = routeCoordinates.map(\.longitude)
        let center = CLLocationCoordinate2D(
            latitude: (lats.min()! + lats.max()!) / 2,
            longitude: (lons.min()! + lons.max()!) / 2
        )
        let span = MKCoordinateSpan(
            latitudeDelta: (lats.max()! - lats.min()!) * 1.4 + 0.5,
            longitudeDelta: (lons.max()! - lons.min()!) * 1.4 + 0.5
        )
        mapRegion = MKCoordinateRegion(center: center, span: span)
    }
}
