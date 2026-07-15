import MapKit
import SwiftUI
import UIKit

/// The briefing route map's rendering substrate, on **MKMapView** (`#428`).
///
/// The route map used to be a SwiftUI declarative `Map`; the forecast map is an
/// imperative `MKMapView` (a deliberate choice — 619 SwiftUI `Annotation`s jank).
/// To let the briefing map reuse the forecast map's marker machinery for the
/// airport-forecast overlay (the iOS port of #424/#425) instead of a second,
/// divergent renderer, both maps converge on `MKMapView` — mirroring the web,
/// where both maps are one Leaflet `RouteMapRenderer`.
///
/// This view owns only the *rendering*; all the controls (metric picker(s),
/// legend, altitude slider, waypoint sheet, deep-link focus) stay in
/// `RouteMapView` as SwiftUI overlaid on top.
///
/// Layer order (bottom → top): base tiles · route overlays (grey base line + the
/// metric-coloured segments) · annotations (waypoints, active point, aircraft,
/// and — in commit 2 — the airport-forecast dots). NOTE: MapKit always draws
/// annotations above overlay renderers, so the airport dots added later sit
/// *above* the route line, the inverse of the web (which draws them beneath);
/// they're kept small + semi-transparent so the route stays visually primary.
struct RouteMapKitView: UIViewRepresentable {
    /// One metric-coloured route segment (already reduced from its two endpoints
    /// in `RouteMapView`, so this view stays pure rendering).
    struct Segment {
        let coords: [CLLocationCoordinate2D]
        let color: UIColor
        let width: CGFloat
    }

    /// Live aircraft state during in-flight tracking (nil when not tracking).
    struct AircraftState: Equatable {
        let coordinate: CLLocationCoordinate2D
        let headingDeg: Double
        let opacity: Double

        static func == (l: AircraftState, r: AircraftState) -> Bool {
            l.coordinate.latitude == r.coordinate.latitude
                && l.coordinate.longitude == r.coordinate.longitude
                && l.headingDeg == r.headingDeg && l.opacity == r.opacity
        }
    }

    let routeCoordinates: [CLLocationCoordinate2D]
    let segments: [Segment]
    /// Cheap signature of the route overlays; they rebuild only when this changes,
    /// so the many SwiftUI re-renders that don't touch the route (e.g. a tracking
    /// tick moving only the aircraft) skip the overlay teardown/rebuild.
    let routeSignature: String
    let waypoints: [RouteMapViewModel.WaypointAnnotation]
    let initialRegion: MKCoordinateRegion

    /// Shared active route point (§4.7) — reflects the scrub point set on the
    /// cross-section / Skew-T or by tapping a waypoint here. nil = none.
    let activePoint: CLLocationCoordinate2D?
    /// Live aircraft during tracking, or nil.
    let aircraft: AircraftState?

    /// A waypoint marker was tapped (reports its ICAO).
    let onSelectWaypoint: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> MKMapView {
        let map = MKMapView()
        map.delegate = context.coordinator
        map.pointOfInterestFilter = .excludingAll
        map.showsCompass = false
        map.register(RouteWaypointMarkerView.self,
                     forAnnotationViewWithReuseIdentifier: RouteWaypointMarkerView.reuseID)
        map.register(RouteActiveMarkerView.self,
                     forAnnotationViewWithReuseIdentifier: RouteActiveMarkerView.reuseID)
        map.register(RouteAircraftMarkerView.self,
                     forAnnotationViewWithReuseIdentifier: RouteAircraftMarkerView.reuseID)
        map.setRegion(initialRegion, animated: false)
        context.coordinator.map = map
        return map
    }

    func updateUIView(_ map: MKMapView, context: Context) {
        context.coordinator.update(parent: self)
    }

    // MARK: - Coordinator

    @MainActor
    final class Coordinator: NSObject {
        var parent: RouteMapKitView
        weak var map: MKMapView?

        private var renderedRouteSignature: String?
        private var renderedWaypointSignature: String?
        private var activeAnnotation: RouteActiveAnnotation?
        private var aircraftAnnotation: RouteAircraftAnnotation?

        init(_ parent: RouteMapKitView) { self.parent = parent }

        func update(parent: RouteMapKitView) {
            self.parent = parent
            guard let map else { return }
            updateRoute(on: map)
            updateWaypoints(on: map)
            updateActivePoint(on: map)
            updateAircraft(on: map)
        }

        // MARK: Route overlays

