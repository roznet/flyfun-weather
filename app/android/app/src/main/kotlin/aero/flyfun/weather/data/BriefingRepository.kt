package aero.flyfun.weather.data

import aero.flyfun.weather.model.*
import kotlinx.coroutines.flow.Flow

/**
 * Abstraction for flight and briefing data access.
 * Mirrors `BriefingRepository` on iOS, enabling transparent offline caching
 * ([CachingBriefingRepository]) and deterministic UI-test fixtures ([FixtureBriefingRepository]).
 */
interface BriefingRepository {
    // Flights
    suspend fun listFlights(): List<FlightResponse>
    suspend fun getFlight(id: String): FlightResponse
    suspend fun createFlight(req: CreateFlightRequest): FlightResponse
    suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse
    suspend fun deleteFlight(id: String)
    suspend fun bulkDeleteFlights(ids: List<String>): BulkDeleteResponse
    suspend fun moveFlight(id: String, req: MoveFlightRequest): FlightResponse
    suspend fun parseFpl(fplText: String): ParseFplResponse
    suspend fun interpretRoute(rawText: String, cruiseSpeedKt: Double? = null): InterpretRouteResponse
    suspend fun routeDistance(waypoints: List<String>): RouteDistanceResponse
    suspend fun fetchAutorouterRoutes(
        departure: String,
        destination: String,
        departureTime: String,
        cruiseAltitudeFt: Int,
        durationHours: Double,
        aircraftId: Int? = null,
    ): AutorouterRoutesResponse

    // Pack metadata
    suspend fun listPacks(flightId: String): List<PackMetaResponse>
    suspend fun latestPack(flightId: String): PackMetaResponse
    suspend fun freshness(flightId: String): DataStatus

    // Pack data (per flightId + timestamp)
    suspend fun advisories(flightId: String, timestamp: String): AdvisoriesResponse
    suspend fun altAdvisories(flightId: String, timestamp: String): AdvisoriesResponse
    suspend fun recalculateAdvisories(flightId: String, timestamp: String): AdvisoriesResponse
    suspend fun recalculateAltAdvisories(flightId: String, timestamp: String): AdvisoriesResponse
    suspend fun advisoryDetail(
        flightId: String,
        timestamp: String,
        advisoryId: String,
        alt: Boolean = false,
    ): AdvisoryDetailResponse
    suspend fun alternates(flightId: String, timestamp: String): AlternatesResponse
    suspend fun digest(flightId: String, timestamp: String): DigestResponse
    suspend fun snapshot(flightId: String, timestamp: String): SnapshotResponse
    suspend fun routeAnalyses(flightId: String, timestamp: String): RouteAnalysesResponse
    suspend fun elevation(flightId: String, timestamp: String): ElevationResponse
    suspend fun soundingProfile(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): SoundingProfileResponse
    suspend fun gramet(flightId: String, timestamp: String): ByteArray
    suspend fun skewT(flightId: String, timestamp: String, icao: String, model: String): ByteArray
    suspend fun routeSkewT(flightId: String, timestamp: String, pointIndex: Int, model: String): ByteArray
    suspend fun chartImage(chart: PackChart): ByteArray
    suspend fun chartOverlay(urlPath: String, flightId: String, timestamp: String, source: String): ChartOverlay

    // Live observation layer
    suspend fun liveLayer(flightId: String): LiveLayerResponse

    // Refresh
    suspend fun triggerRefresh(flightId: String, force: Boolean = false): RefreshStatusResponse
    suspend fun refreshStatus(flightId: String): ActiveRefreshResponse
    suspend fun activeRefreshes(): Map<String, ActiveRefreshResponse>
    fun refreshPack(flightId: String, force: Boolean = false, notifyEmail: Boolean = false): Flow<RefreshEvent>

    // Time options (flexible departure)
    suspend fun timeOptions(flightId: String): TimeOptionsResponse
    suspend fun triggerTimeScan(flightId: String): TimeScanStatusDTO
    suspend fun confirmTimeOption(flightId: String, req: ConfirmTimeOptionRequest): FlightResponse

    // Observed imagery (radar / cloud tops / lightning / storm cells)
    suspend fun observedStatus(): ObservedImageryStatusResponse
    suspend fun observedFrames(layer: String, bbox: String? = null): ObservedFramesResponse
    suspend fun observedFlashes(bbox: String, validTime: String? = null): ObservedFlashesResponse
    suspend fun cellFrames(bbox: String): CellFramesResponse
    suspend fun observedCells(bbox: String, validTime: String? = null): CellDisplay

