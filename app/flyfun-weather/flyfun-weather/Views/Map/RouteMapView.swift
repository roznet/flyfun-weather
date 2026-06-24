import SwiftUI
import MapKit

/// Native map showing route polyline, metric-colored segments, waypoint callouts, and live aircraft.
struct RouteMapView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService

    @State private var mapVM = RouteMapViewModel()
    @State private var selectedColorMetric: RouteMapMetric = .convectiveRisk
    @State private var selectedWidthMetric: RouteMapMetric = .convectiveRisk
    @State private var altitudeFt: Double = 0
    @State private var selectedWaypoint: RouteMapViewModel.WaypointAnnotation?
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Group {
            if mapVM.routeCoordinates.isEmpty {
                switch viewModel.snapshotState {
                case .loading:
                    ProgressView("Loading map data...")
                case .error(let error):
                    ContentUnavailableView("Map Unavailable", systemImage: "map", description: Text(error.localizedDescription))
                default:
                    ProgressView()
                }
            } else {
                loadedMap
            }
        }
        .onChange(of: selectedColorMetric) {
            if !usesDualMetrics {
                selectedWidthMetric = selectedColorMetric
            }
        }
        .onChange(of: horizontalSizeClass) {
            if !usesDualMetrics {
                selectedWidthMetric = selectedColorMetric
            }
        }
        .onChange(of: viewModel.focusIntent) {
            applyFocusIntent(viewModel.focusIntent)
        }
        .onChange(of: viewModel.snapshotState.isLoaded) {
            if case .loaded(let snapshot) = viewModel.snapshotState {
                mapVM.update(from: snapshot)
                ensureAltitudeInitialized()
            }
        }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) {
            if case .loaded(let analyses) = viewModel.routeAnalysesState {
                mapVM.update(from: analyses)
                ensureAltitudeInitialized()
            }
        }
        .task {
            ensureAltitudeInitialized()
            if case .loaded(let analyses) = viewModel.routeAnalysesState {
                mapVM.update(from: analyses)
            } else if case .loaded(let snapshot) = viewModel.snapshotState {
                mapVM.update(from: snapshot)
            }
            applyFocusIntent(viewModel.focusIntent)
        }
    }

    private var loadedMap: some View {
        // Read observable values in view body — Map content builder is @escaping.
        let _ = trackingService.locationUpdateCount
        let isTracking = trackingService.isTracking
        let aircraftLocation = trackingService.currentLocation
        let aircraftOpacity = trackingService.projectedPosition?.opacity ?? 0.3
        let aircraftHeading = trackingService.projectedPosition?.headingDeg ?? 0
        let activeRoutePoint = viewModel.routePoint(for: viewModel.activePoint)
        let widthMetric = usesDualMetrics ? selectedWidthMetric : selectedColorMetric
        let segments = mapVM.segments(
            colorMetric: selectedColorMetric,
            widthMetric: widthMetric,
            model: viewModel.selectedModel,
            altitudeFt: effectiveAltitudeFt
        )

        return ZStack(alignment: .bottom) {
            Map(initialPosition: .region(mapVM.mapRegion)) {
                if segments.isEmpty {
                    MapPolyline(coordinates: mapVM.routeCoordinates)
                        .stroke(.blue, lineWidth: 3)
                } else {
                    ForEach(segments) { segment in
                        MapPolyline(coordinates: segment.coordinates)
                            .stroke(segment.color, lineWidth: segment.width)
                    }
                }

                ForEach(segments) { segment in
                    Annotation("", coordinate: segment.midpoint, anchor: .center) {
                        Button {
                            viewModel.setActivePoint(segment.targetPoint)
                        } label: {
                            Circle()
                                .fill(Color.white.opacity(0.001))
                                .frame(width: 34, height: 34)
                        }
                        .accessibilityLabel(segment.valueDescription ?? "Route segment")
                    }
                    .annotationTitles(.hidden)
                }

                ForEach(mapVM.waypoints) { waypoint in
                    Annotation(waypoint.id, coordinate: waypoint.coordinate) {
                        Button {
                            selectWaypoint(waypoint)
                        } label: {
                            VStack(spacing: 2) {
                                Text(waypoint.id)
                                    .font(.caption2.bold())
                                    .foregroundStyle(.primary)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 2)
                                    .background(.ultraThinMaterial, in: Capsule())
                                Circle()
                                    .fill(waypointColor(waypoint))
                                    .frame(width: 10, height: 10)
                                    .overlay(Circle().stroke(.white, lineWidth: 1.5))
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }

                if let activeRoutePoint {
                    Annotation("", coordinate: .init(latitude: activeRoutePoint.lat, longitude: activeRoutePoint.lon), anchor: .center) {
                        ZStack {
                            Circle()
                                .fill(.orange.opacity(0.20))
                            Circle()
                                .stroke(.orange, lineWidth: 3)
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
            .mapStyle(.standard(elevation: .realistic))

            VStack(spacing: WeatherTheme.Spacing.sm) {
                RouteMapControls(
                    selectedColorMetric: $selectedColorMetric,
                    selectedWidthMetric: $selectedWidthMetric,
                    altitudeFt: $altitudeFt,
                    usesDualMetrics: usesDualMetrics,
                    needsAltitude: selectedColorMetric.altitudeDependent || widthMetric.altitudeDependent,
                    ceilingFt: viewModel.flight.flightCeilingFt,
                    selectedModel: viewModel.selectedModel
                )

                RouteMapLegend(
                    colorMetric: selectedColorMetric,
                    widthMetric: usesDualMetrics && widthMetric != selectedColorMetric ? widthMetric : nil
                )
            }
            .padding(WeatherTheme.Spacing.md)
        }
        .popover(item: $selectedWaypoint) { waypoint in
            RouteWaypointConditionPopover(
                waypoint: waypoint,
                condition: airportCondition(for: waypoint),
                selectedModel: viewModel.selectedModel
            )
            .presentationCompactAdaptation(.popover)
        }
    }

    private var usesDualMetrics: Bool {
        horizontalSizeClass == .regular
    }

    private var effectiveAltitudeFt: Double {
        altitudeFt > 0 ? altitudeFt : Double(viewModel.flight.cruiseAltitudeFt)
    }

    private func ensureAltitudeInitialized() {
        guard altitudeFt == 0 else { return }
        altitudeFt = Double(viewModel.flight.cruiseAltitudeFt)
    }

    private func applyFocusIntent(_ intent: BriefingFocusIntent?) {
        guard intent?.target == .map else { return }
        if let metricId = intent?.metricId, let metric = RouteMapMetric(rawValue: metricId) {
            selectedColorMetric = metric
            if !usesDualMetrics {
                selectedWidthMetric = metric
            }
        }
        if let altitude = intent?.altitudeFt {
            altitudeFt = min(Double(viewModel.flight.flightCeilingFt), max(0, altitude))
        }
    }

    private func selectWaypoint(_ waypoint: RouteMapViewModel.WaypointAnnotation) {
        if let point = mapVM.routePoints.first(where: { $0.waypointIcao == waypoint.id }) {
            viewModel.setActivePoint(point)
        }
        selectedWaypoint = waypoint
    }

    private func airportCondition(for waypoint: RouteMapViewModel.WaypointAnnotation) -> AirportConditionsSummary? {
        guard case .loaded(let advisories) = viewModel.advisoriesState,
              let conditions = advisories.airportConditions
        else { return nil }
        if conditions.departure.icao == waypoint.id {
            return conditions.departure
        }
        if conditions.arrival.icao == waypoint.id {
            return conditions.arrival
        }
        return nil
    }

    private func waypointColor(_ waypoint: RouteMapViewModel.WaypointAnnotation) -> Color {
        guard let condition = airportCondition(for: waypoint) else { return .blue }
        let category = condition.conditions
            .map { $0.flightCategory.uppercased() }
            .sorted { flightCategorySeverity($0) > flightCategorySeverity($1) }
            .first ?? "UNKNOWN"
        return flightCategoryColor(category)
    }
}

private struct RouteMapControls: View {
    @Binding var selectedColorMetric: RouteMapMetric
    @Binding var selectedWidthMetric: RouteMapMetric
    @Binding var altitudeFt: Double
    let usesDualMetrics: Bool
    let needsAltitude: Bool
    let ceilingFt: Int
    let selectedModel: String
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
            HStack(spacing: WeatherTheme.Spacing.sm) {
                Picker("Color", selection: $selectedColorMetric) {
                    ForEach(RouteMapMetric.allCases) { metric in
                        Text(metric.label).tag(metric)
                    }
                }
                .pickerStyle(.menu)

                if usesDualMetrics {
                    Picker("Width", selection: $selectedWidthMetric) {
                        ForEach(RouteMapMetric.allCases) { metric in
                            Text(metric.label).tag(metric)
                        }
                    }
                    .pickerStyle(.menu)
                }

                Spacer()

                Label(selectedModel.shortModelName.uppercased(), systemImage: "square.stack.3d.up")
                    .font(.caption.bold())
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
            }

            if needsAltitude {
                VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
                    HStack {
                        Label("FL\(Int(altitudeFt / 100))", systemImage: "arrow.up.to.line.compact")
                            .font(.caption.monospacedDigit())
                        Spacer()
                        Text("0-FL\(ceilingFt / 100)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                    }
                    Slider(value: $altitudeFt, in: 0...Double(max(ceilingFt, 500)), step: 500)
                }
            }
        }
        .padding(WeatherTheme.Spacing.md)
        .background(WeatherTheme.surface(colorScheme).opacity(0.92), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.card))
        .overlay {
            RoundedRectangle(cornerRadius: WeatherTheme.Radius.card)
                .stroke(WeatherTheme.border(colorScheme), lineWidth: 0.5)
        }
    }
}

private struct RouteMapLegend: View {
    let colorMetric: RouteMapMetric
    let widthMetric: RouteMapMetric?
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
            HStack {
                Text(colorMetric.label)
                    .font(.caption.bold())
                    .foregroundStyle(WeatherTheme.text(colorScheme))
                Spacer()
                if colorMetric.altitudeDependent {
                    Image(systemName: "arrow.up.to.line.compact")
                        .font(.caption2)
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
            }

            LinearGradient(
                colors: colorMetric.legendStops.map(\.color),
                startPoint: .leading,
                endPoint: .trailing
            )
            .frame(height: 8)
            .clipShape(Capsule())

            HStack {
                ForEach(colorMetric.legendStops) { stop in
                    Text(stop.label)
                        .font(.caption2)
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                    if stop.id != colorMetric.legendStops.last?.id {
                        Spacer(minLength: 2)
                    }
                }
            }

            if let widthMetric {
                Label("Width: \(widthMetric.label)", systemImage: "lineweight")
                    .font(.caption2)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
            }
        }
        .padding(WeatherTheme.Spacing.md)
        .background(WeatherTheme.surface(colorScheme).opacity(0.92), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.card))
        .overlay {
            RoundedRectangle(cornerRadius: WeatherTheme.Radius.card)
                .stroke(WeatherTheme.border(colorScheme), lineWidth: 0.5)
        }
    }
}

private struct RouteWaypointConditionPopover: View {
    let waypoint: RouteMapViewModel.WaypointAnnotation
    let condition: AirportConditionsSummary?
    let selectedModel: String
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            HStack {
                VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
                    Text(waypoint.id)
                        .font(.headline)
                    Text(waypoint.name)
                        .font(.caption)
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                }
                Spacer()
                if let modelCondition {
                    Text(modelCondition.flightCategory.uppercased())
                        .font(.caption.bold())
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(flightCategoryColor(modelCondition.flightCategory), in: Capsule())
                }
            }

            if let modelCondition {
                VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
                    Label(modelCondition.model.uppercased(), systemImage: "square.stack.3d.up")
                    if let windSpeed = modelCondition.windSpeedKt {
                        Label(windText(modelCondition, windSpeed: windSpeed), systemImage: "wind")
                    }
                    if let ceiling = modelCondition.ceilingFt {
                        Label("\(ceiling) ft ceiling", systemImage: "cloud")
                    }
                    if let visibility = modelCondition.visibilitySm {
                        Label(String(format: "%.1f sm visibility", visibility), systemImage: "eye")
                    }
                    if let runway = modelCondition.bestRunway {
                        Label("RWY \(runway.runwayId) · XW \(Int(abs(runway.crosswindKt).rounded())) kt", systemImage: "arrow.triangle.branch")
                    }
                }
                .font(.caption)
                .foregroundStyle(WeatherTheme.text(colorScheme))
            } else {
                Text("Airport condition data is not available in this briefing pack.")
                    .font(.caption)
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(width: 260, alignment: .leading)
        .padding(WeatherTheme.Spacing.md)
    }

    private var modelCondition: AirportModelCondition? {
        guard let condition else { return nil }
        return condition.conditions.first { $0.model == selectedModel }
            ?? condition.conditions.first
    }

    private func windText(_ condition: AirportModelCondition, windSpeed: Double) -> String {
        let direction = condition.windDirectionDeg.map { "\(Int($0.rounded())) deg" } ?? "VRB"
        let gust = condition.windGustKt.map { "G\(Int($0.rounded()))" } ?? ""
        return "\(direction) \(Int(windSpeed.rounded()))\(gust) kt"
    }
}

private func flightCategorySeverity(_ category: String) -> Int {
    switch category.uppercased() {
    case "LIFR": 4
    case "IFR": 3
    case "MVFR": 2
    case "VFR": 1
    default: 0
    }
}

private func flightCategoryColor(_ category: String) -> Color {
    switch category.uppercased() {
    case "VFR": .green
    case "MVFR": .orange
    case "IFR": .red
    case "LIFR": .purple
    default: .gray
    }
}

// Helper to detect state changes
extension LoadingState {
    var isLoaded: Bool {
        if case .loaded = self { return true }
        return false
    }
}
