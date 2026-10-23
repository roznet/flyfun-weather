package aero.flyfun.weather.data

import aero.flyfun.weather.model.*

/**
 * Canned trip fixtures for `fixture-trip-1` ("Alps weekend": a flown EGTF -> LSGS outbound and an
 * AMBER, binding LSGS -> EGTF return), from the shared `app/fixtures/api/trip.json` (#895) the iOS
 * journeys use. The legs are in `trip-legs.json`, merged into the flight list by
 * [FixtureBriefingRepository.decodeDefaultFlights].
 *
 * `trip.json` is the server's `TripResponse` (its `summary` carries the legs), which is also the
 * shape of each entry of `GET /api/trips`, so the list is that one trip.
 */
object FixtureTripData {
    const val TRIP_ID = "fixture-trip-1"
    const val OUTBOUND_ID = "fixture-trip-out"
    const val RETURN_ID = "fixture-trip-back"

    val summaries: List<TripSummaryResponse> by lazy {
        listOf(FixtureFiles.decode(TripSummaryResponse.serializer(), "trip"))
    }

    val trip: TripResponse by lazy { FixtureFiles.decode(TripResponse.serializer(), "trip") }
}

/**
 * In-memory [TripRepository] serving deterministic fixtures for tests and offline UI previews.
 * Mirrors `FixtureTripRepository` on iOS.
 */
class FixtureTripRepository : TripRepository {

    val summaries: MutableList<TripSummaryResponse> = FixtureTripData.summaries.toMutableList()
    var detail: TripResponse = FixtureTripData.trip

    override suspend fun listTrips(): List<TripSummaryResponse> = summaries.toList()

    override suspend fun getTrip(tripId: String): TripResponse {
        if (tripId == detail.id) return detail
        throw ApiError.NotFound
    }

    override suspend fun createTrip(req: CreateTripRequest): TripResponse {
        val created = detail.copy(id = "trip-created-${summaries.size + 1}", title = req.name)
        detail = created
        summaries.add(
            0,
            TripSummaryResponse(
                id = created.id,
                title = created.title,
                legCount = created.legCount,
                originIcao = created.originIcao,
                destinationIcao = created.destinationIcao,
                overallAssessment = created.overallAssessment,
            ),
        )
        return created
    }

    override suspend fun addTripLegs(tripId: String, req: AddTripLegsRequest): TripResponse = detail

    override suspend fun updateTrip(tripId: String, req: UpdateTripRequest): TripResponse {
        detail = detail.copy(title = req.name ?: detail.title)
        val idx = summaries.indexOfFirst { it.id == tripId }
        if (idx >= 0) {
            summaries[idx] = summaries[idx].copy(title = detail.title)
        }
        return detail
    }

    override suspend fun deleteTrip(tripId: String, deleteFlights: Boolean) {
        summaries.removeAll { it.id == tripId }
    }

    override suspend fun removeTripLeg(tripId: String, flightId: String): TripResponse {
        val remaining = detail.legs.filter { it.flight.id != flightId }
        detail = detail.copy(legs = remaining, legCount = remaining.size)
        return detail
    }

    override suspend fun refreshTrip(tripId: String, force: Boolean): TripRefreshStatus =
        TripRefreshStatus(tripId = tripId, active = false)

    override suspend fun tripRefreshStatus(tripId: String): TripRefreshStatus =
        TripRefreshStatus(tripId = tripId, active = false)

    override suspend fun tripAiSummary(tripId: String): TripAiSummaryResponse =
        TripAiSummaryResponse(
            tripId = tripId,
            text = detail.aiSummary,
            generatedAt = detail.aiSummaryAt,
        )
}
