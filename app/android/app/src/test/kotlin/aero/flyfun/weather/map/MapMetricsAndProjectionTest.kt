package aero.flyfun.weather.map

import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.viz.VizAltitudeLines
import aero.flyfun.weather.model.viz.VizConvectiveZone
import aero.flyfun.weather.model.viz.VizIcingZone
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.ui.map.AirportMapMetric
import aero.flyfun.weather.ui.map.GeoBounds
import aero.flyfun.weather.ui.map.RouteMapMetric
import aero.flyfun.weather.ui.map.SlippyMapCameraState
import aero.flyfun.weather.ui.map.SlippyMapProjection
import aero.flyfun.weather.viewmodel.ForecastMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalMapViewModel
import aero.flyfun.weather.viewmodel.PanEuropeanMapFixtures
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MapMetricsAndProjectionTest {

    @Test
    fun `all 13 RouteMapMetric entries extract values, colors, widths, and legends`() {
        assertEquals(13, RouteMapMetric.entries.size)

        val samplePoint = VizPoint(
            pointIndex = 2,
            distanceNm = 120.0,
            lat = 48.9694,
            lon = 2.4414,
            waypointIcao = "LFPB",
            waypointName = "Paris Le Bourget",
            flightTimeIso = "2026-04-18T12:00:00Z",
            terrainElevationFt = 200.0,
            altitudeLines = VizAltitudeLines(
                freezingLevelFt = 7500.0,
                minus10cLevelFt = 12500.0,
                minus20cLevelFt = 17500.0,
            ),
            cloudLayers = emptyList(),
            icingZones = listOf(VizIcingZone(baseFt = 6000.0, topFt = 10000.0, risk = "moderate", type = "rime")),
            sfipZones = emptyList(),
            sldZones = emptyList(),
            catLayers = emptyList(),
            inversionLayers = emptyList(),
            thermoConvective = VizConvectiveZone(riskLevel = "low", baseFt = 3000.0, topFt = 22000.0, coverPct = 40.0),
            headwindKt = 14.0,
            crosswindKt = -9.0,
            windSpeedKt = 22.0,
            windDirectionDeg = 250.0,
            temperatureC = 12.0,
            temperatureCruiseC = -2.0,
            precipitationMm = 1.8,
            cloudCoverTotalPct = 65.0,
            cloudCoverLowPct = 45.0,
            cloudCoverMidPct = 30.0,
            capeSurfaceJkg = 420.0,
            visibilityMeters = 7500.0,
            soundingCeilingFt = 2600.0,
            nwpCeilingFt = 2400.0,
        )

        for (metric in RouteMapMetric.entries) {
            val value = metric.extractValue(samplePoint, 8000.0)
            val color = metric.color(value)
            val widthDp = metric.strokeWidthDp(value)
            val formatted = metric.formatValue(value)
            val legend = metric.legendSwatches()

            assertTrue("Width for ${metric.id} should be >= 5.5f", widthDp >= 5.5f)
            assertTrue("Formatted value for ${metric.id} should not be blank", formatted.isNotBlank())
            assertTrue("Legend for ${metric.id} should not be empty", legend.isNotEmpty())
            assertTrue("Color alpha for ${metric.id} should be > 0", color.alpha > 0f)
        }

        // Alias lookup
        assertEquals(RouteMapMetric.ICING, RouteMapMetric.fromId("icing-risk-at-level"))
        assertEquals(RouteMapMetric.TURBULENCE, RouteMapMetric.fromId("cat-risk-at-level"))
        assertEquals(RouteMapMetric.CLOUD_COVER, RouteMapMetric.fromId("cloud-cover-total"))
        assertEquals(RouteMapMetric.HEADWIND, RouteMapMetric.fromId("tailwind"))
    }

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

    @Test
    fun `PanEuropeanMapFixtures and AirportMapMetric cover 15 European airports`() {
        val forecastResp = PanEuropeanMapFixtures.buildForecastMapResponse(day = 0, hourUtc = 12)
        assertEquals(15, forecastResp.airports.size)
        assertTrue(forecastResp.modelInitTimes.keys.containsAll(listOf("ecmwf", "gfs", "icon_eu")))

        val egll = forecastResp.airports.first { it.icao == "EGLL" }
        for (metric in AirportMapMetric.forecastMetrics) {
            val raw = metric.extractForecastRawValue(egll, selectedModel = "ecmwf", worstOfModels = false)
            assertNotNull("Forecast raw value for ${metric.id}", raw)
            val color = metric.resolveColor(raw)
            val radius = metric.resolveRadiusDp(raw)
            assertTrue(color.alpha > 0f)
            assertTrue(radius >= 6.0f)
            assertTrue(metric.formatValue(raw).isNotBlank())
            assertTrue(metric.legendSwatches().isNotEmpty())
        }

        val histResp = PanEuropeanMapFixtures.buildHistoricalMapResponse(date = "2026-04-17", hourUtc = 12)
        assertEquals(15, histResp.airports.size)
        val lszh = histResp.airports.first { it.icao == "LSZH" }
        for (metric in AirportMapMetric.historicalMetrics) {
            val raw = metric.extractHistoricalRawValue(lszh, viewMode = "worst")
            assertNotNull("Historical raw value for ${metric.id}", raw)
            assertTrue(metric.resolveRadiusDp(raw) >= 6.0f)
        }

        val scope = CoroutineScope(Dispatchers.Unconfined)
        val fVm = ForecastMapViewModel(isMockModeProvider = { true }, scopeOverride = scope)
        fVm.applyDeepLinkTarget(
            NavigationTarget.ForecastMap(dayOffset = 1, hourUtc = 15, model = "gfs", metric = "crosswind_kt"),
        )
        assertEquals(1, fVm.selectedDay.value)
        assertEquals(15, fVm.selectedHour.value)
        assertEquals("gfs", fVm.selectedModel.value)
        assertEquals(AirportMapMetric.CROSSWIND, fVm.selectedMetric.value)

        val hVm = HistoricalMapViewModel(isMockModeProvider = { true }, scopeOverride = scope)
        hVm.applyDeepLinkTarget(
            NavigationTarget.HistoricalMap(date = "2026-04-10", hourUtc = 9, metric = "wind_speed_kt", viewMode = "taf"),
        )
        assertEquals("2026-04-10", hVm.selectedDate.value)
        assertEquals(9, hVm.selectedHour.value)
        assertEquals("taf", hVm.viewMode.value)
        assertEquals(AirportMapMetric.WIND_SPEED, hVm.selectedMetric.value)
    }
}
