package aero.flyfun.weather.data

import aero.flyfun.weather.model.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.serialization.builtins.ListSerializer

/**
 * Canned, internally-consistent briefing fixtures for `fixture-1`
 * (LFMD -> LFML, D-1, AMBER overall with one RED convective advisory).
 *
 * The blobs are the shared JSON files in `app/fixtures/api` (#895), the same files the iOS app
 * serves in mock mode, decoded through the production [WbJson] serializer so any DTO contract
 * mismatch fails loudly (see [FixtureFiles]).
 */
object FixtureBriefingData {
    const val FLIGHT_ID = "fixture-1"
    const val TIMESTAMP = "2099-06-30T06:00:00Z"

    val pack: PackMetaResponse by lazy { FixtureFiles.decode(PackMetaResponse.serializer(), "pack") }

    val packs: List<PackMetaResponse> by lazy { listOf(pack) }

    val advisories: AdvisoriesResponse by lazy { FixtureFiles.decode(AdvisoriesResponse.serializer(), "advisories") }

    val convectiveDetail: AdvisoryDetailResponse by lazy {
        FixtureFiles.decode(AdvisoryDetailResponse.serializer(), "advisory-detail-convective")
    }

    val digest: DigestResponse by lazy { FixtureFiles.decode(DigestResponse.serializer(), "digest") }

    val snapshot: SnapshotResponse by lazy { FixtureFiles.decode(SnapshotResponse.serializer(), "snapshot") }

    /** What `/live` returns before any live data exists: every block null. */
    val nullLiveLayer: LiveLayerResponse by lazy { FixtureFiles.decode(LiveLayerResponse.serializer(), "live-null") }

    /** A populated layer: only the "since this briefing" changes are added. */
    val liveLayer: LiveLayerResponse by lazy { FixtureFiles.decode(LiveLayerResponse.serializer(), "live") }

    val routeAnalyses: RouteAnalysesResponse by lazy {
        FixtureFiles.decode(RouteAnalysesResponse.serializer(), "route-analyses")
    }

    val elevation: ElevationResponse by lazy { FixtureFiles.decode(ElevationResponse.serializer(), "elevation") }

    val soundingProfile: SoundingProfileResponse by lazy {
        FixtureFiles.decode(SoundingProfileResponse.serializer(), "sounding-profile")
    }
}

/**
 * In-memory [BriefingRepository] serving deterministic fixtures for tests and offline UI previews.
 * Mirrors `FixtureBriefingRepository` on iOS.
 */
