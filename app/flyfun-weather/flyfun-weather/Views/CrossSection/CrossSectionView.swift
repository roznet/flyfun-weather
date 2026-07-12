import SwiftUI
import TipKit

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
    /// Persisted (#9) so the chosen metrics survive relaunch, like the web.
    @AppStorage("crossSectionGraphLeftMetric") private var graphLeftMetricId = "headwind"
    @AppStorage("crossSectionGraphRightMetric") private var graphRightMetricId = "cloud-cover"
    /// Scroll-to target inside the tab (#310): the "Sounding ›" deep-link and a
    /// `FocusIntent.target == .skewT` set this to "skewt"; the ScrollViewReader
    /// scrolls to the embedded Skew-T and resets it to nil.
    @State private var scrollTarget: String?
    @Environment(\.verticalSizeClass) private var vSizeClass

    // Contextual tips (#312), gated on this tab being visible.
    private let layersTip = CrossSectionLayersTip()
    private let scrubTip = CrossSectionScrubTip()
    /// One-shot guard so the scrub tip is retired exactly once — and only in
    /// portrait, where its `TipView` is actually rendered. Landscape focus has
    /// no scrub `TipView` (distraction-free by design), so a landscape drag must
    /// not consume the tip before the user ever sees it.
    @State private var scrubTipInvalidated = false

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
            // The Highlight visibility toggle shows only while a highlight is
            // active AND the selected model has geometry for it (#374).
            CrossSectionConfigSheet(csVM: csVM, highlightAvailable: derivedHighlights != nil)
        }
        // Gate the cross-section tips on this tab being on screen so they never
        // fire from the Advisory/Map tabs (#312).
        .onAppear { setCrossSectionTipsVisible(true) }
        .onDisappear { setCrossSectionTipsVisible(false) }
    }

    /// Single place that flips the cross-section tips' visibility gate. TipKit
    /// requires each Tip type to own its `@Parameter`, so every cross-section
    /// tip must be listed here — add new ones in one spot instead of scattering
    /// `onAppear`/`onDisappear` writes.
    private func setCrossSectionTipsVisible(_ visible: Bool) {
        CrossSectionLayersTip.crossSectionVisible = visible
        CrossSectionScrubTip.crossSectionVisible = visible
    }

    // MARK: Portrait layout

    private var portrait: some View {
        ScrollViewReader { proxy in
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
                    // "Tap any point" coachmark above the canvas (#312); cleared
                    // on the first scrub via `updateScrub`. Only render once
                    // there's a canvas to interact with — otherwise the tip
                    // would be consumed coaching against a loading/error
                    // placeholder.
                    if csVM.vizData != nil {
                        TipView(scrubTip)
                            .padding(.horizontal, Theme.cardPadding)
                    }
                    crossSectionCanvas
                    RouteGraphView(viewModel: viewModel, vizData: csVM.vizData, scrubDistanceNm: scrubDistanceNm,
                                   leftMetricId: $graphLeftMetricId, rightMetricId: $graphRightMetricId)
                    skewTSection
                }
            }
            .background(Theme.bg)
            .onChange(of: scrollTarget) { _, target in
                guard let target else { return }
                withAnimation(.easeInOut(duration: 0.3)) { proxy.scrollTo(target, anchor: .top) }
                scrollTarget = nil
            }
            .onAppear {
                // The landscape-immersive layout has no ScrollViewReader, so a
                // "Sounding ›" tap there sets `scrollTarget` with nothing to
                // consume it. Returning to portrait re-mounts this scroll view —
                // honor the pending target here (onChange won't fire: unchanged).
                if let target = scrollTarget {
                    proxy.scrollTo(target, anchor: .top)
                    scrollTarget = nil
                }
            }
        }
    }

    /// Skew-T folded under the cross-section (#310): one scroll, bounded height
    /// so the page stays usable on iPhone. The "Sounding ›" deep-link scrolls
    /// here instead of switching tabs.
    private var skewTSection: some View {
        VStack(spacing: 0) {
            Divider()
            SkewTTabView(viewModel: viewModel, embeddedHeight: 480)
        }
        .id("skewt")
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
                set: { viewModel.selectModel($0) }  // sticky pick (#8/#9)
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
            layersTip.invalidate(reason: .actionPerformed)
        } label: {
            Label("Layers", systemImage: "slider.horizontal.3")
                .font(.caption.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(Theme.primary.opacity(0.12), in: Capsule())
                .foregroundStyle(Theme.primary)
        }
        .buttonStyle(.plain)
        .popoverTip(layersTip)
    }

    // MARK: Canvas

    @ViewBuilder
    private var crossSectionCanvas: some View {
        if let vizData = csVM.vizData {
            let _ = trackingService.locationUpdateCount
            let aircraft = aircraftPosition
            let cursor = scrubDistanceNm ?? activePointDistanceNm
            // Effective (not stored) layer set: disables what this model can't
            // provide and substitutes same-style DD clouds / Ogimet-DD / thermo
            // convective for unavailable NWP methods, so a far-out ECMWF flight
            // renders DD clouds instead of a blank NWP layer (matches web). The
            // stored preference is untouched. (#nwp-cloud-layer-ios-web)
            let layers = csVM.effectiveEnabledLayers
            // Read themeId here so the Canvas re-renders when the theme changes
            // (it's an @Observable dependency, like `layers`). (#320)
            let themeId = csVM.themeId

            // Static scene (sky + cloud bands + axes) is the expensive pass and is
            // gated behind an `Equatable` key so a scrub tick — which only changes
            // `cursor`/`aircraft` — does NOT re-run its ~400 gradient fills. The
            // thin cursor rule + aircraft marker live in a cheap overlay Canvas
            // that redraws every tick instead (#303). `themeId` is part of the gate
            // so a theme switch repaints the static layers, not just the overlay (#320).
            // The derived advisory highlight (#374) rides the same gate: it's a
            // small Equatable value, so re-deriving it each body pass is cheap and
            // only an actual geometry change repaints the scene.
            StaticCrossSectionScene(data: vizData, enabledLayers: layers,
                                    dataVersion: csVM.dataVersion, themeId: themeId,
                                    renderSize: canvasSize,
                                    highlights: csVM.highlightVisible ? derivedHighlights : nil)
                // `.equatable()` is load-bearing: it forces SwiftUI to gate the
                // redraw on the custom `==` (dataVersion + layers + themeId + size). Without
                // it the reconciler falls back to reflecting the stored properties, can't
                // compare the non-Equatable `VizRouteData`, and redraws every scrub
                // tick — defeating the split (#303). Do not remove.
                .equatable()
                .frame(minHeight: 300)
                // Portrait constrains to a 2:1 artifact; landscape fills the screen.
                // (Passing `nil` to aspectRatio means "use intrinsic ratio" — a
                // Canvas has none, which collapsed the chart to a sliver. #9)
                .modifier(CanvasAspectModifier(landscape: isLandscapeFocus))
                .overlay {
                    CrossSectionCursorOverlay(data: vizData, cursorDistanceNm: cursor, aircraft: aircraft)
                }
                .background(GeometryReader { geo in
                    Color.clear.onAppear { canvasSize = geo.size }
                        .onChange(of: geo.size) { _, newSize in canvasSize = newSize }
                })
                .gesture(scrubGesture)
                // A Canvas has no intrinsic a11y children, so expose it as a
                // single element with a stable id. The XCUITest cross-section
                // journey (#318) asserts this renders (only present once the
                // canvas draws — vs. the loading/error placeholder below, which
                // has no such id). Kept on this leaf, not a wrapping container,
                // so no parent identifier can propagate over and clobber it.
                .accessibilityElement()
                .accessibilityIdentifier("crossSectionCanvas")
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
        // First real scrub retires the "tap any point" tip — but only in
        // portrait, where the tip is shown. The drag fires this repeatedly, so
        // short-circuit after the first to avoid hammering TipKit.
        if !scrubTipInvalidated && !isLandscapeFocus {
            scrubTipInvalidated = true
            scrubTip.invalidate(reason: .actionPerformed)
        }
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
        guard let intent = viewModel.focusIntent,
              intent.target == .crossSection || intent.target == .skewT else { return }
        // Same-advisory re-tap toggles the highlight OFF while the lens stays
        // (#374): capture "already on" BEFORE the lens application below clears
        // the highlight, then skip re-activation.
        let highlightAlreadyOn = intent.advisoryId != nil
            && csVM.activeHighlightAdvisoryId == intent.advisoryId
        // An advisory lens configures the whole view; a single layerId just
        // force-enables one layer. Apply the lens first so a layerId can refine it.
        if let presetId = intent.advisoryPresetId,
           let preset = CrossSectionPresets.advisory[presetId] {
            csVM.applyAdvisoryPreset(preset)
        }
        if let layerId = intent.layerId { csVM.enableLayer(layerId) }
        // Highlight activation (#374). Old packs / non-emitting advisories carry
        // no highlight geometry, so the guard falls through and the action
        // behaves exactly as before highlights existed.
        var peakDistNm: Double?
        if let advisoryId = intent.advisoryId, !highlightAlreadyOn,
           case .loaded(let manifest) = viewModel.advisoriesState,
           let advisory = manifest.advisories.first(where: { $0.advisoryId == advisoryId }),
           advisory.perModel.contains(where: { $0.highlights != nil }) {
            // Switch to the advisory's representative model so the highlight
            // reflects the aggregate verdict. Assigned directly (not via
            // `selectModel`) — a programmatic switch must not overwrite the
            // user's sticky model preference.
            if let rep = CrossSectionViewModel.representativeModel(for: advisory),
               viewModel.availableModels.contains(rep) {
                viewModel.selectedModel = rep
            }
            csVM.setHighlightAdvisory(advisoryId)  // also force-shows the highlight
            peakDistNm = advisory.perModel
                .first(where: { $0.model == viewModel.selectedModel })?
                .highlights?.peakDistNm
        }
        if let peak = peakDistNm {
            // Land the cursor (and the shared active point → readout/Skew-T
            // linkage) on the advisory's peak.
            scrubDistanceNm = peak
            selectNearestPoint(to: peak)
        } else if let dist = intent.distanceNm {
            scrubDistanceNm = dist
        } else if let pointDist = activePointDistanceNm {
            scrubDistanceNm = pointDist
        }
        if let alt = intent.altitudeFt { scrubAltitudeFt = alt }
        // A skewT-targeted intent (#310) means "scroll to the embedded Skew-T".
        if intent.target == .skewT { scrollTarget = "skewt" }
        viewModel.clearFocusIntent()
    }

    /// Snap the shared active route point to the nearest analysis point (same
    /// rule as `updateScrub` — soundings are discrete).
    private func selectNearestPoint(to dist: Double) {
        guard case .loaded(let analyses) = viewModel.routeAnalysesState else { return }
        let nearest = analyses.analyses.min {
            abs($0.distanceFromOriginNm - dist) < abs($1.distanceFromOriginNm - dist)
        }
        viewModel.activePointIndex = nearest?.pointIndex
    }

    /// Advisory highlight geometry for the tracked advisory × the rendered model
    /// (#374). Derived, never stored — model switches and pack recalcs update it
    /// automatically, and it degrades to nil (highlight + toggle hidden) when the
    /// advisory is gone or the pack/model has no data.
    private var derivedHighlights: VizAdvisoryHighlights? {
        guard case .loaded(let manifest) = viewModel.advisoriesState else { return nil }
        return CrossSectionViewModel.deriveHighlights(
            manifest: manifest,
            advisoryId: csVM.activeHighlightAdvisoryId,
            model: viewModel.selectedModel)
    }

    private func goToSounding() {
        // Ensure an active point, then scroll to the embedded Skew-T (#310 —
        // Skew-T is folded into this tab, no longer a separate tab).
        if viewModel.activePointIndex == nil, case .loaded(let analyses) = viewModel.routeAnalysesState {
            viewModel.activePointIndex = analyses.analyses.first?.pointIndex
        }
        scrollTarget = "skewt"
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

/// Sizes the cross-section canvas per orientation: portrait keeps a 2:1
/// artifact (`.fit`), landscape fills the screen. Kept as a modifier (not an
/// inline `.aspectRatio(landscape ? nil : 2.0, ...)`) because passing `nil`
/// makes SwiftUI use the view's *intrinsic* ratio — a Canvas has none, which
/// collapsed the chart to a vertical sliver in iPhone landscape. (#9)
private struct CanvasAspectModifier: ViewModifier {
    let landscape: Bool
    func body(content: Content) -> some View {
        if landscape {
            content.frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            content.aspectRatio(2.0, contentMode: .fit)
        }
    }
}
