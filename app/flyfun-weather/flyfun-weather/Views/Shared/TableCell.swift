import SwiftUI

extension View {
    /// One cell in a briefing data table — the `Grid`-based tables in the
    /// Advisory tab (`RouteObservationsView`, `RouteSigmetsView`).
    ///
    /// Those grids run at `horizontalSpacing: 0` and the gutter lives *inside*
    /// the cell instead. That is what lets a flagged row (a CONFLICTING
    /// observation, a SEV SIGMET) be shaded per-cell and still read as one
    /// continuous band: `Grid` applies a row background per cell, so with
    /// spacing > 0 the shading tiles into a dashed line of blocks.
    ///
    /// The cell also fills the width the Grid assigns rather than its content
    /// width, for the same reason; `gridColumnAlignment(.leading)` on the Grid
    /// keeps the content itself left-aligned.
    ///
    /// - Parameter highlight: the row's flag colour, or nil for a normal row.
    ///   Each table picks its own semantic (amber for a model conflict, red for
    ///   a severe hazard); the tint strength is shared so the two tables — which
    ///   sit adjacent in the same tab — shade identically.
    func tableCell(highlight: Color? = nil) -> some View {
        self
            .padding(.horizontal, Theme.spacingXS)
            .padding(.vertical, 3)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .background(highlight?.opacity(Theme.tableRowHighlightOpacity) ?? .clear)
    }
}
