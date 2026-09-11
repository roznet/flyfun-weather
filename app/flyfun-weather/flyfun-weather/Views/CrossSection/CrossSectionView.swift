import SwiftUI
import TipKit

/// SwiftUI Canvas wrapper for the cross-section visualization (§4.7 interaction).
///
/// Three layouts, chosen from both size classes (#605, `CrossSectionLayoutMode`):
///  - **iPhone portrait** — a narrow chart with room around it: compact family
///    chips wrap directly under the chart; press and hold a chip for its methods.
///  - **iPad** — a wide chart with room to spare: the web's layer bar (`Icing ·
///    SFIP-NWP` chips over one swapping detail row) sits above the chart, in a
///    band that used to be empty, and the chart is capped to the viewport so
///    bar, chart and axis are all on screen together.
///  - **iPhone landscape** — the tightest view, and fully immersive: the tab
///    and navigation bars are gone (rotate back to navigate), the readout and one
///    chip row sit over the chart; double-tap the chart to hide those too.
/// In all three, flipping a layer changes the chart in place — nothing covers it
/// — because comparing two states is the point. The options sheet keeps only
/// what you set once (emulation, theme, the observed corridor).
///
/// Touch model: tap places the cursor; a drag that starts sideways scrubs; press
/// and hold, then drag, scrubs in any direction; a drag that starts vertically
/// scrolls the page (`ScrubPanGesture`). The cursor drives the readout strip and
/// the shared active point; "Sounding ›" scrolls to the Skew-T below.
struct CrossSectionView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var csVM = CrossSectionViewModel()
    @State private var canvasSize: CGSize = .zero
    @State private var scrubDistanceNm: Double?
    @State private var scrubAltitudeFt: Double?
    @State private var showingOptions = false
    @State private var chromeHidden = false
    /// The family whose detail row is open on the layer bar, if any.
    @State private var openFamily: LayerFamily?
    /// The scroll viewport, and the height of everything stacked above the chart:
    /// the iPad chart is capped to what is left, so its axis never lands below
    /// the fold under the bar (#605).
    @State private var viewportSize: CGSize = .zero
    @State private var aboveChartHeight: CGFloat = 0
    /// Route-graph metric selection, lifted here so the readout strip and the
    /// graph share one cursor + one metric choice (§4.7 unified cursor).
    /// Persisted (#9) so the chosen metrics survive relaunch, like the web.
    @AppStorage("crossSectionGraphLeftMetric") private var graphLeftMetricId = "headwind"
    @AppStorage("crossSectionGraphRightMetric") private var graphRightMetricId = "cloud-cover"
    /// Scroll-to target inside the tab (#310): the "Sounding ›" deep-link and a
    /// `FocusIntent.target == .skewT` set this to "skewt"; the portrait scroll
    /// view scrolls to the embedded Skew-T and resets it to nil.
    @State private var scrollTarget: String?
    /// Native scroll position for the scrolling layouts (replaces `ScrollViewReader`).
    @State private var scrollPosition = ScrollPosition(idType: String.self)
    @Environment(\.horizontalSizeClass) private var hSizeClass
    @Environment(\.verticalSizeClass) private var vSizeClass

    // Contextual tips (#312), gated on this tab being visible.
    private let layersTip = CrossSectionLayersTip()
    private let scrubTip = CrossSectionScrubTip()
    /// One-shot guard so the scrub tip is retired exactly once — and only where
    /// its `TipView` is actually rendered. Landscape focus has no scrub
    /// `TipView` (distraction-free by design), so a landscape drag must not
    /// consume the tip before the user ever sees it.
    @State private var scrubTipInvalidated = false

    private var mode: CrossSectionLayoutMode {
        CrossSectionLayoutMode(horizontal: hSizeClass, vertical: vSizeClass)
    }

    /// iPhone landscape → immersive full-bleed focus mode (§4.7): cross-section
    /// is a wide artifact, so landscape gives it the right aspect ratio.
    private var isLandscapeFocus: Bool { mode == .phoneLandscape }

    var body: some View {
        Group {
            if isLandscapeFocus {
                landscapeFocus
            } else {
                scrolling
            }
        }
        .onChange(of: viewModel.selectedModel) { updateVizData() }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) { updateVizData() }
        .onChange(of: viewModel.elevationState.isLoaded) { updateVizData() }
        // The observed payload rides on the snapshot, and a gated D-0 refresh
        // replaces it in place without minting a new pack — so the chart has to
        // rebuild on a snapshot change, not only on a pack change (#574).
        .onChange(of: viewModel.snapshotState.isLoaded) { updateVizData() }
        .onChange(of: observedComputedAt) { updateVizData() }
        .onChange(of: viewModel.focusIntent) { applyFocusIntent() }
        // What FlyFun, the compact chips and the advisory chip resolve through:
        // the methods this briefing actually graded with (#605).
        .onChange(of: manifestGradedMethods, initial: true) { _, methods in
            csVM.setGradedMethods(methods)
        }
        // A family can drop out of the bar (Observed, when the loaded pack has
        // no observed payload); close its row rather than leave it orphaned
        // under a bar with no chip for it.
        .onChange(of: LayerFamily.visible(in: csVM)) { _, visible in
            if let family = openFamily, !visible.contains(family) { openFamily = nil }
        }
        .task { updateVizData(); applyFocusIntent() }
        .sheet(isPresented: $showingOptions) {
            CrossSectionConfigSheet(
                csVM: csVM,
                showsLayerPills: mode != .regular,
                snapshot: snapshot
            )
        }
        // iPhone landscape is the chart and nothing else (#605): the floating
        // tab bar sat over the terrain and the distance axis, and landscape
        // content is not inset below the floating navigation bar, so the
        // readout — "Sounding ›" included — sat under it, unreachable. Both bars
        // go; rotating back to portrait is how you navigate.
        .toolbar(isLandscapeFocus ? .hidden : .automatic, for: .tabBar)
        .toolbar(isLandscapeFocus ? .hidden : .automatic, for: .navigationBar)
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

    private func retireLayersTip() {
        layersTip.invalidate(reason: .actionPerformed)
    }

    // MARK: Scrolling layouts (iPhone portrait, iPad)

    private var scrolling: some View {
        ScrollView {
            VStack(spacing: 0) {
                VStack(spacing: 0) {
                    chromeBar
                    if mode == .regular { regularLayerBand }
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
                }
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { aboveChartHeight = $0 }
                crossSectionCanvas
                if mode == .phonePortrait { phoneLayerBand }
                RouteGraphView(viewModel: viewModel, vizData: csVM.vizData, scrubDistanceNm: scrubDistanceNm,
                               leftMetricId: $graphLeftMetricId, rightMetricId: $graphRightMetricId)
                skewTSection
            }
            .scrollTargetLayout()
        }
        .scrollPosition($scrollPosition)
        .onGeometryChange(for: CGSize.self) { $0.size } action: { viewportSize = $0 }
        .background(Theme.bg)
        .onChange(of: scrollTarget) { _, target in
            guard let target else { return }
            withAnimation(.easeInOut(duration: 0.3)) { scrollPosition.scrollTo(id: target, anchor: .top) }
            scrollTarget = nil
        }
        .onAppear {
            // The landscape-immersive layout has no scroll view, so a
            // "Sounding ›" tap there sets `scrollTarget` with nothing to consume
            // it. Returning to portrait re-mounts this scroll view — honor the
            // pending target here (onChange won't fire: unchanged).
            if let target = scrollTarget {
                scrollPosition.scrollTo(id: target, anchor: .top)
                scrollTarget = nil
            }
        }
    }

    /// iPad: the web's bar, in the band above the chart that used to be empty.
    /// The slot under it holds either the open family's detail row — one line on
    /// a wide screen — or the Focus caption, so opening a family replaces a line
    /// rather than adding one, and the chart does not move.
    private var regularLayerBand: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            TipView(layersTip)
            LayerBarView(
                csVM: csVM, style: .full, openFamily: $openFamily,
                highlightAvailable: derivedHighlights != nil, onInteract: retireLayersTip)
            if let family = openFamily {
                FamilyDetailRow(csVM: csVM, family: family) { closeFamily() }
            } else {
                focusCaption
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// iPhone portrait: compact chips directly under the chart — one tap flips a
    /// family with the method chosen for you, and the result is right above the
    /// finger. Press and hold opens that family's detail row below the chips,
    /// which pushes the route graph down, never the chart.
    private var phoneLayerBand: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            TipView(layersTip)
            LayerBarView(
                csVM: csVM, style: .compact, openFamily: $openFamily,
                highlightAvailable: derivedHighlights != nil, onInteract: retireLayersTip)
            if let family = openFamily {
                FamilyDetailRow(csVM: csVM, family: family) { closeFamily() }
            } else {
                focusCaption
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The Focus lens's caption while one is active; otherwise how the bar works.
    @ViewBuilder private var focusCaption: some View {
        if let caption = csVM.activeAdvisoryPreset.flatMap({ CrossSectionPresets.advisory[$0]?.caption }) {
            Label(caption, systemImage: "scope")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .lineLimit(2)
        } else {
            Text(mode == .regular
                 ? "Tap a family to choose its methods."
                 : "Tap to show or hide · press and hold for methods.")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        }
    }

    private func closeFamily() {
        withAnimation(.snappy(duration: 0.2)) { openFamily = nil }
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

    /// The readout and one row of compact chips over a full-bleed chart. The
    /// options button ends the chip row, so nothing is pinned into a corner the
    /// readout's "Sounding ›" already uses (the two used to draw on top of each
    /// other, #605). Double-tap the chart to hide every control.
    private var landscapeFocus: some View {
        VStack(spacing: 0) {
            if !chromeHidden {
                CrossSectionReadoutView(
                    vizData: csVM.vizData ?? Self.emptyViz,
                    scrubDistanceNm: scrubDistanceNm,
                    scrubAltitudeFt: scrubAltitudeFt,
                    onSounding: goToSounding,
                    routeGraphMetricIds: [graphLeftMetricId, graphRightMetricId]
                )
                HStack(spacing: Theme.spacingS) {
                    LayerBarView(
                        csVM: csVM, style: .compact, wraps: false, openFamily: .constant(nil),
                        allowsDetail: false, highlightAvailable: derivedHighlights != nil,
                        onInteract: retireLayersTip)
                    optionsButton
                }
                .padding(.horizontal, Theme.cardPadding)
                .padding(.vertical, 6)
                .background(Theme.surface)
            }
            crossSectionCanvas
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Theme.bg)
    }

    // MARK: Chrome bar (scrolling layouts)

    /// Model, Focus and — where there is room — Emulate, then the options button.
    /// Model switching is the most frequent action of all, so it stays here.
    private var chromeBar: some View {
        HStack(spacing: Theme.spacingS) {
            ModelSelectorView(selectedModel: Binding(
                get: { viewModel.selectedModel },
                set: { viewModel.selectModel($0) }  // sticky pick (#8/#9)
            ), models: viewModel.availableModels)
            FocusMenu(csVM: csVM)
            if mode == .regular { EmulateMenu(csVM: csVM) }
            Spacer(minLength: 0)
            optionsButton
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
    }

    private var optionsButton: some View {
        Button {
            showingOptions = true
            retireLayersTip()
        } label: {
            Image(systemName: "slider.horizontal.3")
                .font(.caption.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(Theme.primary.opacity(0.12), in: Capsule())
                .foregroundStyle(Theme.primary)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Chart options")
        .accessibilityIdentifier("crossSectionOptions")
    }

    // MARK: Canvas

    /// iPad: the chart fills the width but never pushes its own axis below the
    /// fold — capped to the viewport left under the bar and the readout. nil
    /// elsewhere (phone portrait keeps the 2:1 artifact; landscape fills).
    private var regularChartHeight: CGFloat? {
        guard mode == .regular, viewportSize.width > 0 else { return nil }
        let natural = viewportSize.width / 2
        let room = viewportSize.height - aboveChartHeight - Theme.spacingM
        return max(300, min(natural, room))
    }

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
                // Portrait constrains to a 2:1 artifact (300pt floor); landscape
                // fills what is left — no floor, or the axis runs off the bottom
                // of a 402pt screen; iPad caps the height to the viewport.
                // (Passing `nil` to aspectRatio means "use intrinsic ratio" — a
                // Canvas has none, which collapsed the chart to a sliver. #9)
                .modifier(CanvasAspectModifier(landscape: isLandscapeFocus, fixedHeight: regularChartHeight))
                .overlay {
                    CrossSectionCursorOverlay(data: vizData, cursorDistanceNm: cursor, aircraft: aircraft)
                }
                .onGeometryChange(for: CGSize.self) { $0.size } action: { canvasSize = $0 }
                .modifier(ScrubGestures(
                    scrollsWithPage: !isLandscapeFocus,
                    onScrub: updateScrub(at:),
                    onDoubleTap: { withAnimation { chromeHidden.toggle() } }))
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

    // MARK: Scrub

    private func updateScrub(at location: CGPoint) {
        guard let vizData = csVM.vizData, canvasSize.width > 0, !vizData.points.isEmpty else { return }
        // First real scrub retires the "tap any point" tip — but only where the
        // tip is shown. The drag fires this repeatedly, so short-circuit after
        // the first to avoid hammering TipKit.
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

    // MARK: Focus intents (advisory chip, deep links)

    /// Consume a pending deep-link intent targeting the cross-section (§4.6
    /// "Show on cross-section ›"): apply the advisory's lens, light up its
    /// highlight and move the scrub cursor to the focus point, then clear the
    /// intent.
    private func applyFocusIntent() {
        guard let intent = viewModel.focusIntent,
              intent.target == .crossSection || intent.target == .skewT else { return }
        // Same-advisory re-tap toggles the highlight OFF while the lens stays
        // (#374): capture "already on" BEFORE the lens application below clears
        // the highlight, then skip re-activation.
        let highlightAlreadyOn = intent.advisoryId != nil
            && csVM.activeHighlightAdvisoryId == intent.advisoryId
        // The advisory being lit up, if any. Old packs / non-emitting advisories
        // carry no highlight geometry, so this stays nil and the action behaves
        // exactly as before highlights existed.
        var highlighting: RouteAdvisoryResult?
        if let advisoryId = intent.advisoryId, !highlightAlreadyOn,
           case .loaded(let manifest) = viewModel.advisoriesState,
           let advisory = manifest.advisories.first(where: { $0.advisoryId == advisoryId }),
           advisory.perModel.contains(where: { $0.highlights != nil }) {
            highlighting = advisory
        }
        // An advisory lens configures the whole view; a single layerId just
        // force-enables one layer. Apply the lens first so a layerId can refine it.
        if let presetId = intent.advisoryPresetId, let lens = lens(for: intent, presetId: presetId) {
            // Resolve through the methods the ADVISORY was graded under — its
            // representative model's `primary_method_id` — while lighting it up,
            // so the chart paints the evidence the grade actually used; turning
            // it off returns to the briefing's graded methods. Mirrors web
            // `handleAdvisoryChip`.
            let methods = highlighting.map {
                CrossSectionPresets.advisoryMethodOverrides(
                    $0, model: $0.resolvedRepresentativeModel, methods: csVM.gradedMethods)
            } ?? csVM.gradedMethods
            csVM.applyAdvisoryPreset(lens, methods: methods)
        }
        if let layerId = intent.layerId { csVM.enableLayer(layerId) }
        var peakDistNm: Double?
        if let advisory = highlighting {
            // Switch to the advisory's representative model so the highlight
            // reflects the aggregate verdict. Assigned directly (not via
            // `selectModel`) — a programmatic switch must not overwrite the
            // user's sticky model preference.
            if let rep = CrossSectionViewModel.representativeModel(for: advisory),
               viewModel.availableModels.contains(rep) {
                viewModel.selectedModel = rep
            }
            csVM.setHighlightAdvisory(advisory.advisoryId)  // also force-shows the highlight
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

    /// The lens an intent names — with the advisory's own extras (FIKI's warm-nose
    /// isotherms) when it came from that advisory's chip.
    private func lens(for intent: FocusIntent, presetId: String) -> AdvisoryPreset? {
        if let advisoryId = intent.advisoryId,
           let lens = CrossSectionPresets.preset(forAdvisory: advisoryId), lens.id == presetId {
            return lens
        }
        return CrossSectionPresets.advisory[presetId]
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

    /// The methods this briefing graded with, re-read whenever the advisories
    /// manifest changes (load, recalc). Engine defaults until it loads.
    private var manifestGradedMethods: [LayerGroup: String] {
        guard case .loaded(let manifest) = viewModel.advisoriesState else {
            return CrossSectionPresets.engineMethodDefaults
        }
        return CrossSectionPresets.gradedMethods(from: manifest)
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

    /// The loaded snapshot, or nil while it is still in flight.
    private var snapshot: SnapshotResponse? {
        if case .loaded(let s) = viewModel.snapshotState { return s }
        return nil
    }

    /// Identity for the observed payload, so a re-sample that leaves the pack
    /// timestamp alone still triggers a rebuild. `computedAt` is the payload's
    /// *assembly* time — used here only as a change token, never rendered as an
    /// observation age (each source carries its own).
    private var observedComputedAt: String? {
        snapshot?.observedConditions?.computedAt
    }

    private func updateVizData() {
        switch viewModel.routeAnalysesState {
        case .idle, .loading, .error:
            break
        case .loaded(let analyses):
            var elevation: ElevationResponse? = nil
            if case .loaded(let elev) = viewModel.elevationState { elevation = elev }
            csVM.update(
                routeAnalyses: analyses, elevation: elevation,
                model: viewModel.selectedModel,
                observed: snapshot?.observedConditions
            )
        }
    }
}

/// Sizes the cross-section canvas per layout: phone portrait keeps a 2:1
/// artifact (`.fit`), landscape fills the screen, and iPad takes the capped
/// height it is given. Kept as a modifier (not an inline
/// `.aspectRatio(landscape ? nil : 2.0, ...)`) because passing `nil` makes
/// SwiftUI use the view's *intrinsic* ratio — a Canvas has none, which
/// collapsed the chart to a vertical sliver in iPhone landscape. (#9)
private struct CanvasAspectModifier: ViewModifier {
    let landscape: Bool
    var fixedHeight: CGFloat?

    func body(content: Content) -> some View {
        if landscape {
            content.frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let fixedHeight {
            content.frame(maxWidth: .infinity).frame(height: fixedHeight)
        } else {
            content.frame(minHeight: 300).aspectRatio(2.0, contentMode: .fit)
        }
    }
}

/// The canvas's touch handling (#605). In a scroll view: tap to place the
/// cursor; a sideways drag, or a hold then any drag, scrubs; a vertical drag
/// scrolls the page (`ScrubPanGesture`). In landscape focus there is no page to
/// scroll, so any drag scrubs at once and a double-tap hides the chrome.
private struct ScrubGestures: ViewModifier {
    let scrollsWithPage: Bool
    let onScrub: (CGPoint) -> Void
    let onDoubleTap: () -> Void

    func body(content: Content) -> some View {
        if scrollsWithPage {
            content
                .gesture(ScrubPanGesture(onChanged: onScrub))
                .onTapGesture(coordinateSpace: .local) { onScrub($0) }
        } else {
            content
                .gesture(DragGesture(minimumDistance: 0).onChanged { onScrub($0.location) })
                .simultaneousGesture(TapGesture(count: 2).onEnded(onDoubleTap))
        }
    }
}
