package aero.flyfun.weather

import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.BriefingCacheStore
import aero.flyfun.weather.data.CachedPackEntry
import aero.flyfun.weather.data.CachingBriefingRepository
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.PackChart
import aero.flyfun.weather.model.WbJson
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

class BriefingCacheStoreTest {

    private lateinit var tempDir: File

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("flyfun-cache-test").toFile()
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun pngBytes(tag: String): ByteArray =
        byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A) + tag.toByteArray(Charsets.UTF_8)

    private fun packMetaJsonBytes(flightId: String, timestamp: String, cacheKeys: List<String>): ByteArray {
        val charts = cacheKeys.mapIndexed { i, key ->
            """
            {"chart_id": "c$i", "label": "L", "caption": "", "valid_time": null, "init_time": null,
             "url": "/x", "overlay_key": "icon", "cache_key": "$key", "file_ext": "png",
             "available": true, "prefetch": false}
            """.trimIndent()
        }.joinToString(",")
        val json = """
        {
          "flight_id": "$flightId", "fetch_timestamp": "$timestamp", "days_out": 1,
          "is_historical": false, "has_gramet": false, "has_skewt": false, "has_digest": false,
          "has_advisories": false, "model_init_times": {}, "grib_init_times": {},
          "models_skipped_region": [],
          "charts": {"in_coverage": true, "sources": [
            {"source": "dwd", "label": "DWD", "attribution": "DWD", "default_chart_id": "c0",
             "overlay_url": "/o", "charts": [$charts]}
          ]}
        }
        """.trimIndent()
        return json.toByteArray(Charsets.UTF_8)
    }

    @Test
    fun `write and read pack data and unpackBundle`() = runTest {
        val store = BriefingCacheStore(tempDir)
        val payload = """{"assessment":"green"}""".toByteArray(Charsets.UTF_8)
        store.writeData(payload, flightId = "flt-1", timestamp = "2026-07-01T06:00:00+00:00", endpoint = "digest")

        assertTrue(store.hasEndpoint("flt-1", "2026-07-01T06:00:00+00:00", "digest"))
        assertArrayEquals(payload, store.readData("flt-1", "2026-07-01T06:00:00+00:00", "digest"))
        assertFalse(store.isPackCached("flt-1", "2026-07-01T06:00:00+00:00"))

        val bundleJson = """
        {
          "advisories": {"advisories": [], "catalog": []},
          "digest": {"assessment": "amber"},
          "snapshot": {"target_date": "2026-07-01"},
          "route-analyses": {"route_name": "LFMD LFML", "analyses": []},
          "elevation": {"route_name": "LFMD LFML", "points": []},
          "sounding-0-ecmwf": {"point_index": 0, "model": "ecmwf", "levels": []},
          "alternates": null
        }
        """.trimIndent().toByteArray(Charsets.UTF_8)

        val entry = store.unpackBundle(
            bundleBytes = bundleJson,
            flightId = "flt-2",
            timestamp = "2026-07-01T06:00:00Z",
            flightTitle = "LFMD → LFML",
            assessment = "amber",
            departureTime = "2026-07-02T09:00:00Z",
        )

        assertTrue(entry.isComplete)
        assertTrue(store.isPackCached("flt-2", "2026-07-01T06:00:00Z"))
        assertTrue(store.hasEndpoint("flt-2", "2026-07-01T06:00:00Z", "sounding-0-ecmwf"))
        assertFalse(store.hasEndpoint("flt-2", "2026-07-01T06:00:00Z", "alternates"))
    }

    @Test
    fun `per-scope directories are isolated and legacy layout migrates cleanly`() = runTest {
        val storeA = BriefingCacheStore(BriefingCacheStore.scopedCacheDir(tempDir, "aaaa"))
        val storeB = BriefingCacheStore(BriefingCacheStore.scopedCacheDir(tempDir, "bbbb"))

        storeA.writeMetadata("""[{"id":"a"}]""".toByteArray(), name = "flights")
        assertNull(storeB.readMetadata("flights"))
        assertNotNull(storeA.readMetadata("flights"))

        // Legacy root artifacts under BriefingCache/
        val root = File(tempDir, "BriefingCache")
        File(root, "flights.json").writeText("[]")
        File(root, "index.json").writeText("{}")
        File(root, "flt-legacy").mkdirs()

        BriefingCacheStore.migrateLegacyLayout(tempDir)

        assertFalse(File(root, "flights.json").exists())
        assertFalse(File(root, "index.json").exists())
        assertFalse(File(root, "flt-legacy").exists())
        assertNotNull(storeA.readMetadata("flights"))
    }

    @Test
    fun `evictInactiveUsers removes only stale non-active user directories`() {
        val nowMs = System.currentTimeMillis()
        fun makeUserDir(scope: String, markerAgeDays: Int?) {
            val dir = BriefingCacheStore.scopedCacheDir(tempDir, scope)
            dir.mkdirs()
            if (markerAgeDays != null) {
                val marker = File(dir, "flights.json")
                marker.writeText("[]")
                marker.setLastModified(nowMs - markerAgeDays.toLong() * 86_400_000L)
            }
        }

        makeUserDir("current", markerAgeDays = 100)
        makeUserDir("stale", markerAgeDays = 60)
        makeUserDir("fresh", markerAgeDays = 3)
        makeUserDir("nomarker", markerAgeDays = null)

        BriefingCacheStore.evictInactiveUsers(tempDir, keepingScope = "current", olderThanDays = 30, nowEpochMs = nowMs)

        assertTrue(BriefingCacheStore.scopedCacheDir(tempDir, "current").exists())
        assertFalse(BriefingCacheStore.scopedCacheDir(tempDir, "stale").exists())
        assertTrue(BriefingCacheStore.scopedCacheDir(tempDir, "fresh").exists())
        assertTrue(BriefingCacheStore.scopedCacheDir(tempDir, "nomarker").exists())
    }

    @Test
    fun `chart cache rejects path traversal and serves cached charts without redownloading`() = runTest {
        val store = BriefingCacheStore(tempDir)
        for (bad in listOf("../escape.png", "dwd//x.png", "/abs.png", "dwd/../../x.png", "dwd\\x.png")) {
            val result = runCatching { store.writeChart(byteArrayOf(1), cachePath = bad) }
            assertTrue("Expected failure for $bad", result.isFailure)
            assertFalse(store.hasChart(bad))
        }

        var downloadCalls = 0
        val bytes = pngBytes("chart-payload")
        val fakeOnline = object : FixtureBriefingRepository() {
            override suspend fun chartImage(chart: PackChart): ByteArray {
                downloadCalls++
                return bytes
            }
        }
        val repo = CachingBriefingRepository(online = fakeOnline, cache = store)
        val chart = PackChart(
            chartId = "048",
            url = "/api/flights/f1/packs/ts/dwd-chart/048",
            cacheKey = "dwd/2026-05-08T06Z/048-20260508T044300Z",
            fileExt = "png",
        )

        assertArrayEquals(bytes, repo.chartImage(chart))
        assertArrayEquals(bytes, repo.chartImage(chart))
        assertEquals(1, downloadCalls)
        assertTrue(store.hasChart(chart.cachePath))

        // Non-image bytes behind a 200 are rejected and not cached
        val htmlOnline = object : FixtureBriefingRepository() {
            override suspend fun chartImage(chart: PackChart): ByteArray =
                "<html>proxy error</html>".toByteArray(Charsets.UTF_8)
        }
        val htmlRepo = CachingBriefingRepository(online = htmlOnline, cache = store)
        val badChart = chart.copy(cacheKey = "dwd/2026-05-08T06Z/bad-key")
        val err = runCatching { htmlRepo.chartImage(badChart) }.exceptionOrNull()
        assertTrue(err is ApiError.InvalidResponse)
        assertFalse(store.hasChart(badChart.cachePath))
    }

    @Test
    fun `pruneCharts enforces LRU size cap and sweeps unreferenced charts`() = runTest {
        val store = BriefingCacheStore(tempDir)
        val chunk = ByteArray(1_000) { 7 }
        for (name in listOf("a", "b", "c")) {
            store.writeChart(chunk, cachePath = "dwd/rc/$name.png")
        }

        val oldTime = System.currentTimeMillis() - 3_600_000L
        File(tempDir, "charts/dwd/rc/a.png").setLastModified(oldTime)
        File(tempDir, "charts/dwd/rc/b.png").setLastModified(oldTime)
        // Reading 'a' touches its mtime, leaving 'b' as the least recently used
        assertNotNull(store.readChart("dwd/rc/a.png"))

        val removed = store.enforceChartSizeCap(maxBytes = 2_000L)
        assertEquals(1, removed)
        assertFalse(store.hasChart("dwd/rc/b.png"))
        assertTrue(store.hasChart("dwd/rc/a.png"))
        assertTrue(store.hasChart("dwd/rc/c.png"))
        assertEquals(2_000L, store.chartCacheSize())
    }

    @Test
    fun `pruneStalePacks removes old packs, keeps upcoming packs, and sweeps orphan charts`() = runTest {
        val store = BriefingCacheStore(tempDir)
        val repo = CachingBriefingRepository(online = FixtureBriefingRepository(), cache = store)

        val nowMs = IsoTime.parseEpochMs("2026-07-15T12:00:00Z")!!
        val soonIso = IsoTime.formatUtc(nowMs + 86_400_000L)       // +1 day
        val staleIso = IsoTime.formatUtc(nowMs - 10L * 86_400_000L) // -10 days (> 7 days)

        val keptChart = "dwd/2026-05-08T06Z/048-A"
        val viewedChart = "dwd/2026-05-08T06Z/060-A"
        val orphanChart = "dwd/2026-05-06T06Z/048-A"
        for (key in listOf(keptChart, viewedChart, orphanChart)) {
            store.writeChart(pngBytes(key), cachePath = "$key.png")
        }

        // Upcoming pack f1 (kept)
        store.registerDownload(
            flightId = "f1",
            timestamp = "t1",
            flightTitle = "Upcoming",
            assessment = "green",
            endpoints = CachedPackEntry.requiredEndpoints,
            totalBytes = 100L,
            departureTime = soonIso,
        )
        store.writeData(
            packMetaJsonBytes("f1", "t1", listOf(keptChart)),
            flightId = "f1",
            timestamp = "t1",
            endpoint = CachingBriefingRepository.PACK_META_ENDPOINT,
        )

        // Stale pack f-old (pruned)
        store.registerDownload(
            flightId = "f-old",
            timestamp = "t-old",
            flightTitle = "Stale",
            assessment = "amber",
            endpoints = CachedPackEntry.requiredEndpoints,
            totalBytes = 100L,
            departureTime = staleIso,
        )
        store.writeData(
            packMetaJsonBytes("f-old", "t-old", listOf(orphanChart)),
            flightId = "f-old",
            timestamp = "t-old",
            endpoint = CachingBriefingRepository.PACK_META_ENDPOINT,
        )

        // Viewed-only flight f2 (upcoming -> kept)
        store.writeFlightMetadata(
            packMetaJsonBytes("f2", "t2", listOf(viewedChart)),
            flightId = "f2",
            name = "latest-pack",
        )
        repo.cacheFlightData(
            FlightResponse(
                id = "f2",
                departureTime = soonIso,
                waypoints = listOf("LFMD", "LFML"),
            )
        )

        val prunedCount = repo.pruneStalePacks(olderThanDays = 7, nowEpochMs = nowMs)
        assertEquals(1, prunedCount)
        assertTrue(store.isPackCached("f1", "t1"))
        assertFalse(store.isPackCached("f-old", "t-old"))
        assertTrue(store.hasChart("$keptChart.png"))
        assertTrue(store.hasChart("$viewedChart.png"))
        assertFalse(store.hasChart("$orphanChart.png"))
    }
}