    // Debrief
    suspend fun debrief(flightId: String): DebriefResponse
    suspend fun saveDebrief(flightId: String, req: DebriefRequest): DebriefResponse
    suspend fun deleteDebrief(flightId: String)

    // PIREPs
    suspend fun submitPirep(req: SubmitPirepRequest): PirepResponse
    suspend fun listPireps(flightId: String): PirepListResponse

    // Shared flight / trip links
    suspend fun resolveSharedFlight(code: String): FlightResponse
    suspend fun subscribeToSharedFlight(code: String): FlightResponse
    suspend fun unsubscribeFromFlight(flightId: String)
    suspend fun resolveSharedTrip(code: String): TripResponse
    suspend fun subscribeToSharedTrip(code: String): TripSubscribeResponse
    suspend fun unsubscribeFromTrip(tripId: String)

    // Convenience aliases used by ViewModels
    suspend fun fetchFlights(): List<FlightResponse> = listFlights()

    suspend fun createFlightShare(flightId: String): SharedFlightCodeResponse {
        val flight = getFlight(flightId)
        val code = flight.shareCode ?: flightId.take(8)
        return SharedFlightCodeResponse(
            code = code,
            shareUrl = "https://weather.flyfun.aero/s/$code",
        )
    }

    suspend fun previewSharedFlight(code: String): SharedFlightPreview {
        val flight = resolveSharedFlight(code)
        return SharedFlightPreview(
            code = code,
            flight = flight,
            sharedBy = flight.ownerDisplayName,
        )
    }

    suspend fun acceptSharedFlight(code: String): FlightResponse = subscribeToSharedFlight(code)

    suspend fun copySharedFlight(code: String): FlightResponse = subscribeToSharedFlight(code)
}

/**
 * Pure network implementation of [BriefingRepository] delegating directly to [ApiClient].
 */
