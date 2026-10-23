package aero.flyfun.weather.journeys

import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The flight-list journeys, ported from iOS `FlightsUITests` (same names, same steps).
 * Backed by the shared fixtures: `fixture-1` (LFMD → LFML, briefed) and `fixture-2`
 * (EGTF → LFAT), plus the two legs of the `fixture-trip-1` trip.
 */
@RunWith(AndroidJUnit4::class)
class FlightsUITests : FlyfunUITestCase() {

    /**
     * Journey 1: launch in mock mode lands past the login gate on the flight list, and the
     * seeded fixtures render (phone and tablet).
     */
    @Test
    fun testFlightListRendersSeededFlights() {
        launchMockApp()
        revealFlightList()

        // Both fixture flights render (keyed off the card tags, not the route text, so a
        // route-formatter change can't break the assertion).
        waitForTag(flightCard("fixture-1"), "first fixture flight should be listed")
        waitForTag(flightCard("fixture-2"), "second fixture flight should be listed")

        // The primary action is reachable.
        waitForTag(ADD_FLIGHT_BUTTON, "the New Flight button should be present")
    }

    /**
     * Journey 2: add-flight form. Submit is gated until there are two waypoints, then a create
     * round-trips and lands on the new flight's briefing.
     */
    @Test
    fun testAddFlightValidationAndCreate() {
        launchMockApp()
        revealFlightList()

        waitForTag(ADD_FLIGHT_BUTTON, "the New Flight button should be present").performClick()

        val waypoints = waitForTag(WAYPOINTS_FIELD, "add-flight form should appear")
        val submit = waitForTag(SUBMIT_FLIGHT_BUTTON, "add-flight form should have a submit button")

        // One waypoint → submit disabled.
        waypoints.performTextInput("EGLL")
        compose.waitForIdle()
        submit.assertIsNotEnabled()

        // Second waypoint → submit enabled.
        waypoints.performTextInput(" EGKK")
        compose.waitForIdle()
        submit.assertIsEnabled()

        submit.performScrollTo().performClick()

        // Creating the flight opens it, so the app navigates to the new flight's briefing.
        waitForBriefingLoaded()
    }
}
