package aero.flyfun.weather.model

enum class BriefingTab(val title: String) {
    ADVISORY("Advisory"),
    DISCUSSION("Discussion"),
    CROSS_SECTION("Cross-Section"),
    MAP("Map"),
    OBSERVED("Observed");

    companion object {
        /**
         * Ordered tab list for the briefing screen, matching iOS `BriefingTab.tabs`:
         * Observed is appended at the end while the flight is in its live window, so the
         * other tabs keep their positions when it comes and goes.
         */
        fun tabs(showsObserved: Boolean): List<BriefingTab> =
            if (showsObserved) {
                listOf(ADVISORY, DISCUSSION, CROSS_SECTION, MAP, OBSERVED)
            } else {
                listOf(ADVISORY, DISCUSSION, CROSS_SECTION, MAP)
            }
    }
}
