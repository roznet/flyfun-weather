package aero.flyfun.weather.data

import aero.flyfun.weather.model.*
import kotlinx.serialization.builtins.ListSerializer

/**
 * Canned trip fixtures for `trip-fixture-1` ("Riviera Hop", 2 legs: LFMD -> LFML -> LFMN).
 * Decoded through the production [WbJson] serializer.
 */
object FixtureTripData {
    const val TRIP_ID = "trip-fixture-1"

    val summaries: List<TripSummaryResponse> by lazy {
        WbJson.decodeFromString(ListSerializer(TripSummaryResponse.serializer()), SUMMARIES_JSON)
    }

    val trip: TripResponse by lazy {
        WbJson.decodeFromString(TripResponse.serializer(), TRIP_JSON)
    }

    const val SUMMARIES_JSON = """
    [
      {
        "id": "trip-fixture-1",
        "title": "Riviera Hop",
        "leg_count": 2,
        "first_departure_time": "2099-07-01T09:00:00Z",
        "last_departure_time": "2099-07-02T14:00:00Z",
        "origin_icao": "LFMD",
        "destination_icao": "LFMN",
        "overall_assessment": "red",
        "ecmwf_assessment": "amber",
        "gfs_assessment": "red",
        "grade_kind": "split",
        "complete_count": 1,
        "ecmwf_only_count": 1,
        "gfs_only_count": 0,
        "beyond_horizon_count": 0,
        "missing_count": 0,
        "stale_leg_count": 1,
        "missing_leg_count": 0,
        "continuity_warning_count": 0,
        "role": "owner",
        "owner_display_name": "Test Pilot",
        "subscriber_count": 0,
        "share_code": "Tr9pK2mN",
        "created_at": "2099-06-30T00:00:00Z"
      }
    ]
    """

    const val TRIP_JSON = """
    {
      "id": "trip-fixture-1",
      "user_id": "ui-test",
      "title": "Riviera Hop",
      "leg_count": 2,
      "first_departure_time": "2099-07-01T09:00:00Z",
      "last_departure_time": "2099-07-02T14:00:00Z",
      "origin_icao": "LFMD",
      "destination_icao": "LFMN",
      "overall_assessment": "red",
      "binding_leg_index": 1,
      "binding_basis": "all_models",
      "ecmwf_assessment": "amber",
      "gfs_assessment": "red",
      "grade_kind": "split",
      "complete_count": 1,
      "ecmwf_only_count": 1,
      "gfs_only_count": 0,
      "beyond_horizon_count": 0,
      "missing_count": 0,
      "stale_leg_count": 1,
      "missing_leg_count": 0,
      "continuity_warning_count": 0,
      "continuity_warnings": [],
      "role": "owner",
      "owner_display_name": "Test Pilot",
      "subscriber_count": 0,
      "share_code": "Tr9pK2mN",
      "ai_summary": "Leg 1 (LFMD → LFML) is the weather pinch point: GFS builds mid-morning CAPE near Cannes while ECMWF stays marginal. Leg 2 (LFML → LFMN) looks benign on ECMWF.",
      "ai_summary_generated_at": "2099-06-30T06:05:00Z",
      "ai_summary_stale": false,
      "created_at": "2099-06-30T00:00:00Z",
      "legs": [
        {
          "leg_index": 1,
          "pack_timestamp": "2099-06-30T06:00:00Z",
          "assessment": "amber",
          "assessment_reason": "Isolated convective cell risk near Cannes on GFS; marginal cruise icing on the coast.",
          "is_stale": false,
          "has_pack": true,
          "leg_state": "complete",
          "ecmwf_assessment": "amber",
          "gfs_assessment": "red",
          "models_fetched": ["ecmwf", "gfs"],
          "advisory_chips": [
            { "id": "convective", "name": "Convective", "status": "red" },
            { "id": "icing", "name": "Icing", "status": "amber" }
          ],
          "watch_items": ["Convective initiation near LFMD after 10Z"],
          "continuity_warning": null,
          "flight": {
            "id": "fixture-1",
            "user_id": "ui-test",
            "route_name": "LFMD LFML",
            "waypoints": ["LFMD", "LFML"],
            "departure_time": "2099-07-01T09:00:00Z",
            "target_date": "2099-07-01",
            "target_time_utc": 9,
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 13000,
            "flight_duration_hours": 1.5,
            "private": false,
            "auto_refresh": true,
            "created_at": "2099-06-30T00:00:00Z"
          }
        },
        {
          "leg_index": 2,
          "pack_timestamp": "2099-06-29T18:00:00Z",
          "assessment": "green",
          "assessment_reason": "Smooth coastal return on ECMWF; GFS not yet in range.",
          "is_stale": true,
          "has_pack": true,
          "leg_state": "ecmwf_only",
          "ecmwf_assessment": "green",
          "gfs_assessment": null,
          "models_fetched": ["ecmwf"],
          "advisory_chips": [],
          "watch_items": [],
          "continuity_warning": null,
          "flight": {
            "id": "fixture-leg-2",
            "user_id": "ui-test",
            "route_name": "LFML LFMN",
            "waypoints": ["LFML", "LFMN"],
            "departure_time": "2099-07-02T14:00:00Z",
            "target_date": "2099-07-02",
            "target_time_utc": 14,
            "cruise_altitude_ft": 7500,
            "flight_ceiling_ft": 13000,
            "flight_duration_hours": 1.25,
            "private": false,
            "auto_refresh": true,
            "created_at": "2099-06-30T00:00:00Z"
          }
        }
      ]
    }
    """
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
        val created = detail.copy(id = "trip-created-${summaries.size + 1}", title = req.title)
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
        detail = detail.copy(title = req.title ?: detail.title)
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

    override suspend fun tripAiSummary(tripId: String, generate: Boolean): TripAiSummaryResponse =
        TripAiSummaryResponse(
            tripId = tripId,
            summary = detail.aiSummary,
            generatedAt = detail.aiSummaryGeneratedAt,
            stale = detail.aiSummaryStale ?: false,
        )
}
