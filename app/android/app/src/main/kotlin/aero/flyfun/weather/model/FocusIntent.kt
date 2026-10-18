package aero.flyfun.weather.model

/**
 * Bundled cross-tab navigation intent so tab switch + preset/point/scroll-anchor
 * selection happen in a single state transition.
 */
data class FocusIntent(
    val tab: BriefingTab,
    val preset: String? = null,
    val distanceNm: Double? = null,
    val waypointIcao: String? = null,
    val scrollAnchorId: String? = null,
    val highlightRangeNm: ClosedFloatingPointRange<Double>? = null,
)
