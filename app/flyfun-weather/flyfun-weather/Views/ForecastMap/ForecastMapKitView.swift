import MapKit
import SwiftUI
import UIKit

/// The forecast-map canvas: `MKMapView` via `UIViewRepresentable`, **no
/// clustering**. 619 `MKAnnotationView`s (reused, `displayPriority = .required`
/// so MapKit never declutters them) are comfortable where 619 SwiftUI
/// `Annotation`s would jank, and clustering is rejected on semantics — a cluster
/// can't be both "the best airport here" (where can I fly) and "the worst" (what's
/// dangerous), so every airport always shows (#420).
///
/// A metric or model switch is a **pure recolour** of the existing views — no
/// annotation rebuild, no refetch. Only a new payload (`payloadRevision` bump:
/// cold-open first load or a day/hour swap) rebuilds annotations.
struct ForecastMapKitView: UIViewRepresentable {
    let payload: ForecastMapResponse?
    let catalog: ForecastMapCatalog?
    let metric: String
    let mode: ForecastModelMode
    let selectedIcao: String?
    /// Revision of the current payload — annotations rebuild when this changes, so
    /// the nil→populated transition on cold open and every slot swap both
    /// repopulate. (Gating on a day/hour string missed the cold-open payload,
    /// which arrives *after* the first `updateUIView` for the same slot.)
    let payloadRevision: Int
    let initialRegion: MKCoordinateRegion
    let focusRequest: ForecastMapViewModel.FocusRequest?
    let onSelect: (String) -> Void
    let onFocusApplied: () -> Void
    /// The user panned/zoomed the map (freezes cold-open recentring).
    let onUserInteraction: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> MKMapView {
        let map = MKMapView()
        map.delegate = context.coordinator
        map.pointOfInterestFilter = .excludingAll
        map.showsUserLocation = true
        map.showsCompass = true
        map.register(AirportMarkerView.self,
                     forAnnotationViewWithReuseIdentifier: AirportMarkerView.reuseID)
        map.setRegion(initialRegion, animated: false)
        context.coordinator.map = map
        context.coordinator.currentDiameter = Coordinator.diameter(for: map)
        return map
    }

    func updateUIView(_ map: MKMapView, context: Context) {
        context.coordinator.update(parent: self)
    }

    // MARK: - Coordinator

    @MainActor
    final class Coordinator: NSObject {
        private var parent: ForecastMapKitView
        weak var map: MKMapView?
        private var renderedRevision: Int?
        private var appliedFocus: ForecastMapViewModel.FocusRequest?
        var currentDiameter: CGFloat = 14

        init(_ parent: ForecastMapKitView) {
            self.parent = parent
        }

        func update(parent: ForecastMapKitView) {
            self.parent = parent
            guard let map else { return }

            if renderedRevision != parent.payloadRevision {
                renderedRevision = parent.payloadRevision
                rebuildAnnotations(on: map)
            }
            recolorVisible(on: map)

            if let focus = parent.focusRequest, focus != appliedFocus {
                appliedFocus = focus
                apply(focus: focus, on: map)
                let cb = parent.onFocusApplied
                DispatchQueue.main.async { cb() }
            }
        }

        // MARK: Annotations

        private func rebuildAnnotations(on map: MKMapView) {
            let existing = map.annotations.compactMap { $0 as? ForecastAnnotation }
            map.removeAnnotations(existing)
            guard let airports = parent.payload?.airports else { return }
            map.addAnnotations(airports.map(ForecastAnnotation.init))
        }

        private func recolorVisible(on map: MKMapView) {
            for annotation in map.annotations {
                guard let a = annotation as? ForecastAnnotation,
                      let view = map.view(for: a) as? AirportMarkerView else { continue }
                configure(view, airport: a.airport)
            }
        }

        /// Fill + border encode (active metric colour + per-metric agreement ring).
        private func configure(_ view: AirportMarkerView, airport: ForecastAirport) {
            let fill = parent.catalog?.color(metric: parent.metric, airport: airport, mode: parent.mode)
                ?? ForecastMapCatalog.muted
            let isSelected = airport.icao == parent.selectedIcao

            let border: UIColor
            let weight: CGFloat
            if isSelected {
                border = UIColor(rgbHex: 0xfbbf24)  // amber
                weight = 4
            } else if parent.mode.isConsensus {
                // Ring = "the models disagree about the thing you're looking at".
                let label = airport.consensus(mode: parent.mode).agreement(forMetric: parent.metric)
                border = parent.catalog?.agreementColor(label) ?? ForecastMapCatalog.muted
                weight = 2
            } else {
                border = fill
                weight = 1
            }
            view.apply(fill: fill, border: border, borderWidth: weight,
                       diameter: currentDiameter, bringToFront: isSelected)
        }

