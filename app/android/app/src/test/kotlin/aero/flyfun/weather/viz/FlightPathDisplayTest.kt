package aero.flyfun.weather.viz

import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.data.FixtureFiles
import aero.flyfun.weather.model.RouteAnalysesResponse
import aero.flyfun.weather.model.viz.FlightPathDisplay
import aero.flyfun.weather.model.viz.VizDataExtractor
import aero.flyfun.weather.model.viz.VizFlightPathPoint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The cross-section's flight path rule (#1005), on the shared live fixture. */
class FlightPathDisplayTest {

    private val live: RouteAnalysesResponse =
        FixtureFiles.decode(RouteAnalysesResponse.serializer(), "route-analyses-live")

    @Test
    fun theLiveFixtureDecodesThroughTheProductionDecoder() {
        val path = requireNotNull(live.flightPath)
        assertEquals("live", live.flightPathMode)
        assertEquals(8000.0, path.cruiseAltitudeFt, 1e-9)
        assertEquals(20.175, path.tocNm, 1e-9)
        assertEquals(38.349, path.todNm, 1e-9)
        assertTrue(path.cruiseReached)
        assertEquals(5, live.analyses.size)
        assertTrue(live.analyses.all { it.plannedAltitudeFt != null && it.phase != null })
    }

    @Test
    fun anOlderPackDecodesWithoutAPath() {
        val old = FixtureBriefingData.routeAnalyses
        assertNull(old.flightPath)
        assertNull(old.flightPathMode)
        assertTrue(old.analyses.all { it.plannedAltitudeFt == null && it.phase == null })
        assertNull(FlightPathDisplay.pathToDraw(old, old.cruiseAltitudeFt.toDouble()))
    }

    @Test
    fun aLivePathAtThePlannedCruiseIsDrawn() {
        val path = requireNotNull(FlightPathDisplay.pathToDraw(live, 8000.0))
        assertEquals(requireNotNull(live.flightPath).profile.size, path.size)
        assertEquals(VizFlightPathPoint(0.0, 0.0), path.first())
    }

    @Test
    fun aShadowPathIsNotDrawn() {
        assertNull(FlightPathDisplay.pathToDraw(live.copy(flightPathMode = "shadow"), 8000.0))
        assertNull(FlightPathDisplay.pathToDraw(live.copy(flightPathMode = null), 8000.0))
    }

    @Test
    fun aNullPathIsNotDrawn() {
        assertNull(FlightPathDisplay.pathToDraw(live.copy(flightPath = null), 8000.0))
    }

    @Test
    fun anotherAltitudeIsNotDrawn() {
        assertNull(FlightPathDisplay.pathToDraw(live, 9000.0))
        assertNull(FlightPathDisplay.pathToDraw(live, 8001.0))
        assertNotNull(FlightPathDisplay.pathToDraw(live, 8000.5))
    }

    @Test
    fun aPathOfFewerThanTwoPointsIsNotDrawn() {
        val path = requireNotNull(live.flightPath)
        val single = live.copy(flightPath = path.copy(profile = listOf(listOf(0.0, 0.0, 0.0))))
        assertNull(FlightPathDisplay.pathToDraw(single, 8000.0))
    }

    @Test
    fun theLabelSitsAtTheTopOfClimb() {
        val path = requireNotNull(FlightPathDisplay.pathToDraw(live, 8000.0))
        val top = FlightPathDisplay.labelPoint(path, 8000.0)
        assertEquals(8000.0, top.altitudeFt, 1.0)
        assertEquals(20.175, top.distanceNm, 1e-3)
    }

    @Test
    fun aPathThatNeverReachesCruiseIsLabelledAtItsStart() {
        val path = listOf(VizFlightPathPoint(0.0, 0.0), VizFlightPathPoint(5.0, 3000.0), VizFlightPathPoint(10.0, 0.0))
        assertEquals(path.first(), FlightPathDisplay.labelPoint(path, 8000.0))
    }

    @Test
    fun theExtractorAppliesTheRuleAtTheFlightsAltitude() {
        val flight = FixtureBriefingRepository.decodeDefaultFlights().first()
        val planned = VizDataExtractor.extractVizData(live, flight = flight.copy(cruiseAltitudeFt = 8000))
        assertNotNull(planned?.flightPath)
        val other = VizDataExtractor.extractVizData(live, flight = flight.copy(cruiseAltitudeFt = 10000))
        assertNull(other?.flightPath)
    }
}
