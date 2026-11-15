package aero.flyfun.weather

import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FlightSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The Advisory tab's flight summary strip (#996); mirrors iOS `FlightSummaryTests`. */
class FlightSummaryTest {

    private fun flight(
        waypoints: List<String> = listOf("EGTF", "LFAT"),
        departure: String = "2026-11-21T10:00:00Z",
        cruiseFt: Int = 6500,
        hours: Double = 85.0 / 60.0,
        rawRoute: String? = null,
    ) = FlightResponse(
        id = "zz-1",
        waypoints = waypoints,
        departureTime = departure,
        cruiseAltitudeFt = cruiseFt,
        flightDurationHours = hours,
        rawRoute = rawRoute,
    )

    @Test
    fun `planned times level and duration`() {
        val lines = FlightSummary.lines(flight(waypoints = listOf("egtf", "LFAT")))
        assertEquals("EGTF 10:00Z → LFAT 11:25Z", lines.main)
        assertEquals("Sat 21 Nov · 1h25 · FL065", lines.detail)
        assertNull(lines.route)
    }

    @Test
    fun `route is raw route else inner waypoints`() {
        assertEquals("MID SFD", FlightSummary.lines(flight(waypoints = listOf("EGTF", "MID", "SFD", "LFAT"))).route)
        assertEquals(
            "MID DCT SFD",
            FlightSummary.lines(flight(waypoints = listOf("EGTF", "MID", "LFAT"), rawRoute = " MID DCT SFD ")).route,
        )
    }

    @Test
    fun `duration label uses exact minutes`() {
        assertEquals("0h45", FlightSummary.durationLabel(0.75))
        assertEquals("2h00", FlightSummary.durationLabel(2.0))
        assertEquals("1h02", FlightSummary.durationLabel(1.0 + 2.0 / 60.0))
    }
}
