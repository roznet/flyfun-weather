import SwiftUI
import MapKit

/// Native MapKit map (§4.9). Earns its tab by being geographic — *where* along
/// the route a hazard sits, on real terrain. Tier 1: per-segment metric overlay
/// (colour + width from the shared MapMetrics registry); Tier 2: altitude slider
/// for level-dependent metrics; Tier 3: waypoint tap → conditions. On iPad
/// (regular width) the colour and width axes get separate metric pickers; on
/// iPhone one metric drives both (§4.9). MapKit's native dark mode follows the
/// app appearance (§1C). SIGMET/hazard shading deferred.
struct RouteMapView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var mapVM = RouteMapViewModel()
    @State private var colorMetricId = "cloud-cover-total"
    @State private var widthMetricId = "cloud-cover-total"
    /// nil = not yet initialised (falls back to cruise altitude). Never overload
    /// 0 as "uninitialised" — FL000 is a legitimate slider value.
    @State private var altitudeFt: Double?
    @State private var selectedWaypoint: WaypointConditions?
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    /// iPad (regular width) drives colour and width from independent metrics.
    private var usesDualMetrics: Bool { horizontalSizeClass == .regular }

    private var colorMetric: MapMetric? { MapMetrics.metric(byId: colorMetricId) }
    /// On compact the single (colour) metric also drives width.
    private var widthMetric: MapMetric? {
        MapMetrics.metric(byId: usesDualMetrics ? widthMetricId : colorMetricId)
    }

    /// Altitude used for level-dependent metrics — the slider value or, until the
    /// user touches it, the flight's cruise altitude.
    private var effectiveAltitudeFt: Double {
        altitudeFt ?? Double(viewModel.flight.cruiseAltitudeFt)
    }

    private var flightCeilingFt: Double {
        vizData?.flightCeilingFt ?? Double(viewModel.flight.flightCeilingFt)
    }

    private var needsAltitude: Bool {
        (colorMetric?.altitudeDependent ?? false)
            || (usesDualMetrics && (widthMetric?.altitudeDependent ?? false))
    }

    private var altitudeBinding: Binding<Double> {
        Binding(get: { effectiveAltitudeFt }, set: { altitudeFt = $0 })
    }

    /// Viz points (with lat/lon + metric fields) for the selected model — reuses
    /// the cross-section's extraction so the map shares the same data contract
    /// (single cloud-cover source/formula, no map-only re-derivation).
    private var vizData: VizRouteData? {
        guard case .loaded(let analyses) = viewModel.routeAnalysesState else { return nil }
        var elevation: ElevationResponse?
        if case .loaded(let e) = viewModel.elevationState { elevation = e }
        return CrossSectionViewModel.extractVizData(from: analyses, model: viewModel.selectedModel, elevation: elevation)
    }

    var body: some View {
        ZStack(alignment: .top) {
            if mapVM.routeCoordinates.isEmpty {
                placeholder
            } else {
                mapContent
                controls
            }
        }
        .onChange(of: viewModel.snapshotState.isLoaded) {
            if case .loaded(let snapshot) = viewModel.snapshotState { mapVM.update(from: snapshot) }
        }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) {
            if case .loaded(let analyses) = viewModel.routeAnalysesState { mapVM.update(from: analyses) }
        }
        .onChange(of: viewModel.focusIntent) { applyFocusIntent() }
        .task {
            if case .loaded(let analyses) = viewModel.routeAnalysesState { mapVM.update(from: analyses) }
            else if case .loaded(let snapshot) = viewModel.snapshotState { mapVM.update(from: snapshot) }
            applyFocusIntent()
        }
        .sheet(item: $selectedWaypoint) { waypointSheet($0) }
    }

    // MARK: Map

    private var mapContent: some View {
        let _ = trackingService.locationUpdateCount
        let isTracking = trackingService.isTracking
        let aircraftLocation = trackingService.currentLocation
        let aircraftOpacity = trackingService.projectedPosition?.opacity ?? 0.3
        let aircraftHeading = trackingService.projectedPosition?.headingDeg ?? 0
        let segs = segments()
        let activeCoord = activePointCoordinate

        return Map(initialPosition: .region(mapVM.mapRegion)) {
            // Base route line under the metric segments.
            MapPolyline(coordinates: mapVM.routeCoordinates)
                .stroke(.gray.opacity(0.4), lineWidth: 2)

            // Metric-coloured segments (colour + width = the metric).
            ForEach(segs) { seg in
                MapPolyline(coordinates: seg.coords)
                    .stroke(seg.color, style: StrokeStyle(lineWidth: seg.width, lineCap: .round))
            }

            ForEach(mapVM.waypoints) { wp in
                Annotation(wp.id, coordinate: wp.coordinate) {
                    Button { selectWaypoint(wp) } label: {
                        VStack(spacing: 2) {
                            Text(wp.id)
                                .font(.caption2.bold())
                                .padding(.horizontal, 4).padding(.vertical, 2)
                                .background(.ultraThinMaterial, in: Capsule())
                            Circle().fill(Theme.primary).frame(width: 8, height: 8)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }

            // Shared active point (§4.7) — reflects the scrub point set on the
            // cross-section / Skew-T or by tapping a waypoint here.
            if let activeCoord {
                Annotation("", coordinate: activeCoord, anchor: .center) {
                    ZStack {
                        Circle().fill(.orange.opacity(0.20))
                        Circle().stroke(.orange, lineWidth: 3)
                    }
                    .frame(width: 22, height: 22)
                }
                .annotationTitles(.hidden)
            }

            if isTracking, let location = aircraftLocation {
                Annotation("", coordinate: location.coordinate, anchor: .center) {
                    Image(systemName: "airplane")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundStyle(.orange)
                        .rotationEffect(.degrees(aircraftHeading - 90))
                        .opacity(aircraftOpacity)
                        .shadow(color: .black.opacity(0.5), radius: 3)
                }
                .annotationTitles(.hidden)
            }
        }
        .mapStyle(.standard)  // follows the app's light/dark appearance natively
    }

    // MARK: Controls (metric picker(s) · legend · altitude slider)

    private var controls: some View {
        VStack(spacing: Theme.spacingS) {
            HStack(spacing: Theme.spacingS) {
                metricMenu(title: "Colour", systemImage: "paintpalette", selection: $colorMetricId)
                if usesDualMetrics {
                    metricMenu(title: "Width", systemImage: "lineweight", selection: $widthMetricId)
                }
                Spacer()
            }

            if let colorMetric {
                legend(colorMetric)
                if needsAltitude { altitudeSlider }
            }
            Spacer()
        }
        .padding(Theme.spacingM)
    }

    private func metricMenu(title: String, systemImage: String, selection: Binding<String>) -> some View {
        Menu {
            ForEach(MapMetrics.all) { m in
                Button(m.label) { selection.wrappedValue = m.id }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: systemImage)
                Text(MapMetrics.metric(byId: selection.wrappedValue)?.label ?? title)
                Image(systemName: "chevron.down").font(.caption2)
            }
            .font(.caption.weight(.medium))
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(.ultraThinMaterial, in: Capsule())
        }
    }

    private func legend(_ metric: MapMetric) -> some View {
        VStack(spacing: 4) {
            HStack(spacing: Theme.spacingS) {
                ForEach(metric.legend, id: \.label) { stop in
                    HStack(spacing: 3) {
                        RoundedRectangle(cornerRadius: 2).fill(stop.color).frame(width: 14, height: 6)
                        Text(stop.label).font(.tabularData(.caption2)).foregroundStyle(Theme.text)
                    }
                }
            }
            // In dual-metric mode, name the (different) width axis below the
            // colour legend so the two encodings stay legible.
            if usesDualMetrics, let widthMetric, widthMetric.id != metric.id {
                Text("Width: \(widthMetric.label)")
                    .font(.tabularData(.caption2)).foregroundStyle(Theme.textMuted)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(.ultraThinMaterial, in: Capsule())
    }

    private var altitudeSlider: some View {
        let ceiling = flightCeilingFt
        return HStack(spacing: Theme.spacingS) {
            Text("FL\(Int(effectiveAltitudeFt / 100))")
                .font(.tabularData(.caption2)).foregroundStyle(Theme.text)
                .frame(width: 52, alignment: .leading)
            Slider(value: altitudeBinding, in: 0...max(ceiling, 500), step: 500)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
    }

    // MARK: Deep-link focus (§4.9)

    /// Consume a map-targeted focus intent: select the metric, scrub the
    /// altitude, then clear the intent so it fires once.
    private func applyFocusIntent() {
        guard let intent = viewModel.focusIntent, intent.target == .map else { return }
        if let id = intent.mapMetricId, MapMetrics.metric(byId: id) != nil {
            colorMetricId = id
            if !usesDualMetrics { widthMetricId = id }
        }
        if let alt = intent.altitudeFt {
            altitudeFt = min(flightCeilingFt, max(0, alt))
        }
        viewModel.clearFocusIntent()
    }

    // MARK: Segment building

    private struct MapSegment: Identifiable {
        let id: Int
        let coords: [CLLocationCoordinate2D]
        let color: Color
        let width: Double
    }

    private func segments() -> [MapSegment] {
        guard let colorMetric, let points = vizData?.points, points.count >= 2 else { return [] }
        let widthMetric = self.widthMetric
        let colorAlt: Double? = colorMetric.altitudeDependent ? effectiveAltitudeFt : nil
        let widthAlt: Double? = (widthMetric?.altitudeDependent ?? false) ? effectiveAltitudeFt : nil

        var out: [MapSegment] = []
        for i in 0..<(points.count - 1) {
            let a = points[i], b = points[i + 1]
            // Risk/ordinal metrics take the worst endpoint (never average the
            // peak away); continuous metrics average — see MapMetric.aggregation.
            guard let colorValue = colorMetric.segmentValue(a, b, altitudeFt: colorAlt) else { continue }
            let widthValue = widthMetric?.segmentValue(a, b, altitudeFt: widthAlt)
            out.append(MapSegment(
                id: i,
                coords: [
                    CLLocationCoordinate2D(latitude: a.lat, longitude: a.lon),
                    CLLocationCoordinate2D(latitude: b.lat, longitude: b.lon),
                ],
                color: colorMetric.color(colorValue),
                width: widthMetric.flatMap { m in widthValue.map { m.width($0) } } ?? 8
            ))
        }
        return out
    }

    // MARK: Active point

    /// Coordinate of the shared active route point, resolved from the route
    /// analyses by `pointIndex`.
    private var activePointCoordinate: CLLocationCoordinate2D? {
        guard let idx = viewModel.activePointIndex,
              case .loaded(let analyses) = viewModel.routeAnalysesState,
              let p = analyses.analyses.first(where: { $0.pointIndex == idx })
        else { return nil }
        return CLLocationCoordinate2D(latitude: p.lat, longitude: p.lon)
    }

    // MARK: Waypoint conditions (Tier 3)

    private func selectWaypoint(_ wp: RouteMapViewModel.WaypointAnnotation) {
        // Move the shared active point to this waypoint's route point so the
        // cross-section / Skew-T follow (§4.7).
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            let match = analyses.analyses.first { $0.waypointIcao == wp.id }
                ?? analyses.analyses.min { haversine($0.lat, $0.lon, wp.coordinate) < haversine($1.lat, $1.lon, wp.coordinate) }
            if let match { viewModel.activePointIndex = match.pointIndex }
        }

        // Conditions from the nearest viz point to the waypoint.
        let nearest = vizData?.points.min {
            haversine($0.lat, $0.lon, wp.coordinate) < haversine($1.lat, $1.lon, wp.coordinate)
        }
        selectedWaypoint = WaypointConditions(
            id: wp.id, name: wp.name,
            temperatureC: nearest?.temperatureC,
            cloudCoverPct: nearest?.cloudCoverTotalPct,
            headwindKt: nearest?.headwindKt,
            crosswindKt: nearest?.crosswindKt,
            ceilingFt: nearest?.nwpCloudDiag?.ceilingFt
        )
    }

    private func haversine(_ lat: Double, _ lon: Double, _ c: CLLocationCoordinate2D) -> Double {
        let dlat = lat - c.latitude, dlon = lon - c.longitude
        return dlat * dlat + dlon * dlon  // squared euclidean is enough for nearest
    }

    private func waypointSheet(_ wc: WaypointConditions) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingM) {
            Text(wc.id).font(.title2.bold()).foregroundStyle(Theme.text)
            if wc.name != wc.id { Text(wc.name).font(.subheadline).foregroundStyle(Theme.textMuted) }
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                if let t = wc.temperatureC { conditionRow("Temperature", "\(Int(t))°C") }
                if let c = wc.cloudCoverPct { conditionRow("Cloud cover", "\(Int(c))%") }
                if let hw = wc.headwindKt { conditionRow("Head/Tailwind", "\(abs(Int(hw))) kt \(hw >= 0 ? "HW" : "TW")") }
                if let xw = wc.crosswindKt { conditionRow("Crosswind", "\(abs(Int(xw))) kt") }
                if let ceil = wc.ceilingFt { conditionRow("Ceiling", "\(Int(ceil)) ft") }
            }
            Spacer()
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bg)
        .presentationDetents([.height(280), .medium])
    }

    private func conditionRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.subheadline).foregroundStyle(Theme.textMuted)
            Spacer()
            Text(value).font(.tabularData(.subheadline)).foregroundStyle(Theme.text)
        }
    }

    @ViewBuilder
    private var placeholder: some View {
        switch viewModel.snapshotState {
        case .loading: ProgressView("Loading map data...")
        case .error(let error):
            ContentUnavailableView("Map Unavailable", systemImage: "map", description: Text(error.localizedDescription))
        default: ProgressView()
        }
    }
}

/// Conditions shown when a waypoint is tapped.
private struct WaypointConditions: Identifiable {
    var id: String
    let name: String
    let temperatureC: Double?
    let cloudCoverPct: Double?
    let headwindKt: Double?
    let crosswindKt: Double?
    let ceilingFt: Double?
}

// Helper to detect state changes
extension LoadingState {
    var isLoaded: Bool {
        if case .loaded = self { return true }
        return false
    }
}
