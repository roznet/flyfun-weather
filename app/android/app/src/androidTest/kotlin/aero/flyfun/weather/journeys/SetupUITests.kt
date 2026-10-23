package aero.flyfun.weather.journeys

import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import aero.flyfun.weather.viewmodel.UiTestLaunch
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * First-run setup (#900), ported from iOS `SetupUITests` (same names, same steps).
 * `FLYFUN_MOCK_SETUP=1` starts the fixture account with setup not done
 * (`FixtureSetupRepository`), so the wizard comes up before the flight list; no other journey sets
 * it, so none sees the wizard.
 */
@RunWith(AndroidJUnit4::class)
class SetupUITests : FlyfunUITestCase() {

    private fun launchWithSetupPending() {
        launchMockApp(extras = mapOf(UiTestLaunch.EXTRA_MOCK_SETUP to "1"))
    }

    /**
     * Welcome → IFR FIKI → aircraft (FIKI switch left alone) → done: the aircraft comes back FIKI,
     * and Get Started lands on the flight list.
     */
    @Test
    fun testFinishingSetupSavesAFikiAircraft() {
        launchWithSetupPending()
        waitForTag(NEXT, "the setup wizard should open on first sign-in").performClick()

        waitForTag("setupProfileCard-ifr_fiki", "the profile step should list IFR FIKI")
            .performScrollTo()
            .performClick()
        waitForTag(NEXT, "the wizard should offer Next").performClick()

        waitForTag("setupIcaoTypeField", "the aircraft step should ask for the ICAO type")
            .performTextInput("SR22")
        waitForTag(NEXT, "the wizard should offer Next").performClick()

        waitForTag("setupSummary", "the done step should show what was saved")
            .assertTextContains("Default profile: IFR FIKI", substring = true)
            .assertTextContains("FIKI equipped", substring = true)

        waitForTag("setupFinishButton", "the done step should offer Get Started").performClick()
        revealFlightList()
        waitForTag(flightCard("fixture-1"), "Get Started should land on the flight list")
        assertTrue("the wizard should be gone", compose.onAllNodesWithTag(NEXT).fetchSemanticsNodes().isEmpty())
    }

    /** Skip on the first screen closes the wizard onto the flight list. */
    @Test
    fun testSkippingSetupLandsOnTheFlightList() {
        launchWithSetupPending()
        waitForTag("setupSkipButton", "the setup wizard should offer Skip").performClick()

        revealFlightList()
        waitForTag(flightCard("fixture-1"), "Skip should land on the flight list")
        assertTrue(
            "the wizard should be gone",
            compose.onAllNodesWithTag("setupSkipButton").fetchSemanticsNodes().isEmpty(),
        )
    }
}

private const val NEXT = "setupNextButton"
