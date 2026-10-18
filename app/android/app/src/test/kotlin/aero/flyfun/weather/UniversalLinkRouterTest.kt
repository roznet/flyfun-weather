package aero.flyfun.weather

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
                viewMode = null,
            ),
            target,
        )
    }

    @Test
    fun `maps forecast deep link parses all parameters`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/maps.html?tab=forecast&fc.day=1&fc.hour=15&fc.model=icon&fc.metric=crosswind&fc.gramet_init=2026040512&now.view=gramet"
        )
        assertEquals(
            NavigationTarget.ForecastMap(
                dayOffset = 1,
                hourUtc = 15,
                model = "icon",
                metric = "crosswind",
                grametInitKey = "2026040512",
                viewMode = "gramet",
            ),
            target,
        )
    }

    @Test
    fun `maps historical deep link parses all parameters`() {
        val target = UniversalLinkRouter.navigationTarget(
            "https://weather.flyfun.aero/maps.html?tab=historical&hist.date=2026-03-15&hist.hour=9&hist.metric=alert&clim.view=verify"
        )
        assertEquals(
            NavigationTarget.HistoricalMap(
                date = "2026-03-15",
                hourUtc = 9,
                metric = "alert",
                viewMode = "verify",
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
