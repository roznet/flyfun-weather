package aero.flyfun.weather.rules

import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.ObservedAttribution
import aero.flyfun.weather.model.ObservedFlash
import aero.flyfun.weather.model.ObservedFlashesResponse
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.LatLonBox
import aero.flyfun.weather.model.rules.ObservedLightningRules
import aero.flyfun.weather.model.rules.ObservedLightningRules.State
import aero.flyfun.weather.model.rules.ObservedSummaryChip
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * Lightning on the Android route map (#907). Same cases and fixture as the iOS
 * `ObservedLightningTests`: the age fade, which flashes are drawn and in what order, the bolt,
 * the request box, and the badge (unavailable / no frame / late feed said as such).
 */
class ObservedLightningRulesTest {

    companion object {
        /** 14:11Z: six minutes after the newest lightning frame below. */
        val NOW: Long = IsoTime.parseEpochMs("2026-10-03T14:11:00Z")!!

        fun response(
            flashes: List<ObservedFlash> = listOf(
                ObservedFlash(49.1, 2.1, "2026-10-03T14:09:00+00:00"), // 2 min
                ObservedFlash(49.2, 2.2, "2026-10-03T13:31:00+00:00"), // 40 min
                ObservedFlash(49.3, 2.3, "2026-10-03T12:41:00+00:00"), // 90 min
            ),
            newest: String? = "2026-10-03T14:05:00+00:00",
            attribution: String? = "EUMETSAT",
        ) = ObservedFlashesResponse(
            flashes = flashes, count = flashes.size, newestValidTime = newest,
            windowMinutes = 10.0, retentionMinutes = 180.0,
            attribution = ObservedAttribution(text = attribution),
        )
    }

    @Test
    fun `fades by age and drops the tail`() {
        assertEquals(0.9, ObservedLightningRules.opacity(0.0), 0.0)
        assertEquals(0.45, ObservedLightningRules.opacity(30.0), 1e-9)
        assertEquals(0.0, ObservedLightningRules.opacity(60.0), 0.0)
        assertEquals(0.0, ObservedLightningRules.opacity(90.0), 0.0)
    }

    @Test
    fun `draws flashes inside the trail, oldest first, newest on top`() {
        val flashes = response().flashes + ObservedFlash(49.4, 2.4, "not a time")
        val drawn = ObservedLightningRules.drawable(flashes, NOW)
        assertEquals(listOf(49.2, 49.1), drawn.map { it.lat })
        assertEquals(ObservedLightningRules.opacity(40.0), drawn[0].opacity, 1e-9)
        assertTrue(drawn[1].opacity > drawn[0].opacity)
    }

    @Test
    fun `the bolt is centred on the flash and stays inside its box`() {
        val w = ObservedLightningRules.GLYPH_WIDTH_DP
        val h = ObservedLightningRules.GLYPH_HEIGHT_DP
        val pts = ObservedLightningRules.glyphPoints(100f, 50f, w, h)
        assertEquals(ObservedLightningRules.GLYPH.size, pts.size)
        pts.forEach { (x, y) ->
            assertTrue(abs(x - 100f) <= w / 2)
            assertTrue(abs(y - 50f) <= h / 2)
        }
        // The tip is the bottom-most vertex, at the bottom of the box.
        assertEquals(50f + h / 2, pts.maxOf { it.second }, 1e-4f)
    }

    @Test
    fun `asks for the corridor box and the trail only`() {
        val box = LatLonBox(south = 48.5, west = 1.25, north = 50.123456, east = 3.0)
        assertEquals(
            listOf("south" to "48.5000", "west" to "1.2500", "north" to "50.1235", "east" to "3.0000", "minutes" to "60"),
            ObservedLightningRules.flashesQuery(box),
        )
    }

    @Test
    fun `badge carries the frame's own time, its age and the drawn count`() {
        val state = State.Loaded(response())
        assertEquals("Lightning 14:05Z · 6 min old · 2 flashes in the last 60 min · EUMETSAT",
            ObservedLightningRules.badge(state, NOW))
        assertEquals(ObservedSummaryChip("Lightning", "6 min", false), ObservedLightningRules.chip(state, NOW))
    }

    @Test
    fun `a single flash is singular`() {
        assertEquals("1 flash in the last 60 min", ObservedLightningRules.flashCountText(1))
        assertEquals("0 flashes in the last 60 min", ObservedLightningRules.flashCountText(0))
    }

    @Test
    fun `unavailable and no frame are named, never no lightning`() {
        assertNull(ObservedLightningRules.badge(State.Loading, NOW))
        assertNull(ObservedLightningRules.chip(State.Loading, NOW))
        assertEquals("Lightning unavailable", ObservedLightningRules.badge(State.Unavailable, NOW))
        assertEquals(true, ObservedLightningRules.chip(State.Unavailable, NOW)?.isWarning)
        val empty = State.Loaded(response(flashes = emptyList(), newest = null, attribution = null))
        assertEquals("Lightning: no current frame", ObservedLightningRules.badge(empty, NOW))
        assertEquals(ObservedSummaryChip("Lightning", "n/a", true), ObservedLightningRules.chip(empty, NOW))
    }

    @Test
    fun `a late feed still draws but says newer flashes may be missing`() {
        val late = NOW + 30 * 60_000L // newest frame 36 min old
        val state = State.Loaded(response())
        val badge = ObservedLightningRules.badge(state, late).orEmpty()
        assertTrue(badge, badge.startsWith("Lightning 14:05Z · 36 min old · feed late, newer flashes may be missing"))
        assertEquals(true, ObservedLightningRules.chip(state, late)?.isWarning)
    }

    @Test
    fun `decodes the server payload`() {
        val json = """
        {"flashes": [{"lat": 49.1, "lon": 2.1, "time": "2026-10-03T14:09:00+00:00"}],
         "count": 1, "newest_valid_time": "2026-10-03T14:05:00+00:00",
         "window_minutes": 10.0, "retention_minutes": 180.0,
         "attribution": {"text": "EUMETSAT", "producer": "EUMETSAT"}}
        """
        val decoded = WbJson.decodeFromString(ObservedFlashesResponse.serializer(), json)
        assertEquals(1, decoded.flashes.size)
        assertEquals("2026-10-03T14:05:00+00:00", decoded.newestValidTime)
        assertEquals("EUMETSAT", decoded.attribution?.text)
        val none = """
        {"flashes": [], "count": 0, "newest_valid_time": null, "window_minutes": 10.0,
         "retention_minutes": 180.0, "attribution": {}}
        """
        assertNull(WbJson.decodeFromString(ObservedFlashesResponse.serializer(), none).newestValidTime)
    }
}
