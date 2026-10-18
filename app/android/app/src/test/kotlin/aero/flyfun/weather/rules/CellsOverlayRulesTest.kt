package aero.flyfun.weather.rules

import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.DisplayCell
import aero.flyfun.weather.model.rules.CellFrameEntry
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.LightningAgeBucket
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CellsOverlayRulesTest {

    private val sampleFrames = listOf(
        CellFrameEntry(timestamp = "20260412T100000Z", validTime = "2026-04-12T10:00:00Z"),
        CellFrameEntry(timestamp = "20260412T101500Z", validTime = "2026-04-12T10:15:00Z"),
        CellFrameEntry(timestamp = "20260412T103000Z", validTime = "2026-04-12T10:30:00Z"),
    )

    @Test
    fun `matchCellFrame handles exact match nearest within 10min drift tie-breaking and out-of-range`() {
        assertNull(CellsOverlayRules.matchCellFrame(emptyList(), "2026-04-12T10:15:00Z"))

        // Null target returns latest frame
        val latest = CellsOverlayRules.matchCellFrame(sampleFrames, null)
        assertNotNull(latest)
        assertEquals("20260412T103000Z", latest?.timestamp)

        // Exact match by compact timestamp or ISO validTime
        assertEquals(
            "20260412T101500Z",
            CellsOverlayRules.matchCellFrame(sampleFrames, "20260412T101500Z")?.timestamp,
        )
        assertEquals(
            "20260412T101500Z",
            CellsOverlayRules.matchCellFrame(sampleFrames, "2026-04-12T10:15:00Z")?.timestamp,
        )

        // Nearest within 10 minutes (10:18Z is 3 min after 10:15Z)
        assertEquals(
            "20260412T101500Z",
            CellsOverlayRules.matchCellFrame(sampleFrames, "2026-04-12T10:18:00Z")?.timestamp,
        )

        // Exact midpoint tie (10:07:30Z is 7.5 min from both 10:00Z and 10:15Z) -> prefers earlier frame
        assertEquals(
            "20260412T100000Z",
            CellsOverlayRules.matchCellFrame(sampleFrames, "2026-04-12T10:07:30Z")?.timestamp,
        )

        // Beyond 10-minute drift threshold (10:45Z is 15 min after 10:30Z) -> returns null
        assertNull(CellsOverlayRules.matchCellFrame(sampleFrames, "2026-04-12T10:45:00Z"))
    }

    @Test
    fun `isCellFrameStale detects frames older than threshold minutes`() {
        val nowMs = CellsOverlayRules.parseFrameEpochMs("2026-04-12T11:00:00Z")!!
        val freshMs = CellsOverlayRules.parseFrameEpochMs("2026-04-12T10:40:00Z")!! // 20m old
        val boundaryMs = CellsOverlayRules.parseFrameEpochMs("2026-04-12T10:35:00Z")!! // 25m old
        val staleMs = CellsOverlayRules.parseFrameEpochMs("2026-04-12T10:34:00Z")!! // 26m old

        assertFalse(CellsOverlayRules.isCellFrameStale(freshMs, nowMs, staleThresholdMinutes = 25))
        assertFalse(CellsOverlayRules.isCellFrameStale(boundaryMs, nowMs, staleThresholdMinutes = 25))
        assertTrue(CellsOverlayRules.isCellFrameStale(staleMs, nowMs, staleThresholdMinutes = 25))
        assertTrue(CellsOverlayRules.isCellFrameStale("2026-04-12T10:20:00Z", nowMs, staleThresholdMinutes = 25))
    }

    @Test
    fun `filterOperationalCells keeps all cells when suppressSuspect is false and removes suspect cells when true`() {
        val cells = listOf(
            DisplayCell(id = "cell-1", severity = "severe", lat = 45.0, lon = 5.0, flashCount = 24, maxDbz = 56.0),
            DisplayCell(id = "cell-2", severity = "moderate", lat = 45.5, lon = 5.2, flashCount = 5, maxDbz = 46.0),
            DisplayCell(id = "suspect-1", severity = "suspect", lat = 46.0, lon = 5.5, flashCount = 0, maxDbz = 38.0),
            DisplayCell(id = "cell-unconfirmed", severity = "unconfirmed", lat = 46.2, lon = 5.8, flashCount = 0, maxDbz = 34.0),
        )
        val display = CellDisplay(validTime = "2026-04-12T10:30:00Z", cells = cells)

        val unfiltered = CellsOverlayRules.filterOperationalCells(display, suppressSuspect = false)
        assertEquals(4, unfiltered.cells?.size)

        val operational = CellsOverlayRules.filterOperationalCells(display, suppressSuspect = true)
        assertEquals(listOf("cell-1", "cell-2"), operational.cells?.map { it.id })
    }

    @Test
    fun `projectCellArrow projects lat lon forward along motion bearing and distance`() {
        // Zero speed returns original position
        val stationary = CellsOverlayRules.projectCellArrow(
            lat = 48.0,
            lon = 2.0,
            speedKt = 0.0,
            dirFromDeg = 90.0,
            minutesAhead = 30.0,
        )
        assertEquals(48.0, stationary.first, 1e-6)
        assertEquals(2.0, stationary.second, 1e-6)

        // Northward motion (0 deg) at 60 kt for 60 min = 60 nm = 111.12 km (~1.0 deg latitude)
        val north = CellsOverlayRules.projectCellArrow(
            lat = 48.0,
            lon = 2.0,
            speedKt = 60.0,
            dirFromDeg = 0.0,
            minutesAhead = 60.0,
        )
        assertEquals(48.0 + (111.12 / 111.32), north.first, 0.01)
        assertEquals(2.0, north.second, 1e-4)

        // Eastward motion (90 deg) at 30 kt for 30 min = 15 nm = 27.78 km at equator
        val east = CellsOverlayRules.projectCellArrow(
            lat = 0.0,
            lon = 10.0,
            speedKt = 30.0,
            dirFromDeg = 90.0,
            minutesAhead = 30.0,
        )
        assertEquals(0.0, east.first, 1e-4)
        assertTrue(east.second > 10.2)
    }

    @Test
    fun `classifyFlashAge assigns strikes to FRESH_5M RECENT_15M OLDER_30M and EXPIRED`() {
        val refSec = 1_700_000_000L

        assertEquals(
            LightningAgeBucket.FRESH_5M,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 120L, refEpochSec = refSec),
        )
        assertEquals(
            LightningAgeBucket.FRESH_5M,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 300L, refEpochSec = refSec),
        )
        assertEquals(
            LightningAgeBucket.RECENT_15M,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 301L, refEpochSec = refSec),
        )
        assertEquals(
            LightningAgeBucket.RECENT_15M,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 900L, refEpochSec = refSec),
        )
        assertEquals(
            LightningAgeBucket.OLDER_30M,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 901L, refEpochSec = refSec),
        )
        assertEquals(
            LightningAgeBucket.OLDER_30M,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 1800L, refEpochSec = refSec),
        )
        assertEquals(
            LightningAgeBucket.EXPIRED,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec - 1801L, refEpochSec = refSec),
        )
        // Far in the future relative to scrubbed frame -> EXPIRED
        assertEquals(
            LightningAgeBucket.EXPIRED,
            CellsOverlayRules.classifyFlashAge(flashEpochSec = refSec + 300L, refEpochSec = refSec),
        )

        // Verify bucket visual properties
        assertEquals(1.0f, LightningAgeBucket.FRESH_5M.alpha, 1e-5f)
        assertEquals(0xFFFACC15L, LightningAgeBucket.FRESH_5M.colorHex)
        assertEquals(0.75f, LightningAgeBucket.RECENT_15M.alpha, 1e-5f)
        assertEquals(0xFFF97316L, LightningAgeBucket.RECENT_15M.colorHex)
        assertEquals(0.45f, LightningAgeBucket.OLDER_30M.alpha, 1e-5f)
        assertEquals(0xFFEF4444L, LightningAgeBucket.OLDER_30M.colorHex)
        assertEquals(0.0f, LightningAgeBucket.EXPIRED.alpha, 1e-5f)
    }
}
