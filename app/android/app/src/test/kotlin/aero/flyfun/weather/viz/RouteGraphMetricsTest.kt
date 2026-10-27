package aero.flyfun.weather.viz

import aero.flyfun.weather.model.viz.MetricSample
import aero.flyfun.weather.model.viz.RouteGraphMetrics
import aero.flyfun.weather.model.viz.VizAltitudeLines
import aero.flyfun.weather.model.viz.VizPoint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RouteGraphMetricsTest {

    private fun makePoint(
        headwindKt: Double? = 15.0,
        ceilingFt: Double? = null,
        temperatureC: Double? = 14.0,
        dewpointC: Double? = null,
        nwpCeilingFt: Double? = 20000.0,
    ): VizPoint = VizPoint(
        pointIndex = 0,
        distanceNm = 25.0,
        lat = 50.0,
        lon = 1.0,
        terrainElevationFt = 500.0,
        altitudeLines = VizAltitudeLines(
            freezingLevelFt = 7000.0,
            minus10cLevelFt = 12000.0,
            minus20cLevelFt = 17000.0,
            lclFt = 3000.0,
        ),
        headwindKt = headwindKt,
        crosswindKt = -6.0,
        temperatureC = temperatureC,
        dewpointC = dewpointC,
        isaDevC = 1.5,
        precipitationMm = 0.8,
        cloudCoverTotalPct = 65.0,
        cloudCoverLowPct = 50.0,
        cloudCoverMidPct = 20.0,
        capeSurfaceJkg = 320.0,
        cinSurfaceJkg = -40.0,
        qnhHpa = 1018.0,
        soundingCeilingFt = ceilingFt,
        nwpCeilingFt = nwpCeilingFt,
    )

    @Test
    fun `all 15 metrics are registered and lookup byId works`() {
        assertEquals(15, RouteGraphMetrics.ALL.size)
        assertNotNull(RouteGraphMetrics.byId("dewpoint"))
        assertNotNull(RouteGraphMetrics.byId("headwind"))
        assertNotNull(RouteGraphMetrics.byId("ceiling-nwp"))
        assertNotNull(RouteGraphMetrics.byId("obs-radar"))
        assertNull(RouteGraphMetrics.byId("none"))
        assertNull(RouteGraphMetrics.byId(""))
    }

    @Test
    fun `sampleMetric handles AboveScale for null or above-cap ceiling metrics and Unavailable for non-aboveScale metrics`() {
        val point = makePoint(headwindKt = null, ceilingFt = null, nwpCeilingFt = 20000.0)

        // headwind has aboveScale = false -> null becomes Unavailable
        val hwSample = RouteGraphMetrics.sampleMetric(RouteGraphMetrics.HEADWIND, point)
        assertEquals(MetricSample.Unavailable, hwSample)

        // ceiling-dd has aboveScale = true -> null (clear sky) becomes AboveScale(15000.0)
        val ceilingDdSample = RouteGraphMetrics.sampleMetric(RouteGraphMetrics.CEILING_DD, point)
        assertTrue(ceilingDdSample is MetricSample.AboveScale)
        assertEquals(15000.0, (ceilingDdSample as MetricSample.AboveScale).cap, 0.01)

        // ceiling-nwp value 20000.0 > 15000.0 -> AboveScale(15000.0)
        val ceilingNwpSample = RouteGraphMetrics.sampleMetric(RouteGraphMetrics.CEILING_NWP, point)
        assertTrue(ceilingNwpSample is MetricSample.AboveScale)
        assertEquals(15000.0, (ceilingNwpSample as MetricSample.AboveScale).cap, 0.01)
    }

    @Test
    fun `computeYScale enforces symmetric bounds for zero-line metrics and suggested range for aboveScale`() {
        val hwSamples = listOf(
            MetricSample.Value(45.0),
            MetricSample.Value(-10.0),
        )
        val (minHw, maxHw) = RouteGraphMetrics.computeYScale(RouteGraphMetrics.HEADWIND, hwSamples)
        assertEquals(-45.0, minHw, 0.01)
        assertEquals(45.0, maxHw, 0.01)

        val ceilingSamples = listOf(
            MetricSample.Value(4000.0),
            MetricSample.AboveScale(15000.0),
        )
        val (minCeil, maxCeil) = RouteGraphMetrics.computeYScale(RouteGraphMetrics.CEILING_NWP, ceilingSamples)
        assertEquals(0.0, minCeil, 0.01)
        assertEquals(15000.0, maxCeil, 0.01)
    }

    @Test
    fun `temperature and dewpoint share one scale, other pairs keep their own`() {
        // #948: one °C axis so the spread is the gap between the lines.
        val points = listOf(
            makePoint(headwindKt = 60.0, temperatureC = 35.0, dewpointC = -20.0),
            makePoint(headwindKt = 10.0, temperatureC = 8.0, dewpointC = 2.0),
        )
        val (l, r) = RouteGraphMetrics.computeYScales(
            RouteGraphMetrics.TEMPERATURE, RouteGraphMetrics.DEWPOINT, points,
        )
        assertEquals(l, r)
        assertTrue(l!!.first <= -20.0 && l.second >= 35.0)

        val (t, h) = RouteGraphMetrics.computeYScales(
            RouteGraphMetrics.TEMPERATURE, RouteGraphMetrics.HEADWIND, points,
        )
        assertTrue(t!!.second < 60.0)
        assertTrue(h!!.second >= 60.0)
    }

    @Test
    fun `dewpoint missing on an old pack is unavailable`() {
        assertEquals(
            MetricSample.Unavailable,
            RouteGraphMetrics.sampleMetric(RouteGraphMetrics.DEWPOINT, makePoint(dewpointC = null)),
        )
        assertEquals(-5.0, RouteGraphMetrics.DEWPOINT.getValue(makePoint(dewpointC = -5.0))!!, 0.0)
    }
}
