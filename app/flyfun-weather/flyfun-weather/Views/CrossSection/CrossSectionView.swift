import OSLog
import SwiftUI

private let logger = Logger(subsystem: "aero.flyfun.weather", category: "CrossSection")

/// SwiftUI Canvas wrapper for the cross-section visualization.
struct CrossSectionView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var csVM = CrossSectionViewModel()
    @State private var canvasSize: CGSize = .zero
    @State private var scrubDistanceNm: Double?
    @State private var scrubAltitudeFt: Double?
    @State private var routeGraphLeftMetricId = "headwind"
    @State private var routeGraphRightMetricId = "cloud-cover"
    @State private var isRouteGraphVisible = true
    @State private var isConfigPresented = false
    @State private var isFocusMode = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                if !isFocusMode {
                    crossSectionChrome
                }

                if let vizData = csVM.vizData {
                    CrossSectionReadoutStrip(
                        viewModel: viewModel,
                        vizData: vizData,
                        enabledLayers: csVM.enabledLayers,
                        distanceNm: selectedDistanceNm,
                        altitudeFt: scrubAltitudeFt,
                        leftMetricId: routeGraphLeftMetricId,
                        rightMetricId: routeGraphRightMetricId
                    )
                }

                // Cross-section canvas
                crossSectionCanvas

                if let activePoint = viewModel.activePoint {
                    CrossSectionActivePointBar(viewModel: viewModel, activePoint: activePoint)
                }

                // Route graph below
                if isRouteGraphVisible && !isFocusMode {
                    RouteGraphView(
                        viewModel: viewModel,
                        vizData: csVM.vizData,
                        selectedDistanceNm: selectedDistanceNm,
                        leftMetricId: $routeGraphLeftMetricId,
                        rightMetricId: $routeGraphRightMetricId,
                        onScrubDistance: scrubToDistance
                    )
                }
            }
        }
        .sheet(isPresented: $isConfigPresented) {
            CrossSectionConfigSheet(csVM: csVM)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
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

    private var crossSectionChrome: some View {
        HStack(spacing: WeatherTheme.Spacing.sm) {
            ModelSelectorView(selectedModel: Binding(
                get: { viewModel.selectedModel },
                set: { viewModel.selectedModel = $0 }
            ), models: viewModel.availableModels)

            Spacer()

            Button {
                isConfigPresented = true
            } label: {
                Label("Layers", systemImage: "slider.horizontal.3")
                    .font(.caption.bold())
            }
            .buttonStyle(.bordered)

            Button {
                withAnimation(.snappy) {
                    isRouteGraphVisible.toggle()
                }
            } label: {
                Image(systemName: isRouteGraphVisible ? "chart.bar.xaxis" : "chart.bar.xaxis.ascending")
            }
            .accessibilityLabel(isRouteGraphVisible ? "Hide route graph" : "Show route graph")

            Button {
                withAnimation(.snappy) {
                    isFocusMode.toggle()
                }
            } label: {
                Image(systemName: "arrow.down.right.and.arrow.up.left")
            }
            .accessibilityLabel("Focus cross-section")
        }
        .padding(.horizontal, WeatherTheme.Spacing.lg)
        .padding(.vertical, WeatherTheme.Spacing.sm)
        .background(.regularMaterial)
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
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        handleScrub(at: value.location)
                    }
                    .onEnded { value in
                        handleScrub(at: value.location)
                    }
            )
            .overlay(alignment: .topTrailing) {
                if isFocusMode {
                    HStack(spacing: WeatherTheme.Spacing.sm) {
                        Button {
                            isConfigPresented = true
                        } label: {
                            Image(systemName: "slider.horizontal.3")
                        }
                        .accessibilityLabel("Layers")

                        Button {
                            withAnimation(.snappy) {
                                isFocusMode = false
                            }
                        } label: {
                            Image(systemName: "arrow.up.left.and.arrow.down.right")
                        }
                        .accessibilityLabel("Exit focus")
                    }
                    .padding(WeatherTheme.Spacing.sm)
                    .background(.regularMaterial, in: Capsule())
                    .padding(WeatherTheme.Spacing.sm)
                }
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

    /// Aircraft position for cross-section overlay, from flight tracking service.
    private var aircraftPosition: CrossSectionRenderer.AircraftPosition? {
        guard trackingService.isTracking, let pos = trackingService.projectedPosition,
              let altFt = pos.altitudeFt else { return nil }
        return .init(distanceNm: pos.distanceNm, altitudeFt: altFt, opacity: pos.opacity)
    }

    /// Distance along route for the selected point, used to draw the vertical indicator.
    private var selectedDistanceNm: Double? {
        scrubDistanceNm ?? viewModel.activePoint?.distanceNm
    }

    // MARK: - Scrub handling

    private func handleScrub(at location: CGPoint) {
        guard let vizData = csVM.vizData else { return }
        guard !vizData.points.isEmpty, canvasSize.width > 0 else { return }

        let transform = CoordTransform(
            size: canvasSize,
            maxDistanceNm: vizData.totalDistanceNm,
            maxAltitudeFt: vizData.flightCeilingFt
        )
        let clampedX = min(max(location.x, transform.plotArea.left), transform.plotArea.right)
        let clampedY = min(max(location.y, transform.plotArea.top), transform.plotArea.bottom)
        let distanceNm = min(max(transform.xToDistance(clampedX), 0), vizData.totalDistanceNm)
        let altitudeFt = min(max(transform.yToAltitude(clampedY), 0), vizData.flightCeilingFt)

        scrubDistanceNm = distanceNm
        scrubAltitudeFt = altitudeFt
        setActivePoint(nearestTo: distanceNm)
    }

    private func scrubToDistance(_ distanceNm: Double) {
        guard let vizData = csVM.vizData else { return }
        let clamped = min(max(distanceNm, 0), vizData.totalDistanceNm)
        scrubDistanceNm = clamped
        setActivePoint(nearestTo: clamped)
    }

    private func setActivePoint(nearestTo distanceNm: Double) {
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            let routePoint = analyses.analyses.min(by: {
                abs($0.distanceFromOriginNm - distanceNm) < abs($1.distanceFromOriginNm - distanceNm)
            })
            if let routePoint {
                withAnimation(.snappy) {
                    viewModel.setActivePoint(routePoint)
                }
                logger.debug("Scrubbed point \(routePoint.pointIndex) at \(routePoint.distanceFromOriginNm)nm")
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

private struct CrossSectionActivePointBar: View {
    let viewModel: BriefingViewModel
    let activePoint: BriefingActivePoint
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: WeatherTheme.Spacing.sm) {
            Label(pointLabel, systemImage: "scope")
                .font(.caption.monospacedDigit())
                .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                .lineLimit(1)

            Spacer()

            Button {
                viewModel.setFocusIntent(.init(
                    target: .skewT,
                    model: viewModel.selectedModel,
                    pointIndex: activePoint.pointIndex,
                    distanceNm: activePoint.distanceNm
                ))
            } label: {
                Label("Sounding", systemImage: "chart.line.uptrend.xyaxis")
                    .font(.caption.bold())
            }
            .buttonStyle(.bordered)
        }
        .padding(.horizontal, WeatherTheme.Spacing.lg)
        .padding(.vertical, WeatherTheme.Spacing.sm)
        .background(WeatherTheme.surface(colorScheme))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WeatherTheme.border(colorScheme))
                .frame(height: 0.5)
        }
    }

    private var pointLabel: String {
        let name = activePoint.waypointIcao ?? "Point \(activePoint.pointIndex)"
        return "\(name) · \(Int(activePoint.distanceNm)) nm · \(viewModel.selectedModel.uppercased())"
    }
}