open class OnlineBriefingRepository(
    val api: ApiClient,
) : BriefingRepository {

    override suspend fun listFlights(): List<FlightResponse> = api.listFlights()

    override suspend fun getFlight(id: String): FlightResponse = api.getFlight(id)

    override suspend fun createFlight(req: CreateFlightRequest): FlightResponse = api.createFlight(req)

    override suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse =
        api.updateFlight(id, req)

    override suspend fun deleteFlight(id: String) = api.deleteFlight(id)

    override suspend fun bulkDeleteFlights(ids: List<String>): BulkDeleteResponse =
        api.bulkDeleteFlights(ids)

    override suspend fun moveFlight(id: String, req: MoveFlightRequest): FlightResponse =
        api.moveFlight(id, req)

    override suspend fun parseFpl(fplText: String): ParseFplResponse = api.parseFpl(fplText)

    override suspend fun interpretRoute(rawText: String, cruiseSpeedKt: Double?): InterpretRouteResponse =
        api.interpretRoute(rawText, cruiseSpeedKt)

    override suspend fun routeDistance(waypoints: List<String>): RouteDistanceResponse =
        api.routeDistance(waypoints)

    override suspend fun fetchAutorouterRoutes(
        departure: String,
        destination: String,
        departureTime: String,
        cruiseAltitudeFt: Int,
        durationHours: Double,
        aircraftId: Int?,
    ): AutorouterRoutesResponse = api.fetchAutorouterRoutes(
        departure = departure,
        destination = destination,
        departureTime = departureTime,
        cruiseAltitudeFt = cruiseAltitudeFt,
        durationHours = durationHours,
        aircraftId = aircraftId,
    )

    override suspend fun listPacks(flightId: String): List<PackMetaResponse> = api.listPacks(flightId)

    override suspend fun latestPack(flightId: String): PackMetaResponse = api.latestPack(flightId)

    override suspend fun freshness(flightId: String): DataStatus = api.getFreshness(flightId)

    override suspend fun advisories(flightId: String, timestamp: String): AdvisoriesResponse =
        api.getAdvisories(flightId, timestamp, alt = false)

    override suspend fun altAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        api.getAdvisories(flightId, timestamp, alt = true)

    override suspend fun recalculateAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        api.recalculateAdvisories(flightId, timestamp, alt = false)

    override suspend fun recalculateAltAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        api.recalculateAdvisories(flightId, timestamp, alt = true)

    override suspend fun advisoryDetail(
        flightId: String,
        timestamp: String,
        advisoryId: String,
        alt: Boolean,
    ): AdvisoryDetailResponse = api.getAdvisoryDetail(flightId, timestamp, advisoryId, alt)

    override suspend fun alternates(flightId: String, timestamp: String): AlternatesResponse =
        api.getAlternates(flightId, timestamp)

    override suspend fun digest(flightId: String, timestamp: String): DigestResponse =
        api.getDigest(flightId, timestamp)

    override suspend fun snapshot(flightId: String, timestamp: String): SnapshotResponse =
        api.getSnapshot(flightId, timestamp)

    override suspend fun routeAnalyses(flightId: String, timestamp: String): RouteAnalysesResponse =
        api.getRouteAnalyses(flightId, timestamp)

    override suspend fun elevation(flightId: String, timestamp: String): ElevationResponse =
        api.getElevation(flightId, timestamp)

    override suspend fun soundingProfile(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): SoundingProfileResponse = api.getSoundingProfile(flightId, timestamp, pointIndex, model)

    override suspend fun gramet(flightId: String, timestamp: String): ByteArray =
        api.getGramet(flightId, timestamp)

    override suspend fun skewT(flightId: String, timestamp: String, icao: String, model: String): ByteArray =
        api.getSkewT(flightId, timestamp, icao, model)

    override suspend fun routeSkewT(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): ByteArray = api.getRouteSkewT(flightId, timestamp, pointIndex, model)

    override suspend fun chartImage(chart: PackChart): ByteArray =
        api.getChartImage(chart.url)

    override suspend fun chartOverlay(
        urlPath: String,
        flightId: String,
        timestamp: String,
        source: String,
    ): ChartOverlay = api.getChartOverlay(urlPath)

    override suspend fun liveLayer(flightId: String): LiveLayerResponse =
        api.getLiveLayer(flightId)

    override suspend fun triggerRefresh(flightId: String, force: Boolean): RefreshStatusResponse =
        api.triggerRefresh(flightId, force)

    override suspend fun refreshStatus(flightId: String): ActiveRefreshResponse =
        api.refreshStatus(flightId)

    override suspend fun activeRefreshes(): Map<String, ActiveRefreshResponse> =
        api.activeRefreshes()

    override fun refreshPack(flightId: String, force: Boolean, notifyEmail: Boolean): Flow<RefreshEvent> =
        api.refreshPackStream(flightId, force = force, notifyEmail = notifyEmail)

    override suspend fun timeOptions(flightId: String): TimeOptionsResponse =
        api.getTimeOptions(flightId)

    override suspend fun triggerTimeScan(flightId: String): TimeScanStatusDTO =
        api.triggerTimeScan(flightId)

    override suspend fun confirmTimeOption(flightId: String, req: ConfirmTimeOptionRequest): FlightResponse =
        api.confirmTimeOption(flightId, req)

    override suspend fun observedStatus(): ObservedImageryStatusResponse =
        api.getObservedStatus()

    override suspend fun observedFrames(layer: String, bbox: String?): ObservedFramesResponse =
        api.getObservedFrames(layer, bbox)

    override suspend fun observedFlashes(bbox: String, validTime: String?): ObservedFlashesResponse =
        api.getObservedFlashes(bbox, validTime)

    override suspend fun cellFrames(bbox: String): CellFramesResponse =
        api.getCellFrames(bbox)

    override suspend fun observedCells(bbox: String, validTime: String?): CellDisplay =
        api.getObservedCells(bbox, validTime)

    override suspend fun debrief(flightId: String): DebriefResponse =
        api.getDebrief(flightId)

    override suspend fun saveDebrief(flightId: String, req: DebriefRequest): DebriefResponse =
        api.saveDebrief(flightId, req)

    override suspend fun deleteDebrief(flightId: String) =
        api.deleteDebrief(flightId)

    override suspend fun submitPirep(req: SubmitPirepRequest): PirepResponse =
        api.submitPirep(req)

    override suspend fun listPireps(flightId: String): PirepListResponse =
        api.listPireps(flightId)

    override suspend fun resolveSharedFlight(code: String): FlightResponse =
        api.resolveSharedFlight(code)

    override suspend fun subscribeToSharedFlight(code: String): FlightResponse =
        api.subscribeToSharedFlight(code)

    override suspend fun unsubscribeFromFlight(flightId: String) =
        api.unsubscribeFromFlight(flightId)

    override suspend fun resolveSharedTrip(code: String): TripResponse =
        api.resolveSharedTrip(code)

    override suspend fun subscribeToSharedTrip(code: String): TripSubscribeResponse =
        api.subscribeToSharedTrip(code)

    override suspend fun unsubscribeFromTrip(tripId: String) =
        api.unsubscribeFromTrip(tripId)
}
