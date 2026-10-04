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
/// Layer order (bottom → top): base tiles · observed imagery tiles (satellite IR
/// under radar, #654) · corridor box · route overlays (grey base line + the
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

    // MARK: Airport-forecast overlay (#428)
    //
    // The per-airport forecast markers for the flight's nearest snapshot time,
    // reusing the forecast map's `ForecastAnnotation` / `AirportMarkerView` and
    // colour catalog so the two views can't disagree. Empty / hidden in commit 1.

    /// Watchlist airports to draw (empty ⇒ nothing). Already filtered to the
    /// current slot by `RouteMapView`; drawn only when `showForecastOverlay`.
    let forecastAirports: [ForecastAirport]
    /// Served colour/legend catalog; nil ⇒ overlay can't colour, so it's skipped.
    let forecastCatalog: ForecastMapCatalog?
    /// Active overlay metric (a `FORECAST_METRICS` id), independent of the
    /// route-segment colour metric.
    let forecastMetric: String
    /// The briefing's selected individual model (gfs/icon/ecmwf) — the overlay
    /// follows it, as on the web.
    let forecastModel: String
    /// User show/hide preference AND within-horizon+model-supported gating.
    let showForecastOverlay: Bool
    /// Bumped when the snapshot payload changes; markers rebuild on change. A
    /// metric/model switch leaves it and is a pure recolour.
    let forecastRevision: Int

    // MARK: Observed imagery (#654)
    //
    // Radar / satellite tiles from `/api/observed/tiles`, already resolved to the
    // frames to draw by `ObservedMapImagery.compose` — this view only reconciles
    // them onto the map. Defaults draw nothing.

    /// Tile layers to draw, underlay first. An unchanged `key` keeps its overlay.
    var observedLayers: [ObservedMapImagery.TileLayerSpec] = []
    /// Fetches one tile's bytes through the API client (bearer auth).
    var observedTileFetcher: (@Sendable (String) async throws -> Data)?
    /// Corridor box the sampled numbers describe, or nil.
    var observedCorridor: ObservedMapImagery.LatLonBox?
    /// Quiet base map while observed imagery is drawn (web parity).
    var mutedBaseMap = false

    // MARK: Radar cells (#661)

    /// The cell overlay to draw (cores only — outlines, markers, arrows), or nil.
    var cellsDisplay: CellDisplay?
    /// Identity of `cellsDisplay` (its request path): overlays rebuild only when
    /// it changes, so an altitude drag keeps an open callout.
    var cellsDisplayKey: String?

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
        // Airport-forecast markers reuse the forecast map's view class (#428).
        map.register(AirportMarkerView.self,
                     forAnnotationViewWithReuseIdentifier: AirportMarkerView.reuseID)
        map.register(CellMarkerView.self,
                     forAnnotationViewWithReuseIdentifier: CellMarkerView.reuseID)
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

        // Airport-forecast overlay state (#428).
        private var renderedForecastRevision: Int?
        private var appliedForecastColorKey: ForecastColorKey?
        private var currentForecastDiameter: CGFloat = 12
        /// Whether airport markers are currently on the map — so a re-show after
        /// the model regained data rebuilds even when the payload is unchanged.
        private var markersShown = false

        // Observed imagery state (#654).
        /// Tile overlays currently wanted, by spec key (source + frame stamp).
        private var liveTiles: [String: ObservedTileOverlay] = [:]
        /// Previous frames kept on screen until their successor has had time to load.
        private var retiringTiles: [ObservedTileOverlay] = []
        private var corridorOverlay: ObservedCorridorOverlay?
        private var appliedMuted: Bool?
        /// Key of the cell overlay currently drawn (nil = none).
        private var renderedCellsKey: String?
        /// How long an old frame stays under its successor. MapKit gives no
        /// "all tiles loaded" signal (the web waits for Leaflet's `load`, capped
        /// at 10 s), so a fixed delay stops the map blanking once per radar cycle.
        static let frameSwapDelay: Duration = .seconds(4)

        /// The inputs the airport markers colour from — a recolour is only needed
        /// when one of these changes (not on every unrelated re-render).
        private struct ForecastColorKey: Equatable {
            let metric: String
            let model: String
            let visible: Bool
        }

        init(_ parent: RouteMapKitView) { self.parent = parent }

        func update(parent: RouteMapKitView) {
            self.parent = parent
            guard let map else { return }
            updateBaseMap(on: map)
            updateObservedTiles(on: map)
            updateCorridor(on: map)
            updateRoute(on: map)
            updateCells(on: map)
            updateForecastOverlay(on: map)
            updateWaypoints(on: map)
            updateActivePoint(on: map)
            updateAircraft(on: map)
        }

        // MARK: Route overlays

        private func updateRoute(on map: MKMapView) {
            guard renderedRouteSignature != parent.routeSignature else { return }
            renderedRouteSignature = parent.routeSignature
            // Only the route's own lines: the observed tiles and corridor box
            // persist across route rebuilds (an altitude drag re-fetches nothing).
            map.removeOverlays(map.overlays.filter { $0 is ColoredPolyline })

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

        // MARK: Observed imagery (#654)

        private func updateBaseMap(on map: MKMapView) {
            guard appliedMuted != parent.mutedBaseMap else { return }
            appliedMuted = parent.mutedBaseMap
            let configuration = MKStandardMapConfiguration(
                emphasisStyle: parent.mutedBaseMap ? .muted : .default)
            // A new configuration carries its own POI filter: keep `makeUIView`'s.
            configuration.pointOfInterestFilter = .excludingAll
            map.preferredConfiguration = configuration
        }

        /// Keep overlays whose frame is still wanted (updating opacity), add new
        /// ones in stacking order, retire the rest. A new radar frame is a new
        /// key, so it swaps; the old one stays under it briefly.
        private func updateObservedTiles(on map: MKMapView) {
            let wanted = parent.observedTileFetcher == nil ? [] : parent.observedLayers
            let wantedKeys = Set(wanted.map(\.key))
            var added: [ObservedTileOverlay] = []

            for spec in wanted {
                if let existing = liveTiles[spec.key] {
                    setOpacity(CGFloat(spec.opacity), of: existing, on: map)
                    continue
                }
                guard let fetch = parent.observedTileFetcher else { continue }
                let overlay = ObservedTileOverlay(spec: spec, fetch: fetch)
                let predecessor = liveTiles.values.first {
                    $0.spec.source == spec.source && !wantedKeys.contains($0.spec.key)
                }
                if let predecessor {
                    // Same layer, newer frame: directly above the frame it replaces.
                    map.insertOverlay(overlay, above: predecessor)
                } else if !spec.isUnderlay,
                          let underlay = (Array(liveTiles.values) + added).last(where: { $0.spec.isUnderlay }) {
                    map.insertOverlay(overlay, above: underlay)
                } else {
                    // Bottom of the level: under the corridor box and route lines
                    // (and, for the satellite, under any radar already drawn).
                    map.insertOverlay(overlay, at: 0, level: .aboveRoads)
                }
                added.append(overlay)
                liveTiles[spec.key] = overlay
            }

            for (key, overlay) in liveTiles where !wantedKeys.contains(key) {
                liveTiles.removeValue(forKey: key)
                let hasSuccessor = added.contains { $0.spec.source == overlay.spec.source }
                if hasSuccessor {
                    retire(overlay, on: map)
                } else {
                    map.removeOverlay(overlay)
                }
            }
        }

        private func retire(_ overlay: ObservedTileOverlay, on map: MKMapView) {
            retiringTiles.append(overlay)
            Task { @MainActor [weak self, weak map] in
                try? await Task.sleep(for: Self.frameSwapDelay)
                map?.removeOverlay(overlay)
                self?.retiringTiles.removeAll { $0 === overlay }
            }
        }

        private func setOpacity(_ opacity: CGFloat, of overlay: ObservedTileOverlay, on map: MKMapView) {
            guard overlay.opacity != opacity else { return }
            overlay.opacity = opacity
            map.renderer(for: overlay)?.alpha = opacity
        }

        private func updateCorridor(on map: MKMapView) {
            guard corridorOverlay?.box != parent.observedCorridor else { return }
            if let old = corridorOverlay { map.removeOverlay(old) }
            corridorOverlay = nil
            guard let box = parent.observedCorridor else { return }
            let overlay = ObservedCorridorOverlay.make(box)
            corridorOverlay = overlay
            // Above the imagery, below the route lines (re-added on top later).
            if let topTile = map.overlays.last(where: { $0 is ObservedTileOverlay }) {
                map.insertOverlay(overlay, above: topTile)
            } else {
                map.insertOverlay(overlay, at: 0, level: .aboveRoads)
            }
        }

        // MARK: Radar cells (#661)

        /// Rebuild the cell layers when the overlay changes. Outlines and arrows
        /// sit at `.aboveLabels`, over the route (web: the cells pane is above
        /// the route); markers are annotations, always on top.
        private func updateCells(on map: MKMapView) {
            let key = parent.cellsDisplay == nil ? nil : parent.cellsDisplayKey
            guard key != renderedCellsKey else { return }
            renderedCellsKey = key
            map.removeOverlays(map.overlays.filter { $0 is CellOutlineOverlay || $0 is CellArrowOverlay })
            map.removeAnnotations(map.annotations.filter { $0 is CellAnnotation })
            guard let display = parent.cellsDisplay else { return }

            // Outlines first (core35 under core41); rain areas are not drawn
            // on the route map (web parity).
            for tier in ["core35", "core41"] {
                for line in display.outlines?[tier] ?? [] {
                    let coords = line.compactMap { p in
                        p.count == 2 ? CLLocationCoordinate2D(latitude: p[0], longitude: p[1]) : nil
                    }
                    guard coords.count >= 2 else { continue }
                    let overlay = CellOutlineOverlay(coordinates: coords, count: coords.count)
                    overlay.tier = tier
                    map.addOverlay(overlay, level: .aboveLabels)
                }
            }
            let cores = display.cells.filter(\.isCore)
            for cell in cores {
                guard let end = CellsOverlay.arrowEnd(cell) else { continue }
                let coords = [CLLocationCoordinate2D(latitude: cell.lat, longitude: cell.lon), end]
                map.addOverlay(CellArrowOverlay(coordinates: coords, count: 2), level: .aboveLabels)
            }
            map.addAnnotations(cores.map(CellAnnotation.init))
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

        // MARK: Airport-forecast overlay (#428)

        private func updateForecastOverlay(on map: MKMapView) {
            let wantMarkers = parent.showForecastOverlay && !parent.forecastAirports.isEmpty
            let payloadChanged = renderedForecastRevision != parent.forecastRevision
            renderedForecastRevision = parent.forecastRevision

            // Tear down when not wanted (toggled off, or the selected model lost
            // data for this day). Rebuild when wanted and either the payload
            // changed (new slot) or the markers aren't currently on the map (first
            // show, or re-show after the model regained data). Otherwise leave the
            // ~620 annotations in place — a metric/model change is a pure recolour.
            if !wantMarkers {
                if markersShown {
                    let existing = map.annotations.compactMap { $0 as? ForecastAnnotation }
                    map.removeAnnotations(existing)
                    markersShown = false
                }
                return
            }
            if payloadChanged || !markersShown {
                let existing = map.annotations.compactMap { $0 as? ForecastAnnotation }
                map.removeAnnotations(existing)
                map.addAnnotations(parent.forecastAirports.map(ForecastAnnotation.init))
                markersShown = true
                appliedForecastColorKey = nil  // fresh markers must be coloured
            }

            currentForecastDiameter = forecastDiameter(for: map)
            let colorKey = ForecastColorKey(metric: parent.forecastMetric,
                                            model: parent.forecastModel, visible: true)
            if colorKey != appliedForecastColorKey {
                appliedForecastColorKey = colorKey
                recolorForecast(on: map)
            }
        }

        /// Resize the airport dots to a new zoom without recolouring (web parity).
        func resizeForecastMarkers(on map: MKMapView) {
            let d = forecastDiameter(for: map)
            guard d != currentForecastDiameter else { return }
            currentForecastDiameter = d
            for annotation in map.annotations {
                guard let a = annotation as? ForecastAnnotation,
                      let v = map.view(for: a) as? AirportMarkerView else { continue }
                v.resize(diameter: d)
            }
        }

        private func recolorForecast(on map: MKMapView) {
            let mode = ForecastModelMode.model(parent.forecastModel)
            for annotation in map.annotations {
                guard let a = annotation as? ForecastAnnotation,
                      let view = map.view(for: a) as? AirportMarkerView else { continue }
                configureForecast(view, airport: a.airport, mode: mode)
            }
        }

        /// One airport marker: the metric fill for the briefing's selected model,
        /// with a matching hairline border (no agreement ring — that's a consensus
        /// concept and the briefing overlay is always an individual model).
        func configureForecast(_ view: AirportMarkerView, airport: ForecastAirport, mode: ForecastModelMode) {
            let fill = parent.forecastCatalog?.color(metric: parent.forecastMetric, airport: airport, mode: mode)
                ?? ForecastMapCatalog.muted
            // Non-interactive on the briefing map so an airport dot never swallows
            // a tap meant for a waypoint (the full forecast map owns tap-to-card).
            view.isEnabled = false
            view.apply(fill: fill, border: fill, borderWidth: 1,
                       diameter: currentForecastDiameter, bringToFront: false)
        }

        /// Marker diameter from zoom — one step smaller than the full forecast
        /// map's so the airport dots stay secondary to the route (web parity).
        /// Shares the zoom formula with the forecast map via `AirportMarkerSizing`.
        func forecastDiameter(for map: MKMapView) -> CGFloat {
            AirportMarkerSizing.diameter(for: map, radii: [4, 5, 6, 8, 10], fallback: 12)
        }
    }
}

