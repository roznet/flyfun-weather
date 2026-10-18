package aero.flyfun.weather.data

import aero.flyfun.weather.model.*

/**
 * Abstraction for multi-leg trip CRUD, refresh, and AI summaries.
 * Mirrors `TripRepository` on iOS.
 */
interface TripRepository {
    suspend fun listTrips(): List<TripSummaryResponse>
    suspend fun getTrip(tripId: String): TripResponse
    suspend fun createTrip(req: CreateTripRequest): TripResponse
    suspend fun addTripLegs(tripId: String, req: AddTripLegsRequest): TripResponse
    suspend fun updateTrip(tripId: String, req: UpdateTripRequest): TripResponse
    suspend fun deleteTrip(tripId: String, deleteFlights: Boolean = false)
    suspend fun removeTripLeg(tripId: String, flightId: String): TripResponse
    suspend fun refreshTrip(tripId: String, force: Boolean = false): TripRefreshStatus
    suspend fun tripRefreshStatus(tripId: String): TripRefreshStatus
    suspend fun tripAiSummary(tripId: String, generate: Boolean = false): TripAiSummaryResponse
}

/**
 * Online [TripRepository] backed by [ApiClient] with optional write-through caching into [BriefingCacheStore].
 */
open class OnlineTripRepository(
    val api: ApiClient,
    val cache: BriefingCacheStore? = null,
) : TripRepository {

    override suspend fun listTrips(): List<TripSummaryResponse> {
        val trips = api.listTrips()
        cache?.let { store ->
            val wrapper = TripListCache(
                cachedAt = IsoTime.formatUtc(System.currentTimeMillis()),
                trips = trips,
            )
            runCatching {
                val bytes = api.json.encodeToString(TripListCache.serializer(), wrapper).toByteArray(Charsets.UTF_8)
                store.writeMetadata(bytes, name = "trips")
            }
        }
        return trips
    }

    override suspend fun getTrip(tripId: String): TripResponse {
        val trip = api.getTrip(tripId)
        cacheTripDetail(trip)
        return trip
    }

    override suspend fun createTrip(req: CreateTripRequest): TripResponse {
        val trip = api.createTrip(req)
        cacheTripDetail(trip)
        return trip
    }

    override suspend fun addTripLegs(tripId: String, req: AddTripLegsRequest): TripResponse {
        val trip = api.addTripLegs(tripId, req)
        cacheTripDetail(trip)
        return trip
    }

    override suspend fun updateTrip(tripId: String, req: UpdateTripRequest): TripResponse {
        val trip = api.updateTrip(tripId, req)
        cacheTripDetail(trip)
        return trip
    }

    override suspend fun deleteTrip(tripId: String, deleteFlights: Boolean) {
        api.deleteTrip(tripId, deleteFlights)
    }

    override suspend fun removeTripLeg(tripId: String, flightId: String): TripResponse {
        val trip = api.removeTripLeg(tripId, flightId)
        cacheTripDetail(trip)
        return trip
    }

    override suspend fun refreshTrip(tripId: String, force: Boolean): TripRefreshStatus =
        api.refreshTrip(tripId, force)

    override suspend fun tripRefreshStatus(tripId: String): TripRefreshStatus =
        api.tripRefreshStatus(tripId)

    override suspend fun tripAiSummary(tripId: String, generate: Boolean): TripAiSummaryResponse =
        api.getTripAiSummary(tripId, generate)

    private suspend fun cacheTripDetail(trip: TripResponse) {
        val store = cache ?: return
        runCatching {
            val bytes = api.json.encodeToString(TripResponse.serializer(), trip).toByteArray(Charsets.UTF_8)
            store.writeMetadata(bytes, name = "trip-${trip.id}")
        }
    }
}
