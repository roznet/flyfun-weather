package aero.flyfun.weather.map

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import aero.flyfun.weather.data.HelpCatalogStore
import aero.flyfun.weather.data.RouteMapCatalog
import aero.flyfun.weather.model.FrontChain
import aero.flyfun.weather.model.FrontChainNode
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.viz.VizAltitudeLines
import aero.flyfun.weather.model.viz.VizIcingZone
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.ui.map.RouteMapMetrics
import aero.flyfun.weather.ui.map.frontPosition
import aero.flyfun.weather.ui.map.layersSummary
import aero.flyfun.weather.ui.map.routeMetricForFocusPreset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The briefing route map's catalog-driven metrics (#998), the same cases as web
 * `route-map-metrics.test.ts` and iOS `MapMetricsTests`.
 */
class RouteMapMetricsTest {

    private val catalog: RouteMapCatalog = requireNotNull(HelpCatalogStore().routeMapCatalog.value) {
        "bundled route-map-metrics-catalog.json"
    }
    private val metrics = RouteMapMetrics(catalog)

    private fun argb(c: Color) = c.toArgb()

    private fun point(
        cloudTotal: Double? = 60.0,
        headwind: Double? = 12.0,
        icing: List<VizIcingZone> = emptyList(),
        lines: VizAltitudeLines = VizAltitudeLines(freezingLevelFt = 6000.0, minus10cLevelFt = 11000.0),
        distanceNm: Double = 0.0,
        lat: Double = 48.0,
        lon: Double = 2.0,
    ) = VizPoint(
        distanceNm = distanceNm,
        lat = lat,
        lon = lon,
        cloudCoverTotalPct = cloudTotal,
        headwindKt = headwind,
        icingZones = icing,
        altitudeLines = lines,
    )

    @Test
    fun `bundled catalog decodes, every metric has a known extractor and a legend`() {
        assertEquals(16, metrics.all.size)
        assertEquals(catalog.metrics.size, metrics.all.size)
        assertEquals("cloud-cover-total", metrics.defaultId)
        assertEquals(listOf("at_altitude", "along_route"), metrics.groups.map { it.key })
        metrics.all.forEach { assertTrue("${it.id} legend", it.legend.isNotEmpty()) }
    }

    @Test
    fun `bundled asset is the web catalog, byte for byte`() {
        val asset = File("src/main/assets/route-map-metrics-catalog.json").takeIf { it.isFile }
            ?: File("app/src/main/assets/route-map-metrics-catalog.json")
        val web = generateSequence(asset.absoluteFile.parentFile) { it.parentFile }
            .map { File(it, "web/ts/data/route-map-metrics-catalog.json") }
            .firstOrNull { it.isFile }
        if (web != null) assertEquals(web.readText(), asset.readText())
    }

    @Test
    fun `an unknown extractor is skipped rather than drawn empty, an unknown id falls back to the default`() {
        val json = File("src/main/assets/route-map-metrics-catalog.json").takeIf { it.isFile }?.readText() ?: return
        val withFuture = json.replaceFirst("\"value\": \"cape_surface_jkg\"", "\"value\": \"future_extractor\"")
        val future = RouteMapMetrics(WbJson.decodeFromString(RouteMapCatalog.serializer(), withFuture))
        assertNull(future.metric("cape"))
        assertEquals(15, future.all.size)
        assertEquals("cloud-cover-total", metrics.resolve("width-metric-from-2025")?.id)
        assertEquals("headwind", metrics.resolve("headwind")?.id)
    }

    @Test
    fun `altitude metrics read nothing without an altitude, surface metrics ignore it`() {
        val icing = metrics.metric("icing-risk-at-level")!!
        val p = point(icing = listOf(VizIcingZone(baseFt = 5000.0, topFt = 9000.0, risk = "moderate")))
        assertTrue(icing.usesAltitude)
        assertNull(icing.value(p, null))
        assertEquals(2.0, icing.value(p, 8000.0)!!, 0.0)
        assertEquals(0.0, icing.value(p, 12000.0)!!, 0.0)

        val cloud = metrics.metric("cloud-cover-total")!!
        assertEquals(false, cloud.usesAltitude)
        assertEquals(60.0, cloud.value(p, null)!!, 0.0)
    }

    @Test
    fun `hazards take the worst endpoint, others average, a missing end uses the other`() {
        val icing = metrics.metric("icing-risk-at-level")!!
        val bad = point(icing = listOf(VizIcingZone(baseFt = 5000.0, topFt = 9000.0, risk = "severe")))
        val clear = point()
        assertEquals(3.0, icing.segmentValue(bad, clear, 8000.0)!!, 0.0)

        val cloud = metrics.metric("cloud-cover-total")!!
        assertEquals(40.0, cloud.segmentValue(point(cloudTotal = 20.0), point(cloudTotal = 60.0), null)!!, 0.0)
        assertEquals(60.0, cloud.segmentValue(point(cloudTotal = null), point(cloudTotal = 60.0), null)!!, 0.0)
        assertNull(cloud.segmentValue(point(cloudTotal = null), point(cloudTotal = null), null))
    }