// MapKit calls the delegate on the main thread; the `@MainActor` coordinator
// satisfies the non-isolated delegate protocol through an inferred isolated
// conformance (same pattern as `ForecastMapKitView` / `FlightTrackingService`).
extension RouteMapKitView.Coordinator: MKMapViewDelegate {
    func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
        if let tiles = overlay as? ObservedTileOverlay {
            let r = MKTileOverlayRenderer(tileOverlay: tiles)
            r.alpha = tiles.opacity
            return r
        }
        if let outline = overlay as? CellOutlineOverlay {
            let style = CellsOverlay.outlineStyle(outline.tier)
            let r = MKPolylineRenderer(polyline: outline)
            r.strokeColor = style.color.withAlphaComponent(0.95)
            r.lineWidth = style.width
            return r
        }
        if let arrow = overlay as? CellArrowOverlay {
            let r = CellArrowRenderer(polyline: arrow)
            r.strokeColor = CellsOverlay.arrowColor
            r.lineWidth = 2.5
            r.lineCap = .round
            return r
        }
        if let corridor = overlay as? ObservedCorridorOverlay {
            let r = MKPolygonRenderer(polygon: corridor)
            r.strokeColor = UIColor(red: 0x25 / 255, green: 0x63 / 255, blue: 0xEB / 255, alpha: 0.45)
            r.lineWidth = 1
            r.lineDashPattern = [5, 4]
            r.fillColor = nil
            return r
        }
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
        case let cell as CellAnnotation:
            let view = mapView.dequeueReusableAnnotationView(
                withIdentifier: CellMarkerView.reuseID, for: cell)
            (view as? CellMarkerView)?.configure(cell.cell)
            return view
        case let apt as ForecastAnnotation:
            let view = mapView.dequeueReusableAnnotationView(
                withIdentifier: AirportMarkerView.reuseID, for: apt)
            if let marker = view as? AirportMarkerView {
                configureForecast(marker, airport: apt.airport, mode: .model(parent.forecastModel))
            }
            return view
        default:
            return nil
        }
    }

    func mapView(_ mapView: MKMapView, didSelect view: MKAnnotationView) {
        guard let wp = view.annotation as? RouteWaypointAnnotation else { return }
        // Drive selection from our callback, not MapKit's own selection state, so
        // re-tapping the same waypoint always re-fires. Airport-forecast dots are
        // non-interactive here (the full forecast map owns tap-to-card).
        mapView.deselectAnnotation(wp, animated: false)
        parent.onSelectWaypoint(wp.icao)
    }

    func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) {
        // Keep the airport dots legibly sized as the user zooms (web parity).
        resizeForecastMarkers(on: mapView)
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
