package aero.flyfun.weather.data

import aero.flyfun.common.InMemoryTokenStore
import aero.flyfun.common.RollingBearerInterceptor
import aero.flyfun.common.TokenStore
import aero.flyfun.weather.model.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.serializer
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/**
 * HTTP client for the FlyFun Weather backend API.
 * Mirrors `APIClient` on iOS, backed by OkHttp + [RollingBearerInterceptor] + `kotlinx.serialization`.
 */
class ApiClient(
    baseUrl: String = DEFAULT_BASE_URL,
    val tokenStore: TokenStore = InMemoryTokenStore(),
    onUnauthorized: () -> Unit = {},
    val json: Json = WbJson,
    httpClient: OkHttpClient? = null,
) {
    @Volatile
    var baseUrl: String = baseUrl.trimEnd('/')
        set(value) {
            field = value.trimEnd('/')
        }

    val httpClient: OkHttpClient = httpClient ?: OkHttpClient.Builder()
        .apply {
            if (aero.flyfun.weather.BuildConfig.DEBUG || baseUrl.contains("localhost.ro-z.me", ignoreCase = true)) {
                dns(aero.flyfun.common.LocalhostRozMeDns)
            }
        }
        .addInterceptor(
            RollingBearerInterceptor(
                tokenStore = tokenStore,
                allowedHostProvider = { runCatching { this.baseUrl.toHttpUrl().host }.getOrNull() },
                onUnauthorized = onUnauthorized,
            )
        )
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val sseHttpClient: OkHttpClient = this.httpClient.newBuilder()
        .readTimeout(5, TimeUnit.MINUTES)
        .build()

    // ========================================================================
    // URL & Core Request Helpers
    // ========================================================================

    fun buildUrl(
        path: String,
        queryItems: List<Pair<String, String>> = emptyList(),
    ): HttpUrl {
        val fullUrlString = if (path.startsWith("http://") || path.startsWith("https://")) {
            path
        } else {
            val cleanPath = if (path.startsWith("/")) path else "/$path"
            "$baseUrl$cleanPath"
        }
        val builder = fullUrlString.toHttpUrl().newBuilder()
        for ((k, v) in queryItems) {
            builder.addQueryParameter(k, v)
        }
        return builder.build()
    }

    /**
     * Percent-encodes a pack timestamp for use as a URL path segment (including `:` -> `%3A` and `+` -> `%2B`).
     */
    fun encodePathSegment(segment: String): String =
        URLEncoder.encode(segment, Charsets.UTF_8.name()).replace("+", "%20")

    suspend fun requestRaw(
        path: String,
        method: String = "GET",
        queryItems: List<Pair<String, String>> = emptyList(),
        bodyBytes: ByteArray? = null,
        contentType: String = "application/json; charset=utf-8",
        headers: Map<String, String> = emptyMap(),
    ): ByteArray = withContext(Dispatchers.IO) {
        val url = buildUrl(path, queryItems)
        val requestBuilder = Request.Builder().url(url)
        for ((k, v) in headers) {
            requestBuilder.header(k, v)
        }

        val requestBody = when {
            bodyBytes != null -> bodyBytes.toRequestBody(contentType.toMediaType())
            method == "POST" || method == "PUT" || method == "PATCH" -> ByteArray(0).toRequestBody(null)
            else -> null
        }
        requestBuilder.method(method, requestBody)

        val response = try {
            httpClient.newCall(requestBuilder.build()).execute()
        } catch (e: IOException) {
            throw ApiError.NetworkError(e)
        } catch (e: Exception) {
            if (e is ApiError) throw e
            throw ApiError.NetworkError(e)
        }

        response.use { resp ->
            val bytes = try {
                resp.body?.bytes() ?: ByteArray(0)
            } catch (e: IOException) {
                throw ApiError.NetworkError(e)
            }
            validateStatus(resp.code, bytes)
            bytes
        }
    }

    fun validateStatus(statusCode: Int, bodyBytes: ByteArray) {
        when (statusCode) {
            in 200..299 -> return
            401 -> throw ApiError.Unauthorized
            403 -> throw ApiError.Forbidden
            404 -> throw ApiError.NotFound
            else -> {
                val detail = extractErrorDetail(bodyBytes)
                throw ApiError.ServerError(statusCode = statusCode, detail = detail)
            }
        }
    }

    private fun extractErrorDetail(bodyBytes: ByteArray): String? {
        if (bodyBytes.isEmpty()) return null
        val text = bodyBytes.toString(Charsets.UTF_8).trim()
        if (text.isEmpty()) return null
        val parsedDetail = runCatching {
            val obj = json.parseToJsonElement(text) as? JsonObject
            obj?.get("detail")?.jsonPrimitive?.content
        }.getOrNull()
        return parsedDetail ?: text
    }

    suspend fun <T> requestDecoded(
        serializer: KSerializer<T>,
        path: String,
        method: String = "GET",
        queryItems: List<Pair<String, String>> = emptyList(),
        bodyBytes: ByteArray? = null,
    ): T {
        val bytes = requestRaw(
            path = path,
            method = method,
            queryItems = queryItems,
            bodyBytes = bodyBytes,
        )
        return try {
            json.decodeFromString(serializer, bytes.toString(Charsets.UTF_8))
        } catch (e: Exception) {
            throw ApiError.DecodingError(e)
        }
    }

    suspend inline fun <reified T> get(
        path: String,
        queryItems: List<Pair<String, String>> = emptyList(),
    ): T = requestDecoded(
        serializer = json.serializersModule.serializer<T>(),
        path = path,
        method = "GET",
        queryItems = queryItems,
    )

    suspend inline fun <reified Req, reified Res> post(
        path: String,
        body: Req,
        queryItems: List<Pair<String, String>> = emptyList(),
    ): Res {
        val reqBytes = json.encodeToString(json.serializersModule.serializer<Req>(), body)
            .toByteArray(Charsets.UTF_8)
        return requestDecoded(
            serializer = json.serializersModule.serializer<Res>(),
            path = path,
            method = "POST",
            queryItems = queryItems,
            bodyBytes = reqBytes,
        )
    }

    suspend inline fun <reified Res> postNoBody(
        path: String,
        queryItems: List<Pair<String, String>> = emptyList(),
    ): Res = requestDecoded(
        serializer = json.serializersModule.serializer<Res>(),
        path = path,
        method = "POST",
        queryItems = queryItems,
    )

    suspend fun postEmpty(
        path: String,
        queryItems: List<Pair<String, String>> = emptyList(),
    ) {
        requestRaw(path = path, method = "POST", queryItems = queryItems)
    }

    suspend inline fun <reified Req> postIgnoreResponse(
        path: String,
        body: Req,
        queryItems: List<Pair<String, String>> = emptyList(),
    ) {
        val reqBytes = json.encodeToString(json.serializersModule.serializer<Req>(), body)
            .toByteArray(Charsets.UTF_8)
        requestRaw(path = path, method = "POST", queryItems = queryItems, bodyBytes = reqBytes)
    }

    suspend inline fun <reified Req, reified Res> patch(
        path: String,
        body: Req,
        queryItems: List<Pair<String, String>> = emptyList(),
    ): Res {
        val reqBytes = json.encodeToString(json.serializersModule.serializer<Req>(), body)
            .toByteArray(Charsets.UTF_8)
        return requestDecoded(
            serializer = json.serializersModule.serializer<Res>(),
            path = path,
            method = "PATCH",
            queryItems = queryItems,
            bodyBytes = reqBytes,
        )
    }

    suspend inline fun <reified Req, reified Res> put(
        path: String,
        body: Req,
        queryItems: List<Pair<String, String>> = emptyList(),
    ): Res {
        val reqBytes = json.encodeToString(json.serializersModule.serializer<Req>(), body)
            .toByteArray(Charsets.UTF_8)
        return requestDecoded(
            serializer = json.serializersModule.serializer<Res>(),
            path = path,
            method = "PUT",
            queryItems = queryItems,
            bodyBytes = reqBytes,
        )
    }

    suspend fun delete(
        path: String,
        queryItems: List<Pair<String, String>> = emptyList(),
    ) {
        requestRaw(path = path, method = "DELETE", queryItems = queryItems)
    }

    suspend inline fun <reified Req, reified Res> deleteWithBody(
        path: String,
        body: Req,
    ): Res {
        val reqBytes = json.encodeToString(json.serializersModule.serializer<Req>(), body)
            .toByteArray(Charsets.UTF_8)
        return requestDecoded(
            serializer = json.serializersModule.serializer<Res>(),
            path = path,
            method = "DELETE",
            bodyBytes = reqBytes,
        )
    }

    // ========================================================================
    // Flights
    // ========================================================================

    suspend fun listFlights(): List<FlightResponse> = get("/api/flights")

    suspend fun getFlight(id: String): FlightResponse = get("/api/flights/${encodePathSegment(id)}")

    suspend fun createFlight(req: CreateFlightRequest): FlightResponse =
        post("/api/flights", req)

    /** `PATCH`: the server has no `PUT` on this path (it answered 405, #885). */
    suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse =
        patch("/api/flights/${encodePathSegment(id)}", req)

    suspend fun deleteFlight(id: String) =
        delete("/api/flights/${encodePathSegment(id)}")

    suspend fun bulkDeleteFlights(ids: List<String>): BulkDeleteResponse =
        post("/api/flights/bulk-delete", BulkDeleteRequest(flightIds = ids))

    suspend fun moveFlight(id: String, req: MoveFlightRequest): FlightResponse =
        post("/api/flights/${encodePathSegment(id)}/move", req)

    suspend fun parseFpl(fplText: String): ParseFplResponse =
        post("/api/flights/parse-fpl", ParseFplRequest(fplText = fplText))

    suspend fun interpretRoute(rawRoute: String): InterpretRouteResponse =
        post("/api/flights/interpret-route", InterpretRouteRequest(rawRoute = rawRoute))

    suspend fun routeDistance(waypoints: List<String>): RouteDistanceResponse =
        post("/api/flights/route-distance", RouteDistanceRequest(waypoints = waypoints))

    // ========================================================================
    // Autorouter
    // ========================================================================

    /** The linked Autorouter account's recent routes; a `409 autorouter_not_linked` means "offer to link". */
    suspend fun fetchAutorouterRoutes(limit: Int = 25): AutorouterRoutesResponse =
        get("/api/flights/autorouter-routes", listOf("limit" to limit.toString()))

    // Account linking lives in flyfun-common, at the root rather than under `/api`.
    suspend fun createAutorouterLinkTicket(scheme: String): AutorouterLinkTicketResponse =
        post("/autorouter/link-ticket", AutorouterLinkTicketRequest(scheme = scheme))

    suspend fun completeAutorouterLink(code: String) =
        postIgnoreResponse("/autorouter/link-complete", AutorouterLinkCompleteRequest(code = code))

    suspend fun unlinkAutorouter() = postEmpty("/autorouter/unlink")

    /** Permanently deletes the signed-in user's account (flyfun-common `DELETE /auth/account`). */
    suspend fun deleteAccount() = delete("/auth/account")

    // ========================================================================
    // Packs & Briefing Data
    // ========================================================================

    suspend fun listPacks(flightId: String): List<PackMetaResponse> =
        get("/api/flights/${encodePathSegment(flightId)}/packs")

    suspend fun latestPack(flightId: String): PackMetaResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/latest")

    suspend fun getSnapshot(flightId: String, timestamp: String): SnapshotResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/snapshot")

    suspend fun getRouteAnalyses(flightId: String, timestamp: String): RouteAnalysesResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/route-analyses")

    suspend fun getAdvisories(flightId: String, timestamp: String, alt: Boolean = false): AdvisoriesResponse {
        val suffix = if (alt) "advisories/alt" else "advisories"
        return get("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/$suffix")
    }

    suspend fun recalculateAdvisories(
        flightId: String,
        timestamp: String,
        alt: Boolean = false,
    ): AdvisoriesResponse {
        val suffix = if (alt) "advisories/alt/recalculate" else "advisories/recalculate"
        return postNoBody("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/$suffix")
    }

    suspend fun getAdvisoryDetail(
        flightId: String,
        timestamp: String,
        advisoryId: String,
        alt: Boolean = false,
    ): AdvisoryDetailResponse {
        val base = if (alt) "advisories/alt" else "advisories"
        return get(
            "/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/$base/${encodePathSegment(advisoryId)}/detail"
        )
    }

    suspend fun getAlternates(flightId: String, timestamp: String): AlternatesResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/alternates")

    suspend fun getDigest(flightId: String, timestamp: String): DigestResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/digest/json")

    suspend fun getElevation(flightId: String, timestamp: String): ElevationResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/elevation")

    suspend fun getSoundingProfile(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): SoundingProfileResponse =
        get(
            "/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/sounding-profile/$pointIndex/${encodePathSegment(model)}"
        )

    suspend fun getGramet(flightId: String, timestamp: String): ByteArray =
        requestRaw("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/gramet")

    suspend fun getSkewT(flightId: String, timestamp: String, icao: String, model: String): ByteArray =
        requestRaw(
            "/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/skewt/${encodePathSegment(icao)}/${encodePathSegment(model)}"
        )

    suspend fun getRouteSkewT(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): ByteArray =
        requestRaw(
            "/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/skewt/route/$pointIndex/${encodePathSegment(model)}"
        )

    suspend fun getChartImage(urlPath: String): ByteArray =
        requestRaw(urlPath)

    suspend fun getChartOverlay(urlPath: String): ChartOverlay =
        requestDecoded(
            serializer = MapSerializer(String.serializer(), ChartOverlayEntry.serializer()),
            path = urlPath,
        )

    suspend fun getBundle(flightId: String, timestamp: String): ByteArray =
        requestRaw("/api/flights/${encodePathSegment(flightId)}/packs/${encodePathSegment(timestamp)}/bundle")

    suspend fun getFreshness(flightId: String): DataStatus =
        get("/api/flights/${encodePathSegment(flightId)}/packs/freshness")

    suspend fun getLiveLayer(flightId: String): LiveLayerResponse =
        get("/api/flights/${encodePathSegment(flightId)}/live")

    // ========================================================================
    // Refresh & SSE
    // ========================================================================

    suspend fun triggerRefresh(flightId: String, force: Boolean = false): RefreshStatusResponse {
        val query = if (force) listOf("force" to "true") else emptyList()
        return postNoBody("/api/flights/${encodePathSegment(flightId)}/packs/refresh", query)
    }

    suspend fun refreshStatus(flightId: String): ActiveRefreshResponse =
        get("/api/flights/${encodePathSegment(flightId)}/packs/refresh/status")

    suspend fun activeRefreshes(): Map<String, ActiveRefreshResponse> =
        requestDecoded(
            serializer = MapSerializer(String.serializer(), ActiveRefreshResponse.serializer()),
            path = "/api/refresh/active",
        )

    /**
     * Streams SSE [RefreshEvent]s for a briefing refresh via `POST /api/flights/{id}/packs/refresh/stream`.
     *
     * The endpoint is POST-only (it starts a pipeline run); a GET is answered 405 and the
     * stream fails before any event, which is how a newly created flight never briefed.
     */
    fun refreshPackStream(
        flightId: String,
        force: Boolean = false,
        notifyEmail: Boolean = false,
    ): Flow<RefreshEvent> = callbackFlow {
        val query = buildList {
            if (force) add("force" to "true")
            if (notifyEmail) add("notify_email" to "true")
        }
        val url = buildUrl("/api/flights/${encodePathSegment(flightId)}/packs/refresh/stream", query)
        val request = Request.Builder()
            .url(url)
            .header("Accept", "text/event-stream")
            .post(ByteArray(0).toRequestBody(null))
            .build()

        val listener = object : EventSourceListener() {
            @Volatile
            private var terminalEventReceived = false

            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String,
            ) {
                val trimmed = data.trim()
                if (trimmed.isEmpty()) return
                val event = runCatching {
                    json.decodeFromString(RefreshEvent.serializer(), trimmed)
                }.getOrNull() ?: return
                val resolvedType = event.type.ifBlank { type.orEmpty() }
                val normalized = if (resolvedType != event.type) event.copy(type = resolvedType) else event
                val isTerminal = normalized.type == "complete" || normalized.type == "error"
                if (isTerminal) {
                    terminalEventReceived = true
                }
                trySend(normalized)
                if (isTerminal) {
                    channel.close()
                    eventSource.cancel()
                }
            }

            override fun onClosed(eventSource: EventSource) {
                channel.close()
            }

            override fun onFailure(
                eventSource: EventSource,
                t: Throwable?,
                response: Response?,
            ) {
                // Once `complete` or `error` has been delivered, cancelling or closing the
                // underlying HTTP/2 or chunked SSE stream triggers `onFailure` with the
                // initial 200 OK response attached; do not turn that teardown into an error.
                if (terminalEventReceived) {
                    channel.close()
                    return
                }
                val err: Throwable = when {
                    response != null && !response.isSuccessful -> {
                        val code = response.code
                        val bodyBytes = runCatching { response.body?.bytes() ?: ByteArray(0) }.getOrDefault(ByteArray(0))
                        when (code) {
                            401 -> ApiError.Unauthorized
                            403 -> ApiError.Forbidden
                            404 -> ApiError.NotFound
                            else -> ApiError.ServerError(code, extractErrorDetail(bodyBytes))
                        }
                    }
                    t is ApiError -> t
                    t != null -> ApiError.NetworkError(t)
                    else -> ApiError.InvalidResponse("SSE stream failed")
                }
                channel.close(err)
            }
        }

        val eventSource = EventSources.createFactory(sseHttpClient).newEventSource(request, listener)
        awaitClose {
            eventSource.cancel()
        }
    }

    // ========================================================================
    // Flexible Departure / Time Options
    // ========================================================================

    suspend fun getTimeOptions(flightId: String): TimeOptionsResponse =
        get("/api/flights/${encodePathSegment(flightId)}/time-options")

    suspend fun triggerTimeScan(flightId: String): TimeScanStatusDTO =
        postNoBody("/api/flights/${encodePathSegment(flightId)}/time-options/scan")

    suspend fun confirmTimeOption(flightId: String, req: ConfirmTimeOptionRequest): FlightResponse =
        post("/api/flights/${encodePathSegment(flightId)}/time-options/confirm", req)

    // ========================================================================
    // Observed Imagery (Radar / Cloud Tops / Lightning / Cells)
    // ========================================================================

    suspend fun getObservedStatus(): ObservedImageryStatusResponse =
        get("/api/observed/status")

    suspend fun getObservedFrames(layer: String, bbox: String? = null): ObservedFramesResponse {
        val query = if (bbox != null) listOf("bbox" to bbox) else emptyList()
        return get("/api/observed/${encodePathSegment(layer)}/frames", query)
    }

    suspend fun getObservedFlashes(bbox: String, validTime: String? = null): ObservedFlashesResponse {
        val query = buildList {
            add("bbox" to bbox)
            if (validTime != null) add("t" to validTime)
        }
        return get("/api/observed/lightning/flashes", query)
    }

    suspend fun getCellFrames(bbox: String): CellFramesResponse =
        get("/api/observed/cells/frames", listOf("bbox" to bbox))

    suspend fun getObservedCells(bbox: String, validTime: String? = null): CellDisplay {
        val query = buildList {
            add("bbox" to bbox)
            if (validTime != null) add("t" to validTime)
        }
        return get("/api/observed/cells", query)
    }

    // ========================================================================
    // Debrief
    // ========================================================================

    suspend fun getDebrief(flightId: String): DebriefResponse =
        get("/api/flights/${encodePathSegment(flightId)}/debrief")

    suspend fun saveDebrief(flightId: String, req: DebriefRequest): DebriefResponse =
        put("/api/flights/${encodePathSegment(flightId)}/debrief", req)

    suspend fun deleteDebrief(flightId: String) =
        delete("/api/flights/${encodePathSegment(flightId)}/debrief")

    // ========================================================================
    // Shared Flights & Trips
    // ========================================================================

    suspend fun resolveSharedFlight(code: String): FlightResponse =
        get("/api/flights/by-share/${encodePathSegment(code)}")

    /** Follow a flight by id (resolve a share code first with [resolveSharedFlight]). */
    suspend fun subscribeToFlight(flightId: String): SubscribeResponse =
        postNoBody("/api/flights/${encodePathSegment(flightId)}/subscribe")

    suspend fun unsubscribeFromFlight(flightId: String) =
        delete("/api/flights/${encodePathSegment(flightId)}/subscribe")

    suspend fun resolveSharedTrip(code: String): TripResponse =
        get("/api/trips/by-share/${encodePathSegment(code)}")

    /** Follow every leg of a trip by id (resolve a share code first with [resolveSharedTrip]). */
    suspend fun subscribeToTrip(tripId: String): TripSubscribeResponse =
        postNoBody("/api/trips/${encodePathSegment(tripId)}/subscribe")

    suspend fun unsubscribeFromTrip(tripId: String) =
        delete("/api/trips/${encodePathSegment(tripId)}/subscribe")

    // ========================================================================
    // Trips
    // ========================================================================

    suspend fun listTrips(): List<TripSummaryResponse> =
        get("/api/trips")

    suspend fun getTrip(tripId: String): TripResponse =
        get("/api/trips/${encodePathSegment(tripId)}")

    suspend fun createTrip(req: CreateTripRequest): TripResponse =
        post("/api/trips", req)

    suspend fun addTripLegs(tripId: String, req: AddTripLegsRequest): TripResponse =
        post("/api/trips/${encodePathSegment(tripId)}/legs", req)

    suspend fun updateTrip(tripId: String, req: UpdateTripRequest): TripResponse =
        patch("/api/trips/${encodePathSegment(tripId)}", req)

    suspend fun deleteTrip(tripId: String, deleteFlights: Boolean = false) {
        val query = if (deleteFlights) listOf("delete_flights" to "true") else emptyList()
        delete("/api/trips/${encodePathSegment(tripId)}", query)
    }

    suspend fun removeTripLeg(tripId: String, flightId: String): TripResponse {
        val bytes = requestRaw(
            path = "/api/trips/${encodePathSegment(tripId)}/legs/${encodePathSegment(flightId)}",
            method = "DELETE",
        )
        return try {
            json.decodeFromString(TripResponse.serializer(), bytes.toString(Charsets.UTF_8))
        } catch (e: Exception) {
            throw ApiError.DecodingError(e)
        }
    }

    suspend fun refreshTrip(tripId: String, force: Boolean = false): TripRefreshStatus {
        val query = if (force) listOf("force" to "true") else emptyList()
        return postNoBody("/api/trips/${encodePathSegment(tripId)}/refresh", query)
    }

    suspend fun tripRefreshStatus(tripId: String): TripRefreshStatus =
        get("/api/trips/${encodePathSegment(tripId)}/refresh/status")

    /** There is no GET: the trip itself carries the stored paragraph (`ai_summary`, `ai_summary_stale`). */
    suspend fun generateTripAiSummary(tripId: String): TripAiSummaryResponse =
        postNoBody("/api/trips/${encodePathSegment(tripId)}/ai-summary")

    // ========================================================================
    // Aircraft, Profiles, Preferences, Usage, Feedback, Messages, PIREPs
    // ========================================================================

    suspend fun listAircraft(): List<AircraftResponse> = get("/api/aircraft")

    suspend fun createAircraft(req: CreateAircraftRequest): AircraftResponse =
        post("/api/aircraft", req)

    suspend fun updateAircraft(id: Int, req: UpdateAircraftRequest): AircraftResponse =
        put("/api/aircraft/$id", req)

    suspend fun deleteAircraft(id: Int) = delete("/api/aircraft/$id")

    suspend fun searchAircraftTypes(query: String? = null): List<AircraftTypeResponse> {
        val q = if (!query.isNullOrBlank()) listOf("q" to query) else emptyList()
        return get("/api/aircraft/types", q)
    }

    suspend fun listProfiles(): List<ProfileResponse> = get("/api/user/profiles")

    suspend fun getPreferences(): PreferencesResponse = get("/api/user/preferences")

    suspend fun updatePreferences(body: Map<String, kotlinx.serialization.json.JsonElement>): PreferencesResponse =
        put("/api/user/preferences", body)

    suspend fun getUsageSummary(): UsageSummaryResponse = get("/api/user/usage")

    suspend fun listSystemMessages(): List<SystemMessageResponse> =
        get("/api/messages")

    suspend fun getMessagesStatus(): MessagesStatusResponse =
        get("/api/messages/status")

    suspend fun markMessagesSeen(upToId: Int? = null) {
        val query = if (upToId != null) listOf("up_to_id" to upToId.toString()) else emptyList()
        postEmpty("/api/messages/seen", query)
    }

    suspend fun registerDeviceToken(req: DeviceRegistrationRequest) =
        postIgnoreResponse("/api/devices", req)

    suspend fun unregisterDeviceToken(token: String) =
        delete("/api/devices/${encodePathSegment(token)}")

    suspend fun getBadgeStatus(): BadgeStatus =
        get("/api/flights/badge")

    suspend fun markFlightSeen(flightId: String): BadgeStatus =
        postNoBody("/api/flights/${encodePathSegment(flightId)}/seen")

    suspend fun submitFeedback(req: FeedbackRequest) =
        postIgnoreResponse("/api/feedback", req)

    suspend fun submitPirep(req: SubmitPirepRequest): PirepResponse =
        post("/api/pireps", req)

    suspend fun listPireps(flightId: String): PirepListResponse =
        get("/api/pireps", listOf("flight_id" to flightId))

    /** App GPS fixes for a flight (#761), owner only; `landed` with the landing auto-stop (#874). */
    suspend fun postLivePosition(flightId: String, request: LivePositionRequest): LivePositionResult =
        post("/api/flights/$flightId/live/position", request)

    // ========================================================================
    // Forecast & Historical Maps & Airport Weather
    // ========================================================================

    suspend fun getForecastDays(): ForecastDaysResponse =
        get("/api/maps/forecast/days")

    suspend fun getForecastMap(day: Int, hourUtc: Int): ForecastMapResponse =
        get("/api/maps/forecast", listOf("day" to day.toString(), "hour" to hourUtc.toString()))

    suspend fun getForecastGramet(
        icao: String,
        day: Int,
        hourUtc: Int,
        initKey: String? = null,
    ): ByteArray {
        val query = buildList {
            add("icao" to icao)
            add("day" to day.toString())
            add("hour" to hourUtc.toString())
            if (initKey != null) add("init_key" to initKey)
        }
        return requestRaw("/api/maps/forecast/gramet", queryItems = query)
    }

    suspend fun getHistoricalRange(): HistoricalRangeResponse =
        get("/api/maps/historical/range")

    suspend fun getHistoricalMap(date: String, hourUtc: Int): HistoricalMapResponse =
        get("/api/maps/historical", listOf("date" to date, "hour" to hourUtc.toString()))

    suspend fun getFrequentAirports(): FrequentAirportsResponse =
        get("/api/flights/frequent-airports")

    companion object {
        /**
         * Returns `true` when running inside the Android Emulator (mirroring iOS
         * `#if targetEnvironment(simulator)`), and `false` on physical devices or JVM unit tests.
         */
        fun isRunningOnEmulator(): Boolean {
            val fingerprint = android.os.Build.FINGERPRINT.orEmpty()
            val model = android.os.Build.MODEL.orEmpty()
            val manufacturer = android.os.Build.MANUFACTURER.orEmpty()
            val brand = android.os.Build.BRAND.orEmpty()
            val device = android.os.Build.DEVICE.orEmpty()
            val product = android.os.Build.PRODUCT.orEmpty()
            val hardware = android.os.Build.HARDWARE.orEmpty()
            return fingerprint.startsWith("generic") ||
                fingerprint.startsWith("unknown") ||
                model.contains("google_sdk") ||
                model.contains("Emulator") ||
                model.contains("Android SDK built for") ||
                model.contains("sdk_gphone") ||
                manufacturer.contains("Genymotion") ||
                (brand.startsWith("generic") && device.startsWith("generic")) ||
                product.contains("sdk") ||
                product.contains("emulator") ||
                product.contains("simulator") ||
                hardware.contains("goldfish") ||
                hardware.contains("ranchu")
        }

        val DEFAULT_BASE_URL: String = if (aero.flyfun.weather.BuildConfig.DEBUG && isRunningOnEmulator()) {
            "https://localhost.ro-z.me:8443"
        } else {
            "https://weather.flyfun.aero"
        }
    }
}
