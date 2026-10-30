package aero.flyfun.weather.journeys

import android.graphics.Bitmap
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.ui.briefing.briefingTabTestTag
import java.io.File
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Screenshots of any flight, for docs and talks: the **real** debug app against the running
 * `/devserver --https` (the emulator's default server in a debug build), signed in as the dev
 * user. Driven by `scripts/capture/android.py`, which mints the token, checks the flight is in
 * the dev DB, passes them as instrumentation arguments and pulls the PNGs from the app's
 * `files/captures/`; without those arguments it is skipped (CI, `connectedDebugAndroidTest`).
 *
 * The twin of iOS `FlightCaptureUITests`: the same screen names, and a missing screen is not
 * fatal. The journey goes on to the next one and fails at the end with the list, so a flight
 * without a sounding still yields the rest.
 */
@RunWith(AndroidJUnit4::class)
class FlightCaptureJourney : FlyfunUITestCase() {

    private val args = InstrumentationRegistry.getArguments()
    private val failures = mutableListOf<String>()

    @Test
    fun captureFlight() {
        val flight = args.getString("flyfunCaptureFlight")
        val token = args.getString("flyfunCaptureToken")
        assumeTrue("captures run only from scripts/capture/android.py", flight != null && token != null)
        val wanted = args.getString("flyfunCaptureScreens").orEmpty().split(",").filter { it.isNotBlank() }.toSet()
        fun want(screen: String) = wanted.isEmpty() || screen in wanted
        val dir = File(InstrumentationRegistry.getInstrumentation().targetContext.filesDir, "captures")
        dir.deleteRecursively()
        dir.mkdirs()

        launchLiveApp("flyfunweather://auth?token=$token")
        revealFlightList()
        if (want("flights")) capture(dir, "flights")

        openBriefing(flight!!)
        if (want("briefing")) capture(dir, "briefing")

        if (want("advisories")) step("advisories") {
            compose.onAllNodesWithText("Advisories").onFirst().performClick()
            capture(dir, "advisories")
        }

        // Observed is a tab only on flight day; its absence is a skip, not a failure.
        val observedTab = briefingTabTestTag(BriefingTab.OBSERVED)
        if (want("observed") && compose.onAllNodesWithTag(observedTab).fetchSemanticsNodes().isNotEmpty()) {
            step("observed") {
                compose.onNodeWithTag(observedTab).performClick()
                capture(dir, "observed")
            }
        }

        if (want("discussion")) step("discussion") {
            compose.onNodeWithTag(briefingTabTestTag(BriefingTab.DISCUSSION)).performClick()
            capture(dir, "discussion")
        }

        if (want("cross-section") || want("skewt")) {
            step("cross-section") {
                compose.onNodeWithTag(briefingTabTestTag(BriefingTab.CROSS_SECTION)).performClick()
                if (want("cross-section")) capture(dir, "cross-section")
            }
            // The Skew-T sits below the chart on the same tab, under its "Sounding" stepper; the
            // diagram itself is composed once its sounding has loaded. Scroll to the stepper,
            // wait for the diagram, then scroll until all of it is in view.
            if (want("skewt")) step("skewt") {
                compose.onAllNodesWithText("Sounding").onFirst().performScrollTo()
                waitForTag(SKEWT_CANVAS, "the Skew-T should render").performScrollTo()
                capture(dir, "skewt")
            }
        }

        if (want("map")) step("map") {
            compose.onNodeWithTag(briefingTabTestTag(BriefingTab.MAP)).performClick()
            capture(dir, "map", settleMillis = MAP_SETTLE_MS)
        }

        if (failures.isNotEmpty()) throw AssertionError("screens not captured: ${failures.joinToString("; ")}")
    }

    /**
     * Open the flight through its universal link (the route a tapped `briefing.html?flight=`
     * link takes), falling back to scrolling the list to its card: the dev user's list is long.
     */
    private fun openBriefing(flight: String) {
        openLink("https://weather.flyfun.aero/briefing.html?flight=$flight")
        val advisoryTab = briefingTabTestTag(BriefingTab.ADVISORY)
        val opened = runCatching { waitForTag(advisoryTab, "the briefing should open from its link") }.isSuccess
        if (opened) return
        compose.onNodeWithTag(FLIGHT_LIST).performScrollToNode(hasTestTag(flightCard(flight)))
        compose.onNodeWithTag(flightCard(flight)).performClick()
        waitForBriefingLoaded()
    }

    private fun step(screen: String, block: () -> Unit) {
        runCatching(block).onFailure { failures += "$screen: ${it.message?.lineSequence()?.firstOrNull()}" }
    }

    /** Live data: charts draw after their fetch, map tiles after that. */
    private fun capture(dir: File, screen: String, settleMillis: Long = RENDER_SETTLE_MS) {
        compose.waitForIdle()
        Thread.sleep(settleMillis)
        val shot: Bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
            ?: throw AssertionError("no screenshot for $screen")
        File(dir, "$screen.png").outputStream().use { shot.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    companion object {
        const val RENDER_SETTLE_MS = 3_000L
        const val MAP_SETTLE_MS = 6_000L
        const val SKEWT_CANVAS = "skewTCanvas"
    }
}