private struct CrossSectionReadoutStrip: View {
    let viewModel: BriefingViewModel
    let vizData: VizRouteData
    let enabledLayers: [String: Bool]
    let distanceNm: Double?
    let altitudeFt: Double?
    let leftMetricId: String
    let rightMetricId: String
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: WeatherTheme.Spacing.sm) {
                ForEach(readoutItems) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 4) {
                            Circle()
                                .fill(item.color)
                                .frame(width: 6, height: 6)
                            Text(item.title)
                                .font(.caption2)
                                .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                        }
                        Text(item.value)
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(WeatherTheme.text(colorScheme))
                    }
                    .padding(.horizontal, WeatherTheme.Spacing.sm)
                    .padding(.vertical, WeatherTheme.Spacing.xs)
                    .background(WeatherTheme.surface(colorScheme), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.control))
                    .overlay {
                        RoundedRectangle(cornerRadius: WeatherTheme.Radius.control)
                            .stroke(WeatherTheme.border(colorScheme), lineWidth: 0.5)
                    }
                }

                Button {
                    viewModel.setFocusIntent(.init(
                        target: .skewT,
                        model: viewModel.selectedModel,
                        pointIndex: viewModel.activePoint?.pointIndex,
                        distanceNm: distanceNm
                    ))
                } label: {
                    Label("Sounding", systemImage: "chart.line.uptrend.xyaxis")
                        .font(.caption.bold())
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(.horizontal, WeatherTheme.Spacing.lg)
            .padding(.vertical, WeatherTheme.Spacing.sm)
        }
        .background(.regularMaterial)
    }

    private var nearestPoint: VizPoint? {
        guard let distanceNm else { return vizData.points.first }
        return vizData.points.min {
            abs($0.distanceNm - distanceNm) < abs($1.distanceNm - distanceNm)
        }
    }

    private var readoutItems: [CrossSectionReadoutItem] {
        guard let point = nearestPoint else { return [] }

        var items: [CrossSectionReadoutItem] = [
            .init(id: "distance", title: "Distance", value: "\(Int(point.distanceNm)) nm", color: WeatherTheme.primary(colorScheme)),
            .init(id: "time", title: "ETA", value: timeLabel(point.time), color: .secondary),
        ]

        if let altitudeFt {
            items.append(.init(id: "altitude", title: "Cursor", value: altitudeLabel(altitudeFt), color: .orange))
        }

        if let cloud = cloudReadout(point: point) {
            items.append(cloud)
        }
        if let icing = icingReadout(point: point) {
            items.append(icing)
        }
        if let turbulence = turbulenceReadout(point: point) {
            items.append(turbulence)
        }
        if let convection = convectionReadout(point: point) {
            items.append(convection)
        }
        if let metric = metricReadout(id: leftMetricId, point: point) {
            items.append(metric)
        }
        if rightMetricId != "none", let metric = metricReadout(id: rightMetricId, point: point) {
            items.append(metric)
        }
        return items
    }

    private func cloudReadout(point: VizPoint) -> CrossSectionReadoutItem? {
        guard let active = activeMethod(for: .clouds) else { return nil }
        let layers = active.contains("nwp") ? (point.nwpCloudLayers ?? []) : point.cloudLayers
        if let altitudeFt,
           let layer = layers.first(where: { altitudeFt >= $0.baseFt && altitudeFt <= $0.topFt }) {
            return .init(id: "clouds", title: "Cloud", value: "\(layer.coverage.uppercased()) \(altitudeLabel(layer.baseFt))-\(altitudeLabel(layer.topFt))", color: .gray)
        }
        return .init(id: "clouds", title: "Cloud", value: "\(Int(point.cloudCoverTotalPct))%", color: .gray)
    }

    private func icingReadout(point: VizPoint) -> CrossSectionReadoutItem? {
        guard let active = activeMethod(for: .icing) else { return nil }
        let zones: [VizIcingZone]
        switch active {
        case "icing-ogimet-nwp-bands":
            zones = point.icingOgimetNwpZones
        case "sfip-bands":
            zones = point.sfipZones.map { VizIcingZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.type) }
        default:
            zones = point.icingZones
        }
        guard !zones.isEmpty else { return nil }
        if let altitudeFt,
           let zone = zones.first(where: { altitudeFt >= $0.baseFt && altitudeFt <= $0.topFt }) {
            return .init(id: "icing", title: "Icing", value: "\(zone.risk.capitalized) \(altitudeLabel(zone.baseFt))-\(altitudeLabel(zone.topFt))", color: .cyan)
        }
        let worst = zones.max { icingRank($0.risk) < icingRank($1.risk) }
        return worst.map { .init(id: "icing", title: "Icing", value: $0.risk.capitalized, color: .cyan) }
    }

    private func turbulenceReadout(point: VizPoint) -> CrossSectionReadoutItem? {
        guard activeMethod(for: .turbulence) != nil, !point.catLayers.isEmpty else { return nil }
        if let altitudeFt,
           let layer = point.catLayers.first(where: { altitudeFt >= $0.baseFt && altitudeFt <= $0.topFt }) {
            return .init(id: "turbulence", title: "Turb", value: "\(layer.risk.capitalized) \(altitudeLabel(layer.baseFt))-\(altitudeLabel(layer.topFt))", color: .orange)
        }
        let worst = point.catLayers.max { icingRank($0.risk) < icingRank($1.risk) }
        return worst.map { .init(id: "turbulence", title: "Turb", value: $0.risk.capitalized, color: .orange) }
    }

    private func convectionReadout(point: VizPoint) -> CrossSectionReadoutItem? {
        guard let active = activeMethod(for: .convection) else { return nil }
        let risk = active == "nwp-convective-bg" ? point.nwpConvectiveRisk : point.convectiveRisk
        guard risk != "none" else { return nil }
        return .init(id: "convection", title: "Convection", value: risk.capitalized, color: .red)
    }

    private func metricReadout(id: String, point: VizPoint) -> CrossSectionReadoutItem? {
        guard let metric = RouteGraphMetrics.metric(byId: id),
              let value = metric.getValue(point) else { return nil }
        return .init(id: "metric-\(id)", title: metric.label, value: metric.formatValue(value), color: metric.color)
    }

    private func activeMethod(for group: LayerGroup) -> String? {
        CrossSectionLayer.methodGroupOrder[group]?.first { enabledLayers[$0] == true }
    }

    private func altitudeLabel(_ feet: Double) -> String {
        if feet >= 10_000 {
            return "FL\(Int(feet / 100))"
        }
        return "\(Int(feet)) ft"
    }

    private func timeLabel(_ raw: String) -> String {
        if let date = ISO8601DateFormatter().date(from: raw) {
            return DateFormatter.utcTime.string(from: date)
        }
        return raw
    }

    private func icingRank(_ risk: String) -> Int {
        switch risk.lowercased() {
        case "severe", "extreme": 3
        case "moderate", "high": 2
        case "light", "low": 1
        default: 0
        }
    }
}