        // MARK: Camera

        private func apply(focus: ForecastMapViewModel.FocusRequest, on map: MKMapView) {
            var center = focus.center
            let span = focus.span ?? map.region.span
            if focus.biasForSheet {
                // Nudge the airport up so it doesn't land under the bottom sheet.
                center.latitude -= span.latitudeDelta * 0.25
            }
            map.setRegion(MKCoordinateRegion(center: center, span: span), animated: true)
        }

        /// Marker diameter from the map's zoom (web `markerRadius`: 5px at z≤4 →
        /// 11px at z≥8), doubled to a diameter.
        static func diameter(for map: MKMapView) -> CGFloat {
            let span = map.region.span.longitudeDelta
            guard span > 0 else { return 14 }
            let zoom = log2(360 / span)
            let radius: CGFloat
            switch zoom {
            case ..<4.5: radius = 5
            case ..<5.5: radius = 6
            case ..<6.5: radius = 7
            case ..<7.5: radius = 9
            default: radius = 11
            }
            return radius * 2
        }
    }
}

// MapKit calls these on the main thread; `@preconcurrency` lets the `@MainActor`
// coordinator satisfy the (non-isolated) delegate protocol (same pattern as
// `FlightTrackingService`'s `CLLocationManagerDelegate`).
extension ForecastMapKitView.Coordinator: @preconcurrency MKMapViewDelegate {
    func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
        guard let a = annotation as? ForecastAnnotation else { return nil }  // user location → default
        guard let view = mapView.dequeueReusableAnnotationView(
            withIdentifier: AirportMarkerView.reuseID, for: a) as? AirportMarkerView else { return nil }
        view.annotation = a
        configure(view, airport: a.airport)
        return view
    }

    func mapView(_ mapView: MKMapView, regionWillChangeAnimated animated: Bool) {
        // Flag a user-initiated pan/zoom (an active gesture on the map's internal
        // container) so a late-resolving cold-open recentre doesn't fight it. Our
        // own `setRegion` moves carry no active gesture, so they don't trip this.
        guard let recognizers = mapView.subviews.first?.gestureRecognizers else { return }
        if recognizers.contains(where: { [.began, .changed, .ended].contains($0.state) }) {
            parent.onUserInteraction()
        }
    }

    func mapView(_ mapView: MKMapView, didSelect view: MKAnnotationView) {
        guard let a = view.annotation as? ForecastAnnotation else { return }
        // Drive selection purely from our VM, not MapKit's own selection state,
        // so re-tapping the same marker always re-fires.
        mapView.deselectAnnotation(a, animated: false)
        parent.onSelect(a.airport.icao)
    }

    func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) {
        let d = Self.diameter(for: mapView)
        guard d != currentDiameter else { return }
        currentDiameter = d
        // Resize visible markers to the new zoom (radius scales with zoom, as web).
        for annotation in mapView.annotations {
            guard let a = annotation as? ForecastAnnotation,
                  let v = mapView.view(for: a) as? AirportMarkerView else { continue }
            v.resize(diameter: d)
        }
    }
}

/// One airport marker annotation.
final class ForecastAnnotation: NSObject, MKAnnotation {
    let airport: ForecastAirport
    var coordinate: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: airport.lat, longitude: airport.lon) }
    var title: String? { airport.icao }

    init(_ airport: ForecastAirport) { self.airport = airport }
}

/// A filled circle marker with a coloured ring. `displayPriority = .required` so
/// MapKit never hides one behind another (no decluttering — every airport shows).
final class AirportMarkerView: MKAnnotationView {
    static let reuseID = "airportMarker"

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        displayPriority = .required
        collisionMode = .none
        canShowCallout = false
        centerOffset = .zero
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func apply(fill: UIColor, border: UIColor, borderWidth: CGFloat, diameter: CGFloat, bringToFront: Bool) {
        resize(diameter: diameter)
        backgroundColor = fill.withAlphaComponent(0.85)
        layer.borderColor = border.cgColor
        layer.borderWidth = borderWidth
        layer.zPosition = bringToFront ? 1000 : 0
    }

    /// Set the circle size (called on zoom change without recolouring).
    func resize(diameter: CGFloat) {
        bounds = CGRect(x: 0, y: 0, width: diameter, height: diameter)
        layer.cornerRadius = diameter / 2
        layer.masksToBounds = true
    }
}
