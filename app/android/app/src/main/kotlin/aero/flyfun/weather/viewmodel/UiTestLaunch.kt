package aero.flyfun.weather.viewmodel

/**
 * How a UI test launched the app (#895): the Android side of the iOS launch environment
 * (`FLYFUN_UITEST` / `FLYFUN_MOCK` / `FLYFUN_MOCK_OFFLINE` / `FLYFUN_MOCK_LIVE`, see iOS
 * `AppState.isUITesting`), passed as string intent extras with the same names and the same "1".
 * `MainActivity` reads them in debug builds only; a release build ignores them.
 */
data class UiTestLaunch(
    /** Serve the fixtures (`app/fixtures/api`) and skip the login gate. */
    val mock: Boolean = false,
    /** Start in offline mode. Only meaningful together with [mock]. */
    val offline: Boolean = false,
    /** Serve the populated live layer instead of the null one, as iOS `FLYFUN_MOCK_LIVE=1`. */
    val mockLive: Boolean = false,
    /** Start the fixture account with first-run setup not done (#900), as iOS `FLYFUN_MOCK_SETUP=1`. */
    val setupPending: Boolean = false,
) {
    companion object {
        const val EXTRA_UITEST = "FLYFUN_UITEST"
        const val EXTRA_MOCK = "FLYFUN_MOCK"
        const val EXTRA_MOCK_OFFLINE = "FLYFUN_MOCK_OFFLINE"
        const val EXTRA_MOCK_LIVE = "FLYFUN_MOCK_LIVE"
        const val EXTRA_MOCK_SETUP = "FLYFUN_MOCK_SETUP"

        /**
         * The launch a test asked for, from a lookup of the intent's string extras; null when
         * this is not a UI-test launch (no `FLYFUN_UITEST=1`).
         */
        fun from(extra: (String) -> String?): UiTestLaunch? {
            if (extra(EXTRA_UITEST) != "1") return null
            return UiTestLaunch(
                mock = extra(EXTRA_MOCK) == "1",
                offline = extra(EXTRA_MOCK_OFFLINE) == "1",
                mockLive = extra(EXTRA_MOCK_LIVE) == "1",
                setupPending = extra(EXTRA_MOCK_SETUP) == "1",
            )
        }
    }
}
