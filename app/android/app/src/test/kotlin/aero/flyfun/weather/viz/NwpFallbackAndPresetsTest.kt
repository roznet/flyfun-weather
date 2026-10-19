package aero.flyfun.weather.viz

import aero.flyfun.weather.model.viz.CrossSectionPresets
import aero.flyfun.weather.model.viz.LayerFamily
import aero.flyfun.weather.model.viz.NwpFallback
import aero.flyfun.weather.model.viz.VizAltitudeLines
import aero.flyfun.weather.model.viz.VizCloudLayer
import aero.flyfun.weather.model.viz.VizConvectiveZone
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NwpFallbackAndPresetsTest {

    private fun emptyPoint(
        hasNwpClouds: Boolean = false,
        hasNwpCeiling: Boolean = false,
    ): VizPoint = VizPoint(
        pointIndex = 0,
        distanceNm = 0.0,
        lat = 48.0,
        lon = 2.0,
        waypointIcao = "LFPO",
        waypointName = "Orly",
        flightTimeIso = null,
        terrainElevationFt = 300.0,
        altitudeLines = VizAltitudeLines(
            freezingLevelFt = 5000.0,
            minus10cLevelFt = 10000.0,
            minus20cLevelFt = 15000.0,
            lclFt = 2500.0,
        ),
        cloudLayers = listOf(VizCloudLayer(3000.0, 6000.0, "bkn", 1.5, null)),
        nwpCloudLayers = if (hasNwpClouds) {
            listOf(VizCloudLayer(3000.0, 6000.0, "bkn", null, 75.0))
        } else {
            emptyList()
        },
        hasNativeNwpClouds = hasNwpClouds,
        thermoConvective = VizConvectiveZone("none", null, null, null),
        nwpConvective = null,
        headwindKt = 10.0,
        crosswindKt = 5.0,
        temperatureC = 12.0,
        isaDevC = -2.0,
        precipitationMm = 0.0,
        cloudCoverTotalPct = 70.0,
        cloudCoverLowPct = 70.0,
        cloudCoverMidPct = 0.0,
        capeSurfaceJkg = 0.0,
        cinSurfaceJkg = 0.0,
        qnhHpa = 1015.0,
        soundingCeilingFt = 3000.0,
        nwpCeilingFt = if (hasNwpCeiling) 3200.0 else null,
    )

    @Test
    fun `NwpFallback substitutes NWP cloud and ceiling layers when unavailable`() {
        val routeWithoutNwp = VizRouteData(
            points = listOf(emptyPoint(hasNwpClouds = false, hasNwpCeiling = false)),
            cruiseAltitudeFt = 8000.0,
            flightCeilingFt = 18000.0,
            maxAltitudeFt = 20000.0,
            totalDistanceNm = 100.0,
        )

        val unavailable = NwpFallback.getUnavailableLayers(routeWithoutNwp)
        assertTrue("clouds-nwp" in unavailable)
        assertTrue("ceiling-nwp" in unavailable)
        assertTrue("icing-ogimet-nwp" in unavailable)
        assertTrue("icing-sfip" in unavailable)

        val stored = mapOf(
            "clouds-nwp" to true,
            "clouds" to false,
            "ceiling-nwp" to true,
            "ceiling" to false,
            "icing-ogimet-nwp" to true,
            "icing-sfip" to true,
            "icing" to false,
        )

        val effective = NwpFallback.applyNwpFallback(stored, unavailable)
        assertFalse(effective["clouds-nwp"] == true)
        assertTrue(effective["clouds"] == true)
        assertFalse(effective["ceiling-nwp"] == true)
        assertTrue(effective["ceiling"] == true)
        assertFalse(effective["icing-ogimet-nwp"] == true)
        assertFalse(effective["icing-sfip"] == true)
        assertTrue(effective["icing"] == true)

        val substituted = NwpFallback.getSubstitutedLayers(stored, effective)
        assertTrue("clouds" in substituted)
        assertTrue("ceiling" in substituted)
        assertTrue("icing" in substituted)
    }

    @Test
    fun `NwpFallback keeps Ogimet-NWP and IENG available on NWP-capable model when icing zones are empty`() {
        val clearNwpRoute = VizRouteData(
            points = listOf(emptyPoint(hasNwpClouds = true, hasNwpCeiling = true)),
            cruiseAltitudeFt = 8000.0,
            flightCeilingFt = 18000.0,
            maxAltitudeFt = 20000.0,
            totalDistanceNm = 100.0,
        )

        val unavailable = NwpFallback.getUnavailableLayers(clearNwpRoute)
        assertFalse("clouds-nwp" in unavailable)
        assertFalse("icing-ogimet-nwp" in unavailable)
        assertFalse("icing-ogimet-nwp-bands" in unavailable)
        assertFalse("icing-ieng" in unavailable)
        assertFalse("ieng-icing-bands" in unavailable)

        val stored = mapOf(
            "clouds-nwp" to true,
            "icing-ogimet-nwp" to true,
            "icing" to false,
        )
        val effective = NwpFallback.applyNwpFallback(stored, unavailable)
        assertTrue(effective["icing-ogimet-nwp"] == true)
        assertFalse(effective["icing"] == true)
        assertTrue(NwpFallback.getSubstitutedLayers(stored, effective).isEmpty())
    }

    @Test
    fun `resolveAdvisoryPreset maps advisory IDs and configures expected layers`() {
        val icingLayers = CrossSectionPresets.resolveAdvisoryPreset("fiki_icing")
        assertTrue(icingLayers["clouds-icing-nwp"] == true)
        assertTrue(icingLayers["icing-ogimet-nwp"] == true)
        assertTrue(icingLayers["sld"] == true)
        assertTrue(icingLayers["freezing"] == true)
        assertTrue(icingLayers["minus-10"] == true)
        assertTrue(icingLayers["minus-20"] == true)
        assertFalse(icingLayers["convective"] == true)

        val convectiveLayers = CrossSectionPresets.resolveAdvisoryPreset("thunderstorm")
        assertTrue(convectiveLayers["convective"] == true)
        assertTrue(convectiveLayers["lcl"] == true)
        assertTrue(convectiveLayers["lfc"] == true)
        assertTrue(convectiveLayers["el"] == true)
    }

    @Test
    fun `Basic shows NWP convection and falls back to Thermo when the model has none`() {
        val defaults = CrossSectionPresets.defaultEnabledLayers()
        assertEquals("nwp", CrossSectionPresets.deriveGradedMethods(defaults).convective)

        val basic = CrossSectionPresets.resolveAdvisoryPreset("basic")
        assertTrue(basic["convective-nwp"] == true)
        assertFalse(basic["convective"] == true)

        val routeWithoutNwpConvective = VizRouteData(
            points = listOf(emptyPoint(hasNwpClouds = true, hasNwpCeiling = true)),
            cruiseAltitudeFt = 8000.0,
            flightCeilingFt = 18000.0,
            maxAltitudeFt = 20000.0,
            totalDistanceNm = 100.0,
        )
        val unavailable = NwpFallback.getUnavailableLayers(routeWithoutNwpConvective)
        val effective = NwpFallback.applyNwpFallback(basic, unavailable)
        assertFalse(effective["convective-nwp"] == true)
        assertTrue(effective["convective"] == true)
    }

    @Test
    fun `getCompactLayerOverrides toggles family off and back on preserving preferred method`() {
        val initial = CrossSectionPresets.defaultEnabledLayers().toMutableMap().apply {
            this["clouds-nwp"] = false
            this["clouds-diag-nwp"] = true
        }
        val preferred = CrossSectionPresets.deriveGradedMethods(initial)
        assertEquals("nwp", preferred.cloudExtent)
        assertEquals("diag", preferred.cloudScale)

        // Toggle clouds OFF
        val toggledOff = CrossSectionPresets.getCompactLayerOverrides(
            family = LayerFamily.CLOUDS,
            currentlyEnabled = initial,
            preferredMethods = preferred,
        )
        assertFalse(CrossSectionPresets.isFamilyEnabled(LayerFamily.CLOUDS, toggledOff))

        // Toggle clouds back ON -> should restore clouds-diag-nwp
        val toggledOn = CrossSectionPresets.getCompactLayerOverrides(
            family = LayerFamily.CLOUDS,
            currentlyEnabled = toggledOff,
            preferredMethods = preferred,
        )
        assertTrue(toggledOn["clouds-diag-nwp"] == true)
    }
}
