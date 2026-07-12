import SwiftUI

/// The static cross-section scene: sky, all enabled data layers, and axes/grid.
///
/// This is the heavy render pass — the natural cloud style alone draws
/// O(n_slots × subBlobsPerSlot) radial-gradient fills per band per segment
/// (~400+ gradient fills on a typical route). It must redraw only when the data,
/// model, layer set, or canvas size changes — NOT on every scrub drag tick
/// (~60 fps), which used to re-run the whole stack and jank older A-series chips
/// (#303).
///
/// The redraw gate is `Equatable` + `.equatable()` at the call site: that pairing
/// forces SwiftUI to skip re-evaluating this view (and so re-running the inner
/// `Canvas` closure) when `==` reports no change. Conformance alone is not enough
/// — without `.equatable()` the reconciler reflects the stored properties, can't
/// compare the non-`Equatable` `VizRouteData`, and treats the view as changed
/// every tick. We key `==` on the view model's monotonic `dataVersion` rather than
/// diffing the deep `VizRouteData` value each tick, plus `renderSize` so a
/// rotation/resize forces a redraw at the new dimensions. The cursor and aircraft
/// are deliberately NOT drawn here; they live in `CrossSectionCursorOverlay`.
struct StaticCrossSectionScene: View, Equatable {
    let data: VizRouteData
    let enabledLayers: [String: Bool]
    /// Identity for `data` (bumped by `CrossSectionViewModel.update`). Comparing
    /// this int + the small layer dict is cheap; diffing `VizRouteData` is not.
    let dataVersion: Int
    /// Active colour theme (#320). Part of `==` so switching themes repaints the
    /// static layers/sky/axes — otherwise the gate would skip the redraw and only
    /// the cursor overlay (which doesn't read the theme) would update.
    let themeId: CrossSectionThemeID
    /// Rendered canvas size, fed from the call site's `GeometryReader`. Included
    /// in `==` so a rotation/resize is a real change and forces a redraw, rather
    /// than relying on the undocumented behaviour of `Canvas` re-running its
    /// closure while `EquatableView` skips `body`.
    let renderSize: CGSize
    /// Advisory highlight geometry (#374), derived by the call site from the
    /// tracked advisory × selected model (never stored). Passed separately from
    /// `data` — whose identity is `dataVersion` — because it changes on its own
    /// triggers (chip tap / visibility toggle) without a data rebuild; being a
    /// small `Equatable` value it participates in the redraw gate directly.
    var highlights: VizAdvisoryHighlights? = nil

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.dataVersion == rhs.dataVersion
            && lhs.themeId == rhs.themeId
            && lhs.renderSize == rhs.renderSize
            && lhs.enabledLayers == rhs.enabledLayers
            && lhs.highlights == rhs.highlights
    }

    var body: some View {
        Canvas { context, size in
            // Attach the derived highlight the way web briefing-main attaches
            // `data.advisoryHighlights` before setData — the layer reads it from
            // the data like every other layer.
            var data = self.data
            data.advisoryHighlights = highlights
            CrossSectionRenderer(data: data, enabledLayers: enabledLayers, themeId: themeId)
                .renderStatic(context: &context, size: size)
        }
    }
}

/// The dynamic cross-section overlay: the scrub cursor rule + the live aircraft
/// marker. Drawn in its own `Canvas` so a scrub tick or GPS update redraws only
/// these O(1) primitives, leaving the cached static scene untouched (#303).
/// Non-interactive — the scrub gesture lives on the static scene beneath it.
struct CrossSectionCursorOverlay: View {
    let data: VizRouteData
    let cursorDistanceNm: Double?
    let aircraft: CrossSectionRenderer.AircraftPosition?

    var body: some View {
        Canvas { context, size in
            CrossSectionRenderer(data: data, enabledLayers: [:],
                                 selectedDistanceNm: cursorDistanceNm,
                                 aircraftPosition: aircraft)
                .renderCursor(context: &context, size: size)
        }
        .allowsHitTesting(false)
    }
}
