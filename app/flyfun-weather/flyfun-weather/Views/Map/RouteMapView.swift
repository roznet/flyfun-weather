import SwiftUI
import MapKit

/// Native MapKit map (§4.9). Earns its tab by being geographic — *where* along
/// the route a hazard sits, on real terrain. Tier 1: per-segment metric overlay
/// (colour + width from the shared MapMetrics registry); Tier 2: altitude slider
/// for level-dependent metrics; Tier 3: waypoint tap → conditions. MapKit's
/// native dark mode follows the app appearance (§1C). SIGMET/hazard shading
/// deferred.
struct RouteMapView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var mapVM = RouteMapViewModel()
    @State private var metricId = "cloud-cover-total"
    @State private var altitudeFt: Double = 5000
    @State private var altitudeInitialized = false
    @State private var selectedWaypoint: WaypointConditions?

    private var metric: MapMetric? { MapMetrics.metric(byId: metricId) }

    /// Viz points (with lat/lon + metric fields) for the selected model — reuses
    /// the cross-section's extraction so the map shares the same data contract.
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
        .task {
            if case .loaded(let analyses) = viewModel.routeAnalysesState { mapVM.update(from: analyses) }
            else if case .loaded(let snapshot) = viewModel.snapshotState { mapVM.update(from: snapshot) }
            if !altitudeInitialized {
                altitudeFt = Double(viewModel.flight.cruiseAltitudeFt)
                altitudeInitialized = true
            }
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

    // MARK: Controls (metric picker · legend · altitude slider)

    private var controls: some View {
        VStack(spacing: Theme.spacingS) {
            HStack {
                Menu {
                    ForEach(MapMetrics.all) { m in
                        Button(m.label) { metricId = m.id }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "paintpalette")
                        Text(metric?.label ?? "Metric")
                        Image(systemName: "chevron.down").font(.caption2)
                    }
                    .font(.caption.weight(.medium))
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(.ultraThinMaterial, in: Capsule())
                }
                Spacer()
            }

            if let metric {
                legend(metric)
                if metric.altitudeDependent {
                    altitudeSlider
                }
            }
            Spacer()
        }
        .padding(Theme.spacingM)
    }

    private func legend(_ metric: MapMetric) -> some View {
        HStack(spacing: Theme.spacingS) {
            ForEach(metric.legend, id: \.label) { stop in
                HStack(spacing: 3) {
                    RoundedRectangle(cornerRadius: 2).fill(stop.color).frame(width: 14, height: 6)
                    Text(stop.label).font(.tabularData(.caption2)).foregroundStyle(Theme.text)
                }
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(.ultraThinMaterial, in: Capsule())
    }

    private var altitudeSlider: some View {
        let ceiling = vizData?.flightCeilingFt ?? 18000
        return HStack(spacing: Theme.spacingS) {
            Text("FL\(Int(altitudeFt / 100))")
                .font(.tabularData(.caption2)).foregroundStyle(Theme.text)
                .frame(width: 52, alignment: .leading)
            Slider(value: $altitudeFt, in: 0...ceiling, step: 500)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
    }

    // MARK: Segment building

    private struct MapSegment: Identifiable {
        let id: Int
        let coords: [CLLocationCoordinate2D]
        let color: Color
        let width: Double
    }

    private func segments() -> [MapSegment] {
        guard let metric, let points = vizData?.points, points.count >= 2 else { return [] }
        let altParam: Double? = metric.altitudeDependent ? altitudeFt : nil
        var out: [MapSegment] = []
        for i in 0..<(points.count - 1) {
            let p = points[i]
            guard let value = metric.getValue(p, altParam) else { continue }
            out.append(MapSegment(
                id: i,
                coords: [
                    CLLocationCoordinate2D(latitude: p.lat, longitude: p.lon),
                    CLLocationCoordinate2D(latitude: points[i + 1].lat, longitude: points[i + 1].lon),
                ],
                color: metric.color(value),
                width: metric.width(value)
            ))
        }
        return out
    }

    // MARK: Waypoint conditions (Tier 3)

    private func selectWaypoint(_ wp: RouteMapViewModel.WaypointAnnotation) {
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
