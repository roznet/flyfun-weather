package aero.flyfun.weather

import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.ui.skewt.SkewTOverlayBand
import aero.flyfun.weather.ui.skewt.SkewTVariableCatalog
import aero.flyfun.weather.ui.skewt.SkewTVariableGroup
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SkewTVariableCatalogTest {

    @Test
    fun `ALL_VARIABLES contains all 14 variables across 4 groups`() {
        assertEquals(14, SkewTVariableCatalog.ALL_VARIABLES.size)
        val grouped = SkewTVariableCatalog.groupedVariables()
        assertEquals(4, grouped.size)
        assertEquals(2, grouped[SkewTVariableGroup.WIND]?.size)
        assertEquals(5, grouped[SkewTVariableGroup.MOISTURE_CLOUD]?.size)
        assertEquals(3, grouped[SkewTVariableGroup.ICING]?.size)
        assertEquals(4, grouped[SkewTVariableGroup.STABILITY_VERTICAL]?.size)
    }

    @Test
    fun `extractSeries extracts all 14 variables from FixtureBriefingData soundingProfile`() {
        val profile = FixtureBriefingData.soundingProfile
        assertEquals("LFMD", profile.waypointIcao)
        assertEquals(9, profile.levels.size)
        assertNotNull(profile.indices)
        assertNotNull(profile.parcelPath)

        for (variable in SkewTVariableCatalog.ALL_VARIABLES) {
            val series = SkewTVariableCatalog.extractSeries(variable.id, profile)
            assertTrue(
                "Expected non-empty series for ${variable.id}",
                series.size >= 8,
            )
            // Sorted from high pressure (surface 1000 hPa) to low pressure (250 hPa)
            assertTrue(series.first().first >= series.last().first)
        }

        val xwSeries = SkewTVariableCatalog.extractSecondarySeries("hw_xw", profile)
        assertEquals(9, xwSeries.size)
    }

    @Test
    fun `overlay band default enabled state matches skewt-canvas spec`() {
        val defaults = SkewTOverlayBand.defaultEnabledIds()
        assertTrue(SkewTOverlayBand.CLOUDS_NWP.isEnabledIn(defaults))
        assertTrue(SkewTOverlayBand.ICING_OGIMET_NWP.isEnabledIn(defaults))
        assertTrue(SkewTOverlayBand.INVERSIONS.isEnabledIn(defaults))
        assertFalse(SkewTOverlayBand.CLOUDS_DD.isEnabledIn(defaults))
        assertFalse(SkewTOverlayBand.CONVECTIVE.isEnabledIn(defaults))
    }
}
