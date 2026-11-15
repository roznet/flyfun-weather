package aero.flyfun.weather.map

import aero.flyfun.weather.ui.map.GeoBounds
import aero.flyfun.weather.ui.map.SlippyMapCameraState
import aero.flyfun.weather.ui.map.SlippyMapProjection
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Route-map metrics moved to the served catalog (#998): see [RouteMapMetricsTest]. */
class MapMetricsAndProjectionTest {

    @Test
    fun `SlippyMapProjection round-trips lat-lon and screen coordinates`() {
        val proj = SlippyMapProjection(
            centerLat = 48.8566,
            centerLon = 2.3522,
            zoom = 6.5f,
            widthPx = 1080f,
            heightPx = 1920f,
        )

        val testCoords = listOf(
            51.4700 to -0.4543, // EGLL
            48.9694 to 2.4414,  // LFPB
            43.5420 to 6.9535,  // LFMD
            47.4582 to 8.5555,  // LSZH
        )

        for ((lat, lon) in testCoords) {
            val screen = proj.latLonToScreen(lat, lon)
            val back = proj.screenToLatLon(screen)
            assertEquals(lat, back.lat, 1e-4)
            assertEquals(lon, back.lon, 1e-4)
        }

        val cam = SlippyMapCameraState()
        cam.viewportWidthPx = 1000f
        cam.viewportHeightPx = 800f
        cam.fitBounds(GeoBounds(minLat = 43.0, maxLat = 52.0, minLon = -1.5, maxLon = 9.0))
        assertTrue(cam.zoom in 4.0f..8.5f)
    }
}
