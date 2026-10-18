package aero.flyfun.weather.model

enum class BriefingTab(val title: String) {
    ADVISORY("Advisory"),
    DISCUSSION("Discussion"),
    CROSS_SECTION("Cross-Section"),
    MAP("Map"),
    OBSERVED("Observed");

    companion object {
        /**
         * Ordered tab list for the briefing screen.
         * Observed sits next to Advisory while the flight is in its live window.
         */
        fun tabs(showsObserved: Boolean): List<BriefingTab> =
            if (showsObserved) {
                listOf(ADVISORY, OBSERVED, DISCUSSION, CROSS_SECTION, MAP)
            } else {
                listOf(ADVISORY, DISCUSSION, CROSS_SECTION, MAP)
            }
    }
}