        private func updateRoute(on map: MKMapView) {
            guard renderedRouteSignature != parent.routeSignature else { return }
            renderedRouteSignature = parent.routeSignature
            map.removeOverlays(map.overlays)

            if parent.routeCoordinates.count >= 2 {
                let base = ColoredPolyline(coordinates: parent.routeCoordinates,
                                           count: parent.routeCoordinates.count)
                base.color = UIColor.systemGray.withAlphaComponent(0.4)
                base.width = 2
                map.addOverlay(base, level: .aboveRoads)
            }
            for seg in parent.segments where seg.coords.count >= 2 {
                let line = ColoredPolyline(coordinates: seg.coords, count: seg.coords.count)
                line.color = seg.color
                line.width = seg.width
                map.addOverlay(line, level: .aboveRoads)
            }
        }

        // MARK: Waypoint annotations

        private func updateWaypoints(on map: MKMapView) {
            let sig = parent.waypoints.map(\.id).joined(separator: ",")
            guard renderedWaypointSignature != sig else { return }
            renderedWaypointSignature = sig
            let existing = map.annotations.compactMap { $0 as? RouteWaypointAnnotation }
            map.removeAnnotations(existing)
            map.addAnnotations(parent.waypoints.map {
                RouteWaypointAnnotation(icao: $0.id, coordinate: $0.coordinate)
            })
        }

        // MARK: Active point (added / moved / removed in place)

        private func updateActivePoint(on map: MKMapView) {
            guard let coord = parent.activePoint else {
                if let a = activeAnnotation { map.removeAnnotation(a); activeAnnotation = nil }
                return
            }
            if let a = activeAnnotation {
                a.coordinate = coord
            } else {
                let a = RouteActiveAnnotation(coordinate: coord)
                activeAnnotation = a
                map.addAnnotation(a)
            }
        }

        // MARK: Aircraft (added / moved / removed in place)

        private func updateAircraft(on map: MKMapView) {
            guard let state = parent.aircraft else {
                if let a = aircraftAnnotation { map.removeAnnotation(a); aircraftAnnotation = nil }
                return
            }
            if let a = aircraftAnnotation {
                a.coordinate = state.coordinate
                a.headingDeg = state.headingDeg
                a.opacity = state.opacity
                if let v = map.view(for: a) as? RouteAircraftMarkerView {
                    v.apply(headingDeg: state.headingDeg, opacity: state.opacity)
                }
            } else {
                let a = RouteAircraftAnnotation(coordinate: state.coordinate,
                                                headingDeg: state.headingDeg, opacity: state.opacity)
                aircraftAnnotation = a
                map.addAnnotation(a)
            }
        }
    }
}

// MapKit calls the delegate on the main thread; `@preconcurrency` lets the
// `@MainActor` coordinator satisfy the non-isolated delegate protocol (same
// pattern as `ForecastMapKitView` / `FlightTrackingService`).
extension RouteMapKitView.Coordinator: @preconcurrency MKMapViewDelegate {
    func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
        guard let line = overlay as? ColoredPolyline else {
            return MKOverlayRenderer(overlay: overlay)
        }
        let r = MKPolylineRenderer(polyline: line)
        r.strokeColor = line.color
        r.lineWidth = line.width
        r.lineCap = .round
        r.lineJoin = .round
        return r
    }

    func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
        switch annotation {
        case let wp as RouteWaypointAnnotation:
            let view = mapView.dequeueReusableAnnotationView(
                withIdentifier: RouteWaypointMarkerView.reuseID, for: wp)
            (view as? RouteWaypointMarkerView)?.configure(text: wp.icao)
            return view
        case is RouteActiveAnnotation:
            return mapView.dequeueReusableAnnotationView(
                withIdentifier: RouteActiveMarkerView.reuseID, for: annotation)
        case let aircraft as RouteAircraftAnnotation:
            let view = mapView.dequeueReusableAnnotationView(
                withIdentifier: RouteAircraftMarkerView.reuseID, for: aircraft)
            (view as? RouteAircraftMarkerView)?.apply(headingDeg: aircraft.headingDeg, opacity: aircraft.opacity)
            return view
        default:
            return nil
        }
    }

    func mapView(_ mapView: MKMapView, didSelect view: MKAnnotationView) {
        guard let wp = view.annotation as? RouteWaypointAnnotation else { return }
        // Drive selection from our callback, not MapKit's own selection state, so
        // re-tapping the same waypoint always re-fires.
        mapView.deselectAnnotation(wp, animated: false)
        parent.onSelectWaypoint(wp.icao)
    }
}

