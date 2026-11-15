import SwiftUI
import MapKit
import UIKit

/// Native MapKit map (§4.9). Earns its tab by being geographic — *where* along
/// the route a hazard sits, on real terrain.
///
/// #998: the #996 map style, the same layout as the web and Android briefing
/// maps. Top bar: the altitude stepper `◀ FL080 ▶` (only for a metric that
/// uses altitude; tapping the level goes back to cruise) and Hide. Bottom
/// panel: SHOWING (one metric list from the served catalog, grouped At
/// altitude / Along route), MODEL (the cross-section's), LAYERS (Airports /
/// Observed / Forecast, each only when the briefing has it), then the route
/// legend and the airport line. Floating above it: Fit route, Open full
/// forecast map and the observed age badge. Hidden leaves one pill.
///
/// SYNC: web `route-map/map-chrome.ts`, Android `ui/map/RouteMapTabScreen.kt`.
struct RouteMapView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var mapVM = RouteMapViewModel()
    /// The route metric (an id from the served catalog); nil = its default.
    /// One metric colours AND sizes the line (#998: no Width metric).
    @State private var metricId: String?
    /// nil = cruise. Never overload 0 as "uninitialised": FL000 is a
    /// legitimate stepper value.
    @State private var altitudeFt: Double?
    @State private var selectedWaypoint: WaypointConditions?
    /// Cached extraction (refreshed on model/analyses/elevation change) so
    /// stepping altitude doesn't re-run the route-analysis mapping.
    @State private var vizData: VizRouteData?
    @Environment(AppState.self) private var appState

    /// Whether the controls are hidden (#998), for this run of the app and
    /// apart from the forecast map's `MapChrome.controlsHidden`: hiding the
    /// briefing map's controls is not a choice about the forecast map.
    static var sessionControlsHidden = false
    @State private var controlsHidden = RouteMapView.sessionControlsHidden

    /// Air-mass boundaries on the route (#998, Forecast layer). Off by
    /// default, as on the web; offered only when the briefing has fronts.
    @AppStorage("mapFrontsVisible") private var frontsVisible = false

    // Airport-forecast overlay (#428): the same per-airport markers the full
    // forecast map draws, for the snapshot nearest the flight time.
    /// Day grid + per-slot snapshot; created on appear from the repository.
    @State private var overlayModel: RouteForecastOverlayModel?
    /// Persisted overlay prefs (mirror the web localStorage keys). Default on.
    @AppStorage("mapForecastOverlayVisible") private var forecastOverlayVisible = true
    @AppStorage("mapForecastMetric") private var forecastMetricRaw = "flight_category"

    // Observed imagery (#654): live radar over satellite IR, as on the web map.
    /// Frame listings + legends; created on appear from the repository.
    @State private var observedModel: RouteObservedImageryModel?
    /// Radar pick: a radar source id, or "" for none. Default reflectivity.
    @AppStorage("mapObservedOverlay") private var observedPick = ObservedMapImagery.defaultSelection
    @AppStorage("mapObservedOpacity") private var observedOpacity = ObservedMapImagery.defaultOpacity
    /// Satellite infrared underlay. On by default: radar over the satellite
    /// picture is the view pilots know, and radar alone hides where the cloud is.
    @AppStorage("mapObservedSatellite") private var observedSatellite = true
    /// Experimental radar-cell overlay (#661). Off by default, as on the web —
    /// an experimental layer on a safety product is opted into.
    @AppStorage("mapObservedCells") private var observedCells = false
    /// The map's cell model: it pairs with the drawn reflectivity frame. The
    /// Observed tab's Cells list reads the server's `/live` storms instead.
    @State private var cellsModel: RouteCellsModel?
    /// Lightning flashes as small bolts (#665). On by default: a measurement,
    /// not an experimental analysis, and the web route map always draws it.
    @AppStorage("mapObservedLightning") private var observedLightning = true
    @State private var lightningModel: RouteLightningModel?
    /// The cross-section's corridor pick (0 = unset → the sampled default).
    /// `@AppStorage` so the corridor box follows a change made on that screen.
    @AppStorage(CrossSectionViewModel.observedRadiusDefaultsKey) private var observedRadiusPick = 0.0
    /// The observed summary's details sheet (legends, sources, caveats).
    @State private var showObservedDetails = false
    /// #690 tap-to-map: the region an Observed-tab item asked to frame, and a
    /// counter so the same item tapped twice frames again.
    @State private var focusRegion: MKCoordinateRegion?
    @State private var focusRegionKey = 0

    /// The served route catalog's metrics (bundled copy before the first sync).
    private var metrics: MapMetrics? { appState.helpCatalog.routeMapsCatalog.map(MapMetrics.init) }

    private var metric: MapMetric? { metrics?.resolve(metricId) }

    /// Altitude used for level-dependent metrics: the stepper's value or, until
    /// the pilot steps, the flight's cruise altitude.
    private var effectiveAltitudeFt: Double {
        altitudeFt ?? Double(viewModel.flight.cruiseAltitudeFt)
    }

    private var flightCeilingFt: Double {
        vizData?.flightCeilingFt ?? Double(viewModel.flight.flightCeilingFt)
    }

    private var needsAltitude: Bool { metric?.altitudeDependent ?? false }

    /// `FL080`, `SFC` below 100 ft (web `formatFlightLevel`).
    private var flightLevelLabel: String {
        let ft = effectiveAltitudeFt
        return ft < 100 ? "SFC" : String(format: "FL%03d", Int((ft / 100).rounded()))
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
                if !controlsHidden { bottomControls }
                topBar
            }
        }
        .onChange(of: controlsHidden) { Self.sessionControlsHidden = controlsHidden }
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
        .task(id: observedPollKey) {
            // Polls only while the map is on screen and something is wanted;
            // restarts when the wanted set changes.
            guard showsObserved, !observedPollSources.isEmpty else { return }
            if observedModel == nil, let repo = appState.repository {
                observedModel = RouteObservedImageryModel(repository: repo)
            }
            await observedModel?.loadLegendsIfNeeded()
            await observedModel?.poll(observedPollSources)
        }
        .task(id: cellsPollKey) {
            // Restarts when the drawn radar frame changes, so the overlay
            // follows it (same stamp, else the newest at or before it).
            guard showsCells else { return }
            if cellsModel == nil, let repo = appState.repository {
                cellsModel = RouteCellsModel(repository: repo)
            }
            let stamp = cellsRadarStamp
            await cellsModel?.poll(radarStamp: { stamp }, box: viewModel.cellsBox)
        }
        .task(id: lightningPollKey) {
            // Restarts when the corridor box changes (the cross-section's pick).
            // Hidden → forget the flashes, so toggling back on doesn't show
            // stale bolts and badge until the first poll lands.
            guard showsLightning, let box = observedCorridor else {
                lightningModel?.reset()
                return
            }
            if lightningModel == nil, let repo = appState.repository {
                lightningModel = RouteLightningModel(repository: repo)
            }
            await lightningModel?.poll(box: box)
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
            observedLayers: observedComposition.layers,
            observedTileFetcher: observedModel?.tileFetcher,
            observedCorridor: observedCorridor,
            mutedBaseMap: showsObserved && (!observedRadar.isEmpty || observedSatellite),
            cellsDisplay: showsCells ? cellsModel?.display : nil,
            cellsDisplayKey: showsCells ? cellsModel?.displayPath : nil,
            lightningFlashes: showsLightning ? lightningModel?.drawable ?? [] : [],
            lightningRevision: showsLightning ? lightningModel?.revision ?? 0 : -1,
            frontMarkers: frontMarkers,
            focusRegion: focusRegion,
            focusRegionKey: focusRegionKey,
            onFocusApplied: { focusRegion = nil }
        )
        .ignoresSafeArea(edges: .bottom)
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
        "\(metric?.id ?? "")|\(Int(effectiveAltitudeFt))|\(viewModel.selectedModel)|\(vizData?.points.count ?? 0)"
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

    // MARK: Observed imagery (#654)

    private var observedConditions: ObservedConditions? {
        guard case .loaded(let snapshot) = viewModel.snapshotState else { return nil }
        return snapshot.observedConditions
    }

    /// Only a briefing that collected observed conditions offers the imagery —
    /// the same gate as the web's picker.
    private var showsObserved: Bool { observedConditions?.hasAnyField ?? false }

    private var observedRadarOptions: [String] { ObservedMapImagery.availableRadar(observedConditions) }

    /// The radar source drawn, after falling back from a pick this briefing lacks.
    private var observedRadar: String {
        showsObserved ? ObservedMapImagery.resolveSelection(observedPick, available: observedRadarOptions) : ""
    }

    private var observedPollSources: [String] {
        (observedRadar.isEmpty ? [] : [observedRadar])
            + (observedSatellite ? [ObservedMapImagery.satelliteSource] : [])
    }

    private var observedPollKey: String {
        showsObserved ? observedPollSources.joined(separator: ",") : "off"
    }

    private var observedComposition: (layers: [ObservedMapImagery.TileLayerSpec], badges: [String],
                                      chips: [ObservedMapImagery.SummaryChip]) {
        guard showsObserved, let model = observedModel else { return ([], [], []) }
        return ObservedMapImagery.compose(
            selection: observedRadar, showSatellite: observedSatellite,
            radarOpacity: observedOpacity, frames: model.frames,
            failed: model.failed, now: model.now)
    }

    // MARK: Radar cells (#661)

    private var showsCells: Bool { showsObserved && observedCells }

    /// The reflectivity frame on screen, which the overlay pairs with; nil when
    /// reflectivity isn't drawn (rain rate, none, or stale) → the newest overlay.
    private var cellsRadarStamp: String? {
        guard observedRadar == "opera_dbzh",
              let frame = ObservedMapImagery.currentFrame(observedModel?.frames["opera_dbzh"]) else { return nil }
        return frame.stamp
    }

    private var cellsPollKey: String {
        guard showsCells, let box = viewModel.cellsBox else { return "off" }
        return "\(cellsRadarStamp ?? "newest")|\(box.south),\(box.west),\(box.north),\(box.east)"
    }

    // MARK: Lightning (#665)

    /// The briefing collected lightning: only sources present are offered.
    private var hasLightning: Bool { observedConditions?.lightning != nil }

    private var showsLightning: Bool { showsObserved && hasLightning && observedLightning }

    private var lightningPollKey: String {
        guard showsLightning, let box = observedCorridor else { return "off" }
        return "\(box.south),\(box.west),\(box.north),\(box.east)"
    }

    /// The corridor the sampled numbers describe, at the cross-section's pick.
    private var observedCorridor: ObservedMapImagery.LatLonBox? {
        guard showsObserved else { return nil }
        let picked = observedRadiusPick
        guard let radius = ObservedMapImagery.corridorRadius(observedConditions, picked: picked > 0 ? picked : nil)
        else { return nil }
        return ObservedMapImagery.corridorBox(mapVM.routeCoordinates, radiusNm: radius)
    }

    /// One tappable line on the map — each drawn layer's age ("Cells 10 min ·
    /// Radar 12 min · Sat 25 min"), orange where a layer is stale or failed so
    /// an empty map never reads as "no echoes". The age is the only thing on
    /// screen that says how old an echo is — at 120 kt, minutes are tens of
    /// nautical miles — so it stays; legends, attributions and caveats are one
    /// tap away in `observedDetailsSheet` rather than covering the map.
    @ViewBuilder private var observedBadge: some View {
        let chips = observedChips
        if !chips.isEmpty {
            Button { showObservedDetails = true } label: {
                HStack(spacing: 6) {
                    ForEach(Array(chips.enumerated()), id: \.offset) { index, chip in
                        if index > 0 { Text("·").foregroundStyle(Theme.textMuted) }
                        Text(chip.text)
                            .foregroundStyle(chip.isWarning ? Color.orange : Theme.text)
                    }
                    Image(systemName: "info.circle").foregroundStyle(Theme.textMuted)
                }
                .font(.tabularData(.caption2))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(MapChrome.surface(Capsule()))
            }
            .buttonStyle(.plain)
            // The full lines for VoiceOver (and the UI journeys), not the chips.
            .accessibilityLabel(observedBadges.joined(separator: ". "))
            .accessibilityHint("Shows legends, sources and caveats")
            .accessibilityIdentifier("map.observedBadge")
            .sheet(isPresented: $showObservedDetails) { observedDetailsSheet }
        }
    }

    /// Cells first (the overlay the pilot opted into), then radar, then
    /// satellite, then lightning.
    private var observedChips: [ObservedMapImagery.SummaryChip] {
        (showsCells ? [cellsModel?.chip].compactMap { $0 } : []) + observedComposition.chips
            + (showsLightning ? [lightningModel?.chip].compactMap { $0 } : [])
    }

    /// Full badge lines — time, age, rolling-max window, attribution.
    private var observedBadges: [String] {
        observedComposition.badges + (showsCells ? [cellsModel?.badge].compactMap { $0 } : [])
            + (showsLightning ? [lightningModel?.badge].compactMap { $0 } : [])
    }

    /// Everything the summary leaves out: the radar ramp, each layer's full
    /// line with its producer, the cell legend and its caveat.
    private var observedDetailsSheet: some View {
        let legend = observedRadar.isEmpty ? nil : observedModel?.legends[observedRadar]
        return NavigationStack {
            List {
                Section {
                    ForEach(observedBadges, id: \.self) { line in
                        Text(line).font(.footnote).foregroundStyle(Theme.text)
                    }
                } header: {
                    Text("Layers")
                } footer: {
                    Text("Observed imagery shows the sky as it was, not as it is: at 120 kt, ten minutes is twenty nautical miles.")
                }
                if let legend, let stops = legend.legend, stops.count >= 2 {
                    Section(ObservedMapImagery.label(for: observedRadar)) {
                        observedLegend(legend, stops: stops)
                    }
                }
                if showsLightning {
                    Section {
                        HStack(spacing: 6) {
                            Image(systemName: "bolt.fill")
                                .foregroundStyle(Color(uiColor: ObservedLightning.color))
                            Text("one flash, fading over \(Int(ObservedLightning.trailMinutes)) min")
                        }
                        .font(.caption2)
                        .foregroundStyle(Theme.text)
                    } header: {
                        Text("Lightning")
                    } footer: {
                        Text("Satellite total lightning (in-cloud and cloud-to-ground) in the corridor box, each flash at its own time.")
                    }
                }
                if showsCells {
                    Section {
                        cellsLegend
                    } header: {
                        Text("Radar cells (experimental)")
                    } footer: {
                        Text("Markers are coloured by trend; the magenta arrow points to where a core would be in 30 min if its motion continued. Tap a marker for its measurements. "
                             + CellsOverlay.caveat)
                    }
                }
            }
            .navigationTitle("Observed")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { showObservedDetails = false }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .accessibilityIdentifier("map.observedDetails")
    }

    /// Cell markers are coloured by trend (evolution, not safety); the magenta
    /// arrow is where a core would be in 30 min if its motion continued. Tap a
    /// marker for its measurements.
    private var cellsLegend: some View {
        HStack(spacing: 6) {
            ForEach(CellsOverlay.trendOrder, id: \.self) { state in
                HStack(spacing: 2) {
                    Circle()
                        .fill(Color(uiColor: CellsOverlay.trendColor(state)))
                        .overlay(Circle().stroke(Color(white: 0.13), lineWidth: 0.5))
                        .frame(width: 7, height: 7)
                    Text(state)
                }
            }
            Text("━").foregroundStyle(Color(uiColor: CellsOverlay.arrowColor))
            Text("30 min")
        }
        .font(.caption2)
        .foregroundStyle(Theme.text)
    }

    /// The server's own ramp, ends labelled (a label per stop is unreadable).
    private func observedLegend(_ status: ObservedImagerySourceStatus, stops: [ObservedLegendStop]) -> some View {
        HStack(spacing: 6) {
            Text(ObservedMapImagery.legendValue(stops[0].value, units: nil))
            HStack(spacing: 0) {
                ForEach(Array(stops.enumerated()), id: \.offset) { _, stop in
                    Rectangle()
                        .fill(Color(uiColor: ObservedMapImagery.color(hex: stop.color) ?? .clear))
                        .frame(width: 12, height: 6)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 2))
            Text(ObservedMapImagery.legendValue(stops[stops.count - 1].value, units: status.units))
        }
        .font(.tabularData(.caption2))
        .foregroundStyle(Theme.text)
    }

    // MARK: Top bar (altitude stepper · Hide)

    private var topBar: some View {
        HStack(spacing: Theme.spacingS) {
            if !controlsHidden, needsAltitude { altitudeStepper }
            Spacer(minLength: Theme.spacingS)
            if controlsHidden {
                MapChrome.showControlsPill(time: needsAltitude ? "\(flightLevelLabel) · Show" : "Show") {
                    controlsHidden = false
                }
            } else {
                MapChrome.circleButton(systemImage: "eye.slash") { controlsHidden = true }
                    .accessibilityLabel("Hide map controls")
                    .accessibilityIdentifier("map.hideControls")
            }
        }
        .padding(Theme.spacingM)
    }

    /// `◀ FL080 ▶` in 500 ft steps, 0 … the flight ceiling. The level resets to
    /// cruise when tapped.
    private var altitudeStepper: some View {
        let alt = effectiveAltitudeFt
        let ceiling = flightCeilingFt
        return HStack(spacing: 0) {
            MapChrome.stepButton("chevron.left", label: "500 feet lower", enabled: alt > 0) {
                altitudeFt = Self.stepAltitude(alt, by: -1, ceilingFt: ceiling)
            }
            Button { altitudeFt = nil } label: {
                Text(flightLevelLabel)
                    .font(.subheadline.weight(.bold))
                    .monospacedDigit()
                    .foregroundStyle(Theme.primary)
                    .frame(minWidth: 52)
            }
            .accessibilityLabel("\(flightLevelLabel), back to cruise altitude")
            .accessibilityIdentifier("map.altitudeReset")
            MapChrome.stepButton("chevron.right", label: "500 feet higher", enabled: alt < ceiling) {
                altitudeFt = Self.stepAltitude(alt, by: 1, ceilingFt: ceiling)
            }
        }
        .foregroundStyle(Theme.text)
        .padding(.horizontal, 4)
        .background(MapChrome.surface(Capsule()))
    }

    /// One stepper press: snap to the 500 ft grid, step, clamp to 0 … ceiling
    /// (web `stepAltitude`).
    nonisolated static func stepAltitude(_ ft: Double, by dir: Double, ceilingFt: Double) -> Double {
        let snapped = (ft / 500).rounded() * 500
        return Swift.max(0, Swift.min(ceilingFt, snapped + dir * 500))
    }

    // MARK: Bottom panel (SHOWING · MODEL · LAYERS · legend)

    private var bottomControls: some View {
        VStack(alignment: .trailing, spacing: Theme.spacingS) {
            Spacer()
            HStack(alignment: .bottom, spacing: Theme.spacingS) {
                if showsObserved { observedBadge }
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: Theme.spacingS) {
                    if overlaySlot != nil {
                        MapChrome.labelledButton("Forecast map", systemImage: "map") { openFullForecastMap() }
                            .accessibilityLabel("Open full forecast map")
                            .accessibilityIdentifier("map.openForecastMap")
                    }
                    MapChrome.circleButton(systemImage: "arrow.up.left.and.down.right.magnifyingglass") { fitRoute() }
                        .accessibilityLabel("Fit route")
                        .accessibilityIdentifier("map.fitRoute")
                }
            }
            MapChrome.bottomPanel(picks: {
                metricMenu
                if viewModel.availableModels.count > 1 { modelMenu }
                if hasLayers { layersMenu }
            }, legend: {
                if let metric { routeLegend(metric) }
                if forecastOverlayVisible, overlayModelSupported,
                   let legend = overlayCatalog?.legend(metric: overlayMetric) {
                    airportLegendLine(legend)
                }
            })
            .frame(maxWidth: .infinity)
        }
        .padding(Theme.spacingM)
        // Clear of MapKit's logo + Legal link, which must stay visible.
        .padding(.bottom, 20)
    }

    /// SHOWING: the served metric list, grouped At altitude / Along route.
    private var metricMenu: some View {
        Menu {
            ForEach(metrics?.groups ?? []) { group in
                Section(group.label) {
                    ForEach(group.metrics) { m in
                        Button { metricId = m.id } label: {
                            MapChrome.menuRow(m.label, selected: m.id == metric?.id)
                        }
                    }
                }
            }
        } label: {
            MapChrome.pickLabel(caption: "Showing", value: metric?.label ?? "Metric")
        }
        .accessibilityIdentifier("map.metricMenu")
    }

    /// MODEL: the cross-section's model, so the map and the chart agree.
    private var modelMenu: some View {
        Menu {
            ForEach(viewModel.availableModels, id: \.self) { m in
                Button { viewModel.selectModel(m) } label: {
                    MapChrome.menuRow(m.shortModelName, selected: m == viewModel.selectedModel)
                }
            }
        } label: {
            MapChrome.pickLabel(caption: "Model", value: viewModel.selectedModel.shortModelName)
        }
        .accessibilityIdentifier("map.modelMenu")
    }

    private var hasFronts: Bool { !(viewModel.routeFronts?.frontChains?.isEmpty ?? true) }

    private var hasLayers: Bool { overlaySlot != nil || showsObserved || hasFronts }

    /// What LAYERS reads when closed: what is on, or None.
    private var layersSummary: String {
        var on: [String] = []
        if overlaySlot != nil, forecastOverlayVisible { on.append("Airports") }
        if showsObserved {
            if !observedRadar.isEmpty { on.append("Radar") }
            if observedSatellite { on.append("Satellite") }
            if showsLightning { on.append("Lightning") }
            if observedCells { on.append("Cells") }
        }
        if hasFronts, frontsVisible { on.append("Fronts") }
        return on.isEmpty ? "None" : on.joined(separator: ", ")
    }

    /// LAYERS: Airports / Observed / Forecast, each only when the briefing has it.
    private var layersMenu: some View {
        Menu {
            if overlaySlot != nil {
                Section("Airports") {
                    Toggle("Show airports", isOn: $forecastOverlayVisible)
                    if forecastOverlayVisible {
                        if overlayModelSupported {
                            overlayMetricMenu
                        } else {
                            // Empty because of the model, not the weather: say so.
                            Text("No \(viewModel.selectedModel.shortModelName) airport data")
                        }
                    }
                }
            }
            if showsObserved {
                Section("Observed") {
                    if !observedRadarOptions.isEmpty {
                        Menu("Radar: \(observedRadar.isEmpty ? "None" : ObservedMapImagery.label(for: observedRadar))") {
                            observedChoice("None", id: "")
                            ForEach(observedRadarOptions, id: \.self) { source in
                                observedChoice(ObservedMapImagery.label(for: source), id: source)
                            }
                        }
                    }
                    Toggle("Satellite infrared", isOn: $observedSatellite)
                    if hasLightning {
                        Toggle("Lightning", isOn: $observedLightning)
                            .accessibilityIdentifier("map.observedLightningToggle")
                    }
                    Toggle("Radar cells (experimental)", isOn: $observedCells)
                        .accessibilityIdentifier("map.observedCellsToggle")
                    if !observedRadar.isEmpty {
                        Menu("Radar opacity \(Int((observedOpacity * 100).rounded()))%") {
                            ForEach(ObservedMapImagery.opacitySteps, id: \.self) { step in
                                Button { observedOpacity = step } label: {
                                    MapChrome.menuRow("\(Int((step * 100).rounded()))%",
                                                      selected: abs(step - observedOpacity) < 0.001)
                                }
                            }
                        }
                    }
                }
            }
            if hasFronts {
                Section("Forecast") {
                    Toggle("Air-mass boundaries", isOn: $frontsVisible)
                }
            }
        } label: {
            MapChrome.pickLabel(caption: "Layers", value: layersSummary)
        }
        .accessibilityIdentifier("map.layersMenu")
    }

    private func observedChoice(_ title: String, id: String) -> some View {
        Button { observedPick = id } label: {
            MapChrome.menuRow(title, selected: id == observedRadar)
        }
    }

    /// The airport overlay's metric, in the forecast map's question sections,
    /// filtered to metrics the served catalog defines.
    private var overlayMetricMenu: some View {
        Menu("Airports show: \(overlayCatalog?.label(metric: overlayMetric) ?? "Metric")") {
            ForEach(ForecastMapView.metricSections, id: \.title) { section in
                Section(section.title) {
                    ForEach(section.items, id: \.metric) { item in
                        if overlayCatalog?.metrics[item.metric] != nil {
                            Button { forecastMetricRaw = item.metric } label: {
                                MapChrome.menuRow(item.label, selected: item.metric == overlayMetric)
                            }
                        }
                    }
                }
            }
        }
    }

    /// The route scale: one label centred under each swatch, unit at the end.
    private func routeLegend(_ metric: MapMetric) -> some View {
        VStack(alignment: .trailing, spacing: 2) {
            HStack(spacing: 0) {
                ForEach(Array(metric.legend.enumerated()), id: \.offset) { _, stop in
                    Rectangle().fill(stop.color).frame(maxWidth: .infinity).frame(height: 8)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 2))
            .overlay(RoundedRectangle(cornerRadius: 2).stroke(Theme.border, lineWidth: 0.5))
            HStack(spacing: 0) {
                ForEach(Array(metric.legend.enumerated()), id: \.offset) { _, stop in
                    Text(stop.label)
                        .font(.caption2)
                        .foregroundStyle(Theme.textMuted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .frame(maxWidth: .infinity)
                }
            }
            if !metric.unit.isEmpty {
                Text(metric.unit).font(.system(size: 9)).foregroundStyle(Theme.textMuted)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("map.routeLegend")
    }

    /// The airport overlay's legend as one compact line, plus its valid time
    /// (the floating airport legend is gone: the map has one legend area).
    private func airportLegendLine(_ legend: ForecastMapCatalog.Legend) -> some View {
        let time = overlayModel?.forecastTimeLabel ?? ""
        return FlowLayout(spacing: 8) {
            Text(legend.title).font(.caption2.weight(.semibold)).foregroundStyle(Theme.text)
            ForEach(legend.items) { item in
                HStack(spacing: 3) {
                    Circle().fill(Color.catalog(item.color)).frame(width: 8, height: 8)
                    Text(item.label).font(.caption2).foregroundStyle(Theme.textMuted)
                }
            }
            Text("Airports: \(time.isEmpty ? "…" : time)")
                .font(.tabularData(.caption2)).foregroundStyle(Theme.textMuted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Deep-link into the full forecast map seeded with this slot/model/metric,
    /// via the existing `PendingNavigation.forecastMap` seam.
    private func openFullForecastMap() {
        guard let slot = overlaySlot else { return }
        let link = RouteForecastOverlay.deepLink(slot: slot, model: viewModel.selectedModel, metric: overlayMetric)
        appState.pendingNavigation = .forecastMap(link)
    }

    /// Frame the whole route again (#998), through the tap-to-map seam.
    private func fitRoute() {
        focusRegion = mapVM.mapRegion
        focusRegionKey += 1
    }

    // MARK: Fronts (#998)

    /// Where the selected model's linked fronts cross the route.
    private var frontMarkers: [RouteMapKitView.FrontMarker] {
        guard frontsVisible, let points = vizData?.points,
              let chains = viewModel.routeFronts?.frontChains?[viewModel.selectedModel] else { return [] }
        return chains.compactMap { RouteFrontPlacement.marker(for: $0, along: points) }
    }

    // MARK: Deep-link focus (§4.9)

    /// Consume a map-targeted focus intent: select the metric, scrub the
    /// altitude, then clear the intent so it fires once.
    private func applyFocusIntent() {
        guard let intent = viewModel.focusIntent, intent.target == .map else { return }
        if let id = intent.mapMetricId, metrics?.metric(byId: id) != nil {
            metricId = id
        }
        if let alt = intent.altitudeFt {
            altitudeFt = min(flightCeilingFt, max(0, alt))
        }
        if intent.showObservedCells {
            // The Observed tab's "Show on map": cells on, over reflectivity —
            // the radar the overlay was analysed from.
            observedCells = true
            if observedRadarOptions.contains("opera_dbzh") { observedPick = "opera_dbzh" }
        }
        if let focus = intent.mapFocus {
            // #690 tap-to-map: the item's own layers, then frame its box.
            if focus.wantsCells { observedCells = true }
            if focus.wantsLightning { observedLightning = true }
            if (focus.wantsRadar || focus.wantsCells), observedRadarOptions.contains("opera_dbzh") {
                observedPick = "opera_dbzh"
            }
            if let region = focus.region {
                focusRegion = region
                focusRegionKey += 1
            }
        }
        viewModel.clearFocusIntent()
    }

    // MARK: Segment building

    /// One segment per pair of points. A segment with no value draws the
    /// catalog's no-data grey at the thinnest width (#998), so a gap is never
    /// read as a colour, and never silently skipped.
    private func segments() -> [RouteMapKitView.Segment] {
        guard let metrics, let metric, let points = vizData?.points, points.count >= 2 else { return [] }
        let alt: Double? = metric.altitudeDependent ? effectiveAltitudeFt : nil
        var out: [RouteMapKitView.Segment] = []
        for i in 0..<(points.count - 1) {
            let a = points[i], b = points[i + 1]
            let value = metric.segmentValue(a, b, altitudeFt: alt)
            out.append(RouteMapKitView.Segment(
                coords: [
                    CLLocationCoordinate2D(latitude: a.lat, longitude: a.lon),
                    CLLocationCoordinate2D(latitude: b.lat, longitude: b.lon),
                ],
                color: value.map { UIColor(metric.color($0)) } ?? metrics.noDataColor,
                width: CGFloat(value.map(metric.width) ?? MapMetrics.minWidth)
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

/// Where a linked front chain crosses the route (#998): its lowest node's
/// along-route distance, placed on the viz points by interpolation. Server
/// data, placed only; nothing about the front is computed here.
nonisolated enum RouteFrontPlacement {
    static let kmPerNm = 1.852

    static func marker(for chain: FrontChain, along points: [VizPoint]) -> RouteMapKitView.FrontMarker? {
        guard let node = chain.nodes.first,
              let c = coordinate(atDistanceNm: node.distanceKm / kmPerNm, along: points) else { return nil }
        return RouteMapKitView.FrontMarker(latitude: c.lat, longitude: c.lon,
                                           kind: chain.kind ?? node.kind, intensity: node.intensity)
    }

    /// Lat/lon at an along-route distance; nil off the ends of the route.
    static func coordinate(atDistanceNm d: Double, along points: [VizPoint]) -> (lat: Double, lon: Double)? {
        guard let first = points.first, let last = points.last,
              d >= first.distanceNm, d <= last.distanceNm else { return nil }
        for (a, b) in zip(points, points.dropFirst()) where d >= a.distanceNm && d <= b.distanceNm {
            let span = b.distanceNm - a.distanceNm
            let t = span > 0 ? (d - a.distanceNm) / span : 0
            return (a.lat + t * (b.lat - a.lat), a.lon + t * (b.lon - a.lon))
        }
        return nil
    }
}