private struct CrossSectionReadoutItem: Identifiable {
    let id: String
    let title: String
    let value: String
    let color: Color
}

private struct CrossSectionConfigSheet: View {
    @Bindable var csVM: CrossSectionViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: WeatherTheme.Spacing.lg) {
                    presetSection
                    methodSection
                    referenceSection
                }
                .padding(WeatherTheme.Spacing.lg)
            }
            .background(WeatherTheme.background(colorScheme))
            .navigationTitle("Cross-Section")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var presetSection: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            Text("Preset")
                .font(.headline)

            HStack(spacing: WeatherTheme.Spacing.sm) {
                ForEach(CrossSectionLayerPreset.allCases, id: \.self) { preset in
                    Button {
                        csVM.applyLayerPreset(preset.enabledLayers)
                    } label: {
                        Label(preset.label, systemImage: preset.systemImage)
                            .font(.caption.bold())
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
        .padding(WeatherTheme.Spacing.lg)
        .background(WeatherTheme.surface(colorScheme), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.card))
    }

    private var methodSection: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            Text("Methods")
                .font(.headline)

            ForEach(LayerGroup.allCases.filter(\.isMethodGroup), id: \.self) { group in
                methodRow(for: group)
            }
        }
        .padding(WeatherTheme.Spacing.lg)
        .background(WeatherTheme.surface(colorScheme), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.card))
    }

    private var referenceSection: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.md) {
            Text("Reference")
                .font(.headline)

            ForEach(CrossSectionLayer.allLayers.filter { !$0.group.isMethodGroup }, id: \.id) { layer in
                Toggle(isOn: layerBinding(layer.id)) {
                    HStack(spacing: WeatherTheme.Spacing.sm) {
                        swatch(for: layer.id)
                        Text(layer.name)
                    }
                }
            }
        }
        .padding(WeatherTheme.Spacing.lg)
        .background(WeatherTheme.surface(colorScheme), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.card))
    }

    private func methodRow(for group: LayerGroup) -> some View {
        let active = csVM.activeMethod(for: group)
        let activeLabel = active.flatMap { CrossSectionLayer.methodLabels[$0] } ?? "None"

        return HStack(spacing: WeatherTheme.Spacing.md) {
            swatch(for: active)
            Text(group.label)
                .font(.subheadline.bold())
            Spacer()
            Menu {
                Button {
                    csVM.setMethod(nil, for: group)
                } label: {
                    menuLabel("None", selected: active == nil)
                }
                ForEach(CrossSectionLayer.methodGroupOrder[group] ?? [], id: \.self) { layerId in
                    Button {
                        csVM.setMethod(layerId, for: group)
                    } label: {
                        menuLabel(CrossSectionLayer.methodLabels[layerId] ?? layerId, selected: active == layerId)
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    Text(activeLabel)
                        .font(.caption.bold())
                    Image(systemName: "chevron.down")
                        .font(.caption2)
                }
                .padding(.horizontal, WeatherTheme.Spacing.sm)
                .padding(.vertical, WeatherTheme.Spacing.xs)
                .background(WeatherTheme.primary(colorScheme).opacity(active == nil ? 0 : 0.10), in: Capsule())
            }
            .buttonStyle(.plain)
        }
    }

    private func menuLabel(_ title: String, selected: Bool) -> some View {
        HStack {
            Text(title)
            if selected {
                Image(systemName: "checkmark")
            }
        }
    }

    private func layerBinding(_ id: String) -> Binding<Bool> {
        Binding(
            get: { csVM.enabledLayers[id] ?? false },
            set: { csVM.setLayer(id, enabled: $0) }
        )
    }

    private func swatch(for layerId: String?) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(swatchColor(for: layerId))
            .frame(width: 18, height: 12)
            .overlay {
                RoundedRectangle(cornerRadius: 3)
                    .stroke(WeatherTheme.border(colorScheme), lineWidth: 0.5)
            }
    }

    private func swatchColor(for layerId: String?) -> Color {
        guard let layerId else { return .clear }
        if layerId.contains("cloud") { return .gray.opacity(0.65) }
        if layerId.contains("icing") || layerId.contains("sfip") { return .cyan.opacity(0.75) }
        if layerId.contains("cat") { return .orange.opacity(0.75) }
        if layerId.contains("convective") { return .red.opacity(0.65) }
        if layerId.contains("terrain") { return ColorScales.terrainFill }
        if layerId.contains("freezing") { return ColorScales.freezingLevelColor }
        if layerId.contains("minus-10") { return ColorScales.minus10cColor }
        if layerId.contains("minus-20") { return ColorScales.minus20cColor }
        if layerId == "lcl" { return ColorScales.lclColor }
        if layerId == "lfc" { return ColorScales.lfcColor }
        if layerId == "el" { return ColorScales.elColor }
        return WeatherTheme.primary(colorScheme)
    }
}

