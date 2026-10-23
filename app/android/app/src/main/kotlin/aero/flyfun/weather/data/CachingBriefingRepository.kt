package aero.flyfun.weather.data

import aero.flyfun.weather.model.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.io.IOException

/**
 * Decorator around [BriefingRepository] that transparently caches briefing packs,
 * flight metadata, and surface charts on disk via [BriefingCacheStore].
 * Mirrors `CachingBriefingRepository` on iOS.
 */
class CachingBriefingRepository(
    val online: BriefingRepository,
    val cache: BriefingCacheStore,
    val isOnline: () -> Boolean = { true },
    val json: Json = WbJson,
) : BriefingRepository {

    private val _lastFetchWasCached = MutableStateFlow(false)
    val lastFetchWasCachedFlow: StateFlow<Boolean> = _lastFetchWasCached.asStateFlow()

    var lastFetchWasCached: Boolean
        get() = _lastFetchWasCached.value
        private set(value) {
            _lastFetchWasCached.value = value
        }

    private val _downloadProgress = MutableStateFlow<Map<String, Double>>(emptyMap())
    val downloadProgress: StateFlow<Map<String, Double>> = _downloadProgress.asStateFlow()

    // ========================================================================
    // Flights (mutable — network-first with cache fallback)
    // ========================================================================

    override suspend fun listFlights(): List<FlightResponse> {
        if (isOnline()) {
            try {
                val flights = online.listFlights()
                cacheFlightList(flights)
                lastFetchWasCached = false
                return flights
            } catch (e: Throwable) {
                if (!isTransientNetworkFailure(e)) throw e
                readCachedFlightList()?.let {
                    lastFetchWasCached = true
                    return it
                }
                throw e
            }
        } else {
            readCachedFlightList()?.let {
                lastFetchWasCached = true
                return it
            }
            throw ApiError.OfflineNotCached()
        }
    }

    override suspend fun getFlight(id: String): FlightResponse {
        if (isOnline()) {
            try {
                val flight = online.getFlight(id)
                cacheFlightData(flight)
                lastFetchWasCached = false
                return flight
            } catch (e: Throwable) {
                if (!isTransientNetworkFailure(e)) throw e
                readCachedFlight(id)?.let {
                    lastFetchWasCached = true
                    return it
                }
                throw e
            }
        } else {
            readCachedFlight(id)?.let {
                lastFetchWasCached = true
                return it
            }
            throw ApiError.OfflineNotCached()
        }
    }

    override suspend fun createFlight(req: CreateFlightRequest): FlightResponse =
        online.createFlight(req)

    override suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse =
        online.updateFlight(id, req)

    override suspend fun deleteFlight(id: String) {
        online.deleteFlight(id)
        cache.removeFlight(id)
    }

    override suspend fun bulkDeleteFlights(ids: List<String>): BulkDeleteResponse {
        val result = online.bulkDeleteFlights(ids)
        for (id in result.deleted) {
            cache.removeFlight(id)
        }
        return result
    }

    override suspend fun moveFlight(id: String, req: MoveFlightRequest): FlightResponse {
        val flight = online.moveFlight(id, req)
        // Move deletes the source flight and its packs server-side.
        cache.removeFlight(id)
        cacheFlightData(flight)
        return flight
    }

    override suspend fun parseFpl(fplText: String): ParseFplResponse =
        online.parseFpl(fplText)

    override suspend fun interpretRoute(rawRoute: String): InterpretRouteResponse =
        online.interpretRoute(rawRoute)

    override suspend fun routeDistance(waypoints: List<String>): RouteDistanceResponse =
        online.routeDistance(waypoints)

    override suspend fun fetchAutorouterRoutes(
        departure: String,
        destination: String,
        departureTime: String,
        cruiseAltitudeFt: Int,
        durationHours: Double,
        aircraftId: Int?,
    ): AutorouterRoutesResponse = online.fetchAutorouterRoutes(
        departure = departure,
        destination = destination,
        departureTime = departureTime,
        cruiseAltitudeFt = cruiseAltitudeFt,
        durationHours = durationHours,
        aircraftId = aircraftId,
    )

    // ========================================================================
    // Pack Metadata (network-first with cache fallback)
    // ========================================================================

    override suspend fun listPacks(flightId: String): List<PackMetaResponse> {
        if (isOnline()) {
            try {
                val packs = online.listPacks(flightId)
                val bytes = json.encodeToString(ListSerializer(PackMetaResponse.serializer()), packs)
                    .toByteArray(Charsets.UTF_8)
                cache.writeFlightMetadata(bytes, flightId = flightId, name = "packs")
                lastFetchWasCached = false
                return packs
            } catch (e: Throwable) {
                if (!isTransientNetworkFailure(e)) throw e
                readCachedPacks(flightId)?.let {
                    lastFetchWasCached = true
                    return it
                }
                throw e
            }
        } else {
            readCachedPacks(flightId)?.let {
                lastFetchWasCached = true
                return it
            }
            throw ApiError.OfflineNotCached()
        }
    }

    override suspend fun latestPack(flightId: String): PackMetaResponse {
        if (isOnline()) {
            try {
                val pack = online.latestPack(flightId)
                cachePackMeta(pack, flightId = flightId)
                lastFetchWasCached = false
                return pack
            } catch (e: Throwable) {
                if (!isTransientNetworkFailure(e)) throw e
                readCachedLatestPack(flightId)?.let {
                    lastFetchWasCached = true
                    return it
                }
                throw e
            }
        } else {
            readCachedLatestPack(flightId)?.let {
                lastFetchWasCached = true
                return it
            }
            throw ApiError.OfflineNotCached()
        }
    }

    override suspend fun freshness(flightId: String): DataStatus =
        online.freshness(flightId)

    // ========================================================================
    // Pack Data (immutable per flightId + timestamp)
    // ========================================================================

    override suspend fun advisories(flightId: String, timestamp: String): AdvisoriesResponse =
        cachedFetch(
            serializer = AdvisoriesResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "advisories",
        ) { online.advisories(flightId, timestamp) }

    override suspend fun altAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        cachedFetch(
            serializer = AdvisoriesResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "alt-advisories",
        ) { online.altAdvisories(flightId, timestamp) }

    override suspend fun recalculateAdvisories(flightId: String, timestamp: String): AdvisoriesResponse {
        val result = online.recalculateAdvisories(flightId, timestamp)
        val bytes = json.encodeToString(AdvisoriesResponse.serializer(), result).toByteArray(Charsets.UTF_8)
        cache.writeData(bytes, flightId = flightId, timestamp = timestamp, endpoint = "advisories")
        return result
    }

    override suspend fun recalculateAltAdvisories(flightId: String, timestamp: String): AdvisoriesResponse {
        val result = online.recalculateAltAdvisories(flightId, timestamp)
        val bytes = json.encodeToString(AdvisoriesResponse.serializer(), result).toByteArray(Charsets.UTF_8)
        cache.writeData(bytes, flightId = flightId, timestamp = timestamp, endpoint = "alt-advisories")
        return result
    }

    override suspend fun advisoryDetail(
        flightId: String,
        timestamp: String,
        advisoryId: String,
        alt: Boolean,
    ): AdvisoryDetailResponse =
        online.advisoryDetail(flightId, timestamp, advisoryId, alt)

    override suspend fun alternates(flightId: String, timestamp: String): AlternatesResponse =
        cachedFetch(
            serializer = AlternatesResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "alternates",
        ) { online.alternates(flightId, timestamp) }

    override suspend fun digest(flightId: String, timestamp: String): DigestResponse =
        cachedFetch(
            serializer = DigestResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "digest",
        ) { online.digest(flightId, timestamp) }

    override suspend fun snapshot(flightId: String, timestamp: String): SnapshotResponse =
        cachedFetch(
            serializer = SnapshotResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "snapshot",
        ) { online.snapshot(flightId, timestamp) }

    override suspend fun routeAnalyses(flightId: String, timestamp: String): RouteAnalysesResponse =
        cachedFetch(
            serializer = RouteAnalysesResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "route-analyses",
        ) { online.routeAnalyses(flightId, timestamp) }

    override suspend fun elevation(flightId: String, timestamp: String): ElevationResponse =
        cachedFetch(
            serializer = ElevationResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "elevation",
        ) { online.elevation(flightId, timestamp) }

    override suspend fun soundingProfile(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): SoundingProfileResponse =
        cachedFetch(
            serializer = SoundingProfileResponse.serializer(),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "sounding-$pointIndex-$model",
        ) { online.soundingProfile(flightId, timestamp, pointIndex, model) }

    override suspend fun gramet(flightId: String, timestamp: String): ByteArray =
        cachedRawFetch(
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "gramet",
        ) { online.gramet(flightId, timestamp) }

    override suspend fun skewT(
        flightId: String,
        timestamp: String,
        icao: String,
        model: String,
    ): ByteArray =
        cachedRawFetch(
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "skewt-$icao-$model",
        ) { online.skewT(flightId, timestamp, icao, model) }

    override suspend fun routeSkewT(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): ByteArray =
        cachedRawFetch(
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "skewt-route-$pointIndex-$model",
        ) { online.routeSkewT(flightId, timestamp, pointIndex, model) }

    override suspend fun chartImage(chart: PackChart): ByteArray {
        val key = chart.cachePath
        cache.readChart(key)?.let { cached ->
            lastFetchWasCached = !isOnline()
            return cached
        }
        if (!isOnline()) {
            throw ApiError.OfflineNotCached()
        }
        val bytes = online.chartImage(chart)
        if (!PackChart.looksLikeImage(bytes)) {
            throw ApiError.InvalidResponse("Chart response is not a valid image")
        }
        runCatching { cache.writeChart(bytes, cachePath = key) }
        lastFetchWasCached = false
        return bytes
    }

    override suspend fun chartOverlay(
        urlPath: String,
        flightId: String,
        timestamp: String,
        source: String,
    ): ChartOverlay =
        cachedFetch(
            serializer = MapSerializer(String.serializer(), ChartOverlayEntry.serializer()),
            flightId = flightId,
            timestamp = timestamp,
            endpoint = "chart-overlay-$source",
        ) { online.chartOverlay(urlPath, flightId, timestamp, source) }

    override suspend fun liveLayer(flightId: String): LiveLayerResponse =
        online.liveLayer(flightId)

    // ========================================================================
    // Refresh, Time Options, Observed, Debrief, PIREPs, Shared (online-only)
    // ========================================================================

    override suspend fun triggerRefresh(flightId: String, force: Boolean): RefreshStatusResponse =
        online.triggerRefresh(flightId, force)

    override suspend fun refreshStatus(flightId: String): ActiveRefreshResponse =
        online.refreshStatus(flightId)

    override suspend fun activeRefreshes(): Map<String, ActiveRefreshResponse> =
        online.activeRefreshes()

    override fun refreshPack(flightId: String, force: Boolean, notifyEmail: Boolean): Flow<RefreshEvent> =
        online.refreshPack(flightId, force, notifyEmail)

    override suspend fun timeOptions(flightId: String): TimeOptionsResponse =
        online.timeOptions(flightId)

    override suspend fun triggerTimeScan(flightId: String): TimeScanStatusDTO =
        online.triggerTimeScan(flightId)

    override suspend fun confirmTimeOption(flightId: String, req: ConfirmTimeOptionRequest): FlightResponse =
        online.confirmTimeOption(flightId, req)

    override suspend fun observedStatus(): ObservedImageryStatusResponse =
        online.observedStatus()

    override suspend fun observedFrames(layer: String, bbox: String?): ObservedFramesResponse =
        online.observedFrames(layer, bbox)

    override suspend fun observedFlashes(bbox: String, validTime: String?): ObservedFlashesResponse =
        online.observedFlashes(bbox, validTime)

    override suspend fun cellFrames(bbox: String): CellFramesResponse =
        online.cellFrames(bbox)

    override suspend fun observedCells(bbox: String, validTime: String?): CellDisplay =
        online.observedCells(bbox, validTime)

    override suspend fun debrief(flightId: String): DebriefResponse =
        online.debrief(flightId)

    override suspend fun saveDebrief(flightId: String, req: DebriefRequest): DebriefResponse =
        online.saveDebrief(flightId, req)

    override suspend fun deleteDebrief(flightId: String) =
        online.deleteDebrief(flightId)

    override suspend fun submitPirep(req: SubmitPirepRequest): PirepResponse =
        online.submitPirep(req)

    override suspend fun listPireps(flightId: String): PirepListResponse =
        online.listPireps(flightId)

    override suspend fun resolveSharedFlight(code: String): FlightResponse =
        online.resolveSharedFlight(code)

    override suspend fun subscribeToSharedFlight(code: String): FlightResponse =
        online.subscribeToSharedFlight(code)

    override suspend fun unsubscribeFromFlight(flightId: String) =
        online.unsubscribeFromFlight(flightId)

    override suspend fun resolveSharedTrip(code: String): TripResponse =
        online.resolveSharedTrip(code)

    override suspend fun subscribeToSharedTrip(code: String): TripSubscribeResponse =
        online.subscribeToSharedTrip(code)

    override suspend fun unsubscribeFromTrip(tripId: String) =
        online.unsubscribeFromTrip(tripId)

    // ========================================================================
    // Explicit Pack Download & Pruning
    // ========================================================================

    suspend fun downloadPack(
        flightId: String,
        timestamp: String,
        flightTitle: String,
        assessment: String?,
        includeGramet: Boolean = false,
        departureTime: String? = null,
        packMeta: PackMetaResponse? = null,
    ): CachedPackEntry {
        val key = "$flightId/$timestamp"
        _downloadProgress.value = _downloadProgress.value + (key to 0.0)
        try {
            val resolvedMeta = packMeta ?: runCatching { latestPack(flightId) }.getOrNull()
            val entry = if (online is OnlineBriefingRepository) {
                val bundleBytes = online.api.getBundle(flightId, timestamp)
                _downloadProgress.value = _downloadProgress.value + (key to 0.7)
                cache.unpackBundle(
                    bundleBytes = bundleBytes,
                    flightId = flightId,
                    timestamp = timestamp,
                    flightTitle = flightTitle,
                    assessment = assessment,
                    departureTime = departureTime,
                    packMeta = resolvedMeta,
                )
            } else {
                advisories(flightId, timestamp)
                digest(flightId, timestamp)
                snapshot(flightId, timestamp)
                routeAnalyses(flightId, timestamp)
                elevation(flightId, timestamp)
                if (resolvedMeta != null) {
                    cachePackMeta(resolvedMeta, flightId)
                }
                cache.registerDownload(
                    flightId = flightId,
                    timestamp = timestamp,
                    flightTitle = flightTitle,
                    assessment = assessment,
                    endpoints = CachedPackEntry.requiredEndpoints,
                    totalBytes = 0L,
                    departureTime = departureTime,
                )
                cache.cachedEntry(flightId, timestamp)!!
            }

            if (includeGramet) {
                runCatching { gramet(flightId, timestamp) }
            }
            if (resolvedMeta != null) {
                prefetchPackCharts(resolvedMeta, flightId, timestamp)
            }
            _downloadProgress.value = _downloadProgress.value + (key to 1.0)
            return entry
        } finally {
            _downloadProgress.value = _downloadProgress.value - key
        }
    }

    suspend fun prefetchPackCharts(
        pack: PackMetaResponse,
        flightId: String,
        timestamp: String,
    ) {
        val manifest = pack.charts ?: return
        for (source in manifest.sources) {
            source.overlayUrl?.let { overlayUrl ->
                runCatching {
                    chartOverlay(
                        urlPath = overlayUrl,
                        flightId = flightId,
                        timestamp = timestamp,
                        source = source.source,
                    )
                }
            }
            for (chart in source.charts) {
                if (chart.prefetch && chart.available) {
                    runCatching { chartImage(chart) }
                }
            }
        }
    }

    suspend fun pruneStalePacks(
        olderThanDays: Int = 7,
        nowEpochMs: Long = System.currentTimeMillis(),
    ): Int = cache.pruneStalePacks(olderThanDays = olderThanDays, nowEpochMs = nowEpochMs)

    // ========================================================================
    // Seed & Cache Metadata Helpers
    // ========================================================================

    suspend fun cacheFlightList(flights: List<FlightResponse>) {
        runCatching {
            val bytes = json.encodeToString(ListSerializer(FlightResponse.serializer()), flights)
                .toByteArray(Charsets.UTF_8)
            cache.writeMetadata(bytes, name = "flights")
        }
        for (flight in flights) {
            cacheFlightData(flight)
        }
    }

    suspend fun cacheFlightData(flight: FlightResponse) {
        runCatching {
            val bytes = json.encodeToString(FlightResponse.serializer(), flight)
                .toByteArray(Charsets.UTF_8)
            cache.writeFlightMetadata(bytes, flightId = flight.id, name = "flight")
        }
    }

    suspend fun cachePackMeta(pack: PackMetaResponse, flightId: String) {
        runCatching {
            val bytes = json.encodeToString(PackMetaResponse.serializer(), pack)
                .toByteArray(Charsets.UTF_8)
            cache.writeFlightMetadata(bytes, flightId = flightId, name = "latest-pack")
            if (pack.fetchTimestamp.isNotEmpty()) {
                cache.writeData(
                    bytes = bytes,
                    flightId = flightId,
                    timestamp = pack.fetchTimestamp,
                    endpoint = PACK_META_ENDPOINT,
                )
            }
        }
    }

    suspend fun readCachedFlightList(): List<FlightResponse>? {
        val bytes = cache.readMetadata("flights") ?: return null
        return runCatching {
            json.decodeFromString(
                ListSerializer(FlightResponse.serializer()),
                bytes.toString(Charsets.UTF_8),
            )
        }.getOrNull()
    }

    suspend fun readCachedFlight(id: String): FlightResponse? {
        cache.readFlightMetadata(id, name = "flight")?.let { bytes ->
            runCatching {
                json.decodeFromString(FlightResponse.serializer(), bytes.toString(Charsets.UTF_8))
            }.getOrNull()?.let { return it }
        }
        return readCachedFlightList()?.firstOrNull { it.id == id }
    }

    suspend fun readCachedPacks(flightId: String): List<PackMetaResponse>? {
        val bytes = cache.readFlightMetadata(flightId, name = "packs") ?: return null
        return runCatching {
            json.decodeFromString(
                ListSerializer(PackMetaResponse.serializer()),
                bytes.toString(Charsets.UTF_8),
            )
        }.getOrNull()
    }

    suspend fun readCachedLatestPack(flightId: String): PackMetaResponse? {
        cache.readFlightMetadata(flightId, name = "latest-pack")?.let { bytes ->
            runCatching {
                json.decodeFromString(PackMetaResponse.serializer(), bytes.toString(Charsets.UTF_8))
            }.getOrNull()?.let { return it }
        }
        return readCachedPacks(flightId)?.firstOrNull()
    }

    suspend fun readCachedTripList(
        allowCalendarStale: Boolean = false,
        nowEpochMs: Long = System.currentTimeMillis(),
    ): List<TripSummaryResponse>? {
        val bytes = cache.readMetadata("trips") ?: return null
        val text = bytes.toString(Charsets.UTF_8)
        runCatching {
            val wrapper = json.decodeFromString(TripListCache.serializer(), text)
            if (!allowCalendarStale && wrapper.isCalendarStale(nowEpochMs)) {
                return null
            }
            return wrapper.trips
        }
        return runCatching {
            json.decodeFromString(ListSerializer(TripSummaryResponse.serializer()), text)
        }.getOrNull()
    }

    // ========================================================================
    // Internal Cache-First Fetch Primitives
    // ========================================================================

    private suspend fun <T> cachedFetch(
        serializer: KSerializer<T>,
        flightId: String,
        timestamp: String,
        endpoint: String,
        networkFetch: suspend () -> T,
    ): T {
        if (isOnline()) {
            // Pack data is immutable for (flightId, timestamp) — serve from disk if already cached
            cache.readData(flightId, timestamp, endpoint)?.let { cachedBytes ->
                runCatching {
                    json.decodeFromString(serializer, cachedBytes.toString(Charsets.UTF_8))
                }.getOrNull()?.let { decoded ->
                    lastFetchWasCached = false
                    return decoded
                }
            }
            try {
                val result = networkFetch()
                runCatching {
                    val bytes = json.encodeToString(serializer, result).toByteArray(Charsets.UTF_8)
                    cache.writeData(bytes, flightId = flightId, timestamp = timestamp, endpoint = endpoint)
                }
                lastFetchWasCached = false
                return result
            } catch (e: Throwable) {
                if (!isTransientNetworkFailure(e)) throw e
                cache.readData(flightId, timestamp, endpoint)?.let { cachedBytes ->
                    runCatching {
                        json.decodeFromString(serializer, cachedBytes.toString(Charsets.UTF_8))
                    }.getOrNull()?.let { decoded ->
                        lastFetchWasCached = true
                        return decoded
                    }
                }
                throw ApiError.OfflineNotCached()
            }
        } else {
            val cachedBytes = cache.readData(flightId, timestamp, endpoint)
                ?: throw ApiError.OfflineNotCached()
            val decoded = try {
                json.decodeFromString(serializer, cachedBytes.toString(Charsets.UTF_8))
            } catch (e: Exception) {
                throw ApiError.DecodingError(e)
            }
            lastFetchWasCached = true
            return decoded
        }
    }

    private suspend fun cachedRawFetch(
        flightId: String,
        timestamp: String,
        endpoint: String,
        networkFetch: suspend () -> ByteArray,
    ): ByteArray {
        if (isOnline()) {
            cache.readData(flightId, timestamp, endpoint)?.let { cachedBytes ->
                lastFetchWasCached = false
                return cachedBytes
            }
            try {
                val bytes = networkFetch()
                runCatching {
                    cache.writeData(bytes, flightId = flightId, timestamp = timestamp, endpoint = endpoint)
                }
                lastFetchWasCached = false
                return bytes
            } catch (e: Throwable) {
                if (!isTransientNetworkFailure(e)) throw e
                cache.readData(flightId, timestamp, endpoint)?.let { cachedBytes ->
                    lastFetchWasCached = true
                    return cachedBytes
                }
                throw ApiError.OfflineNotCached()
            }
        } else {
            val cachedBytes = cache.readData(flightId, timestamp, endpoint)
                ?: throw ApiError.OfflineNotCached()
            lastFetchWasCached = true
            return cachedBytes
        }
    }

    private fun isTransientNetworkFailure(e: Throwable): Boolean = when (e) {
        is ApiError.NetworkError -> true
        is ApiError.ServerError -> e.statusCode >= 500
        is IOException -> true
        else -> false
    }

    companion object {
        const val PACK_META_ENDPOINT = BriefingCacheStore.PACK_META_ENDPOINT
    }
}
