import SwiftUI

/// The three shapes the cross-section tab takes (#605), decided from BOTH size
/// classes rather than from the device: iPad Split View at a third of the screen
/// is `.phonePortrait`, and a Plus/Max iPhone turned sideways is
/// `.phoneLandscape`. Before #605 only the vertical class was read, so iPad ran
/// the iPhone-portrait layout verbatim.
enum CrossSectionLayoutMode: Equatable {
    /// Narrow chart with room above and below it: compact chips under the chart.
    case phonePortrait
    /// Wide chart and room to spare — iPad in either orientation: the web's bar.
    case regular
    /// The tightest view: one chip row over a full-bleed chart.
    case phoneLandscape

    init(horizontal: UserInterfaceSizeClass?, vertical: UserInterfaceSizeClass?) {
        if vertical == .compact {
            self = .phoneLandscape
        } else if horizontal == .regular {
            self = .regular
        } else {
            self = .phonePortrait
        }
    }
}