private enum CrossSectionLayerPreset: CaseIterable {
    case gramet
    case windy
    case foreFlight

    var label: String {
        switch self {
        case .gramet: "GRAMET"
        case .windy: "Windy"
        case .foreFlight: "ForeFlight"
        }
    }

    var systemImage: String {
        switch self {
        case .gramet: "cloud"
        case .windy: "wind"
        case .foreFlight: "square.grid.3x3"
        }
    }

    var enabledLayers: [String: Bool] {
        var layers = CrossSectionLayer.defaultEnabled
        switch self {
        case .gramet:
            return layers
        case .windy:
            layers["soft-nwp-cloud-bands"] = false
            layers["nwp-cloud-bands"] = true
            layers["square-nwp-cloud-bands"] = false
            layers["icing-ogimet-nwp-bands"] = true
            layers["nwp-convective-bg"] = true
            layers["minus-10c"] = true
            layers["minus-20c"] = true
            return layers
        case .foreFlight:
            layers["soft-nwp-cloud-bands"] = false
            layers["square-nwp-cloud-bands"] = true
            layers["nwp-cloud-bands"] = false
            layers["icing-ogimet-nwp-bands"] = true
            layers["cat-bands"] = true
            layers["nwp-convective-bg"] = true
            return layers
        }
    }
}
