package aero.flyfun.weather.data

import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.PackMetaResponse
import aero.flyfun.weather.model.WbJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.io.File

/**
 * Metadata for a single downloaded briefing pack tracked in `index.json`.
 */
@Serializable
data class CachedPackEntry(
    val flightId: String,
    val timestamp: String,
    val flightTitle: String,
    val assessment: String? = null,
    val cachedAtEpochMs: Long = System.currentTimeMillis(),
    val endpoints: Set<String> = emptySet(),
    val totalBytes: Long = 0L,
    val departureTime: String? = null,
) {
    val id: String
        get() = "$flightId/$timestamp"

    val isComplete: Boolean
        get() = endpoints.containsAll(requiredEndpoints)

    companion object {
        val requiredEndpoints: Set<String> = setOf(
            "advisories",
            "digest",
            "snapshot",
            "route-analyses",
            "elevation",
        )
    }
}

/**
 * Coroutine-safe disk cache for briefing packs, flight/trip metadata, and shared surface charts.
 * Mirrors `BriefingCacheStore` on iOS.
 *
 * Directory layout under [cacheDir] (typically `baseDir/BriefingCache/users/<scope>/`):
 * - `index.json` — list of [CachedPackEntry] for explicitly downloaded packs
 * - `flights.json`, `trips.json`, `trip-<id>.json` — top-level metadata
 * - `charts/<cacheKey>.<ext>` — shared surface-chart image cache across all packs
 * - `<flightId>/flight.json`, `packs.json`, `latest-pack.json` — flight-level metadata
 * - `<flightId>/<safeTimestamp>/<endpoint>.json` — immutable pack endpoint payloads
 */
typealias BriefingCache = BriefingCacheStore

