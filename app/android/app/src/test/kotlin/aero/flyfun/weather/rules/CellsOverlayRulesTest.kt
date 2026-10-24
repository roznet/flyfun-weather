package aero.flyfun.weather.rules

import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.CellFrame
import aero.flyfun.weather.model.CellFramesResponse
import aero.flyfun.weather.model.CellMotion
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.CellsOverlayRules.Match
import aero.flyfun.weather.model.rules.LatLonBox
import aero.flyfun.weather.model.rules.LiveStormRules
import aero.flyfun.weather.model.rules.ObservedSummaryChip
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The radar-cell overlay on the Android route map (#907). Same cases and fixture as the iOS
 * `CellsOverlayTests` (stamp choice and staleness, the badge, motion/trend words, arrows only for
 * measured motion on cores, display decoding), so the two apps cannot drift apart.
 */
class CellsOverlayRulesTest {

    companion object {
        /** 14:17Z: twelve minutes after the 14:05Z frame. */
        val NOW: Long = IsoTime.parseEpochMs("2026-10-03T14:17:00Z")!!

        fun frame(stamp: String, valid: String, key: String? = null) = CellFrame(stamp = stamp, validTime = valid, key = key)

        fun listing(enabled: Boolean = true, frames: List<CellFrame>? = null): CellFramesResponse {
            val list = frames ?: listOf(
                frame("20261003T1405", "2026-10-03T14:05:00+00:00"),
                frame("20261003T1400", "2026-10-03T14:00:00+00:00"),
                frame("20261003T1350", "2026-10-03T13:50:00+00:00"),
            )
            return CellFramesResponse(
                enabled = enabled, frames = list, newest = list.firstOrNull(), stale = false,
                staleAfterMinutes = 25.0, unavailableSince = null,
                urlTemplate = "/api/observed/cells/{stamp}.json",
            )
        }

        /** A display file as the server sends it, fictional cells (the iOS fixture). */
        const val DISPLAY_JSON = """
        {
          "schema": "observed-cells-display/1",
          "policy_version": "cells-v1+abc123",
          "code_revision": null,
          "valid_time": "2026-10-03T14:05:00+00:00",
          "window_minutes": 10,
          "times": {"radar": "2026-10-03T14:05:00+00:00", "rate": null, "lightning": null, "cloud_top": null},
          "unavailable": [{"what": "lightning", "reason": "late"}],
          "rain_min_area_km2": 2000.0,
          "arrow_minutes": 30,
          "outlines": {"core35": [[[44.0, 5.0], [44.1, 5.1], [44.0, 5.2]]], "rain20": []},
          "cells": [
            {"id": "c1", "tier": "core35", "lat": 44.05, "lon": 5.1, "area_km2": 120.5,
             "peak_dbz": 44.5, "rate_peak_mm_h": 18.2, "flashes": 3, "top_fl": 310,
             "truncated": false, "age_min": 25, "event": "continue",
             "trend": {"state": "developing", "window_min": 30, "d_peak_db": 4, "area_ratio": 1.6, "d_flashes": 2},
             "motion": {"status": "available", "reason": null, "speed_kt": 18.4, "toward_deg": 47},
             "arrow": [44.2, 5.3]},
            {"id": "c2", "tier": "core41", "lat": 44.3, "lon": 5.4, "area_km2": 40,
             "peak_dbz": 52, "rate_peak_mm_h": null, "flashes": null, "top_fl": null,
             "truncated": true, "age_min": 5, "event": "new",
             "trend": {"state": "new", "window_min": null, "d_peak_db": null, "area_ratio": null, "d_flashes": null},
             "motion": {"status": "withheld", "reason": "split", "speed_kt": null, "toward_deg": null},
             "arrow": [44.4, 5.5]},
            {"id": "r1", "tier": "rain20", "lat": 45.0, "lon": 6.0, "area_km2": 5000,
             "peak_dbz": 30, "rate_peak_mm_h": 2, "flashes": 0, "top_fl": null,
             "truncated": false, "age_min": 60, "event": "continue",
             "trend": {"state": "steady", "window_min": 30, "d_peak_db": 0, "area_ratio": 1.0, "d_flashes": 0},
             "motion": {"status": "available", "reason": null, "speed_kt": 10, "toward_deg": 90},
             "arrow": [45.0, 6.2]}
          ]
        }
        """

        fun display(): CellDisplay = WbJson.decodeFromString(CellDisplay.serializer(), DISPLAY_JSON)
    }

    // ---- Which overlay ----

    @Test
    fun `pairs with a stamp, else the newest at or before it`() {
        assertEquals(Match.Ok(frame("20261003T1400", "2026-10-03T14:00:00+00:00")),
            CellsOverlayRules.match(listing(), "20261003T1400", NOW))
        assertEquals(Match.Ok(frame("20261003T1400", "2026-10-03T14:00:00+00:00")),
            CellsOverlayRules.match(listing(), "20261003T1402", NOW))
    }

    @Test
    fun `the chosen overlay's own age decides`() {
        // 13:50Z is 27 min old at 14:17Z, past the 25 min threshold.
        assertEquals(Match.Unavailable("2026-10-03T13:50:00+00:00"),
            CellsOverlayRules.match(listing(), "20261003T1355", NOW))
    }

    @Test
    fun `no radar stamp means the newest, which Android always uses`() {
        assertEquals(Match.Ok(frame("20261003T1405", "2026-10-03T14:05:00+00:00")),
            CellsOverlayRules.match(listing(), null, NOW))
    }

    @Test
    fun `an overlay older than the server's threshold is not drawn`() {
        val late = NOW + 20 * 60_000L // 32 min after 14:05Z
        assertEquals(Match.Unavailable("2026-10-03T14:05:00+00:00"), CellsOverlayRules.match(listing(), null, late))
    }

    @Test
    fun `disabled server and missing listing are distinct answers`() {
        assertEquals(Match.Disabled, CellsOverlayRules.match(listing(enabled = false), null, NOW))
        assertEquals(Match.Unavailable(null), CellsOverlayRules.match(null, null, NOW))
        assertEquals(Match.Unavailable(null), CellsOverlayRules.match(listing(frames = emptyList()), null, NOW))
    }

    @Test
    fun `a radar frame older than every overlay has no pair`() {
        assertEquals(Match.Unavailable("2026-10-03T14:05:00+00:00"),
            CellsOverlayRules.match(listing(), "20261003T1300", NOW))
    }

    // ---- Badge and chip ----

    @Test
    fun `badge carries the overlay's own time, age, count and what is missing`() {
        val match = Match.Ok(frame("20261003T1405", "2026-10-03T14:05:00+00:00"))
        assertEquals("Cells 14:05Z · 12 min old · 3 cells · lightning unavailable · experimental",
            CellsOverlayRules.badge(match, display(), NOW))
    }

    @Test
    fun `chip carries the overlay's own age, unavailable is a warning`() {
        val ok = CellsOverlayRules.chip(Match.Ok(frame("20261003T1405", "2026-10-03T14:05:00+00:00")), NOW)
        assertEquals("Cells 12 min", ok.text)
        assertFalse(ok.isWarning)
        assertEquals("Cells n/a", CellsOverlayRules.chip(Match.Disabled, NOW).text)
        assertEquals(ObservedSummaryChip("Cells", "n/a", true), CellsOverlayRules.chip(Match.Unavailable(null), NOW))
    }

    @Test
    fun `badge wording for disabled and unavailable, the stale note is never hidden`() {
        assertEquals("Cell analysis: not available on this server", CellsOverlayRules.badge(Match.Disabled, null, NOW))
        assertEquals("Cell analysis unavailable since 13:40Z",
            CellsOverlayRules.badge(Match.Unavailable("2026-10-03T13:40:00+00:00"), null, NOW))
        assertEquals("Cell analysis unavailable (nothing received yet)",
            CellsOverlayRules.badge(Match.Unavailable(null), null, NOW))
    }

    // ---- Words ----

    @Test
    fun `motion in words, never codes`() {
        fun m(status: String, speed: Double? = null, toward: Double? = null) =
            CellMotion(status = status, speedKt = speed, towardDeg = toward)
        assertEquals("moving NE at 18 kt", CellsOverlayRules.motionText(m("available", 18.4, 47.0)))
        assertEquals("nearly stationary", CellsOverlayRules.motionText(m("available", 0.5, 47.0)))
        assertEquals("motion not yet measured", CellsOverlayRules.motionText(m("withheld")))
        assertEquals("motion withheld: too little of the cell in matched tiles", CellsOverlayRules.motionText(m("unsupported")))
        assertEquals("no motion yet: no earlier radar frame to compare", CellsOverlayRules.motionText(m("no_pair")))
        assertEquals("motion unknown", CellsOverlayRules.motionText(null))
    }

    @Test
    fun `trend with its numbers`() {
        val cells = display().cells
        assertEquals("developing over 30 min (peak +4 dB, area ×1.6, flashes +2)", CellsOverlayRules.trendText(cells[0].trend))
        assertEquals("new (less than 15 min of history)", CellsOverlayRules.trendText(cells[1].trend))
    }

    @Test
    fun `compass wraps and rounds`() {
        assertEquals("N", CellsOverlayRules.compass(0.0))
        assertEquals("N", CellsOverlayRules.compass(359.0))
        assertEquals("W", CellsOverlayRules.compass(-90.0))
        assertEquals("–", CellsOverlayRules.compass(null))
    }

    @Test
    fun `whole-number dBZ and singular flash`() {
        assertEquals("76 dBZ", CellsOverlayRules.dbzText(75.5))
        assertEquals("–", CellsOverlayRules.dbzText(null))
        assertEquals("1 flash", CellsOverlayRules.flashesText(1.0))
        assertEquals("3 flashes", CellsOverlayRules.flashesText(3.0))
    }

    @Test
    fun `detail lines for a measured core`() {
        assertEquals(
            listOf(
                "peak 45 dBZ · area 120.5 km²",
                "rain rate peak 18.2 mm/h · lightning 3 · cloud top FL310",
                "age 25 min (continue) · developing over 30 min (peak +4 dB, area ×1.6, flashes +2)",
                "moving NE at 18 kt",
            ),
            CellsOverlayRules.detailLines(display().cells[0]),
        )
        assertTrue(CellsOverlayRules.detailLines(display().cells[1]).contains("partly outside radar coverage"))
    }

    // ---- Decoding, arrows, drawing set ----

    @Test
    fun `decodes the server's display file`() {
        val d = display()
        assertEquals(3, d.cells.size)
        assertEquals(18.2, d.cells[0].ratePeakMmH!!, 1e-9)
        assertEquals(120.5, d.cells[0].areaKm2!!, 1e-9)
        assertEquals(4.0, d.cells[0].trend?.dPeakDb!!, 1e-9)
        assertEquals(3, d.outlines?.get("core35")?.first()?.size)
        assertNull(d.times?.cloudTop)
    }

    @Test
    fun `a malformed cell is dropped, not the whole overlay`() {
        val json = """
        {"valid_time": "2026-10-03T14:05:00+00:00",
         "cells": [
           {"id": "ok", "tier": "core35", "lat": 44.0, "lon": 5.0},
           {"id": "no-position", "tier": "core41"},
           {"id": "ok2", "tier": "core41", "lat": 44.2, "lon": 5.2}
         ]}
        """
        val d = WbJson.decodeFromString(CellDisplay.serializer(), json)
        assertEquals(listOf("ok", "ok2"), d.cells.map { it.id })
        assertNull(d.schema)
        assertNull(d.outlines)
    }

    @Test
    fun `arrows only for measured motion on cores, from the feed's own point`() {
        val cells = display().cells
        assertEquals(44.2 to 5.3, CellsOverlayRules.arrowEnd(cells[0])) // core, available
        assertNull(CellsOverlayRules.arrowEnd(cells[1])) // withheld, despite an arrow point
        assertNull(CellsOverlayRules.arrowEnd(cells[2])) // rain area
    }

    @Test
    fun `the route map draws cores and core outlines only`() {
        val d = display()
        assertEquals(listOf("c1", "c2"), CellsOverlayRules.drawnCells(d).map { it.id })
        assertEquals(listOf("core35", "core41"), CellsOverlayRules.OUTLINE_TIERS)
        assertEquals(1, CellsOverlayRules.outlines(d, "core35").size)
        assertTrue(CellsOverlayRules.outlines(d, "core41").isEmpty())
    }

    @Test
    fun `trend colours are the shared table`() {
        assertEquals(0xFFD7263DL, CellsOverlayRules.trendColorArgb("developing"))
        assertEquals(LiveStormRules.UNKNOWN_TREND_HEX, CellsOverlayRules.trendColorArgb(null))
        CellsOverlayRules.TREND_ORDER.forEach { assertNotNull(LiveStormRules.TREND_HEX[it]) }
        assertEquals(0xFFB000B5L, CellsOverlayRules.ARROW_ARGB)
    }

    @Test
    fun `box query uses two decimals, none without a box`() {
        val box = LatLonBox(south = 43.123, west = 4.5, north = 45.0, east = 6.789)
        assertEquals(
            listOf("south" to "43.12", "west" to "4.50", "north" to "45.00", "east" to "6.79"),
            CellsOverlayRules.boxQuery(box),
        )
        assertTrue(CellsOverlayRules.boxQuery(null).isEmpty())
    }

    @Test
    fun `an amended frame is fetched by its newest revision`() {
        val json = """
        {"enabled": true, "stale": false, "stale_after_minutes": 25, "unavailable_since": null,
         "url_template": "/api/observed/cells/{stamp}.json", "newest": null,
         "frames": [{"stamp": "20261003T1405", "key": "20261003T1405.r1", "revision": 1,
                     "valid_time": "2026-10-03T14:05:00+00:00", "received_at": null, "age_minutes": 12}]}
        """
        val listing = WbJson.decodeFromString(CellFramesResponse.serializer(), json)
        assertEquals(1, listing.frames.first().revision)
        assertEquals("20261003T1405.r1", listing.frames.first().displayKey)
        assertEquals("20261003T1405", frame("20261003T1405", "2026-10-03T14:05:00+00:00").displayKey)
    }

    @Test
    fun `lightning on its way reads pending, never no lightning`() {
        val json = """
        {"valid_time": "2026-10-03T14:05:00+00:00", "pending": ["lightning"], "unavailable": [], "revision": 0,
         "cells": [{"id": "c1", "tier": "core41", "lat": 44.0, "lon": 5.0, "flashes": null,
                    "flashes_pending": true, "rate_peak_mm_h": 12, "rate_as_of": "2026-10-03T13:45:00+00:00"}]}
        """
        val d = WbJson.decodeFromString(CellDisplay.serializer(), json)
        val badge = CellsOverlayRules.badge(Match.Ok(frame("20261003T1405", "2026-10-03T14:05:00+00:00")), d, NOW)
        assertTrue(badge.contains("lightning pending"))
        assertFalse(badge.contains("no lightning"))
        val lines = CellsOverlayRules.detailLines(d.cells.first())
        assertTrue(lines.any { it.contains("lightning pending") })
        assertTrue(lines.any { it.contains("12 mm/h (as of 13:45Z)") })
    }

    @Test
    fun `cells box is the corridor plus 50 NM, 20 NM corridor when none was sampled`() {
        val route = listOf(48.0 to 2.0, 49.0 to 3.0)
        val box = CellsOverlayRules.cellsBox(route, null)!!
        val pad = 70 * 1.852 / 111.0
        assertEquals(48.0 - pad, box.south, 1e-9)
        assertEquals(49.0 + pad, box.north, 1e-9)
        assertNull(CellsOverlayRules.cellsBox(emptyList(), 10.0))
    }
}
