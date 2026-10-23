package aero.flyfun.weather.rules

import aero.flyfun.weather.model.FlightEditRules
import aero.flyfun.weather.model.FlightEditRules.Impact
import aero.flyfun.weather.model.FlightResponse
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The structural-edit rules (#552, #885), mirroring the iOS `AddFlightViewModel` tests:
 * UTC-day comparison, endpoint-only route identity, and the Field-15 baseline.
 */
class FlightEditRulesTest {

    private val flight = FlightResponse(
        id = "ZZ-EDIT-1",
        waypoints = listOf("LFPN", "LFRK"),
        rawRoute = "LFPN N0120VFR DCT LFRK",
        departureTime = "2099-07-10T22:30:00Z",
        cruiseAltitudeFt = 6500,
        flightCeilingFt = 10000,
        flightDurationHours = 1.5,
        profileId = 3,
    )

    private fun values(
        routeInput: String = FlightEditRules.baselineRouteInput(flight),
        waypoints: List<String> = flight.waypoints,
        departure: Instant = Instant.parse(flight.departureTime),
        cruiseAltitudeFt: Int = flight.cruiseAltitudeFt,
        flightCeilingFt: Int = flight.flightCeilingFt,
        durationChanged: Boolean = false,
        profileId: Int? = flight.profileId,
    ) = FlightEditRules.EditedValues(
        routeInput = routeInput,
        waypoints = waypoints,
        departure = departure,
        cruiseAltitudeFt = cruiseAltitudeFt,
        flightCeilingFt = flightCeilingFt,
        durationChanged = durationChanged,
        profileId = profileId,
    )

    @Test
    fun baselineIsTheFieldFifteenTextWhenThereIsOne() {
        assertEquals("LFPN N0120VFR DCT LFRK", FlightEditRules.baselineRouteInput(flight))
        assertEquals("LFPN LFRK", FlightEditRules.baselineRouteInput(flight.copy(rawRoute = null)))
        assertEquals("LFPN LFRK", FlightEditRules.baselineRouteInput(flight.copy(rawRoute = "  ")))
    }

    @Test
    fun untouchedFieldFifteenRouteIsNotAChange() {
        // Its naive token split is not the waypoint list, which is why text is compared.
        assertFalse(FlightEditRules.routeInputChanged("lfpn  n0120vfr dct lfrk", flight))
        assertEquals(Impact.NONE, FlightEditRules.impact(values(), flight))
    }

    @Test
    fun midnightInParisIsTheDayBeforeInUtc() {
        // 00:30 Europe/Paris (CEST) on the 11th is 22:30 UTC on the 10th: same UTC day as the
        // flight, so not structural, though the local calendar day differs.
        val parisLocal = LocalDateTime.of(2099, 7, 11, 0, 30).atZone(ZoneId.of("Europe/Paris")).toInstant()
        assertEquals(Instant.parse("2099-07-10T22:30:00Z"), parisLocal)
        assertFalse(FlightEditRules.departureDayChanged(parisLocal, Instant.parse(flight.departureTime)))

        // 02:30 Paris on the 11th is 00:30 UTC on the 11th: a different flight.
        val later = LocalDateTime.of(2099, 7, 11, 2, 30).atZone(ZoneId.of("Europe/Paris")).toInstant()
        assertEquals(Impact.STRUCTURAL, FlightEditRules.impact(values(departure = later), flight))
    }

    @Test
    fun nudgingTheTimeAcrossUtcMidnightIsStructural() {
        val acrossMidnight = Instant.parse("2099-07-11T00:15:00Z")
        assertEquals(Impact.STRUCTURAL, FlightEditRules.impact(values(departure = acrossMidnight), flight))
        val sameDay = Instant.parse("2099-07-10T23:45:00Z")
        assertEquals(Impact.REBRIEF, FlightEditRules.impact(values(departure = sameDay), flight))
    }

    @Test
    fun onlyTheEndpointsAreTheRouteIdentity() {
        val viaMid = values(routeInput = "LFPN LFOE LFRK", waypoints = listOf("LFPN", "LFOE", "LFRK"))
        assertEquals(Impact.REBRIEF, FlightEditRules.impact(viaMid, flight))

        val newDest = values(routeInput = "LFPN LFRG", waypoints = listOf("LFPN", "LFRG"))
        assertEquals(Impact.STRUCTURAL, FlightEditRules.impact(newDest, flight))

        val newOrigin = values(routeInput = "LFPT LFRK", waypoints = listOf("LFPT", "LFRK"))
        assertTrue(FlightEditRules.isStructural(newOrigin, flight))
    }

    @Test
    fun impactFollowsTheServerInvalidationRules() {
        assertEquals(Impact.REBRIEF, FlightEditRules.impact(values(durationChanged = true), flight))
        assertEquals(Impact.ADVISORIES, FlightEditRules.impact(values(cruiseAltitudeFt = 8500), flight))
        assertEquals(Impact.ADVISORIES, FlightEditRules.impact(values(flightCeilingFt = 12000), flight))
        assertEquals(Impact.ADVISORIES, FlightEditRules.impact(values(profileId = 4), flight))
        // Time beats altitude, as on the server (refetch_needed before advisories_only).
        val both = values(departure = Instant.parse("2099-07-10T20:00:00Z"), cruiseAltitudeFt = 8500)
        assertEquals(Impact.REBRIEF, FlightEditRules.impact(both, flight))
    }

    @Test
    fun alternateIsBoundToTheDeparturesUtcDay() {
        val aligned = FlightEditRules.alignUtcDay(
            Instant.parse("2099-07-01T14:00:00Z"),
            Instant.parse("2099-07-10T22:30:00Z"),
        )
        assertEquals(Instant.parse("2099-07-10T14:00:00Z"), aligned)
    }

    @Test
    fun moveCopyNamesNoNumberItHasNotLoaded() {
        assertEquals(
            "Replaces this flight. Its existing briefings are discarded. Share link kept.",
            FlightEditRules.moveConsequence(packCount = null, isLive = false),
        )
        assertEquals(
            "Replaces this flight. Its 3 briefings are discarded. Share link kept.",
            FlightEditRules.moveConsequence(packCount = 3, isLive = false),
        )
        assertTrue(FlightEditRules.moveConsequence(packCount = 1, isLive = true).endsWith("tracking for today are dropped."))
    }
}
