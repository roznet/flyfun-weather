package aero.flyfun.weather.rules

import aero.flyfun.weather.model.LiveStorm
import aero.flyfun.weather.model.LiveStorms
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.LiveStormRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * #886: the Observed storm cards and the route map read the server's `/live` storms as
 * the server sends them (models/live.py `LiveStorm`): trend, intensity, side, place,
 * relative motion and abeam in its words, never a phone-side disposition or severity.
 * The fixture is iOS `LiveStormListTests.stormsJSON`, so both clients assert one payload.
 */
class LiveStormRulesTest {

    private val stormsJson = """
        {"status": "available", "frame_time": "2099-06-30T08:25:00Z", "corridor_nm": 30.0, "route_nm": 120.0,
         "lightning_pending": false,
         "storms": [
           {"id": "core35-behind", "lat": 43.5, "lon": 5.2, "peak_dbz": 44.0, "along_nm": 10.0, "offtrack_nm": 12.0,
            "cross_nm": -12.0, "side": "left", "ahead": false, "relative_motion": "moving_away", "closing_kt": -5.0},
           {"id": "core35-zz", "lat": 43.6, "lon": 5.5, "peak_dbz": 48.0, "intensity": "heavy", "flashes": 3,
            "trend": "developing", "top_fl": 320, "along_nm": 40.0, "offtrack_nm": 6.0, "cross_nm": 6.0, "side": "right",
            "ahead": true, "relative_motion": "closing", "closing_kt": 7.2, "abeam_eta": "2099-06-30T08:52:00Z",
            "minutes_to_abeam": 26.4},
           {"id": "core41-dest", "lat": 43.9, "lon": 5.9, "peak_dbz": 52.0, "along_nm": 125.0, "offtrack_nm": 8.0,
            "cross_nm": 0.0, "end": "destination", "end_icao": "ZZAR", "end_bearing": "NE", "ahead": true,
            "relative_motion": "unknown", "flashes_pending": true, "truncated": true}
         ]}
    """.trimIndent()

    private fun storms(): List<LiveStorm> = WbJson.decodeFromString(LiveStorms.serializer(), stormsJson).cells

    private fun storm(json: String): LiveStorm = WbJson.decodeFromString(LiveStorm.serializer(), json)

    @Test
    fun `server-shaped storms decode with their positions and words`() {
        val (behind, zz, dest) = storms()
        assertEquals(43.6, zz.lat!!, 1e-9)
        assertEquals(5.5, zz.lon!!, 1e-9)
        assertEquals("Heavy cell", LiveStormRules.title(zz))
        assertEquals("Cell", LiveStormRules.title(dest))
        assertEquals("6 NM right of track at 40 NM", LiveStormRules.positionText(zz))
        assertEquals("8 NM NE of ZZAR", LiveStormRules.positionText(dest))
        assertEquals("12 NM left of track at 10 NM", LiveStormRules.positionText(behind))
    }

    @Test
    fun `motion and abeam are the server's words`() {
        val (behind, zz, dest) = storms()
        assertEquals("closing 7 kt", LiveStormRules.motionText(zz))
        assertEquals("abeam in ~25 min (~08:52Z)", LiveStormRules.abeamText(zz))
        assertEquals("moving away 5 kt", LiveStormRules.motionText(behind))
        assertNull(LiveStormRules.abeamText(behind))
        assertEquals("motion not yet measured", LiveStormRules.motionText(dest))
        assertNull(LiveStormRules.abeamText(dest))
        assertEquals(listOf("lightning pending", "partly outside radar coverage"), LiveStormRules.caveats(dest))
        assertEquals(emptyList<String>(), LiveStormRules.caveats(zz))
    }

    @Test
    fun `trend pill follows the server's trend and the map's colours`() {
        val (behind, zz, _) = storms()
        assertEquals("Developing", LiveStormRules.trendLabel(zz))
        assertEquals(0xFFD7263DL, LiveStormRules.trendColorArgb(zz.trend))
        // No trend from the server: no pill, not a default "Developing".
        assertNull(LiveStormRules.trendLabel(behind))

        val decaying = storm("""{"id": "d", "along_nm": 50, "offtrack_nm": 25, "cross_nm": 25, "side": "left", "trend": "decaying"}""")
        assertEquals("Decaying", LiveStormRules.trendLabel(decaying))
        assertEquals(0xFF1B6CA8L, LiveStormRules.trendColorArgb(decaying.trend))
        // 25 NM off track says where it is, not "VICINITY".
        assertEquals("25 NM left of track at 50 NM", LiveStormRules.positionText(decaying))
    }

    @Test
    fun `every server trend word has a colour`() {
        for (word in listOf("developing", "steady", "decaying", "mixed", "new")) {
            assertNotEquals("no colour for server trend $word", LiveStormRules.UNKNOWN_TREND_HEX, LiveStormRules.trendColorArgb(word))
        }
        assertEquals(LiveStormRules.UNKNOWN_TREND_HEX, LiveStormRules.trendColorArgb(null))
        assertEquals(LiveStormRules.UNKNOWN_TREND_HEX, LiveStormRules.trendColorArgb("whatever"))
    }

    @Test
    fun `abeam rounds like the server`() {
        fun s(minutes: Double?, eta: String? = null) = storm(
            """{"id": "s", "along_nm": 1, "offtrack_nm": 1, "cross_nm": 1, "ahead": true, "minutes_to_abeam": ${minutes ?: "null"},
               "abeam_eta": ${eta?.let { "\"$it\"" } ?: "null"}}""",
        )
        assertEquals("abeam now", LiveStormRules.abeamText(s(1.5)))
        assertEquals("abeam in ~7 min", LiveStormRules.abeamText(s(7.4)))
        assertEquals("abeam in ~45 min", LiveStormRules.abeamText(s(43.0)))
        assertEquals("abeam ~09:05Z", LiveStormRules.abeamText(s(null, eta = "2099-06-30T09:05:00Z")))
        assertNull(LiveStormRules.abeamText(s(null)))
    }

    @Test
    fun `a place from the server leads the position`() {
        val placed = storm(
            """{"id": "p", "along_nm": 40, "offtrack_nm": 6, "cross_nm": 6, "side": "right",
               "place": {"text": "near ZZAA", "relation": "near"}}""",
        )
        assertEquals("6 NM right of track, near ZZAA", LiveStormRules.positionText(placed))
        val past = storm(
            """{"id": "q", "along_nm": 130, "offtrack_nm": 8, "cross_nm": 0, "end": "destination",
               "place": {"text": "8 NM NE of ZZAR", "relation": "past_destination"}}""",
        )
        assertEquals("8 NM NE of ZZAR", LiveStormRules.positionText(past))
    }
}
