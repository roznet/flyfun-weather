package aero.flyfun.weather

import aero.flyfun.weather.model.FlightOrder
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FlightSection
import aero.flyfun.weather.model.IsoTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FlightResponseGroupingTest {

    private val nowMs: Long = IsoTime.parseEpochMs("2026-07-15T12:00:00Z")!!

    private fun isoOffsetDays(days: Double): String =
        IsoTime.formatUtc(nowMs + (days * 86_400_000.0).toLong())

    private fun makeFlight(
        id: String = "f1",
        departureTime: String = "2026-07-16T09:00:00Z",
        flightDurationHours: Double = 2.0,
        section: String? = null,
    ): FlightResponse = FlightResponse(
        id = id,
        userId = "u1",
        routeName = "EGTK_LFPB",
        waypoints = listOf("EGTK", "LFPB"),
        departureTime = departureTime,
        targetDate = departureTime.take(10),
        flightDurationHours = flightDurationHours,
        section = section,
    )

    private fun flight(
        id: String,
        days: Double,
        section: String? = null,
        durationHours: Double = 2.0,
    ): FlightResponse = makeFlight(
        id = id,
        departureTime = isoOffsetDays(days),
        flightDurationHours = durationHours,
        section = section,
    )

    @Test
    fun `legacy grouping buckets by date with 7 day Recent window`() {
        val groups = FlightResponse.groupFlights(
            flights = listOf(
                flight("future", days = 1.0),
                flight("recent", days = -2.0),
                flight("old", days = -30.0),
            ),
            nowEpochMs = nowMs,
        )
        assertEquals(listOf("Future", "Recent", "Past"), groups.map { it.title })
        assertEquals(listOf("recent"), groups.first { it.title == "Recent" }.flights.map { it.id })
        assertEquals(listOf("old"), groups.first { it.title == "Past" }.flights.map { it.id })
    }

    @Test
    fun `empty groups are dropped`() {
        val groups = FlightResponse.groupFlights(
            flights = listOf(flight("a", days = 2.0)),
            nowEpochMs = nowMs,
        )
        assertEquals(listOf("Future"), groups.map { it.title })
    }

    @Test
    fun `server section wins over date bucketing`() {
        val groups = FlightResponse.groupFlights(
            flights = listOf(
                flight("a", days = -100.0, section = "recent"),
                flight("b", days = 5.0, section = "past"),
            ),
            nowEpochMs = nowMs,
        )
        assertEquals(listOf("a"), groups.first { it.title == "Recent" }.flights.map { it.id })
        assertEquals(listOf("b"), groups.first { it.title == "Past" }.flights.map { it.id })
    }

    @Test
    fun `upcoming flights ordering respects furthest_first and soonest_first while Recent and Past stay most-recent-first`() {
        val fixture = listOf(
            flight("near", days = 2.0, section = "future"),
            flight("far", days = 9.0, section = "future"),
            flight("mid", days = 5.0, section = "future"),
            flight("recent-old", days = -5.0, section = "recent"),
            flight("recent-new", days = -1.0, section = "recent"),
            flight("past-old", days = -40.0, section = "past"),
            flight("past-new", days = -30.0, section = "past"),
        )

        val furthest = FlightResponse.groupFlights(fixture, order = FlightOrder.FURTHEST_FIRST, nowEpochMs = nowMs)
        assertEquals(listOf("far", "mid", "near"), furthest.first { it.title == "Future" }.flights.map { it.id })
        assertEquals(listOf("recent-new", "recent-old"), furthest.first { it.title == "Recent" }.flights.map { it.id })
        assertEquals(listOf("past-new", "past-old"), furthest.first { it.title == "Past" }.flights.map { it.id })

        val soonest = FlightResponse.groupFlights(fixture, order = FlightOrder.SOONEST_FIRST, nowEpochMs = nowMs)
        assertEquals(listOf("near", "mid", "far"), soonest.first { it.title == "Future" }.flights.map { it.id })
        assertEquals(listOf("recent-new", "recent-old"), soonest.first { it.title == "Recent" }.flights.map { it.id })
        assertEquals(listOf("past-new", "past-old"), soonest.first { it.title == "Past" }.flights.map { it.id })
    }

    @Test
    fun `in-progress flight is Future and leads under soonest_first`() {
        val flying = flight("flying", days = -0.5 / 24.0, durationHours = 3.0)
        val later = flight("later", days = 3.0)
        val groups = FlightResponse.groupFlights(
            flights = listOf(flying, later),
            order = FlightOrder.SOONEST_FIRST,
            nowEpochMs = nowMs,
        )
        assertEquals(listOf("flying", "later"), groups.first { it.title == "Future" }.flights.map { it.id })
    }

    @Test
    fun `zero-duration flight is past the instant after departure`() {
        val f = flight("zero", days = -1.0 / 1440.0, durationHours = 0.0)
        val groups = FlightResponse.groupFlights(listOf(f), nowEpochMs = nowMs)
        assertEquals(listOf("Recent"), groups.map { it.title })
    }

    @Test
    fun `hasEnded boundary mirrors server`() {
        val f = makeFlight(departureTime = "2026-06-24T12:00:00Z", flightDurationHours = 2.0)
        fun at(iso: String): Long = IsoTime.parseEpochMs(iso)!!

        assertFalse(f.hasEnded(at("2026-06-24T12:00:00Z"))) // departing
        assertFalse(f.hasEnded(at("2026-06-24T13:59:59Z"))) // airborne
        assertFalse(f.hasEnded(at("2026-06-24T14:00:00Z"))) // exact landing instant
        assertTrue(f.hasEnded(at("2026-06-24T14:00:01Z")))  // 1s after landing
    }

    @Test
    fun `resolvedSection uses server section or 7-day fallback`() {
        val recent = flight("r", days = -2.0)
        assertEquals(FlightSection.RECENT, recent.resolvedSection(nowMs))
        assertEquals(FlightSection.PAST, recent.copy(section = "past").resolvedSection(nowMs))

        val future = flight("f", days = 3.0)
        assertEquals(FlightSection.FUTURE, future.resolvedSection(nowMs))

        val old = flight("o", days = -30.0)
        assertEquals(FlightSection.PAST, old.resolvedSection(nowMs))
    }

    @Test
    fun `isInTrackingWindow pins plus and minus 2h boundaries`() {
        val f = makeFlight(departureTime = "2026-06-24T12:00:00Z", flightDurationHours = 2.0)
        fun at(iso: String): Long = IsoTime.parseEpochMs(iso)!!

        assertTrue(f.isInTrackingWindow(at("2026-06-24T10:00:00Z")))  // departure - 2h
        assertTrue(f.isInTrackingWindow(at("2026-06-24T12:00:00Z")))  // departure
        assertTrue(f.isInTrackingWindow(at("2026-06-24T16:00:00Z")))  // departure + dur + 2h
        assertFalse(f.isInTrackingWindow(at("2026-06-24T09:59:59Z"))) // 1s before start
        assertFalse(f.isInTrackingWindow(at("2026-06-24T16:00:01Z"))) // 1s after end
    }

    @Test
    fun `isHistoricalForRefresh honours 3h post-arrival grace and 12h cap`() {
        val f = makeFlight(departureTime = "2026-06-24T12:00:00Z", flightDurationHours = 2.0)
        fun at(iso: String): Long = IsoTime.parseEpochMs(iso)!!

        assertFalse(f.isHistoricalForRefresh(at("2026-06-24T06:00:00Z")))
        assertFalse(f.isHistoricalForRefresh(at("2026-06-24T12:00:00Z")))
        assertFalse(f.isHistoricalForRefresh(at("2026-06-24T13:30:00Z")))
        // Post-arrival grace window (14:00Z .. 17:00Z)
        assertFalse(f.isHistoricalForRefresh(at("2026-06-24T14:30:00Z")))
        assertFalse(f.isHistoricalForRefresh(at("2026-06-24T17:00:00Z")))
        // Historical after grace window
        assertTrue(f.isHistoricalForRefresh(at("2026-06-24T17:00:01Z")))
        assertTrue(f.isHistoricalForRefresh(at("2026-06-25T12:00:00Z")))

        // Oversized duration capped at 12h max window
        val longFlight = makeFlight(departureTime = "2026-06-24T12:00:00Z", flightDurationHours = 40.0)
        assertFalse(longFlight.isHistoricalForRefresh(at("2026-06-25T00:00:00Z"))) // +12h edge
        assertTrue(longFlight.isHistoricalForRefresh(at("2026-06-25T00:00:01Z")))
    }
}
