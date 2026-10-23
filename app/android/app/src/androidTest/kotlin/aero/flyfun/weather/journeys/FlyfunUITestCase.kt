package aero.flyfun.weather.journeys

import android.content.Intent
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.junit4.ComposeTestRule
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import aero.flyfun.weather.MainActivity
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.ui.briefing.briefingTabTestTag
import aero.flyfun.weather.viewmodel.UiTestLaunch
import org.junit.After
import org.junit.Rule

/**
 * Compose UI-test journeys (#895), the Android counterpart of the iOS XCUI journeys
 * (`app/flyfun-weather/flyfun-weatherUITests/`). Launched in mock mode (`FLYFUN_UITEST` +
 * `FLYFUN_MOCK` intent extras, see [UiTestLaunch]) so they skip the login gate and serve the
 * shared fixtures in `app/fixtures/api`: deterministic, offline, no backend or OAuth.
 *
 * Selectors key off test tags, not visible text, so they survive copy changes. Where a screen
 * exists on both platforms the tag is the iOS accessibilityIdentifier (`flightCard-<id>`,
 * `addFlightButton`, `waypointsField`, `submitFlightButton`), so a journey can be ported from
 * its iOS version line by line. Each platform keeps its own journeys: this file is the shared
 * base (launch, timeouts, navigation helpers), as `FlyfunUITestCase.swift` is on iOS.
 *
 * Unlike XCUITest, the tests share one instrumentation process: the app's process-lived state
 * survives from one test to the next. Each launch therefore reseeds it
 * (`AppStateViewModel.startUiTestSession`) instead of relying on a fresh process.
 */
abstract class FlyfunUITestCase {

    /** No activity of its own: [launchMockApp] starts one with the test's intent extras. */
    @get:Rule
    val compose: ComposeTestRule = createEmptyComposeRule()

    private var scenario: ActivityScenario<MainActivity>? = null

    @After
    fun closeApp() {
        scenario?.close()
        scenario = null
    }

    /**
     * Launch the app as a UI test would: past the login gate, fixture-backed. `offline` also sets
     * `FLYFUN_MOCK_OFFLINE`; `extras` adds launch extras (e.g. `FLYFUN_MOCK_LIVE`).
     */
    fun launchMockApp(offline: Boolean = false, extras: Map<String, String> = emptyMap()) {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val intent = Intent(context, MainActivity::class.java).apply {
            putExtra(UiTestLaunch.EXTRA_UITEST, "1")
            putExtra(UiTestLaunch.EXTRA_MOCK, "1")
            if (offline) putExtra(UiTestLaunch.EXTRA_MOCK_OFFLINE, "1")
            extras.forEach { (key, value) -> putExtra(key, value) }
        }
        scenario = ActivityScenario.launch<MainActivity>(intent)
    }

    /**
     * Wait until a node with [tag] exists, failing with [message]. Returns the node.
     *
     * Sized for the slowest machine that runs these (a CI emulator without a GPU is several
     * times slower than a local one): waiting returns the moment the node exists, so a long
     * timeout costs only a test that was going to fail anyway. Same reasoning, and the same
     * 20 s, as `FlyfunUITestCase.uiTimeout` on iOS.
     */
    fun waitForTag(tag: String, message: String, timeoutMillis: Long = UI_TIMEOUT_MS): SemanticsNodeInteraction {
        try {
            compose.waitUntil(timeoutMillis) {
                compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
            }
        } catch (e: Throwable) {
            throw AssertionError("$message (no node tagged '$tag' after ${timeoutMillis / 1000} s)", e)
        }
        return compose.onNodeWithTag(tag)
    }

    /** The flight list is on screen: the phone's single pane, or the left pane on a tablet. */
    fun revealFlightList() {
        waitForTag(FLIGHT_LIST, "the flight list should be shown")
    }

    /**
     * A briefing is open once its Advisory tab is present: the phone's bottom bar and the wide
     * layout's top tab row carry the same tag, so this is the layout-agnostic "briefing loaded"
     * signal, as the Advisory tab button is on iOS.
     */
    fun waitForBriefingLoaded() {
        waitForTag(briefingTabTestTag(BriefingTab.ADVISORY), "the briefing (with its Advisory tab) should be shown")
    }

    companion object {
        const val UI_TIMEOUT_MS = 20_000L

        const val FLIGHT_LIST = "flightList"
        const val ADD_FLIGHT_BUTTON = "addFlightButton"
        const val WAYPOINTS_FIELD = "waypointsField"
        const val SUBMIT_FLIGHT_BUTTON = "submitFlightButton"

        fun flightCard(flightId: String) = "flightCard-$flightId"
    }
}