    @Test
    fun `widths run 3 to 18 on the catalog span, either order`() {
        val hw = metrics.metric("headwind")!!
        assertEquals(3f, hw.widthDp(0.0), 0.001f)
        assertEquals(18f, hw.widthDp(30.0), 0.001f)
        assertEquals(18f, hw.widthDp(80.0), 0.001f)
        val ceiling = metrics.metric("nwp-ceiling")!! // thick when low
        assertEquals(18f, ceiling.widthDp(0.0), 0.001f)
        assertEquals(3f, ceiling.widthDp(6000.0), 0.001f)
    }

    @Test
    fun `colours come from the catalog scales, linear interpolates, legend matches the line`() {
        val icing = metrics.metric("icing-risk-at-level")!!
        assertEquals(argb(Color(0xFFF97316)), argb(icing.color(2.0)))
        assertEquals(argb(icing.color(2.0)), argb(icing.legend.first { it.label == "Moderate" }.color))

        val temp = metrics.metric("temp-at-level")!!
        assertEquals(argb(Color(0xFF1E50DC)), argb(temp.color(-40.0))) // clamped
        assertEquals(argb(Color(0xFF3D6FE5)), argb(temp.color(-15.0))) // a quarter of the way to 0 °C
    }

    @Test
    fun `formats follow the catalog`() {
        assertEquals("moderate", metrics.metric("icing-risk-at-level")!!.format(2.0))
        assertEquals("SFIP 18", metrics.metric("sfip-at-level")!!.format(18.0))
        assertEquals("4.5 km", metrics.metric("visibility")!!.format(4500.0))
        assertEquals("-12.0°C", metrics.metric("temp-at-level")!!.format(-12.0))
    }

    @Test
    fun `temperature at a level interpolates the isotherms and extrapolates beyond them`() {
        val p = point(lines = VizAltitudeLines(freezingLevelFt = 6000.0, minus10cLevelFt = 11000.0))
        assertEquals(0.0, RouteMapMetrics.tempAtAltitude(p, 6000.0)!!, 1e-9)
        assertEquals(-5.0, RouteMapMetrics.tempAtAltitude(p, 8500.0)!!, 1e-9)
        assertEquals(4.0, RouteMapMetrics.tempAtAltitude(p, 4000.0)!!, 1e-9)
        assertEquals(-12.0, RouteMapMetrics.tempAtAltitude(p, 12000.0)!!, 1e-9)
        assertNull(RouteMapMetrics.tempAtAltitude(point(lines = VizAltitudeLines()), 8000.0))
    }

    @Test
    fun `headwind and tailwind split the component, nothing for a missing wind`() {
        assertEquals(12.0, metrics.metric("headwind")!!.value(point(headwind = 12.0), null)!!, 0.0)
        assertEquals(0.0, metrics.metric("tailwind")!!.value(point(headwind = 12.0), null)!!, 0.0)
        assertEquals(8.0, metrics.metric("tailwind")!!.value(point(headwind = -8.0), null)!!, 0.0)
        assertNull(metrics.metric("headwind")!!.value(point(headwind = null), null))
    }

    @Test
    fun `altitude stepper - 500 ft steps clamped to the ceiling, flight level label`() {
        assertEquals(8500.0, RouteMapMetrics.stepAltitude(8000.0, 1, 18000.0), 0.0)
        assertEquals(7500.0, RouteMapMetrics.stepAltitude(7800.0, -1, 18000.0), 0.0)
        assertEquals(18000.0, RouteMapMetrics.stepAltitude(18000.0, 1, 18000.0), 0.0)
        assertEquals(0.0, RouteMapMetrics.stepAltitude(0.0, -1, 18000.0), 0.0)
        assertEquals("FL080", RouteMapMetrics.formatFlightLevel(8000.0))
        assertEquals("SFC", RouteMapMetrics.formatFlightLevel(0.0))
    }

    @Test
    fun `advisory focus presets pick the web's map metric`() {
        assertEquals("icing-risk-at-level", routeMetricForFocusPreset("aircraft_icing"))
        assertEquals("cat-risk-at-level", routeMetricForFocusPreset("turbulence"))
        assertEquals("nwp-ceiling", routeMetricForFocusPreset("vfr_feasibility"))
        assertEquals("headwind", routeMetricForFocusPreset("headwind"))
    }

    @Test
    fun `a front chain is placed where its lowest node crosses the route`() {
        val pts = listOf(point(distanceNm = 0.0, lat = 48.0, lon = 2.0), point(distanceNm = 100.0, lat = 50.0, lon = 2.0))
        val chain = FrontChain(nodes = listOf(FrontChainNode(levelHpa = 925, distanceKm = 92.6)), kind = "cold")
        val (at, _) = requireNotNull(frontPosition(chain, pts))
        assertEquals(49.0, at.lat, 1e-9)
        assertNull(frontPosition(FrontChain(nodes = listOf(FrontChainNode(distanceKm = 400.0))), pts))
    }

    @Test
    fun `layers summary names what is on`() {
        assertEquals("Airports · Radar · Sat", layersSummary(true, true, true, false, false, false))
        assertEquals("None", layersSummary(false, false, false, false, false, false))
    }
}