// MARK: - Overlay model

/// An `MKPolyline` carrying its own stroke colour + width, so a single renderer
/// draws both the grey base line and each metric-coloured segment.
final class ColoredPolyline: MKPolyline {
    var color: UIColor = .systemGray
    var width: CGFloat = 8
}

// MARK: - Annotations

/// A route waypoint (departure / turning point / destination).
final class RouteWaypointAnnotation: NSObject, MKAnnotation {
    let icao: String
    @objc dynamic var coordinate: CLLocationCoordinate2D
    var title: String? { icao }
    init(icao: String, coordinate: CLLocationCoordinate2D) {
        self.icao = icao
        self.coordinate = coordinate
    }
}

/// The shared active route point (orange ring).
final class RouteActiveAnnotation: NSObject, MKAnnotation {
    @objc dynamic var coordinate: CLLocationCoordinate2D
    init(coordinate: CLLocationCoordinate2D) { self.coordinate = coordinate }
}

/// The live aircraft position during in-flight tracking.
final class RouteAircraftAnnotation: NSObject, MKAnnotation {
    @objc dynamic var coordinate: CLLocationCoordinate2D
    var headingDeg: Double
    var opacity: Double
    init(coordinate: CLLocationCoordinate2D, headingDeg: Double, opacity: Double) {
        self.coordinate = coordinate
        self.headingDeg = headingDeg
        self.opacity = opacity
    }
}

// MARK: - Annotation views

/// A small ICAO capsule marking a waypoint. `displayPriority = .required` so
/// MapKit never declutters it (parity with the SwiftUI `Annotation` it replaces).
final class RouteWaypointMarkerView: MKAnnotationView {
    static let reuseID = "routeWaypoint"
    private let label = UILabel()
    private let background = UIView()

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        canShowCallout = false
        displayPriority = .required
        collisionMode = .none
        background.backgroundColor = UIColor.secondarySystemBackground.withAlphaComponent(0.92)
        background.layer.cornerRadius = 6
        background.layer.borderWidth = 1
        background.layer.borderColor = UIColor.separator.cgColor
        label.font = .systemFont(ofSize: 11, weight: .semibold)
        label.textColor = .label
        label.textAlignment = .center
        addSubview(background)
        addSubview(label)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    /// Manual layout (no Auto Layout — annotation views resize awkwardly under it).
    func configure(text: String) {
        label.text = text
        label.sizeToFit()
        let padX: CGFloat = 6, padY: CGFloat = 3
        let w = label.bounds.width + padX * 2
        let h = label.bounds.height + padY * 2
        bounds = CGRect(x: 0, y: 0, width: w, height: h)
        background.frame = bounds
        label.frame = CGRect(x: padX, y: padY, width: label.bounds.width, height: label.bounds.height)
        centerOffset = .zero
    }
}

/// The shared active point: a translucent orange disc with a bold orange ring.
final class RouteActiveMarkerView: MKAnnotationView {
    static let reuseID = "routeActive"

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        canShowCallout = false
        isUserInteractionEnabled = false
        displayPriority = .required
        collisionMode = .none
        bounds = CGRect(x: 0, y: 0, width: 22, height: 22)
        backgroundColor = UIColor.systemOrange.withAlphaComponent(0.20)
        layer.cornerRadius = 11
        layer.borderColor = UIColor.systemOrange.cgColor
        layer.borderWidth = 3
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

/// The live aircraft glyph, rotated to track heading and faded by projection
/// confidence (opacity), matching the SwiftUI version it replaces.
final class RouteAircraftMarkerView: MKAnnotationView {
    static let reuseID = "routeAircraft"
    private let imageView = UIImageView()

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        canShowCallout = false
        isUserInteractionEnabled = false
        displayPriority = .required
        collisionMode = .none
        bounds = CGRect(x: 0, y: 0, width: 34, height: 34)
        let cfg = UIImage.SymbolConfiguration(pointSize: 26, weight: .bold)
        imageView.image = UIImage(systemName: "airplane", withConfiguration: cfg)
        imageView.tintColor = .systemOrange
        imageView.contentMode = .center
        imageView.frame = bounds
        addSubview(imageView)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    /// Rotate the glyph (heading is degrees true; the symbol points up = 90°) and
    /// fade it to the projection opacity.
    func apply(headingDeg: Double, opacity: Double) {
        imageView.transform = CGAffineTransform(rotationAngle: CGFloat((headingDeg - 90) * .pi / 180))
        alpha = CGFloat(opacity)
    }
}