class BriefingCacheStore(
    val cacheDir: File,
    private val json: Json = WbJson,
) {
    constructor(baseDir: File, scope: String, json: Json = WbJson) : this(
        cacheDir = File(baseDir, "users/${scope.ifBlank { "default" }}"),
        json = json,
    )

    private val mutex = Mutex()
    private var indexCache: MutableMap<String, CachedPackEntry>? = null

    private val indexFile: File
        get() = File(cacheDir, INDEX_FILE_NAME)

    private val chartsDir: File
        get() = File(cacheDir, CHARTS_DIR_NAME)

    // ========================================================================
    // Pack Data Read / Write
    // ========================================================================

    suspend fun writeData(
        bytes: ByteArray,
        flightId: String,
        timestamp: String,
        endpoint: String,
    ) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val dir = packDir(flightId, timestamp)
            dir.mkdirs()
            val file = File(dir, "$endpoint.json")
            file.writeBytes(bytes)

            val index = loadIndexLocked()
            val key = "$flightId/$timestamp"
            val existing = index[key]
            if (existing != null) {
                val updatedEndpoints = existing.endpoints + endpoint
                val updatedSize = directorySize(dir)
                index[key] = existing.copy(
                    endpoints = updatedEndpoints,
                    totalBytes = updatedSize,
                )
                saveIndexLocked(index)
            }
        }
    }

    suspend fun readData(
        flightId: String,
        timestamp: String,
        endpoint: String,
    ): ByteArray? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = File(packDir(flightId, timestamp), "$endpoint.json")
            if (file.isFile) runCatching { file.readBytes() }.getOrNull() else null
        }
    }

    suspend fun hasEndpoint(
        flightId: String,
        timestamp: String,
        endpoint: String,
    ): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            File(packDir(flightId, timestamp), "$endpoint.json").isFile
        }
    }

    // ========================================================================
    // Metadata Read / Write (flight lists, trip lists, pack lists)
    // ========================================================================

    suspend fun writeMetadata(bytes: ByteArray, name: String) = withContext(Dispatchers.IO) {
        mutex.withLock {
            cacheDir.mkdirs()
            File(cacheDir, "$name.json").writeBytes(bytes)
        }
    }

    suspend fun readMetadata(name: String): ByteArray? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = File(cacheDir, "$name.json")
            if (file.isFile) runCatching { file.readBytes() }.getOrNull() else null
        }
    }

    suspend fun writeFlightMetadata(
        bytes: ByteArray,
        flightId: String,
        name: String,
    ) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val dir = File(cacheDir, flightId)
            dir.mkdirs()
            File(dir, "$name.json").writeBytes(bytes)
        }
    }

    suspend fun readFlightMetadata(flightId: String, name: String): ByteArray? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = File(File(cacheDir, flightId), "$name.json")
            if (file.isFile) runCatching { file.readBytes() }.getOrNull() else null
        }
    }

    // ========================================================================
    // Bundle Unpacking & Download Registration
    // ========================================================================

    /**
     * Unpacks a `/bundle` response JSON object into individual `<endpoint>.json` files
     * inside `<flightId>/<safeTimestamp>/` and registers the pack in `index.json`.
     */
    suspend fun unpackBundle(
        bundleBytes: ByteArray,
        flightId: String,
        timestamp: String,
        flightTitle: String = "",
        assessment: String? = null,
        departureTime: String? = null,
        packMeta: PackMetaResponse? = null,
    ): CachedPackEntry = withContext(Dispatchers.IO) {
        val rootObj: JsonObject = try {
            json.parseToJsonElement(bundleBytes.toString(Charsets.UTF_8)).jsonObject
        } catch (e: Exception) {
            throw ApiError.DecodingError(e)
        }

        mutex.withLock {
            val dir = packDir(flightId, timestamp)
            dir.mkdirs()

            val writtenEndpoints = mutableSetOf<String>()
            for ((key, element) in rootObj) {
                if (element is JsonNull) continue
                val endpointBytes = element.toString().toByteArray(Charsets.UTF_8)
                File(dir, "$key.json").writeBytes(endpointBytes)
                writtenEndpoints.add(key)
            }

            if (packMeta != null) {
                val metaBytes = json.encodeToString(PackMetaResponse.serializer(), packMeta)
                    .toByteArray(Charsets.UTF_8)
                File(dir, "$PACK_META_ENDPOINT.json").writeBytes(metaBytes)
            }

            val totalBytes = directorySize(dir)
            val entry = CachedPackEntry(
                flightId = flightId,
                timestamp = timestamp,
                flightTitle = flightTitle,
                assessment = assessment ?: packMeta?.assessment,
                cachedAtEpochMs = System.currentTimeMillis(),
                endpoints = writtenEndpoints,
                totalBytes = totalBytes,
                departureTime = departureTime,
            )
            val index = loadIndexLocked()
            index[entry.id] = entry
            saveIndexLocked(index)
            entry
        }
    }

    suspend fun registerDownload(
        flightId: String,
        timestamp: String,
        flightTitle: String,
        assessment: String?,
        endpoints: Set<String>,
        totalBytes: Long,
        departureTime: String? = null,
    ) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val index = loadIndexLocked()
            val entry = CachedPackEntry(
                flightId = flightId,
                timestamp = timestamp,
                flightTitle = flightTitle,
                assessment = assessment,
                cachedAtEpochMs = System.currentTimeMillis(),
                endpoints = endpoints,
                totalBytes = totalBytes,
                departureTime = departureTime,
            )
            index[entry.id] = entry
            saveIndexLocked(index)
        }
    }

    // ========================================================================
    // Index Queries & Pack Removal
    // ========================================================================

    suspend fun isPackCached(flightId: String, timestamp: String): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            val index = loadIndexLocked()
            index["$flightId/$timestamp"]?.isComplete == true
        }
    }

    suspend fun cachedEntry(flightId: String, timestamp: String): CachedPackEntry? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val index = loadIndexLocked()
            index["$flightId/$timestamp"]
        }
    }

    suspend fun allCachedPacks(): List<CachedPackEntry> = withContext(Dispatchers.IO) {
        mutex.withLock {
            loadIndexLocked().values.sortedByDescending { it.cachedAtEpochMs }
        }
    }

    suspend fun totalCacheSize(): Long = withContext(Dispatchers.IO) {
        mutex.withLock {
            loadIndexLocked().values.sumOf { it.totalBytes } + chartCacheSizeLocked()
        }
    }

    suspend fun removePack(flightId: String, timestamp: String) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val dir = packDir(flightId, timestamp)
            dir.deleteRecursively()
            val index = loadIndexLocked()
            index.remove("$flightId/$timestamp")
            saveIndexLocked(index)
        }
    }

    suspend fun removeFlight(flightId: String) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val dir = File(cacheDir, flightId)
            dir.deleteRecursively()
            val index = loadIndexLocked()
            val toRemove = index.keys.filter { it.startsWith("$flightId/") }
            for (k in toRemove) {
                index.remove(k)
            }
            saveIndexLocked(index)
        }
    }

    suspend fun flightDirectoryIds(): List<String> = withContext(Dispatchers.IO) {
        mutex.withLock {
            flightDirectoryIdsLocked()
        }
    }

    suspend fun packDirectoryNames(flightId: String): List<String> = withContext(Dispatchers.IO) {
        mutex.withLock {
            packDirectoryNamesLocked(flightId)
        }
    }

    suspend fun readPackMetadataBySafeDir(
        flightId: String,
        safePackDir: String,
        endpoint: String,
    ): ByteArray? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = File(File(File(cacheDir, flightId), safePackDir), "$endpoint.json")
            if (file.isFile) runCatching { file.readBytes() }.getOrNull() else null
        }
    }

    suspend fun clearAll() = withContext(Dispatchers.IO) {
        mutex.withLock {
            cacheDir.deleteRecursively()
            indexCache = mutableMapOf()
        }
    }

    // ========================================================================
    // Shared Surface-Chart Cache
    // ========================================================================

    private fun chartFileLocked(cachePath: String): File {
        if (cachePath.isEmpty() || cachePath.startsWith("/") || cachePath.contains('\\')) {
            throw IllegalArgumentException("Invalid chart cache path: $cachePath")
        }
        val segments = cachePath.split('/')
        if (segments.any { it.isEmpty() || it == "." || it == ".." }) {
            throw IllegalArgumentException("Invalid chart cache path: $cachePath")
        }
        val root = chartsDir.canonicalFile
        val resolved = File(root, segments.joinToString(File.separator)).canonicalFile
        val rootPathWithSep = root.path + File.separator
        if (!resolved.path.startsWith(rootPathWithSep)) {
            throw IllegalArgumentException("Chart cache path escapes charts directory: $cachePath")
        }
        return resolved
    }

    suspend fun writeChart(bytes: ByteArray, cachePath: String) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = chartFileLocked(cachePath)
            file.parentFile?.mkdirs()
            file.writeBytes(bytes)
        }
    }

    suspend fun readChart(cachePath: String): ByteArray? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = runCatching { chartFileLocked(cachePath) }.getOrNull() ?: return@withLock null
            if (!file.isFile) return@withLock null
            val bytes = runCatching { file.readBytes() }.getOrNull() ?: return@withLock null
            // Touch modification time for LRU eviction
            runCatching { file.setLastModified(System.currentTimeMillis()) }
            bytes
        }
    }

    suspend fun hasChart(cachePath: String): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            val file = runCatching { chartFileLocked(cachePath) }.getOrNull() ?: return@withLock false
            file.isFile
        }
    }

    suspend fun chartCacheSize(): Long = withContext(Dispatchers.IO) {
        mutex.withLock {
            chartCacheSizeLocked()
        }
    }

    private fun chartCacheSizeLocked(): Long {
        if (!chartsDir.exists()) return 0L
        return directorySize(chartsDir)
    }

    suspend fun pruneCharts(
        keeping: Set<String>?,
        maxBytes: Long = DEFAULT_CHART_CACHE_MAX_BYTES,
    ) = withContext(Dispatchers.IO) {
        mutex.withLock {
            pruneChartsLocked(keeping, maxBytes)
        }
    }

    private fun pruneChartsLocked(
        keeping: Set<String>?,
        maxBytes: Long = DEFAULT_CHART_CACHE_MAX_BYTES,
    ) {
        if (keeping != null) {
            sweepUnreferencedChartsLocked(keeping)
        }
        enforceChartSizeCapLocked(maxBytes)
    }

    suspend fun sweepUnreferencedCharts(keeping: Set<String>): Int = withContext(Dispatchers.IO) {
        mutex.withLock {
            sweepUnreferencedChartsLocked(keeping)
        }
    }

    private fun sweepUnreferencedChartsLocked(keeping: Set<String>): Int {
        if (!chartsDir.exists()) return 0
        val root = chartsDir.canonicalFile
        val rootPrefix = root.path + File.separator
        val files = chartsDir.walkTopDown().filter { it.isFile }.toList()
        var removed = 0
        for (file in files) {
            val canonical = file.canonicalFile
            if (!canonical.path.startsWith(rootPrefix)) continue
            val rel = canonical.path.removePrefix(rootPrefix).replace(File.separatorChar, '/')
            if (rel !in keeping) {
                if (file.delete()) removed++
            }
        }
        removeEmptyDirectoriesLocked(chartsDir)
        return removed
    }

    suspend fun enforceChartSizeCap(
        maxBytes: Long = DEFAULT_CHART_CACHE_MAX_BYTES,
    ): Int = withContext(Dispatchers.IO) {
        mutex.withLock {
            enforceChartSizeCapLocked(maxBytes)
        }
    }

    private fun enforceChartSizeCapLocked(maxBytes: Long): Int {
        if (!chartsDir.exists()) return 0
        data class ChartFileEntry(val file: File, val mtime: Long, val size: Long)

        val entries = chartsDir.walkTopDown()
            .filter { it.isFile }
            .map { ChartFileEntry(it, it.lastModified(), it.length()) }
            .sortedBy { it.mtime } // oldest first
            .toList()

        var total = entries.sumOf { it.size }
        if (total <= maxBytes) return 0

        var removed = 0
        for (entry in entries) {
            if (total <= maxBytes) break
            if (entry.file.delete()) {
                total -= entry.size
                removed++
            }
        }
        removeEmptyDirectoriesLocked(chartsDir)
        return removed
    }

    // ========================================================================
    // Stale Pack & Chart Pruning
    // ========================================================================

    /**
     * Removes cached packs and viewed-only flight directories whose flight departure time
     * is older than [olderThanDays] before [nowEpochMs], then sweeps unreferenced surface charts.
     *
     * Returns the number of indexed packs removed.
     */
    suspend fun pruneStalePacks(
        olderThanDays: Int = 7,
        nowEpochMs: Long = System.currentTimeMillis(),
    ): Int = withContext(Dispatchers.IO) {
        mutex.withLock {
            val cutoffMs = nowEpochMs - olderThanDays.toLong() * 86_400_000L
            val index = loadIndexLocked()
            val entries = index.values.toList()

            val flightDepartures = mutableMapOf<String, Long>()
            for (entry in entries) {
                if (entry.flightId !in flightDepartures) {
                    val depMs = IsoTime.parseEpochMs(entry.departureTime)
                        ?: readFlightDepartureEpochMsLocked(entry.flightId)
                    if (depMs != null) {
                        flightDepartures[entry.flightId] = depMs
                    }
                }
            }

            var removedCount = 0
            val prunedFlights = mutableSetOf<String>()
            for (entry in entries) {
                val depMs = flightDepartures[entry.flightId] ?: continue
                if (depMs < cutoffMs) {
                    packDir(entry.flightId, entry.timestamp).deleteRecursively()
                    index.remove(entry.id)
                    prunedFlights.add(entry.flightId)
                    removedCount++
                }
            }
            if (removedCount > 0) {
                saveIndexLocked(index)
            }

            // If a pruned flight has no remaining cached packs, remove its flight directory
            val remainingFlightIds = index.values.mapTo(mutableSetOf()) { it.flightId }
            for (flightId in prunedFlights) {
                if (flightId !in remainingFlightIds) {
                    File(cacheDir, flightId).deleteRecursively()
                }
            }

            // Sweep viewed-only flight directories on the same departure cutoff
            for (flightId in flightDirectoryIdsLocked()) {
                if (flightId in remainingFlightIds) continue
                val depMs = readFlightDepartureEpochMsLocked(flightId)
                if (depMs != null && depMs < cutoffMs) {
                    File(cacheDir, flightId).deleteRecursively()
                }
            }

            // Mark-and-sweep surface charts + enforce size cap
            val referenced = collectReferencedChartPathsLocked()
            pruneChartsLocked(keeping = referenced)

            removedCount
        }
    }

    private fun readFlightDepartureEpochMsLocked(flightId: String): Long? {
        val file = File(File(cacheDir, flightId), "flight.json")
        if (!file.isFile) return null
        val flight = runCatching {
            json.decodeFromString(FlightResponse.serializer(), file.readText(Charsets.UTF_8))
        }.getOrNull() ?: return null
        return flight.departureEpochMs
    }

    /**
     * Walks every surviving flight directory and collects all `chart.cachePath` strings
     * referenced by `pack-meta.json`, `latest-pack.json`, or `packs.json`.
     * Returns `null` if any sidecar fails to decode as [PackMetaResponse] (so the caller
     * skips the mark-and-sweep rather than deleting charts that an undecodable manifest might need).
     */
    private fun collectReferencedChartPathsLocked(): Set<String>? {
        val keep = mutableSetOf<String>()
        for (flightId in flightDirectoryIdsLocked()) {
            val flightDir = File(cacheDir, flightId)
            for (packDirName in packDirectoryNamesLocked(flightId)) {
                val metaFile = File(File(flightDir, packDirName), "$PACK_META_ENDPOINT.json")
                if (metaFile.isFile) {
                    val pack = decodeStrictPackMetaLocked(metaFile) ?: return null
                    pack.charts?.sources?.forEach { src ->
                        src.charts.forEach { keep.add(it.cachePath) }
                    }
                }
            }
            val latestFile = File(flightDir, "latest-pack.json")
            if (latestFile.isFile) {
                val latest = decodeStrictPackMetaLocked(latestFile) ?: return null
                latest.charts?.sources?.forEach { src ->
                    src.charts.forEach { keep.add(it.cachePath) }
                }
            }
            val packsFile = File(flightDir, "packs.json")
            if (packsFile.isFile) {
                val packs = runCatching {
                    val list = json.decodeFromString(
                        ListSerializer(PackMetaResponse.serializer()),
                        packsFile.readText(Charsets.UTF_8),
                    )
                    if (list.any { it.flightId.isEmpty() || it.fetchTimestamp.isEmpty() }) null else list
                }.getOrNull() ?: return null
                for (p in packs) {
                    p.charts?.sources?.forEach { src ->
                        src.charts.forEach { keep.add(it.cachePath) }
                    }
                }
            }
        }
        return keep
    }

    private fun decodeStrictPackMetaLocked(file: File): PackMetaResponse? {
        val text = runCatching { file.readText(Charsets.UTF_8) }.getOrNull() ?: return null
        val obj = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return null
        if ("flight_id" !in obj || "fetch_timestamp" !in obj) return null
        return runCatching { json.decodeFromString(PackMetaResponse.serializer(), text) }.getOrNull()
    }

    // ========================================================================
    // Internal Directory & Index Helpers
    // ========================================================================

    private fun packDir(flightId: String, timestamp: String): File =
        File(File(cacheDir, flightId), safeTimestamp(timestamp))

    private fun flightDirectoryIdsLocked(): List<String> {
        val children = cacheDir.listFiles() ?: return emptyList()
        return children
            .filter { it.isDirectory && it.name != CHARTS_DIR_NAME && !it.name.startsWith(".") }
            .map { it.name }
    }

    private fun packDirectoryNamesLocked(flightId: String): List<String> {
        val flightDir = File(cacheDir, flightId)
        val children = flightDir.listFiles() ?: return emptyList()
        return children
            .filter { it.isDirectory && !it.name.startsWith(".") }
            .map { it.name }
    }

    private fun loadIndexLocked(): MutableMap<String, CachedPackEntry> {
        indexCache?.let { return it }
        if (!indexFile.isFile) {
            val empty = mutableMapOf<String, CachedPackEntry>()
            indexCache = empty
            return empty
        }
        val entries = runCatching {
            json.decodeFromString(
                ListSerializer(CachedPackEntry.serializer()),
                indexFile.readText(Charsets.UTF_8),
            )
        }.getOrDefault(emptyList())
        val map = entries.associateByTo(LinkedHashMap()) { it.id }
        indexCache = map
        return map
    }

    private fun saveIndexLocked(index: MutableMap<String, CachedPackEntry>) {
        indexCache = index
        cacheDir.mkdirs()
        val list = index.values.toList()
        val text = json.encodeToString(ListSerializer(CachedPackEntry.serializer()), list)
        indexFile.writeText(text, Charsets.UTF_8)
    }

    private fun directorySize(dir: File): Long {
        if (!dir.exists()) return 0L
        return dir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
    }

    private fun removeEmptyDirectoriesLocked(root: File) {
        val dirs = root.walkBottomUp().filter { it.isDirectory && it != root }.toList()
        for (dir in dirs) {
            val children = dir.listFiles()
            if (children != null && children.isEmpty()) {
                dir.delete()
            }
        }
    }

    companion object {
        const val ROOT_DIR_NAME = "BriefingCache"
        const val USERS_DIR_NAME = "users"
        const val CHARTS_DIR_NAME = "charts"
        const val INDEX_FILE_NAME = "index.json"
        const val PACK_META_ENDPOINT = "pack-meta"
        const val DEFAULT_CHART_CACHE_MAX_BYTES: Long = 50L * 1024L * 1024L

        fun safeTimestamp(timestamp: String): String =
            timestamp.replace(":", "-")

        /**
         * Per-user cache root: `<baseDir>/BriefingCache/users/<scope>`.
         */
        fun scopedCacheDir(baseDir: File, scope: String): File =
            File(File(File(baseDir, ROOT_DIR_NAME), USERS_DIR_NAME), scope)

        /**
         * One-shot migration from the pre-scoping flat layout (`<baseDir>/BriefingCache/<flightId|index.json|...>`).
         * Deletes everything directly under `<baseDir>/BriefingCache/` except `users/`.
         * Idempotent.
         */
        fun migrateLegacyLayout(baseDir: File) {
            val root = File(baseDir, ROOT_DIR_NAME)
            val entries = root.listFiles() ?: return
            for (entry in entries) {
                if (entry.name == USERS_DIR_NAME) continue
                entry.deleteRecursively()
            }
        }

        /**
         * Cross-user janitor: removes `<baseDir>/BriefingCache/users/<scope>` directories
         * (other than [keepingScope]) whose last activity is older than [olderThanDays].
         */
        fun evictInactiveUsers(
            baseDir: File,
            keepingScope: String,
            olderThanDays: Int = 30,
            nowEpochMs: Long = System.currentTimeMillis(),
        ) {
            val usersDir = File(File(baseDir, ROOT_DIR_NAME), USERS_DIR_NAME)
            val entries = usersDir.listFiles() ?: return
            val cutoffMs = nowEpochMs - olderThanDays.toLong() * 86_400_000L

            for (dir in entries) {
                if (!dir.isDirectory) continue
                if (dir.name == keepingScope) continue
                val flightsMarker = File(dir, "flights.json")
                val indexMarker = File(dir, INDEX_FILE_NAME)
                val lastActiveMs = when {
                    flightsMarker.isFile -> flightsMarker.lastModified()
                    indexMarker.isFile -> indexMarker.lastModified()
                    else -> dir.lastModified()
                }
                if (lastActiveMs in 1 until cutoffMs) {
                    dir.deleteRecursively()
                }
            }
        }
    }
}
