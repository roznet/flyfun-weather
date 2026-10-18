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
}
