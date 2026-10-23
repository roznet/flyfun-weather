package aero.flyfun.weather.rules

import aero.flyfun.weather.data.PendingFixStore
import aero.flyfun.weather.data.TrackingSession
import aero.flyfun.weather.model.LivePositionFix
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** #889: queued position fixes survive a process kill (a new store over the same directory). */
class PendingFixStoreTest {

    private lateinit var tempDir: File

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("flyfun-fixes-test").toFile()
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun fix(at: String) = LivePositionFix(lat = 51.0, lon = -0.5, altitudeFt = 3000.0, recordedAt = at, hAccuracyM = 8.0)

    @Test
    fun `queues and session survive a new store instance`() {
        val first = PendingFixStore(tempDir)
        first.save("ZZ-flight-1", listOf(fix("2026-06-24T10:00:00Z"), fix("2026-06-24T10:01:00Z")), landed = false)
        first.save("ZZ-flight-2", emptyList(), landed = true)
        first.saveSession(TrackingSession("ZZ-flight-1", listOf(listOf(51.0, -0.5), listOf(47.0, 7.0)), 1_000L))

        val afterKill = PendingFixStore(tempDir)
        assertEquals(2, afterKill.queue("ZZ-flight-1")?.fixes?.size)
        assertTrue(afterKill.queue("ZZ-flight-2")?.landed == true)
        assertEquals("ZZ-flight-1", afterKill.session()?.flightId)
        assertEquals(1_000L, afterKill.session()?.endEpochMs)
    }

    @Test
    fun `an emptied queue is removed and an empty store deletes its file`() {
        val store = PendingFixStore(tempDir)
        store.save("ZZ-flight-1", listOf(fix("2026-06-24T10:00:00Z")), landed = false)
        store.save("ZZ-flight-1", emptyList(), landed = false)

        assertNull(store.queue("ZZ-flight-1"))
        assertFalse(File(tempDir, PendingFixStore.FILENAME).exists())
    }

    @Test
    fun `a corrupt file reads as empty and clear removes everything`() {
        File(tempDir, PendingFixStore.FILENAME).writeText("{not json")
        val store = PendingFixStore(tempDir)
        assertTrue(store.queues().isEmpty())

        store.save("ZZ-flight-1", listOf(fix("2026-06-24T10:00:00Z")), landed = true)
        store.clear()
        assertTrue(PendingFixStore(tempDir).queues().isEmpty())
    }
}
