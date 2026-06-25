import SwiftUI

/// SwiftUI Canvas wrapper for the cross-section visualization (§4.7 interaction).
/// Touch model (a): tap/drag = scrub → moves a continuous cursor that drives the
/// readout strip and the shared active point; "Sounding ›" deep-links to the
/// Skew-T tab. Config lives in a bottom sheet behind a "Layers" pill (§4.5);
/// the model selector stays in the chrome. Landscape = full-bleed focus mode.
struct CrossSectionView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var csVM = CrossSectionViewModel()
    @State private var canvasSize: CGSize = .zero
    @State private var scrubDistanceNm: Double?
    @State private var scrubAltitudeFt: Double?
    @State private var showingConfig = false
    @State private var chromeHidden = false
    /// Route-graph metric selection, lifted here so the readout strip and the
    /// graph share one cursor + one metric choice (§4.7 unified cursor).
    @State private var graphLeftMetricId = "headwind"
    @State private var graphRightMetricId = "cloud-cover"
    @Environment(\.verticalSizeClass) private var vSizeClass

    /// iPhone landscape → immersive full-bleed focus mode (§4.7): cross-section
    /// is a wide artifact, so landscape gives it the right aspect ratio.
    private var isLandscapeFocus: Bool { vSizeClass == .compact }

    var body: some View {
        Group {
            if isLandscapeFocus {
                landscapeFocus
            } else {
                portrait
            }
        }
        .onChange(of: viewModel.selectedModel) { updateVizData() }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) { updateVizData() }
        .onChange(of: viewModel.elevationState.isLoaded) { updateVizData() }
        .onChange(of: viewModel.focusIntent) { applyFocusIntent() }
        .task { updateVizData(); applyFocusIntent() }
        .sheet(isPresented: $showingConfig) {
            CrossSectionConfigSheet(csVM: csVM)
        }
    }

    // MARK: Portrait layout

    private var portrait: some View {
        ScrollView {
            VStack(spacing: 0) {
                chromeBar
                CrossSectionReadoutView(
                    vizData: csVM.vizData ?? Self.emptyViz,
                    scrubDistanceNm: scrubDistanceNm,
                    scrubAltitudeFt: scrubAltitudeFt,
                    onSounding: goToSounding,
                    routeGraphMetricIds: [graphLeftMetricId, graphRightMetricId]
                )
                crossSectionCanvas
                RouteGraphView(viewModel: viewModel, vizData: csVM.vizData, scrubDistanceNm: scrubDistanceNm,
                               leftMetricId: $graphLeftMetricId, rightMetricId: $graphRightMetricId)
            }
        }
        .background(Theme.bg)
    }

    // MARK: Landscape immersive focus

    private var landscapeFocus: some View {
        ZStack(alignment: .topTrailing) {
            crossSectionCanvas
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if !chromeHidden {
                VStack {
                    CrossSectionReadoutView(
                        vizData: csVM.vizData ?? Self.emptyViz,
                        scrubDistanceNm: scrubDistanceNm,
                        scrubAltitudeFt: scrubAltitudeFt,
                        onSounding: goToSounding,
                        routeGraphMetricIds: [graphLeftMetricId, graphRightMetricId]
                    )
                    Spacer()
                }
                layersPill
                    .padding(Theme.cardPadding)
            }
        }
        .background(Theme.bg)
        .onTapGesture(count: 2) { withAnimation { chromeHidden.toggle() } } // Photos-style chrome toggle
    }

    // MARK: Chrome bar (portrait)

    private var chromeBar: some View {
        HStack(spacing: Theme.spacingM) {
            ModelSelectorView(selectedModel: Binding(
                get: { viewModel.selectedModel },
                set: { viewModel.selectedModel = $0 }
            ), models: viewModel.availableModels)
            Spacer()
            layersPill
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
    }

    private var layersPill: some View {
        Button {
            showingConfig = true
        } label: {
            Label("Layers", systemImage: "slider.horizontal.3")
                .font(.caption.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(Theme.primary.opacity(0.12), in: Capsule())
                .foregroundStyle(Theme.primary)
        }
        .buttonStyle(.plain)
    }

    // MARK: Canvas

    @ViewBuilder
    private var crossSectionCanvas: some View {
        if let vizData = csVM.vizData {
            let _ = trackingService.locationUpdateCount
            let aircraft = aircraftPosition
            let cursor = scrubDistanceNm ?? activePointDistanceNm
            let layers = csVM.enabledLayers

            Canvas { context, size in
                CrossSectionRenderer(data: vizData, enabledLayers: layers,
                                     selectedDistanceNm: cursor,
                                     aircraftPosition: aircraft)
                    .render(context: &context, size: size)
            }
            .frame(minHeight: 300)
            .aspectRatio(isLandscapeFocus ? nil : 2.0, contentMode: .fit)
            .background(GeometryReader { geo in
                Color.clear.onAppear { canvasSize = geo.size }
                    .onChange(of: geo.size) { _, newSize in canvasSize = newSize }
            })
            .gesture(scrubGesture)
        } else {
            switch viewModel.routeAnalysesState {
            case .idle, .loading:
                ProgressView("Loading cross-section...").frame(minHeight: 300)
            case .error(let error):
                ContentUnavailableView("Cross-Section Unavailable", systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            case .loaded:
                ContentUnavailableView("No Data for Model", systemImage: "chart.xyaxis.line",
                                       description: Text("No cross-section data available for \(viewModel.selectedModel). Try selecting a different model."))
            }
        }
    }

    // MARK: Scrub gesture (tap = zero-length drag)

    private var scrubGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in updateScrub(at: value.location) }
    }

    private func updateScrub(at location: CGPoint) {
        guard let vizData = csVM.vizData, canvasSize.width > 0, !vizData.points.isEmpty else { return }
        let transform = CoordTransform(size: canvasSize,
                                       maxDistanceNm: vizData.totalDistanceNm,
                                       maxAltitudeFt: vizData.flightCeilingFt)
        let dist = min(max(transform.xToDistance(location.x), 0), vizData.totalDistanceNm)
        let alt = min(max(transform.yToAltitude(location.y), 0), vizData.flightCeilingFt)
        scrubDistanceNm = dist
        scrubAltitudeFt = alt

        // Shared active point = nearest route point to the cursor (soundings are
        // discrete, so the Skew-T snaps to the nearest point, not the raw x).
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            let nearest = analyses.analyses.min {
                abs($0.distanceFromOriginNm - dist) < abs($1.distanceFromOriginNm - dist)
            }
            viewModel.activePointIndex = nearest?.pointIndex
        }
    }

    /// Consume a pending deep-link intent targeting the cross-section (§4.6
    /// "Show on cross-section ›"): enable the advisory's layer and move the
    /// scrub cursor to the focus point, then clear the intent.
    private func applyFocusIntent() {
        guard let intent = viewModel.focusIntent, intent.target == .crossSection else { return }
        if let layerId = intent.layerId { csVM.enableLayer(layerId) }
        if let dist = intent.distanceNm {
            scrubDistanceNm = dist
        } else if let pointDist = activePointDistanceNm {
            scrubDistanceNm = pointDist
        }
        if let alt = intent.altitudeFt { scrubAltitudeFt = alt }
        viewModel.clearFocusIntent()
    }

    private func goToSounding() {
        // Ensure an active point, then switch to the Skew-T tab (§4.7 deep-link).
        if viewModel.activePointIndex == nil, case .loaded(let analyses) = viewModel.routeAnalysesState {
            viewModel.activePointIndex = analyses.analyses.first?.pointIndex
        }
        viewModel.selectedTab = .skewT
    }

    // MARK: Helpers

    private var activePointDistanceNm: Double? {
        guard let idx = viewModel.activePointIndex,
              case .loaded(let analyses) = viewModel.routeAnalysesState,
              let rpa = analyses.analyses.first(where: { $0.pointIndex == idx })
        else { return nil }
        return rpa.distanceFromOriginNm
    }

    private var aircraftPosition: CrossSectionRenderer.AircraftPosition? {
        guard trackingService.isTracking, let pos = trackingService.projectedPosition,
              let altFt = pos.altitudeFt else { return nil }
        return .init(distanceNm: pos.distanceNm, altitudeFt: altFt, opacity: pos.opacity)
    }

    /// Empty placeholder so the readout strip can render before data loads.
    /// Static so it isn't re-allocated on every render pass (referenced twice).
    private static let emptyViz = VizRouteData(points: [], cruiseAltitudeFt: 0, ceilingAltitudeFt: 0, flightCeilingFt: 0,
                                               totalDistanceNm: 1, waypointMarkers: [], departureTime: "",
                                               flightDurationHours: 0, terrainProfile: nil)

    private func updateVizData() {
        switch viewModel.routeAnalysesState {
        case .idle, .loading, .error:
            break
        case .loaded(let analyses):
            var elevation: ElevationResponse? = nil
            if case .loaded(let elev) = viewModel.elevationState { elevation = elev }
            csVM.update(routeAnalyses: analyses, elevation: elevation, model: viewModel.selectedModel)
        }
    }
}
