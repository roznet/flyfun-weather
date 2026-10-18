package aero.flyfun.weather.rules

import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.PirepOfflineStore
import aero.flyfun.weather.model.PirepResponse
import aero.flyfun.weather.model.SubmitPirepRequest
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files

class PirepOfflineStoreTest {

    private lateinit var tempDir: File

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("flyfun-pirep-test").toFile()
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    @Test
    fun `enqueue persists to pending_pireps json and survives store re-instantiation`() = runTest {
        val store1 = PirepOfflineStore(tempDir)
        val req = SubmitPirepRequest(
            clientUuid = "uuid-101",
            flightId = "flt-1",
            observedAt = "2026-04-12T10:15:00Z",
            latitude = 43.55,
            longitude = 7.01,
            reportedAltitudeFt = 5500,
            turbulenceIntensity = "moderate",
            icingIntensity = "none",
            remarks = "Chop over coastal ridge",
        )

        val queued = store1.enqueue(req, nowEpochMs = 1_775_988_900_000L)
        assertEquals("uuid-101", queued.clientUuid)
        assertEquals(1, store1.pendingCount)
        assertTrue(File(tempDir, PirepOfflineStore.QUEUE_FILENAME).exists())

        // Re-instantiate store on the same directory to verify disk persistence
        val store2 = PirepOfflineStore(tempDir)
        assertEquals(1, store2.pendingCount)
        val restored = store2.pendingItems().single()
        assertEquals("uuid-101", restored.clientUuid)
        assertEquals("2026-04-12T10:15:00Z", restored.request.observedAt)
        assertEquals("moderate", restored.request.turbulenceIntensity)
        assertEquals(5500, restored.request.reportedAltitudeFt)
    }

    @Test
    fun `enqueue deduplicates by clientUuid without creating duplicate entries`() = runTest {
        val store = PirepOfflineStore(tempDir)
        val first = SubmitPirepRequest(
            clientUuid = "uuid-dup",
            flightId = "flt-1",
            observedAt = "2026-04-12T10:00:00Z",
            latitude = 43.55,
            longitude = 7.01,
            turbulenceIntensity = "light",
        )
        val updated = first.copy(turbulenceIntensity = "severe", remarks = "Updated intensity")

        store.enqueue(first, nowEpochMs = 1000L)
        store.enqueue(updated, nowEpochMs = 2000L)

        assertEquals(1, store.pendingCount)
        val item = store.pendingItems().single()
        assertEquals("uuid-dup", item.clientUuid)
        assertEquals("severe", item.request.turbulenceIntensity)
        assertEquals("Updated intensity", item.request.remarks)
        // Preserves original queuedAtEpochMs
        assertEquals(1000L, item.queuedAtEpochMs)
    }

    @Test
    fun `flushAll removes succeeded items drops 4xx permanent failures and retains transient 5xx or network failures`() = runTest {
        val store = PirepOfflineStore(tempDir)

        store.enqueue(
            SubmitPirepRequest(
                clientUuid = "uuid-ok",
                flightId = "flt-1",
                observedAt = "2026-04-12T10:00:00Z",
                latitude = 43.55,
                longitude = 7.01,
                turbulenceIntensity = "light",
            ),
        )
        store.enqueue(
            SubmitPirepRequest(
                clientUuid = "uuid-transient-503",
                flightId = "flt-1",
                observedAt = "2026-04-12T10:05:00Z",
                latitude = 43.60,
                longitude = 7.05,
                turbulenceIntensity = "moderate",
            ),
        )
        store.enqueue(
            SubmitPirepRequest(
                clientUuid = "uuid-permanent-422",
                flightId = "flt-1",
                observedAt = "2026-04-12T10:10:00Z",
                latitude = 43.65,
                longitude = 7.10,
                turbulenceIntensity = "invalid",
            ),
        )
        store.enqueue(
            SubmitPirepRequest(
                clientUuid = "uuid-network-io",
                flightId = "flt-1",
                observedAt = "2026-04-12T10:12:00Z",
                latitude = 43.70,
                longitude = 7.15,
                icingIntensity = "trace",
            ),
        )

        val flushResult = store.flushAll { req ->
            when (req.clientUuid) {
                "uuid-ok" -> PirepResponse(
                    id = 42,
                    clientUuid = req.clientUuid,
                    flightId = req.flightId,
                    observedAt = req.observedAt,
                    turbulenceIntensity = req.turbulenceIntensity,
                )
                "uuid-transient-503" -> throw ApiError.ServerError(503, "Backend temporarily unavailable")
                "uuid-permanent-422" -> throw ApiError.ServerError(422, "Validation failed")
                "uuid-network-io" -> throw ApiError.NetworkError(IOException("No route to host"))
                else -> error("Unexpected uuid ${req.clientUuid}")
            }
        }

        // 1 succeeded, 1 dropped (422), 2 retained (503 + NetworkError)
        assertEquals(1, flushResult.succeeded.size)
        assertEquals("uuid-ok", flushResult.succeeded.single().clientUuid)

        assertEquals(1, flushResult.droppedPermanentFailures.size)
        assertEquals("uuid-permanent-422", flushResult.droppedPermanentFailures.single().clientUuid)

        assertEquals(2, flushResult.remaining.size)
        assertEquals(listOf("uuid-transient-503", "uuid-network-io"), store.pendingItems().map { it.clientUuid })
        assertTrue(store.pendingItems().all { it.attempts == 1 })
        assertNotNull(store.pendingItems().first().lastError)

        // Verify disk state matches remaining items
        val reloaded = PirepOfflineStore(tempDir)
        assertEquals(listOf("uuid-transient-503", "uuid-network-io"), reloaded.pendingItems().map { it.clientUuid })

        // Second flush when network recovers succeeds for all remaining items and deletes the queue file
        val secondFlush = reloaded.flushAll { req ->
            PirepResponse(id = 99, clientUuid = req.clientUuid, observedAt = req.observedAt)
        }
        assertEquals(2, secondFlush.succeeded.size)
        assertEquals(0, secondFlush.remaining.size)
        assertEquals(0, reloaded.pendingCount)
        assertFalse(File(tempDir, PirepOfflineStore.QUEUE_FILENAME).exists())
    }

    @Test
    fun `submitOrEnqueue queues on network failure and rethrows on permanent 4xx validation error`() = runTest {
        val store = PirepOfflineStore(tempDir)
        val req = SubmitPirepRequest(
            clientUuid = "uuid-offline-1",
            flightId = "flt-1",
            observedAt = "2026-04-12T10:20:00Z",
            latitude = 43.55,
            longitude = 7.01,
            turbulenceIntensity = "moderate",
        )

        // Network failure -> returns null and enqueues item on disk
        val resOffline = store.submitOrEnqueue(req, nowEpochMs = 1_700_000_000_000L) {
            throw ApiError.NetworkError(IOException("Airplane mode"))
        }
        assertNull(resOffline)
        assertEquals(1, store.pendingCount)
        assertEquals("uuid-offline-1", store.pendingItems().single().clientUuid)

        // Permanent 422 failure -> rethrows without queueing
        val badReq = SubmitPirepRequest(
            clientUuid = "uuid-bad-422",
            flightId = "flt-1",
            observedAt = "2026-04-12T10:25:00Z",
            latitude = 43.55,
            longitude = 7.01,
        )
        val err = runCatching {
            store.submitOrEnqueue(badReq) {
                throw ApiError.ServerError(422, "Invalid payload")
            }
        }.exceptionOrNull()
        assertTrue(err is ApiError.ServerError)
        assertEquals(1, store.pendingCount)
    }
}
