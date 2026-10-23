package aero.flyfun.weather

import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.data.FixtureTripData
import aero.flyfun.weather.data.HelpCatalogStore
import aero.flyfun.weather.model.*
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DtoSerializationTest {

    @Test
    fun `decode FixtureBriefingData and FixtureTripData blobs`() {
        val flights = FixtureBriefingRepository.decodeDefaultFlights()
        assertEquals(2, flights.size)
        val f1 = flights.first()
        assertEquals("fixture-1", f1.id)
        assertEquals("LFMD → LFML", f1.shortTitle)
        assertEquals(Assessment.AMBER, f1.resolvedAssessment)
        assertEquals("Riviera Hop · Leg 1/2", f1.trip?.badgeText)

        val pack = FixtureBriefingData.pack
        assertEquals("fixture-1", pack.flightId)
        assertEquals(Assessment.AMBER, pack.resolvedAssessment)
        assertEquals(0, pack.modelInitTimes["ecmwf"])
        assertEquals(6, pack.modelInitTimes["gfs"])

        val advisories = FixtureBriefingData.advisories
        assertEquals(4, advisories.advisories.size)
        assertEquals(Assessment.RED, advisories.advisories.first().resolvedStatus)
        assertEquals("LFMD", advisories.airportConditions?.departure?.icao)
        assertEquals("LFML", advisories.airportConditions?.arrival?.icao)

        val detail = FixtureBriefingData.convectiveDetail
        assertEquals("convective", detail.advisoryId)
        assertEquals(1400.0, detail.convective?.get("gfs")?.thermo?.peak?.capeJkg ?: 0.0, 1e-6)

        val digest = FixtureBriefingData.digest
        assertEquals(Assessment.AMBER, digest.resolvedAssessment)
        assertEquals(3, digest.watchItems?.size)

        val snapshot = FixtureBriefingData.snapshot
        assertEquals(2, snapshot.analyses.size)
        assertEquals(2, snapshot.routeObservations?.airports?.size)
        assertEquals(1, snapshot.routeSigmets?.sigmets?.size)

        val routeAnalyses = FixtureBriefingData.routeAnalyses
        assertEquals(1, routeAnalyses.analyses.size)
        val pt0 = routeAnalyses.analyses.first()
        assertEquals(12.0, pt0.sounding["gfs"]?.sfipZones?.first()?.meanSfip100 ?: 0.0, 1e-6)

        val elevation = FixtureBriefingData.elevation
        assertEquals(5, elevation.points.size)
        assertEquals(2400.0, elevation.maxElevationFt, 1e-6)

        val nullLive = FixtureBriefingData.nullLiveLayer
        assertFalse(nullLive.hasObservationOverlay)
        val live = FixtureBriefingData.liveLayer
        assertTrue(live.supersedes(nullLive))
        assertEquals(1, live.changes?.changes?.size)

        val tripSummaries = FixtureTripData.summaries
        assertEquals(1, tripSummaries.size)
        assertEquals("LFMD → LFMN", tripSummaries.first().routeLabel)
        val trip = FixtureTripData.trip
        assertEquals(2, trip.legs.size)
        assertEquals(TripLegState.COMPLETE, trip.legs[0].resolvedLegState)
        assertEquals(TripLegState.ECMWF_ONLY, trip.legs[1].resolvedLegState)
    }

    @Test
    fun `UpdateFlightResponse decodes flat server JSON with invalidation`() {
        val flatJson = """
        {
          "id": "flt-99",
          "user_id": "u1",
          "route_name": "EGTK LFPB",
          "waypoints": ["EGTK", "LFPB"],
          "departure_time": "2026-07-01T09:00:00Z",
          "target_date": "2026-07-01",
          "target_time_utc": 9,
          "cruise_altitude_ft": 10000,
          "flight_ceiling_ft": 18000,
          "flight_duration_hours": 2.25,
          "created_at": "2026-06-30T00:00:00Z",
          "invalidation": "advisories_only"
        }
        """.trimIndent()
        val resp = WbJson.decodeFromString(UpdateFlightResponse.serializer(), flatJson)
        assertEquals("flt-99", resp.flight.id)
        assertEquals(10000, resp.flight.cruiseAltitudeFt)
        assertEquals(FlightInvalidation.ADVISORIES_ONLY, resp.invalidation)
    }

    @Test
    fun `DigestResponse watch_items decodes both array and newline string`() {
        val stringJson = """{"assessment": "green", "watch_items": "First item\nSecond item\n"}"""
        val decoded = WbJson.decodeFromString(DigestResponse.serializer(), stringJson)
        assertEquals(listOf("First item", "Second item"), decoded.watchItems)
    }

    @Test
    fun `HelpCatalogStore loads bundled metrics-catalog and map-metrics-catalog assets`() {
        val store = HelpCatalogStore()
        val cape = store.metric("cape_surface_jkg")
        assertNotNull(cape)
        assertEquals("CAPE", cape?.name)
        assertEquals("J/kg", cape?.unit)
        assertTrue((cape?.thresholds?.size ?: 0) >= 5)

        val mapCatalog = store.mapCatalog.value
        assertNotNull(mapCatalog)
        assertTrue(mapCatalog!!.scales.categorical.containsKey("flight_category"))
    }

    @Test
    fun `live position request matches the server schema`() {
        // api/packs.py LivePositionFix / LivePositionRequest (#761, #874): `at`, `alt_ft`, `gs_kt`,
        // required `h_accuracy_m`, `landed`; an unknown value is omitted, never sent as 0.
        val request = LivePositionRequest(
            fixes = listOf(
                LivePositionFix(
                    lat = 50.1, lon = 1.2, altitudeFt = 3281.0, groundSpeedKt = 12.0, trackDeg = null,
                    recordedAt = "2026-06-24T12:00:00Z", hAccuracyM = 8.0,
                ),
            ),
            landed = true,
        )
        val obj = WbJson.parseToJsonElement(WbJson.encodeToString(LivePositionRequest.serializer(), request))
            as kotlinx.serialization.json.JsonObject
        assertEquals("true", obj["landed"].toString())
        val fix = (obj["fixes"] as kotlinx.serialization.json.JsonArray)[0] as kotlinx.serialization.json.JsonObject
        assertEquals(setOf("lat", "lon", "alt_ft", "gs_kt", "at", "h_accuracy_m"), fix.keys)
        assertEquals("\"2026-06-24T12:00:00Z\"", fix["at"].toString())

        val result = WbJson.decodeFromString(
            LivePositionResult.serializer(),
            """{"flight_id":"f","accepted":1,"ignored":[],"landed_recorded":true,"progress":null}""",
        )
        assertEquals(1, result.accepted)
        assertTrue(result.landedRecorded)
    }

    /** #889: one `POST /api/feedback` body for the form and both thumbs, keyed as the server validates. */
    @Test
    fun `feedback requests carry the server's category, sentiment and target`() {
        fun keysOf(req: FeedbackRequest) =
            WbJson.parseToJsonElement(WbJson.encodeToString(FeedbackRequest.serializer(), req))
                as kotlinx.serialization.json.JsonObject

        val digest = keysOf(FeedbackRequest.digestRating("ZZ-f1", "2026-06-24T06:00:00Z", thumbsUp = false))
        assertEquals("\"digest_rating\"", digest["category"].toString())
        assertEquals("\"down\"", digest["sentiment"].toString())
        assertEquals("\"digest\"", digest["target"].toString())
        assertFalse("context" in digest)

        val general = keysOf(FeedbackRequest.general(FeedbackCategory.INCORRECT_INTERPRETATION, "Missed the front"))
        assertEquals("\"incorrect_interpretation\"", general["category"].toString())
        assertEquals("\"Missed the front\"", general["comment"].toString())
        assertFalse("sentiment" in general)

        val line = LiveHighlight(text = "New SIGMET at destination", model = "m", factsHash = "abc", generatedAt = "2026-06-24T07:00:00Z")
        val highlight = keysOf(FeedbackRequest.highlightRating("ZZ-f1", "2026-06-24T06:00:00Z", line, thumbsUp = true)!!)
        assertEquals("\"live_highlight\"", highlight["target"].toString())
        assertEquals("\"highlight_rating\"", highlight["category"].toString())
        assertEquals(
            setOf("facts_hash", "generated_at", "model", "text"),
            (highlight["context"] as kotlinx.serialization.json.JsonObject).keys,
        )
        // The server rejects a highlight rating without its full context, so none is sent.
        assertEquals(null, FeedbackRequest.highlightRating("ZZ-f1", "t", line.copy(factsHash = null), thumbsUp = true))
    }

    @Test
    fun `decode autorouter recent routes and the badge`() {
        val routes = WbJson.decodeFromString(
            AutorouterRoutesResponse.serializer(),
            """{"routes":[{"routeid":"r1","departure":"EGTF","destination":"LSGS","departure_time":"2026-06-24T08:00:00Z",
               "fplan":"(FPL-ZZABC-VG ...)","route_distance_nm":412,"aircraft_description":"SR22"}]}""",
        )
        val route = routes.routes.single()
        assertEquals("EGTF \u2192 LSGS", route.title)
        assertEquals("2026-06-24 \u00b7 412 nm \u00b7 SR22", route.subtitle)
        assertEquals("(FPL-ZZABC-VG ...)", route.fplan)

        assertEquals(3, WbJson.decodeFromString(BadgeStatus.serializer(), """{"count":3}""").count)
    }
}
