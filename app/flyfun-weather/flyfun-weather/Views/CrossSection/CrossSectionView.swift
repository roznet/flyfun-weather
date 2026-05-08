import OSLog
import SwiftUI

private let logger = Logger(subsystem: "aero.flyfun.weather", category: "CrossSection")

/// SwiftUI Canvas wrapper for the cross-section visualization.
struct CrossSectionView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var csVM = CrossSectionViewModel()
    @State private var selectedPointIndex: Int?
    @State private var canvasSize: CGSize = .zero

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // Layer toggle chips
                layerChips

                // Cross-section canvas
                crossSectionCanvas

                // Route graph below
                RouteGraphView(viewModel: viewModel, vizData: csVM.vizData)

                // Skew-T detail for selected point
                if let pointIndex = selectedPointIndex {
                    Divider()
                    SkewTDetailView(viewModel: viewModel, pointIndex: pointIndex)
                        .frame(minHeight: 300)
                        .transition(.move(edge: .bottom))
                }
            }
        }
        .onChange(of: viewModel.selectedModel) {
            updateVizData()
        }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) {
            updateVizData()
        }
        .onChange(of: viewModel.elevationState.isLoaded) {
            updateVizData()
        }
        .task {
            updateVizData()
        }
    }

    @ViewBuilder
    private var crossSectionCanvas: some View {
        if let vizData = csVM.vizData {
            // Read observable values here (view body) so SwiftUI tracks changes.
            // Canvas closures are @escaping — observation doesn't work inside them.
            // locationUpdateCount forces re-evaluation since CLLocation is a reference type.
            let _ = trackingService.locationUpdateCount
            let aircraft = aircraftPosition
            let selectedNm = selectedDistanceNm
            let layers = csVM.enabledLayers

            Canvas { context, size in
                CrossSectionRenderer(data: vizData, enabledLayers: layers,
                                     selectedDistanceNm: selectedNm,
                                     aircraftPosition: aircraft)
                    .render(context: &context, size: size)
            }
            .frame(minHeight: 300)
            .aspectRatio(2.0, contentMode: .fit)
            .background(GeometryReader { geo in
                Color.clear.onAppear { canvasSize = geo.size }
                    .onChange(of: geo.size) { _, newSize in canvasSize = newSize }
            })
            .onTapGesture { location in
                handleTap(at: location)
            }
        } else {
            switch viewModel.routeAnalysesState {
            case .idle, .loading:
                ProgressView("Loading cross-section...")
                    .frame(minHeight: 300)
            case .error(let error):
                ContentUnavailableView("Cross-Section Unavailable", systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            case .loaded:
                ContentUnavailableView("No Data for Model", systemImage: "chart.xyaxis.line",
                                       description: Text("No cross-section data available for \(viewModel.selectedModel). Try selecting a different model."))
            }
        }
    }

    private var layerChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                // Model selector
                ModelSelectorView(selectedModel: Binding(
                    get: { viewModel.selectedModel },
                    set: { viewModel.selectedModel = $0 }
                ), models: viewModel.availableModels)

                Divider().frame(height: 20)

                // Method-picker dropdowns: clouds / icing / turbulence / convection.
                // Each is a single mutually-exclusive choice with a "None" option.
                ForEach(LayerGroup.allCases.filter(\.isMethodGroup), id: \.self) { group in
                    methodMenu(for: group)
                }

                Divider().frame(height: 20)

                // Toggle chips: terrain / reference / temperature / stability layers
                // remain independently toggleable.
                ForEach(CrossSectionLayer.allLayers.filter { !$0.group.isMethodGroup }, id: \.id) { layer in
                    toggleChip(for: layer)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
        }
    }

    /// Dropdown menu for a method group (clouds / icing / turbulence / convection).
    /// Items: "None" + each method in the group, with a checkmark on the active one.
    @ViewBuilder
    private func methodMenu(for group: LayerGroup) -> some View {
        let active = csVM.activeMethod(for: group)
        let activeLabel = active.flatMap { CrossSectionLayer.methodLabels[$0] } ?? "None"

        Menu {
            Button {
                csVM.setMethod(nil, for: group)
            } label: {
                if active == nil {
                    Label("None", systemImage: "checkmark")
                } else {
                    Text("None")
                }
            }
            ForEach(CrossSectionLayer.methodGroupOrder[group] ?? [], id: \.self) { layerId in
                Button {
                    csVM.setMethod(layerId, for: group)
                } label: {
                    if active == layerId {
                        Label(CrossSectionLayer.methodLabels[layerId] ?? layerId, systemImage: "checkmark")
                    } else {
                        Text(CrossSectionLayer.methodLabels[layerId] ?? layerId)
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Text("\(group.label): \(activeLabel)")
                    .font(.caption2)
                Image(systemName: "chevron.down")
                    .font(.system(size: 8, weight: .semibold))
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(active != nil ? Color.accentColor.opacity(0.15) : Color.clear)
            .foregroundStyle(active != nil ? .primary : .secondary)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(active != nil ? Color.accentColor : Color.gray.opacity(0.3), lineWidth: 0.5))
        }
    }

    @ViewBuilder
    private func toggleChip(for layer: any CrossSectionLayerProtocol) -> some View {
        let enabled = csVM.enabledLayers[layer.id] ?? false
        Button {
            csVM.toggleLayer(layer.id)
        } label: {
            Text(layer.name)
                .font(.caption2)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(enabled ? Color.accentColor.opacity(0.15) : Color.clear)
                .foregroundStyle(enabled ? .primary : .secondary)
                .clipShape(Capsule())
                .overlay(Capsule().stroke(enabled ? Color.accentColor : Color.gray.opacity(0.3), lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }

    /// Aircraft position for cross-section overlay, from flight tracking service.
    private var aircraftPosition: CrossSectionRenderer.AircraftPosition? {
        guard trackingService.isTracking, let pos = trackingService.projectedPosition,
              let altFt = pos.altitudeFt else { return nil }
        return .init(distanceNm: pos.distanceNm, altitudeFt: altFt, opacity: pos.opacity)
    }

    /// Distance along route for the selected point, used to draw the vertical indicator.
    private var selectedDistanceNm: Double? {
        guard let idx = selectedPointIndex,
              case .loaded(let analyses) = viewModel.routeAnalysesState,
              let rpa = analyses.analyses.first(where: { $0.pointIndex == idx })
        else { return nil }
        return rpa.distanceFromOriginNm
    }

    // MARK: - Tap handling

    private func handleTap(at location: CGPoint) {
        guard let vizData = csVM.vizData else { return }
        let points = vizData.points
        guard !points.isEmpty, canvasSize.width > 0 else { return }

        let transform = CoordTransform(
            size: canvasSize,
            maxDistanceNm: vizData.totalDistanceNm,
            maxAltitudeFt: vizData.flightCeilingFt
        )
        let tapDistanceNm = transform.xToDistance(location.x)
        let nearest = points.enumerated().min(by: {
            abs($0.element.distanceNm - tapDistanceNm) < abs($1.element.distanceNm - tapDistanceNm)
        })

        guard let nearest else { return }

        // Find the point_index from route analyses
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            let routePoint = analyses.analyses.min(by: {
                abs($0.distanceFromOriginNm - vizData.points[nearest.offset].distanceNm) <
                abs($1.distanceFromOriginNm - vizData.points[nearest.offset].distanceNm)
            })
            if let routePoint {
                withAnimation {
                    if selectedPointIndex == routePoint.pointIndex {
                        selectedPointIndex = nil // toggle off
                    } else {
                        selectedPointIndex = routePoint.pointIndex
                    }
                }
                logger.info("Tapped point \(routePoint.pointIndex) at \(routePoint.distanceFromOriginNm)nm")
            }
        }
    }

    private func updateVizData() {
        switch viewModel.routeAnalysesState {
        case .idle:
            logger.debug("updateVizData: routeAnalysesState is idle")
        case .loading:
            logger.debug("updateVizData: routeAnalysesState is loading")
        case .error(let error):
            logger.error("updateVizData: routeAnalysesState error: \(error)")
        case .loaded(let analyses):
            logger.info("updateVizData: loaded \(analyses.analyses.count) points, model=\(viewModel.selectedModel), models=\(analyses.models)")
            var elevation: ElevationResponse? = nil
            if case .loaded(let elev) = viewModel.elevationState {
                elevation = elev
            }
            csVM.update(routeAnalyses: analyses, elevation: elevation, model: viewModel.selectedModel)
            if let viz = csVM.vizData {
                logger.info("vizData: \(viz.points.count) points, \(viz.totalDistanceNm)nm, ceiling=\(viz.flightCeilingFt)ft")
            } else {
                logger.warning("vizData is nil after update")
            }
        }
    }
}
