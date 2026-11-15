package aero.flyfun.weather.rules

import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.ForecastDayEntry
import aero.flyfun.weather.model.ObservedAttribution
import aero.flyfun.weather.model.ObservedFramesResponse
import aero.flyfun.weather.model.ObservedImageryFrame
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.ObservedImageryRules
import aero.flyfun.weather.viewmodel.RouteAirportOverlayModel
import aero.flyfun.weather.viewmodel.RouteObservedImageryModel
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * Observed imagery on the route map (#998), the iOS `ObservedMapImageryTests` cases: the right
 * frames path and tile template, radar over satellite, stale frames never drawn, a failed listing
 * says so. Plus the airport overlay's slot.
 */
class ObservedImageryRulesTest {

    private val now = Instant.parse("2026-11-15T14:17:00Z").toEpochMilli()

    private fun listing(source: String, valid: String = "2026-11-15T14:05:00+00:00", stale: Boolean = false) =
        ObservedFramesResponse(
            source = source,
            label = ObservedImageryRules.label(source),
            frames = listOf(ObservedImageryFrame(stamp = "20261115T1405", validTime = valid, ageMinutes = 12.0)),
            stale = stale,
            tileUrlTemplate = "/api/observed/tiles/$source/{stamp}/{z}/{x}/{y}.png",
            minZoom = 3,
            maxZoom = 9,
            windowMinutes = if (source == ObservedImageryRules.SATELLITE_SOURCE) 0.0 else 10.0,
            attribution = ObservedAttribution(text = "EUMETNET OPERA"),
        )

    @Test
    fun `server listing decodes`() {
        val json = """
            {"source":"opera_dbzh","label":"Radar reflectivity","frames":[{"stamp":"20261115T1405",
            "valid_time":"2026-11-15T14:05:00+00:00","age_minutes":12.0}],"stale":false,"window_minutes":10.0,
            "attribution":{"text":"OPERA"},"tile_url_template":"/api/observed/tiles/opera_dbzh/{stamp}/{z}/{x}/{y}.png",
            "min_zoom":3,"max_zoom":9}
        """.trimIndent()
        val r = WbJson.decodeFromString(ObservedFramesResponse.serializer(), json)
        assertEquals("20261115T1405", r.frames.single().stamp)
        assertEquals(9, r.maxZoom)
        assertEquals("OPERA", r.attribution?.text)
    }

    @Test
    fun `tile path fills the template`() {
        assertEquals(
            "/api/observed/tiles/opera_dbzh/20261115T1405/6/33/22.png",
            ObservedImageryRules.tilePath("/api/observed/tiles/opera_dbzh/{stamp}/{z}/{x}/{y}.png", "20261115T1405", 6, 33, 22),
        )
    }

    @Test
    fun `radar pick falls back only when this briefing lacks it, None stays none`() {
        assertEquals("opera_rate", ObservedImageryRules.resolveSelection("opera_rate", listOf("opera_dbzh", "opera_rate")))
        assertEquals("opera_rate", ObservedImageryRules.resolveSelection("opera_dbzh", listOf("opera_rate")))
        assertEquals("", ObservedImageryRules.resolveSelection("", listOf("opera_dbzh")))
        assertEquals("", ObservedImageryRules.resolveSelection("opera_dbzh", emptyList()))
    }

    @Test
    fun `satellite under radar, badges and chips radar first`() {
        val frames = mapOf(
            "opera_dbzh" to listing("opera_dbzh"),
            ObservedImageryRules.SATELLITE_SOURCE to listing(ObservedImageryRules.SATELLITE_SOURCE),
        )
        val c = ObservedImageryRules.compose("opera_dbzh", true, 0.75f, frames, emptySet(), now)
        assertEquals(listOf(ObservedImageryRules.SATELLITE_SOURCE, "opera_dbzh"), c.layers.map { it.source })
        assertEquals(listOf("Radar 12 min", "Sat 12 min"), c.chips.map { it.text })
        assertTrue(c.badges.first().startsWith("Radar reflectivity 14:05Z · 12 min old · 10 min rolling max"))
        assertEquals(0.8f, c.layers.first().opacity, 0f)
    }

    @Test
    fun `a stale frame is not drawn and says so, a failed listing says unavailable`() {
        val stale = ObservedImageryRules.compose(
            "opera_dbzh", false, 0.75f, mapOf("opera_dbzh" to listing("opera_dbzh", stale = true)), emptySet(), now,
        )
        assertTrue(stale.layers.isEmpty())
        assertEquals("Radar stale", stale.chips.single().text)
        assertTrue(stale.chips.single().isWarning)

        val failed = ObservedImageryRules.compose("opera_dbzh", false, 0.75f, emptyMap(), setOf("opera_dbzh"), now)
        assertEquals("Radar n/a", failed.chips.single().text)
        assertEquals("Radar reflectivity: imagery unavailable", failed.badges.single())

        val loading = ObservedImageryRules.compose("opera_dbzh", false, 0.75f, emptyMap(), emptySet(), now)
        assertTrue(loading.chips.isEmpty())
    }

    @Test
    fun `imagery model keeps a held listing on failure and marks a never-loaded source failed`() = runTest {
        var fail = false
        val repo = object : BriefingRepository by FixtureBriefingRepository() {
            override suspend fun observedFrames(source: String): ObservedFramesResponse {
                if (fail) error("offline")
                return listing(source)
            }
        }
        val model = RouteObservedImageryModel(repo)
        model.refresh(setOf("opera_dbzh"))
        assertEquals("20261115T1405", model.state.value.frames["opera_dbzh"]?.frames?.first()?.stamp)
        fail = true
        model.refresh(setOf("opera_dbzh", ObservedImageryRules.SATELLITE_SOURCE))
        assertTrue("opera_dbzh" in model.state.value.frames)
        assertEquals(setOf(ObservedImageryRules.SATELLITE_SOURCE), model.state.value.failed)
    }

    @Test
    fun `airport overlay slot - nearest hour on the departure day, none outside the grid`() {
        val days = listOf(
            ForecastDayEntry(day = 0, hours = listOf(6, 9, 12, 15), models = listOf("gfs", "icon", "ecmwf")),
            ForecastDayEntry(day = 1, hours = listOf(6, 12, 18), models = listOf("gfs", "ecmwf")),
            ForecastDayEntry(day = 2, available = false),
        )
        val today = Instant.parse("2026-11-15T08:00:00Z")
        val slot = RouteAirportOverlayModel.resolveSlot("2026-11-16T09:00:00Z", days, today)
        assertEquals(RouteAirportOverlayModel.Slot(1, 6, listOf("gfs", "ecmwf")), slot) // 9 → tie → earlier
        assertEquals(12, RouteAirportOverlayModel.resolveSlot("2026-11-15T13:40:00Z", days, today)?.hour)
        assertNull(RouteAirportOverlayModel.resolveSlot("2026-11-17T12:00:00Z", days, today))
        assertNull(RouteAirportOverlayModel.resolveSlot("not a time", days, today))
    }
}