open class FixtureBriefingRepository(
    val mockLive: Boolean = false,
) : BriefingRepository {

    val flights: MutableList<FlightResponse> = decodeDefaultFlights().toMutableList()
    var lastMovedFlight: Pair<String, MoveFlightRequest>? = null
    var lastUpdatedFlight: Pair<String, UpdateFlightRequest>? = null
    var lastCreatedFlight: CreateFlightRequest? = null
    val refreshTriggeredFor = mutableListOf<String>()

    /** Invalidation the next [updateFlight] reports, so tests can drive the post-save paths. */
    var nextInvalidation: FlightInvalidation = FlightInvalidation.NONE
    /** When set, `updateFlight` / `interpretRoute` / `triggerRefresh` / `recalculateAdvisories` throw it (tests). */
    var updateFlightError: Throwable? = null
    var interpretRouteError: Throwable? = null
    var triggerRefreshError: Throwable? = null
    var recalculateAdvisoriesError: Throwable? = null
    var savedDebrief: DebriefResponse? = null

    override suspend fun listFlights(): List<FlightResponse> = flights.toList()

    override suspend fun getFlight(id: String): FlightResponse =
        flights.firstOrNull { it.id == id } ?: throw ApiError.NotFound

    override suspend fun createFlight(req: CreateFlightRequest): FlightResponse {
        lastCreatedFlight = req
        val id = "fixture-created-${flights.size + 1}"
        val routeName = req.routeName ?: req.waypoints.joinToString(" ")
        val created = FlightResponse(
            id = id,
            userId = "ui-test",
            routeName = routeName,
            waypoints = req.waypoints,
            departureTime = req.departureTime,
            targetDate = req.departureTime.take(10),
            cruiseAltitudeFt = req.cruiseAltitudeFt ?: 8000,
            flightCeilingFt = req.flightCeilingFt ?: 18000,
            flightDurationHours = req.flightDurationHours ?: 2.0,
            createdAt = "2099-06-30T00:00:00Z",
            profileId = req.profileId,
            aircraftId = req.aircraftId,
            flexibility = req.flexibility ?: FlightFlexibility.NONE,
            rawRoute = req.rawRoute,
            description = req.description,
        )
        flights.add(0, created)
        return created
    }

    override suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse {
        lastUpdatedFlight = id to req
        updateFlightError?.let { throw it }
        val idx = flights.indexOfFirst { it.id == id }
        if (idx < 0) throw ApiError.NotFound
        val current = flights[idx]
        val updated = current.copy(
            waypoints = req.waypoints ?: current.waypoints,
            departureTime = req.departureTime ?: current.departureTime,
            cruiseAltitudeFt = req.cruiseAltitudeFt ?: current.cruiseAltitudeFt,
            flightCeilingFt = req.flightCeilingFt ?: current.flightCeilingFt,
            flightDurationHours = req.flightDurationHours ?: current.flightDurationHours,
            profileId = req.profileId ?: current.profileId,
            aircraftId = req.aircraftId ?: current.aircraftId,
            flexibility = req.flexibility ?: current.flexibility,
            altDepartureTime = when (req.altDepartureTime) {
                null -> current.altDepartureTime
                "" -> null
                else -> req.altDepartureTime
            },
            rawRoute = if (req.waypoints != null) req.rawRoute else current.rawRoute,
        )
        flights[idx] = updated
        return UpdateFlightResponse(flight = updated, invalidation = nextInvalidation)
    }

    override suspend fun deleteFlight(id: String) {
        flights.removeAll { it.id == id }
    }

    override suspend fun bulkDeleteFlights(ids: List<String>): BulkDeleteResponse {
        val idSet = ids.toSet()
        flights.removeAll { it.id in idSet }
        return BulkDeleteResponse(deleted = ids)
    }

    override suspend fun moveFlight(id: String, req: MoveFlightRequest): FlightResponse {
        lastMovedFlight = id to req
        val idx = flights.indexOfFirst { it.id == id }
        if (idx < 0) throw ApiError.NotFound
        val source = flights[idx]
        val waypoints = req.waypoints ?: source.waypoints
        val departure = req.departureTime ?: source.departureTime
        // Like the server, the id follows the route endpoints and the UTC date.
        val sameIdentity = departure.take(10) == source.departureTime.take(10) &&
            waypoints.firstOrNull() == source.waypoints.firstOrNull() &&
            waypoints.lastOrNull() == source.waypoints.lastOrNull()
        val moved = source.copy(
            id = if (sameIdentity) source.id else "fixture-moved-${flights.size + 1}",
            waypoints = waypoints,
            departureTime = departure,
            targetDate = departure.take(10),
            cruiseAltitudeFt = req.cruiseAltitudeFt ?: source.cruiseAltitudeFt,
            flightCeilingFt = req.flightCeilingFt ?: source.flightCeilingFt,
            flightDurationHours = req.flightDurationHours ?: source.flightDurationHours,
            rawRoute = if (req.waypoints != null) req.rawRoute else source.rawRoute,
            trip = if (req.keepInTrip) source.trip else null,
        )
        flights[idx] = moved
        return moved
    }

    override suspend fun parseFpl(fplText: String): ParseFplResponse =
        ParseFplResponse(
            waypoints = listOf("LFMD", "LFML"),
            altitudeFt = 8000,
            flightRules = "V",
            durationHours = 1.5,
            rawRoute = "LFMD DCT LFML",
        )

    /** Keeps the 4-letter idents, drops Field-15 syntax (DCT, VFR, N0120F065...). */
    override suspend fun interpretRoute(rawRoute: String): InterpretRouteResponse {
        interpretRouteError?.let { throw it }
        val tokens = rawRoute.uppercase().split(Regex("[\\s,\\->]+")).filter { it.isNotBlank() }
        val syntax = setOf("DCT", "VFR", "IFR")
        val idents = tokens.filter { it.length == 4 && it.all(Char::isLetter) && it !in syntax }
        return InterpretRouteResponse(originalTokens = tokens, interpreted = idents)
    }

    override suspend fun routeDistance(waypoints: List<String>): RouteDistanceResponse =
        RouteDistanceResponse(
            waypoints = listOf(
                Waypoint(icao = "LFMD", name = "Cannes-Mandelieu", lat = 43.542, lon = 6.953),
                Waypoint(icao = "LFML", name = "Marseille Provence", lat = 43.436, lon = 5.215),
            ),
            totalDistanceNm = 78.0,
        )

    override suspend fun fetchAutorouterRoutes(limit: Int): AutorouterRoutesResponse = AutorouterRoutesResponse()

    override suspend fun listPacks(flightId: String): List<PackMetaResponse> =
        if (flightId == FixtureBriefingData.FLIGHT_ID) FixtureBriefingData.packs else emptyList()

    override suspend fun latestPack(flightId: String): PackMetaResponse {
        if (flightId == FixtureBriefingData.FLIGHT_ID) return FixtureBriefingData.pack
        throw ApiError.NotFound
    }

    override suspend fun freshness(flightId: String): DataStatus =
        FixtureBriefingData.pack.dataStatus ?: DataStatus(fresh = true)

    override suspend fun advisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun altAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun recalculateAdvisories(flightId: String, timestamp: String): AdvisoriesResponse {
        recalculateAdvisoriesError?.let { throw it }
        return FixtureBriefingData.advisories
    }

    override suspend fun recalculateAltAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun advisoryDetail(
        flightId: String,
        timestamp: String,
        advisoryId: String,
        alt: Boolean,
    ): AdvisoryDetailResponse = FixtureBriefingData.convectiveDetail

    override suspend fun alternates(flightId: String, timestamp: String): AlternatesResponse =
        AlternatesResponse()

    override suspend fun digest(flightId: String, timestamp: String): DigestResponse =
        FixtureBriefingData.digest

    override suspend fun snapshot(flightId: String, timestamp: String): SnapshotResponse =
        FixtureBriefingData.snapshot

    override suspend fun routeAnalyses(flightId: String, timestamp: String): RouteAnalysesResponse =
        FixtureBriefingData.routeAnalyses

    override suspend fun elevation(flightId: String, timestamp: String): ElevationResponse =
        FixtureBriefingData.elevation

    override suspend fun soundingProfile(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): SoundingProfileResponse = FixtureBriefingData.soundingProfile

    override suspend fun gramet(flightId: String, timestamp: String): ByteArray =
        throw ApiError.NotFound

    override suspend fun skewT(flightId: String, timestamp: String, icao: String, model: String): ByteArray =
        throw ApiError.NotFound

    override suspend fun routeSkewT(flightId: String, timestamp: String, pointIndex: Int, model: String): ByteArray =
        throw ApiError.NotFound

    open override suspend fun chartImage(chart: PackChart): ByteArray =
        throw ApiError.NotFound

    override suspend fun chartOverlay(
        urlPath: String,
        flightId: String,
        timestamp: String,
        source: String,
    ): ChartOverlay = emptyMap()

    override suspend fun liveLayer(flightId: String): LiveLayerResponse =
        if (mockLive && flightId == FixtureBriefingData.FLIGHT_ID) {
            FixtureBriefingData.liveLayer
        } else {
            FixtureBriefingData.nullLiveLayer
        }

    override suspend fun triggerRefresh(flightId: String, force: Boolean): RefreshStatusResponse {
        triggerRefreshError?.let { throw it }
        refreshTriggeredFor += flightId
        return RefreshStatusResponse(status = "queued", flightId = flightId)
    }

    override suspend fun refreshStatus(flightId: String): ActiveRefreshResponse =
        ActiveRefreshResponse(active = false)

    override suspend fun activeRefreshes(): Map<String, ActiveRefreshResponse> =
        emptyMap()

    override fun refreshPack(flightId: String, force: Boolean, notifyEmail: Boolean): Flow<RefreshEvent> =
        flowOf(RefreshEvent(type = "complete", pack = FixtureBriefingData.pack))

    override suspend fun timeOptions(flightId: String): TimeOptionsResponse =
        TimeOptionsResponse(flightId = flightId)

    override suspend fun triggerTimeScan(flightId: String): TimeScanStatusDTO =
        TimeScanStatusDTO(status = TimeScanJobStatus.READY)

    override suspend fun confirmTimeOption(flightId: String, req: ConfirmTimeOptionRequest): FlightResponse =
        moveFlight(flightId, MoveFlightRequest(departureTime = req.departureTime))

    override suspend fun observedStatus(): ObservedImageryStatusResponse =
        ObservedImageryStatusResponse()

    override suspend fun observedFrames(layer: String, bbox: String?): ObservedFramesResponse =
        ObservedFramesResponse()

    override suspend fun observedFlashes(bbox: String, validTime: String?): ObservedFlashesResponse =
        ObservedFlashesResponse()

    override suspend fun cellFrames(bbox: String): CellFramesResponse =
        CellFramesResponse()

    override suspend fun observedCells(bbox: String, validTime: String?): CellDisplay =
        CellDisplay()

    override suspend fun debrief(flightId: String): DebriefResponse =
        savedDebrief ?: DebriefResponse(flightId = flightId)

    override suspend fun saveDebrief(flightId: String, req: DebriefRequest): DebriefResponse {
        val resp = DebriefResponse(
            flightId = flightId,
            outcomes = req.outcomes,
            otherText = req.otherText,
            ratingOverall = req.ratingOverall,
            createdAt = "2099-06-30T12:00:00Z",
            updatedAt = "2099-06-30T12:00:00Z",
        )
        savedDebrief = resp
        return resp
    }

    override suspend fun deleteDebrief(flightId: String) {
        savedDebrief = null
    }

    override suspend fun submitPirep(req: SubmitPirepRequest): PirepResponse =
        PirepResponse(
            id = 1,
            clientUuid = req.clientUuid,
            flightId = req.flightId,
            observedAt = req.observedAt,
            latitude = req.latitude,
            longitude = req.longitude,
            isOwn = true,
        )

    override suspend fun listPireps(flightId: String): PirepListResponse =
        PirepListResponse()

    override suspend fun resolveSharedFlight(code: String): FlightResponse =
        flights.first()

    override suspend fun subscribeToSharedFlight(code: String): FlightResponse =
        flights.first()

    override suspend fun unsubscribeFromFlight(flightId: String) = Unit

    override suspend fun resolveSharedTrip(code: String): TripResponse =
        FixtureTripData.trip

    override suspend fun subscribeToSharedTrip(code: String): TripSubscribeResponse =
        TripSubscribeResponse(
            tripId = FixtureTripData.TRIP_ID,
            changed = FixtureTripData.trip.flightIds.size,
            totalLegs = FixtureTripData.trip.flightIds.size,
            isSubscribed = true,
        )

    override suspend fun unsubscribeFromTrip(tripId: String) = Unit

    companion object {
        /**
         * `fixture-1` (briefed), `fixture-2` (no pack) and the two legs of `fixture-trip-1`
         * (see [FixtureTripData]), as iOS merges them: the list groups the trip legs exactly as
         * it groups real ones.
         */
        fun decodeDefaultFlights(): List<FlightResponse> {
            val flights = ListSerializer(FlightResponse.serializer())
            return FixtureFiles.decode(flights, "flights") + FixtureFiles.decode(flights, "trip-legs")
        }
    }
}
