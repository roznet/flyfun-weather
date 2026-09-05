import SwiftUI
import MapKit
import UIKit

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
    /// Cached extraction (refreshed on model/analyses/elevation change) so slider
    /// drags don't re-run the full route-analysis mapping on every render.
    @State private var vizData: VizRouteData?
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(AppState.self) private var appState

    // Airport-forecast overlay (#428): the same per-airport markers the full
    // forecast map draws, for the snapshot nearest the flight time.
    /// Day grid + per-slot snapshot; created on appear from the repository.
    @State private var overlayModel: RouteForecastOverlayModel?
    /// Persisted overlay prefs (mirror the web localStorage keys). Default on.
    @AppStorage("mapForecastOverlayVisible") private var forecastOverlayVisible = true
    @AppStorage("mapForecastMetric") private var forecastMetricRaw = "flight_category"

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
    private func refreshVizData() {
        guard case .loaded(let analyses) = viewModel.routeAnalysesState else { vizData = nil; return }
        var elevation: ElevationResponse?
        if case .loaded(let e) = viewModel.elevationState { elevation = e }
        vizData = CrossSectionViewModel.extractVizData(from: analyses, model: viewModel.selectedModel, elevation: elevation)
    }

    var body: some View {
        ZStack(alignment: .top) {
            if mapVM.routeCoordinates.isEmpty {
                placeholder
            } else {
                mapContent
            }
            if viewModel.observedMotionState.modeEnabled {
                ObservedMotionView(
                    state: viewModel.observedMotionState,
                    refreshCapability: { await viewModel.refreshObservedMotionCapability() },
                    leaveMode: { viewModel.leaveObservedMotionMode() })
            } else {
                if !mapVM.routeCoordinates.isEmpty { controls }
                Button {
                    viewModel.enterObservedMotionMode()
                } label: {
                    Label("Experimental motion", systemImage: "move.3d")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(.ultraThinMaterial, in: Capsule())
                }
                .buttonStyle(.plain)
                .padding(Theme.spacingM)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .accessibilityIdentifier("observedMotionMode")
            }
        }
        .onChange(of: viewModel.snapshotState.isLoaded) {
            if case .loaded(let snapshot) = viewModel.snapshotState { mapVM.update(from: snapshot) }
        }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) {
            if case .loaded(let analyses) = viewModel.routeAnalysesState { mapVM.update(from: analyses) }
            refreshVizData()
        }
        .onChange(of: viewModel.selectedModel) { refreshVizData() }
        .onChange(of: viewModel.elevationState.isLoaded) { refreshVizData() }
        .onChange(of: viewModel.focusIntent) { applyFocusIntent() }
        .task {
            if case .loaded(let analyses) = viewModel.routeAnalysesState { mapVM.update(from: analyses) }
            else if case .loaded(let snapshot) = viewModel.snapshotState { mapVM.update(from: snapshot) }
            refreshVizData()
            applyFocusIntent()
        }
        .sheet(item: $selectedWaypoint) { waypointSheet($0) }
        .task {
            // Create the overlay model + lazy-load the server day grid once; when it
            // lands, `overlaySlot` resolves and the slice task below fires.
            if overlayModel == nil, let repo = appState.repository {
                overlayModel = RouteForecastOverlayModel(repository: repo)
            }
            await overlayModel?.loadDaysIfNeeded()
        }
        .task(id: overlaySliceKey) {
            guard forecastOverlayVisible, let slot = overlaySlot else { return }
            await overlayModel?.ensureSlice(slot)
        }
    }

    // MARK: Map

    private var mapContent: some View {
        // Reading the tracking count keeps SwiftUI re-rendering (hence pushing a
        // fresh `aircraft` into the representable) as the position updates.
        let _ = trackingService.locationUpdateCount
        return RouteMapKitView(
            routeCoordinates: mapVM.routeCoordinates,
            segments: segments(),
            routeSignature: routeSignature,
            waypoints: mapVM.waypoints,
            initialRegion: mapVM.mapRegion,
            activePoint: activePointCoordinate,
            aircraft: aircraftState,
            onSelectWaypoint: { icao in
                if let wp = mapVM.waypoints.first(where: { $0.id == icao }) { selectWaypoint(wp) }
            },
            forecastAirports: overlayAirports,
            forecastCatalog: overlayCatalog,
            forecastMetric: overlayMetric,
            forecastModel: viewModel.selectedModel,
            showForecastOverlay: forecastOverlayVisible && overlayModelSupported,
            forecastRevision: overlayModel?.payloadRevision ?? 0,
            observedMotionOverlay: observedMotionOverlay
        )
        .ignoresSafeArea(edges: .bottom)
    }

    private var observedMotionOverlay: ObservedMotionOverlaySnapshot {
        let state = viewModel.observedMotionState
        return ObservedMotionOverlay.build(
            envelope: state.envelope,
            selectedProjectionTime: state.selectedProjectionTime,
            enabledFamilies: state.modeEnabled ? state.enabledFamilies : [],
            selectedFeatureID: state.selectedFeatureID,
            selectedAssociation: state.selectedAssociation,
            allowProjectedGeometry: state.canPresentActivePrediction
                || (state.isStoredPresentation && state.selectedProjection != nil),
            storedOnly: state.isStoredPresentation || state.capability != .enabled)
    }

    /// Live aircraft state for the representable, or nil when not tracking.
    private var aircraftState: RouteMapKitView.AircraftState? {
        guard trackingService.isTracking, let location = trackingService.currentLocation else { return nil }
        return RouteMapKitView.AircraftState(
            coordinate: location.coordinate,
            headingDeg: trackingService.projectedPosition?.headingDeg ?? 0,
            opacity: trackingService.projectedPosition?.opacity ?? 0.3
        )
    }

    /// Cheap identity of the route overlays — the representable rebuilds segment
    /// overlays only when this changes (not on every unrelated re-render, e.g. a
    /// tracking tick that moves only the aircraft).
    private var routeSignature: String {
        "\(colorMetricId)|\(usesDualMetrics ? widthMetricId : colorMetricId)|\(Int(effectiveAltitudeFt))|\(viewModel.selectedModel)|\(vizData?.points.count ?? 0)"
    }

    // MARK: Airport-forecast overlay (#428)

    /// Served colour/legend catalog (bundled fallback until the first sync).
    private var overlayCatalog: ForecastMapCatalog? { appState.helpCatalog.mapsCatalog }

    /// The overlay slot for this flight, or nil when the grid hasn't loaded or the
    /// flight is outside the server-advertised D-0..D-6 horizon (overlay not offered).
    private var overlaySlot: RouteForecastSlot? {
        overlayModel?.slot(departureTime: viewModel.flight.departureTime)
    }

    /// The overlay metric, validated against the served catalog — a stale persisted
    /// id falls back to flight_category rather than colouring nothing.
    private var overlayMetric: String {
        guard let catalog = overlayCatalog, catalog.metrics[forecastMetricRaw] != nil else { return "flight_category" }
        return forecastMetricRaw
    }

    /// The briefing's selected model has airport data for this day. When false the
    /// overlay is empty because of the model, not the weather — the controls show a
    /// note instead of the metric/time so a blank map never reads as "all clear".
    private var overlayModelSupported: Bool {
        guard let slot = overlaySlot else { return false }
        return slot.models.contains(viewModel.selectedModel)
    }

    /// Airports to draw — only within the horizon, toggled on, and with model data;
    /// otherwise empty (hidden), never muted-grey.
    private var overlayAirports: [ForecastAirport] {
        guard forecastOverlayVisible, overlayModelSupported, let slot = overlaySlot,
              let airports = overlayModel?.airports(for: slot) else { return [] }
        return airports
    }

    /// `.task(id:)` key for lazily fetching the slice: changes when the slot first
    /// resolves (grid lands) or the toggle flips on. Metric/model changes don't
    /// refetch (the payload holds every model — a pure recolour).
    private var overlaySliceKey: String {
        guard forecastOverlayVisible, let slot = overlaySlot else { return "off" }
        return slot.key
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

            // Airport-forecast overlay controls — only within the forecast horizon.
            if overlaySlot != nil { forecastOverlayControls }

            if let colorMetric {
                legend(colorMetric)
                if needsAltitude { altitudeSlider }
            }
            Spacer()
        }
        .padding(Theme.spacingM)
    }

    /// Airport-forecast overlay controls (#428): toggle · metric picker + valid-time
    /// label (or a "no model data" note) · open-full-forecast-map deep link.
    private var forecastOverlayControls: some View {
        HStack(spacing: Theme.spacingS) {
            Button {
                forecastOverlayVisible.toggle()
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: forecastOverlayVisible ? "checkmark.circle.fill" : "circle")
                    Text("Airports")
                }
                .font(.caption.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(.ultraThinMaterial, in: Capsule())
            }
            .buttonStyle(.plain)

            if forecastOverlayVisible {
                if overlayModelSupported {
                    overlayMetricMenu
                    let timeLabel = overlayModel?.forecastTimeLabel ?? ""
                    Text(timeLabel.isEmpty ? "…" : timeLabel)
                        .font(.tabularData(.caption2)).foregroundStyle(Theme.textMuted)
                } else {
                    // Empty because the selected model has no airport data for this
                    // day, not because the weather is clear — say so.
                    Text("No \(viewModel.selectedModel.uppercased()) data")
                        .font(.caption2).italic().foregroundStyle(Theme.textMuted)
                }
            }

            Spacer()

            Button { openFullForecastMap() } label: {
                Image(systemName: "map")
                    .font(.caption.weight(.medium))
                    .padding(8)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .accessibilityLabel("Open full forecast map")
        }
    }

    /// Overlay metric picker — reuses the forecast map's "question" sections,
    /// filtered to metrics the served catalog defines. Independent of the
    /// route-segment colour metric.
    private var overlayMetricMenu: some View {
        Menu {
            ForEach(ForecastMapView.metricSections, id: \.title) { section in
                Section(section.title) {
                    ForEach(section.items, id: \.metric) { item in
                        if overlayCatalog?.metrics[item.metric] != nil {
                            Button {
                                forecastMetricRaw = item.metric
                            } label: {
                                if item.metric == overlayMetric {
                                    Label(item.label, systemImage: "checkmark")
                                } else {
                                    Text(item.label)
                                }
                            }
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "cloud.sun")
                Text(overlayCatalog?.label(metric: overlayMetric) ?? "Metric")
                Image(systemName: "chevron.down").font(.caption2)
            }
            .font(.caption.weight(.medium))
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(.ultraThinMaterial, in: Capsule())
        }
    }

    /// Deep-link into the full forecast map seeded with this slot/model/metric,
    /// via the existing `PendingNavigation.forecastMap` seam.
    private func openFullForecastMap() {
        guard let slot = overlaySlot else { return }
        let link = RouteForecastOverlay.deepLink(slot: slot, model: viewModel.selectedModel, metric: overlayMetric)
        appState.pendingNavigation = .forecastMap(link)
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

    private func segments() -> [RouteMapKitView.Segment] {
        guard let colorMetric, let points = vizData?.points, points.count >= 2 else { return [] }
        let widthMetric = self.widthMetric
        let colorAlt: Double? = colorMetric.altitudeDependent ? effectiveAltitudeFt : nil
        let widthAlt: Double? = (widthMetric?.altitudeDependent ?? false) ? effectiveAltitudeFt : nil

        var out: [RouteMapKitView.Segment] = []
        for i in 0..<(points.count - 1) {
            let a = points[i], b = points[i + 1]
            // Risk/ordinal metrics take the worst endpoint (never average the
            // peak away); continuous metrics average — see MapMetric.aggregation.
            guard let colorValue = colorMetric.segmentValue(a, b, altitudeFt: colorAlt) else { continue }
            let widthValue = widthMetric?.segmentValue(a, b, altitudeFt: widthAlt)
            out.append(RouteMapKitView.Segment(
                coords: [
                    CLLocationCoordinate2D(latitude: a.lat, longitude: a.lon),
                    CLLocationCoordinate2D(latitude: b.lat, longitude: b.lon),
                ],
                color: UIColor(colorMetric.color(colorValue)),
                width: widthMetric.flatMap { m in widthValue.map { CGFloat(m.width($0)) } } ?? 8
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
