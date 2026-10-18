package aero.flyfun.weather.data

import android.content.Context
import aero.flyfun.rzflight.Airport
import aero.flyfun.rzflight.Coord
import aero.flyfun.rzflight.KnownAirports
import aero.flyfun.weather.model.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File

/**
 * Manages the local `airports.db` SQLite database downloaded from `/api/airports/db`,
 * verifying ETag for incremental updates and exposing [KnownAirports] from `:rzflight`.
 * Mirrors `AirportDatabase` on iOS.
 */
class AirportDatabaseService(
    context: Context? = null,
    private val filesDir: File = context?.filesDir ?: File(System.getProperty("java.io.tmpdir"), "flyfun-airports"),
) {
    private val mutex = Mutex()

    private val _knownAirports = MutableStateFlow<KnownAirports?>(null)
    val knownAirports: StateFlow<KnownAirports?> = _knownAirports.asStateFlow()

    val isReady: Boolean
        get() = _knownAirports.value != null

    val dbFile: File
        get() = File(filesDir, DB_FILE_NAME)

    private val etagFile: File
        get() = File(filesDir, ETAG_FILE_NAME)

    /**
     * Opens the cached `airports.db` immediately if present on disk, then performs an
     * ETag-conditional `GET /api/airports/db` check against [api] to download updates.
     */
    suspend fun ensureLoaded(api: ApiClient) = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (_knownAirports.value == null && dbFile.isFile && dbFile.length() > 0L) {
                runCatching { KnownAirports(dbFile) }.onSuccess { _knownAirports.value = it }
            }
        }
        refreshFromServer(api)
    }

    /**
     * Loads [KnownAirports] directly from an existing [file] (useful for tests or offline startup).
     */
    suspend fun loadFromFile(file: File = dbFile): KnownAirports? = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (!file.isFile || file.length() == 0L) return@withLock null
            val loaded = KnownAirports(file)
            _knownAirports.value = loaded
            loaded
        }
    }

    /**
     * Performs an ETag-conditional download of `/api/airports/db`.
     */
    suspend fun refreshFromServer(api: ApiClient): Boolean = withContext(Dispatchers.IO) {
        val url = api.buildUrl("/api/airports/db")
        val reqBuilder = Request.Builder().url(url).get()
        val storedEtag = if (etagFile.isFile) {
            runCatching { etagFile.readText(Charsets.UTF_8).trim() }.getOrNull()
        } else {
            null
        }
        if (!storedEtag.isNullOrEmpty() && dbFile.isFile) {
            reqBuilder.header("If-None-Match", storedEtag)
        }

        val response = runCatching { api.httpClient.newCall(reqBuilder.build()).execute() }.getOrNull()
            ?: return@withContext false

        response.use { resp ->
            if (resp.code == 304) {
                return@withContext false
            }
            if (!resp.isSuccessful) {
                return@withContext false
            }
            val bytes = runCatching { resp.body?.bytes() }.getOrNull()
            if (bytes == null || bytes.size < 16) {
                return@withContext false
            }
            // Verify SQLite magic header ("SQLite format 3\u0000")
            val header = bytes.take(15).toByteArray().toString(Charsets.US_ASCII)
            if (!header.startsWith("SQLite format 3")) {
                return@withContext false
            }

            mutex.withLock {
                filesDir.mkdirs()
                val tmpFile = File(filesDir, "$DB_FILE_NAME.tmp")
                tmpFile.writeBytes(bytes)
                if (!tmpFile.renameTo(dbFile)) {
                    dbFile.writeBytes(bytes)
                    tmpFile.delete()
                }
                resp.header("ETag")?.takeIf { it.isNotBlank() }?.let { newEtag ->
                    runCatching { etagFile.writeText(newEtag, Charsets.UTF_8) }
                }
                runCatching { KnownAirports(dbFile) }.onSuccess {
                    _knownAirports.value = it
                }
            }
            true
        }
    }

    /**
     * Look up a single airport by ICAO code.
     */
    fun lookup(icao: String): Airport? =
        _knownAirports.value?.airport(icao)

    /**
     * Ranked prefix + substring search across ICAO, name, and municipality.
     */
    fun search(query: String, limit: Int = 12): List<Airport> =
        _knownAirports.value?.rankedSearch(query, limit) ?: emptyList()

    /**
     * Resolves a list of ICAO codes to their coordinates, skipping unknown airports.
     */
    fun resolveCoordinates(icaos: List<String>): List<Coord> =
        icaos.mapNotNull { lookup(it)?.coord }

    /**
     * Resolves a list of ICAO codes into [Waypoint] models.
     */
    fun resolveWaypoints(icaos: List<String>): List<Waypoint> =
        icaos.mapNotNull { code ->
            val apt = lookup(code) ?: return@mapNotNull null
            Waypoint(
                icao = apt.icao,
                name = apt.name,
                lat = apt.latitude,
                lon = apt.longitude,
            )
        }

    /**
     * Builds a [RunwayWindPicture] combining runway ends from [summary] (or airport DB)
     * with model and observation [WindSample]s.
     */
    fun buildRunwayWindPicture(
        summary: AirportConditionsSummary,
        observation: AirportObservation? = null,
        selectedModel: String? = null,
    ): RunwayWindPicture {
        val ends = summary.runwayEnds.map { RunwayEndInfo(id = it.id, headingTrue = it.headingDeg) }
        val runways = buildRunwayPairs(ends)

        val samples = mutableListOf<WindSample>()
        if (observation != null) {
            if (observation.hasMetar && observation.metarWindDir != null && observation.metarWindSpeedKt != null) {
                samples.add(
                    WindSample(
                        source = "METAR",
                        directionDeg = observation.metarWindDir.toDouble(),
                        speedKt = observation.metarWindSpeedKt.toDouble(),
                        gustKt = observation.metarWindGustKt?.toDouble(),
                    )
                )
            }
            if (observation.hasTaf && observation.tafWindDir != null && observation.tafWindSpeedKt != null) {
                samples.add(
                    WindSample(
                        source = "TAF",
                        directionDeg = observation.tafWindDir.toDouble(),
                        speedKt = observation.tafWindSpeedKt.toDouble(),
                        gustKt = observation.tafWindGustKt?.toDouble(),
                    )
                )
            }
        }
        for (cond in summary.conditions) {
            val dir = cond.windDirectionDeg
            val spd = cond.windSpeedKt
            if (dir != null && spd != null) {
                samples.add(
                    WindSample(
                        source = cond.model,
                        directionDeg = dir,
                        speedKt = spd,
                        gustKt = cond.windGustKt,
                    )
                )
            }
        }

        val winds = samples.map { WindAtAirport.evaluate(it, ends) }
        val primary = when {
            winds.any { it.sample.source == "METAR" } -> "METAR"
            selectedModel != null && winds.any { it.sample.source == selectedModel } -> selectedModel
            else -> winds.firstOrNull()?.sample?.source
        }

        val resolvedName = summary.name.ifBlank { lookup(summary.icao)?.name.orEmpty() }
        return RunwayWindPicture(
            icao = summary.icao,
            name = resolvedName,
            runways = runways,
            ends = ends,
            winds = winds,
            primarySource = primary,
        )
    }

    private fun buildRunwayPairs(ends: List<RunwayEndInfo>): List<RunwayInfo> {
        if (ends.isEmpty()) return emptyList()
        val used = BooleanArray(ends.size)
        val result = mutableListOf<RunwayInfo>()
        for (i in ends.indices) {
            if (used[i]) continue
            used[i] = true
            val e1 = ends[i]
            var pairIdx = -1
            for (j in i + 1 until ends.size) {
                if (used[j]) continue
                val diff = kotlin.math.abs(kotlin.math.abs(e1.headingTrue - ends[j].headingTrue) - 180.0)
                if (diff < 25.0) {
                    pairIdx = j
                    break
                }
            }
            val e2 = if (pairIdx >= 0) {
                used[pairIdx] = true
                ends[pairIdx]
            } else {
                RunwayEndInfo(
                    id = "",
                    headingTrue = (e1.headingTrue + 180.0) % 360.0,
                )
            }
            val id = if (e2.id.isNotEmpty()) "${e1.id}/${e2.id}" else e1.id
            result.add(
                RunwayInfo(
                    id = id,
                    lengthFt = 0,
                    widthFt = 0,
                    surface = "",
                    end1 = e1,
                    end2 = e2,
                )
            )
        }
        return result
    }

    companion object {
        const val DB_FILE_NAME = "airports.db"
        const val ETAG_FILE_NAME = "airports.db.etag"
    }
}
