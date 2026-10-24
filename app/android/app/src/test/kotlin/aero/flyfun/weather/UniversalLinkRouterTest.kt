package aero.flyfun.weather

import aero.flyfun.weather.model.MapViewport
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.UniversalLinkRouter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class UniversalLinkRouterTest {

    @Test
    fun `briefing link with flight id only`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/briefing.html?flight=abc-123"
        )
        assertEquals(
            NavigationTarget.Briefing(flightId = "abc-123", packTimestamp = null, openObserved = false),
            target,
        )
    }

    @Test
    fun `briefing link preserves pack timestamp`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/briefing.html?flight=abc-123&t=2026-04-06T06:00:00%2B00:00"
        )
        assertEquals(
            NavigationTarget.Briefing(
                flightId = "abc-123",
                packTimestamp = "2026-04-06T06:00:00+00:00",
                openObserved = false,
            ),
            target,
        )
    }

    @Test
    fun `briefing link with observed=1 opens observed tab`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/briefing.html?flight=abc-123&t=2026-04-06T06:00:00%2B00:00&observed=1"
        )
        assertEquals(
            NavigationTarget.Briefing(
                flightId = "abc-123",
                packTimestamp = "2026-04-06T06:00:00+00:00",
                openObserved = true,
            ),
            target,
        )
    }

    @Test
    fun `briefing link without flight param is rejected`() {
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/briefing.html"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/briefing.html?flight="))
    }

    @Test
    fun `trip link with id or trip param`() {
        assertEquals(
            NavigationTarget.Trip(tripId = "trip-42"),
            UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/trip.html?id=trip-42"),
        )
        assertEquals(
            NavigationTarget.Trip(tripId = "trip-42"),
            UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/trip.html?trip=trip-42"),
        )
    }

    @Test
    fun `trip link without id is rejected`() {
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/trip.html"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/trip.html?id="))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/trip.html?trip="))
    }

    @Test
    fun `maps link defaults to forecast map`() {
        val target = UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/maps.html")
        assertEquals(
            NavigationTarget.ForecastMap(
                dayOffset = null,
                hourUtc = null,
                model = null,
                metric = null,
                grametInitKey = null,
                view = null,
            ),
            target,
        )
    }

    @Test
    fun `maps forecast deep link parses all parameters`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/maps.html?tab=forecast&fc.day=1&fc.hour=15&fc.model=icon&fc.metric=crosswind_kt" +
                "&fc.apt=EGTK&fc.gramet_init=2026040512&fc.view=51.50,-1.20,7"
        )
        assertEquals(
            NavigationTarget.ForecastMap(
                dayOffset = 1,
                hourUtc = 15,
                model = "icon",
                metric = "crosswind_kt",
                airport = "EGTK",
                grametInitKey = "2026040512",
                view = MapViewport(lat = 51.5, lon = -1.2, zoom = 7),
            ),
            target,
        )
    }

    @Test
    fun `maps link to a web-only tab lands on the forecast map at that tab's view`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/maps.html?tab=now&now.view=45.00,6.00,8&fc.view=50.00,2.00,5"
        )
        assertEquals(NavigationTarget.ForecastMap(view = MapViewport(lat = 45.0, lon = 6.0, zoom = 8)), target)
        // A malformed or out-of-range view is dropped, not guessed
        assertEquals(
            NavigationTarget.ForecastMap(),
            UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/maps.html?fc.view=95.00,6.00,8"),
        )
        assertEquals(
            NavigationTarget.ForecastMap(),
            UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/maps.html?fc.view=gramet"),
        )
    }

    @Test
    fun `maps forecast deep link parses fc date`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/maps.html?fc.day=3&fc.date=2026-10-25&fc.hour=12"
        )
        assertEquals(
            NavigationTarget.ForecastMap(dayOffset = 3, date = "2026-10-25", hourUtc = 12),
            target,
        )
    }

    @Test
    fun `maps forecast deep link drops a malformed fc date and keeps fc day`() {
        for (bad in listOf("2026-02-30", "2026-10-2", "%2B026-10-21", "tomorrow", "")) {
            val target = UniversalLinkRouter.navigationTarget(
                "https://weather.flyfun.aero/maps.html?fc.day=2&fc.date=$bad"
            )
            assertEquals(bad, NavigationTarget.ForecastMap(dayOffset = 2), target)
        }
    }

    @Test
    fun `maps historical deep link parses all parameters`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/maps.html?tab=historical&hist.date=2026-03-15&hist.time=09:30&hist.lead=2" +
                "&hist.source=taf&hist.metric=ceiling_ft&hist.apt=LFPB&hist.view=48.90,2.40,9"
        )
        assertEquals(
            NavigationTarget.HistoricalMap(
                date = "2026-03-15",
                time = "09:30",
                lead = 2,
                source = "taf",
                metric = "ceiling_ft",
                airport = "LFPB",
                view = MapViewport(lat = 48.9, lon = 2.4, zoom = 9),
            ),
            target,
        )
    }

    @Test
    fun `shared flight link extracts code and rejects malformed codes`() {
        assertEquals(
            NavigationTarget.SharedFlight(code = "Ab3xYz9Q"),
            UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/s/Ab3xYz9Q"),
        )
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/s/"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/s/ab"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/s/0123456789abcdefX"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/s/abc\$def"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/s/Ab3xYz9Q/extra"))
    }

    @Test
    fun `shared trip link extracts code and rejects malformed codes`() {
        assertEquals(
            NavigationTarget.SharedTrip(code = "Tr9pK2mN"),
            UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/t/Tr9pK2mN"),
        )
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/t/"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/t/xy"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/t/0123456789abcdefX"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/t/bad!code"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/t/Tr9pK2mN/more"))
    }

    @Test
    fun `foreign host and auth callback are rejected`() {
        assertNull(UniversalLinkRouter.navigationTarget("https://evil.example.com/briefing.html?flight=abc-123"))
        assertNull(UniversalLinkRouter.navigationTarget("https://evil.example.com/s/Ab3xYz9Q"))
        assertNull(UniversalLinkRouter.navigationTarget("flyfunweather://briefing.html?flight=abc-123"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/auth/callback?code=c&state=s"))
        assertNull(UniversalLinkRouter.navigationTarget("https://weather.flyfun.aero/index.html"))
    }
}
